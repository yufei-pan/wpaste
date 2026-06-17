# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

wpaste is a Flask web clipboard for sharing text, HTML, images, video, and arbitrary files between machines via the browser. No database — state lives in TSVZ-backed indexes plus files on disk. The default/root board is anonymous and public (as it always was); additionally, users can open named **private boards** gated by TOTP (no usernames/passwords). Authored by Yufei Pan.

## Commands

```bash
# Development (auto-reload, debug)
pip install -r requirements.txt
flask run            # or: python app.py  (runs app.run(debug=True))

# Production
pip install gunicorn
gunicorn app:app
```

There are no automated tests in-repo. `TSVZ` and `pyotp` are pip dependencies; the local `TSVZ.py`, `mainIndex.tsv`, `messages/`, `boards/`, `boards.nsv`, and `.wpaste_secret` are gitignored. (`itsdangerous` ships with Flask; the QR generator is vendored JS, no extra Python dep.)

## Architecture

Backend is `app.py` (routing, default board, config, admin CLI) + `boards.py` (private-board registry, `BoardState`, TOTP, sessions, rate limiter, `canonical_slug`, the `can()` permission matrix). Frontend is `templates/index.html` + `static/js/scripts.js` + `static/css/style.css`.

### Storage model
- **`mainIndex`** is a `TSVZ.TSVZed` (dict-like, TSV-backed, async writes) keyed by an 8-char random id. Header columns: `id, unix_time, path, type, filename`. It backs the **default/root board** and is wrapped by `default_board = BoardState(None, mainIndex, BASE_DIR)`.
- **Gotcha:** the stored value list *includes the key as element `[0]`*. So a row is `[id, unix_time, path, type, filename]` and code reads `mainIndex[id][1]`=unix_time, `[2]`=path, `[3]`=type, `[4]`=filename — even though writes assign only the 4 trailing columns. The board registry follows the same offset (see below). Preserve it.
- Default-board files: `messages/<YYYY-MM-DD>/<id>.<ext>`. `type` ∈ `text|image|video|file`.
- **Named boards** are isolated per directory: index `boards/<slug>/index.tsv` (same header), files `boards/<slug>/<YYYY-MM-DD>/<id>.<ext>`. Lazily loaded into `Boards._states[slug]`.
- **Board registry** `boards.nsv` is a `TSVZ.TSVZed` (null-separated) keyed by canonical slug. Header `[slug, secret, perm, retention, tokens, created, display]`; `retention=''` means inherit site default; `tokens` is comma-joined session tokens, newest last.

### Request flow
Every message route exists twice via Flask `defaults=`: a bare path for the default board (`slug=None`) and a `/b/<slug>/...` path for named boards. `resolve(slug, action)` is the choke point: it canonicalizes the slug, 404s unknown boards, computes `authed` (`board_authed`), enforces the `can(perm, action, authed)` matrix, and returns `(BoardState, perm, authed)` — aborting `401 {auth_required:true}` when a code is needed. Handlers operate on the returned `state.index` / `state.base_dir` and call `state.bump()`.
- `POST /message` (or `/b/<slug>/message`) — `message` text + multipart `image`/`video`/`file` (each `getlist`-ed). Images sniffed via `filetype.guess`, rejected if not `image/*`. Requires `post` permission.
- `GET /messages` — live messages newest-first (text inlined; media → URL, prefixed `/b/<slug>` for named boards). Also returns `board, perm, authed, display, retention`. **Lazy cleanup**: entries older than the board's resolved retention are purged here (retention `0` = never). Requires `read`.
- `GET /image|/video|/file/<id>` (and `/b/<slug>/...`) — one `get_file` handler; **read-permission gated** (closes the prior leak where any id was world-readable). Path confined to `state.base_dir`.
- `POST /delete/<id>`, `POST /delete_all` — require `delete`.
- **Board lifecycle:** `GET /b/<slug>/access` (rate-limited; returns `action ∈ {open, setup, login}`, plus a candidate `secret`+`otpauth` for setup), `POST /b/<slug>/setup`, `/login`, `/logout`, `/settings` (admin), `/delete_board` (admin).
- **curl:** front page (`/`, `/b/<slug>`) renders plain text when the UA looks like curl/wget (`wants_plaintext`). Headless auth = a live code in the `X-TOTP` header (or `totp` param); stateless, does not consume a session slot.

### Delete semantics (`delete_file_on_disk`)
Deletion renames the file to `<path>.deleted` (soft delete) **unless** it exceeds `RETENTION_SIZE`, in which case it's hard-removed. `.deleted` files are never cleaned up automatically.

### Private boards & auth (`boards.py`)
- **Permission ladder** (`can()`): `private` < `read` < `append` < `public`. New named boards default to `private`; the default/root board is `public`. "Owner" == any holder of a valid session — TOTP is the only credential, so `admin` actions (settings/delete-board) just require `authed`.
- **Sessions:** Flask's signed `session` cookie holds `{slug: token}`. The registry keeps the last `MAX_SESSIONS` (default 7) tokens per board in a `deque(maxlen=...)`; a new login evicts the oldest. `board_authed` is valid-token-OR-live-TOTP, cached per request on `flask.g`.
- **TSVZ deletion gotcha:** in TSVZ 3.38 a single-key `del` does **not** survive a reload (the tombstone reloads as an empty row). `Boards.delete()` therefore rebuilds the registry (capture survivors → `clear()` → re-add) instead of relying on `del`. Appends/overwrites persist fine; only key deletion needs this.
- **Admin CLI:** `python app.py admin <list|remove-board|regen-totp>`, run while the service is stopped (it mutates files the running process caches). `regen-totp` is the only lost-secret recovery.

### Live updates
There is no websocket/SSE. Each board has its own `BoardState.last_update` (ns), bumped on every mutation. The client (`scripts.js`) polls the board-scoped `GET /last-update` every 5s and re-fetches `/messages` on change. A `401` (private, not authed) stops the poll and shows the locked panel.

### Config
- `DEFAULT_CONFIG` (top of `app.py`) holds the defaults; `load_config()` overlays the first JSON file found in `CONFIG_SEARCH_PATHS` (`./wpaste.config.json`, `~/.wpaste.config.json`, `~/.config/wpaste/wpaste.config.json`, `/etc/wpaste.config.json`). Only keys present in `DEFAULT_CONFIG` are honored. `wpaste.config.example.json` is the committed template; `wpaste.config.json` is gitignored.
- `parse_size` accepts bytes or `"16GB"`/`"100MB"` strings; `parse_duration` accepts seconds or `"4h"`/`"30m"` strings. Applied to `RETENTION_SIZE`/`MAX_CONTENT_LENGTH` (sizes) and `RETENTION_TIME` (duration).
- Core keys: `BASE_DIR`, `INDEX_FILE`, `INDEX_REWRITE_INTERVAL`, `RETENTION_SIZE` (100MB, hard-delete vs soft-delete threshold), `RETENTION_TIME` (4h; `0` disables purge), `MAX_CONTENT_LENGTH` (16GB upload cap), `HOST`/`PORT`/`DEBUG`.
- Private-board keys: `BOARDS_DIR`, `REGISTRY_FILE`, `SECRET_KEY` (cookie signing; blank → generated to `.wpaste_secret` atomically at `0o600`; a too-permissive existing file is refused and regenerated), `MAX_SESSIONS` (7), `PREFER_SECURE_COOKIES`, `ACCESS_RATE_LIMIT`/`ACCESS_RATE_WINDOW` (existence-lookup throttle).
- TOTP lockout keys: `TOTP_MAX_FAILURES` (per IP) **and** `TOTP_BOARD_MAX_FAILURES` (per board across all IPs — so XFF/IP rotation can't reset it) within `TOTP_LOCKOUT_TIME`.
- `TRUSTED_PROXY_HOPS` (default 0): number of trusted reverse proxies. When >0 the app wraps `wsgi_app` in Werkzeug `ProxyFix(x_for=hops)` so `request.remote_addr` (which `get_client_ip()` returns) is the real client. Behind a proxy with hops left at 0, every client collapses to the proxy IP and shares one rate-limit/lockout bucket. Never hand-parse `X-Forwarded-For` (its client-facing end is spoofable).
- Headless TOTP auth (`board_authed`) reads the code only from the `X-TOTP` header or `?totp=` query — **never** the form body, so a denied POST isn't forced to buffer its body first.
- `version` string at module top is the canonical version.

### Deployment constraint
- **Single worker only.** Per-board `last_update`, `mainIndex`, the board registry, the in-memory `RateLimiter`, and the lazily-loaded `BoardState`s are all per-process state, so multiple gunicorn workers would diverge and could corrupt the TSVZ files. `gunicorn.conf.py` (auto-loaded by `gunicorn app:app`) pins `workers = 1` and uses threads. Concurrent paths iterate over `list(index)` snapshots; registry mutations are guarded by a lock in `Boards`.

## Conventions
- `print` is monkey-patched to `functools.partial(print, flush=True)` for unbuffered logging.
- This repo is a subproject of `/mnt/klein/work`; see that parent `CLAUDE.md` for sibling projects (notably `TSVZ`).

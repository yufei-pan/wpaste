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

There are no automated tests in-repo. `TSVZ` (>= 3.39) and `pyotp` are pip dependencies; the local `TSVZ.py`, `mainIndex.tsv`, `messages/`, `boards/`, `boards.nsv`, and `.wpaste_secret` are gitignored. (`itsdangerous` ships with Flask.) Frontend libraries (DOMPurify, marked, qrcode-generator) are vendored in `static/js/vendor/` and served locally — there is no CDN.

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
- `POST /message` (or `/b/<slug>/message`) — `message` text + multipart `image`/`video`/`file` (each `getlist`-ed). Images sniffed via `filetype.guess`; one the sniffer can't confirm (e.g. SVG) is stored as a `file` rather than refused. Requires `post` permission. Flask's per-field form cap is raised to 64 MB (`MAX_FORM_MEMORY_SIZE`) so large pasted text fits.
- `GET /messages` — live messages newest-first (text inlined; media → URL, prefixed `/b/<slug>` for named boards). Also returns `board, perm, authed, display, retention`. Requires `read`. Listing goes through `live_rows(state)`, which also purges stale rows (older than the board's resolved retention — `0` = never — or blank) and rows whose file is gone; the curl view and the sweep use the same function.
- `GET /image|/video|/file|/text/<id>` (and `/b/<slug>/...`) — one `get_file` handler; **read-permission gated**, path confined to `state.base_dir`, and an expired message 404s (and is purged). Every file is sent with `X-Content-Type-Options: nosniff` and, except PDFs, `Content-Security-Policy: sandbox`, so an uploaded HTML/SVG page opened directly can't run script as wpaste. `app.url_map.redirect_defaults = False` stops Werkzeug 308-redirecting the default-board rules to one another.
- `POST /delete/<id>`, `POST /delete_all` — require `delete`.
- **Board lifecycle:** `GET /b/<slug>/access` (rate-limited; returns `action ∈ {open, setup, login}`, plus a candidate `secret`+`otpauth` for setup), `POST /b/<slug>/setup`, `/login`, `/logout`, `/settings` (admin), `/delete_board` (admin).
- **curl:** front page (`/`, `/b/<slug>`) renders plain text when the UA looks like curl/wget (`wants_plaintext`). Headless auth = a live code in the `X-TOTP` header (or `totp` param); stateless, does not consume a session slot.

### Delete semantics (`delete_file_on_disk`)
Deletion (manual or retention) renames the file to `<path>.deleted` (soft delete) and resets its mtime to the deletion time, **unless** it exceeds `RETENTION_SIZE`, in which case it's hard-removed. The sweep removes `.deleted` files once their mtime is older than `DELETED_RETENTION_TIME` (`0` = keep forever).

### Background sweep
`sweep()` runs every 60s in one daemon thread, started by the first request (`start_sweeper`, so the debug reloader's parent and the admin CLI never sweep). It calls `live_rows` on the default board and every registered board — so retention holds on boards nobody opens — then walks `BASE_DIR` and `BOARDS_DIR` removing expired `.deleted` files.

### Private boards & auth (`boards.py`)
- **Permission ladder** (`can()`): `private` < `read` < `append` < `public`. New named boards default to `private`; the default/root board is `public`. "Owner" == any holder of a valid session — TOTP is the only credential, so `admin` actions (settings/delete-board) just require `authed`.
- **Sessions:** Flask's signed `session` cookie holds `{slug: token}`. The registry keeps the last `MAX_SESSIONS` (default 7) tokens per board in a `deque(maxlen=...)`; a new login evicts the oldest. `board_authed` is valid-token-OR-live-TOTP, cached per request on `flask.g`.
- **TSVZ deletion:** requires TSVZ >= 3.39, where a single-key `del` survives a reload. Tombstones written by 3.38 and older (`<sep>` fillers) still reload as blank rows in existing index files; `_stale()` treats a blank row as stale, and purging it writes a proper tombstone.
- `Boards.state()` loads a board's index under the registry lock (one TSVZ writer per file) and returns `None` for an unregistered slug, so a request racing a board deletion 404s instead of recreating its directory.
- **Admin CLI:** `python app.py admin <list|remove-board|regen-totp>`, run while the service is stopped (it mutates files the running process caches). `regen-totp` is the only lost-secret recovery.

### Live updates
There is no websocket/SSE. Each board has its own `BoardState.last_update` (ns), bumped on every mutation. The client (`scripts.js`) polls the board-scoped `GET /last-update` every 5s and re-fetches `/messages` on change. A `401` (private, not authed) stops the poll and shows the locked panel. `renderMessages` updates the list by message id (adds new, removes gone), so a playing video or a Show Raw toggle survives other posts.

### Client rendering & clipboard
- Text renders as HTML only if it starts with `<` and every parsed element is a known HTML element; as Markdown (`breaks: true`) only on a strong sign (fence, `[link](url)`, table separator, `**bold**`) or two weak ones with no code hints (shebang, lines ending `;{}`, YAML keys). Everything else is plain. DOMPurify runs with `FORBID_TAGS: ['style']` so a message can't restyle the page.
- Copy (button and Ctrl+C with nothing selected) always puts the message's **original** text as `text/plain`, plus the rendering as `text/html` while rendered. Text goes through `copyFormats()` (`execCommand('copy')` + our own copy-event data), which works over plain HTTP; only image copy needs `navigator.clipboard` (falls back to the image link).
- Paste or drop makes one message: the files if any (routed by MIME to `image`/`video`/`file`), otherwise the plain text.

### Config
- `DEFAULT_CONFIG` (top of `app.py`) holds the defaults; `load_config()` overlays the first JSON file found in `CONFIG_SEARCH_PATHS` (`./wpaste.config.json`, `~/.wpaste.config.json`, `~/.config/wpaste/wpaste.config.json`, `/etc/wpaste.config.json`). Only keys present in `DEFAULT_CONFIG` are honored. `wpaste.config.example.json` is the committed template; `wpaste.config.json` is gitignored.
- `parse_size` accepts bytes or `"16GB"`/`"100MB"` strings; `parse_duration` accepts seconds or `"4h"`/`"30m"` strings. Applied to `RETENTION_SIZE`/`MAX_CONTENT_LENGTH` (sizes) and `RETENTION_TIME` (duration).
- Core keys: `BASE_DIR`, `INDEX_FILE`, `INDEX_REWRITE_INTERVAL`, `RETENTION_SIZE` (100MB, hard-delete vs soft-delete threshold), `RETENTION_TIME` (4h; `0` disables purge), `DELETED_RETENTION_TIME` (7d; how long `.deleted` files are kept; `0` = forever), `MAX_CONTENT_LENGTH` (16GB upload cap), `HOST`/`PORT`/`DEBUG`.
- Private-board keys: `BOARDS_DIR`, `REGISTRY_FILE`, `SECRET_KEY` (cookie signing; blank → generated to `.wpaste_secret` atomically at `0o600`; a too-permissive existing file is refused and regenerated), `MAX_SESSIONS` (7), `PREFER_SECURE_COOKIES` (also makes `index.html` hop plain-HTTP default-port visitors to HTTPS client-side — browsers drop a `Secure` cookie set over HTTP, so a login there never sticks), `ACCESS_RATE_LIMIT`/`ACCESS_RATE_WINDOW` (existence-lookup throttle).
- TOTP lockout keys: `TOTP_MAX_FAILURES` (per IP) **and** `TOTP_BOARD_MAX_FAILURES` (per board across all IPs — so XFF/IP rotation can't reset it) within `TOTP_LOCKOUT_TIME`.
- `TRUSTED_PROXY_HOPS` (default 0): number of trusted reverse proxies. When >0 the app wraps `wsgi_app` in Werkzeug `ProxyFix(x_for=hops)` so `request.remote_addr` (which `get_client_ip()` returns) is the real client. Behind a proxy with hops left at 0, every client collapses to the proxy IP and shares one rate-limit/lockout bucket. Never hand-parse `X-Forwarded-For` (its client-facing end is spoofable).
- Headless TOTP auth (`board_authed`) reads the code only from the `X-TOTP` header or `?totp=` query — **never** the form body, so a denied POST isn't forced to buffer its body first.
- `version` string at module top is the canonical version.

### Deployment constraint
- **Single worker only.** Per-board `last_update`, `mainIndex`, the board registry, the in-memory `RateLimiter`, the lazily-loaded `BoardState`s, and the sweep thread are all per-process state, so multiple gunicorn workers would diverge and could corrupt the TSVZ files. `gunicorn.conf.py` (auto-loaded by `gunicorn app:app`) pins `workers = 1` and uses threads. Concurrent paths iterate over `list(index)` snapshots; registry mutations are guarded by a lock in `Boards`.

## Conventions
- `print` is monkey-patched to `functools.partial(print, flush=True)` for unbuffered logging.
- This repo is a subproject of `/mnt/klein/work`; see that parent `CLAUDE.md` for sibling projects (notably `TSVZ`).

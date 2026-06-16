# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

wpaste is a single-file Flask web clipboard for sharing text, HTML, images, video, and arbitrary files between machines via the browser. No database, no auth — state lives in a TSVZ-backed index plus files on disk. Authored by Yufei Pan.

## Commands

```bash
# Development (auto-reload, debug)
pip install -r requirements.txt
flask run            # or: python app.py  (runs app.run(debug=True))

# Production
pip install gunicorn
gunicorn app:app
```

There are no tests. `TSVZ` is an external dependency (https://github.com/yufei-pan/TSVZ), installed via pip; the local `TSVZ.py`, `mainIndex.tsv`, and `messages/` are gitignored.

## Architecture

The whole backend is `app.py` (~240 lines). Frontend is `templates/index.html` + `static/js/scripts.js` + `static/css/style.css`.

### Storage model
- **`mainIndex`** is a `TSVZ.TSVZed` (dict-like, TSV-backed, async writes) keyed by an 8-char random id. Header columns: `id, unix_time, path, type, filename`.
- **Gotcha:** the stored value list *includes the id as element `[0]`*. So a row is `[id, unix_time, path, type, filename]` and code reads `mainIndex[id][1]`=unix_time, `[2]`=path, `[3]`=type, `[4]`=filename — even though writes assign only the 4 trailing columns (`mainIndex[id] = [timestamp, path, type, filename]`). Preserve this offset when touching index access.
- Uploaded files are written to `messages/<YYYY-MM-DD>/<id>.<ext>`. `type` is one of `text`, `image`, `video`, `file`.

### Request flow
- `POST /message` — single endpoint that accepts any combination of form field `message` (text) and multipart files under `image`, `video`, `file` (each `getlist`-ed, so multiple per field). Images are sniffed with `filetype.guess` and rejected if not `image/*`; videos/files keep their original extension.
- `GET /messages` — returns all live messages newest-first. Text content is inlined; image/video/file return a URL (`/image/<id>` etc.). **This endpoint also performs lazy cleanup**: any entry older than `RETENTION_TIME` is deleted as a side effect of listing.
- `GET /image|/video|/file/<id>` — all three routes map to one `get_file` handler that serves by id (extension in the URL is stripped). Path is validated to stay under `BASE_DIR` before serving.
- `POST /delete/<id>` and `POST /delete_all`.

### Delete semantics (`__delete_file`)
Deletion renames the file to `<path>.deleted` (soft delete) **unless** it exceeds `RETENTION_SIZE`, in which case it's hard-removed. `.deleted` files are never cleaned up automatically.

### Live updates
There is no websocket/SSE. `app.py` keeps a global `last_update_time` (ns) bumped by `update_last_modified()` on every mutation. The client polls `GET /last-update` every 5s (`scripts.js`) and re-fetches `/messages` when it changes.

### Config
- `DEFAULT_CONFIG` (top of `app.py`) holds the defaults; `load_config()` overlays the first JSON file found in `CONFIG_SEARCH_PATHS` (`./wpaste.config.json`, `~/.wpaste.config.json`, `~/.config/wpaste/wpaste.config.json`, `/etc/wpaste.config.json`). Only keys present in `DEFAULT_CONFIG` are honored. `wpaste.config.example.json` is the committed template; `wpaste.config.json` is gitignored.
- `parse_size` accepts bytes or `"16GB"`/`"100MB"` strings; `parse_duration` accepts seconds or `"4h"`/`"30m"` strings. Applied to `RETENTION_SIZE`/`MAX_CONTENT_LENGTH` (sizes) and `RETENTION_TIME` (duration).
- Keys: `BASE_DIR`, `INDEX_FILE`, `INDEX_REWRITE_INTERVAL`, `RETENTION_SIZE` (100MB default, hard-delete vs soft-delete-rename threshold), `RETENTION_TIME` (4h default, purge age on next `/messages` read), `MAX_CONTENT_LENGTH` (16GB default upload cap, wired into `app.config`), `HOST`/`PORT`/`DEBUG` (dev server).
- `version` string at module top is the canonical version.

### Deployment constraint
- **Single worker only.** `last_update_time` and `mainIndex` are per-process in-memory state, so multiple gunicorn workers would not see each other's messages and could corrupt `mainIndex.tsv`. `gunicorn.conf.py` (auto-loaded by `gunicorn app:app`) pins `workers = 1` and uses threads. Concurrent-access paths iterate over `list(mainIndex)` snapshots to avoid mutation-during-iteration.

## Conventions
- `print` is monkey-patched to `functools.partial(print, flush=True)` for unbuffered logging.
- This repo is a subproject of `/mnt/klein/work`; see that parent `CLAUDE.md` for sibling projects (notably `TSVZ`).

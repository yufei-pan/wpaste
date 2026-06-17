A simple web paste application.

install via

```bash
pip install -r requirements.txt
flask run
```

If you want a more production server,
I use gunicorn:
```bash
pip install gunicorn
gunicorn app:app
```

> **Note:** wpaste must run as a **single worker** — its live-update timestamp
> and the message index live in process memory, so multiple workers would not
> see each other's messages and could corrupt the index. The bundled
> `gunicorn.conf.py` (auto-loaded when you run `gunicorn app:app` from this
> directory) pins `workers = 1` and uses threads for concurrency.

## Configuration

Defaults work out of the box. To override them, copy
`wpaste.config.example.json` to one of the following (first match wins):

```
./wpaste.config.json
~/.wpaste.config.json
~/.config/wpaste/wpaste.config.json
/etc/wpaste.config.json
```

Configurable keys: `BASE_DIR`, `INDEX_FILE`, `INDEX_REWRITE_INTERVAL`,
`RETENTION_SIZE`, `RETENTION_TIME`, `MAX_CONTENT_LENGTH`, `HOST`, `PORT`,
`DEBUG`, plus the private-board keys `BOARDS_DIR`, `REGISTRY_FILE`,
`SECRET_KEY`, `MAX_SESSIONS`, `PREFER_SECURE_COOKIES`, `TOTP_MAX_FAILURES`,
`TOTP_BOARD_MAX_FAILURES`, `TOTP_LOCKOUT_TIME`, `ACCESS_RATE_LIMIT`,
`ACCESS_RATE_WINDOW`, `TRUSTED_PROXY_HOPS`. Sizes accept bytes or strings like
`"16GB"`/`"100MB"`; durations accept seconds or strings like `"4h"`/`"30m"`. The
default upload limit (`MAX_CONTENT_LENGTH`) is 16GB. Set `RETENTION_TIME` to `0`
to disable auto-deletion of messages.

> **Behind a reverse proxy?** Set `TRUSTED_PROXY_HOPS` to the number of proxies
> in front of wpaste (e.g. `1` for a single nginx). Otherwise every visitor
> looks like the proxy's IP and shares one rate-limit/lockout bucket.

## Private boards

Open a private board by typing a name into the box in the top bar. Boards are
protected by a **TOTP** authenticator code — no usernames, no passwords.

- **New name** → a setup screen shows a QR code, an `otpauth://` link (tap it to
  add the board on the same phone), and the secret. Scan it, enter the code, and
  you're in. **Save the secret — it is the only backup.** Lose it and the board
  is gone forever.
- **Existing board** → if it's private you're asked for a code; if it allows
  public reading you just see it.
- A board owner (anyone holding the code) can set the board's **permission**
  level and **retention** from *Settings*:

  | Level | Public can… | Code required to… |
  |---|---|---|
  | Private | nothing | read, post, delete |
  | Read-only | read | post, delete |
  | Append | read, post | delete |
  | Public | read, post, delete | change settings / delete board |

  Sessions: the last `MAX_SESSIONS` (default 7) logins per board stay valid; a
  newer login silently evicts the oldest device.

### curl / headless

The front page renders as plain text for `curl`/`wget`. Scripts authenticate by
passing a live code in an `X-TOTP` header (stateless — it does not consume a
session slot):

```bash
curl https://host/                                   # public board, plain text
curl https://host/b/myboard                          # a readable board
curl -H 'X-TOTP: 123456' https://host/b/myboard      # private board, headless
curl -H 'X-TOTP: 123456' -d 'message=hi' https://host/b/myboard/message
```

### Admin (server operator)

There is no admin *account* — administration is done from the server shell while
the service is **stopped** (it edits the same files the running process caches):

```bash
python app.py admin list                  # list boards
python app.py admin remove-board <name>   # delete a board and its data
python app.py admin regen-totp <name>     # new secret (logs everyone out); prints QR/secret
```

`regen-totp` is the only recovery path for a lost secret — and means a server
operator can take over any board (they can already read every file on disk).

### A note on deletion / disk usage

Deleting a message **renames** its file to `<name>.deleted` rather than
removing it (a soft delete), so it can be recovered. Files larger than
`RETENTION_SIZE` are hard-deleted instead. This is intended behavior — but
`.deleted` files are **never cleaned up automatically** and will accumulate on
disk. If you do *not* want files retained on delete, set `RETENTION_SIZE` very
small (e.g. `"1"`) so everything is hard-deleted; otherwise prune `.deleted`
files yourself periodically.

![screenshot1](/etc/Screenshot 2024-05-01 145831.png)

Currently support:

Text, HTML, and Imgaes
![screenshot2](/etc/Screenshot 2024-05-01 145853.png)
![screenshot3](/etc/Screenshot 2024-05-01 150454.png)

Drag and Drop

Ctrl C (CMD C)

Ctrl V (CMD V)

Select file

Delete messages manually

Delete ALL

Private boards (TOTP-authenticated)

Include TSVZ from https://github.com/yufei-pan/TSVZ

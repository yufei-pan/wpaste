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
`DEBUG`. Sizes accept bytes or strings like `"16GB"`/`"100MB"`; durations
accept seconds or strings like `"4h"`/`"30m"`. The default upload limit
(`MAX_CONTENT_LENGTH`) is 16GB.

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

TODO: ADD user session support to allow private copy paste boards.


Include TSVZ from https://github.com/yufei-pan/TSVZ

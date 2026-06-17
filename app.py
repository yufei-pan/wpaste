#!/usr/bin/env python3
from flask import (Flask, request, jsonify, render_template, send_file, abort,
                   session, g, make_response, Response)
from werkzeug.middleware.proxy_fix import ProxyFix
from datetime import datetime
import time
import os
import sys
import random
import json
import secrets
import TSVZ
#import imghdr
import filetype

from boards import (Boards, BoardState, RateLimiter, canonical_slug, can,
                    new_secret, verify_code, provisioning_uri,
                    PERMS, DEFAULT_PERM, PUBLIC_PERM)

version = '1.6.0'

#TODO: add feature: copy from the webpage should be easier : ctrl c copy the last message , add a copy to clipboard button to messages
#TODO: add periodic update / event based update to the webpage

# print with flush on
from functools import partial
print = partial(print, flush=True)

# ---------------------------------------------------------------------------
# Configuration
#
# Defaults below can be overridden by a JSON config file. The first file found
# in CONFIG_SEARCH_PATHS wins. Sizes accept plain bytes or human-readable
# strings like "16GB"/"100MB"; durations accept seconds or strings like
# "4h"/"30m". Only keys present in DEFAULT_CONFIG are honored.
# ---------------------------------------------------------------------------
DEFAULT_CONFIG = {
    'BASE_DIR': 'messages/',                        # where default-board files live
    'INDEX_FILE': 'mainIndex.tsv',                  # TSVZ-backed default-board index
    'INDEX_REWRITE_INTERVAL': 3600 * 20,            # TSVZ compaction interval (s)
    'RETENTION_SIZE': '100MB',                       # hard-delete files larger than this
    'RETENTION_TIME': '4h',                          # purge entries older than this (0 = never)
    'MAX_CONTENT_LENGTH': '16GB',                    # max accepted upload size
    'HOST': '127.0.0.1',                             # dev server bind host
    'PORT': 5000,                                    # dev server bind port
    'DEBUG': True,                                    # dev server debug mode
    # --- private boards ---
    'BOARDS_DIR': 'boards/',                          # per-board indexes + files
    'REGISTRY_FILE': 'boards.nsv',                    # TSVZ board registry (null-separated)
    'SECRET_KEY': '',                                 # cookie signing key ('' = auto-generate)
    'MAX_SESSIONS': 7,                                # valid cookies kept per board
    'PREFER_SECURE_COOKIES': False,                   # set Secure flag on the session cookie
    'TOTP_MAX_FAILURES': 5,                           # failed codes per IP before lockout
    'TOTP_BOARD_MAX_FAILURES': 20,                    # failed codes per board (all IPs) before lockout
    'TOTP_LOCKOUT_TIME': '5m',                        # lockout / failure window
    'ACCESS_RATE_LIMIT': 30,                          # board-existence lookups per window per IP
    'ACCESS_RATE_WINDOW': '1m',                       # window for the above
    'TRUSTED_PROXY_HOPS': 0,                          # # of trusted reverse proxies (0 = none); enables X-Forwarded-For
}

CONFIG_SEARCH_PATHS = [
    'wpaste.config.json',
    os.path.expanduser('~/.wpaste.config.json'),
    os.path.expanduser('~/.config/wpaste/wpaste.config.json'),
    '/etc/wpaste.config.json',
]

_SIZE_UNITS = {'B': 1, 'K': 1024, 'KB': 1024, 'M': 1024**2, 'MB': 1024**2,
               'G': 1024**3, 'GB': 1024**3, 'T': 1024**4, 'TB': 1024**4}
_TIME_UNITS = {'s': 1, 'm': 60, 'h': 3600, 'd': 86400}

def parse_size(value):
    '''Accept an int/float of bytes or a string like "16GB"/"100MB"/"512K".'''
    if isinstance(value, (int, float)):
        return int(value)
    s = str(value).strip().upper()
    for unit in sorted(_SIZE_UNITS, key=len, reverse=True):
        if s.endswith(unit):
            return int(float(s[:-len(unit)].strip()) * _SIZE_UNITS[unit])
    return int(float(s))

def parse_duration(value):
    '''Accept an int/float of seconds or a string like "4h"/"30m"/"7d".'''
    if isinstance(value, (int, float)):
        return int(value)
    s = str(value).strip().lower()
    for unit, mult in _TIME_UNITS.items():
        if s.endswith(unit):
            return int(float(s[:-1].strip()) * mult)
    return int(float(s))

def load_config():
    config = dict(DEFAULT_CONFIG)
    for path in CONFIG_SEARCH_PATHS:
        if os.path.isfile(path):
            try:
                with open(path) as f:
                    user_config = json.load(f)
                unknown = set(user_config) - set(DEFAULT_CONFIG)
                if unknown:
                    print(f"Ignoring unknown config keys in {path}: {sorted(unknown)}")
                config.update({k: v for k, v in user_config.items() if k in DEFAULT_CONFIG})
                print(f"Loaded config from {path}")
            except (json.JSONDecodeError, OSError) as e:
                print(f"Failed to load config from {path}: {e}")
            break
    return config

_config = load_config()
BASE_DIR = _config['BASE_DIR']
INDEX_FILE = _config['INDEX_FILE']
INDEX_REWRITE_INTERVAL = int(_config['INDEX_REWRITE_INTERVAL'])
RETENTION_SIZE = parse_size(_config['RETENTION_SIZE'])  # hard-delete files bigger than this
RETENTION_TIME = parse_duration(_config['RETENTION_TIME'])  # purge entries older than this (0 = never)
MAX_CONTENT_LENGTH = parse_size(_config['MAX_CONTENT_LENGTH'])
BOARDS_DIR = _config['BOARDS_DIR']
REGISTRY_FILE = _config['REGISTRY_FILE']
MAX_SESSIONS = int(_config['MAX_SESSIONS'])
PREFER_SECURE_COOKIES = bool(_config['PREFER_SECURE_COOKIES'])
TOTP_MAX_FAILURES = int(_config['TOTP_MAX_FAILURES'])
TOTP_BOARD_MAX_FAILURES = int(_config['TOTP_BOARD_MAX_FAILURES'])
TOTP_LOCKOUT_WINDOW = parse_duration(_config['TOTP_LOCKOUT_TIME'])
ACCESS_RATE_LIMIT = int(_config['ACCESS_RATE_LIMIT'])
ACCESS_RATE_WINDOW = parse_duration(_config['ACCESS_RATE_WINDOW'])
TRUSTED_PROXY_HOPS = int(_config['TRUSTED_PROXY_HOPS'])


def _load_existing_secret(path):
    '''Return the stored cookie key, or None if absent/empty/unsafe. A key file
    readable or writable by group/other may already be exposed, so we refuse it
    (and a fresh one is generated, invalidating existing sessions).'''
    if not os.path.isfile(path):
        return None
    try:
        mode = os.stat(path).st_mode
    except OSError:
        return None
    if mode & 0o077:
        print(f"Warning: {path} has insecure permissions {oct(mode & 0o777)}; "
              f"ignoring it and generating a fresh SECRET_KEY (existing sessions are invalidated).")
        return None
    try:
        with open(path) as f:
            data = f.read().strip()
        return data or None
    except OSError:
        return None

def _persist_secret(path, sk):
    '''Atomically write the key with mode 0o600, replacing any existing file
    (so there is no window where the key sits at the umask default).'''
    tmp = f"{path}.tmp.{os.getpid()}"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'w') as f:
            f.write(sk)
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

def resolve_secret_key(config):
    '''Use the configured SECRET_KEY, or persist a generated one to .wpaste_secret
    so signed session cookies survive restarts.'''
    sk = config.get('SECRET_KEY') or ''
    if sk:
        return sk
    secret_path = '.wpaste_secret'
    existing = _load_existing_secret(secret_path)
    if existing:
        return existing
    sk = secrets.token_urlsafe(48)
    try:
        _persist_secret(secret_path, sk)
    except OSError as e:
        print(f"Warning: could not persist SECRET_KEY to {secret_path}: {e}")
    return sk


app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = MAX_CONTENT_LENGTH
app.config['SECRET_KEY'] = resolve_secret_key(_config)
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = PREFER_SECURE_COOKIES

# Behind N trusted reverse proxies, let Werkzeug rewrite request.remote_addr from
# the proxy-appended end of X-Forwarded-For. This is the ONLY safe way to read
# the real client IP: a raw `XFF.split(',')[0]` returns the client-supplied
# (spoofable) left end. With 0 hops, remote_addr is the direct peer.
if TRUSTED_PROXY_HOPS > 0:
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=TRUSTED_PROXY_HOPS,
                            x_proto=TRUSTED_PROXY_HOPS, x_host=TRUSTED_PROXY_HOPS)

if not os.path.exists(BASE_DIR):
    os.makedirs(BASE_DIR)


def generate_random_id(index, length=8):
    letters = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz123456789'
    randStr = ''.join(random.choice(letters) for i in range(length))
    while randStr in index:
        randStr = ''.join(random.choice(letters) for i in range(length))
    return randStr

# Function to validate if file is an image
def validate_image(stream):
    header = stream.read(512)  # 512 bytes should be enough for a header check
    stream.seek(0)  # Reset stream pointer
    # imghdr had been deprecated, use filetype instead
    kind = filetype.guess(header)
    if kind is not None and kind.mime.startswith('image/'):
        return kind.extension
    # filetype (1.2.0) doesn't recognize JPEG XL; sniff its two signatures:
    # naked codestream (FF 0A) and the ISOBMFF container box ('JXL ').
    if header[:2] == b'\xff\x0a' or header[:12] == b'\x00\x00\x00\x0cJXL \x0d\x0a\x87\x0a':
        return 'jxl'
    return None

def delete_file_on_disk(index, message_id):
    '''Soft-delete (rename to <path>.deleted) unless the file exceeds
    RETENTION_SIZE, in which case hard-delete. Mirrors the original behavior.'''
    if message_id not in index:
        print(f"Message {message_id} not found in index.")
        return
    old_file_path = index[message_id][2]
    new_file_path = f"{old_file_path}.deleted"
    if os.path.exists(old_file_path):
        if os.path.getsize(old_file_path) > RETENTION_SIZE:
            os.remove(old_file_path)
        else:
            os.rename(old_file_path, new_file_path)
    else:
        print(f"File not found: {old_file_path}")
    print(f"Message {message_id} deleted successfully.")


# Default/root board: the original global, public, unowned board.
mainIndex = TSVZ.TSVZed(INDEX_FILE, header=['id', 'unix_time', 'path', 'type', 'filename'],
                        rewrite_interval=INDEX_REWRITE_INTERVAL, verbose=False)
default_board = BoardState(None, mainIndex, BASE_DIR)

rate_limiter = RateLimiter()
boards = Boards(boards_dir=BOARDS_DIR, registry_file=REGISTRY_FILE,
                max_sessions=MAX_SESSIONS, index_rewrite_interval=INDEX_REWRITE_INTERVAL,
                rate_limiter=rate_limiter)


# ---------------------------------------------------------------------------
# Auth / permission helpers
# ---------------------------------------------------------------------------
def get_client_ip():
    # request.remote_addr is the direct peer, or the real client when
    # TRUSTED_PROXY_HOPS>0 (ProxyFix has rewritten it from X-Forwarded-For).
    # Never parse X-Forwarded-For by hand here — its client-facing end is
    # spoofable and would let an attacker dodge per-IP rate limits/lockouts.
    return request.remote_addr or 'unknown'

# TOTP lockout is keyed two ways: per IP (cheap, stops a single host) AND per
# board across all IPs (so header/IP rotation cannot reset the counter). A
# verification is locked out if EITHER ceiling is hit within the window.
def _totp_locked(slug):
    ip = get_client_ip()
    return (rate_limiter.locked_out(f'totp:{slug}:{ip}', TOTP_MAX_FAILURES, TOTP_LOCKOUT_WINDOW) or
            rate_limiter.locked_out(f'totp:{slug}', TOTP_BOARD_MAX_FAILURES, TOTP_LOCKOUT_WINDOW))

def _totp_record_failure(slug):
    ip = get_client_ip()
    rate_limiter.record_failure(f'totp:{slug}:{ip}')
    rate_limiter.record_failure(f'totp:{slug}')

def _totp_clear_failures(slug):
    ip = get_client_ip()
    rate_limiter.clear_failures(f'totp:{slug}:{ip}')
    rate_limiter.clear_failures(f'totp:{slug}')

def _set_session(slug, token):
    b = dict(session.get('b') or {})
    b[slug] = token
    session['b'] = b
    session.permanent = True

def _clear_session(slug):
    b = dict(session.get('b') or {})
    if slug in b:
        del b[slug]
        session['b'] = b

def board_authed(slug):
    '''Cached per-request: is this request authenticated for `slug`?
    True if it carries a valid session token OR a valid live TOTP code
    (the stateless headless path, which does not consume a session slot).'''
    cache = g.setdefault('_authed', {})
    if slug in cache:
        return cache[slug]
    authed = False
    token = (session.get('b') or {}).get(slug)
    if token and boards.valid_token(slug, token):
        authed = True
    else:
        # Header or query only — NOT request.values/form, which would parse the
        # whole request body (up to MAX_CONTENT_LENGTH) just to auth, letting an
        # unauthenticated POST to a non-postable board be buffered before the 401.
        code = request.headers.get('X-TOTP') or request.args.get('totp')
        if code:
            if not _totp_locked(slug):
                if boards.verify(slug, code):
                    _totp_clear_failures(slug)
                    authed = True
                else:
                    _totp_record_failure(slug)
    cache[slug] = authed
    return authed

def _deny(perm):
    '''Abort with a machine-readable 401 so the client knows to prompt for TOTP.'''
    abort(make_response(jsonify({'success': False, 'auth_required': True, 'perm': perm}), 401))

def resolve(slug, action):
    '''Resolve a board and enforce `action` permission. Returns
    (BoardState, perm, authed). Aborts 404/401 on failure.

    slug is None for the default/root board (public, unowned, no admin actions).
    '''
    if slug is None:
        if action == 'admin':
            abort(404)
        return default_board, PUBLIC_PERM, False
    cslug = canonical_slug(slug)
    if cslug is None or not boards.exists(cslug):
        abort(404)
    perm = boards.meta(cslug)['perm']
    authed = board_authed(cslug)
    if not can(perm, action, authed):
        _deny(perm)
    return boards.state(cslug), perm, authed

def board_retention(slug):
    '''Resolve a board's retention (seconds; 0 = never purge).'''
    if slug is None:
        return RETENTION_TIME
    m = boards.meta(slug)
    if not m or m['retention'] in ('', None):
        return RETENTION_TIME
    try:
        return parse_duration(m['retention'])
    except (ValueError, TypeError):
        return RETENTION_TIME

def _purge(state, message_id):
    '''Internal delete used by lazy cleanup (no permission check).'''
    if message_id in state.index:
        delete_file_on_disk(state.index, message_id)
        del state.index[message_id]
        state.bump()


# ---------------------------------------------------------------------------
# curl / plaintext
# ---------------------------------------------------------------------------
def wants_plaintext(req):
    ua = (req.headers.get('User-Agent') or '').lower()
    return any(tok in ua for tok in ('curl', 'wget', 'libcurl', 'httpie'))

def render_plaintext(slug):
    '''Terminal-friendly board dump for curl/wget on the front page.'''
    base = request.host_url.rstrip('/')
    if slug is None:
        state, perm = default_board, PUBLIC_PERM
        title = 'wpaste — public board'
    else:
        if not boards.exists(slug):
            return (f"Board '{slug}' does not exist.\n"
                    f"Create it: POST to {base}/b/{slug}/setup with a TOTP secret + code.\n")
        m = boards.meta(slug)
        perm = m['perm']
        if not can(perm, 'read', board_authed(slug)):
            return (f"Board '{m['display']}' is private.\n"
                    f"Read it: curl -H 'X-TOTP: <code>' {base}/b/{slug}\n")
        state = boards.state(slug)
        title = f"wpaste — board '{m['display']}' ({perm})"
    prefix = f'/b/{slug}' if slug else ''
    retention = board_retention(slug)
    now = datetime.now().timestamp()
    rows = []
    for mid in list(state.index):
        r = state.index[mid] if mid in state.index else None
        if r is None:
            continue
        try:                              # skip blank/partial (tombstone) rows
            unix_time = float(r[1])
        except (ValueError, TypeError, IndexError):
            continue
        if retention and now - unix_time > retention:
            continue
        if not r[2] or not os.path.exists(r[2]):
            continue
        rows.append((unix_time, mid, r[2], r[3], r[4]))
    rows.sort(reverse=True)

    lines = [title, '=' * len(title)]
    if not rows:
        lines.append('(empty)')
    for unix_time, mid, fpath, mtype, fname in rows:
        ts = datetime.fromtimestamp(unix_time).strftime('%Y-%m-%d %H:%M:%S')
        if mtype == 'text':
            with open(fpath) as fh:
                body = fh.read()
            lines.append(f'[{ts}] {mid} text')
            lines.append(body.rstrip('\n'))
        else:
            lines.append(f'[{ts}] {mid} {mtype}  {base}{prefix}/{mtype}/{mid}  ({fname})')
        lines.append('-' * 60)
    auth_hint = '' if slug is None else "  -H 'X-TOTP: <code>'"
    lines += ['', f'post: curl -d "message=hello"{auth_hint} {base}{prefix}/message']
    return '\n'.join(lines) + '\n'


# ---------------------------------------------------------------------------
# Routes — front page + favicon
# ---------------------------------------------------------------------------
@app.route('/', defaults={'slug': None})
@app.route('/b/<slug>')
def index(slug):
    cslug = canonical_slug(slug) if slug else None
    if slug and cslug is None:
        abort(404)
    if wants_plaintext(request):
        return Response(render_plaintext(cslug), mimetype='text/plain; charset=utf-8')
    return render_template('index.html', board=(cslug or ''), version=version)

@app.route('/favicon.ico')
def favicon():
    return app.send_static_file('favicon.ico')


# ---------------------------------------------------------------------------
# Routes — messages (default board: bare paths; named boards: /b/<slug>/...)
# ---------------------------------------------------------------------------
@app.route('/message', methods=['POST'], defaults={'slug': None})
@app.route('/b/<slug>/message', methods=['POST'])
def post_message(slug):
    state, perm, authed = resolve(slug, 'post')
    index = state.index
    today = datetime.now().strftime("%Y-%m-%d")
    dir_path = os.path.join(state.base_dir, today)
    if not os.path.exists(dir_path):
        os.makedirs(dir_path, exist_ok=True)

    message = request.form.get('message', '')
    if message.strip():
        file_id = generate_random_id(index)
        file_path = os.path.join(dir_path, f"{file_id}.txt")
        with open(file_path, 'w') as file:
            file.write(message)
        index[file_id] = [str(datetime.now().timestamp()), file_path, 'text', f"{file_id}.txt"]
        state.bump()

    if 'image' in request.files:
        for image in request.files.getlist('image'):
            if image.filename != '':
                file_id = generate_random_id(index)
                image_extension = validate_image(image.stream)
                if image_extension:
                    file_path = os.path.join(dir_path, f"{file_id}.{image_extension}")
                    image.save(file_path)
                    print(f"Image saved to {file_path}")
                    index[file_id] = [str(datetime.now().timestamp()), file_path, 'image', image.filename]
                    state.bump()
                else:
                    return jsonify({"success": False, "message": f"Invalid image file: {image.filename}"})

    if 'video' in request.files:
        for video in request.files.getlist('video'):
            if video.filename != '':
                file_id = generate_random_id(index)
                video_extension = os.path.splitext(video.filename)[1]
                if video_extension:
                    file_path = os.path.join(dir_path, f"{file_id}{video_extension}")
                    video.save(file_path)
                    print(f"Video saved to {file_path}")
                    index[file_id] = [str(datetime.now().timestamp()), file_path, 'video', video.filename]
                    state.bump()
                else:
                    return jsonify({"success": False, "message": f"Invalid video file: {video.filename}"})

    if 'file' in request.files:
        for file in request.files.getlist('file'):
            if file.filename != '':
                file_id = generate_random_id(index)
                file_extension = os.path.splitext(file.filename)[1]
                file_path = os.path.join(dir_path, f"{file_id}{file_extension}")
                file.save(file_path)
                print(f"File saved to {file_path}")
                index[file_id] = [str(datetime.now().timestamp()), file_path, 'file', file.filename]
                state.bump()

    return jsonify({"success": True, "message": "Message saved successfully."})


@app.route('/last-update', methods=['GET'], defaults={'slug': None})
@app.route('/b/<slug>/last-update', methods=['GET'])
def get_last_update(slug):
    state, perm, authed = resolve(slug, 'read')
    return jsonify({"last_update": state.last_update})


@app.route('/messages', methods=['GET'], defaults={'slug': None})
@app.route('/b/<slug>/messages', methods=['GET'])
def get_messages(slug):
    state, perm, authed = resolve(slug, 'read')
    index = state.index
    retention = board_retention(state.slug)
    prefix = f'/b/{state.slug}' if state.slug else ''
    messages = []
    message_to_delete = []
    now = datetime.now().timestamp()
    # Iterate over a snapshot of the keys so a concurrent POST/delete cannot
    # mutate the index mid-iteration; re-check membership before each access.
    for id in list(index):
        row = index[id] if id in index else None
        if row is None:
            continue
        # A deleted entry can resurrect as a blank/partial row after a TSVZ
        # reload (its tombstone reloads with empty fields). Treat any row whose
        # timestamp won't parse as a stale entry to reap, rather than letting
        # float('') take down the whole listing with a 500.
        try:
            unix_time = float(row[1])
        except (ValueError, TypeError, IndexError):
            message_to_delete.append(id)
            continue
        file_path, msg_type = row[2], row[3]
        if retention and now - unix_time > retention:
            message_to_delete.append(id)
            continue
        if not os.path.exists(file_path):
            message_to_delete.append(id)
            continue
        if msg_type == 'image':
            content = f'{prefix}/image/{id}'
        elif msg_type == 'text':
            with open(file_path, 'r') as file:
                content = file.read()
        elif msg_type == 'video':
            content = f'{prefix}/video/{id}'
        elif msg_type == 'file':
            content = f'{prefix}/file/{id}'
        else:
            content = "Content type not supported."
        messages.append({"id": id, "content": content, "timestamp": int(unix_time), "type": msg_type, "filename": row[4]})
    messages.reverse()
    for id in message_to_delete:
        _purge(state, id)
    meta = boards.meta(state.slug) if state.slug else None
    return jsonify({"messages": messages, "board": state.slug, "perm": perm,
                    "authed": authed, "display": (meta['display'] if meta else None),
                    "retention": (meta['retention'] if meta else None)})


@app.route('/image/<message_id>', methods=['GET'], defaults={'slug': None})
@app.route('/video/<message_id>', methods=['GET'], defaults={'slug': None})
@app.route('/file/<message_id>', methods=['GET'], defaults={'slug': None})
@app.route('/b/<slug>/image/<message_id>', methods=['GET'])
@app.route('/b/<slug>/video/<message_id>', methods=['GET'])
@app.route('/b/<slug>/file/<message_id>', methods=['GET'])
def get_file(slug, message_id):
    state, perm, authed = resolve(slug, 'read')
    index = state.index
    message_id = os.path.splitext(message_id)[0]  # tolerate an extension in the URL
    if message_id in index:
        file_path = index[message_id][2]
        # Confine to this board's directory before serving.
        base = os.path.normpath(state.base_dir)
        if os.path.commonpath([base, os.path.normpath(file_path)]) != base:
            abort(404, description="Path not valid.")
        if os.path.exists(file_path):
            mime = filetype.guess(file_path)
            if mime is not None:
                return send_file(file_path, mimetype=mime.mime, download_name=index[message_id][4])
            return send_file(file_path, download_name=index[message_id][4])
        abort(404, description="File not found.")
    abort(404, description="Message not found.")


@app.route('/delete_all', methods=['POST'], defaults={'slug': None})
@app.route('/b/<slug>/delete_all', methods=['POST'])
def delete_all_messages(slug):
    state, perm, authed = resolve(slug, 'delete')
    for id in list(state.index):
        delete_file_on_disk(state.index, id)
    state.index.clear()
    state.bump()
    return jsonify({"success": True, "message": "All messages have been deleted."})


@app.route('/delete/<message_id>', methods=['POST'], defaults={'slug': None})
@app.route('/b/<slug>/delete/<message_id>', methods=['POST'])
def delete_message(slug, message_id):
    state, perm, authed = resolve(slug, 'delete')
    if message_id in state.index:
        delete_file_on_disk(state.index, message_id)
        del state.index[message_id]
        state.bump()
        return jsonify({"success": True, "message": f"Message {message_id} deleted successfully."})
    return jsonify({"success": False, "message": "Message not found."})


# ---------------------------------------------------------------------------
# Routes — board access / auth lifecycle
# ---------------------------------------------------------------------------
@app.route('/b/<slug>/access', methods=['GET'])
def board_access(slug):
    '''Tell the client what to do for `slug`: open / setup / login.
    Rate-limited per IP (this is also the board-enumeration oracle).'''
    if not rate_limiter.allow(f'access:{get_client_ip()}', ACCESS_RATE_LIMIT, ACCESS_RATE_WINDOW):
        abort(429)
    cslug = canonical_slug(slug)
    if cslug is None:
        return jsonify({"ok": False, "message": "Invalid board name."}), 400
    if not boards.exists(cslug):
        candidate = new_secret()
        display = (request.args.get('display') or slug).strip()
        return jsonify({"ok": True, "slug": cslug, "exists": False, "action": "setup",
                        "display": display, "secret": candidate,
                        "otpauth": provisioning_uri(candidate, display)})
    m = boards.meta(cslug)
    authed = board_authed(cslug)
    action = 'open' if (authed or can(m['perm'], 'read', False)) else 'login'
    return jsonify({"ok": True, "slug": cslug, "exists": True, "action": action,
                    "perm": m['perm'], "authed": authed, "display": m['display']})


@app.route('/b/<slug>/setup', methods=['POST'])
def board_setup(slug):
    cslug = canonical_slug(slug)
    if cslug is None:
        return jsonify({"success": False, "message": "Invalid board name."}), 400
    if boards.exists(cslug):
        return jsonify({"success": False, "message": "Board already exists.", "exists": True}), 409
    # Setup checks a client-supplied secret against a client-supplied code (no
    # server secret to brute-force), so a per-IP limit on abuse is enough here.
    key = f'setup:{cslug}:{get_client_ip()}'
    if rate_limiter.locked_out(key, TOTP_MAX_FAILURES, TOTP_LOCKOUT_WINDOW):
        return jsonify({"success": False, "message": "Too many attempts. Try again later."}), 429
    secret = (request.form.get('secret') or '').strip()
    code = (request.form.get('code') or '').strip()
    display = (request.form.get('display') or slug).strip()
    if not secret or not verify_code(secret, code):
        rate_limiter.record_failure(key)
        return jsonify({"success": False, "message": "Invalid code."}), 401
    rate_limiter.clear_failures(key)
    # Atomic create: closes the check-then-create race where two concurrent
    # setups of the same new name would both succeed (the second resetting the
    # first's secret/session).
    if boards.create(cslug, display, secret=secret) is None:
        return jsonify({"success": False, "message": "Board already exists.", "exists": True}), 409
    token = boards.issue_token(cslug)
    _set_session(cslug, token)
    return jsonify({"success": True, "slug": cslug, "perm": DEFAULT_PERM,
                    "authed": True, "display": display})


@app.route('/b/<slug>/login', methods=['POST'])
def board_login(slug):
    cslug = canonical_slug(slug)
    if cslug is None or not boards.exists(cslug):
        return jsonify({"success": False, "message": "No such board."}), 404
    if _totp_locked(cslug):
        return jsonify({"success": False, "message": "Too many attempts. Try again later."}), 429
    code = (request.form.get('code') or request.headers.get('X-TOTP') or '').strip()
    if not boards.verify(cslug, code):
        _totp_record_failure(cslug)
        return jsonify({"success": False, "message": "Invalid code."}), 401
    _totp_clear_failures(cslug)
    token = boards.issue_token(cslug)
    _set_session(cslug, token)
    m = boards.meta(cslug)
    return jsonify({"success": True, "slug": cslug, "perm": m['perm'],
                    "authed": True, "display": m['display']})


@app.route('/b/<slug>/logout', methods=['POST'])
def board_logout(slug):
    cslug = canonical_slug(slug)
    if cslug:
        token = (session.get('b') or {}).get(cslug)
        if token:
            boards.revoke_token(cslug, token)
        _clear_session(cslug)
    return jsonify({"success": True})


@app.route('/b/<slug>/settings', methods=['POST'])
def board_settings(slug):
    state, perm, authed = resolve(slug, 'admin')
    cslug = state.slug
    if 'perm' in request.form:
        newperm = request.form['perm'].strip()
        if newperm not in PERMS:
            return jsonify({"success": False, "message": "Invalid permission."}), 400
        boards.set_perm(cslug, newperm)
    if 'retention' in request.form:
        rv = request.form['retention'].strip()
        if rv != '':
            try:
                parse_duration(rv)
            except (ValueError, TypeError):
                return jsonify({"success": False, "message": "Invalid retention."}), 400
        boards.set_retention(cslug, rv)
    state.bump()
    m = boards.meta(cslug)
    return jsonify({"success": True, "perm": m['perm'], "retention": m['retention']})


@app.route('/b/<slug>/delete_board', methods=['POST'])
def board_delete(slug):
    state, perm, authed = resolve(slug, 'admin')
    cslug = state.slug
    boards.delete(cslug)
    _clear_session(cslug)
    return jsonify({"success": True})


# ---------------------------------------------------------------------------
# Admin CLI: `python app.py admin <list|remove-board|regen-totp>`
# Run while the service is stopped (it mutates TSVZ-backed files the running
# process holds in memory).
# ---------------------------------------------------------------------------
def run_admin(argv):
    import argparse
    parser = argparse.ArgumentParser(prog='app.py admin', description='wpaste board administration')
    sub = parser.add_subparsers(dest='cmd', required=True)
    sub.add_parser('list', help='list boards')
    p_rm = sub.add_parser('remove-board', help='delete a board and all its data')
    p_rm.add_argument('slug')
    p_rg = sub.add_parser('regen-totp', help='issue a new TOTP secret (invalidates all sessions)')
    p_rg.add_argument('slug')
    args = parser.parse_args(argv)

    if args.cmd == 'list':
        rows = boards.list_boards()
        if not rows:
            print('No boards.')
        for m in rows:
            ntok = len([t for t in boards.registry[m['slug']][4].split(',') if t])
            print(f"{m['slug']}\tperm={m['perm']}\tsessions={ntok}\tcreated={m['created']}\tdisplay={m['display']}")
    elif args.cmd == 'remove-board':
        cslug = canonical_slug(args.slug)
        if cslug and boards.delete(cslug):
            print(f"Removed board '{cslug}'.")
        else:
            print(f"No such board: {args.slug}")
    elif args.cmd == 'regen-totp':
        cslug = canonical_slug(args.slug)
        if not cslug or not boards.exists(cslug):
            print(f"No such board: {args.slug}")
        else:
            secret = boards.regen_secret(cslug)
            m = boards.meta(cslug)
            print(f"New TOTP for board '{cslug}':")
            print(f"  secret:  {secret}")
            print(f"  otpauth: {provisioning_uri(secret, m['display'])}")
            print("All existing sessions were invalidated.")

    boards.registry.close()
    mainIndex.close()


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'admin':
        run_admin(sys.argv[2:])
    else:
        app.run(host=_config['HOST'], port=int(_config['PORT']), debug=bool(_config['DEBUG']))

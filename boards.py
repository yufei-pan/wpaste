#!/usr/bin/env python3
'''
boards.py — named, TOTP-authenticated boards for wpaste.

This module owns everything that the single global board in app.py does NOT:
the board registry, per-board message indexes, TOTP secrets, session tokens,
permission decisions, and a tiny in-memory rate limiter. app.py stays thin
routing and delegates here.

Storage:
  - boards.nsv            TSVZ registry (null-separated), keyed by canonical slug.
  - boards/<slug>/index.tsv   per-board TSVZ message index (same shape as the
                              default board's mainIndex.tsv).
  - boards/<slug>/<date>/<id>.<ext>   per-board files.

Registry row (mirrors the project-wide TSVZ "offset" gotcha: the value list
INCLUDES the key at element [0], but writes assign only the trailing columns):
    header = [slug, secret, perm, retention, tokens, created, display]
    write:  registry[slug] = [secret, perm, retention, tokens, created, display]
    read:   registry[slug][1]=secret [2]=perm [3]=retention [4]=tokens
            [5]=created [6]=display
'''
import os
import re
import time
import shutil
import secrets
import threading
from collections import deque

import TSVZ
import pyotp

# print with flush on, matching app.py's convention.
from functools import partial
print = partial(print, flush=True)

# ---------------------------------------------------------------------------
# Permission model
# ---------------------------------------------------------------------------
# A board has one of these levels. "Owner" == anyone holding a valid session
# for the board (TOTP is the only credential), so an authenticated request can
# always do everything. The table below is the *public* (unauthenticated)
# capability for each level.
PERMS = ('private', 'read', 'append', 'public')
DEFAULT_PERM = 'private'        # new named boards start fully closed
PUBLIC_PERM = 'public'         # the default/root board behaves like this

# Actions: 'read' (list/serve), 'post', 'delete' (delete one / clear all),
# 'admin' (change perms/retention, delete board) — admin is session-only.
def can(perm, action, authed):
    '''Return True if `action` is allowed on a board at `perm` for a request
    that is (authed) or is not authenticated.'''
    if authed:
        return True
    if action == 'read':
        return perm in ('read', 'append', 'public')
    if action == 'post':
        return perm in ('append', 'public')
    if action == 'delete':
        return perm == 'public'
    # 'admin' and anything unknown: authenticated only.
    return False


# ---------------------------------------------------------------------------
# Slug canonicalization (must match the JS canonicalizeSlug() in scripts.js)
# ---------------------------------------------------------------------------
# Reserved values are compared AFTER canonicalization, so they must be in
# canonical form ("default", not "__default__").
RESERVED_SLUGS = {'default'}
_SLUG_SUB = re.compile(r'[^a-z0-9]+')

def canonical_slug(raw, maxlen=64):
    '''Fold arbitrary user input to a filesystem- and URL-safe slug, or return
    None if nothing usable remains. Distinct inputs that fold to the same slug
    intentionally address the same board.'''
    if raw is None:
        return None
    s = _SLUG_SUB.sub('-', str(raw).strip().lower()).strip('-')
    s = s[:maxlen].strip('-')
    if not s or s in RESERVED_SLUGS:
        return None
    return s


# ---------------------------------------------------------------------------
# TOTP helpers
# ---------------------------------------------------------------------------
def new_secret():
    return pyotp.random_base32()

def provisioning_uri(secret, display, issuer='wpaste'):
    return pyotp.TOTP(secret).provisioning_uri(name=display or 'board', issuer_name=issuer)

def verify_code(secret, code):
    if not secret or not code:
        return False
    try:
        return pyotp.TOTP(secret).verify(str(code).strip(), valid_window=1)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Rate limiter — in-memory, single-process (wpaste runs one worker).
# ---------------------------------------------------------------------------
class RateLimiter:
    '''Sliding-window event throttle plus failure-based lockout. Keys are
    client-derived (IP, board slug), so they must not accumulate forever: each
    op drops its key when its deque empties, and a periodic sweep reaps keys
    that were touched once and abandoned.'''
    def __init__(self, sweep_interval=600.0):
        self._events = {}    # key -> deque[timestamps]
        self._fails = {}     # key -> deque[timestamps]
        self._lock = threading.Lock()
        self._max_window = 0.0          # widest window seen, for the sweep horizon
        self._sweep_interval = sweep_interval
        self._last_sweep = time.monotonic()

    def _sweep(self, now):
        '''Drop keys whose newest timestamp is older than any live window.'''
        if now - self._last_sweep < self._sweep_interval:
            return
        self._last_sweep = now
        for store in (self._events, self._fails):
            for k in list(store):
                dq = store[k]
                while dq and now - dq[0] > self._max_window:
                    dq.popleft()
                if not dq:
                    del store[k]

    def allow(self, key, max_events, window):
        '''Record an event for `key`; return False if it exceeds `max_events`
        within the trailing `window` seconds.'''
        now = time.monotonic()
        with self._lock:
            self._max_window = max(self._max_window, window)
            self._sweep(now)
            dq = self._events.setdefault(key, deque())
            while dq and now - dq[0] > window:
                dq.popleft()
            if len(dq) >= max_events:
                return False
            dq.append(now)
            return True

    def record_failure(self, key):
        now = time.monotonic()
        with self._lock:
            self._sweep(now)
            self._fails.setdefault(key, deque()).append(now)

    def locked_out(self, key, max_fails, window):
        '''True if `key` has at least `max_fails` failures in the trailing
        `window` seconds.'''
        now = time.monotonic()
        with self._lock:
            self._max_window = max(self._max_window, window)
            self._sweep(now)
            dq = self._fails.setdefault(key, deque())
            while dq and now - dq[0] > window:
                dq.popleft()
            locked = len(dq) >= max_fails
            if not dq:                  # don't leave an empty key behind
                del self._fails[key]
            return locked

    def clear_failures(self, key):
        with self._lock:
            self._fails.pop(key, None)


# ---------------------------------------------------------------------------
# Per-board in-memory state
# ---------------------------------------------------------------------------
class BoardState:
    '''Holds a board's live index and its own update clock. Used for both the
    default/root board (slug=None) and named boards.'''
    def __init__(self, slug, index, base_dir):
        self.slug = slug              # None for the default/root board
        self.index = index            # TSVZ.TSVZed
        self.base_dir = base_dir      # directory files are written under
        self.last_update = time.time_ns()

    def bump(self):
        self.last_update = time.time_ns()


# ---------------------------------------------------------------------------
# Board manager — registry + named board states
# ---------------------------------------------------------------------------
REGISTRY_HEADER = ['slug', 'secret', 'perm', 'retention', 'tokens', 'created', 'display']

class Boards:
    def __init__(self, *, boards_dir, registry_file, max_sessions,
                 index_rewrite_interval, rate_limiter):
        self.boards_dir = boards_dir
        self.max_sessions = int(max_sessions)
        self.index_rewrite_interval = int(index_rewrite_interval)
        self.rl = rate_limiter
        os.makedirs(boards_dir, exist_ok=True)
        self.registry = TSVZ.TSVZed(registry_file, header=REGISTRY_HEADER,
                                    rewrite_interval=self.index_rewrite_interval,
                                    verbose=False)
        self._states = {}             # slug -> BoardState
        # Reentrant: mutators hold it across a read-modify-write AND call the
        # readers below (which also take it), and readers must take it so they
        # never observe the registry mid-rebuild in delete().
        self._lock = threading.RLock()

    # --- existence / metadata ---------------------------------------------
    def exists(self, slug):
        with self._lock:
            return slug in self.registry

    def list_boards(self):
        with self._lock:
            return [m for m in (self.meta(s) for s in list(self.registry)) if m]

    def meta(self, slug):
        '''Return {slug, secret, perm, retention, created, display} or None.'''
        with self._lock:
            row = self.registry[slug] if slug in self.registry else None
            if row is None:
                return None
            return {
                'slug': slug,
                'secret': row[1],
                'perm': row[2] if row[2] in PERMS else DEFAULT_PERM,
                'retention': row[3],    # '' = inherit site default, else seconds
                'created': row[5],
                'display': row[6],
            }

    def _write_row(self, slug, *, secret, perm, retention, tokens, created, display):
        # tokens is a list[str]; store comma-joined (tokens are url-safe, no commas).
        self.registry[slug] = [secret, perm, str(retention), ','.join(tokens),
                               str(created), display]

    # --- per-board live state ---------------------------------------------
    def state(self, slug):
        '''Lazily create/load a named board's BoardState (index + base dir).'''
        st = self._states.get(slug)
        if st is None:
            base_dir = os.path.join(self.boards_dir, slug)
            os.makedirs(base_dir, exist_ok=True)
            index = TSVZ.TSVZed(os.path.join(base_dir, 'index.tsv'),
                                header=['id', 'unix_time', 'path', 'type', 'filename'],
                                rewrite_interval=self.index_rewrite_interval,
                                verbose=False)
            st = BoardState(slug, index, base_dir)
            self._states[slug] = st
        return st

    # --- lifecycle ---------------------------------------------------------
    def create(self, slug, display, perm=DEFAULT_PERM, retention='', secret=None):
        '''Atomically register a NEW board and return its TOTP secret, or None if
        a board with this slug already exists. No session yet. Pass `secret` to
        commit a secret already shown to the user as a QR.'''
        secret = secret or new_secret()
        with self._lock:
            if slug in self.registry:
                return None
            self._write_row(slug, secret=secret,
                            perm=perm if perm in PERMS else DEFAULT_PERM,
                            retention=retention, tokens=[],
                            created=int(time.time()), display=display or slug)
        return secret

    def set_perm(self, slug, perm):
        if perm not in PERMS:
            return False
        # Read meta INSIDE the lock so the read-modify-write is atomic; reading
        # it outside lets a concurrent write (e.g. regen_secret) be clobbered.
        with self._lock:
            m = self.meta(slug)
            if not m:
                return False
            self._write_row(slug, secret=m['secret'], perm=perm,
                            retention=m['retention'], tokens=self._tokens(slug),
                            created=m['created'], display=m['display'])
        return True

    def set_retention(self, slug, retention):
        with self._lock:
            m = self.meta(slug)
            if not m:
                return False
            self._write_row(slug, secret=m['secret'], perm=m['perm'],
                            retention=retention, tokens=self._tokens(slug),
                            created=m['created'], display=m['display'])
        return True

    def regen_secret(self, slug):
        '''Admin: issue a fresh secret and invalidate every session.'''
        secret = new_secret()
        with self._lock:
            m = self.meta(slug)
            if not m:
                return None
            self._write_row(slug, secret=secret, perm=m['perm'],
                            retention=m['retention'], tokens=[],
                            created=m['created'], display=m['display'])
        return secret

    def delete(self, slug):
        '''Remove a board: drop its registry row, files, and live state.

        TSVZ in this version does not persist a single-key deletion across a
        reload (the tombstone reloads as an empty row), so we rebuild the
        registry from its surviving rows instead — reliable and cheap for the
        modest number of boards.'''
        existed = slug in self.registry
        # Stop the per-board index's append thread before removing its file,
        # or it errors flushing to a path that no longer exists.
        st = self._states.pop(slug, None)
        if st is not None:
            try:
                st.index.close()
            except Exception:
                pass
        board_dir = os.path.join(self.boards_dir, slug)
        if os.path.isdir(board_dir):
            shutil.rmtree(board_dir, ignore_errors=True)
        if existed:
            with self._lock:
                survivors = {k: list(self.registry[k][1:])
                             for k in list(self.registry) if k != slug}
                self.registry.clear()        # truncates the file
                for k, trailing in survivors.items():
                    self.registry[k] = trailing
        return existed

    # --- sessions / tokens -------------------------------------------------
    def _tokens(self, slug):
        with self._lock:
            row = self.registry[slug] if slug in self.registry else None
            if row is None:
                return []
            return [t for t in row[4].split(',') if t]

    def valid_token(self, slug, token):
        return bool(token) and token in self._tokens(slug)

    def issue_token(self, slug):
        '''Create a new session token, evict the oldest beyond max_sessions,
        persist, and return it. Returns None if the board is gone.'''
        token = secrets.token_urlsafe(18)
        with self._lock:
            m = self.meta(slug)
            if not m:
                return None
            dq = deque(self._tokens(slug), maxlen=self.max_sessions)
            dq.append(token)           # evicts oldest when full
            self._write_row(slug, secret=m['secret'], perm=m['perm'],
                            retention=m['retention'], tokens=list(dq),
                            created=m['created'], display=m['display'])
        return token

    def revoke_token(self, slug, token):
        with self._lock:
            m = self.meta(slug)
            if not m:
                return
            tokens = [t for t in self._tokens(slug) if t != token]
            self._write_row(slug, secret=m['secret'], perm=m['perm'],
                            retention=m['retention'], tokens=tokens,
                            created=m['created'], display=m['display'])

    # --- totp --------------------------------------------------------------
    def verify(self, slug, code):
        m = self.meta(slug)
        return bool(m) and verify_code(m['secret'], code)

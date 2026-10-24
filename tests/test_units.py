import time

import pytest

import app as wpaste
from boards import PERMS, RateLimiter, can, canonical_slug


# --- permission matrix -------------------------------------------------------
# What an anonymous visitor may do at each level; a logged-in owner may do all.
ANON = {
    'private': set(),
    'read':    {'read'},
    'append':  {'read', 'post'},
    'public':  {'read', 'post', 'delete'},
}

@pytest.mark.parametrize('perm', PERMS)
@pytest.mark.parametrize('action', ['read', 'post', 'delete', 'admin', 'bogus'])
def test_can(perm, action):
    assert can(perm, action, authed=False) == (action in ANON[perm])
    assert can(perm, action, authed=True)


# --- slugs -------------------------------------------------------------------
@pytest.mark.parametrize('raw, slug', [
    ('My Board!', 'my-board'),
    ('  --x--  ', 'x'),
    ('a__b..c', 'a-b-c'),
    ('x' * 80, 'x' * 64),
    ('default', None),
    ('__DEFAULT__', None),
    ('', None),
    ('日本', None),
    (None, None),
])
def test_canonical_slug(raw, slug):
    assert canonical_slug(raw) == slug


# --- sizes and durations -----------------------------------------------------
@pytest.mark.parametrize('value, size', [
    (512, 512), ('512', 512), ('100MB', 100 * 1024**2), ('16gb', 16 * 1024**3),
    ('1.5K', 1536), ('0', 0),
])
def test_parse_size(value, size):
    assert wpaste.parse_size(value) == size

@pytest.mark.parametrize('value, seconds', [
    (3600, 3600), ('90', 90), ('30m', 1800), ('4h', 14400), ('7d', 604800),
    ('1.5h', 5400), ('0', 0), ('0s', 0),
])
def test_parse_duration(value, seconds):
    assert wpaste.parse_duration(value) == seconds

@pytest.mark.parametrize('value', ['-1', -1, '-5m', 'inf', 'nan', 'abc', '4w', ''])
def test_bad_amounts_are_rejected(value):
    with pytest.raises(ValueError):
        wpaste.parse_duration(value)
    with pytest.raises(ValueError):
        wpaste.parse_size(value)


# --- staleness ---------------------------------------------------------------
def test_stale():
    now = time.time()
    fresh = ['id', str(now - 10), 'p', 'text', 'f']
    old = ['id', str(now - 100), 'p', 'text', 'f']
    assert not wpaste._stale(fresh, 60, now)
    assert wpaste._stale(old, 60, now)
    assert not wpaste._stale(old, 0, now)                       # 0 = keep forever
    assert wpaste._stale(['id', '', '', '', ''], 0, now)        # blank (old tombstone)


# --- rate limiter ------------------------------------------------------------
def test_rate_limiter_allows_up_to_the_limit():
    rl = RateLimiter()
    assert all(rl.allow('k', 3, 60) for _ in range(3))
    assert not rl.allow('k', 3, 60)
    assert rl.allow('other', 3, 60)

def test_rate_limiter_lockout_and_clear():
    rl = RateLimiter()
    for _ in range(2):
        assert not rl.locked_out('k', 3, 60)
        rl.record_failure('k')
    rl.record_failure('k')
    assert rl.locked_out('k', 3, 60)
    rl.clear_failures('k')
    assert not rl.locked_out('k', 3, 60)

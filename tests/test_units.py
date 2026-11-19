import base64
from datetime import datetime, timezone
import hmac
import struct
import time

import pyotp
import pytest

import app as wpaste
import boards
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


# --- TOTP time ---------------------------------------------------------------
def rfc6238(secret, at):
    '''The code at Unix time `at` by RFC 6238 itself (SHA-1, 30 s, 6 digits), apart from pyotp.'''
    key = base64.b32decode(secret, casefold=True)
    mac = hmac.new(key, struct.pack('>Q', int(at) // 30), 'sha1').digest()
    offset = mac[-1] & 15
    return '%06d' % ((struct.unpack('>I', mac[offset:offset + 4])[0] & 0x7FFFFFFF) % 10**6)


@pytest.fixture
def pacific(monkeypatch):
    '''Local time on the US Pacific coast, whose clocks went back from 02:00 PDT to 01:00 PST on 2026-11-01 (a POSIX
    rule, so no zoneinfo file is needed).'''
    monkeypatch.setenv('TZ', 'PST8PDT,M3.2.0,M11.1.0')
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def test_verify_code_in_the_hour_daylight_saving_repeats(pacific):
    '''pyotp before 2.12 counts a naive time from local time through time.mktime, which in the second 01:30 reads an
    hour early: every valid code was refused, the phone's too. Both 01:30s take their own code.'''
    first, second = (datetime(2026, 11, 1, h, 30, tzinfo=timezone.utc) for h in (8, 9))   # 01:30 PDT, 01:30 PST
    naive = datetime.fromtimestamp(second.timestamp())
    assert time.mktime(naive.timetuple()) - second.timestamp() == -3600     # the premise: this hour is the broken one
    secret = pyotp.random_base32()
    for at in (first, second):
        assert boards.verify_code(secret, rfc6238(secret, at.timestamp()), now=at), at
        assert not boards.verify_code(secret, rfc6238(secret, at.timestamp() - 3600), now=at), at


def test_verify_code_hands_pyotp_an_aware_time(monkeypatch):
    '''The default path (now): pyotp is given this moment, timezone-aware, never a naive time or none.'''
    seen = []

    def verify(self, otp, for_time=None, valid_window=0):
        seen.append(for_time)
        return True
    monkeypatch.setattr(pyotp.TOTP, 'verify', verify)
    assert boards.verify_code(pyotp.random_base32(), '123456')
    [for_time] = seen
    assert for_time.tzinfo is not None
    assert abs(for_time.timestamp() - time.time()) < 5

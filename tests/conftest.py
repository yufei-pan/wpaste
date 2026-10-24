'''Test setup. app.py loads its config and opens its data files at import, in
the current directory, so the whole session runs in a throwaway directory with
its own wpaste.config.json (the first config path searched).'''
import io
import json
import os
import shutil
import sys
import tempfile
import uuid

import pyotp
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

WORKDIR = tempfile.mkdtemp(prefix='wpaste-test-')
os.chdir(WORKDIR)
with open('wpaste.config.json', 'w') as f:
    json.dump({'DEBUG': False}, f)

import app as wpaste  # noqa: E402  (must follow the chdir above)
from boards import RateLimiter  # noqa: E402

# Tests call wpaste.sweep() themselves; keep the background sweeper from
# starting on the first request and racing them.
wpaste._sweeper_started.acquire()


@pytest.fixture(scope='session', autouse=True)
def close_indexes():
    # Close while still in WORKDIR: the TSVZ files are opened by relative path,
    # and pytest returns to the invocation dir before pytest_sessionfinish.
    yield
    wpaste.mainIndex.close()
    for state in list(wpaste.boards._states.values()):
        state.index.close()
    wpaste.boards.registry.close()


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(WORKDIR, ignore_errors=True)


@pytest.fixture(autouse=True)
def fresh_state(monkeypatch):
    '''Each test starts with an empty default board, no rate-limit history and
    the default retention settings.'''
    wpaste.default_board.index.clear()
    monkeypatch.setattr(wpaste, 'rate_limiter', RateLimiter())
    monkeypatch.setattr(wpaste, 'RETENTION_TIME', 0)
    monkeypatch.setattr(wpaste, 'DELETED_RETENTION_TIME', 0)


@pytest.fixture
def client():
    return wpaste.app.test_client()


def new_board(client, perm=None):
    '''Create a board with a random name; `client` ends up logged in to it.
    Returns (slug, secret).'''
    slug = 'b-' + uuid.uuid4().hex[:12]
    secret = pyotp.random_base32()
    r = client.post(f'/b/{slug}/setup', data={'secret': secret, 'code': pyotp.TOTP(secret).now()})
    assert r.status_code == 200, r.get_json()
    if perm:
        r = client.post(f'/b/{slug}/settings', data={'perm': perm})
        assert r.status_code == 200, r.get_json()
    return slug, secret


def upload(client, field, data, filename, prefix=''):
    return client.post(f'{prefix}/message', data={field: (io.BytesIO(data), filename)})


def messages(client, prefix=''):
    r = client.get(f'{prefix}/messages')
    assert r.status_code == 200
    return r.get_json()['messages']

import base64
import glob
import os
import re
import shutil
import subprocess
import sys
import time
import uuid

import pyotp
import pytest

import app as wpaste
from conftest import ROOT, messages, new_board, upload

PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 64


def backdate(index, message_id, seconds):
    row = index[message_id]
    index[message_id] = [str(float(row[1]) - seconds)] + list(row[2:])


# --- permissions ---------------------------------------------------------------
@pytest.mark.parametrize('perm, can_read, can_post, can_delete', [
    ('private', False, False, False),
    ('read',    True,  False, False),
    ('append',  True,  True,  False),
    ('public',  True,  True,  True),
])
def test_anonymous_access_follows_the_board_level(client, perm, can_read, can_post, can_delete):
    slug, _ = new_board(client, perm)
    client.post(f'/b/{slug}/message', data={'message': 'owner note'})
    mid = messages(client, f'/b/{slug}')[0]['id']

    anon = wpaste.app.test_client()
    expect = lambda allowed: 200 if allowed else 401
    assert anon.get(f'/b/{slug}/messages').status_code == expect(can_read)
    assert anon.get(f'/b/{slug}/text/{mid}').status_code == expect(can_read)
    assert anon.get(f'/b/{slug}/last-update').status_code == expect(can_read)
    assert anon.post(f'/b/{slug}/message', data={'message': 'x'}).status_code == expect(can_post)
    assert anon.post(f'/b/{slug}/delete/{mid}').status_code == expect(can_delete)
    # Settings and board deletion always need the code.
    assert anon.post(f'/b/{slug}/settings', data={'perm': 'public'}).status_code == 401
    assert anon.post(f'/b/{slug}/delete_board').status_code == 401

def test_unknown_and_reserved_boards_are_404(client):
    assert client.get('/b/no-such-board/messages').status_code == 404
    assert client.get('/b/default/messages').status_code == 404
    assert client.get('/b/!!!/messages').status_code == 404

def test_default_board_has_no_admin_actions(client):
    assert client.post('/settings', data={'perm': 'private'}).status_code in (404, 405)


# --- setup, login, sessions ----------------------------------------------------
def test_setup_rejects_a_wrong_code(client):
    secret = pyotp.random_base32()
    r = client.post('/b/wrong-code-board/setup', data={'secret': secret, 'code': '000000'})
    assert r.status_code == 401
    assert client.get('/b/wrong-code-board/messages').status_code == 404

def test_setup_of_an_existing_board_is_refused(client):
    slug, _ = new_board(client)
    secret = pyotp.random_base32()
    r = wpaste.app.test_client().post(f'/b/{slug}/setup', data={'secret': secret, 'code': pyotp.TOTP(secret).now()})
    assert r.status_code == 409

def test_login_and_logout(client):
    slug, secret = new_board(client)
    other = wpaste.app.test_client()
    assert other.post(f'/b/{slug}/login', data={'code': '000000'}).status_code == 401
    assert other.post(f'/b/{slug}/login', data={'code': pyotp.TOTP(secret).now()}).status_code == 200
    assert other.get(f'/b/{slug}/messages').status_code == 200
    assert other.post(f'/b/{slug}/logout').status_code == 200
    assert other.get(f'/b/{slug}/messages').status_code == 401

def test_oldest_session_is_evicted(client):
    slug, secret = new_board(client)            # session 1
    for _ in range(wpaste.MAX_SESSIONS):        # sessions 2..MAX+1
        c = wpaste.app.test_client()
        assert c.post(f'/b/{slug}/login', data={'code': pyotp.TOTP(secret).now()}).status_code == 200
    assert client.get(f'/b/{slug}/messages').status_code == 401
    assert c.get(f'/b/{slug}/messages').status_code == 200

def test_wrong_codes_lock_out_even_the_right_code(client):
    slug, secret = new_board(client)
    other = wpaste.app.test_client()
    for _ in range(wpaste.TOTP_MAX_FAILURES):
        assert other.post(f'/b/{slug}/login', data={'code': '000000'}).status_code == 401
    assert other.post(f'/b/{slug}/login', data={'code': pyotp.TOTP(secret).now()}).status_code == 429

def test_headless_totp_header(client):
    slug, secret = new_board(client)
    anon = wpaste.app.test_client()
    assert anon.get(f'/b/{slug}/messages').status_code == 401
    assert anon.get(f'/b/{slug}/messages', headers={'X-TOTP': pyotp.TOTP(secret).now()}).status_code == 200
    # The code must not come from the form body.
    assert anon.post(f'/b/{slug}/message', data={'message': 'x', 'totp': pyotp.TOTP(secret).now()}).status_code == 401


# --- uploads and file serving --------------------------------------------------
def test_uploaded_html_is_sandboxed(client):
    upload(client, 'file', b'<script>alert(1)</script>', 'evil.html')
    r = client.get(messages(client)[0]['content'])
    assert r.status_code == 200
    assert r.headers['Content-Security-Policy'] == 'sandbox'
    assert r.headers['X-Content-Type-Options'] == 'nosniff'

def test_pdf_is_not_sandboxed(client):
    upload(client, 'file', b'%PDF-1.4\n%%EOF', 'doc.pdf')
    r = client.get(messages(client)[0]['content'])
    assert r.headers['Content-Type'] == 'application/pdf'
    assert 'Content-Security-Policy' not in r.headers

def test_unrecognised_image_is_kept_as_a_file(client):
    r = upload(client, 'image', b'<svg xmlns="http://www.w3.org/2000/svg"/>', 'x.svg')
    assert r.status_code == 200 and r.get_json()['success']
    assert [(m['type'], m['filename']) for m in messages(client)] == [('file', 'x.svg')]

def test_video_without_extension_is_accepted(client):
    upload(client, 'video', b'\x00\x00\x00\x18ftypmp42', 'clip')
    assert [(m['type'], m['filename']) for m in messages(client)] == [('video', 'clip')]

def test_image_links_are_served_without_a_redirect(client):
    upload(client, 'image', PNG, 'p.png')
    m = messages(client)[0]
    assert m['type'] == 'image'
    r = client.get(m['content'])
    assert r.status_code == 200 and r.headers['Content-Type'] == 'image/png'

def test_large_text_message(client):
    big = 'x' * 600_000
    r = client.post('/message', data={'message': big}, content_type='multipart/form-data')
    assert r.status_code == 200
    assert messages(client)[0]['content'] == big

def test_files_outside_the_board_are_never_served(client, tmp_path):
    outside = tmp_path / 'secret.txt'
    outside.write_text('not yours')
    wpaste.mainIndex['evil1234'] = [str(time.time()), str(outside), 'file', 'secret.txt']
    assert client.get('/file/evil1234').status_code == 404

def test_files_on_a_private_board_need_the_code(client):
    slug, _ = new_board(client)
    upload(client, 'file', b'data', 'f.bin', prefix=f'/b/{slug}')
    link = messages(client, f'/b/{slug}')[0]['content']
    assert client.get(link).status_code == 200
    assert wpaste.app.test_client().get(link).status_code == 401


# --- delete, retention, sweep --------------------------------------------------
def test_delete_removes_the_file(client):
    client.post('/message', data={'message': 'secret-pw'})
    m = messages(client)[0]
    assert client.post(f"/delete/{m['id']}").get_json()['success']
    assert messages(client) == []
    assert glob.glob(f"messages/*/{m['id']}*") == []

def test_delete_of_a_missing_file_still_succeeds(client):
    client.post('/message', data={'message': 'already gone'})
    mid = messages(client)[0]['id']
    os.remove(wpaste.mainIndex[mid][2])
    assert client.post(f'/delete/{mid}').get_json()['success']

def test_delete_all_needs_permission(client):
    slug, _ = new_board(client, 'append')
    client.post(f'/b/{slug}/message', data={'message': 'keep me'})
    assert wpaste.app.test_client().post(f'/b/{slug}/delete_all').status_code == 401
    assert client.post(f'/b/{slug}/delete_all').status_code == 200
    assert messages(client, f'/b/{slug}') == []

def test_expired_messages_disappear(client, monkeypatch):
    client.post('/message', data={'message': 'old news'})
    mid = messages(client)[0]['id']
    backdate(wpaste.mainIndex, mid, 7200)
    monkeypatch.setattr(wpaste, 'RETENTION_TIME', 3600)
    assert client.get(f'/text/{mid}').status_code == 404
    assert messages(client) == []
    assert glob.glob(f'messages/*/{mid}*') == []

def test_board_retention_overrides_the_site_default(client, monkeypatch):
    monkeypatch.setattr(wpaste, 'RETENTION_TIME', 3600)
    slug, _ = new_board(client)
    client.post(f'/b/{slug}/settings', data={'retention': '0'})     # keep forever
    client.post(f'/b/{slug}/message', data={'message': 'evergreen'})
    mid = messages(client, f'/b/{slug}')[0]['id']
    backdate(wpaste.boards.state(slug).index, mid, 86400 * 30)
    assert [m['id'] for m in messages(client, f'/b/{slug}')] == [mid]

def test_sweep_expires_boards_nobody_opens(client, monkeypatch):
    slug, _ = new_board(client)
    client.post(f'/b/{slug}/message', data={'message': 'forgotten'})
    index = wpaste.boards.state(slug).index
    mid = next(iter(index))
    backdate(index, mid, 7200)
    monkeypatch.setattr(wpaste, 'RETENTION_TIME', 3600)
    wpaste.sweep()
    assert mid not in index
    assert glob.glob(f'{wpaste.BOARDS_DIR}{slug}/*/{mid}*') == []


# --- board settings and deletion -----------------------------------------------
@pytest.mark.parametrize('value', ['-1', '-5m', 'inf', 'nan', 'soon'])
def test_settings_reject_bad_retention(client, value):
    slug, _ = new_board(client)
    r = client.post(f'/b/{slug}/settings', data={'retention': value})
    assert r.status_code == 400
    assert wpaste.boards.meta(slug)['retention'] == ''

@pytest.mark.parametrize('value', ['0', '7d', '90', ''])
def test_settings_accept_good_retention(client, value):
    slug, _ = new_board(client)
    r = client.post(f'/b/{slug}/settings', data={'retention': value})
    assert r.status_code == 200 and r.get_json()['retention'] == value

def test_settings_reject_unknown_permission(client):
    slug, _ = new_board(client)
    assert client.post(f'/b/{slug}/settings', data={'perm': 'everyone'}).status_code == 400

def test_delete_board(client):
    slug, _ = new_board(client)
    client.post(f'/b/{slug}/message', data={'message': 'bye'})
    assert client.post(f'/b/{slug}/delete_board').status_code == 200
    assert client.get(f'/b/{slug}/messages').status_code == 404
    assert not os.path.exists(os.path.join(wpaste.BOARDS_DIR, slug))
    assert wpaste.boards.state(slug) is None


# --- curl view -----------------------------------------------------------------
def test_curl_gets_plain_text(client):
    client.post('/message', data={'message': 'hello terminal'})
    r = client.get('/', headers={'User-Agent': 'curl/8.5'})
    assert r.mimetype == 'text/plain' and 'hello terminal' in r.get_data(as_text=True)

def test_curl_on_a_private_board_gets_a_hint(client):
    slug, _ = new_board(client)
    client.post(f'/b/{slug}/message', data={'message': 'hidden'})
    r = wpaste.app.test_client().get(f'/b/{slug}', headers={'User-Agent': 'curl/8.5'})
    body = r.get_data(as_text=True)
    assert 'is private' in body and 'hidden' not in body


def test_curl_view_prints_no_control_characters(client):
    '''A stored name (a file's, a board's) can hold ESC, BEL or a C1 character, which a terminal acts on: the curl
    view prints each as `_`, as a download name has it.'''
    curl = {'User-Agent': 'curl/8.5'}
    upload(client, 'file', b'x', 'evil\x1b[2Jname.bin')
    upload(client, 'file', b'x', 'flip\u202egnp.bin')
    body = client.get('/', headers=curl).get_data(as_text=True)
    assert '(evil_[2Jname.bin)' in body
    assert '(flip_gnp.bin)' in body       # U+202E is not a control character: it flips what follows it
    slug = 'b-' + uuid.uuid4().hex[:12]
    secret = pyotp.random_base32()
    r = client.post(f'/b/{slug}/setup', data={'secret': secret, 'code': pyotp.TOTP(secret).now(),
                                                'display': 'bad\x07name\x9b'})
    assert r.status_code == 200, r.get_json()
    upload(client, 'file', b'y', 'bell\x07.bin', prefix=f'/b/{slug}')
    body = client.get(f'/b/{slug}', headers=curl).get_data(as_text=True)
    assert "board 'bad_name_'" in body and '(bell_.bin)' in body
    anon = wpaste.app.test_client().get(f'/b/{slug}', headers=curl).get_data(as_text=True)
    assert "Board 'bad_name_' is private." in anon
    for text in (body, anon):
        assert not wpaste.CONTROL_CHARS.search(text.replace('\n', ''))
        assert all(c.isprintable() for c in text.replace('\n', '')), repr(text)


def test_curl_hint_for_a_new_board_names_the_secret_form(client):
    body = client.get('/b/never-made', headers={'User-Agent': 'curl/8.5'}).get_data(as_text=True)
    assert 'does not exist' in body and '32 characters of A-Z and 2-7' in body, body


def test_setup_takes_only_the_secret_the_page_offered(client):
    '''/access offers a 32-character upper-case base32 secret, and setup takes that form only: another secret whose code
    verifies (16 lower-case characters, a short or long one, padding, a wrong case) is a 400 and no board.'''
    offered = pyotp.random_base32()
    assert wpaste.valid_secret_form(offered) and len(offered) == 32
    short = base64.b32encode(os.urandom(10)).decode()      # 16 characters: pyotp.random_base32 refuses to make one
    odd = {'lower case, 16': short.lower(), 'upper case, 16': short, 'lower case, 32': offered.lower(),
           'long': pyotp.random_base32(40), 'padded': offered[:30] + '==',
           'spaced': ' '.join(offered[i:i + 8] for i in range(0, 32, 8)), 'empty': ''}
    for what, secret in odd.items():
        slug = 'odd-' + uuid.uuid4().hex[:12]
        try:
            code = pyotp.TOTP(secret).now()         # the right code, where pyotp can make one for it
        except Exception:
            code = '000000'
        r = client.post(f'/b/{slug}/setup', data={'secret': secret, 'code': code})
        assert (what, r.status_code) == (what, 400), (what, r.get_json())
        assert r.get_json()['success'] is False
        assert '32 characters of A-Z and 2-7' in r.get_json()['message'], what   # the 400 names the form it wants
        assert client.get(f'/b/{slug}/access').get_json()['exists'] is False, what
    slug = 'odd-' + uuid.uuid4().hex[:12]
    r = client.post(f'/b/{slug}/setup', data={'secret': offered, 'code': pyotp.TOTP(offered).now()})
    assert r.status_code == 200, r.get_json()


def test_setup_stores_a_display_name_of_at_most_64_characters(client):
    '''Setup is open to anyone and a form field may be 64 MiB: the display name is cut to 64 characters (the way a slug is
    cut), and the reply, /access and the registry all hold that value. The page sends the slug as the name, so nothing it
    sends is cut.'''
    secret = pyotp.random_base32()
    huge = 'x' * (20 * 1024**2)
    slug = 'cap-' + uuid.uuid4().hex[:12]
    r = client.post(f'/b/{slug}/setup', data={'secret': secret, 'code': pyotp.TOTP(secret).now(), 'display': huge})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['display'] == 'x' * 64
    assert wpaste.boards.meta(slug)['display'] == 'x' * 64
    assert client.get(f'/b/{slug}/access').get_json()['display'] == 'x' * 64
    assert len(wpaste.boards.registry[slug][6]) == 64

    # cut where a slug is cut, then stripped: a space or a dash at the cut does not stay
    cases = {'a' * 63 + ' ' + 'b' * 10: 'a' * 63, '  padded name  ': 'padded name', 'é' * 100: 'é' * 64,
             '   ': None, '': None}            # nothing left: the slug stands in
    for display, want in cases.items():
        slug = 'cap-' + uuid.uuid4().hex[:12]
        r = client.post(f'/b/{slug}/setup', data={'secret': secret, 'code': pyotp.TOTP(secret).now(), 'display': display})
        assert r.status_code == 200, (display, r.get_json())
        assert r.get_json()['display'] == (want or slug), display
        assert wpaste.boards.meta(slug)['display'] == (want or slug)
    # the page's own name (a canonical slug, at most 64) goes through unchanged
    slug = 'p' * 64
    r = client.post(f'/b/{slug}/setup', data={'secret': secret, 'code': pyotp.TOTP(secret).now(), 'display': slug})
    assert r.get_json()['display'] == slug
    # /access offers what setup would store
    assert client.get('/b/another-new/access', query_string={'display': 'y' * 5000}).get_json()['display'] == 'y' * 64


# --- admin CLI -----------------------------------------------------------------
def test_admin_list_prints_no_control_characters(tmp_path):
    '''A board's display name is whatever the first visitor typed at setup, so `admin list` shows it with each character
    that is not printable made `_`, as the curl view does: the operator's terminal acts on ESC sequences, and U+202E flips
    the text after it.'''
    appdir = tmp_path / 'wpaste'
    appdir.mkdir()
    for name in ('app.py', 'boards.py'):
        shutil.copy(os.path.join(ROOT, name), appdir)
    script = ("import app; app.boards.create('evil-board', 'bad\\x1b[2Jname\\x07\\x9b\\u202e\\u200b', secret=%r); "
              "app.boards.registry.close(); app.mainIndex.close()" % pyotp.random_base32())
    subprocess.run([sys.executable, '-c', script], cwd=appdir, check=True, capture_output=True)
    out = subprocess.run([sys.executable, str(appdir / 'app.py'), 'admin', 'list'],
                         cwd=tmp_path, check=True, capture_output=True, text=True).stdout
    assert 'display=bad_[2Jname____\n' in out          # ESC, BEL, C1, U+202E (right-to-left override) and U+200B
    assert all(c.isprintable() for c in out.replace('\n', '').replace('\t', '')), repr(out)


def test_admin_cli_finds_the_data_from_any_directory(tmp_path):
    appdir = tmp_path / 'wpaste'
    appdir.mkdir()
    for name in ('app.py', 'boards.py'):
        shutil.copy(os.path.join(ROOT, name), appdir)
    secret = pyotp.random_base32()
    script = (f"import app; app.boards.create('cli-board', 'CLI board', secret={secret!r}); "
              "app.boards.registry.close(); app.mainIndex.close()")
    subprocess.run([sys.executable, '-c', script], cwd=appdir, check=True, capture_output=True)

    elsewhere = tmp_path / 'elsewhere'
    elsewhere.mkdir()
    out = subprocess.run([sys.executable, str(appdir / 'app.py'), 'admin', 'list'],
                         cwd=elsewhere, check=True, capture_output=True, text=True).stdout
    assert 'cli-board' in out
    assert list(elsewhere.iterdir()) == []      # nothing created where it was typed

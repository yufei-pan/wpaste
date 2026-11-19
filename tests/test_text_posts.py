'''Text files sent in the `text` field keep their name; reserved board names.'''
import builtins
import io

import app as wpaste
from boards import canonical_slug
from conftest import messages, new_board


def post_text(client, *parts, prefix=''):
    '''POST one request with a `text` part per (bytes, filename).'''
    files = [(io.BytesIO(data), name) for data, name in parts]
    r = client.post(f'{prefix}/message', data={'text': files})
    assert r.status_code == 200, r.get_data(as_text=True)
    return messages(client, prefix)


def test_text_part_keeps_name(client):
    [m] = post_text(client, (b'# Hi\n', 'notes.md'))
    assert (m['type'], m['filename'], m['content']) == ('text', 'notes.md', '# Hi\n')
    assert wpaste.mainIndex[m['id']][2].endswith(f"{m['id']}.md")


def test_text_part_without_extension_stored_as_txt(client):
    [m] = post_text(client, (b'read me\n', 'README'))
    assert (m['type'], m['filename']) == ('text', 'README')
    assert wpaste.mainIndex[m['id']][2].endswith(f"{m['id']}.txt")
    assert client.get(f"/text/{m['id']}").data == b'read me\n'


def test_several_text_parts_one_request(client):
    got = post_text(client, (b'a\n', 'a.md'), (b'b\n', 'b.py'), (b'c\n', 'c.log'))
    assert sorted((m['type'], m['filename'], m['content']) for m in got) == [
        ('text', 'a.md', 'a\n'), ('text', 'b.py', 'b\n'), ('text', 'c.log', 'c\n')]


def test_text_part_not_utf8_kept_as_file(client):
    [m] = post_text(client, (b'caf\xe9\n', 'a.txt'))
    assert (m['type'], m['filename']) == ('file', 'a.txt')
    assert client.get(m['content']).data == b'caf\xe9\n'


def test_text_part_too_big_to_inline_kept_as_file(client, monkeypatch):
    '''/messages inlines every text post; one bigger than a pasted message may
    be is kept as a file.'''
    monkeypatch.setattr(wpaste, 'TEXT_POST_MAX', 4)
    got = post_text(client, (b'1234', 'fits.md'), (b'12345', 'big.md'))
    assert sorted((m['filename'], m['type']) for m in got) == [('big.md', 'file'), ('fits.md', 'text')]


def test_text_part_on_a_private_board_needs_the_code(client):
    slug, _ = new_board(client)
    [m] = post_text(client, (b'x\n', 'x.md'), prefix=f'/b/{slug}')
    assert m['filename'] == 'x.md'
    anon = wpaste.app.test_client()
    r = anon.post(f'/b/{slug}/message', data={'text': (io.BytesIO(b'y'), 'y.md')})
    assert r.status_code == 401


def test_messages_reads_utf8_regardless_of_locale(client, monkeypatch):
    '''A server whose locale isn't UTF-8 still reads and writes text as UTF-8:
    `open` without an encoding here behaves as under a Latin-1 locale.'''
    def latin1_locale_open(file, mode='r', *args, encoding=None, **kwargs):
        if 'b' not in mode and encoding is None:
            encoding = 'latin-1'
        return builtins.open(file, mode, *args, encoding=encoding, **kwargs)
    monkeypatch.setattr(wpaste, 'open', latin1_locale_open, raising=False)
    text = 'é✓\n'
    [m] = post_text(client, (text.encode(), 'u.md'))
    assert m['content'] == text
    client.post('/message', data={'message': 'pasted é✓'})
    assert [m['content'] for m in messages(client) if m['filename'] != 'u.md'] == ['pasted é✓']


def test_main_is_reserved(client):
    assert canonical_slug('main') is None and canonical_slug('default') is None
    assert canonical_slug(' MAIN ') is None
    assert client.get('/b/main/messages').status_code == 404


def test_paste_unchanged(client):
    client.post('/message', data={'message': 'just a paste'})
    [m] = messages(client)
    assert (m['type'], m['filename'], m['content']) == ('text', f"{m['id']}.txt", 'just a paste')


# --- a POST that stores nothing says so ------------------------------------------

def test_post_that_stores_nothing_is_a_400(client):
    '''A plain `text` form value is not a file part, an empty or blank message
    and a part with no name store nothing: none of them may answer success.'''
    cases = [
        {'text': 'a form value, not a file'},
        {'message': ''},
        {'message': ' \n\t '},
        {'text': (io.BytesIO(b'nameless'), '')},
        {'file': (io.BytesIO(b'nameless'), '')},
        {},
    ]
    for data in cases:
        r = client.post('/message', data=data)
        assert r.status_code == 400, data
        assert r.get_json() == {'success': False, 'error': 'nothing to post'}, data
    assert messages(client) == []


def test_post_that_stores_something_is_still_200(client):
    r = client.post('/message', data={'message': 'x', 'text': 'ignored value'})
    assert r.status_code == 200 and r.get_json()['success'] is True
    r = client.post('/message', data={'text': 'ignored value', 'file': (io.BytesIO(b'f'), 'f.bin')})
    assert r.status_code == 200
    assert sorted(m['type'] for m in messages(client)) == ['file', 'text']


def test_nothing_to_post_on_a_private_board_still_needs_the_code_first(client):
    slug, _ = new_board(client)
    anon = wpaste.app.test_client()
    assert anon.post(f'/b/{slug}/message', data={'message': ''}).status_code == 401
    assert client.post(f'/b/{slug}/message', data={'message': ''}).status_code == 400


# --- a NUL byte means binary -----------------------------------------------------

def test_text_part_with_a_nul_byte_is_a_file(client):
    '''A NUL byte is valid UTF-8, but text never holds one: it is binary, and
    inlining it would put it in every listing.'''
    data = b'looks like text\x00but is not\n'
    [m] = post_text(client, (data, 'blob.txt'))
    assert (m['type'], m['filename']) == ('file', 'blob.txt')
    assert client.get(m['content']).data == data


# --- display strips a leading BOM, the stored bytes keep it ------------------------

def test_leading_bom_is_stripped_for_display_only(client):
    '''marked doesn't strip U+FEFF, so `# Title` after a BOM would show
    literally. /messages and the curl view drop one; /text is exact.'''
    raw = b'\xef\xbb\xbf# Title\n'
    [m] = post_text(client, (raw, 'bom.md'))
    assert m['content'] == '# Title\n'
    assert client.get(f"/text/{m['id']}").data == raw
    page = client.get('/', headers={'User-Agent': 'curl/8.5'}).get_data(as_text=True)
    assert '# Title' in page and '﻿' not in page
    client.post('/message', data={'message': '﻿# Pasted'})
    assert '# Pasted' in [x['content'] for x in messages(client)]
    # only one: a second BOM is content
    [two] = [x for x in post_text(client, (b'\xef\xbb\xbf\xef\xbb\xbf2\n', 'two.md')) if x['filename'] == 'two.md']
    assert two['content'] == '﻿2\n'


# --- every text post is served as text, whatever its bytes look like ---------------

def test_text_post_is_served_as_plain_text_whatever_it_starts_with(client):
    '''filetype sniffs content: text starting %PDF-, BM or PK would be served as
    a PDF (no sandbox CSP), an image or a zip.'''
    bodies = {'a.txt': b'%PDF-1.4 but really text\n', 'b.txt': b'BM is a pair of letters\n',
              'c.txt': b'PK\x03\x04 and then words\n', 'd.html': b'<script>alert(1)</script>\n',
              'e.svg': b'<svg xmlns="http://www.w3.org/2000/svg"><script>1</script></svg>\n'}
    got = post_text(client, *[(data, name) for name, data in bodies.items()])
    assert {m['filename'] for m in got} == set(bodies)
    for m in got:
        assert m['type'] == 'text', m['filename']
        r = client.get(f"/text/{m['id']}")
        assert r.data == bodies[m['filename']]
        assert r.headers['Content-Type'] == 'text/plain; charset=utf-8', m['filename']
        assert r.headers['X-Content-Type-Options'] == 'nosniff'
        assert r.headers['Content-Security-Policy'] == 'sandbox', m['filename']


def test_a_real_pdf_file_post_is_still_served_as_a_pdf(client):
    r = client.post('/message', data={'file': (io.BytesIO(b'%PDF-1.4\n%\xe2\xe3\xcf\xd3\n'), 'doc.pdf')})
    assert r.status_code == 200
    [m] = messages(client)
    assert m['type'] == 'file'
    r = client.get(m['content'])
    assert r.headers['Content-Type'] == 'application/pdf'
    assert 'Content-Security-Policy' not in r.headers

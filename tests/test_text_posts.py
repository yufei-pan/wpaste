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

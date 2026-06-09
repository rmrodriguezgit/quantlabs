from pathlib import Path
from io import BytesIO

from werkzeug.datastructures import FileStorage

from memory.uploads import UploadStore

def test_csv_summary(tmp_path, monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, 'upload_root', str(tmp_path))
    p=tmp_path/'x.csv'; p.write_text('a,b\n1,2\n3,4\n')
    s=UploadStore()._summarize(p,'csv')
    assert '2 filas x 2 columnas' in s

def test_text_summary(tmp_path, monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, 'upload_root', str(tmp_path))
    p=tmp_path/'x.md'; p.write_text('# Hola')
    assert '# Hola' in UploadStore()._summarize(p,'md')

def test_escola_uploads_are_not_general_context(tmp_path, monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, 'upload_root', str(tmp_path))
    store=UploadStore()
    general=store.save('user', FileStorage(stream=BytesIO(b'general'), filename='general.txt'), scope='general')
    escola=store.save('user', FileStorage(stream=BytesIO(b'escola private'), filename='escola.txt'), scope='escola')

    assert [item['id'] for item in store.list('user')] == [general['id']]
    assert [item['id'] for item in store.list('user', scope='escola')] == [escola['id']]
    context=store.context('user', [general['id'], escola['id']])
    assert 'general' in context
    assert 'escola private' not in context

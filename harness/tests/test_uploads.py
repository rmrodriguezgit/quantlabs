from pathlib import Path
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

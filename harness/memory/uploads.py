from __future__ import annotations
import json, re, uuid
from datetime import datetime
from pathlib import Path
import nbformat
import pandas as pd
from pypdf import PdfReader
from PIL import Image
import pytesseract
from werkzeug.utils import secure_filename
from config import settings

ALLOWED_EXTENSIONS = {"pdf","docx","csv","xls","xlsx","txt","md","json","ipynb","png","jpg","jpeg"}
_SAFE_USER_RE = re.compile(r'[^A-Za-z0-9_.-]+')
def user_key(user_id):
    value=str(user_id or 'anonymous').strip() or 'anonymous'
    return (_SAFE_USER_RE.sub('_', value)[:96] or 'anonymous')

class UploadStore:
    def __init__(self):
        self.root = Path(settings.upload_root); self.root.mkdir(parents=True, exist_ok=True)
    def _user_root(self, user_id):
        path = self.root / user_key(user_id); path.mkdir(parents=True, exist_ok=True); return path
    def save(self, user_id, file):
        original = file.filename or "archivo"; safe = secure_filename(original); ext = safe.rsplit(".",1)[-1].lower() if "." in safe else ""
        if ext not in ALLOWED_EXTENSIONS: raise ValueError("tipo de archivo no permitido")
        file_id=str(uuid.uuid4()); user_root=self._user_root(user_id); path=user_root/f"{file_id}.{ext}"; file.save(path)
        meta={"id":file_id,"owner_id":user_key(user_id),"name":safe,"ext":ext,"size":path.stat().st_size,"path":str(path),"created_at":datetime.utcnow().isoformat()+"Z"}
        meta['summary']=self._summarize(path, ext)
        (user_root/f"{file_id}.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False)); return meta
    def list(self, user_id):
        out=[]
        for meta in self._user_root(user_id).glob("*.json"):
            try: out.append(json.loads(meta.read_text()))
            except Exception: pass
        return sorted(out,key=lambda x:x.get("created_at",""),reverse=True)
    def get(self, user_id, file_id):
        p=self._user_root(user_id)/f"{file_id}.json"; return json.loads(p.read_text()) if p.exists() else None
    def delete(self, user_id, file_id):
        meta=self.get(user_id,file_id)
        if not meta: return False
        Path(meta['path']).unlink(missing_ok=True); (self._user_root(user_id)/f"{file_id}.json").unlink(missing_ok=True); return True
    def context(self, user_id, file_ids):
        chunks=[]
        for file_id in file_ids[:5]:
            meta=self.get(user_id,file_id)
            if not meta: continue
            intro=f"Archivo adjunto: {meta['name']} ({meta['ext']}, {meta['size']} bytes)"
            summary=meta.get('summary') or self._summarize(Path(meta['path']), meta['ext'])
            chunks.append(intro+'\n'+summary)
        return '\n\n'.join(chunks)
    def _summarize(self, path: Path, ext: str):
        try:
            if ext in {'txt','md','json'}:
                return 'Contenido extraído:\n'+path.read_text(errors='ignore')[:8000]
            if ext=='csv':
                return self._frame_summary(pd.read_csv(path))
            if ext=='xlsx':
                xl=pd.ExcelFile(path); parts=[f"Hojas: {', '.join(xl.sheet_names[:8])}"]
                for sheet in xl.sheet_names[:3]:
                    parts.append(f"Hoja {sheet}:\n"+self._frame_summary(pd.read_excel(path,sheet_name=sheet)))
                return '\n'.join(parts)
            if ext=='xls':
                return 'Archivo Excel legacy almacenado. Usa File Analyst para extracción profunda.'
            if ext=='docx':
                return 'Documento Word almacenado. Usa File Analyst para extracción profunda.'
            if ext=='pdf':
                reader=PdfReader(str(path)); text='\n'.join((page.extract_text() or '') for page in reader.pages[:10])[:8000]
                return f"PDF: {len(reader.pages)} páginas. Texto extraído inicial:\n{text}" if text else f"PDF: {len(reader.pages)} páginas. No se pudo extraer texto legible."
            if ext=='ipynb':
                nb=nbformat.read(path,as_version=4); code=[c.source for c in nb.cells if c.cell_type=='code']; md=[c.source for c in nb.cells if c.cell_type=='markdown']
                imports=[line for cell in code for line in cell.splitlines() if line.strip().startswith(('import ','from '))][:30]
                return f"Notebook: {len(nb.cells)} celdas, {len(code)} código, {len(md)} markdown.\nImports detectados: {', '.join(imports) or 'ninguno'}\nPrimeras celdas markdown:\n"+'\n'.join(md[:3])[:4000]
            if ext in {'png','jpg','jpeg'}:
                img=Image.open(path); text=pytesseract.image_to_string(img)[:6000]
                return f"Imagen: {img.width}x{img.height}. OCR extraído:\n{text}" if text.strip() else f"Imagen: {img.width}x{img.height}. Sin texto OCR detectable."
        except Exception as exc:
            return f"No se pudo extraer contenido automáticamente: {exc}"
        return 'Archivo almacenado.'
    def _frame_summary(self, df: pd.DataFrame):
        cols=', '.join(map(str, df.columns[:30])); dtypes=', '.join(f"{c}:{t}" for c,t in df.dtypes.astype(str).items()); preview=df.head(5).to_string(index=False)[:5000]
        return f"Tabla: {len(df)} filas x {len(df.columns)} columnas.\nColumnas: {cols}\nTipos: {dtypes}\nVista previa:\n{preview}"

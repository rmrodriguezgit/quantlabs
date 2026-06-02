from __future__ import annotations
import hashlib, json, math, re
from pathlib import Path
from config import settings

class VectorMemory:
    def __init__(self):
        self.path=Path(settings.session_root)/'vector_memory.jsonl'; self.path.parent.mkdir(parents=True, exist_ok=True)

    def _normalize(self, text: str) -> str:
        return re.sub(r"\s+", " ", str(text or "").strip().lower())

    def _hash(self, text: str) -> str:
        return hashlib.sha256(self._normalize(text).encode("utf-8")).hexdigest()

    def exists_duplicate(self, text_hash: str, metadata: dict | None=None) -> bool:
        if not text_hash or not self.path.exists():
            return False
        expected = metadata or {}
        for line in self.path.read_text().splitlines():
            try:
                item=json.loads(line); meta=item.get('metadata') or {}
            except Exception:
                continue
            if meta.get('text_hash') != text_hash:
                continue
            if expected.get('user_id') and meta.get('user_id') != expected.get('user_id'):
                continue
            if expected.get('agent') and meta.get('agent') != expected.get('agent'):
                continue
            if expected.get('project_id') and meta.get('project_id') != expected.get('project_id'):
                continue
            return True
        return False

    def add(self, text: str, embedding: list[float], metadata: dict | None=None):
        meta=dict(metadata or {})
        meta.setdefault('text_hash', self._hash(text))
        if self.exists_duplicate(meta.get('text_hash'), {'user_id': meta.get('user_id'), 'agent': meta.get('agent')}):
            return False
        with self.path.open('a') as fh: fh.write(json.dumps({'text':text,'embedding':embedding,'metadata':meta})+'\n')
        return True
    def embed(self, text: str, dims: int=96) -> list[float]:
        vector=[0.0]*dims
        for token in re.findall(r"[\wáéíóúñüÁÉÍÓÚÑÜ-]{3,}", str(text or "").lower()):
            digest=hashlib.sha256(token.encode("utf-8")).digest()
            idx=int.from_bytes(digest[:4],"big")%dims
            vector[idx]+=1.0
        norm=math.sqrt(sum(x*x for x in vector)) or 1.0
        return [x/norm for x in vector]
    def search(self, query: list[float], top_k: int=5):
        rows=[]
        if not self.path.exists(): return rows
        for line in self.path.read_text().splitlines():
            item=json.loads(line); rows.append((self._cos(query,item['embedding']),item))
        return [item for _,item in sorted(rows,key=lambda x:x[0], reverse=True)[:top_k]]
    def iter_items(self, metadata: dict | None=None):
        expected=metadata or {}
        if not self.path.exists(): return []
        rows=[]
        for line in self.path.read_text().splitlines():
            try: item=json.loads(line); meta=item.get('metadata') or {}
            except Exception: continue
            ok=True
            for key,value in expected.items():
                if value is not None and meta.get(key)!=value:
                    ok=False; break
            if ok: rows.append(item)
        return rows
    def stats(self, metadata: dict | None=None):
        rows=self.iter_items(metadata)
        by_agent={}
        by_project={}
        for item in rows:
            meta=item.get('metadata') or {}
            agent=meta.get('agent') or 'unknown'
            project=meta.get('project_id') or 'legacy'
            by_agent[agent]=by_agent.get(agent,0)+1
            by_project[project]=by_project.get(project,0)+1
        return {'items':len(rows),'by_agent':by_agent,'by_project':by_project,'path':str(self.path)}
    def _cos(self,a,b):
        denom=(math.sqrt(sum(x*x for x in a))*math.sqrt(sum(x*x for x in b))) or 1
        return sum(x*y for x,y in zip(a,b))/denom

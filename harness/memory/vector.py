from __future__ import annotations
import hashlib, json, math, re
from pathlib import Path
from config import settings

class VectorMemory:
    def __init__(self):
        self.path=Path(settings.session_root)/'vector_memory.jsonl'; self.path.parent.mkdir(parents=True, exist_ok=True)
    def add(self, text: str, embedding: list[float], metadata: dict | None=None):
        with self.path.open('a') as fh: fh.write(json.dumps({'text':text,'embedding':embedding,'metadata':metadata or {}})+'\n')
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
    def _cos(self,a,b):
        denom=(math.sqrt(sum(x*x for x in a))*math.sqrt(sum(x*x for x in b))) or 1
        return sum(x*y for x,y in zip(a,b))/denom

from __future__ import annotations
import json, math
from pathlib import Path
from config import settings

class VectorMemory:
    def __init__(self):
        self.path=Path(settings.session_root)/'vector_memory.jsonl'; self.path.parent.mkdir(parents=True, exist_ok=True)
    def add(self, text: str, embedding: list[float], metadata: dict | None=None):
        with self.path.open('a') as fh: fh.write(json.dumps({'text':text,'embedding':embedding,'metadata':metadata or {}})+'\n')
    def search(self, query: list[float], top_k: int=5):
        rows=[]
        if not self.path.exists(): return rows
        for line in self.path.read_text().splitlines():
            item=json.loads(line); rows.append((self._cos(query,item['embedding']),item))
        return [item for _,item in sorted(rows,key=lambda x:x[0], reverse=True)[:top_k]]
    def _cos(self,a,b):
        denom=(math.sqrt(sum(x*x for x in a))*math.sqrt(sum(x*x for x in b))) or 1
        return sum(x*y for x,y in zip(a,b))/denom

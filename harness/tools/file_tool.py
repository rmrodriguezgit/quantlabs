from pathlib import Path
import json, yaml, nbformat
from policies.security import FilePolicy
from .base import BaseTool
class FileTool(BaseTool):
    name='file'
    def __init__(self): self.policy=FilePolicy()
    def run(self, action: str, path: str, content=None, **kwargs):
        p=self.policy.resolve(path)
        if action=='read': return p.read_text()
        if action=='write': p.parent.mkdir(parents=True, exist_ok=True); p.write_text(content or ''); return str(p)
        if action=='json': return json.loads(p.read_text())
        if action=='yaml': return yaml.safe_load(p.read_text())
        if action=='notebook': return nbformat.read(p, as_version=4)
        raise ValueError('unsupported file action')

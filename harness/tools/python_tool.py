from __future__ import annotations
import json, subprocess, tempfile
from config import settings
from .base import BaseTool

class PythonTool(BaseTool):
    name='python'
    def run(self, code: str, cuda: bool | None = None):
        prelude = "import json\n"
        if cuda if cuda is not None else settings.enable_cuda:
            prelude += "import os\nos.environ.setdefault('CUDA_VISIBLE_DEVICES','0')\n"
        with tempfile.NamedTemporaryFile('w', suffix='.py', delete=False) as fh:
            fh.write(prelude + code); path=fh.name
        completed=subprocess.run(['python3', path], capture_output=True, text=True, timeout=settings.max_tool_seconds)
        return {'code':completed.returncode,'stdout':completed.stdout[-12000:],'stderr':completed.stderr[-12000:]}

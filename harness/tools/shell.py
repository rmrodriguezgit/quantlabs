from __future__ import annotations

import shlex
import subprocess

from config import settings
from policies.security import ShellPolicy
from .base import BaseTool

class ShellTool(BaseTool):
    name='shell'

    def __init__(self):
        self.policy = ShellPolicy()

    def run(self, command: str):
        decision = self.policy.validate(command)
        if not decision.allowed:
            raise PermissionError(decision.reason)
        completed = subprocess.run(
            shlex.split(command),
            cwd=settings.shell_workdir,
            capture_output=True,
            text=True,
            timeout=settings.max_tool_seconds,
        )
        return {'code': completed.returncode, 'stdout': completed.stdout[-12000:], 'stderr': completed.stderr[-12000:]}

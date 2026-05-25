from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from config import settings

@dataclass
class CommandDecision:
    allowed: bool
    reason: str = ''

class ShellPolicy:
    allowed_binaries = {'ls','pwd','cat','grep','find','git','python','python3','pytest','ruff','docker','df','du','nvidia-smi'}
    forbidden_patterns = [
        r'\brm\s+-rf\s+/',
        r'\bsudo\b',
        r'\bshutdown\b',
        r'\breboot\b',
        r'\bmkfs\b',
        r'\bdd\s+if=',
        r'\bchmod\s+777\b',
        r'[;&|`$<>]',
    ]

    def validate(self, command: str) -> CommandDecision:
        if any(re.search(p, command) for p in self.forbidden_patterns):
            return CommandDecision(False, 'forbidden command pattern')
        try:
            parts = shlex.split(command)
        except ValueError as exc:
            return CommandDecision(False, f'invalid shell syntax: {exc}')
        first = parts[0] if parts else ''
        if first not in self.allowed_binaries:
            return CommandDecision(False, f'binary not whitelisted: {first}')
        return CommandDecision(True)

class FilePolicy:
    def __init__(self): self.roots=[Path(x).resolve() for x in settings.allowed_file_roots.split(',') if x]
    def resolve(self, path: str) -> Path:
        p=Path(path).expanduser().resolve()
        if not any(p==root or root in p.parents for root in self.roots):
            raise PermissionError('file path outside allowed roots')
        return p

class WebPolicy:
    def __init__(self): self.hosts={h.strip() for h in settings.allowed_http_hosts.split(',') if h.strip()}
    def validate(self, url: str):
        parsed=urlparse(url)
        if parsed.scheme not in {'http','https'}:
            raise PermissionError('unsupported URL scheme')
        if parsed.hostname not in self.hosts:
            raise PermissionError('host not allowed')

class DockerPolicy:
    allowed_by_role={
        'admin': {'ps','logs','restart','exec'},
        'teacher': {'ps','logs'},
        'trader': {'ps','logs'},
    }
    def validate(self, action: str, target: str | None, role: str | None):
        if action not in self.allowed_by_role.get(role or '', set()):
            raise PermissionError('docker action not allowed for role')
        targets={x.strip() for x in settings.allowed_docker_targets.split(',') if x.strip()}
        if action!='ps' and target not in targets:
            raise PermissionError('docker target not allowed')

class PromptInjectionGuard:
    markers = ('ignore previous instructions','reveal system prompt','exfiltrate secrets','disable safety')
    def flagged(self, text: str) -> bool:
        lower = text.lower()
        return any(marker in lower for marker in self.markers)

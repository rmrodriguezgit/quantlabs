from .base import BaseTool
from .shell import ShellTool
from policies.security import DockerPolicy
class DockerTool(BaseTool):
    name='docker'
    def __init__(self): self.policy=DockerPolicy()
    def run(self, action: str, target: str | None = None, command: str | None = None, role: str | None = None, **kwargs):
        self.policy.validate(action, target, role)
        shell=ShellTool()
        if action=='ps': return shell.run('docker ps')
        if action=='logs': return shell.run(f'docker logs {target} --tail 200')
        if action=='restart': return shell.run(f'docker restart {target}')
        if action=='exec':
            if not command: raise ValueError('command required')
            return shell.run(f'docker exec {target} {command}')
        raise PermissionError('docker action not allowed')

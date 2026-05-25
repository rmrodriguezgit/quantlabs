from tools.registry import ToolRegistry
class AutoValidator:
    def __init__(self): self.tools=ToolRegistry()
    def run(self):
        return {
            'pytest': self.tools.execute('shell', role='admin', command='pytest -q').model_dump(),
            'ruff': self.tools.execute('shell', role='admin', command='ruff check .').model_dump(),
        }

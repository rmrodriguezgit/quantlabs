from .shell import ShellTool
from .python_tool import PythonTool
from .file_tool import FileTool
from .docker_tool import DockerTool
from .financial import FinancialTool
from .web_api import WebAPITool
from .mexc_spot import MexcSpotTool
from .paper_trading import PaperTradingTool
from .polymarket import PolymarketTool
from .deep_research import DeepResearchTool
from .dexter_research import DexterResearchTool
from .jupyter_gpu import JupyterGPUTool


class ToolRegistry:
    allowed_by_role = {
        'guest': {'financial', 'web_api', 'polymarket', 'deep_research', 'dexter_research'},
        'admin': {'shell', 'python', 'file', 'docker', 'financial', 'web_api', 'mexc_spot', 'polymarket', 'paper_trading', 'deep_research', 'dexter_research', 'jupyter_gpu'},
        'teacher': {'python', 'file', 'financial', 'web_api', 'docker', 'polymarket', 'deep_research', 'dexter_research', 'jupyter_gpu'},
        'trader': {'python', 'file', 'financial', 'web_api', 'docker', 'mexc_spot', 'polymarket', 'paper_trading', 'deep_research', 'dexter_research', 'jupyter_gpu'},
    }

    def __init__(self):
        self.tools = {
            t.name: t
            for t in [
                ShellTool(),
                PythonTool(),
                FileTool(),
                DockerTool(),
                FinancialTool(),
                WebAPITool(),
                MexcSpotTool(),
                PolymarketTool(),
                PaperTradingTool(),
                DeepResearchTool(),
                DexterResearchTool(),
                JupyterGPUTool(),
            ]
        }

    def execute(self, name: str, role: str | None = None, **kwargs):
        if name not in self.tools:
            raise KeyError(f'unknown tool: {name}')
        effective_role = role or 'guest'
        if name not in self.allowed_by_role.get(effective_role, set()):
            raise PermissionError('tool not allowed for role')
        return self.tools[name].execute(role=effective_role, **kwargs)

    def visible_tools(self, role: str | None = None):
        allowed = self.allowed_by_role.get(role or 'guest', set())
        return sorted(name for name in self.tools if name in allowed)

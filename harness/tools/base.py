from __future__ import annotations
from abc import ABC, abstractmethod
from time import perf_counter
from core.models import ToolResult
from telemetry.metrics import TOOL_CALLS, TOOL_LATENCY

class BaseTool(ABC):
    name: str
    @abstractmethod
    def run(self, **kwargs): ...
    def execute(self, **kwargs) -> ToolResult:
        start = perf_counter()
        try:
            output = self.run(**kwargs); ok=True; error=None
        except Exception as exc:
            output=None; ok=False; error=str(exc)
        ms = int((perf_counter()-start)*1000)
        TOOL_CALLS.labels(self.name, str(ok).lower()).inc(); TOOL_LATENCY.labels(self.name).observe(ms/1000)
        return ToolResult(name=self.name, ok=ok, output=output, error=error, duration_ms=ms)

from __future__ import annotations
from dataclasses import dataclass
from core.models import SessionState
from config import settings
from tools.registry import ToolRegistry
from runtime.agent_loop import AgentLoop

@dataclass
class AgentContext:
    state: SessionState
    tools: ToolRegistry
    role: str | None = None

class BaseAgent:
    name='base'
    workflow=None
    instructions=''
    def act(self, objective: str, ctx: AgentContext) -> dict:
        outcome = AgentLoop(max_steps=settings.max_agent_steps).run(objective, ctx, self.instructions, self.workflow)
        return {'agent': self.name, 'objective': objective, 'result': outcome['final'], 'events': outcome['events'], 'usage': outcome.get('usage', {}), 'last_usage': outcome.get('last_usage', {})}

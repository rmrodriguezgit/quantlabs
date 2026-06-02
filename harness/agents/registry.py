from .decision_router import DecisionRouter, RoutedAgent
from .specialists import PlannerAgent,CodingAgent,Codex4UAgent,FileAnalystAgent,FinanceAgent,PolymrktAgent,DexterAgent,ResearchAgent,ValidationAgent,ExecutionAgent

class AgentRegistry:
    def __init__(self):
        self.router = DecisionRouter()
        raw_agents = [PlannerAgent(),CodingAgent(),Codex4UAgent(),FileAnalystAgent(),FinanceAgent(),PolymrktAgent(),DexterAgent(),ResearchAgent(),ValidationAgent(),ExecutionAgent()]
        self.agents = {a.name: RoutedAgent(a, self.router) for a in raw_agents}
    def get(self,name): return self.agents[name]
    def list(self): return list(self.agents)

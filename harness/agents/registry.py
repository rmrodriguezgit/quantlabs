from .specialists import PlannerAgent,CodingAgent,Codex4UAgent,FinanceAgent,PolymrktAgent,DexterAgent,ResearchAgent,ValidationAgent,ExecutionAgent
class AgentRegistry:
    def __init__(self): self.agents={a.name:a for a in [PlannerAgent(),CodingAgent(),Codex4UAgent(),FinanceAgent(),PolymrktAgent(),DexterAgent(),ResearchAgent(),ValidationAgent(),ExecutionAgent()]}
    def get(self,name): return self.agents[name]
    def list(self): return list(self.agents)

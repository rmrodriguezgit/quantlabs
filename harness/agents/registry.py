from .specialists import PlannerAgent,CodingAgent,FinanceAgent,PolymrktAgent,ResearchAgent,ValidationAgent,ExecutionAgent
class AgentRegistry:
    def __init__(self): self.agents={a.name:a for a in [PlannerAgent(),CodingAgent(),FinanceAgent(),PolymrktAgent(),ResearchAgent(),ValidationAgent(),ExecutionAgent()]}
    def get(self,name): return self.agents[name]
    def list(self): return list(self.agents)

from agents.base import AgentContext
from agents.decision_router import DecisionRouter, RoutedAgent
from agents.registry import AgentRegistry
from core.models import SessionState
from tools.registry import ToolRegistry


def test_decision_router_rule_for_nginx_docker():
    route = DecisionRouter().classify("execution", "como reinicio nginx en docker", "admin")

    assert route.route == "rule"
    assert route.response
    assert "docker restart quantlab_nginx" in route.response
    assert route.expected_latency_ms < 100


def test_routed_agent_returns_rule_without_llm_or_tool():
    class FakeAgent:
        name = "execution"
        def act(self, objective, ctx):
            raise AssertionError("No debe llamar al agente si hay regla directa")

    ctx = AgentContext(state=SessionState(session_id="demo"), tools=ToolRegistry(), role="admin")
    result = RoutedAgent(FakeAgent()).act("como reinicio nginx en docker", ctx)

    assert result["usage"] == {}
    assert result["events"][0]["decision"]["action"] == "route_decision"
    assert result["events"][0]["decision"]["route"] == "rule"
    assert "docker restart quantlab_nginx" in result["result"]


def test_routed_agent_prepends_hybrid_decision_event():
    class FakeAgent:
        name = "finance"
        def act(self, objective, ctx):
            return {"agent": "finance", "objective": objective, "result": "ok", "events": [{"step": 1, "decision": {"action": "tool"}}], "usage": {}, "last_usage": {}}

    ctx = AgentContext(state=SessionState(session_id="demo"), tools=ToolRegistry(), role="trader")
    result = RoutedAgent(FakeAgent()).act("analisis de BTC-USD scalping 1d", ctx)

    assert result["events"][0]["step"] == "decision_router"
    assert result["events"][0]["decision"]["route"] == "hybrid"
    assert result["events"][0]["decision"]["tool"] == "financial"
    assert result["events"][1]["decision"]["action"] == "tool"


def test_registry_wraps_agents_with_router():
    agent = AgentRegistry().get("finance")
    assert hasattr(agent, "_router")
    assert "finance" in AgentRegistry().list()

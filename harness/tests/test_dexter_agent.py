from agents.base import AgentContext
from agents.registry import AgentRegistry
from agents.specialists import DexterAgent, PolymrktAgent
from core.models import SessionState
from tools.registry import ToolRegistry


def test_dexter_agent_runs_research_only_and_records_artifacts(tmp_path, monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "artifact_root", str(tmp_path / "artifacts"))
    ctx = AgentContext(state=SessionState(session_id="demo"), tools=ToolRegistry(), role="trader")

    result = DexterAgent().act("Dexter tesis BTC para Polymarket", ctx)

    assert result["agent"] == "dexter"
    assert result["events"][0]["decision"]["tool"] == "dexter_research"
    assert "research_only" in result["result"]
    assert ctx.state.artifacts


def test_polymrkt_agent_delegates_macro_context_to_dexter(monkeypatch):
    ctx = AgentContext(state=SessionState(session_id="demo"), tools=ToolRegistry(), role="trader")
    calls = []

    def fake_execute(name, role=None, **kwargs):
        calls.append((name, kwargs))
        if name == "polymarket":
            output = {"action": "NO_TRADE", "side": "NONE", "strategy": "test", "candidates": [], "reasons": ["no_event_passed_filters"], "filters": {}}
        elif name == "dexter_research":
            output = {"thesis": "Contexto macro neutral", "report": {"thesis": "Contexto macro neutral"}, "artifacts": {"coordination": "storage/artifacts/dexter/demo/test.json"}}
        else:
            output = {}
        return type("Result", (), {"model_dump": lambda self: {"name": name, "ok": True, "output": output}})()

    monkeypatch.setattr(ctx.tools, "execute", fake_execute)
    result = PolymrktAgent().act("Evalua BTC 5m/15m", ctx)

    assert [name for name, _ in calls][:2] == ["polymarket", "dexter_research"]
    assert "Dexter research" in result["result"]


def test_registry_exposes_dexter_agent_and_tool():
    assert "dexter" in AgentRegistry().list()
    assert "dexter_research" in ToolRegistry().visible_tools("trader")

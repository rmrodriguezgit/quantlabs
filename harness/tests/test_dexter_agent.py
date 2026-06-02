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



def test_polymrkt_hybrid_prediction_simulation_skips_llm(monkeypatch):
    ctx = AgentContext(state=SessionState(session_id="demo"), tools=ToolRegistry(), role="trader")

    def fake_execute(name, role=None, **kwargs):
        if name == "polymarket":
            output = {
                "action": "NO_TRADE",
                "side": "NONE",
                "candidates": [{
                    "interval": "5m",
                    "window_et": "2026-06-02 12:30:00 EDT - 2026-06-02 12:35:00 EDT",
                    "countdown": "04:39",
                    "preferred_side": "Up",
                    "probability": 0.607,
                    "edge": 0.1372,
                    "passes_filters": False,
                    "reasons": ["confidence_below_threshold"],
                    "microstructure": {"ask": 0.47},
                    "model_components": {"nowcast_probability_up": 0.607, "technical_probability_up": 0.495, "technical_weight": 0.35, "technical_status": "ok_model_blend"},
                }],
                "signals": [{"interval": "5m", "model_components": {"nowcast_probability_up": 0.607, "technical_probability_up": 0.495, "technical_weight": 0.35}, "technical": {"status": "ok_model_blend"}}],
            }
        elif name == "dexter_research":
            output = {"thesis": "neutral", "artifacts": {}}
        else:
            output = {}
        return type("Result", (), {"model_dump": lambda self: {"name": name, "ok": True, "output": output}})()

    monkeypatch.setattr(ctx.tools, "execute", fake_execute)
    result = PolymrktAgent().act("Simula la nueva predicción híbrida de Polymarket BTC Up/Down para 5m y 15m", ctx)

    assert result["usage"] == {}
    assert result["events"][-1]["decision"]["skipped"] is True
    assert "Simulación predictiva Polymarket BTC Up/Down" in result["result"]
    assert "| Intervalo | Ventana ET | Countdown |" in result["result"]
    assert "confidence_below_threshold" in result["result"]

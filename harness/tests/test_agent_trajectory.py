from __future__ import annotations

import json

import requests

from agents.base import AgentContext
from core.models import SessionState
from orchestrator.engine import HarnessEngine
from runtime.agent_loop import AgentLoop
from tools.registry import ToolRegistry


class FakeLLM:
    def __init__(self, responses):
        self.responses = list(responses)

    def chat(self, messages):
        content = self.responses.pop(0)
        return {
            "content": content,
            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        }


def test_agent_loop_records_tool_events(monkeypatch):
    loop = AgentLoop(
        llm=FakeLLM([
            json.dumps({
                "thought": "verifico",
                "action": "tool",
                "tool": "financial",
                "arguments": {"action": "sharpe", "tickers": ["SPY"]},
            }),
            json.dumps({"thought": "listo", "action": "respond", "final": "Resultado validado."}),
        ])
    )
    ctx = AgentContext(state=SessionState(session_id="x"), tools=ToolRegistry(), role=None)
    monkeypatch.setattr(
        ctx.tools,
        "execute",
        lambda name, **kwargs: type("Result", (), {"model_dump": lambda self: {"name": name, "ok": True}})(),
    )

    result = loop.run("calcula sharpe", ctx, "rol")

    assert result["final"] == "Resultado validado."
    assert result["events"][0]["step"] == 1
    assert result["events"][0]["decision"]["tool"] == "financial"
    assert result["events"][0]["result"]["name"] == "financial"


def test_agent_loop_falls_back_to_tool_output_after_llm_timeout(monkeypatch):
    class TimeoutAfterToolLLM:
        def __init__(self):
            self.calls = 0

        def chat(self, messages):
            self.calls += 1
            if self.calls == 1:
                return {
                    "content": json.dumps({
                        "thought": "consulto señal",
                        "action": "tool",
                        "tool": "polymarket",
                        "arguments": {"action": "btc_updown_scalping_signal"},
                    }),
                    "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
                }
            raise requests.Timeout("read timeout")

    loop = AgentLoop(llm=TimeoutAfterToolLLM(), max_steps=3)
    ctx = AgentContext(state=SessionState(session_id="x"), tools=ToolRegistry(), role="trader")
    monkeypatch.setattr(
        ctx.tools,
        "execute",
        lambda name, **kwargs: type("Result", (), {
            "model_dump": lambda self: {
                "name": "polymarket",
                "ok": True,
                "output": {
                    "signals": [{
                        "interval": "5m",
                        "countdown": "02:15",
                        "preferred_side": "Up",
                        "confidence": 0.84,
                        "meets_threshold": True,
                        "current_price_reference": 101,
                        "start_price_reference": 100,
                        "prophet": {"status": "fallback"},
                    }]
                },
            }
        })(),
    )

    result = loop.run("analiza bitcoin", ctx, "rol")

    assert "Señal operativa Polymarket BTC" in result["final"]
    assert "Decisión UP" in result["final"]
    assert "5m" in result["final"]
    assert result["events"][-1]["action"] == "llm_timeout"


def test_engine_synthesizes_without_agent_prefix_or_internal_json():
    engine = HarnessEngine()
    response = engine._synthesize(
        "demo",
        {
            "agent": "finance",
            "result": '{"thought":"interno","action":"respond","final":"Decisión: NO TRADE\\nRazón: confianza menor a 80%."}',
        },
    )

    assert response == "Decisión: NO TRADE\nRazón: confianza menor a 80%."
    assert "[finance]" not in response
    assert '"thought"' not in response


def test_engine_persists_trajectory_metadata(tmp_path, monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "artifact_root", str(tmp_path / "artifacts"))
    monkeypatch.setattr(settings, "conversation_root", str(tmp_path / "conversations"))
    monkeypatch.setattr(settings, "session_root", str(tmp_path / "sessions"))

    engine = HarnessEngine()

    class FakeAgent:
        def act(self, prompt, ctx):
            return {
                "agent": "planner",
                "result": "ok",
                "events": [{"step": 1, "decision": {"action": "respond"}}],
                "usage": {"completion_tokens": 2},
                "last_usage": {"prompt_tokens": 4},
            }

    engine.agents.get = lambda name: FakeAgent()
    response = engine.chat("demo", "hola", "planner")
    task = response["tasks"][-1]

    assert task["metadata"]["trajectory_path"].endswith(".jsonl")
    assert task["metadata"]["events"][0]["step"] == 1
    assert response["metadata"]["tokens_generated_total"] == 2


def test_engine_saves_running_task_before_agent_finishes(tmp_path, monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "artifact_root", str(tmp_path / "artifacts"))
    monkeypatch.setattr(settings, "conversation_root", str(tmp_path / "conversations"))
    monkeypatch.setattr(settings, "session_root", str(tmp_path / "sessions"))

    engine = HarnessEngine()

    class InspectingAgent:
        def act(self, prompt, ctx):
            saved = engine.sessions.load("demo", "user-1")
            assert saved.tasks[-1].status == "running"
            assert saved.tasks[-1].metadata["started_at"]
            return {
                "agent": "planner",
                "result": "ok",
                "events": [],
                "usage": {},
                "last_usage": {},
            }

    engine.agents.get = lambda name: InspectingAgent()
    engine.chat("demo", "hola", "planner", "user-1", "trader")


def test_engine_titles_new_conversation_from_first_prompt(tmp_path, monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "artifact_root", str(tmp_path / "artifacts"))
    monkeypatch.setattr(settings, "conversation_root", str(tmp_path / "conversations"))
    monkeypatch.setattr(settings, "session_root", str(tmp_path / "sessions"))

    engine = HarnessEngine()
    created = engine.sessions.create("user-1", "Nueva conversación")

    class FakeAgent:
        def act(self, prompt, ctx):
            return {"agent": "finance", "result": "ok", "events": [], "usage": {}, "last_usage": {}}

    engine.agents.get = lambda name: FakeAgent()
    engine.chat(created.session_id, "Analiza MEXC Spot BTC/USDT ETH/USDT con RSI", "finance", "user-1", "trader")
    saved = engine.sessions.load(created.session_id, "user-1")

    assert saved.metadata["title"] == "MEXC Spot BTCUSDT, ETHUSDT"



def test_base_agent_coerces_final_from_json_text():
    from agents.base import BaseAgent

    raw = '{"final":"Respuesta limpia\\n\\n| A | B |\\n|---|---|\\n| 1 | 2 |"}'
    assert BaseAgent()._coerce_llm_final(raw).startswith("Respuesta limpia")
    assert "{\"final\"" not in BaseAgent()._coerce_llm_final(raw)



def test_response_normalizes_mojibake_utf8_text():
    from runtime.text_encoding import normalize_utf8_text

    broken = "La decisiÃ³n es NO TRADE. SegÃºn el flujo de seÃ±ales no se recomienda acciÃ³n."
    fixed = normalize_utf8_text(broken)

    assert fixed == "La decisión es NO TRADE. Según el flujo de señales no se recomienda acción."
    assert "Ã" not in fixed


def test_engine_clean_response_normalizes_mojibake_json_final():
    engine = HarnessEngine()
    response = engine._synthesize("demo", {"result": '{"final":"La decisiÃ³n es NO TRADE. SegÃºn seÃ±ales."}'})

    assert response == "La decisión es NO TRADE. Según señales."

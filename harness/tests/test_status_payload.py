from core.models import AgentTask, SessionState, TaskStatus
from api.app import build_status_payload, summarize_latest_task
from observability import ValidationCollector


def test_summarize_latest_task_reports_steps_and_last_tool():
    state = SessionState(session_id="demo")
    task = AgentTask(id="task-1", objective="consulta mercado", agent="finance", status=TaskStatus.completed)
    task.metadata = {
        "started_at": "2026-05-19T01:00:00Z",
        "finished_at": "2026-05-19T01:00:03Z",
        "events": [
            {"step": 1, "result": {"name": "polymarket", "ok": True, "duration_ms": 120}},
            {"step": 2, "decision": {"action": "respond"}},
        ],
        "trajectory_path": "/app/storage/artifacts/trajectories/2026-05-19.jsonl",
    }
    state.tasks.append(task)

    summary = summarize_latest_task(state)

    assert summary["status"] == "completed"
    assert summary["steps_count"] == 2
    assert summary["tool_count"] == 1
    assert summary["duration_ms"] == 3000
    assert summary["last_tool"] == "polymarket"
    assert summary["last_tool_ok"] is True
    assert summary["trajectory_path"].endswith(".jsonl")


def test_build_status_payload_includes_operational_tokens():
    state = SessionState(session_id="demo")
    state.metadata.update({
        "last_prompt_tokens": 321,
        "last_completion_tokens": 80,
        "tokens_generated_total": 140,
        "context_window": 16384,
    })

    payload = build_status_payload(state, "trader")

    assert payload["ok"] is True
    assert payload["service"] == "quantlab_harness"
    assert payload["session_id"] == "demo"
    assert payload["tokens"]["last_prompt_tokens"] == 321
    assert payload["tokens"]["last_completion_tokens"] == 80
    assert payload["tokens"]["tokens_generated_total"] == 140
    assert payload["latest_task"] is None
    assert "system" in payload


def test_validation_collector_resolves_expired_polymarket_trade(monkeypatch, tmp_path):
    collector = ValidationCollector(runtime_root=tmp_path / "runtime")
    monkeypatch.setattr(collector, "_polymarket_final_price", lambda interval, start, end: 99.0)

    tx = collector._normalize_transaction(
        {
            "id": "poly-1",
            "timestamp": "2020-01-01T20:00:00Z",
            "venue": "polymarket",
            "market": "Bitcoin",
            "symbol": "BTC",
            "side": "DOWN",
            "status": "accepted",
            "price": 0.25,
            "stake_usdt": 25,
            "interval": "5m",
            "window": "2020-01-01 15:00:00 EST - 2020-01-01 15:05:00 EST",
            "indicators": {"price_to_beat_reference": 100},
        },
        {"agent": "paper_trading", "mode": "paper", "strategy": "test"},
    )

    assert tx["status"] == "won"
    assert tx["pnl"] == 75.0
    assert tx["indicators"]["final_price_reference"] == 99.0
    assert tx["indicators"]["winning_side"] == "DOWN"


def test_validation_collector_keeps_open_polymarket_trade_pending(monkeypatch, tmp_path):
    collector = ValidationCollector(runtime_root=tmp_path / "runtime")
    monkeypatch.setattr(collector, "_polymarket_final_price", lambda interval, start, end: 99.0)

    tx = collector._normalize_transaction(
        {
            "id": "poly-open",
            "timestamp": "2099-01-01T20:00:00Z",
            "venue": "polymarket",
            "side": "DOWN",
            "status": "accepted",
            "price": 0.25,
            "stake_usdt": 25,
            "interval": "5m",
            "window": "2099-01-01 15:00:00 EST - 2099-01-01 15:05:00 EST",
            "indicators": {"price_to_beat_reference": 100},
        },
        {"agent": "paper_trading", "mode": "paper", "strategy": "test"},
    )

    assert tx["status"] == "accepted"
    assert tx["pnl"] == 0

import json

from api.app import build_paper_trading_snapshot


def test_build_paper_trading_snapshot_reports_mode_trades_and_log_health(tmp_path):
    root = tmp_path / "paper_trading"
    root.mkdir()
    (root / "config.json").write_text(
        json.dumps({"enabled": True, "mode": "paper", "bankroll_usdt": 1000, "max_stake_pct": 0.05}),
        encoding="utf-8",
    )
    cycle = {
        "mode": "paper",
        "cycle_id": "cycle-1",
        "created_at": "2026-05-21T04:09:02Z",
        "bankroll_usdt": 1000,
        "max_stake_pct": 0.05,
        "orders": [{"venue": "polymarket", "market": "BTC Up/Down", "side": "DOWN", "stake_usdt": 50}],
        "observations": [{"venue": "mexc"}],
        "errors": [],
    }
    (root / "2026-05-21.jsonl").write_text(json.dumps(cycle) + "\n", encoding="utf-8")
    (root / "systemd.log").write_text("ok\n", encoding="utf-8")
    (root / "systemd.err").write_text("", encoding="utf-8")

    snapshot = build_paper_trading_snapshot(root)

    assert snapshot["mode"] == "paper"
    assert snapshot["success"] is True
    assert snapshot["status"] in {"ok", "stale"}
    assert snapshot["orders"][0]["stake_usdt"] == 50
    assert snapshot["logs"]["files"]
    assert snapshot["logs"]["retention"].startswith("logrotate")



def test_paper_trading_config_accepts_custom_stake(tmp_path, monkeypatch):
    from api import app as api_app
    from config import settings

    monkeypatch.setattr(settings, "artifact_root", str(tmp_path / "artifacts"))
    config = api_app._sanitize_paper_trading_update({
        "polymarket_stake_usdt": 4.75,
        "polymarket_strategy_profile": "adaptive_5m15m",
        "threshold": 0.82,
        "polymarket_min_edge": 0.1,
        "polymarket_max_spread": 0.08,
        "polymarket_min_ask_size": 2,
        "polymarket_min_seconds_to_close": 75,
    })

    assert config["polymarket_stake_usdt"] == 4.75
    assert config["polymarket_strategy_profile"] == "adaptive_5m15m"
    assert config["threshold"] == 0.82
    assert config["polymarket_min_edge"] == 0.1
    assert config["polymarket_max_spread"] == 0.08
    assert config["polymarket_min_ask_size"] == 2
    assert config["polymarket_min_seconds_to_close"] == 75
    payload = api_app.paper_trading_rules_payload()
    assert payload["stake_min"] == 0.1
    assert payload["stake_max"] == 100.0
    assert payload["allowed_stakes"] == [1, 2, 3]
    assert payload["allowed_strategy_profiles"] == ["adaptive_5m15m", "legacy"]
    assert payload["polymarket_strategy_profile"] == "adaptive_5m15m"

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

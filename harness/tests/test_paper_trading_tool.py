from datetime import UTC, datetime
from pathlib import Path

from config import settings
from tools.paper_trading import PaperTradingTool


def test_paper_trading_cycle_records_simulated_orders(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "artifact_root", str(tmp_path / "artifacts"))

    def fake_poly_run(self, **kwargs):
        return {
            "signals": [{
                "interval": "5m",
                "countdown": "03:20",
                "start_time_et": "2026-05-20 12:00:00 EDT",
                "end_time_et": "2026-05-20 12:05:00 EDT",
                "preferred_side": "Up",
                "confidence": 0.84,
                "meets_threshold": True,
                "prophet": {"up_probability": 0.84},
            }],
            "markets": [{
                "interval": "5m",
                "seconds_to_close": 200,
                "liquidity": "10000",
                "tokens": [
                    {"outcome": "Up", "book": {"best_ask": {"price": "0.70"}}},
                    {"outcome": "Down", "book": {"best_ask": {"price": "0.31"}}},
                ],
            }],
        }

    def fake_mexc_run(self, **kwargs):
        return {
            "results": [{
                "symbol": "BTCUSDT",
                "signal": "BUY",
                "price": 100,
                "rsi": 29,
                "macd_histogram": -1,
                "vwap": 101,
                "risk": "Controlado",
            }]
        }

    monkeypatch.setattr("tools.paper_trading.PolymarketTool.run", fake_poly_run)
    monkeypatch.setattr("tools.paper_trading.MexcSpotTool.run", fake_mexc_run)

    result = PaperTradingTool().run(
        action="run_cycle",
        role="trader",
        mode="paper",
        venues=["polymarket", "mexc"],
        mexc_tickers=["BTCUSDT"],
    )

    assert result["mode"] == "paper"
    assert result["bankroll_usdt"] == 10000
    assert result["polymarket_stake_usdt"] == 1
    assert result["orders_count"] == 2
    assert result["orders"][0]["execution"] == "simulated_only"
    assert result["orders"][0]["stake_usdt"] == 1
    assert Path(result["audit_path"]).exists()


def test_paper_trading_uses_coordinated_polymarket_signal(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "artifact_root", str(tmp_path / "artifacts"))

    def fake_poly_run(self, **kwargs):
        assert kwargs["action"] == "btc_updown_5m15m_coordinated_signal"
        return {
            "action": "TRADE",
            "side": "UP",
            "strategy": "BTC Up/Down coordinated 5m/15m paper signal",
            "filters": {"min_edge": 0.03},
            "candidates": [
                {
                    "interval": "5m",
                    "preferred_side": "Up",
                    "confidence": 0.86,
                    "probability": 0.86,
                    "edge": 0.06,
                    "microstructure": {"ask": 0.80, "spread": 0.04, "ask_size": 5},
                    "price_to_beat_reference": 100.5,
                    "current_price_reference": 101.2,
                    "forecast_price_at_close": 102.0,
                    "passes_filters": True,
                    "countdown": "03:00",
                    "window_et": "2026-05-20 12:00:00 EDT - 2026-05-20 12:05:00 EDT",
                },
                {
                    "interval": "15m",
                    "preferred_side": "Up",
                    "confidence": 0.84,
                    "probability": 0.84,
                    "edge": 0.05,
                    "microstructure": {"ask": 0.79, "spread": 0.04, "ask_size": 5},
                    "price_to_beat_reference": 99.5,
                    "current_price_reference": 101.2,
                    "forecast_price_at_close": 102.0,
                    "passes_filters": True,
                    "countdown": "13:00",
                    "window_et": "2026-05-20 12:00:00 EDT - 2026-05-20 12:15:00 EDT",
                },
            ],
            "reasons": [],
        }

    monkeypatch.setattr("tools.paper_trading.PolymarketTool.run", fake_poly_run)

    result = PaperTradingTool().run(
        action="run_cycle",
        role="trader",
        mode="paper",
        venues=["polymarket"],
        bankroll_usdt=1000,
    )

    assert result["orders_count"] == 2
    assert result["orders"][0]["strategy"] == "BTC Up/Down coordinated 5m/15m paper signal"
    assert result["orders"][0]["stake_usdt"] == 1
    assert result["orders"][1]["stake_usdt"] == 1
    assert result["orders"][0]["full_kelly"] == 0.3
    assert result["orders"][0]["price_to_beat_reference"] == 100.5
    assert result["transactions"][0]["indicators"]["price_to_beat_reference"] == 100.5


def test_paper_trading_trades_each_polymarket_event_independently(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "artifact_root", str(tmp_path / "artifacts"))

    def fake_poly_run(self, **kwargs):
        return {
            "action": "NO_TRADE",
            "side": "NONE",
            "strategy": "BTC Up/Down coordinated 5m/15m paper signal",
            "filters": {"min_edge": 0.03},
            "candidates": [
                {
                    "interval": "5m",
                    "preferred_side": "Down",
                    "confidence": 0.88,
                    "probability": 0.88,
                    "edge": 0.18,
                    "microstructure": {"ask": 0.70, "spread": 0.01, "ask_size": 25},
                    "price_to_beat_reference": 100,
                    "current_price_reference": 99,
                    "forecast_price_at_close": 98,
                    "passes_filters": True,
                    "countdown": "03:00",
                    "window_et": "2026-05-20 12:00:00 EDT - 2026-05-20 12:05:00 EDT",
                },
                {
                    "interval": "15m",
                    "preferred_side": "Down",
                    "confidence": 0.72,
                    "probability": 0.72,
                    "edge": 0.02,
                    "microstructure": {"ask": 0.70, "spread": 0.01, "ask_size": 25},
                    "passes_filters": False,
                    "reasons": ["confidence_below_threshold", "edge_too_small"],
                    "countdown": "13:00",
                    "window_et": "2026-05-20 12:00:00 EDT - 2026-05-20 12:15:00 EDT",
                },
            ],
            "reasons": ["both_windows_must_pass_filters"],
        }

    monkeypatch.setattr("tools.paper_trading.PolymarketTool.run", fake_poly_run)

    result = PaperTradingTool().run(
        action="run_cycle",
        role="trader",
        mode="paper",
        venues=["polymarket"],
        bankroll_usdt=1000,
    )

    assert result["orders_count"] == 1
    assert result["orders"][0]["interval"] == "5m"
    assert result["orders"][0]["side"] == "DOWN"
    assert result["transactions"][0]["side"] == "DOWN"


def test_paper_trading_blocks_live_mode_without_execution_gate(monkeypatch):
    monkeypatch.setattr(settings, "polymarket_live_trading_enabled", False)
    tool = PaperTradingTool()

    result = tool.run(action="run_cycle", role="trader", mode="live")

    assert result["live_blocked"] is True
    assert result["live_execution_enabled"] is False
    assert any("LIVE bloqueado" in item.get("error", "") for item in result["errors"])


def test_paper_trading_allows_only_one_polymarket_trade_per_window(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "artifact_root", str(tmp_path / "artifacts"))

    def fake_poly_run(self, **kwargs):
        return {
            "action": "TRADE",
            "side": "DOWN",
            "strategy": "BTC Up/Down coordinated 5m/15m paper signal",
            "filters": {"min_edge": 0.03},
            "candidates": [
                {
                    "interval": "5m",
                    "preferred_side": "Down",
                    "confidence": 0.9,
                    "probability": 0.9,
                    "edge": 0.3,
                    "microstructure": {"ask": 0.60, "spread": 0.01, "ask_size": 10},
                    "price_to_beat_reference": 100,
                    "current_price_reference": 99,
                    "forecast_price_at_close": 98,
                    "passes_filters": True,
                    "countdown": "03:00",
                    "window_et": "2026-12-20 12:00:00 EDT - 2026-12-20 12:05:00 EDT",
                }
            ],
            "reasons": [],
        }

    monkeypatch.setattr("tools.paper_trading.PolymarketTool.run", fake_poly_run)

    first = PaperTradingTool().run(
        action="run_cycle",
        role="trader",
        mode="paper",
        venues=["polymarket"],
        bankroll_usdt=1000,
    )
    second = PaperTradingTool().run(
        action="run_cycle",
        role="trader",
        mode="paper",
        venues=["polymarket"],
        bankroll_usdt=1000,
    )

    assert first["orders_count"] == 1
    assert second["orders_count"] == 0
    assert second["transactions_count"] == 0
    assert second["observations"][0]["reason"] == "duplicate_window_trade"



def test_polymarket_inverts_prediction_before_order_sizing(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "artifact_root", str(tmp_path / "artifacts"))

    def fake_poly_run(self, **kwargs):
        return {
            "strategy": "BTC Up/Down coordinated 5m/15m live signal",
            "filters": {"min_edge": 0.03},
            "markets": [{
                "interval": "5m",
                "tokens": [
                    {"outcome": "Up", "token_id": "up-token", "book": {"best_bid": {"price": "0.20"}, "best_ask": {"price": "0.24", "size": "20"}}},
                    {"outcome": "Down", "token_id": "down-token", "book": {"best_bid": {"price": "0.60"}, "best_ask": {"price": "0.64", "size": "20"}}},
                ],
            }],
            "candidates": [{
                "interval": "5m",
                "preferred_side": "Down",
                "confidence": 0.9,
                "probability": 0.9,
                "edge": 0.26,
                "microstructure": {"ask": 0.64, "spread": 0.04, "ask_size": 20, "token_id": "down-token"},
                "passes_filters": True,
                "countdown": "03:00",
                "window_et": "2026-05-20 12:00:00 EDT - 2026-05-20 12:05:00 EDT",
            }],
            "reasons": [],
        }

    monkeypatch.setattr("tools.paper_trading.PolymarketTool.run", fake_poly_run)

    result = PaperTradingTool().run(
        action="run_cycle",
        role="trader",
        mode="paper",
        venues=["polymarket"],
        bankroll_usdt=1000,
        polymarket_stake_usdt=3,
        polymarket_invert_prediction_enabled=True,
    )

    assert result["orders_count"] == 1
    assert result["orders"][0]["side"] == "UP"
    assert result["orders"][0]["predicted_side"] == "Down"
    assert result["orders"][0]["prediction_inverted"] is True
    assert result["orders"][0]["token_id"] == "up-token"


def test_live_polymarket_uses_quarter_kelly_and_executor(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "artifact_root", str(tmp_path / "artifacts"))
    monkeypatch.setattr(settings, "polymarket_live_trading_enabled", True)

    def fake_poly_run(self, **kwargs):
        return {
            "strategy": "BTC Up/Down coordinated 5m/15m live signal",
            "filters": {"min_edge": 0.03},
            "candidates": [{
                "interval": "5m",
                "preferred_side": "Up",
                "confidence": 0.9,
                "probability": 0.9,
                "edge": 0.3,
                "microstructure": {"ask": 0.60, "spread": 0.01, "ask_size": 10, "token_id": "up-token"},
                "passes_filters": True,
                "countdown": "03:00",
                "window_et": "2026-05-20 12:00:00 EDT - 2026-05-20 12:05:00 EDT",
            }],
            "reasons": [],
        }

    executions = []

    def fake_execute(self, result, order, base):
        executions.append(order.copy())
        order["execution"] = "live_order_sent"
        order["transaction_status"] = "accepted"
        order["execution_result"] = {"order_id": "abc", "secret_exposed": False}

    monkeypatch.setattr("tools.paper_trading.PolymarketTool.run", fake_poly_run)
    monkeypatch.setattr("tools.paper_trading.PaperTradingTool._execute_polymarket_live_if_needed", fake_execute)

    result = PaperTradingTool().run(
        action="run_cycle",
        role="trader",
        mode="live",
        venues=["polymarket", "mexc"],
        live_execution_enabled=True,
        bankroll_usdt=1000,
        polymarket_stake_usdt=3,
        kelly_fraction=0.25,
    )

    assert result["mode"] == "live"
    assert result["kelly_fraction"] == 0.25
    assert result["orders_count"] == 1
    assert result["orders"][0]["stake_usdt"] == 3
    assert result["orders"][0]["execution"] == "live_order_sent"
    assert result["orders"][0]["token_id"] == "up-token"
    assert executions



def test_live_polymarket_liquidates_position_at_100_percent_profit(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "artifact_root", str(tmp_path / "artifacts"))
    monkeypatch.setattr(settings, "polymarket_live_trading_enabled", True)

    def fake_poly_run(self, **kwargs):
        return {"strategy": "BTC Up/Down coordinated 5m/15m live signal", "candidates": [], "reasons": ["no_event"]}

    positions = [{
        "title": "Bitcoin Up or Down - May 28, 2:50PM-2:55PM ET",
        "asset": "token-profit",
        "conditionId": "cond-profit",
        "outcome": "Up",
        "size": 4.25,
        "avgPrice": 0.34,
        "curPrice": 0.80,
        "currentValue": 3.4,
        "cashPnl": 1.95,
        "percentPnl": 135.0,
        "redeemable": False,
    }]
    sells = []

    def fake_sell(self, token_id, shares, current_price):
        sells.append((token_id, shares, current_price))
        return {"order_id": "sell-1", "status": "matched", "success": True, "secret_exposed": False}

    monkeypatch.setattr("tools.paper_trading.PolymarketTool.run", fake_poly_run)
    monkeypatch.setattr("tools.paper_trading.PaperTradingTool._fetch_polymarket_positions", lambda self: positions)
    monkeypatch.setattr("tools.paper_trading.PaperTradingTool._place_polymarket_market_sell", fake_sell)

    result = PaperTradingTool().run(
        action="run_cycle",
        role="trader",
        mode="live",
        venues=["polymarket"],
        live_execution_enabled=True,
    )

    assert sells == [("token-profit", 4.25, 0.80)]
    assert result["position_actions_count"] == 1
    assert result["position_actions"][0]["status"] == "take_profit_order_sent"
    assert result["position_actions"][0]["execution_result"]["order_id"] == "sell-1"


def test_live_polymarket_records_redeemable_profit_for_claim(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "artifact_root", str(tmp_path / "artifacts"))
    monkeypatch.setattr(settings, "polymarket_live_trading_enabled", True)

    def fake_poly_run(self, **kwargs):
        return {"strategy": "BTC Up/Down coordinated 5m/15m live signal", "candidates": [], "reasons": ["no_event"]}

    positions = [{
        "title": "Bitcoin Up or Down - May 28, 2:50PM-2:55PM ET",
        "asset": "token-redeem",
        "conditionId": "cond-redeem",
        "outcome": "Down",
        "size": 5,
        "avgPrice": 0.40,
        "curPrice": 1,
        "currentValue": 5,
        "cashPnl": 3,
        "percentPnl": 150,
        "redeemable": True,
    }]

    monkeypatch.delenv("POLYMARKET_CLAIM_HTTP_URL", raising=False)
    monkeypatch.setattr("tools.paper_trading.PolymarketTool.run", fake_poly_run)
    monkeypatch.setattr("tools.paper_trading.PaperTradingTool._fetch_polymarket_positions", lambda self: positions)

    result = PaperTradingTool().run(
        action="run_cycle",
        role="trader",
        mode="live",
        venues=["polymarket"],
        live_execution_enabled=True,
    )

    assert result["claim_actions_count"] == 1
    assert result["claim_actions"][0]["status"] == "claim_ready_relayer_not_configured"
    assert result["claim_actions"][0]["cash_pnl"] == 3


def test_live_polymarket_liquidates_position_at_stop_loss(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "artifact_root", str(tmp_path / "artifacts"))
    monkeypatch.setattr(settings, "polymarket_live_trading_enabled", True)

    def fake_poly_run(self, **kwargs):
        return {"strategy": "BTC Up/Down coordinated 5m/15m live signal", "candidates": [], "reasons": ["no_event"]}

    start_ts = int(datetime.now(UTC).timestamp()) - 240
    positions = [{
        "title": "Bitcoin Up or Down - current 5m window",
        "slug": f"btc-updown-5m-{start_ts}",
        "asset": "token-loss",
        "conditionId": "cond-loss",
        "outcome": "Down",
        "size": 3,
        "avgPrice": 1.00,
        "curPrice": 0.91,
        "currentValue": 2.73,
        "cashPnl": -0.27,
        "percentPnl": -9.0,
        "redeemable": False,
    }]
    sells = []

    def fake_sell(self, token_id, shares, current_price):
        sells.append((token_id, shares, current_price))
        return {"order_id": "sl-1", "status": "matched", "success": True, "secret_exposed": False}

    monkeypatch.setattr("tools.paper_trading.PolymarketTool.run", fake_poly_run)
    monkeypatch.setattr("tools.paper_trading.PaperTradingTool._fetch_polymarket_positions", lambda self: positions)
    monkeypatch.setattr("tools.paper_trading.PaperTradingTool._place_polymarket_market_sell", fake_sell)

    result = PaperTradingTool().run(
        action="run_cycle",
        role="trader",
        mode="live",
        venues=["polymarket"],
        live_execution_enabled=True,
        polymarket_time_stop_pct=60,
    )

    assert sells == [("token-loss", 3, 0.91)]
    assert result["position_actions_count"] == 1
    assert result["position_actions"][0]["action"] == "liquidate_stop_loss_time_75"
    assert result["position_actions"][0]["status"] == "stop_loss_time_75_order_sent"
    assert result["position_actions"][0]["threshold_pct"] == 60



def test_paper_trading_preserves_custom_polymarket_stake(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "artifact_root", str(tmp_path / "artifacts"))

    def fake_poly_run(self, **kwargs):
        return {
            "strategy": "BTC Up/Down coordinated 5m/15m paper signal",
            "candidates": [{
                "interval": "5m",
                "preferred_side": "Up",
                "confidence": 0.9,
                "probability": 0.9,
                "edge": 0.3,
                "microstructure": {"ask": 0.60, "spread": 0.01, "ask_size": 10, "token_id": "up-token"},
                "passes_filters": True,
                "countdown": "03:00",
                "window_et": "2026-05-20 12:00:00 EDT - 2026-05-20 12:05:00 EDT",
            }],
            "reasons": [],
        }

    monkeypatch.setattr("tools.paper_trading.PolymarketTool.run", fake_poly_run)

    result = PaperTradingTool().run(
        action="run_cycle",
        role="trader",
        mode="paper",
        venues=["polymarket"],
        bankroll_usdt=1000,
        polymarket_stake_usdt=4.5,
    )

    assert result["polymarket_stake_usdt"] == 4.5
    assert result["orders"][0]["stake_usdt"] == 4.5

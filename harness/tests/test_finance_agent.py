from agents.base import AgentContext
from agents.specialists import FinanceAgent
from core.models import SessionState
from tools.registry import ToolRegistry


def test_finance_agent_formats_btc_updown_without_llm(monkeypatch):
    agent = FinanceAgent()
    ctx = AgentContext(state=SessionState(session_id="demo"), tools=ToolRegistry(), role="trader")

    def fake_execute(name, role=None, **kwargs):
        assert name == "polymarket"
        assert kwargs["action"] == "btc_updown_5m15m_coordinated_signal"
        assert kwargs["candle_interval"] == "5m"
        assert kwargs["lookback_window"] == "1d"
        assert kwargs["lookback"] == 288
        assert kwargs["prediction_candle_interval"] == "1m"
        assert kwargs["prediction_lookback"] == 90
        return type("Result", (), {
            "model_dump": lambda self: {
                "name": "polymarket",
                "ok": True,
                "output": {
                    "signals": [{
                        "interval": "5m",
                        "countdown": "04:36",
                        "start_time_et": "2026-05-20 14:00:00 EDT",
                        "end_time_et": "2026-05-20 14:05:00 EDT",
                        "price_to_beat_reference": 77553.25,
                        "start_price_reference": 77552.34,
                        "current_price_reference": 77554.06,
                        "price_delta_reference": 0.81,
                        "preferred_side": "Up",
                        "confidence": 0.47,
                        "meets_threshold": False,
                        "forecast_price_at_close": 77590.12,
                        "prophet": {"up_probability": 0.47},
                    }],
                    "markets": [{
                        "interval": "5m",
                        "liquidity": "11135.7469",
                        "seconds_to_close": 276,
                        "tokens": [
                            {"outcome": "Up", "book": {"best_bid": {"price": "0.47"}, "best_ask": {"price": "0.49"}}},
                            {"outcome": "Down", "book": {"best_bid": {"price": "0.51"}, "best_ask": {"price": "0.52"}}},
                        ],
                    }],
                },
            }
        })()

    monkeypatch.setattr(ctx.tools, "execute", fake_execute)

    result = agent.act("Analiza Polymarket Bitcoin 5m y 15m scalping", ctx)

    assert result["usage"] == {}
    assert result["events"][0]["decision"]["tool"] == "polymarket"
    assert result["result"].startswith("Decisión: NO TRADE")
    assert "2023" not in result["result"]
    assert '"Hora_actual_simulacion"' not in result["result"]
    assert "| Mercado | Intervalo | Ventana ET | Countdown | Precio a superar |" in result["result"]
    assert "| Kelly | Stake Máx |" in result["result"]
    assert "| Bitcoin | 5m |" in result["result"]
    assert "77553.25" in result["result"]
    assert "77590.12" in result["result"]
    assert "UP 47.0% / DOWN 53.0%" in result["result"]
    assert "chainlink_1m_bounded_nowcast" in result["result"]
    assert "Kelly: fraccion" in result["result"]


def test_finance_agent_formats_mexc_spot_scan_without_llm(monkeypatch):
    agent = FinanceAgent()
    ctx = AgentContext(state=SessionState(session_id="demo"), tools=ToolRegistry(), role="trader")

    def fake_execute(name, role=None, **kwargs):
        assert name == "mexc_spot"
        assert kwargs["action"] == "scan_spot_long_candidates"
        assert "AIDOGEUSDT" in kwargs["tickers"]
        return type("Result", (), {
            "model_dump": lambda self: {
                "name": "mexc_spot",
                "ok": True,
                "output": {
                    "interval": "15m",
                    "results": [{
                        "symbol": "AIDOGEUSDT",
                        "price": 0.0001,
                        "rsi": 28.5,
                        "macd_histogram": -0.01,
                        "vwap": 0.00011,
                        "price_below_vwap": True,
                        "volume_ratio": 1.2,
                        "lower_band_touch": True,
                        "ema20": 0.00012,
                        "ema50": 0.00013,
                        "setup_score": 6,
                        "risk": "Controlado",
                        "signal": "BUY",
                        "rsi_oversold": True,
                        "macd_negative": True,
                        "price_above_vwap": False,
                        "upper_band_touch": False,
                        "sell_score": 0,
                    }],
                },
            }
        })()

    monkeypatch.setattr(ctx.tools, "execute", fake_execute)

    result = agent.act("MEXC spot LONG RSI MACD VWAP AIDOGE/USDT ELF/USDT", ctx)

    assert result["usage"] == {}
    assert result["events"][0]["decision"]["tool"] == "mexc_spot"
    assert result["result"].startswith("Señal MEXC Spot: BUY")
    assert "| Ticker | Señal | Precio | RSI |" in result["result"]
    assert "SELL:" in result["result"] or "| SELL Score |" in result["result"]
    assert "| AIDOGEUSDT | BUY |" in result["result"]


def test_finance_agent_runs_paper_trading_cycle_without_llm(monkeypatch):
    agent = FinanceAgent()
    ctx = AgentContext(state=SessionState(session_id="demo"), tools=ToolRegistry(), role="trader")

    def fake_execute(name, role=None, **kwargs):
        assert name == "paper_trading"
        assert kwargs["mode"] == "paper"
        assert kwargs["venues"] == ["polymarket", "mexc"]
        assert "BTCUSDT" in kwargs["mexc_tickers"]
        return type("Result", (), {
            "model_dump": lambda self: {
                "name": "paper_trading",
                "ok": True,
                "output": {
                    "mode": "paper",
                    "bankroll_usdt": 1000,
                    "max_stake_pct": 0.05,
                    "audit_path": "/app/storage/artifacts/paper_trading/2026-05-20.jsonl",
                    "orders": [{
                        "venue": "polymarket",
                        "market": "BTC Up/Down",
                        "interval": "5m",
                        "side": "UP",
                        "price": 0.7,
                        "probability": 0.84,
                        "fractional_kelly": 0.116,
                        "stake_usdt": 50,
                        "reason": "probabilidad y Kelly positivos",
                    }],
                    "observations": [],
                    "errors": [],
                },
            }
        })()

    monkeypatch.setattr(ctx.tools, "execute", fake_execute)

    result = agent.act("MODE=paper automatiza ciclo Polymarket y MEXC BTC/USDT", ctx)

    assert result["events"][0]["decision"]["tool"] == "paper_trading"
    assert result["result"].startswith("Paper Trading:")
    assert "| polymarket | BTC Up/Down | 5m | UP |" in result["result"]
    assert "MODE=paper" in result["result"]

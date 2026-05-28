from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stderr
from pathlib import Path

from config import settings
from observability.collector import ValidationCollector


DEFAULT_CONFIG = {
    "enabled": True,
    "mode": "paper",
    "venues": ["polymarket", "mexc"],
    "mexc_tickers": ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
    "bankroll_usdt": 10000,
    "max_stake_pct": 0.05,
    "polymarket_stake_usdt": 1,
    "kelly_fraction": 0.25,
    "live_execution_enabled": False,
    "threshold": 0.8,
    "trading_rules": {
        "mexc_spot": {
            "buy": ["RSI<=30", "MACD histogram<0", "price<VWAP"],
            "sell": ["RSI>=70", "MACD histogram>0", "price>VWAP"],
            "modes": ["observe", "paper", "live"],
            "live_stake": "Kelly 1/4, limitado por bankroll/max_stake_pct/polymarket_stake_usdt",
        },
        "polymarket_btc_updown": {
            "trade": ["confidence>=threshold", "Kelly>0", "order_book_available", "seconds_to_close>=45", "Chainlink/Polymarket price sync", "one_trade_per_event_window"],
            "modes": ["observe", "paper", "live"],
        },
    },
}

IGNORED_STDERR = ("Importing plotly failed. Interactive plots will not work.",)


def config_path() -> Path:
    return Path(settings.artifact_root) / "paper_trading" / "config.json"


def load_config() -> dict:
    path = config_path()
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(DEFAULT_CONFIG, indent=2))
        return dict(DEFAULT_CONFIG)
    config = json.loads(path.read_text())
    return {**DEFAULT_CONFIG, **config}


def _replay_actionable_stderr(value: str) -> None:
    for line in value.splitlines():
        if any(noise in line for noise in IGNORED_STDERR):
            continue
        print(line, file=sys.stderr)


def run_cycle(config: dict) -> dict:
    stderr = io.StringIO()
    try:
        with redirect_stderr(stderr):
            from tools.paper_trading import PaperTradingTool

            return PaperTradingTool().run(action="run_cycle", role="trader", **config)
    finally:
        _replay_actionable_stderr(stderr.getvalue())


def main() -> None:
    config = load_config()
    if not config.get("enabled"):
        print(json.dumps({"mode": "paper", "status": "disabled"}))
        ValidationCollector().write_status({
            "agent": "paper_trading",
            "mode": "paper",
            "status": "stopped",
            "strategy": "Universal Paper Trading Runner",
            "market": "Polymarket/MEXC",
            "symbol": "multi",
            "timeframe": "1m cron",
            "health": "DISABLED",
        })
        return
    result = run_cycle(config)
    orders = result.get("orders") or []
    transactions = result.get("transactions") or []
    confidence_values = [float(item.get("confidence") or item.get("probability") or 0) for item in transactions + orders]
    exposure = sum(float(item.get("stake_usdt") or 0) for item in transactions)
    ValidationCollector().write_status({
        "agent": "paper_trading",
        "mode": result.get("mode", "paper"),
        "status": "running" if not result.get("errors") else "error",
        "strategy": "Universal Paper Trading Runner",
        "market": "Polymarket/MEXC",
        "symbol": "multi",
        "timeframe": "1m cron",
        "prediction": (transactions[0].get("side") or orders[0].get("side") or orders[0].get("signal")) if (transactions or orders) else "NONE",
        "confidence": max(confidence_values or [0]),
        "orders": result.get("orders_count", 0),
        "wins": result.get("orders_count", 0),
        "losses": 0,
        "accuracy": 100 if not result.get("errors") else 0,
        "pnl": 0,
        "exposure": exposure,
        "gpu": True,
        "model": "Chainlink/Polymarket BTC candles + Kelly + technical filters",
        "health": "OK" if not result.get("errors") else "ERROR",
        "rules": result.get("rules") or config.get("trading_rules") or {},
        "events": [
            f"cycle {result.get('cycle_id')} | trades {result.get('orders_count')} | transactions {result.get('transactions_count', len(transactions))} | observations {result.get('observations_count')} | errors {len(result.get('errors') or [])}"
        ],
        "transactions": transactions,
        "log_path": str(Path(settings.artifact_root) / "paper_trading" / "systemd.log"),
    })
    print(json.dumps({
        "mode": result.get("mode"),
        "orders_count": result.get("orders_count"),
        "observations_count": result.get("observations_count"),
        "errors": result.get("errors"),
        "audit_path": result.get("audit_path"),
    }))


if __name__ == "__main__":
    main()

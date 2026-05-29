from __future__ import annotations

import json
import math
import os
import re
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import psutil
import requests

from config import settings


MODES = {"observe", "paper", "live"}
STATUSES = {"starting", "running", "waiting", "error", "stopped", "completed"}
POLYMARKET_CHAINLINK_CANDLES_URL = "https://polymarket.com/api/chainlink-candles"


class ValidationCollector:
    """Universal observability layer for QuantLab agents and scheduled strategies."""

    def __init__(self, runtime_root: Path | None = None):
        self.runtime_root = runtime_root or Path(settings.artifact_root).parent / "runtime"
        self.status_dir = self.runtime_root / "status"
        self.logs_dir = self.runtime_root / "logs"
        self.metrics_dir = self.runtime_root / "metrics"
        self.reports_dir = self.runtime_root / "reports"
        self._polymarket_final_price_cache: dict[tuple[str, int, int], float | None] = {}
        for path in (self.status_dir, self.logs_dir, self.metrics_dir, self.reports_dir):
            path.mkdir(parents=True, exist_ok=True)

    def snapshot(self) -> dict[str, Any]:
        agents = self._agents()
        infra = self.infrastructure()
        return {
            "system": "QuantLab AI Capital",
            "environment": settings.app_env,
            "server": os.uname().nodename,
            "generated_at": self._now(),
            "summary": self._summary(agents, infra),
            "agents": agents,
            "infrastructure": infra,
            "discovery": self.discovery(),
            "alerts": self.alerts(agents, infra),
        }

    def health(self) -> dict[str, Any]:
        payload = self.snapshot()
        scores = [agent.get("health_score", 0) for agent in payload["agents"]]
        system_score = min(scores) if scores else payload["summary"]["health_score"]
        return {
            "system": payload["system"],
            "generated_at": payload["generated_at"],
            "health_score": system_score,
            "status": "ok" if system_score >= 80 and not payload["alerts"] else "warning",
            "alerts": payload["alerts"],
        }

    def logs(self, limit: int = 80) -> dict[str, Any]:
        logs: dict[str, Any] = {"generated_at": self._now(), "agents": {}}
        for status in self._status_files():
            name = status.get("agent") or status.get("name")
            if not name:
                continue
            log_path = status.get("log_path")
            lines = self._tail(Path(log_path), limit) if log_path else []
            logs["agents"][name] = lines or status.get("events") or []
        return logs

    def performance(self) -> dict[str, Any]:
        agents = self._agents()
        return {
            "generated_at": self._now(),
            "agents": [
                {
                    "name": agent["name"],
                    "mode": agent["mode"],
                    "status": agent["status"],
                    "pnl": agent.get("pnl", 0),
                    "accuracy": agent.get("accuracy", 0),
                    "sharpe": agent.get("sharpe", 0),
                    "sortino": agent.get("sortino", 0),
                    "max_drawdown": agent.get("max_drawdown", 0),
                    "var": agent.get("var", 0),
                    "cvar": agent.get("cvar", 0),
                    "orders": agent.get("orders", 0),
                    "wins": agent.get("wins", 0),
                    "losses": agent.get("losses", 0),
                    "health_score": agent.get("health_score", 0),
                }
                for agent in agents
            ],
        }

    def transactions(self, limit: int = 200) -> dict[str, Any]:
        rows = []
        for status in self._status_files():
            rows.extend(self._transactions_from_status(status))
        rows.extend(self._paper_trading_transactions(limit))
        by_id: dict[str, dict[str, Any]] = {}
        for row in rows:
            key = self._transaction_dedupe_key(row)
            previous = by_id.get(key)
            if previous is None or self._transaction_resolution_rank(row) >= self._transaction_resolution_rank(previous):
                by_id[key] = row
        rows = sorted(by_id.values(), key=lambda item: item.get("timestamp") or "", reverse=True)
        rows = rows[: max(1, min(limit, 1000))]
        return {
            "generated_at": self._now(),
            "count": len(rows),
            "summary": self._transactions_summary(rows),
            "transactions": rows,
        }

    def report(self) -> str:
        payload = self.snapshot()
        cards = "\n".join(
            f"""
            <section class="agent-card mode-{self._esc(agent['mode'])} status-{self._esc(agent['status'])}">
              <header><div><small>{self._esc(agent.get('market', 'strategy'))}</small><h2>{self._esc(agent['name'])}</h2></div><strong>{agent['health_score']}</strong></header>
              <div class="grid">
                <span><b>Mode</b>{self._esc(agent['mode']).upper()}</span>
                <span><b>Status</b>{self._esc(agent['status'])}</span>
                <span><b>PnL</b>{agent.get('pnl', 0):,.2f}</span>
                <span><b>Accuracy</b>{agent.get('accuracy', 0):.1f}%</span>
                <span><b>Sharpe</b>{agent.get('sharpe', 0):.2f}</span>
                <span><b>Drawdown</b>{agent.get('max_drawdown', 0):.2f}%</span>
              </div>
              <p>{self._esc(agent.get('last_event') or agent.get('health', 'Sin evento reciente'))}</p>
            </section>
            """
            for agent in payload["agents"]
        )
        alerts = "\n".join(f"<li>{self._esc(item)}</li>" for item in payload["alerts"]) or "<li>Sin alertas críticas.</li>"
        return f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8"><title>QuantLab Validation Report</title>
<style>
body{{font-family:Inter,Arial,sans-serif;background:#080d16;color:#edf3ff;margin:0;padding:28px}}
.top{{display:flex;justify-content:space-between;gap:16px;align-items:flex-end;margin-bottom:22px}}
h1{{margin:0;font-size:28px}} small{{color:#8ea0ba;text-transform:uppercase;letter-spacing:.08em}}
.summary,.agents{{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:14px}}
.kpi,.agent-card{{border:1px solid #1b314b;background:#0e1726;border-radius:8px;padding:16px}}
.kpi strong,.agent-card header strong{{font-size:28px;color:#00e5ff}} .agent-card header{{display:flex;justify-content:space-between;gap:12px}}
.grid{{display:grid;grid-template-columns:repeat(2,1fr);gap:8px;margin:12px 0}} .grid span{{background:#07101d;border-radius:6px;padding:10px}}
.grid b{{display:block;color:#8ea0ba;font-size:11px;text-transform:uppercase}} .mode-live{{border-color:#ff3b5c}} .mode-paper{{border-color:#ffd166}} .mode-observe{{border-color:#00a3ff}}
.status-error{{box-shadow:0 0 0 1px #ff3b5c inset}} ul{{line-height:1.8}}
</style></head><body>
<div class="top"><div><small>QuantLab AI Capital</small><h1>Institutional Validation Report</h1></div><small>{self._esc(payload['generated_at'])}</small></div>
<div class="summary">
<div class="kpi"><small>Health Score</small><br><strong>{payload['summary']['health_score']}</strong></div>
<div class="kpi"><small>Agents</small><br><strong>{payload['summary']['agents_total']}</strong></div>
<div class="kpi"><small>Active</small><br><strong>{payload['summary']['agents_active']}</strong></div>
<div class="kpi"><small>Alerts</small><br><strong>{len(payload['alerts'])}</strong></div>
</div>
<h2>Agents</h2><div class="agents">{cards or '<p>Sin agentes registrados.</p>'}</div>
<h2>Alerts</h2><ul>{alerts}</ul>
</body></html>"""

    def discovery(self) -> dict[str, Any]:
        return {
            "crons": self._discover_crons(),
            "processes": self._discover_processes(),
            "docker": self._docker_ps(),
            "gpu": self._nvidia_smi(),
        }

    def alerts(self, agents: list[dict[str, Any]], infra: dict[str, Any]) -> list[str]:
        alerts: list[str] = []
        for agent in agents:
            if agent["status"] == "error":
                alerts.append(f"{agent['name']}: status error")
            if agent.get("mode") == "live":
                alerts.append(f"{agent['name']}: MODE live requiere vigilancia activa")
            if (agent.get("max_drawdown") or 0) <= -5:
                alerts.append(f"{agent['name']}: drawdown excede umbral")
            if (agent.get("last_age_seconds") or 0) > 300 and agent["status"] in {"running", "waiting"}:
                alerts.append(f"{agent['name']}: sin ejecución reciente")
            if (agent.get("health_score") or 0) < 70:
                alerts.append(f"{agent['name']}: health score bajo")
        if infra["cpu_percent"] >= 90:
            alerts.append("CPU saturada")
        if infra["ram_percent"] >= 90:
            alerts.append("RAM saturada")
        for disk in infra.get("disks", []):
            if disk.get("percent", 0) >= 90:
                alerts.append(f"Disco alto en {disk.get('mountpoint')}")
        for gpu in infra.get("gpu", []):
            if gpu.get("memory_percent", 0) >= 90:
                alerts.append(f"GPU VRAM alta en {gpu.get('name')}")
        return alerts

    def infrastructure(self) -> dict[str, Any]:
        disks = []
        for part in psutil.disk_partitions(all=False):
            try:
                usage = psutil.disk_usage(part.mountpoint)
            except OSError:
                continue
            disks.append({"mountpoint": part.mountpoint, "percent": usage.percent, "free_gb": round(usage.free / 1e9, 2)})
        return {
            "cpu_percent": psutil.cpu_percent(interval=0.1),
            "ram_percent": psutil.virtual_memory().percent,
            "ram_available_gb": round(psutil.virtual_memory().available / 1e9, 2),
            "disks": disks[:6],
            "gpu": self._nvidia_smi(),
            "docker": self._docker_ps(),
            "api_latency_ms": self._api_latency_ms(),
        }

    def write_status(self, payload: dict[str, Any]) -> Path:
        normalized = self.normalize_status(payload)
        path = self.status_dir / f"{normalized['agent']}.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(normalized, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
        return path

    def normalize_status(self, payload: dict[str, Any]) -> dict[str, Any]:
        now = self._now()
        name = str(payload.get("agent") or payload.get("name") or "unknown_agent").strip().replace(" ", "_").lower()
        mode = str(payload.get("mode") or "observe").lower()
        status = str(payload.get("status") or "waiting").lower()
        if mode not in MODES:
            mode = "observe"
        if status not in STATUSES:
            status = "waiting"
        merged = {
            "agent": name,
            "name": payload.get("name") or name,
            "mode": mode,
            "status": status,
            "strategy": payload.get("strategy", "unknown"),
            "market": payload.get("market", "unknown"),
            "symbol": payload.get("symbol", "unknown"),
            "timeframe": payload.get("timeframe", "unknown"),
            "start_time": payload.get("start_time") or now,
            "last_execution": payload.get("last_execution") or now,
            "prediction": payload.get("prediction"),
            "signal": payload.get("signal") or payload.get("prediction"),
            "confidence": self._number(payload.get("confidence"), 0),
            "entry_price": self._number(payload.get("entry_price"), 0),
            "target_price": self._number(payload.get("target_price"), 0),
            "current_price": self._number(payload.get("current_price"), 0),
            "pnl": self._number(payload.get("pnl"), 0),
            "orders": int(self._number(payload.get("orders"), 0)),
            "wins": int(self._number(payload.get("wins"), 0)),
            "losses": int(self._number(payload.get("losses"), 0)),
            "accuracy": self._number(payload.get("accuracy"), 0),
            "sharpe": self._number(payload.get("sharpe"), 0),
            "sortino": self._number(payload.get("sortino"), 0),
            "max_drawdown": self._number(payload.get("max_drawdown"), 0),
            "var": self._number(payload.get("var"), 0),
            "cvar": self._number(payload.get("cvar"), 0),
            "exposure": self._number(payload.get("exposure"), 0),
            "gpu": bool(payload.get("gpu", False)),
            "model": payload.get("model", "not_configured"),
            "health": payload.get("health", "OK"),
            "rules": payload.get("rules") or {},
            "events": payload.get("events") or [],
            "log_path": payload.get("log_path"),
            "transactions": payload.get("transactions") or [],
            "updated_at": now,
        }
        merged["health_score"] = int(payload.get("health_score") or self._health_score(merged))
        return merged

    def _agents(self) -> list[dict[str, Any]]:
        statuses = [self.normalize_status(item) for item in self._status_files()]
        if not any(item["agent"] == "paper_trading" for item in statuses):
            paper = self._paper_trading_status()
            if paper:
                statuses.append(self.normalize_status(paper))
        for status in statuses:
            last = status.get("last_execution")
            status["last_age_seconds"] = self._age_seconds(last)
            if status["last_age_seconds"] is not None and status["last_age_seconds"] > 300 and status["status"] == "running":
                status["status"] = "waiting"
                status["health_score"] = min(status["health_score"], 72)
            status["uptime"] = self._human_age(self._age_seconds(status.get("start_time")))
            status["last_event"] = self._last_event(status)
        return sorted(statuses, key=lambda item: item.get("agent", ""))

    def _status_files(self) -> list[dict[str, Any]]:
        files = sorted(self.status_dir.glob("*.json"))
        data = []
        for path in files:
            try:
                data.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                continue
        return data

    def _paper_trading_status(self) -> dict[str, Any] | None:
        root = Path(settings.artifact_root) / "paper_trading"
        files = sorted(root.glob("*.jsonl")) if root.exists() else []
        if not files:
            return None
        records = self._jsonl_tail(files[-1], 10)
        if not records:
            return None
        latest = records[-1]
        orders = latest.get("orders") or []
        wins = sum(1 for item in orders if item.get("paper"))
        return {
            "agent": "paper_trading",
            "mode": latest.get("mode", "paper"),
            "status": "running" if not latest.get("errors") else "error",
            "strategy": "Universal Paper Trading Runner",
            "market": "Polymarket/MEXC",
            "symbol": "multi",
            "timeframe": "1m cron",
            "start_time": records[0].get("created_at"),
            "last_execution": latest.get("created_at"),
            "prediction": self._paper_prediction(orders),
            "confidence": self._max_probability(orders),
            "pnl": sum(float(item.get("pnl", 0) or 0) for item in orders),
            "orders": len(orders),
            "wins": wins,
            "losses": max(0, len(orders) - wins),
            "accuracy": self._accuracy(records),
            "sharpe": 0,
            "max_drawdown": 0,
            "var": 0,
            "cvar": 0,
            "exposure": sum(float(item.get("stake_usdt", 0) or 0) for item in orders),
            "gpu": True,
            "model": "Prophet + technical filters",
            "health": "OK" if not latest.get("errors") else "ERROR",
            "events": self._paper_events(records[-6:]),
            "log_path": str(root / "systemd.log"),
            "transactions": self._transactions_from_paper_records(records[-4:]),
            "rules": latest.get("rules") or {},
        }

    def _transactions_from_status(self, status: dict[str, Any]) -> list[dict[str, Any]]:
        agent = str(status.get("agent") or status.get("name") or "unknown_agent")
        defaults = {
            "agent": agent,
            "mode": status.get("mode") or "observe",
            "strategy": status.get("strategy"),
            "market": status.get("market"),
            "symbol": status.get("symbol"),
        }
        transactions = status.get("transactions") or []
        if not isinstance(transactions, list):
            return []
        rows = []
        for item in transactions:
            if not isinstance(item, dict):
                continue
            if str(item.get("venue") or "").lower() == "polymarket" and str(item.get("side") or "").upper() == "NONE":
                candidates = (item.get("indicators") or {}).get("candidates") or []
                expanded = False
                for candidate in candidates:
                    if not isinstance(candidate, dict):
                        continue
                    interval = candidate.get("interval")
                    window = candidate.get("window_et")
                    if not interval or not window:
                        continue
                    rows.append(self._normalize_transaction({
                        **item,
                        "id": f"{item.get('id')}-candidate-{interval}-{window}",
                        "interval": interval,
                        "window": window,
                        "confidence": candidate.get("probability") or candidate.get("confidence") or item.get("confidence"),
                        "risk": ', '.join(candidate.get("reasons") or []) or item.get("risk"),
                        "indicators": {
                            "candidate": candidate,
                            "filters": (item.get("indicators") or {}).get("filters") or {},
                            "price_to_beat_reference": candidate.get("price_to_beat_reference"),
                            "current_price_reference": candidate.get("current_price_reference"),
                            "forecast_price_at_close": candidate.get("forecast_price_at_close"),
                        },
                    }, defaults))
                    expanded = True
                if expanded:
                    continue
            rows.append(self._normalize_transaction(item, defaults))
        return rows

    def _paper_trading_transactions(self, limit: int) -> list[dict[str, Any]]:
        root = Path(settings.artifact_root) / "paper_trading"
        files = sorted(root.glob("*.jsonl")) if root.exists() else []
        if not files:
            return []
        records = self._jsonl_tail(files[-1], min(max(limit, 20), 300))
        return self._transactions_from_paper_records(records)

    def _transactions_from_paper_records(self, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        rows = []
        for record in records:
            order_by_tx_id = {
                f"{record.get('cycle_id', record.get('created_at'))}-{idx}": order
                for idx, order in enumerate(record.get("orders") or [])
                if isinstance(order, dict)
            }
            defaults = {
                "agent": "paper_trading",
                "mode": record.get("mode", "paper"),
                "strategy": "Universal Paper Trading Runner",
            }
            timestamp = record.get("created_at")
            for tx in record.get("transactions") or []:
                if not isinstance(tx, dict):
                    continue
                linked_order = order_by_tx_id.get(str(tx.get("id") or ""))
                if linked_order:
                    merged_indicators = dict(tx.get("indicators") or {})
                    if linked_order.get("execution_error"):
                        merged_indicators["execution_error"] = linked_order.get("execution_error")
                    tx = {
                        **tx,
                        "status": linked_order.get("transaction_status") or tx.get("status"),
                        "execution": linked_order.get("execution") or tx.get("execution"),
                        "execution_error": linked_order.get("execution_error") or tx.get("execution_error"),
                        "indicators": merged_indicators,
                    }
                if str(tx.get("venue") or "").lower() == "polymarket" and str(tx.get("side") or "").upper() == "NONE":
                    candidates = (tx.get("indicators") or {}).get("candidates") or []
                    expanded = False
                    for candidate in candidates:
                        if not isinstance(candidate, dict):
                            continue
                        interval = candidate.get("interval")
                        window = candidate.get("window_et")
                        if not interval or not window:
                            continue
                        rows.append(self._normalize_transaction({
                            **tx,
                            "id": f"{tx.get('id')}-candidate-{interval}-{window}",
                            "interval": interval,
                            "window": window,
                            "confidence": candidate.get("probability") or candidate.get("confidence") or tx.get("confidence"),
                            "risk": ', '.join(candidate.get("reasons") or []) or tx.get("risk"),
                            "indicators": {
                                "candidate": candidate,
                                "filters": (tx.get("indicators") or {}).get("filters") or {},
                                "price_to_beat_reference": candidate.get("price_to_beat_reference"),
                                "current_price_reference": candidate.get("current_price_reference"),
                                "forecast_price_at_close": candidate.get("forecast_price_at_close"),
                            },
                        }, defaults))
                        expanded = True
                    if expanded:
                        continue
                rows.append(self._normalize_transaction(tx, defaults))
            for idx, order in enumerate(record.get("orders") or []):
                if any(tx.get("id") == f"{record.get('cycle_id', timestamp)}-{idx}" for tx in rows):
                    continue
                market = order.get("market") or order.get("venue") or "unknown"
                symbol = order.get("symbol") or ("BTC" if "BTC" in str(market).upper() else "multi")
                rows.append(
                    self._normalize_transaction(
                        {
                            "id": f"{record.get('cycle_id', timestamp)}-{idx}",
                            "timestamp": timestamp,
                            "venue": order.get("venue"),
                            "market": market,
                            "symbol": symbol,
                            "side": order.get("side") or order.get("signal") or order.get("preferred_side"),
                            "status": "accepted" if not record.get("errors") else "error",
                            "price": order.get("price"),
                            "stake_usdt": order.get("stake_usdt"),
                            "confidence": order.get("probability"),
                            "kelly": order.get("fractional_kelly") or order.get("full_kelly"),
                            "pnl": order.get("pnl", 0),
                            "execution": order.get("execution") or "simulated_only",
                            "risk": order.get("risk") or order.get("reason"),
                            "interval": order.get("interval"),
                            "window": order.get("window_et"),
                            "indicators": {
                                "price_to_beat_reference": order.get("price_to_beat_reference"),
                                "current_price_reference": order.get("current_price_reference"),
                                "forecast_price_at_close": order.get("forecast_price_at_close"),
                                "candidate": order.get("candidate"),
                            },
                        },
                        defaults,
                    )
                )
            for idx, observation in enumerate(record.get("observations") or []):
                if not isinstance(observation, dict):
                    continue
                if str(observation.get("venue") or "").lower() != "polymarket":
                    continue
                candidate = observation.get("candidate") or {}
                interval = observation.get("interval") or candidate.get("interval")
                window = observation.get("window_et") or candidate.get("window_et")
                if not interval and not window:
                    continue
                rows.append(
                    self._normalize_transaction(
                        {
                            "id": f"{record.get('cycle_id', timestamp)}-poly-observation-{interval}-{window}",
                            "timestamp": timestamp,
                            "venue": "polymarket",
                            "market": observation.get("market") or "BTC Up/Down",
                            "symbol": "BTC",
                            "side": "NONE",
                            "status": "no_trade",
                            "price": 0,
                            "stake_usdt": 0,
                            "confidence": observation.get("probability") or candidate.get("probability") or candidate.get("confidence"),
                            "kelly": 0,
                            "pnl": 0,
                            "execution": observation.get("execution") or "simulated_only",
                            "risk": observation.get("reason"),
                            "interval": interval,
                            "window": window,
                            "indicators": {
                                "candidate": candidate,
                                "filters": observation.get("filters") or {},
                                "price_to_beat_reference": observation.get("price_to_beat_reference") or candidate.get("price_to_beat_reference"),
                                "current_price_reference": observation.get("current_price_reference") or candidate.get("current_price_reference"),
                                "forecast_price_at_close": observation.get("forecast_price_at_close") or candidate.get("forecast_price_at_close"),
                            },
                        },
                        defaults,
                    )
                )
        return rows

    def _normalize_transaction(self, tx: dict[str, Any], defaults: dict[str, Any]) -> dict[str, Any]:
        confidence = self._number(tx.get("confidence"), 0)
        if confidence <= 1 and confidence > 0:
            confidence *= 100
        normalized = {
            "id": str(tx.get("id") or tx.get("transaction_id") or f"{defaults.get('agent')}-{tx.get('timestamp', self._now())}"),
            "timestamp": tx.get("timestamp") or tx.get("created_at") or tx.get("time") or self._now(),
            "agent": tx.get("agent") or defaults.get("agent") or "unknown_agent",
            "mode": str(tx.get("mode") or defaults.get("mode") or "observe").lower(),
            "strategy": tx.get("strategy") or defaults.get("strategy") or "unknown",
            "venue": tx.get("venue") or tx.get("exchange") or defaults.get("market") or "unknown",
            "market": tx.get("market") or defaults.get("market") or tx.get("symbol") or "unknown",
            "symbol": tx.get("symbol") or defaults.get("symbol") or "unknown",
            "side": tx.get("side") or tx.get("signal") or tx.get("prediction") or "NONE",
            "status": tx.get("status") or "observed",
            "price": self._number(tx.get("price"), 0),
            "stake_usdt": self._number(tx.get("stake_usdt") or tx.get("notional") or tx.get("amount"), 0),
            "confidence": round(confidence, 2),
            "kelly": self._number(tx.get("kelly"), 0),
            "pnl": self._number(tx.get("pnl"), 0),
            "execution": tx.get("execution") or ("simulated_only" if str(tx.get("mode") or defaults.get("mode")) == "paper" else "unknown"),
            "execution_error": tx.get("execution_error") or (tx.get("indicators") or {}).get("execution_error"),
            "risk": tx.get("risk") or tx.get("reason") or "",
            "interval": tx.get("interval") or tx.get("timeframe") or "",
            "window": tx.get("window") or tx.get("window_et") or "",
            "indicators": tx.get("indicators") or {},
            "rule_evaluation": tx.get("rule_evaluation") or {},
        }
        return self._resolve_polymarket_transaction(normalized)

    def _resolve_polymarket_transaction(self, tx: dict[str, Any]) -> dict[str, Any]:
        if str(tx.get("venue") or "").lower() != "polymarket":
            return tx
        side = str(tx.get("side") or "").upper()
        if side not in {"UP", "DOWN", "NONE"}:
            return tx
        status = str(tx.get("status") or "").lower()
        if status in {"won", "lost", "error", "rejected"}:
            return tx
        bounds = self._polymarket_window_bounds(tx.get("window"))
        if not bounds:
            return tx
        start_utc, end_utc = bounds
        if datetime.now(UTC) < end_utc + timedelta(seconds=10):
            return tx
        price_to_beat = self._polymarket_price_to_beat(tx)
        interval = str(tx.get("interval") or "").lower()
        if not price_to_beat or interval not in {"5m", "15m"}:
            return tx
        final_price = self._polymarket_final_price(interval, start_utc, end_utc)
        if final_price is None:
            return tx
        winning_side = "UP" if final_price >= price_to_beat else "DOWN"
        indicators = dict(tx.get("indicators") or {})
        indicators.update({
            "final_price_reference": final_price,
            "winning_side": winning_side,
            "actual_close_side": winning_side,
            "resolved_at_et": datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d %H:%M:%S %Z"),
        })
        if side in {"UP", "DOWN"}:
            won = side == winning_side
            tx["status"] = "won" if won else "lost"
            tx["pnl"] = self._polymarket_paper_pnl(tx, won)
            indicators["resolution_status"] = tx["status"]
        else:
            indicators["resolution_status"] = "no_trade"
        tx["indicators"] = indicators
        return tx

    def _polymarket_window_bounds(self, value: Any) -> tuple[datetime, datetime] | None:
        if not value:
            return None
        parts = str(value).split(" - ")
        if len(parts) != 2:
            return None
        tz = ZoneInfo("America/New_York")
        parsed = []
        for part in parts:
            match = re.search(r"(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}:\d{2})", part)
            if not match:
                return None
            try:
                parsed.append(datetime.strptime(" ".join(match.groups()), "%Y-%m-%d %H:%M:%S").replace(tzinfo=tz).astimezone(UTC))
            except ValueError:
                return None
        return parsed[0], parsed[1]

    def _polymarket_price_to_beat(self, tx: dict[str, Any]) -> float:
        indicators = tx.get("indicators") or {}
        candidate = indicators.get("candidate") or {}
        for value in (
            indicators.get("price_to_beat_reference"),
            candidate.get("price_to_beat_reference"),
            tx.get("price_to_beat_reference"),
        ):
            number = self._number(value, 0)
            if number > 0:
                return number
        return 0

    def _polymarket_final_price(self, interval: str, start_utc: datetime, end_utc: datetime) -> float | None:
        target = int(start_utc.timestamp())
        cache_key = (interval, target, int(end_utc.timestamp()))
        if cache_key in self._polymarket_final_price_cache:
            return self._polymarket_final_price_cache[cache_key]
        params = {
            "symbol": "BTC",
            "interval": interval,
            "limit": 60,
            "endTime": int((end_utc + timedelta(minutes=1)).timestamp() * 1000),
        }
        final_price = None
        try:
            response = requests.get(POLYMARKET_CHAINLINK_CANDLES_URL, params=params, timeout=6)
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError):
            self._polymarket_final_price_cache[cache_key] = None
            return None
        candles = payload.get("candles") if isinstance(payload, dict) else payload
        if isinstance(candles, list):
            for candle in candles:
                if not isinstance(candle, dict):
                    continue
                candle_time = int(self._number(candle.get("time"), -1))
                if candle_time == target:
                    close = self._number(candle.get("close"), 0)
                    final_price = close or None
                    break
        self._polymarket_final_price_cache[cache_key] = final_price
        return final_price

    def _polymarket_paper_pnl(self, tx: dict[str, Any], won: bool) -> float:
        stake = self._number(tx.get("stake_usdt"), 0)
        if not won:
            return round(-stake, 2)
        entry = self._number(tx.get("price"), 0)
        if entry <= 0 or entry >= 1:
            return round(stake, 2)
        return round(stake * ((1 / entry) - 1), 2)

    def _transaction_dedupe_key(self, row: dict[str, Any]) -> str:
        if str(row.get("venue") or "").lower() == "polymarket":
            return "|".join([
                "polymarket",
                str(row.get("interval") or "").lower(),
                str(row.get("window") or ""),
                str(row.get("mode") or "").lower(),
            ])
        return str(row.get("id") or row.get("timestamp") or "unknown")

    def _transaction_resolution_rank(self, row: dict[str, Any]) -> int:
        status = str(row.get("status") or "").lower()
        if status in {"won", "lost"}:
            return 4
        if str(row.get("side") or "").upper() in {"UP", "DOWN"} and float(row.get("stake_usdt") or 0) > 0:
            return 3
        if row.get("pnl"):
            return 2
        if status == "no_trade":
            return 1
        return 0

    def _transactions_summary(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        by_mode: dict[str, int] = {}
        by_status: dict[str, int] = {}
        by_agent: dict[str, int] = {}
        for row in rows:
            by_mode[row["mode"]] = by_mode.get(row["mode"], 0) + 1
            by_status[row["status"]] = by_status.get(row["status"], 0) + 1
            by_agent[row["agent"]] = by_agent.get(row["agent"], 0) + 1
        return {
            "total": len(rows),
            "exposure_usdt": round(sum(row.get("stake_usdt", 0) for row in rows), 2),
            "pnl": round(sum(row.get("pnl", 0) for row in rows), 2),
            "by_mode": by_mode,
            "by_status": by_status,
            "by_agent": by_agent,
        }

    def _summary(self, agents: list[dict[str, Any]], infra: dict[str, Any]) -> dict[str, Any]:
        scores = [agent.get("health_score", 0) for agent in agents]
        health_score = int(sum(scores) / len(scores)) if scores else 100
        return {
            "agents_total": len(agents),
            "agents_active": sum(1 for agent in agents if agent["status"] in {"running", "waiting"}),
            "agents_error": sum(1 for agent in agents if agent["status"] == "error"),
            "paper": sum(1 for agent in agents if agent["mode"] == "paper"),
            "observe": sum(1 for agent in agents if agent["mode"] == "observe"),
            "live": sum(1 for agent in agents if agent["mode"] == "live"),
            "total_pnl": round(sum(agent.get("pnl", 0) for agent in agents), 2),
            "health_score": min(health_score, 88 if infra.get("ram_percent", 0) > 85 else 100),
        }

    def _health_score(self, status: dict[str, Any]) -> int:
        score = 100
        if status["status"] == "error":
            score -= 35
        if status["status"] in {"stopped"}:
            score -= 25
        if status["mode"] == "live":
            score -= 5
        if status["confidence"] and status["confidence"] < 50:
            score -= 8
        if status["max_drawdown"] <= -5:
            score -= 15
        if status["orders"] and status["accuracy"] < 45:
            score -= 12
        return max(0, min(100, score))

    def _discover_crons(self) -> list[dict[str, Any]]:
        paths = [Path("/etc/crontab"), Path("/etc/cron.d")]
        rows: list[dict[str, Any]] = []
        for path in paths:
            if path.is_file():
                rows.extend(self._cron_lines(path))
            elif path.is_dir():
                for item in sorted(path.iterdir()):
                    if item.is_file():
                        rows.extend(self._cron_lines(item))
        crontab = self._run(["crontab", "-l"], timeout=2)
        for line in crontab.splitlines():
            clean = line.strip()
            if clean and not clean.startswith("#"):
                rows.append({"source": "crontab -l", "command": clean})
        return rows[:80]

    def _discover_processes(self) -> list[dict[str, Any]]:
        rows = []
        for proc in psutil.process_iter(["pid", "name", "cmdline", "cpu_percent", "memory_percent", "status"]):
            try:
                cmd = " ".join(proc.info.get("cmdline") or [])
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
            text = f"{proc.info.get('name', '')} {cmd}".lower()
            if any(token in text for token in ("python", "cron", "gunicorn", "websocket", "paper_trading", "polymarket", "mexc")):
                rows.append({
                    "pid": proc.info["pid"],
                    "name": proc.info.get("name"),
                    "cmd": cmd[:220],
                    "status": proc.info.get("status"),
                    "cpu": proc.info.get("cpu_percent"),
                    "memory": round(proc.info.get("memory_percent") or 0, 2),
                })
        return rows[:80]

    def _docker_ps(self) -> list[dict[str, Any]]:
        text = self._run(["docker", "ps", "--format", "{{.Names}}|{{.Status}}|{{.Image}}"], timeout=3)
        rows = []
        for line in text.splitlines():
            parts = line.split("|")
            if len(parts) == 3:
                rows.append({"name": parts[0], "status": parts[1], "image": parts[2]})
        return rows

    def _nvidia_smi(self) -> list[dict[str, Any]]:
        query = "name,memory.used,memory.total,utilization.gpu,temperature.gpu"
        text = self._run(["nvidia-smi", f"--query-gpu={query}", "--format=csv,noheader,nounits"], timeout=3)
        rows = []
        for line in text.splitlines():
            parts = [part.strip() for part in line.split(",")]
            if len(parts) < 5:
                continue
            used = self._number(parts[1], 0)
            total = self._number(parts[2], 0)
            rows.append({
                "name": parts[0],
                "memory_used_mb": used,
                "memory_total_mb": total,
                "memory_percent": round((used / total) * 100, 2) if total else 0,
                "utilization_percent": self._number(parts[3], 0),
                "temperature_c": self._number(parts[4], 0),
            })
        return rows

    def _api_latency_ms(self) -> int | None:
        start = datetime.now(UTC)
        try:
            psutil.cpu_percent(interval=0)
        except Exception:
            return None
        return round((datetime.now(UTC) - start).total_seconds() * 1000)

    def _cron_lines(self, path: Path) -> list[dict[str, Any]]:
        rows = []
        try:
            lines = path.read_text(errors="replace").splitlines()
        except OSError:
            return rows
        for line in lines:
            clean = line.strip()
            if clean and not clean.startswith("#"):
                rows.append({"source": str(path), "command": clean})
        return rows

    def _run(self, command: list[str], timeout: int = 3) -> str:
        try:
            return subprocess.run(command, text=True, capture_output=True, timeout=timeout, check=False).stdout
        except (OSError, subprocess.TimeoutExpired):
            return ""

    def _jsonl_tail(self, path: Path, limit: int) -> list[dict[str, Any]]:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()[-limit:]
        except OSError:
            return []
        rows = []
        for line in lines:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return rows

    def _tail(self, path: Path, limit: int = 80) -> list[str]:
        try:
            return path.read_text(errors="replace").splitlines()[-limit:]
        except OSError:
            return []

    def _paper_events(self, records: list[dict[str, Any]]) -> list[str]:
        events = []
        for record in records:
            created = record.get("created_at", "")
            orders = len(record.get("orders") or [])
            observations = len(record.get("observations") or [])
            errors = len(record.get("errors") or [])
            events.append(f"{created} | paper cycle | trades {orders} | observations {observations} | errors {errors}")
        return events

    def _paper_prediction(self, orders: list[dict[str, Any]]) -> str:
        if not orders:
            return "NONE"
        return str(orders[0].get("side") or orders[0].get("signal") or "NONE")

    def _max_probability(self, orders: list[dict[str, Any]]) -> float:
        values = [self._number(item.get("probability"), 0) for item in orders]
        value = max(values) if values else 0
        return round(value * 100, 2) if value <= 1 else round(value, 2)

    def _accuracy(self, records: list[dict[str, Any]]) -> float:
        total = sum(len(record.get("orders") or []) for record in records)
        clean = sum(1 for record in records if not record.get("errors"))
        return round((clean / len(records)) * 100, 2) if records else (100.0 if total else 0.0)

    def _last_event(self, status: dict[str, Any]) -> str:
        events = status.get("events") or []
        return str(events[-1]) if events else f"Última ejecución {status.get('last_execution')}"

    def _age_seconds(self, value: str | None) -> int | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return max(0, round((datetime.now(UTC) - parsed.astimezone(UTC)).total_seconds()))

    def _human_age(self, seconds: int | None) -> str:
        if seconds is None:
            return "n/d"
        minutes, sec = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours}h {minutes}m"
        if minutes:
            return f"{minutes}m {sec}s"
        return f"{sec}s"

    def _number(self, value: Any, default: float = 0) -> float:
        try:
            number = float(value)
            return default if math.isnan(number) or math.isinf(number) else number
        except (TypeError, ValueError):
            return default

    def _now(self) -> str:
        return datetime.now(UTC).isoformat().replace("+00:00", "Z")

    def _esc(self, value: Any) -> str:
        return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

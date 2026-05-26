from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from config import settings
from observability.collector import ValidationCollector


EXPORT_DIR = Path(settings.artifact_root) / "paper_trading_table_exports"
STATE_PATH = EXPORT_DIR / "active_24h_state.json"
JSONL_PATH = EXPORT_DIR / "polymarket_table_24h.jsonl"
JSON_PATH = EXPORT_DIR / "polymarket_table_24h.json"


def now_utc() -> datetime:
    return datetime.now(UTC)


def iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def market_interval(tx: dict[str, Any]) -> str:
    raw = str(tx.get("interval") or (tx.get("indicators") or {}).get("candidate", {}).get("interval") or "").lower()
    if "15" in raw:
        return "15m"
    if "5" in raw:
        return "5m"
    return raw or ""


def market_symbol(tx: dict[str, Any]) -> str:
    interval = market_interval(tx)
    return f"BTC-UP-DOWN_{interval}" if interval else "BTC-UP-DOWN"


def price_to_beat(tx: dict[str, Any]) -> float | None:
    indicators = tx.get("indicators") or {}
    candidate = indicators.get("candidate") or {}
    return number(indicators.get("price_to_beat_reference") or candidate.get("price_to_beat_reference") or tx.get("price_to_beat_reference") or tx.get("price"))


def predicted_price(tx: dict[str, Any]) -> float | None:
    indicators = tx.get("indicators") or {}
    candidate = indicators.get("candidate") or {}
    return number(indicators.get("forecast_price_at_close") or candidate.get("forecast_price_at_close") or tx.get("forecast_price_at_close"))


def close_price(tx: dict[str, Any]) -> float | None:
    indicators = tx.get("indicators") or {}
    candidate = indicators.get("candidate") or {}
    return number(indicators.get("final_price_reference") or candidate.get("final_price_reference") or tx.get("final_price_reference"))


def outcome(tx: dict[str, Any]) -> str:
    indicators = tx.get("indicators") or {}
    actual = str(indicators.get("winning_side") or indicators.get("actual_close_side") or "").upper()
    side = str(tx.get("side") or "NONE").upper()
    status = str(tx.get("status") or "").lower()
    pnl = number(tx.get("pnl")) or 0
    if actual:
        if side == "NONE":
            return f"Cerro {actual} · Sin trade"
        return f"Cerro {actual} · {'Acierto' if side == actual else 'Error'}"
    if status == "won":
        return "Acierto"
    if status == "lost":
        return "Error"
    if status == "no_trade" or side == "NONE":
        return "Pendiente / Sin trade"
    if pnl > 0:
        return "Acierto"
    if pnl < 0:
        return "Error"
    return "Pendiente"


def reason(tx: dict[str, Any]) -> str:
    labels = {
        "confidence_below_threshold": "Confianza < 80%",
        "edge_too_small": "Edge/Kelly insuficiente",
        "missing_ask": "Sin ask en book",
        "spread_too_wide": "Spread alto",
        "insufficient_ask_depth": "Profundidad baja",
        "too_close_to_close": "Cierre muy cerca",
        "missing_side": "Sin direccion",
        "duplicate_window_trade": "Trade ya registrado",
        "coordinator_blocked": "Sin evento valido",
        "no_event_passed_filters": "Sin evento valido",
        "kelly_or_stake_zero": "Kelly/stake en cero",
    }
    candidate = (tx.get("indicators") or {}).get("candidate") or {}
    reasons = candidate.get("reasons") or []
    if reasons:
        return " · ".join(labels.get(item, item) for item in reasons)
    raw = tx.get("risk") or ""
    return labels.get(raw, raw)


def normalize(tx: dict[str, Any], collected_at: str) -> dict[str, Any]:
    confidence = number(tx.get("confidence")) or 0
    return {
        "collected_at": collected_at,
        "source_timestamp": tx.get("timestamp"),
        "hora_et": tx.get("window") or ((tx.get("indicators") or {}).get("candidate") or {}).get("window_et") or "",
        "venue": "Polymarket",
        "mercado": market_symbol(tx),
        "interval": market_interval(tx),
        "side": str(tx.get("side") or "NONE").upper(),
        "mode": tx.get("mode") or "paper",
        "status": tx.get("status") or "",
        "precio_a_superar": price_to_beat(tx),
        "precio_predicho": predicted_price(tx),
        "precio_cierre": close_price(tx),
        "stake": number(tx.get("stake_usdt")) or 0,
        "confianza_pct": round(confidence, 4),
        "motivo": reason(tx),
        "acierto_error": outcome(tx),
        "pnl_paper": number(tx.get("pnl")) or 0,
        "row_key": "|".join([
            str(tx.get("timestamp") or ""),
            str(tx.get("window") or ""),
            market_interval(tx),
            str(tx.get("side") or "NONE").upper(),
            str(tx.get("status") or ""),
        ]),
    }


def load_state() -> dict[str, Any]:
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    start = now_utc()
    state = {
        "started_at": iso(start),
        "ends_at": iso(start + timedelta(hours=24)),
        "status": "active",
        "description": "24h Polymarket table export for LSTM dataset",
    }
    STATE_PATH.write_text(json.dumps(state, indent=2))
    return state


def load_existing() -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    if not JSONL_PATH.exists():
        return rows
    for line in JSONL_PATH.read_text().splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        rows[item["row_key"]] = item
    return rows


def main() -> None:
    state = load_state()
    end_at = datetime.fromisoformat(state["ends_at"].replace("Z", "+00:00"))
    current = now_utc()
    if current > end_at:
        state["status"] = "complete"
        state["completed_at"] = iso(current)
        STATE_PATH.write_text(json.dumps(state, indent=2))
        print(json.dumps({"status": "complete", "rows_added": 0, "ends_at": state["ends_at"]}))
        return

    collected_at = iso(current)
    payload = ValidationCollector().transactions(limit=40)
    existing = load_existing()
    added = []
    for tx in payload.get("transactions") or []:
        if str(tx.get("venue") or "").lower() != "polymarket":
            continue
        row = normalize(tx, collected_at)
        if not row["hora_et"] or not row["interval"]:
            continue
        if row["row_key"] in existing:
            continue
        existing[row["row_key"]] = row
        added.append(row)

    if added:
        with JSONL_PATH.open("a") as handle:
            for row in added:
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    ordered = sorted(existing.values(), key=lambda item: (item.get("source_timestamp") or "", item.get("hora_et") or "", item.get("interval") or ""))
    JSON_PATH.write_text(json.dumps({
        "state": state,
        "row_count": len(ordered),
        "rows": ordered,
    }, ensure_ascii=False, indent=2))
    print(json.dumps({"status": "active", "rows_added": len(added), "row_count": len(ordered), "ends_at": state["ends_at"]}))


if __name__ == "__main__":
    main()

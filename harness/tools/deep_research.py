from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import requests
import yfinance as yf

from .base import BaseTool


ARTIFACT_ROOT = Path("storage/artifacts/research")


class DeepResearchTool(BaseTool):
    name = "deep_research"

    def run(
        self,
        objective: str,
        tickers: list[str] | None = None,
        horizon: str = "3mo",
        session_id: str = "default",
        max_tickers: int = 8,
        **_,
    ):
        query = str(objective or "").strip()
        symbols = self._normalize_tickers(tickers or self._extract_tickers(query))[:max_tickers]
        plan = self._build_plan(query, symbols, horizon)
        run_id = self._run_id(query)
        artifact_dir = ARTIFACT_ROOT / self._safe(session_id)
        artifact_dir.mkdir(parents=True, exist_ok=True)
        scratchpad_path = artifact_dir / f"{run_id}.jsonl"
        report_path = artifact_dir / f"{run_id}_report.json"

        events: list[dict[str, Any]] = []
        self._append(scratchpad_path, {"type": "init", "timestamp": self._now(), "objective": query, "plan": plan})

        market_rows = []
        if symbols:
            market_rows = self._market_snapshot(symbols, horizon)
            self._append(
                scratchpad_path,
                {
                    "type": "tool_result",
                    "timestamp": self._now(),
                    "toolName": "market_snapshot",
                    "args": {"tickers": symbols, "horizon": horizon},
                    "result": market_rows,
                },
            )
            events.append({"step": 1, "decision": {"action": "market_snapshot", "tickers": symbols}, "result": {"ok": True, "rows": len(market_rows)}})
        else:
            self._append(
                scratchpad_path,
                {
                    "type": "thinking",
                    "timestamp": self._now(),
                    "content": "No se detectaron tickers explícitos; la investigación queda como marco cualitativo.",
                },
            )

        thesis = self._build_thesis(query, market_rows)
        evidence = self._build_evidence(market_rows)
        risks = self._build_risks(query, market_rows)
        confidence = self._confidence(market_rows)
        recommendation = self._recommendation(market_rows, confidence)

        report = {
            "status": "completed",
            "mode": "deep_research",
            "objective": query,
            "plan": plan,
            "tickers": symbols,
            "horizon": horizon,
            "thesis": thesis,
            "evidence": evidence,
            "risks": risks,
            "confidence": confidence,
            "recommendation": recommendation,
            "artifacts": {
                "scratchpad": str(scratchpad_path),
                "report": str(report_path),
            },
            "generated_at": self._now(),
        }
        report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        self._append(scratchpad_path, {"type": "final", "timestamp": self._now(), "result": report})
        return report

    def _extract_tickers(self, text: str) -> list[str]:
        explicit = re.findall(r"\b[A-Z]{1,6}(?:[/\-]?USDT|[-/]USD)?\b", text.upper())
        blacklist = {
            "DCF", "BTC", "ETH", "SOL", "USD", "USDT", "IA", "AI", "API", "ET", "EDT",
            "NO", "UP", "DOWN", "BUY", "SELL", "MEXC", "RSI", "MACD", "VWAP", 'DEXTER', 'DE', 'DEL', 'DAME', 'TESIS', 'RIESGO', 'RIESGOS', 'EVIDENCIA', 'INVESTIGACION', 'INVESTIGACIÓN', 'PROFUNDA', 'PROFUNDO', 'ANALISIS', 'ANÁLISIS', 'Y', 'O', 'PARA', 'CON',
        }
        symbols = []
        if re.search(r"\bBTC|BITCOIN\b", text, re.I):
            symbols.append("BTC-USD")
        if re.search(r"\bETH|ETHEREUM\b", text, re.I):
            symbols.append("ETH-USD")
        if re.search(r"\bSOL|SOLANA\b", text, re.I):
            symbols.append("SOL-USD")
        for token in explicit:
            normalized = token.replace("/", "").replace("USDT", "-USD")
            if normalized in {"BTC", "ETH", "SOL"}:
                normalized = f"{normalized}-USD"
            if normalized in blacklist or token in blacklist:
                continue
            if normalized not in symbols:
                symbols.append(normalized)
        return symbols

    def _normalize_tickers(self, tickers: list[str]) -> list[str]:
        symbols = []
        for ticker in tickers:
            value = str(ticker or "").upper().strip().replace("/", "")
            if not value:
                continue
            if value.endswith("USDT"):
                value = f"{value[:-4]}-USD"
            if value in {"BTC", "ETH", "SOL"}:
                value = f"{value}-USD"
            if value not in symbols:
                symbols.append(value)
        return symbols

    def _market_snapshot(self, symbols: list[str], horizon: str) -> list[dict[str, Any]]:
        data = yf.download(symbols, period=horizon, auto_adjust=True, progress=False, group_by="ticker", threads=False)
        rows = []
        for symbol in symbols:
            frame = self._symbol_frame(data, symbol, len(symbols) == 1)
            if frame.empty or "Close" not in frame:
                rows.append({"ticker": symbol, "status": "no_data"})
                continue
            close = frame["Close"].dropna() if "Close" in frame else pd.Series(dtype=float)
            if close.empty:
                close = self._fallback_crypto_close(symbol, horizon)
            if close.empty:
                rows.append({"ticker": symbol, "status": "no_data"})
                continue
            returns = close.pct_change().dropna()
            last = float(close.iloc[-1])
            first = float(close.iloc[0])
            change = (last / first - 1) if first else 0
            vol = float(returns.std() * math.sqrt(252)) if not returns.empty else 0
            ma20 = float(close.tail(min(20, len(close))).mean())
            ma50 = float(close.tail(min(50, len(close))).mean())
            high = close.cummax()
            drawdown = ((close / high) - 1).min()
            rows.append({
                "ticker": symbol,
                "status": "ok",
                "last_price": round(last, 6),
                "period_return": round(change, 6),
                "annualized_volatility": round(vol, 6),
                "max_drawdown": round(float(drawdown), 6),
                "ma20": round(ma20, 6),
                "ma50": round(ma50, 6),
                "trend": "bullish" if ma20 >= ma50 and change > 0 else ("bearish" if ma20 < ma50 and change < 0 else "mixed"),
                "observations": int(len(close)),
            })
        return rows

    def _symbol_frame(self, data, symbol: str, single: bool):
        if not isinstance(data, pd.DataFrame):
            return pd.DataFrame()
        try:
            if isinstance(data.columns, pd.MultiIndex):
                return data[symbol]
            return data
        except Exception:
            return pd.DataFrame()

    def _fallback_crypto_close(self, symbol: str, horizon: str) -> pd.Series:
        if not symbol.endswith("-USD"):
            return pd.Series(dtype=float)
        base = symbol.replace("-USD", "")
        if base not in {"BTC", "ETH", "SOL", "XRP", "BNB", "DOGE", "ADA", "AVAX", "LINK", "LTC"}:
            return pd.Series(dtype=float)
        limit = self._horizon_limit(horizon)
        try:
            resp = requests.get(
                "https://api.mexc.com/api/v3/klines",
                params={"symbol": f"{base}USDT", "interval": "1d", "limit": limit},
                timeout=20,
            )
            resp.raise_for_status()
            rows = resp.json()
            values = [float(row[4]) for row in rows if len(row) > 4]
            index = pd.to_datetime([int(row[0]) for row in rows if len(row) > 4], unit="ms", utc=True)
            return pd.Series(values, index=index, dtype=float)
        except Exception:
            return pd.Series(dtype=float)

    def _horizon_limit(self, horizon: str) -> int:
        match = re.match(r"(\d+)(d|mo|y)$", str(horizon or "3mo").lower())
        if not match:
            return 90
        value = int(match.group(1))
        unit = match.group(2)
        if unit == "d":
            return max(7, min(value, 365))
        if unit == "mo":
            return max(30, min(value * 30, 730))
        return max(365, min(value * 365, 1000))

    def _build_plan(self, objective: str, symbols: list[str], horizon: str) -> list[str]:
        return [
            "Descomponer la pregunta en tesis, evidencia, riesgos y decisión.",
            f"Recolectar snapshot de mercado para {', '.join(symbols) if symbols else 'los activos detectados'} en horizonte {horizon}.",
            "Medir retorno, volatilidad, drawdown y tendencia por medias móviles.",
            "Sintetizar una conclusión con confianza y próximos datos a validar.",
        ]

    def _build_thesis(self, objective: str, rows: list[dict[str, Any]]) -> str:
        ok = [r for r in rows if r.get("status") == "ok"]
        if not ok:
            return "No hay datos de mercado suficientes para sostener una tesis cuantitativa; usar como investigación preliminar."
        bullish = [r["ticker"] for r in ok if r.get("trend") == "bullish"]
        bearish = [r["ticker"] for r in ok if r.get("trend") == "bearish"]
        if bullish and not bearish:
            return f"La evidencia favorece momentum constructivo en {', '.join(bullish)} dentro del horizonte analizado."
        if bearish and not bullish:
            return f"La evidencia favorece cautela: {', '.join(bearish)} muestra tendencia deteriorada y riesgo de continuación bajista."
        return "La evidencia es mixta; conviene usar esta salida como mapa de investigación y exigir confirmación con microestructura, liquidez y catalizadores."

    def _build_evidence(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        evidence = []
        for row in rows:
            if row.get("status") != "ok":
                evidence.append({"ticker": row.get("ticker"), "finding": "Sin datos suficientes", "weight": "low"})
                continue
            evidence.append({
                "ticker": row["ticker"],
                "finding": f"Retorno {row['period_return']:.2%}, volatilidad anualizada {row['annualized_volatility']:.2%}, drawdown {row['max_drawdown']:.2%}, tendencia {row['trend']}.",
                "weight": "high" if row.get("observations", 0) >= 50 else "medium",
                "metrics": row,
            })
        return evidence

    def _build_risks(self, objective: str, rows: list[dict[str, Any]]) -> list[str]:
        risks = [
            "No es recomendación financiera; requiere validación externa antes de operar.",
            "Los datos históricos no garantizan comportamiento futuro.",
        ]
        if any((r.get("annualized_volatility") or 0) > 0.75 for r in rows):
            risks.append("Volatilidad elevada: reducir tamaño y exigir mayor margen de seguridad.")
        if any((r.get("max_drawdown") or 0) < -0.15 for r in rows):
            risks.append("Drawdown relevante: confirmar soporte/liquidez antes de asumir continuidad.")
        if "polymarket" in objective.lower():
            risks.append("En Polymarket la dirección debe confirmarse con order book, spread, Kelly y segundos al cierre.")
        return risks

    def _confidence(self, rows: list[dict[str, Any]]) -> float:
        ok = [r for r in rows if r.get("status") == "ok"]
        if not ok:
            return 0.35
        complete = sum(1 for r in ok if r.get("observations", 0) >= 50) / max(1, len(ok))
        trend_consistency = max(
            sum(1 for r in ok if r.get("trend") == "bullish"),
            sum(1 for r in ok if r.get("trend") == "bearish"),
            sum(1 for r in ok if r.get("trend") == "mixed"),
        ) / max(1, len(ok))
        return round(min(0.85, 0.45 + 0.25 * complete + 0.15 * trend_consistency), 3)

    def _recommendation(self, rows: list[dict[str, Any]], confidence: float) -> str:
        if confidence < 0.55:
            return "OBSERVE: faltan datos suficientes para una decisión."
        trends = {r.get("trend") for r in rows if r.get("status") == "ok"}
        if trends == {"bullish"}:
            return "BULLISH WATCH: buscar confirmación operativa antes de entrada."
        if trends == {"bearish"}:
            return "DEFENSIVE / SHORT WATCH: evitar largo sin reversión confirmada."
        return "NEUTRAL: priorizar escenarios y catalizadores antes de ejecutar."

    def _append(self, path: Path, payload: dict[str, Any]) -> None:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def _run_id(self, query: str) -> str:
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        digest = hashlib.sha1(query.encode("utf-8")).hexdigest()[:10]
        return f"{stamp}_{digest}"

    def _safe(self, value: str) -> str:
        return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "default"))[:120] or "default"

    def _now(self) -> str:
        return datetime.now(UTC).isoformat().replace("+00:00", "Z")

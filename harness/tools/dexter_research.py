from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests

from .base import BaseTool
from .deep_research import DeepResearchTool


class DexterResearchTool(BaseTool):
    name = "dexter_research"

    def run(
        self,
        objective: str,
        tickers: list[str] | None = None,
        horizon: str = "6mo",
        session_id: str = "default",
        timeout: int = 90,
        **kwargs,
    ) -> dict[str, Any]:
        objective = str(objective or "").strip()
        if not objective:
            raise ValueError("objective is required")
        mode = "fallback_deep_research"
        external = self._try_external_dexter(objective, tickers or [], horizon, session_id, timeout)
        if external is not None:
            report = external
            mode = "external_dexter"
        else:
            report = DeepResearchTool().run(
                objective=objective,
                tickers=tickers or [],
                horizon=horizon,
                session_id=f"dexter_{session_id}",
                **kwargs,
            )
        artifact = self._write_coordination_artifact(objective, tickers or [], horizon, session_id, mode, report)
        artifacts = dict(report.get("artifacts") or {}) if isinstance(report, dict) else {}
        artifacts["coordination"] = artifact
        return {
            "status": "completed",
            "agent": "dexter",
            "mode": mode,
            "objective": objective,
            "tickers": tickers or [],
            "horizon": horizon,
            "report": report,
            "thesis": report.get("thesis") if isinstance(report, dict) else None,
            "evidence": report.get("evidence") if isinstance(report, dict) else [],
            "risks": report.get("risks") if isinstance(report, dict) else [],
            "confidence": report.get("confidence") if isinstance(report, dict) else None,
            "recommendation": report.get("recommendation") if isinstance(report, dict) else None,
            "artifacts": artifacts,
            "execution_allowed": False,
            "coordination_contract": {
                "role": "research_only",
                "may_execute_orders": False,
                "handoff_target": "polymrkt/execution",
                "handoff_requires_validation": True,
            },
            "generated_at": self._now(),
        }

    def _try_external_dexter(self, objective: str, tickers: list[str], horizon: str, session_id: str, timeout: int) -> dict[str, Any] | None:
        url = (os.getenv("DEXTER_HTTP_URL") or "").strip().rstrip("/")
        if not url:
            return None
        payload = {
            "objective": objective,
            "tickers": tickers,
            "horizon": horizon,
            "session_id": session_id,
        }
        try:
            response = requests.post(f"{url}/research", json=payload, timeout=timeout)
            response.raise_for_status()
            data = response.json()
            if isinstance(data, dict):
                return data
        except Exception:
            return None
        return None

    def _write_coordination_artifact(self, objective: str, tickers: list[str], horizon: str, session_id: str, mode: str, report: dict[str, Any]) -> str:
        root = Path("storage/artifacts/dexter") / self._safe(session_id)
        root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        path = root / f"{stamp}_coordination.json"
        payload = {
            "agent": "dexter",
            "mode": mode,
            "objective": objective,
            "tickers": tickers,
            "horizon": horizon,
            "report": report,
            "execution_allowed": False,
            "handoff": {"target": "polymrkt", "requires_validation": True},
            "generated_at": self._now(),
        }
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return str(path)

    def _safe(self, value: str) -> str:
        return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in str(value or "default"))[:120] or "default"

    def _now(self) -> str:
        return datetime.now(UTC).isoformat().replace("+00:00", "Z")

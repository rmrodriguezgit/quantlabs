from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from config import settings


def append_trajectory(entry: dict[str, Any]) -> str:
    """Persist one harness run as JSONL for debugging and offline evaluation."""
    root = Path(settings.artifact_root) / "trajectories"
    root.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    path = root / f"{now:%Y-%m-%d}.jsonl"
    payload = {
        "timestamp": now.isoformat().replace("+00:00", "Z"),
        **entry,
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
    return str(path)

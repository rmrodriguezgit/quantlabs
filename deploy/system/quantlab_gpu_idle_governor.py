from __future__ import annotations

import json
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


STATE_PATH = Path("/home/quantlab/quantlab-runtime/harness/storage/artifacts/system_dashboard/gpu_idle_governor.json")
PUBLIC_STATE_PATH = Path("/home/quantlab/quantlab-ai-capital/nginx/html/dashboard/system/gpu_idle_governor.json")
IDLE_UTILIZATION_PERCENT = 5.0
ACTIVE_UTILIZATION_PERCENT = 20.0
IDLE_AFTER_CYCLES = 5


def run(cmd: list[str], timeout: float = 8) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout, check=False)
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except FileNotFoundError as exc:
        return 127, "", str(exc)
    except subprocess.TimeoutExpired as exc:
        return 124, exc.stdout or "", exc.stderr or "timeout"


def number(value: Any, default: float = 0) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def parse_power_bounds() -> dict[str, float]:
    code, out, _ = run(["nvidia-smi", "-q", "-d", "POWER"])
    bounds = {"min_w": 60.0, "max_w": 70.0, "default_w": 70.0}
    if code != 0:
        return bounds
    patterns = {
        "min_w": r"Min Power Limit\s+:\s+([0-9.]+) W",
        "max_w": r"Max Power Limit\s+:\s+([0-9.]+) W",
        "default_w": r"Default Power Limit\s+:\s+([0-9.]+) W",
    }
    for key, pattern in patterns.items():
        match = re.search(pattern, out)
        if match:
            bounds[key] = number(match.group(1), bounds[key])
    return bounds


def gpu_snapshot() -> dict[str, Any]:
    code, out, err = run([
        "nvidia-smi",
        "--query-gpu=index,utilization.gpu,memory.used,memory.total,power.draw,power.limit,temperature.gpu",
        "--format=csv,noheader,nounits",
    ])
    if code != 0:
        return {"ok": False, "error": err or out}
    parts = [part.strip() for part in out.splitlines()[0].split(",")]
    apps_code, apps_out, _ = run([
        "nvidia-smi",
        "--query-compute-apps=pid,process_name,used_memory",
        "--format=csv,noheader,nounits",
    ])
    apps = []
    if apps_code == 0:
        for line in apps_out.splitlines():
            cols = [col.strip() for col in line.split(",")]
            if len(cols) >= 3:
                apps.append({"pid": cols[0], "name": cols[1], "used_memory_mb": number(cols[2])})
    return {
        "ok": True,
        "index": int(number(parts[0])),
        "utilization_percent": number(parts[1]),
        "memory_used_mb": number(parts[2]),
        "memory_total_mb": number(parts[3]),
        "power_draw_w": number(parts[4]),
        "power_limit_w": number(parts[5]),
        "temperature_c": number(parts[6]),
        "apps": apps,
    }


def load_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {"idle_cycles": 0, "mode": "active", "last_action": "initialized"}
    try:
        return json.loads(STATE_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return {"idle_cycles": 0, "mode": "active", "last_action": "state_reset"}


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    PUBLIC_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(state, indent=2, ensure_ascii=False)
    STATE_PATH.write_text(text)
    PUBLIC_STATE_PATH.write_text(text)


def set_power_limit(watts: float) -> tuple[bool, str]:
    run(["nvidia-smi", "-pm", "1"])
    code, out, err = run(["nvidia-smi", "-pl", str(int(round(watts)))])
    return code == 0, err or out or f"power_limit={watts}"


def main() -> None:
    current = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    state = load_state()
    bounds = parse_power_bounds()
    idle_limit = bounds["min_w"]
    active_limit = bounds["default_w"]
    snap = gpu_snapshot()
    if not snap.get("ok"):
        state.update({"checked_at": current, "status": "error", "error": snap.get("error")})
        save_state(state)
        print(json.dumps({"status": "error", "error": snap.get("error")}))
        return

    utilization = float(snap["utilization_percent"])
    is_idle = utilization <= IDLE_UTILIZATION_PERCENT
    idle_cycles = int(state.get("idle_cycles") or 0)
    idle_cycles = idle_cycles + 1 if is_idle else 0

    mode = state.get("mode") or "active"
    action = "none"
    message = ""
    target_limit = active_limit

    if utilization >= ACTIVE_UTILIZATION_PERCENT:
        target_limit = active_limit
        if abs(float(snap["power_limit_w"]) - target_limit) > 0.5:
            ok, message = set_power_limit(target_limit)
            action = "restore_active_limit" if ok else "restore_failed"
            if ok:
                snap = gpu_snapshot()
        mode = "active"
    elif idle_cycles >= IDLE_AFTER_CYCLES:
        target_limit = idle_limit
        if abs(float(snap["power_limit_w"]) - target_limit) > 0.5:
            ok, message = set_power_limit(target_limit)
            action = "apply_idle_limit" if ok else "idle_limit_failed"
            if ok:
                snap = gpu_snapshot()
        mode = "idle"

    state.update({
        "checked_at": current,
        "status": "ok",
        "mode": mode,
        "idle_cycles": idle_cycles,
        "idle_after_cycles": IDLE_AFTER_CYCLES,
        "idle_utilization_percent": IDLE_UTILIZATION_PERCENT,
        "active_utilization_percent": ACTIVE_UTILIZATION_PERCENT,
        "idle_power_limit_w": idle_limit,
        "active_power_limit_w": active_limit,
        "target_power_limit_w": target_limit,
        "last_action": action,
        "message": message,
        "gpu": snap,
    })
    save_state(state)
    print(json.dumps({"status": "ok", "mode": mode, "idle_cycles": idle_cycles, "action": action, "target_power_limit_w": target_limit}))


if __name__ == "__main__":
    main()

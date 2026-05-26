from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


OUTPUT_PATH = Path("/home/quantlab/quantlab-ai-capital/nginx/html/dashboard/system/status.json")
GPU_IDLE_GOVERNOR_PATH = Path("/home/quantlab/quantlab-runtime/harness/storage/artifacts/system_dashboard/gpu_idle_governor.json")
EXPECTED_UPS = {
    "brand": "Dahua",
    "model": "No Break UPS Dahua 1500VA 900W Regulador Con Bateria Respaldo",
    "expected_driver": "NUT usbhid-ups si se presenta como USB HID",
}


def run(cmd: list[str], timeout: float = 5) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout, check=False)
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except FileNotFoundError as exc:
        return 127, "", str(exc)
    except subprocess.TimeoutExpired as exc:
        return 124, exc.stdout or "", exc.stderr or "timeout"


def lines(value: str) -> list[str]:
    return [line.strip() for line in value.splitlines() if line.strip()]


def read_text(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="ignore").strip()
    except OSError:
        return ""


def bytes_to_gb(value: float) -> float:
    return round(value / 1024 / 1024, 2)


def cpu_times() -> list[int]:
    raw = read_text("/proc/stat").splitlines()[0].split()[1:]
    return [int(item) for item in raw]


def cpu_percent() -> float:
    first = cpu_times()
    time.sleep(0.2)
    second = cpu_times()
    idle_a = first[3] + first[4]
    idle_b = second[3] + second[4]
    total_a = sum(first)
    total_b = sum(second)
    total_delta = max(1, total_b - total_a)
    idle_delta = idle_b - idle_a
    return round((1 - idle_delta / total_delta) * 100, 1)


def cpu_info() -> dict[str, Any]:
    model = "CPU"
    for line in read_text("/proc/cpuinfo").splitlines():
        if line.lower().startswith("model name"):
            model = line.split(":", 1)[1].strip()
            break
    temps = []
    for zone in Path("/sys/class/thermal").glob("thermal_zone*/temp"):
        try:
            value = float(zone.read_text().strip()) / 1000
            if 0 < value < 130:
                temps.append(round(value, 1))
        except (OSError, ValueError):
            pass
    load1, load5, load15 = os.getloadavg()
    return {
        "model": model,
        "cores": os.cpu_count(),
        "utilization_percent": cpu_percent(),
        "load": {"1m": round(load1, 2), "5m": round(load5, 2), "15m": round(load15, 2)},
        "temperature_c": max(temps) if temps else None,
    }


def memory_info() -> dict[str, Any]:
    data: dict[str, float] = {}
    for line in read_text("/proc/meminfo").splitlines():
        key, value = line.split(":", 1)
        data[key] = float(value.strip().split()[0])
    total = data.get("MemTotal", 0)
    available = data.get("MemAvailable", 0)
    used = max(0, total - available)
    swap_total = data.get("SwapTotal", 0)
    swap_free = data.get("SwapFree", 0)
    swap_used = max(0, swap_total - swap_free)
    return {
        "total_gb": bytes_to_gb(total),
        "used_gb": bytes_to_gb(used),
        "available_gb": bytes_to_gb(available),
        "used_percent": round((used / total) * 100, 1) if total else 0,
        "swap_total_gb": bytes_to_gb(swap_total),
        "swap_used_gb": bytes_to_gb(swap_used),
    }


def gpu_info() -> list[dict[str, Any]]:
    code, out, err = run([
        "nvidia-smi",
        "--query-gpu=index,name,driver_version,temperature.gpu,utilization.gpu,memory.used,memory.total,power.draw,power.limit",
        "--format=csv,noheader,nounits",
    ], timeout=8)
    if code != 0:
        return [{"status": "unavailable", "error": err or out or "nvidia-smi no disponible"}]
    rows = []
    for line in lines(out):
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 9:
            continue
        used = float(parts[5])
        total = float(parts[6])
        rows.append({
            "index": int(parts[0]),
            "name": parts[1],
            "driver": parts[2],
            "temperature_c": float(parts[3]),
            "utilization_percent": float(parts[4]),
            "memory_used_mb": used,
            "memory_total_mb": total,
            "memory_used_percent": round((used / total) * 100, 1) if total else 0,
            "power_draw_w": float(parts[7]),
            "power_limit_w": float(parts[8]),
            "status": "ok",
        })
    return rows


def disk_info() -> list[dict[str, Any]]:
    mounts = ["/", "/home", "/home/quantlab/quantlab-runtime"]
    seen = set()
    disks = []
    for mount in mounts:
        if mount in seen or not Path(mount).exists():
            continue
        seen.add(mount)
        usage = shutil.disk_usage(mount)
        code, out, _ = run(["df", "-PT", mount], timeout=3)
        fs_type = ""
        device = ""
        if code == 0 and len(out.splitlines()) > 1:
            cols = out.splitlines()[1].split()
            if len(cols) >= 2:
                device, fs_type = cols[0], cols[1]
        disks.append({
            "mount": mount,
            "device": device,
            "type": fs_type,
            "total_gb": round(usage.total / 1024**3, 1),
            "used_gb": round(usage.used / 1024**3, 1),
            "free_gb": round(usage.free / 1024**3, 1),
            "used_percent": round((usage.used / usage.total) * 100, 1) if usage.total else 0,
            "status": "ok" if usage.free / usage.total > 0.15 else "warning",
        })
    return disks


def docker_container(name: str) -> dict[str, Any]:
    code, out, err = run(["docker", "inspect", name], timeout=5)
    if code != 0:
        return {"name": name, "status": "missing", "error": err or out}
    payload = json.loads(out)[0]
    state = payload.get("State", {})
    return {
        "name": name,
        "image": payload.get("Config", {}).get("Image"),
        "status": state.get("Status"),
        "running": bool(state.get("Running")),
        "started_at": state.get("StartedAt"),
        "health": (state.get("Health") or {}).get("Status"),
        "restart_count": payload.get("RestartCount"),
    }


def bitcoind_info() -> dict[str, Any]:
    container = docker_container("bitcoind")
    result: dict[str, Any] = {"container": container}
    if not container.get("running"):
        result["status"] = "offline"
        return result
    code, out, err = run(["docker", "exec", "bitcoind", "bitcoin-cli", "-getinfo"], timeout=10)
    if code != 0:
        result.update({"status": "warning", "error": err or out})
        return result
    info: dict[str, Any] = {}
    for line in lines(out):
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        info[key.strip().lower().replace(" ", "_")] = value.strip()
    result.update({"status": "ok", "info": info})
    return result


def docker_summary() -> list[dict[str, Any]]:
    names = [
        "quantlab_nginx",
        "quantlab_harness",
        "quantlab_api",
        "quantlab_market_gpu",
        "jupyter_quantlab_gpu",
        "quantlab_llm",
        "quantlab_ollama",
        "bitcoind",
    ]
    return [docker_container(name) for name in names]


def nut_service_state() -> dict[str, Any]:
    services = {}
    for unit in ["nut-server", "nut-monitor", "nut-driver-enumerator.path"]:
        _, active, _ = run(["systemctl", "is-active", unit], timeout=3)
        _, enabled, _ = run(["systemctl", "is-enabled", unit], timeout=3)
        services[unit] = {"active": active or "unknown", "enabled": enabled or "unknown"}
    return services


def ups_info() -> dict[str, Any]:
    tools = {name: shutil.which(name) for name in ["upsc", "upscmd", "upsrw", "nut-scanner"]}
    code, usb_out, _ = run(["lsusb"], timeout=4)
    usb_devices = lines(usb_out) if code == 0 else []
    code, ups_list, _ = run(["upsc", "-l"], timeout=4)
    names = lines(ups_list) if code == 0 else []
    devices = []
    for name in names:
        dev_code, dev_out, dev_err = run(["upsc", name], timeout=5)
        devices.append({"name": name, "status": "ok" if dev_code == 0 else "error", "raw": dev_out, "error": dev_err})
    scan_code, scan_out, scan_err = run(["nut-scanner", "-U"], timeout=6)
    return {
        "expected_device": EXPECTED_UPS,
        "tools": tools,
        "nut_mode": read_text("/etc/nut/nut.conf"),
        "services": nut_service_state(),
        "usb_devices": usb_devices,
        "devices": devices,
        "scanner": {"status": "ok" if scan_code == 0 else "unavailable", "output": scan_out, "error": scan_err},
        "status": "detected" if devices else "not_detected",
        "note": "NUT instalado. El UPS Dahua no aparece por USB todavia; al conectarlo se espera detectarlo como HID USB o configurarlo con el driver correcto.",
    }


def gpu_idle_governor_info() -> dict[str, Any]:
    if not GPU_IDLE_GOVERNOR_PATH.exists():
        return {"status": "not_configured"}
    try:
        return json.loads(GPU_IDLE_GOVERNOR_PATH.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return {"status": "error", "error": str(exc)}


def main() -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "host": {"hostname": platform.node(), "system": platform.platform(), "uptime": read_text("/proc/uptime").split()[0] if read_text("/proc/uptime") else None},
        "gpu": gpu_info(),
        "gpu_idle_governor": gpu_idle_governor_info(),
        "memory": memory_info(),
        "cpu": cpu_info(),
        "disks": disk_info(),
        "bitcoind": bitcoind_info(),
        "docker": docker_summary(),
        "ups": ups_info(),
    }
    tmp = OUTPUT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(OUTPUT_PATH)
    print(json.dumps({"status": "ok", "output": str(OUTPUT_PATH), "generated_at": payload["generated_at"]}))


if __name__ == "__main__":
    main()

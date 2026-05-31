#!/usr/bin/env python3
"""Tiny local control endpoint for the admin GPU toggle.

It only starts/stops the fixed GPU containers used by QuantLab. It listens on a
Unix socket consumed by nginx after the normal admin auth_request check.
"""
from http.server import BaseHTTPRequestHandler
from socketserver import UnixStreamServer
import json
import os
import subprocess
import sys
import time
from pathlib import Path

SOCKET_PATH = os.environ.get("QUANTLAB_GPU_CONTROL_SOCKET", "/run/quantlab/gpu-control.sock")
CONTAINERS = ["quantlab_market_gpu", "quantlab_llm"]
PROJECT_DIR = Path(os.environ.get("QUANTLAB_PROJECT_DIR", "/home/quantlab/quantlab-ai-capital"))
RUNTIME_DIR = Path(os.environ.get("QUANTLAB_RUNTIME_DIR", "/home/quantlab/quantlab-runtime"))
MODELS_DIR = RUNTIME_DIR / "models"
ENV_FILE = PROJECT_DIR / ".env"
COMPOSE_FILE = PROJECT_DIR / "docker-compose.yml"


def run_docker(args, timeout=45):
    return subprocess.run(
        ["docker", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def run_project(args, timeout=120):
    return subprocess.run(
        args,
        cwd=str(PROJECT_DIR),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def reload_nginx():
    test = run_docker(["exec", "quantlab_nginx", "nginx", "-t"], timeout=15)
    if test.returncode != 0:
        return {
            "ok": False,
            "stage": "test",
            "stdout": test.stdout.strip(),
            "stderr": test.stderr.strip(),
        }
    reload_proc = run_docker(["exec", "quantlab_nginx", "nginx", "-s", "reload"], timeout=15)
    return {
        "ok": reload_proc.returncode == 0,
        "stage": "reload",
        "stdout": reload_proc.stdout.strip(),
        "stderr": reload_proc.stderr.strip(),
    }


def model_profile(model):
    lower = model.lower()
    if "qwen2.5-coder-14b" in lower:
        return {"template": "chatml", "ctx": 8192, "gpu_layers": 40, "threads": 6, "agent": "codex4u"}
    if "qwen2.5-14b" in lower:
        return {"template": "chatml", "ctx": 8192, "gpu_layers": 40, "threads": 6, "agent": "coding"}
    if "phi-4" in lower:
        return {"template": "chatml", "ctx": 8192, "gpu_layers": 40, "threads": 6, "agent": "planner"}
    if "nous-hermes" in lower or "mistral" in lower:
        return {"template": "chatml", "ctx": 8192, "gpu_layers": 32, "threads": 6, "agent": "coding"}
    return {"template": "chatml", "ctx": 8192, "gpu_layers": 32, "threads": 6, "agent": "coding"}


def env_values():
    values = {}
    try:
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            if not line or line.lstrip().startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    except OSError:
        return values
    return values


def update_env(updates):
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else []
    seen = set()
    next_lines = []
    for line in lines:
        if "=" not in line or line.lstrip().startswith("#"):
            next_lines.append(line)
            continue
        key = line.split("=", 1)[0].strip()
        if key in updates:
            next_lines.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            next_lines.append(line)
    for key, value in updates.items():
        if key not in seen:
            next_lines.append(f"{key}={value}")
    ENV_FILE.write_text("\n".join(next_lines) + "\n", encoding="utf-8")


def available_models():
    if not MODELS_DIR.exists():
        return []
    return sorted(path.name for path in MODELS_DIR.glob("*.gguf") if not path.is_symlink())


def llm_model_status():
    values = env_values()
    active = values.get("LLM_MODEL") or ""
    models = available_models()
    profile = model_profile(active)
    return {
        "available": True,
        "models": models,
        "active_model": active,
        "loaded": active in models,
        "template": values.get("LLM_CHAT_TEMPLATE") or profile["template"],
        "ctx_size": int(values.get("LLM_CTX_SIZE") or profile["ctx"]),
        "gpu_layers": int(values.get("LLM_GPU_LAYERS") or profile["gpu_layers"]),
        "threads": int(values.get("LLM_THREADS") or profile["threads"]),
        "specialist": values.get("LLM_SPECIALIST") or profile.get("agent") or "",
        "gpu": gpu_status(),
    }


def wait_for_model(model, timeout=180):
    deadline = time.time() + timeout
    last_error = ""
    while time.time() < deadline:
        proc = run_project(
            [
                "docker",
                "exec",
                "quantlab_harness",
                "python3",
                "-c",
                (
                    "import requests,sys;"
                    "r=requests.get('http://llm:8080/v1/models',timeout=5);"
                    "print(r.text);"
                    "sys.exit(0 if r.ok and %r in r.text else 1)"
                ) % model,
            ],
            timeout=12,
        )
        if proc.returncode == 0:
            return {"ready": True, "response": proc.stdout.strip()[:1000]}
        last_error = (proc.stderr or proc.stdout or "").strip()
        time.sleep(3)
    return {"ready": False, "error": last_error or "model did not become ready"}


def switch_llm_model(model):
    models = available_models()
    if model not in models:
        return 400, {"error": "model not found", "models": models}
    profile = model_profile(model)
    update_env(
        {
            "LLM_MODEL": model,
            "LLM_CHAT_TEMPLATE": profile["template"],
            "LLM_CTX_SIZE": str(profile["ctx"]),
            "LLM_GPU_LAYERS": str(profile["gpu_layers"]),
            "LLM_THREADS": str(profile["threads"]),
            "LLM_SPECIALIST": profile.get("agent", "coding"),
        }
    )
    symlink = MODELS_DIR / "current-model.gguf"
    try:
        if symlink.exists() or symlink.is_symlink():
            symlink.unlink()
        symlink.symlink_to(MODELS_DIR / model)
    except OSError:
        pass
    proc = run_project(["docker", "compose", "-f", str(COMPOSE_FILE), "up", "-d", "--no-deps", "--force-recreate", "llm"], timeout=180)
    payload = llm_model_status()
    payload["selected_model"] = model
    payload["profile"] = profile
    payload["result"] = {
        "returncode": proc.returncode,
        "stdout": proc.stdout.strip(),
        "stderr": proc.stderr.strip(),
    }
    if proc.returncode != 0:
        payload["error"] = "docker compose failed"
        return 500, payload
    payload["readiness"] = wait_for_model(model)
    payload["nginx_reload"] = reload_nginx()
    return (200 if payload["readiness"].get("ready") else 202), payload


def gpu_status():
    proc = run_docker(["inspect", *CONTAINERS], timeout=8)
    if proc.returncode != 0:
        return {
            "available": False,
            "error": (proc.stderr or proc.stdout or "docker inspect failed").strip(),
            "containers": [],
            "gpu_enabled": False,
        }
    inspected = json.loads(proc.stdout)
    containers = []
    for item in inspected:
        state = item.get("State") or {}
        config = item.get("Config") or {}
        containers.append({
            "name": (item.get("Name") or "").lstrip("/"),
            "image": config.get("Image") or item.get("Image") or "",
            "status": state.get("Status") or "unknown",
            "running": bool(state.get("Running")),
            "started_at": state.get("StartedAt"),
            "finished_at": state.get("FinishedAt"),
        })
    return {
        "available": True,
        "containers": containers,
        "gpu_enabled": all(container["running"] for container in containers) if containers else False,
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "QuantLabGPUControl/1.0"

    def log_message(self, fmt, *args):
        print(fmt % args, file=sys.stderr)

    def send_json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/admin/llm-model":
            try:
                self.send_json(200, llm_model_status())
            except Exception as exc:
                self.send_json(500, {"error": str(exc), "available": False, "models": []})
            return
        if path != "/admin/gpu":
            self.send_json(404, {"error": "not found"})
            return
        try:
            self.send_json(200, gpu_status())
        except Exception as exc:
            self.send_json(500, {"error": str(exc), "available": False, "containers": []})

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path not in {"/admin/gpu", "/admin/llm-model"}:
            self.send_json(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length") or "0")
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            self.send_json(400, {"error": "invalid JSON"})
            return
        if path == "/admin/llm-model":
            model = str(payload.get("model") or "").strip()
            try:
                status, data = switch_llm_model(model)
                self.send_json(status, data)
            except Exception as exc:
                self.send_json(500, {"error": str(exc), "available": False})
            return
        action = str(payload.get("action") or "").strip().lower()
        if action not in {"start", "stop"}:
            self.send_json(400, {"error": "invalid action"})
            return
        proc = run_docker([action, *CONTAINERS])
        result = {
            "action": action,
            "returncode": proc.returncode,
            "stdout": proc.stdout.strip(),
            "stderr": proc.stderr.strip(),
        }
        status = gpu_status()
        status["result"] = result
        if proc.returncode != 0:
            status["error"] = "docker command failed"
            self.send_json(500, status)
            return
        self.send_json(200, status)


def main():
    os.makedirs(os.path.dirname(SOCKET_PATH), exist_ok=True)
    if os.path.exists(SOCKET_PATH):
        os.unlink(SOCKET_PATH)
    with UnixStreamServer(SOCKET_PATH, Handler) as server:
        os.chmod(SOCKET_PATH, 0o666)
        server.serve_forever()


if __name__ == "__main__":
    main()

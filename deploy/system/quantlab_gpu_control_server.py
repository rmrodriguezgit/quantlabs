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

SOCKET_PATH = os.environ.get("QUANTLAB_GPU_CONTROL_SOCKET", "/run/quantlab/gpu-control.sock")
CONTAINERS = ["quantlab_market_gpu", "quantlab_llm"]


def run_docker(args, timeout=45):
    return subprocess.run(
        ["docker", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


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
        if self.path.split("?", 1)[0] != "/admin/gpu":
            self.send_json(404, {"error": "not found"})
            return
        try:
            self.send_json(200, gpu_status())
        except Exception as exc:
            self.send_json(500, {"error": str(exc), "available": False, "containers": []})

    def do_POST(self):
        if self.path.split("?", 1)[0] != "/admin/gpu":
            self.send_json(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length") or "0")
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            self.send_json(400, {"error": "invalid JSON"})
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

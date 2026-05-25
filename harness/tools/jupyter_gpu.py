from __future__ import annotations

import asyncio
import json
import uuid

import requests
import websockets

from config import settings
from .base import BaseTool


class JupyterGPUTool(BaseTool):
    name = "jupyter_gpu"
    base_url = "http://jupyter_gpu:8888/jupyter-gpu"
    ws_base_url = "ws://jupyter_gpu:8888/jupyter-gpu"

    def run(self, code: str, timeout: int | None = None, **_):
        if not str(code or "").strip():
            raise ValueError("code required")
        max_seconds = min(int(timeout or settings.max_tool_seconds), 900)
        return asyncio.run(self._execute(str(code), max_seconds))

    async def _execute(self, code: str, timeout: int):
        kernel_id, session, xsrf = self._start_kernel()
        try:
            return await asyncio.wait_for(self._run_in_kernel(kernel_id, code, session), timeout=timeout)
        finally:
            self._stop_kernel(kernel_id, session, xsrf)

    def _start_kernel(self) -> tuple[str, requests.Session, str]:
        session = requests.Session()
        session.get(f"{self.base_url}/", timeout=10)
        xsrf = session.cookies.get("_xsrf") or ""
        response = session.post(
            f"{self.base_url}/api/kernels",
            json={"name": "python3"},
            headers={"X-XSRFToken": xsrf},
            timeout=15,
        )
        response.raise_for_status()
        return response.json()["id"], session, xsrf

    def _stop_kernel(self, kernel_id: str, session: requests.Session | None = None, xsrf: str = "") -> None:
        try:
            client = session or requests.Session()
            client.delete(f"{self.base_url}/api/kernels/{kernel_id}", headers={"X-XSRFToken": xsrf}, timeout=10)
        except Exception:
            pass

    async def _run_in_kernel(self, kernel_id: str, code: str, session: requests.Session):
        session_id = str(uuid.uuid4())
        msg_id = str(uuid.uuid4())
        message = {
            "header": {
                "msg_id": msg_id,
                "username": "quantlab-harness",
                "session": session_id,
                "msg_type": "execute_request",
                "version": "5.3",
            },
            "parent_header": {},
            "metadata": {},
            "content": {
                "code": "import os\nos.environ.setdefault('CUDA_VISIBLE_DEVICES','0')\n" + code,
                "silent": False,
                "store_history": False,
                "user_expressions": {},
                "allow_stdin": False,
                "stop_on_error": True,
            },
            "channel": "shell",
            "buffers": [],
        }
        stdout: list[str] = []
        stderr: list[str] = []
        ws_url = f"{self.ws_base_url}/api/kernels/{kernel_id}/channels?session_id={session_id}"
        cookie = "; ".join(f"{key}={value}" for key, value in session.cookies.get_dict().items())
        async with websockets.connect(ws_url, max_size=8_000_000, additional_headers={"Cookie": cookie}) as ws:
            await ws.send(json.dumps(message))
            while True:
                raw = await ws.recv()
                data = json.loads(raw)
                parent = data.get("parent_header") or {}
                if parent.get("msg_id") != msg_id:
                    continue
                msg_type = (data.get("header") or {}).get("msg_type")
                content = data.get("content") or {}
                if msg_type == "stream":
                    if content.get("name") == "stderr":
                        stderr.append(content.get("text", ""))
                    else:
                        stdout.append(content.get("text", ""))
                elif msg_type in {"execute_result", "display_data"}:
                    text = (content.get("data") or {}).get("text/plain")
                    if text:
                        stdout.append(str(text) + "\n")
                elif msg_type == "error":
                    stderr.append("\n".join(content.get("traceback") or [content.get("evalue", "")]))
                elif msg_type == "status" and content.get("execution_state") == "idle":
                    break
        error_text = "".join(stderr)
        return {
            "kernel_id": kernel_id,
            "code": 1 if error_text else 0,
            "stdout": "".join(stdout)[-16000:],
            "stderr": error_text[-16000:],
        }

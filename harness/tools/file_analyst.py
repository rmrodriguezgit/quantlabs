from __future__ import annotations

import json
from pathlib import Path

import requests

from config import settings
from memory.uploads import UploadStore
from policies.security import FilePolicy

from .base import BaseTool


class FileAnalystTool(BaseTool):
    name = "file_analyst"

    def __init__(self):
        self.uploads = UploadStore()
        self.policy = FilePolicy()

    def run(
        self,
        action: str | None = None,
        role: str | None = None,
        user_id: str = "shared",
        file_id: str | None = None,
        path: str | None = None,
        text: str | None = None,
        mode: str = "specialist",
        language: str = "es",
        **_,
    ):
        if role not in {"admin", "teacher", "trader"}:
            raise PermissionError("file_analyst is only allowed for authenticated analyst roles")
        action = action or ("analyze_text" if text else "analyze_file")
        if action == "health":
            return self._get("/health")
        if action == "analyze_text":
            if not text or not text.strip():
                raise ValueError("text required")
            return self._post_json("/analyze/text", {"text": text, "mode": mode, "language": language})
        if action == "analyze_file":
            file_path, filename = self._resolve_file(user_id, file_id, path)
            result = self._post_file(file_path, filename, mode, language)
            return {
                **result,
                "source": {
                    "file_id": file_id,
                    "path": str(file_path),
                    "filename": filename,
                },
            }
        raise ValueError("unsupported file_analyst action")

    def _base_url(self) -> str:
        return str(getattr(settings, "file_analyst_url", "") or "http://file_analyst:8010").rstrip("/")

    def _get(self, path: str):
        response = requests.get(f"{self._base_url()}{path}", timeout=20)
        response.raise_for_status()
        return response.json()

    def _post_json(self, path: str, payload: dict):
        response = requests.post(f"{self._base_url()}{path}", json=payload, timeout=240)
        response.raise_for_status()
        return response.json()

    def _post_file(self, path: Path, filename: str, mode: str, language: str):
        with path.open("rb") as handle:
            response = requests.post(
                f"{self._base_url()}/analyze",
                files={"file": (filename, handle)},
                data={"mode": mode, "language": language},
                timeout=300,
            )
        response.raise_for_status()
        return response.json()

    def _resolve_file(self, user_id: str, file_id: str | None, path: str | None) -> tuple[Path, str]:
        if file_id:
            meta = self.uploads.get(user_id, file_id)
            if not meta:
                raise FileNotFoundError("file_id not found")
            file_path = Path(meta["path"])
            return file_path, meta.get("name") or file_path.name
        if path:
            file_path = self.policy.resolve(path)
            return file_path, file_path.name
        raise ValueError("file_id or path required")


def format_file_analysis(output: dict) -> str:
    observations = output.get("observations") or []
    actions = output.get("action_plan") or []
    lines = [
        "File Analyst completado.",
        "",
        f"Resumen: {output.get('summary', 'sin resumen')}",
        "",
        f"Interpretación: {output.get('interpretation', 'sin interpretación')}",
        "",
        "Observaciones principales:",
    ]
    for item in observations[:8]:
        lines.append(f"- [{item.get('severity','info')}] {item.get('detail','')}")
    lines.extend(["", "Conclusiones:"])
    for item in (output.get("conclusions") or [])[:8]:
        lines.append(f"- {item}")
    lines.extend(["", "Plan de acción:"])
    for item in actions[:8]:
        detail = item.get("action", "")
        responsible = item.get("responsible")
        suffix = f" · Responsable: {responsible}" if responsible else ""
        lines.append(f"{item.get('priority', '-')}. {detail}{suffix}")
    metadata = output.get("metadata") or {}
    lines.extend([
        "",
        f"Motor: {metadata.get('analysis_engine', 'n/d')} · Modelo: {metadata.get('model', 'n/d')} · Palabras: {metadata.get('word_count', 'n/d')}",
    ])
    source = output.get("source") or {}
    if source.get("filename"):
        lines.append(f"Archivo: {source.get('filename')}")
    return "\n".join(lines)

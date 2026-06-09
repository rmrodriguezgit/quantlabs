from __future__ import annotations

from pathlib import Path

from document_intelligence import DocumentIntelligenceSupervisor
from memory.uploads import UploadStore
from policies.security import FilePolicy

from .base import BaseTool


class DocumentIntelligenceTool(BaseTool):
    name = "document_intelligence"

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
        language: str = "spa",
        dry_run: bool = True,
        extraction_prompt: str | None = None,
        **_,
    ):
        if role not in {"admin", "teacher", "trader"}:
            raise PermissionError("document_intelligence requires an authenticated analyst role")
        action = action or "process"
        if action == "rules":
            return self.rules()
        if action == "process":
            file_path, filename = self._resolve_file(user_id, file_id, path)
            result = DocumentIntelligenceSupervisor().process(
                file_path,
                language=language,
                dry_run=dry_run,
                extraction_prompt=extraction_prompt,
            )
            result["source"]["file_id"] = file_id
            result["source"]["filename"] = filename
            return result
        raise ValueError("unsupported document_intelligence action")

    def rules(self) -> dict:
        return {
            "module": "Document Intelligence",
            "mode": "draft_and_review",
            "supported_files": ["pdf", "png", "jpg", "jpeg", "csv", "xls", "xlsx", "txt", "md", "json"],
            "agents": ["IngestAgent", "ExtractorAgent", "AnalysisAgent", "VerificationAgent", "ClientAgent", "EmailDraftAgent", "AuditAgent"],
            "safety": [
                "no automatic email sending",
                "human review required when confidence < 0.85",
                "multiple emails or missing client block automation",
                "optional extraction prompts guide fields only and never authorize actions",
                "all decisions are written to audit JSONL",
            ],
        }

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

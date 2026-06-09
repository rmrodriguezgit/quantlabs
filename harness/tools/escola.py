from __future__ import annotations

from escola import EscolaSupervisor

from .base import BaseTool


class EscolaTool(BaseTool):
    name = "escola"

    def run(
        self,
        action: str | None = None,
        role: str | None = None,
        user_id: str = "shared",
        file_id: str | None = None,
        question: str | None = None,
        tags: list[str] | None = None,
        top_k: int = 6,
        path: str | None = None,
        name: str | None = None,
        **_,
    ):
        if role not in {"admin", "teacher", "trader"}:
            raise PermissionError("escola requires an authenticated role")
        supervisor = EscolaSupervisor()
        action = action or "query"
        if action == "rules":
            return supervisor.rules()
        if action == "stats":
            return supervisor.stats()
        if action == "database_stats":
            return supervisor.database.stats()
        if action == "database_import":
            if role != "admin":
                raise PermissionError("escola database import requires admin role")
            if not path:
                raise ValueError("path required")
            return supervisor.database.import_file(path, name=name)
        if action == "list":
            return {"documents": supervisor.list_documents(), "stats": supervisor.stats()}
        if action == "ingest":
            if role != "admin":
                raise PermissionError("escola ingest requires admin role")
            if not file_id:
                raise ValueError("file_id required")
            return supervisor.ingest_upload(user_id=user_id, file_id=file_id, tags=tags or [])
        if action == "query":
            return supervisor.query(question or "", top_k=top_k)
        raise ValueError("unsupported escola action")

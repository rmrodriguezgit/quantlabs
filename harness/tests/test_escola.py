from io import BytesIO

from werkzeug.datastructures import FileStorage

from escola import EscolaSupervisor
from memory.uploads import UploadStore
from tools.registry import ToolRegistry


def _upload_text(user_id: str, name: str, text: str) -> dict:
    file = FileStorage(stream=BytesIO(text.encode("utf-8")), filename=name, content_type="text/plain")
    return UploadStore().save(user_id, file)


def test_escola_ingests_upload_and_returns_copy_ready_answer(tmp_path, monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "upload_root", str(tmp_path / "uploads"))
    monkeypatch.setattr(settings, "artifact_root", str(tmp_path / "artifacts"))
    meta = _upload_text(
        "admin-user",
        "programa.txt",
        "Modulo Becas: el programa ESCOLA evalua becas por promedio, asistencia y documentos completos.",
    )

    supervisor = EscolaSupervisor()
    ingest = supervisor.ingest_upload("admin-user", meta["id"], tags=["becas"])
    result = supervisor.query("Que criterios usa el modulo de becas?")

    assert ingest["chunks"] >= 1
    assert result["agent"] == "ESCOLA"
    assert "Modulo Becas" in result["copy_ready"]
    assert result["evidence"]


def test_escola_tool_requires_admin_for_ingest(tmp_path, monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "upload_root", str(tmp_path / "uploads"))
    monkeypatch.setattr(settings, "artifact_root", str(tmp_path / "artifacts"))
    meta = _upload_text("teacher-user", "programa.txt", "Contenido de prueba")

    output = ToolRegistry().execute(
        "escola",
        role="teacher",
        action="ingest",
        user_id="teacher-user",
        file_id=meta["id"],
    )

    assert output.ok is False
    assert "admin" in output.error

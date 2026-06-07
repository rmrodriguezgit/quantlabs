import json

from document_intelligence import DocumentIntelligenceSupervisor
from tools.registry import ToolRegistry


def test_document_intelligence_extracts_and_verifies_text_document(tmp_path):
    document = tmp_path / "cliente.txt"
    document.write_text(
        "Cliente: ACME SA de CV\n"
        "Correo: contacto@acme.mx\n"
        "RFC: ACM010203AB1\n"
        "Monto: $12,500.50\n"
        "Concepto: Renovacion de servicio\n",
        encoding="utf-8",
    )

    result = DocumentIntelligenceSupervisor(artifact_root=tmp_path / "artifacts").process(document)

    assert result["status"] == "draft_ready"
    assert result["analysis"]["cliente"] == "ACME SA de CV"
    assert result["analysis"]["correo"] == "contacto@acme.mx"
    assert result["analysis"]["rfc"] == "ACM010203AB1"
    assert result["analysis"]["monto"] == 12500.50
    assert result["verification"]["safe_to_email"] is True
    assert result["communication"]["auto_send_enabled"] is False

    audit_files = list((tmp_path / "artifacts" / "document_intelligence" / "audit").glob("*.jsonl"))
    assert audit_files
    audit = json.loads(audit_files[0].read_text().splitlines()[0])
    assert audit["document_id"] == result["document_id"]


def test_document_intelligence_tool_requires_review_for_missing_client(tmp_path, monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "allowed_file_roots", str(tmp_path))
    monkeypatch.setattr(settings, "artifact_root", str(tmp_path / "artifacts"))
    document = tmp_path / "mensaje.txt"
    document.write_text("Favor de revisar el monto $99.00 sin correo claro.", encoding="utf-8")

    output = ToolRegistry().execute(
        "document_intelligence",
        role="teacher",
        path=str(document),
    )

    assert output.ok is True
    result = output.output
    assert result["status"] == "ready_for_review"
    assert result["verification"]["requires_human_review"] is True
    assert "cliente" in result["verification"]["missing_fields"]
    assert result["communication"]["status"] == "blocked"

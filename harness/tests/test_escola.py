from io import BytesIO

from werkzeug.datastructures import FileStorage

from escola import EscolaSupervisor
from escola.database import EscolaDatabaseManager
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
    assert result["formatted_json"]["agente"] == "ESCOLA"
    assert "**Consulta:**" in result["copy_ready"]
    assert "## Evidencia consultada" in result["copy_ready"]
    assert "Modulo Becas" in result["copy_ready"]
    assert "## JSON" not in result["copy_ready"]
    assert "```json" not in result["copy_ready"]
    assert "| --- |" not in result["copy_ready"]
    assert result["evidence"]


def test_escola_humanizes_json_fragments_in_copy_ready(tmp_path, monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "upload_root", str(tmp_path / "uploads"))
    monkeypatch.setattr(settings, "artifact_root", str(tmp_path / "artifacts"))
    meta = _upload_text(
        "admin-user",
        "base.json",
        '{"programas":{"lan":{"programa":"Licenciatura en Administración de Negocios","modalidad":"Escolar","anio":2025,"semestres":[{"semestre":5,"claves_materias":["LAN538","LAN539"]}]}}}',
    )

    supervisor = EscolaSupervisor()
    supervisor.ingest_upload("admin-user", meta["id"], tags=["planes"])
    result = supervisor.query("Que programa LAN aparece?")

    assert "Licenciatura en Administración de Negocios" in result["copy_ready"]
    assert '"programas"' not in result["copy_ready"]
    assert "{ \"programas\"" not in result["copy_ready"]
    assert "| --- |" not in result["copy_ready"]


def test_escola_lists_actuaria_subjects_by_semester(tmp_path, monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "upload_root", str(tmp_path / "uploads"))
    monkeypatch.setattr(settings, "artifact_root", str(tmp_path / "artifacts"))
    meta = _upload_text(
        "admin-user",
        "planes.txt",
        '"programa": "Licenciatura en Actuaría", '
        '"semestre": 1, "claves_materias": ["LARRDS", "LAR105"], '
        '"semestre": 2, "claves_materias": ["LARHUA"], '
        '"clave": "LARRDS", "nombre": "Radiografía Social", '
        '"clave": "LAR105", "nombre": "Prácticas Preliminares de la Profesión", '
        '"clave": "LARHUA", "nombre": "Humanismo en Acción"',
    )

    supervisor = EscolaSupervisor()
    supervisor.ingest_upload("admin-user", meta["id"], tags=["planes"])
    result = supervisor.query("Dame todas las materias de la licenciatura en Actuaría")

    assert "Licenciatura en Actuaría" in result["answer"]["response"]
    assert "Semestre 1" in result["answer"]["response"]
    assert "LARRDS: Radiografía Social" in result["answer"]["response"]
    assert "LAR105: Prácticas Preliminares de la Profesión" in result["answer"]["response"]
    assert "Semestre 2" in result["answer"]["response"]
    assert "LARHUA: Humanismo en Acción" in result["answer"]["response"]


def test_escola_database_manager_answers_subjects_and_content(tmp_path, monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "artifact_root", str(tmp_path / "artifacts"))
    db_file = tmp_path / "bd.json"
    db_file.write_text(
        """
        {
          "_meta": {"nombre": "BD Facultad"},
          "programas": [{
            "_id": "actuaria",
            "programa": "Licenciatura en Actuaría",
            "total_semestres": 2,
            "semestres": [{
              "semestre": 1,
              "materias": [
                {"clave": "LARRDS", "nombre": "Radiografía Social", "creditos": 3},
                {"clave": "LARTHC", "nombre": "Taller de Habilidades Comunicativas", "creditos": 5}
              ]
            }, {
              "semestre": 2,
              "materias": [
                {"clave": "LARHUA", "nombre": "Humanismo en Acción", "creditos": 3}
              ]
            }]
          }, {
            "_id": "lan",
            "programa": "Licenciatura en Administración de Negocios",
            "total_semestres": 1,
            "semestres": [{
              "semestre": 1,
              "materias": [
                {"clave": "LANRDS", "nombre": "Radiografía Social", "creditos": 3},
                {"clave": "LAN107", "nombre": "Contabilidad Financiera", "creditos": 7}
              ]
            }]
          }],
          "materias": [{
            "_id": "materia_larrds",
            "programa_id": "actuaria",
            "clave": "LARRDS",
            "nombre": "Radiografía Social",
            "semestre": 1,
            "fines_aprendizaje": "Comprender la realidad social.",
            "unidades": [{"numero_romano": "I", "nombre": "INDIVIDUO Y SOCIEDAD", "temas": [{"numero": "1.1", "nombre": "Marco conceptual"}]}]
          }, {
            "_id": "materia_lan_lanrds",
            "programa_id": "lan",
            "clave": "LANRDS",
            "nombre": "Radiografía Social",
            "semestre": 1,
            "area": "Currícula Común",
            "tipo": "Obligatoria",
            "creditos": 3,
            "horas_independientes": 16,
            "instalacion": "Aula",
            "fines_aprendizaje": "Comprender la realidad social."
          }, {
            "_id": "materia_lan_lan107",
            "programa_id": "lan",
            "clave": "LAN107",
            "nombre": "Contabilidad Financiera",
            "semestre": 1,
            "area": "Economía y Finanzas",
            "tipo": "Obligatoria",
            "creditos": 7,
            "horas_independientes": 32,
            "instalacion": "Aula",
            "fines_aprendizaje": "Aplicar registros contables financieros.",
            "estadisticas": {"total_unidades": 2, "total_temas": 4, "total_subtemas": 0}
          }],
          "optativas": []
        }
        """,
        encoding="utf-8",
    )

    manager = EscolaDatabaseManager()
    imported = manager.import_file(db_file, name="facultad")
    assert imported["stats"]["programas"] == 2

    supervisor = EscolaSupervisor(artifact_root := tmp_path / "artifacts")
    subjects = supervisor.query("Dame todas las materias de la licenciatura en Actuaría")
    assert subjects["answer"]["source"] == "database"
    assert "LARRDS: Radiografía Social · 3 créditos" in subjects["answer"]["response"]

    semester = supervisor.query("Materias de Actuaría de Semestre 1")
    assert semester["answer"]["source"] == "database"
    assert "Semestre 1" in semester["answer"]["response"]
    assert "LARRDS: Radiografía Social · 3 créditos" in semester["answer"]["response"]
    assert "Semestre 2" not in semester["answer"]["response"]
    assert "LARHUA" not in semester["answer"]["response"]

    first_semester = supervisor.query("Materias de Actuaría de Primer Semestre")
    assert "Semestre 1" in first_semester["answer"]["response"]
    assert "Semestre 2" not in first_semester["answer"]["response"]

    program_plan = supervisor.query("Licenciatura Administración de Negocios")
    assert program_plan["answer"]["source"] == "database"
    assert "Licenciatura en Administración de Negocios" in program_plan["answer"]["response"]
    assert "Semestre 1" in program_plan["answer"]["response"]
    assert "LANRDS: Radiografía Social · 3 créditos" in program_plan["answer"]["response"]
    assert "programas" not in program_plan["answer"]["response"]
    assert "valor_corregido" not in program_plan["answer"]["response"]

    credits = supervisor.query("Cuantos creditos tiene Administracion de Negocios")
    assert credits["answer"]["source"] == "database"
    assert "Créditos: 10" in credits["answer"]["response"]

    subject_search = supervisor.query("Que materias tienen contabilidad")
    assert subject_search["answer"]["source"] == "database"
    assert "LAN107: Contabilidad Financiera" in subject_search["answer"]["response"]

    subject_detail = supervisor.query("LAN107")
    assert subject_detail["answer"]["source"] == "database"
    assert "Créditos: 7" in subject_detail["answer"]["response"]
    assert "Economía y Finanzas" in subject_detail["answer"]["response"]

    content = supervisor.query("Dame el contenido de LARRDS")
    assert "Comprender la realidad social" in content["answer"]["response"]
    assert "1.1: Marco conceptual" in content["answer"]["response"]


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

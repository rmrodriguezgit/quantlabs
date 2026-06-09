from __future__ import annotations

import json
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from config import settings


PROGRAM_ALIASES = {
    "actuaria": ["actuaria", "actuaría", "licenciatura en actuaria", "licenciatura en actuaría"],
    "lan": ["lan", "administracion de negocios", "administración de negocios"],
    "lcp": ["lcp", "contaduria", "contaduría", "contaduría pública", "contaduria publica"],
    "lnd": ["lnd", "negocios digitales"],
    "lni": ["lni", "negocios internacionales"],
    "lar": ["lar", "actuaría"],
}


class EscolaDatabaseManager:
    """Administra bases NoSQL académicas usadas por ESCOLA."""

    def __init__(self, root: str | Path | None = None):
        self.root = Path(root or settings.artifact_root) / "escola" / "databases"
        self.root.mkdir(parents=True, exist_ok=True)
        self.manifest_path = self.root / "manifest.json"

    def import_file(self, path: str | Path, name: str | None = None) -> dict[str, Any]:
        source = Path(path).expanduser().resolve()
        if not source.exists():
            raise FileNotFoundError("database file not found")
        data = self._load_json(source)
        db_name = self._safe_name(name or source.stem)
        target = self.root / f"{db_name}.json"
        shutil.copyfile(source, target)
        manifest = {
            "name": db_name,
            "filename": target.name,
            "path": str(target),
            "source_path": str(source),
            "stats": self._stats_for_data(data),
            "imported_at": datetime.now(UTC).isoformat(),
        }
        self.manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        return manifest

    def active_manifest(self) -> dict[str, Any] | None:
        if not self.manifest_path.exists():
            return None
        try:
            return json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def active_data(self) -> dict[str, Any] | None:
        manifest = self.active_manifest()
        if not manifest:
            return None
        path = Path(manifest.get("path") or "")
        if not path.exists():
            return None
        return self._load_json(path)

    def stats(self) -> dict[str, Any]:
        manifest = self.active_manifest()
        if not manifest:
            return {"active": False, "databases": [], "stats": {}}
        return {"active": True, "databases": [manifest], "stats": manifest.get("stats") or {}}

    def answer(self, question: str) -> dict[str, Any] | None:
        data = self.active_data()
        if not data:
            return None
        lowered = self._norm(question)
        if not lowered:
            return None
        program_id = self._detect_program(lowered, data)
        if any(term in lowered for term in ["materia", "materias", "semestre", "plan", "curricular"]) and program_id:
            return self._answer_program_subjects(data, program_id)
        if any(term in lowered for term in ["optativa", "optativas", "ingles", "inglés", "frances", "francés", "idioma"]):
            return self._answer_optatives(data, program_id)
        subject = self._detect_subject(lowered, data)
        if subject and any(term in lowered for term in ["unidad", "unidades", "tema", "temas", "contenido", "subtema", "subtemas"]):
            return self._answer_subject_content(subject)
        if any(term in lowered for term in ["estadistica", "estadísticas", "resumen", "cuantos", "cuántos"]):
            return self._answer_database_summary(data)
        return None

    def _answer_program_subjects(self, data: dict[str, Any], program_id: str) -> dict[str, Any]:
        program = self._program_by_id(data, program_id)
        if not program:
            return None
        lines = [program.get("programa") or program_id]
        total = 0
        for semester in program.get("semestres") or []:
            lines.append("")
            lines.append(f"Semestre {semester.get('semestre')}")
            for subject in semester.get("materias") or []:
                total += 1
                credits = subject.get("creditos")
                suffix = f" · {credits} créditos" if credits not in (None, "") else ""
                lines.append(f"- {subject.get('clave')}: {subject.get('nombre')}{suffix}")
        return {
            "summary": f"Se consultó la BD NoSQL académica: {program.get('programa')} con {total} materia(s).",
            "response": "\n".join(lines),
            "confidence": "alta",
            "pending": [],
            "source": "database",
        }

    def _answer_subject_content(self, subject: dict[str, Any]) -> dict[str, Any]:
        lines = [
            f"{subject.get('clave')}: {subject.get('nombre')}",
            f"Programa: {subject.get('programa_id')}",
            f"Semestre: {subject.get('semestre', 'n/d')}",
            "",
            "Fines de aprendizaje:",
            subject.get("fines_aprendizaje") or "No disponible.",
        ]
        units = subject.get("unidades") or []
        if units:
            lines.extend(["", "Unidades y temas:"])
            for unit in units:
                lines.append(f"- {unit.get('numero_romano') or ''} {unit.get('nombre')}".strip())
                for topic in (unit.get("temas") or [])[:12]:
                    lines.append(f"  - {topic.get('numero')}: {topic.get('nombre')}")
        return {
            "summary": f"Se consultó la materia {subject.get('clave')} en la BD NoSQL académica.",
            "response": "\n".join(lines),
            "confidence": "alta",
            "pending": [],
            "source": "database",
        }

    def _answer_optatives(self, data: dict[str, Any], program_id: str | None) -> dict[str, Any]:
        rows = data.get("optativas") or []
        if program_id:
            rows = [row for row in rows if row.get("programa_id") == program_id or row.get("programa_codigo_origen") == program_id]
        lines = ["Optativas detectadas"]
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            grouped.setdefault(row.get("nivel") or "Sin nivel", []).append(row)
        for level in sorted(grouped):
            lines.append("")
            lines.append(level)
            for row in grouped[level][:24]:
                lines.append(f"- {row.get('clave')}: {row.get('nombre')}")
        return {
            "summary": f"Se consultaron {len(rows)} optativa(s) en la BD NoSQL académica.",
            "response": "\n".join(lines),
            "confidence": "alta" if rows else "media",
            "pending": [] if rows else ["No se detectaron optativas para el filtro solicitado."],
            "source": "database",
        }

    def _answer_database_summary(self, data: dict[str, Any]) -> dict[str, Any]:
        stats = self._stats_for_data(data)
        lines = [
            "Resumen de BD NoSQL académica",
            f"- Programas: {stats.get('programas', 0)}",
            f"- Materias: {stats.get('materias', 0)}",
            f"- Optativas: {stats.get('optativas', 0)}",
        ]
        for program in data.get("programas") or []:
            lines.append(f"- {program.get('_id')}: {program.get('programa')} ({program.get('total_semestres')} semestres)")
        return {
            "summary": "Se consultaron estadísticas de la BD NoSQL académica.",
            "response": "\n".join(lines),
            "confidence": "alta",
            "pending": [],
            "source": "database",
        }

    def _detect_program(self, lowered: str, data: dict[str, Any]) -> str | None:
        for program_id, aliases in PROGRAM_ALIASES.items():
            if any(alias in lowered for alias in aliases):
                return program_id
        for program in data.get("programas") or []:
            name = self._norm(program.get("programa"))
            if name and name in lowered:
                return program.get("_id")
        return None

    def _detect_subject(self, lowered: str, data: dict[str, Any]) -> dict[str, Any] | None:
        subjects = data.get("materias") or []
        candidates = re.findall(r"\b[A-Z]{5,12}\b|\b[A-Z]{2,8}[0-9][0-9A-Z]{1,6}\b", str(lowered).upper())
        for code in candidates:
            for subject in data.get("materias") or []:
                if subject.get("clave") == code:
                    return subject
        for subject in subjects:
            name = self._norm(subject.get("nombre"))
            if name and name in lowered:
                return subject
        return None

    def _program_by_id(self, data: dict[str, Any], program_id: str) -> dict[str, Any] | None:
        for program in data.get("programas") or []:
            if program.get("_id") == program_id or program.get("codigo_origen") == program_id:
                return program
        return None

    def _stats_for_data(self, data: dict[str, Any]) -> dict[str, Any]:
        return {
            "programas": len(data.get("programas") or []),
            "materias": len(data.get("materias") or []),
            "optativas": len(data.get("optativas") or []),
            "nombre": (data.get("_meta") or {}).get("nombre"),
            "institucion": (data.get("_meta") or {}).get("institucion"),
            "anio": (data.get("_meta") or {}).get("anio"),
        }

    def _load_json(self, path: Path) -> dict[str, Any]:
        data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
        if not isinstance(data, dict):
            raise ValueError("database root must be an object")
        return data

    def _safe_name(self, value: str) -> str:
        return re.sub(r"[^a-zA-Z0-9_.-]+", "_", value.strip().lower())[:80] or "database"

    def _norm(self, value: Any) -> str:
        return " ".join(str(value or "").strip().lower().split())

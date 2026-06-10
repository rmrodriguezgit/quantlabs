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
        semester_number = self._detect_semester(lowered)
        subject_by_code = self._detect_subject_code(lowered, data)
        if subject_by_code and any(term in lowered for term in ["unidad", "unidades", "tema", "temas", "contenido", "subtema", "subtemas", "temario", "aprendizaje", "fines"]):
            return self._answer_subject_content(subject_by_code)
        if subject_by_code:
            return self._answer_subject_detail(subject_by_code)
        if any(term in lowered for term in ["credito", "crédito", "creditos", "créditos", "total", "totales"]):
            return self._answer_credits(data, program_id, semester_number=semester_number)
        if any(term in lowered for term in ["area", "área", "areas", "áreas", "linea", "línea"]):
            return self._answer_areas(data, program_id)
        if any(term in lowered for term in ["instalacion", "instalación", "instalaciones", "laboratorio", "aula"]):
            return self._answer_installations(data, program_id)
        if any(term in lowered for term in ["buscar", "busca", "contienen", "contenga", "tienen", "relacionadas", "materias con", "materias de"]):
            matched = self._answer_subject_search(data, lowered, program_id, semester_number=semester_number)
            if matched:
                return matched
        if self._asks_for_program_plan(lowered, program_id):
            return self._answer_program_subjects(data, program_id, semester_number=semester_number)
        if any(term in lowered for term in ["optativa", "optativas", "ingles", "inglés", "frances", "francés", "idioma"]):
            return self._answer_optatives(data, program_id)
        subject = self._detect_subject(lowered, data)
        if subject and any(term in lowered for term in ["detalle", "materia", "clave", "unidad", "unidades", "tema", "temas", "contenido", "subtema", "subtemas", "temario", "aprendizaje", "fines"]):
            if any(term in lowered for term in ["unidad", "unidades", "tema", "temas", "contenido", "subtema", "subtemas", "temario", "aprendizaje", "fines"]):
                return self._answer_subject_content(subject)
            return self._answer_subject_detail(subject)
        if program_id:
            return self._answer_program_summary(data, program_id)
        if any(term in lowered for term in ["estadistica", "estadísticas", "resumen", "cuantos", "cuántos"]):
            return self._answer_database_summary(data)
        return None

    def _answer_program_subjects(
        self,
        data: dict[str, Any],
        program_id: str,
        semester_number: int | None = None,
    ) -> dict[str, Any]:
        program = self._program_by_id(data, program_id)
        if not program:
            return None
        lines = [program.get("programa") or program_id]
        total = 0
        semesters = program.get("semestres") or []
        if semester_number is not None:
            semesters = [semester for semester in semesters if self._as_int(semester.get("semestre")) == semester_number]
        for semester in semesters:
            lines.append("")
            lines.append(f"Semestre {semester.get('semestre')}")
            for subject in semester.get("materias") or []:
                total += 1
                credits = subject.get("creditos")
                suffix = f" · {credits} créditos" if credits not in (None, "") else ""
                area = f" · {subject.get('area')}" if subject.get("area") else ""
                lines.append(f"- {subject.get('clave')}: {subject.get('nombre')}{suffix}{area}")
        if semester_number is not None and not total:
            lines.append("")
            lines.append(f"No encontré materias para el semestre {semester_number}.")
        summary_semester = f" del semestre {semester_number}" if semester_number is not None else ""
        return {
            "summary": f"Se consultó la BD NoSQL académica: {program.get('programa')}{summary_semester} con {total} materia(s).",
            "response": "\n".join(lines),
            "confidence": "alta",
            "pending": [] if total else ["No se encontraron materias para el filtro solicitado."],
            "source": "database",
        }

    def _answer_program_summary(self, data: dict[str, Any], program_id: str) -> dict[str, Any] | None:
        program = self._program_by_id(data, program_id)
        if not program:
            return None
        subjects = self._subjects_for_program(data, program_id)
        credits = sum(self._as_int(subject.get("creditos")) or 0 for subject in subjects)
        lines = [
            program.get("programa") or program_id,
            f"- Modalidad: {program.get('modalidad', 'n/d')}",
            f"- Año: {program.get('anio', 'n/d')}",
            f"- Semestres: {program.get('total_semestres') or len(program.get('semestres') or [])}",
            f"- Materias: {len(subjects)}",
            f"- Créditos calculados: {credits}",
        ]
        areas = self._area_counts(subjects)
        if areas:
            lines.append("")
            lines.append("Áreas")
            for area, count in areas:
                lines.append(f"- {area}: {count} materia(s)")
        return {
            "summary": f"Se consultó el resumen estructurado de {program.get('programa')}.",
            "response": "\n".join(lines),
            "confidence": "alta",
            "pending": [],
            "source": "database",
        }

    def _answer_subject_detail(self, subject: dict[str, Any]) -> dict[str, Any]:
        lines = [
            f"{subject.get('clave')}: {subject.get('nombre')}",
            f"- Programa: {subject.get('programa_id')}",
            f"- Semestre: {subject.get('semestre', 'n/d')}",
            f"- Área: {subject.get('area', 'n/d')}",
            f"- Tipo: {subject.get('tipo', 'n/d')}",
            f"- Créditos: {subject.get('creditos', 'n/d')}",
            f"- Horas independientes: {subject.get('horas_independientes', 'n/d')}",
            f"- Instalación: {subject.get('instalacion', 'n/d')}",
        ]
        stats = subject.get("estadisticas") or {}
        if stats:
            lines.append(f"- Contenido: {stats.get('total_unidades', 0)} unidad(es), {stats.get('total_temas', 0)} tema(s), {stats.get('total_subtemas', 0)} subtema(s)")
        return {
            "summary": f"Se consultó el detalle de {subject.get('clave')} en la BD NoSQL académica.",
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

    def _answer_credits(self, data: dict[str, Any], program_id: str | None, semester_number: int | None = None) -> dict[str, Any] | None:
        if not program_id:
            return None
        program = self._program_by_id(data, program_id)
        subjects = self._subjects_for_program(data, program_id, semester_number=semester_number)
        if not program or not subjects:
            return None
        total_credits = sum(self._as_int(subject.get("creditos")) or 0 for subject in subjects)
        total_hours = sum(self._as_int(subject.get("horas_independientes")) or 0 for subject in subjects)
        title = f"{program.get('programa')} - Semestre {semester_number}" if semester_number else program.get("programa")
        lines = [
            title,
            f"- Materias: {len(subjects)}",
            f"- Créditos: {total_credits}",
            f"- Horas independientes: {total_hours}",
        ]
        if not semester_number:
            for semester in program.get("semestres") or []:
                rows = semester.get("materias") or []
                credits = sum(self._as_int(row.get("creditos")) or 0 for row in rows)
                lines.append(f"- Semestre {semester.get('semestre')}: {len(rows)} materia(s), {credits} créditos")
        return {
            "summary": f"Se calcularon créditos desde la BD NoSQL académica para {title}.",
            "response": "\n".join(lines),
            "confidence": "alta",
            "pending": [],
            "source": "database",
        }

    def _answer_subject_search(
        self,
        data: dict[str, Any],
        lowered: str,
        program_id: str | None,
        semester_number: int | None = None,
    ) -> dict[str, Any] | None:
        subjects = self._subjects_for_program(data, program_id, semester_number=semester_number)
        query_terms = [
            term for term in re.findall(r"[\wáéíóúñü]+", lowered)
            if len(term) >= 4 and term not in {"materias", "materia", "buscar", "busca", "tienen", "tiene", "contienen", "contenga", "relacionadas", "licenciatura", "semestre"}
        ]
        program_aliases = {alias for aliases in PROGRAM_ALIASES.values() for alias in aliases}
        query_terms = [term for term in query_terms if term not in program_aliases]
        if not query_terms:
            return None
        broad_content = any(term in lowered for term in ["contenido", "tema", "temas", "temario", "subtema", "aprendizaje", "fines"])
        matches = []
        for subject in subjects:
            fields = [
                subject.get("clave") or "",
                subject.get("nombre") or "",
                subject.get("area") or "",
            ]
            if broad_content:
                fields.extend([
                    subject.get("fines_aprendizaje") or "",
                    ((subject.get("busqueda") or {}).get("texto") or ""),
                ])
            haystack = self._norm(" ".join(fields))
            if any(term in haystack for term in query_terms):
                matches.append(subject)
        if not matches:
            return None
        lines = [f"Materias encontradas para: {', '.join(query_terms)}"]
        for subject in matches[:80]:
            credits = subject.get("creditos")
            suffix = f" · {credits} créditos" if credits not in (None, "") else ""
            lines.append(f"- {subject.get('clave')}: {subject.get('nombre')} · Semestre {subject.get('semestre')}{suffix} · {subject.get('area', 'Sin área')}")
        return {
            "summary": f"Se encontraron {len(matches)} materia(s) en la BD NoSQL académica.",
            "response": "\n".join(lines),
            "confidence": "alta",
            "pending": [] if len(matches) <= 80 else ["Se muestran las primeras 80 coincidencias."],
            "source": "database",
        }

    def _answer_areas(self, data: dict[str, Any], program_id: str | None) -> dict[str, Any] | None:
        subjects = self._subjects_for_program(data, program_id)
        if not subjects:
            return None
        lines = ["Áreas académicas"]
        for area, count in self._area_counts(subjects):
            credits = sum(self._as_int(subject.get("creditos")) or 0 for subject in subjects if (subject.get("area") or "Sin área") == area)
            lines.append(f"- {area}: {count} materia(s), {credits} créditos")
        return {
            "summary": f"Se agruparon {len(subjects)} materia(s) por área.",
            "response": "\n".join(lines),
            "confidence": "alta",
            "pending": [],
            "source": "database",
        }

    def _answer_installations(self, data: dict[str, Any], program_id: str | None) -> dict[str, Any] | None:
        subjects = self._subjects_for_program(data, program_id)
        if not subjects:
            return None
        grouped: dict[str, list[dict[str, Any]]] = {}
        for subject in subjects:
            grouped.setdefault(subject.get("instalacion") or "Sin instalación", []).append(subject)
        lines = ["Instalaciones"]
        for name in sorted(grouped):
            rows = grouped[name]
            lines.append(f"- {name}: {len(rows)} materia(s)")
            for subject in rows[:8]:
                lines.append(f"  - {subject.get('clave')}: {subject.get('nombre')} · Semestre {subject.get('semestre')}")
        return {
            "summary": f"Se agruparon {len(subjects)} materia(s) por instalación.",
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

    def _subjects_for_program(
        self,
        data: dict[str, Any],
        program_id: str | None,
        semester_number: int | None = None,
    ) -> list[dict[str, Any]]:
        rows = []
        programs = data.get("programas") or []
        if program_id:
            programs = [program for program in programs if program.get("_id") == program_id or program.get("codigo_origen") == program_id]
        for program in programs:
            pid = program.get("_id") or program.get("codigo_origen")
            for semester in program.get("semestres") or []:
                for subject in semester.get("materias") or []:
                    rows.append({
                        **self._content_subject(data, pid, subject.get("clave")),
                        **subject,
                        "programa_id": pid,
                        "programa_codigo_origen": program.get("codigo_origen") or pid,
                        "semestre": semester.get("semestre"),
                    })
        if not rows and not program_id:
            rows = data.get("materias") or []
        if semester_number is not None:
            rows = [row for row in rows if self._as_int(row.get("semestre")) == semester_number]
        return rows

    def _content_subject(self, data: dict[str, Any], program_id: str | None, code: str | None) -> dict[str, Any]:
        for subject in data.get("materias") or []:
            if subject.get("clave") == code and (not program_id or subject.get("programa_id") == program_id or subject.get("programa_codigo_origen") == program_id):
                return subject
        return {}

    def _merge_plan_subject(self, data: dict[str, Any], subject: dict[str, Any]) -> dict[str, Any]:
        program_id = subject.get("programa_id") or subject.get("programa_codigo_origen")
        code = subject.get("clave")
        for row in self._subjects_for_program(data, program_id):
            if row.get("clave") == code:
                return {**subject, **row}
        return subject

    def _area_counts(self, subjects: list[dict[str, Any]]) -> list[tuple[str, int]]:
        counts: dict[str, int] = {}
        for subject in subjects:
            counts[subject.get("area") or "Sin área"] = counts.get(subject.get("area") or "Sin área", 0) + 1
        return sorted(counts.items(), key=lambda item: (-item[1], item[0]))

    def _asks_for_program_plan(self, lowered: str, program_id: str | None) -> bool:
        if not program_id:
            return False
        plan_terms = [
            "carrera",
            "licenciatura",
            "materia",
            "materias",
            "semestre",
            "plan",
            "curricular",
            "mapa",
            "programa",
        ]
        return any(term in lowered for term in plan_terms)

    def _detect_subject(self, lowered: str, data: dict[str, Any]) -> dict[str, Any] | None:
        subject = self._detect_subject_code(lowered, data)
        if subject:
            return subject
        for subject in data.get("materias") or []:
            name = self._norm(subject.get("nombre"))
            if name and name in lowered:
                return subject
        return None

    def _detect_subject_code(self, lowered: str, data: dict[str, Any]) -> dict[str, Any] | None:
        candidates = re.findall(r"\b[A-Z]{5,12}\b|\b[A-Z]{2,8}[0-9][0-9A-Z]{1,6}\b", str(lowered).upper())
        for code in candidates:
            for subject in data.get("materias") or []:
                if subject.get("clave") == code:
                    return self._merge_plan_subject(data, subject)
            for subject in self._subjects_for_program(data, None):
                if subject.get("clave") == code:
                    return subject
        return None

    def _detect_semester(self, lowered: str) -> int | None:
        ordinal_map = {
            "primer": 1,
            "primero": 1,
            "uno": 1,
            "segundo": 2,
            "dos": 2,
            "tercer": 3,
            "tercero": 3,
            "tres": 3,
            "cuarto": 4,
            "cuatro": 4,
            "quinto": 5,
            "cinco": 5,
            "sexto": 6,
            "seis": 6,
            "septimo": 7,
            "séptimo": 7,
            "siete": 7,
            "octavo": 8,
            "ocho": 8,
            "noveno": 9,
            "nueve": 9,
            "decimo": 10,
            "décimo": 10,
            "diez": 10,
        }
        numeric_patterns = [
            r"\bsemestre\s*(\d{1,2})\b",
            r"\b(\d{1,2})\s*(?:er|ro|do|to|o)?\s*semestre\b",
        ]
        for pattern in numeric_patterns:
            match = re.search(pattern, lowered)
            if match:
                number = self._as_int(match.group(1))
                if number:
                    return number
        for word, number in ordinal_map.items():
            if re.search(rf"\b{re.escape(word)}\s+semestre\b|\bsemestre\s+{re.escape(word)}\b", lowered):
                return number
        return None

    def _program_by_id(self, data: dict[str, Any], program_id: str) -> dict[str, Any] | None:
        for program in data.get("programas") or []:
            if program.get("_id") == program_id or program.get("codigo_origen") == program_id:
                return program
        return None

    def _as_int(self, value: Any) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
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

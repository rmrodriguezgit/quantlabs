from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from config import settings
from memory.uploads import UploadStore


TOKEN_RE = re.compile(r"[\wáéíóúñüÁÉÍÓÚÑÜ-]{3,}", re.IGNORECASE)
STOP_WORDS = {
    "como", "para", "pero", "este", "esta", "estos", "estas", "desde", "donde", "cuando",
    "programa", "archivo", "archivos", "documento", "documentos", "sobre", "puedes", "quiero",
    "necesito", "consulta", "respuesta", "salida", "formato", "facil", "fácil",
}
ACADEMIC_PROGRAMS = {
    "actuaria": {
        "id": "actuaria",
        "prefix": "LAR",
        "name": "Licenciatura en Actuaría",
        "aliases": ["actuaria", "actuaría", "licenciatura en actuaria", "licenciatura en actuaría"],
    },
    "lan": {
        "id": "lan",
        "prefix": "LAN",
        "name": "Licenciatura en Administración de Negocios",
        "aliases": ["administracion de negocios", "administración de negocios", "lan"],
    },
}


@dataclass
class EscolaChunk:
    id: str
    document_id: str
    text: str
    embedding: list[float]
    metadata: dict[str, Any]


class EscolaSupervisor:
    """RAG documental para consultas e inferencias del programa ESCOLA."""

    def __init__(self, root: str | Path | None = None):
        self.root = Path(root or settings.artifact_root) / "escola"
        self.docs_path = self.root / "documents.jsonl"
        self.chunks_path = self.root / "chunks.jsonl"
        self.audit_path = self.root / "audit.jsonl"
        self.root.mkdir(parents=True, exist_ok=True)
        self.uploads = UploadStore()

    def ingest_upload(self, user_id: str, file_id: str, tags: list[str] | None = None) -> dict[str, Any]:
        meta = self.uploads.get(user_id, file_id)
        if not meta:
            raise FileNotFoundError("file_id not found")
        summary = self._source_text(meta)
        if not summary.strip():
            raise ValueError("archivo sin texto extraible")
        document_id = self._document_id(file_id, meta.get("name"))
        existing = self.get_document(document_id)
        if existing:
            return {**existing, "already_indexed": True}

        chunks = self._chunk_text(summary)
        now = datetime.now(UTC).isoformat()
        document = {
            "document_id": document_id,
            "file_id": file_id,
            "owner_id": meta.get("owner_id"),
            "filename": meta.get("name"),
            "extension": meta.get("ext"),
            "size": meta.get("size"),
            "tags": sorted(set(tags or [])),
            "chunks": len(chunks),
            "created_at": now,
            "source_path": meta.get("path"),
        }
        self._append_jsonl(self.docs_path, document)
        for index, text in enumerate(chunks):
            chunk = EscolaChunk(
                id=str(uuid.uuid4()),
                document_id=document_id,
                text=text,
                embedding=self.embed(text),
                metadata={
                    "chunk_index": index,
                    "filename": meta.get("name"),
                    "extension": meta.get("ext"),
                    "tags": document["tags"],
                    "created_at": now,
                },
            )
            self._append_jsonl(self.chunks_path, {
                "id": chunk.id,
                "document_id": chunk.document_id,
                "text": chunk.text,
                "embedding": chunk.embedding,
                "metadata": chunk.metadata,
            })
        self._audit("ingest", {"document_id": document_id, "file_id": file_id, "chunks": len(chunks)})
        return {**document, "already_indexed": False}

    def _source_text(self, meta: dict[str, Any]) -> str:
        ext = (meta.get("ext") or "").lower()
        path = Path(meta.get("path") or "")
        if ext in {"txt", "md", "json", "csv"} and path.exists():
            try:
                return "Contenido extraído:\n" + path.read_text(errors="ignore")
            except Exception:
                pass
        return meta.get("summary") or ""

    def list_documents(self) -> list[dict[str, Any]]:
        return sorted(self._read_jsonl(self.docs_path), key=lambda item: item.get("created_at", ""), reverse=True)

    def get_document(self, document_id: str) -> dict[str, Any] | None:
        for item in self._read_jsonl(self.docs_path):
            if item.get("document_id") == document_id:
                return item
        return None

    def query(self, question: str, top_k: int = 6, copy_ready: bool = True) -> dict[str, Any]:
        clean_question = " ".join(str(question or "").split())
        if not clean_question:
            raise ValueError("question required")
        chunks = self.search(clean_question, top_k=top_k)
        answer = self._compose_answer(clean_question, chunks)
        result = {
            "agent": "ESCOLA",
            "question": clean_question,
            "answer": answer,
            "formatted_json": self._formatted_json(clean_question, answer, chunks),
            "evidence": [
                {
                    "filename": item["metadata"].get("filename"),
                    "document_id": item["document_id"],
                    "chunk_id": item["id"],
                    "score": item["score"],
                    "preview": self._humanize_fragment(item["text"])[:420],
                }
                for item in chunks
            ],
            "stats": self.stats(),
            "created_at": datetime.now(UTC).isoformat(),
        }
        result["copy_ready"] = self._copy_ready(result) if copy_ready else None
        self._audit("query", {"question": clean_question, "matches": len(chunks)})
        return result

    def search(self, question: str, top_k: int = 6) -> list[dict[str, Any]]:
        query_embedding = self.embed(question)
        query_terms = set(self._tokens(question))
        rows = []
        for item in self._read_jsonl(self.chunks_path):
            vector_score = self._cos(query_embedding, item.get("embedding") or [])
            item_terms = set(self._tokens(item.get("text") or ""))
            overlap = len(query_terms & item_terms) / max(1, len(query_terms))
            score = round((vector_score * 0.72) + (overlap * 0.28), 4)
            rows.append({**item, "score": score})
        rows.sort(key=lambda item: item["score"], reverse=True)
        return rows[: max(1, min(int(top_k or 6), 12))]

    def stats(self) -> dict[str, Any]:
        documents = self.list_documents()
        chunks = self._read_jsonl(self.chunks_path)
        return {
            "documents": len(documents),
            "chunks": len(chunks),
            "path": str(self.root),
            "updated_at": documents[0].get("created_at") if documents else None,
        }

    def rules(self) -> dict[str, Any]:
        return {
            "agent": "ESCOLA",
            "mode": "rag_nosql",
            "storage": "jsonl",
            "supports": ["pdf", "docx", "csv", "xls", "xlsx", "txt", "md", "json", "ipynb", "png", "jpg", "jpeg"],
            "actions": ["ingest", "query", "list", "stats"],
            "copy_format": ["Respuesta ChatGPT", "Bullets de evidencia", "Pendientes"],
            "safety": [
                "no ejecuta acciones externas",
                "no modifica documentos fuente",
                "las respuestas indican evidencia usada",
                "si no hay evidencia suficiente marca pendiente",
            ],
        }

    def embed(self, text: str, dims: int = 128) -> list[float]:
        vector = [0.0] * dims
        for token in self._tokens(text):
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            vector[int.from_bytes(digest[:4], "big") % dims] += 1.0
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]

    def _compose_answer(self, question: str, chunks: list[dict[str, Any]]) -> dict[str, Any]:
        if not chunks:
            return {
                "summary": "No encontre evidencia indexada para responder.",
                "response": "Sube e ingesta archivos del programa antes de consultar ESCOLA.",
                "confidence": "baja",
                "pending": ["Ingestar documentos fuente"],
            }
        structured = self._academic_subjects_answer(question)
        if structured:
            return structured
        key_terms = [term for term in self._tokens(question) if term not in STOP_WORDS][:8]
        snippets = []
        seen = set()
        for item in chunks[:8]:
            snippet = self._best_sentence(item["text"], key_terms)
            fingerprint = snippet.lower()[:140]
            if snippet and fingerprint not in seen:
                snippets.append(snippet)
                seen.add(fingerprint)
            if len(snippets) >= 4:
                break
        confidence = "alta" if chunks[0]["score"] >= 0.42 else "media" if chunks[0]["score"] >= 0.25 else "baja"
        return {
            "summary": f"ESCOLA encontro {len(chunks)} fragmento(s) relevantes en la base documental.",
            "response": "\n".join(f"- {snippet}" for snippet in snippets) or chunks[0]["text"][:700],
            "confidence": confidence,
            "pending": [] if confidence != "baja" else ["Validar manualmente: la coincidencia documental fue baja"],
        }

    def _academic_subjects_answer(self, question: str) -> dict[str, Any] | None:
        lowered = question.lower()
        if not any(term in lowered for term in ("materia", "materias", "plan", "curricular", "semestre")):
            return None
        program = self._detect_academic_program(lowered)
        if not program:
            return None
        corpus = "\n".join(item.get("text") or "" for item in self._read_jsonl(self.chunks_path))
        curriculum = self._extract_curriculum(corpus, program["prefix"])
        names = self._extract_subject_names(corpus, program["prefix"])
        if not curriculum and not names:
            return None
        lines = [program["name"]]
        total_subjects = 0
        named_subjects = 0
        if curriculum:
            for semester in sorted(curriculum):
                lines.append("")
                lines.append(f"Semestre {semester}")
                for code in curriculum[semester]:
                    name = names.get(code)
                    total_subjects += 1
                    if name:
                        named_subjects += 1
                    lines.append(f"- {code}: {name or 'nombre no disponible en fragmentos indexados'}")
        else:
            lines.append("")
            lines.append("Materias detectadas")
            for code, name in sorted(names.items()):
                total_subjects += 1
                named_subjects += 1
                lines.append(f"- {code}: {name}")
        pending = []
        if total_subjects and named_subjects < total_subjects:
            pending.append(f"Faltan nombres para {total_subjects - named_subjects} clave(s); el índice actual solo contiene fragmentos parciales del JSON fuente.")
        return {
            "summary": f"Se extrajo el mapa curricular disponible para {program['name']}: {total_subjects} materia(s) detectada(s), {named_subjects} con nombre.",
            "response": "\n".join(lines),
            "confidence": "alta" if total_subjects and named_subjects == total_subjects else "media",
            "pending": pending,
        }

    def _detect_academic_program(self, lowered_question: str) -> dict[str, Any] | None:
        for program in ACADEMIC_PROGRAMS.values():
            if any(alias in lowered_question for alias in program["aliases"]):
                return program
        return None

    def _extract_curriculum(self, corpus: str, prefix: str) -> dict[int, list[str]]:
        curriculum: dict[int, list[str]] = {}
        pattern = re.compile(r'"semestre"\s*:\s*(\d+).*?"claves_materias"\s*:\s*\[(.*?)\]', re.IGNORECASE | re.DOTALL)
        for match in pattern.finditer(corpus):
            semester = int(match.group(1))
            codes = [code for code in re.findall(r'"([A-Z]{3,8}[0-9A-Z]{2,6})"', match.group(2)) if code.startswith(prefix)]
            if codes:
                existing = curriculum.setdefault(semester, [])
                for code in codes:
                    if code not in existing:
                        existing.append(code)
        # Fallback for the plan JSON, where subjects are full objects under each semester.
        sem_pattern = re.compile(r'"semestre"\s*:\s*(\d+).*?"materias"\s*:\s*\[(.*?)(?=\]\s*},\s*{\s*"semestre"|\]\s*}\s*\])', re.IGNORECASE | re.DOTALL)
        for match in sem_pattern.finditer(corpus):
            semester = int(match.group(1))
            if semester in curriculum:
                continue
            codes = [code for code in re.findall(r'"clave"\s*:\s*"([^"]+)"', match.group(2)) if code.startswith(prefix)]
            if codes:
                existing = curriculum.setdefault(semester, [])
                for code in codes:
                    if code not in existing:
                        existing.append(code)
        return curriculum

    def _extract_subject_names(self, corpus: str, prefix: str) -> dict[str, str]:
        names: dict[str, str] = {}
        pattern = re.compile(r'\{[^{}]*"clave"\s*:\s*"([^"]+)"[^{}]*"nombre"\s*:\s*"([^"]+)"[^{}]*\}', re.IGNORECASE | re.DOTALL)
        for code, name in pattern.findall(corpus):
            if code.startswith(prefix):
                names.setdefault(code, self._clean(name))
        pair_pattern = re.compile(r'"clave"\s*:\s*"([^"]+)"\s*,\s*"nombre"\s*:\s*"([^"]+)"', re.IGNORECASE)
        for code, name in pair_pattern.findall(corpus):
            if code.startswith(prefix):
                names.setdefault(code, self._clean(name))
        return names

    def _formatted_json(self, question: str, answer: dict[str, Any], chunks: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "agente": "ESCOLA",
            "consulta": question,
            "resultado": {
                "resumen": answer.get("summary"),
                "respuesta": answer.get("response"),
                "confianza": answer.get("confidence"),
                "pendientes": answer.get("pending") or [],
            },
            "evidencia": [
                {
                    "archivo": item["metadata"].get("filename"),
                    "document_id": item["document_id"],
                    "chunk": item["metadata"].get("chunk_index"),
                    "score": item["score"],
                    "preview": self._humanize_fragment(item["text"])[:260],
                }
                for item in chunks[:6]
            ],
        }

    def _copy_ready(self, result: dict[str, Any]) -> str:
        answer = result.get("answer") or {}
        evidence = result.get("evidence") or []
        response = self._format_response_for_copy(answer.get("response") or "--")
        pending = "; ".join(answer.get("pending") or ["Ninguno"])
        evidence_lines = [
            f"{idx}. {item.get('filename') or item.get('document_id')} (score {item.get('score')}): {self._clean(item.get('preview', ''))[:260]}"
            for idx, item in enumerate(evidence[:6], start=1)
        ] or ["1. Sin evidencia disponible."]
        return (
            "# ESCOLA\n\n"
            "## Respuesta\n"
            f"**Consulta:** {self._clean(result.get('question'))}\n\n"
            f"**Resumen:** {self._clean(answer.get('summary'))}\n\n"
            f"**Respuesta:**\n{response}\n\n"
            f"**Confianza:** {self._clean(answer.get('confidence'))}\n\n"
            f"**Pendientes:** {pending}\n\n"
            "## Evidencia consultada\n"
            + "\n".join(evidence_lines)
        )

    def _best_sentence(self, text: str, terms: list[str]) -> str:
        human_text = self._humanize_fragment(text)
        sentences = re.split(r"(?<=[.!?])\s+|\n+", human_text)
        best = ""
        best_score = -1
        for sentence in sentences:
            clean = self._clean(sentence)
            if len(clean) < 30:
                continue
            score = sum(1 for term in terms if term in clean.lower())
            if score > best_score:
                best = clean
                best_score = score
        return best[:520]

    def _humanize_fragment(self, text: str) -> str:
        value = self._clean(str(text or "").replace("Contenido extraído:", ""))
        if not value:
            return ""
        parsed = self._try_parse_jsonish(value)
        if parsed:
            return parsed
        if value.count("{") + value.count("[") + value.count('"') > 8:
            return self._summarize_jsonish_text(value)
        return value

    def _try_parse_jsonish(self, value: str) -> str | None:
        try:
            payload = json.loads(value)
        except Exception:
            return None
        return self._summarize_structured(payload)

    def _summarize_structured(self, payload: Any) -> str:
        items = []
        priority = ("nombre", "institucion", "facultad", "programa", "modalidad", "anio", "documento", "descripcion")
        if isinstance(payload, dict):
            for key in priority:
                if payload.get(key) not in (None, "", [], {}):
                    items.append(f"{key}: {payload[key]}")
            programas = payload.get("programas")
            if isinstance(programas, dict):
                names = [self._clean((program or {}).get("programa", key)) for key, program in list(programas.items())[:6]]
                if names:
                    items.append("programas: " + ", ".join(names))
        return ". ".join(items)[:900] if items else self._summarize_jsonish_text(json.dumps(payload, ensure_ascii=False))

    def _summarize_jsonish_text(self, value: str) -> str:
        priority = ("nombre", "institucion", "facultad", "programa", "modalidad", "anio", "documento", "descripcion", "semestre", "clave", "materia")
        pairs = []
        for key in priority:
            pattern = rf'"{re.escape(key)}"\s*:\s*("([^"]{{1,180}})"|[0-9]{{1,4}})'
            for match in re.finditer(pattern, value, flags=re.IGNORECASE):
                raw = match.group(2) or match.group(1)
                clean = self._clean(raw.strip('"'))
                if clean and f"{key}: {clean}" not in pairs:
                    pairs.append(f"{key}: {clean}")
                if len(pairs) >= 10:
                    break
            if len(pairs) >= 10:
                break
        claves = re.findall(r'"([A-Z]{2,8}[0-9A-Z]{2,6})"', value)
        if claves:
            pairs.append("claves detectadas: " + ", ".join(dict.fromkeys(claves[:12])))
        if pairs:
            return ". ".join(pairs)[:900]
        readable = re.sub(r"[{}\[\]\",:]+", " ", value)
        return self._clean(readable)[:900]

    def _chunk_text(self, text: str, max_chars: int = 1400, overlap: int = 180) -> list[str]:
        clean = self._clean(text)
        if not clean:
            return []
        chunks = []
        start = 0
        while start < len(clean):
            end = min(len(clean), start + max_chars)
            chunks.append(clean[start:end])
            if end >= len(clean):
                break
            start = max(0, end - overlap)
        return chunks

    def _document_id(self, file_id: str, filename: str | None) -> str:
        return hashlib.sha256(f"{file_id}:{filename or ''}".encode("utf-8")).hexdigest()[:24]

    def _tokens(self, text: str) -> list[str]:
        return [token.lower() for token in TOKEN_RE.findall(str(text or "")) if token.lower() not in STOP_WORDS]

    def _clean(self, text: str) -> str:
        return " ".join(str(text or "").strip().split())

    def _markdown_table(self, headers: list[str], rows: list[tuple[Any, ...]]) -> str:
        header = "| " + " | ".join(self._escape_markdown_cell(value) for value in headers) + " |"
        separator = "| " + " | ".join("---" for _ in headers) + " |"
        body = [
            "| " + " | ".join(self._escape_markdown_cell(value) for value in row) + " |"
            for row in rows
        ]
        return "\n".join([header, separator, *body])

    def _escape_markdown_cell(self, value: Any) -> str:
        text = self._clean(value)
        return text.replace("|", "\\|").replace("\n", "<br>")

    def _format_response_for_copy(self, value: Any) -> str:
        lines = [self._clean(line) for line in str(value or "").splitlines()]
        lines = [line for line in lines if line]
        if not lines:
            return "--"
        return "\n".join(lines)

    def _append_jsonl(self, path: Path, item: dict[str, Any]) -> None:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")

    def _read_jsonl(self, path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        rows = []
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return rows

    def _audit(self, action: str, payload: dict[str, Any]) -> None:
        self._append_jsonl(self.audit_path, {
            "action": action,
            "payload": payload,
            "created_at": datetime.now(UTC).isoformat(),
        })

    def _cos(self, a: list[float], b: list[float]) -> float:
        if not a or not b:
            return 0.0
        denom = (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))) or 1.0
        return sum(x * y for x, y in zip(a, b)) / denom

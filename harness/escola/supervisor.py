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
        summary = meta.get("summary") or ""
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
            "copy_ready": self._copy_ready(clean_question, answer, chunks) if copy_ready else None,
            "evidence": [
                {
                    "filename": item["metadata"].get("filename"),
                    "document_id": item["document_id"],
                    "chunk_id": item["id"],
                    "score": item["score"],
                    "preview": item["text"][:420],
                }
                for item in chunks
            ],
            "stats": self.stats(),
            "created_at": datetime.now(UTC).isoformat(),
        }
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
            "copy_format": ["Resumen", "Respuesta", "Evidencia", "Pendientes"],
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
        key_terms = [term for term in self._tokens(question) if term not in STOP_WORDS][:8]
        snippets = [self._best_sentence(item["text"], key_terms) for item in chunks[:4]]
        snippets = [snippet for snippet in snippets if snippet]
        confidence = "alta" if chunks[0]["score"] >= 0.42 else "media" if chunks[0]["score"] >= 0.25 else "baja"
        return {
            "summary": f"ESCOLA encontro {len(chunks)} fragmento(s) relevantes en la base documental.",
            "response": "\n".join(f"- {snippet}" for snippet in snippets) or chunks[0]["text"][:700],
            "confidence": confidence,
            "pending": [] if confidence != "baja" else ["Validar manualmente: la coincidencia documental fue baja"],
        }

    def _copy_ready(self, question: str, answer: dict[str, Any], chunks: list[dict[str, Any]]) -> str:
        evidence = "\n".join(
            f"- {item['metadata'].get('filename')} | score {item['score']}: {self._clean(item['text'][:180])}"
            for item in chunks[:4]
        ) or "- Sin evidencia"
        pending = "\n".join(f"- {item}" for item in answer.get("pending") or ["Ninguno"])
        return (
            "ESCOLA\n"
            f"Consulta: {question}\n\n"
            f"Resumen:\n{answer.get('summary')}\n\n"
            f"Respuesta:\n{answer.get('response')}\n\n"
            f"Confianza: {answer.get('confidence')}\n\n"
            f"Evidencia:\n{evidence}\n\n"
            f"Pendientes:\n{pending}"
        )

    def _best_sentence(self, text: str, terms: list[str]) -> str:
        sentences = re.split(r"(?<=[.!?])\s+|\n+", str(text or ""))
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

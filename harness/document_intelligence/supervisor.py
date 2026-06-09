from __future__ import annotations

import csv
import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, UTC
from pathlib import Path
from typing import Any

import pandas as pd
from PIL import Image
from pypdf import PdfReader
import pytesseract

from config import settings


SUPPORTED_EXTENSIONS = {"pdf", "png", "jpg", "jpeg", "csv", "xls", "xlsx", "txt", "md", "json"}
EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
RFC_RE = re.compile(r"\b[A-ZÑ&]{3,4}\d{6}[A-Z0-9]{3}\b", re.IGNORECASE)
PHONE_RE = re.compile(r"(?:\+?\d[\d\s().-]{7,}\d)")
MONEY_RE = re.compile(r"(?:\$|USD|USDT|MXN|Monto[:\s]*)\s*([0-9][0-9,]*(?:\.\d{1,2})?)", re.IGNORECASE)
DATE_RE = re.compile(r"\b(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}-\d{2}-\d{2})\b")
PROMPT_FIELD_ALIASES = {
    "cliente": ["cliente", "razon social", "razón social", "empresa", "nombre"],
    "correo": ["correo", "email", "e-mail"],
    "rfc": ["rfc"],
    "telefono": ["telefono", "teléfono", "phone", "celular"],
    "monto": ["monto", "importe", "total", "saldo", "precio"],
    "fecha": ["fecha"],
    "fecha_vencimiento": ["fecha de vencimiento", "vencimiento", "vence"],
    "folio": ["folio", "factura", "invoice", "numero de factura", "número de factura"],
    "concepto": ["concepto", "asunto", "descripcion", "descripción"],
    "riesgos": ["riesgo", "riesgos", "observaciones", "alertas"],
}
MAX_EXTRACTION_PROMPT_CHARS = 2000


@dataclass
class ExtractionResult:
    source_type: str
    text: str
    tables: list[dict[str, Any]]
    metadata: dict[str, Any]


class DocumentIntelligenceSupervisor:
    """Coordinates extraction, analysis, verification, and audit for documents."""

    def __init__(self, artifact_root: str | Path | None = None):
        self.root = Path(artifact_root or settings.artifact_root) / "document_intelligence"
        self.audit_dir = self.root / "audit"
        self.extracted_dir = self.root / "extracted"
        self.analysis_dir = self.root / "analysis"
        for path in (self.audit_dir, self.extracted_dir, self.analysis_dir):
            path.mkdir(parents=True, exist_ok=True)

    def process(
        self,
        path: str | Path,
        language: str = "spa",
        dry_run: bool = True,
        extraction_prompt: str | None = None,
    ) -> dict[str, Any]:
        file_path = Path(path).expanduser().resolve()
        if not file_path.exists():
            raise FileNotFoundError("document not found")
        ext = file_path.suffix.lower().lstrip(".")
        if ext not in SUPPORTED_EXTENSIONS:
            raise ValueError(f"unsupported document type: {ext}")

        document_id = str(uuid.uuid4())
        guidance_prompt = self._normalize_prompt(extraction_prompt)
        extraction = self.extract(file_path, ext, language)
        analysis = self.analyze(extraction, guidance_prompt)
        verification = self.verify(analysis)
        next_agent = self.next_agent(verification, analysis)
        result = {
            "document_id": document_id,
            "status": "ready_for_review" if verification["requires_human_review"] else "draft_ready",
            "source": {
                "path": str(file_path),
                "filename": file_path.name,
                "extension": ext,
                "size": file_path.stat().st_size,
            },
            "extraction": {
                "source_type": extraction.source_type,
                "metadata": extraction.metadata,
                "text_preview": extraction.text[:2500],
                "tables_count": len(extraction.tables),
            },
            "analysis": analysis,
            "verification": verification,
            "guidance": {
                "extraction_prompt": guidance_prompt,
                "requested_fields": analysis.get("requested_fields", []),
                "prompt_field_hits": analysis.get("prompt_field_hits", {}),
            },
            "next_agent": next_agent,
            "communication": self.email_draft(analysis, verification),
            "dry_run": bool(dry_run),
            "created_at": datetime.now(UTC).isoformat(),
        }
        self._write_artifacts(document_id, extraction, result)
        return result

    def extract(self, path: Path, ext: str, language: str) -> ExtractionResult:
        if ext in {"txt", "md"}:
            return ExtractionResult("text", path.read_text(errors="ignore"), [], {"pages": 0})
        if ext == "json":
            text = json.dumps(json.loads(path.read_text(errors="ignore")), indent=2, ensure_ascii=False)
            return ExtractionResult("json", text, [], {"records": 1})
        if ext == "csv":
            df = pd.read_csv(path)
            return self._frame_extraction(path, "csv", {"CSV": df})
        if ext in {"xls", "xlsx"}:
            sheets = pd.read_excel(path, sheet_name=None)
            return self._frame_extraction(path, "excel", sheets)
        if ext == "pdf":
            reader = PdfReader(str(path))
            pages = [(page.extract_text() or "") for page in reader.pages]
            text = "\n\n".join(page for page in pages if page.strip())
            return ExtractionResult("pdf", text, [], {"pages": len(reader.pages), "ocr_used": False})
        if ext in {"png", "jpg", "jpeg"}:
            image = Image.open(path)
            text = pytesseract.image_to_string(image, lang=language)
            return ExtractionResult("image_ocr", text, [], {"width": image.width, "height": image.height, "ocr_language": language})
        raise ValueError(f"unsupported document type: {ext}")

    def analyze(self, extraction: ExtractionResult, extraction_prompt: str | None = None) -> dict[str, Any]:
        text = extraction.text or ""
        tables_text = "\n".join(json.dumps(t.get("preview", []), ensure_ascii=False) for t in extraction.tables[:5])
        combined = f"{text}\n{tables_text}".strip()
        emails = sorted(set(EMAIL_RE.findall(combined)))
        rfcs = sorted({m.group(0).upper() for m in RFC_RE.finditer(combined)})
        phones = sorted({self._clean_space(m.group(0)) for m in PHONE_RE.finditer(combined)})
        amounts = [self._parse_money(m.group(1)) for m in MONEY_RE.finditer(combined)]
        dates = sorted(set(DATE_RE.findall(combined)))
        client = self._guess_client(combined, emails, rfcs)
        risks = []
        if not emails:
            risks.append("missing_email")
        if not client:
            risks.append("missing_client")
        if not combined:
            risks.append("empty_extraction")
        if extraction.source_type == "pdf" and not combined:
            risks.append("possible_scanned_pdf_requires_ocr")
        if len(emails) > 1:
            risks.append("multiple_emails")

        analysis = {
            "summary": self._summary(combined, extraction),
            "cliente": client,
            "correos": emails,
            "correo": emails[0] if len(emails) == 1 else None,
            "rfc": rfcs[0] if rfcs else None,
            "rfcs": rfcs,
            "telefonos": phones[:5],
            "monto": max(amounts) if amounts else None,
            "fechas": dates[:8],
            "concepto": self._guess_concept(combined),
            "estado_documento": "extraido" if combined else "sin_texto",
            "observaciones": self._observations(extraction),
            "riesgos": risks,
            "accion_recomendada": "generar_borrador" if not risks else "revision_humana",
        }
        requested_fields = self._requested_fields_from_prompt(extraction_prompt)
        prompt_field_hits = {
            field: self._field_has_value(analysis, field, combined)
            for field in requested_fields
        }
        if any(not found for found in prompt_field_hits.values()):
            analysis["riesgos"].append("missing_prompt_fields")
            analysis["accion_recomendada"] = "revision_humana"
        analysis["extraction_prompt"] = extraction_prompt
        analysis["requested_fields"] = requested_fields
        analysis["prompt_field_hits"] = prompt_field_hits
        return analysis

    def verify(self, analysis: dict[str, Any]) -> dict[str, Any]:
        missing = []
        if not analysis.get("cliente"):
            missing.append("cliente")
        if not analysis.get("correo"):
            missing.append("correo_unico")
        if analysis.get("estado_documento") != "extraido":
            missing.append("texto_extraido")
        prompt_missing = [
            field
            for field, found in (analysis.get("prompt_field_hits") or {}).items()
            if not found
        ]
        if prompt_missing:
            missing.append("prompt_fields")
        contradictions = []
        if len(analysis.get("correos") or []) > 1:
            contradictions.append("multiples_correos_detectados")
        score = 1.0
        score -= 0.25 * len(missing)
        score -= 0.15 * len(contradictions)
        score -= 0.10 * len(analysis.get("riesgos") or [])
        confidence = max(0.0, min(1.0, round(score, 2)))
        return {
            "confidence": confidence,
            "missing_fields": missing,
            "prompt_missing_fields": prompt_missing,
            "contradictions": contradictions,
            "requires_human_review": bool(missing or contradictions or confidence < 0.75),
            "safe_to_email": bool(confidence >= 0.85 and not missing and not contradictions),
        }

    def next_agent(self, verification: dict[str, Any], analysis: dict[str, Any]) -> str:
        if "texto_extraido" in verification.get("missing_fields", []):
            return "OCRAgent"
        if "cliente" in verification.get("missing_fields", []):
            return "ClientAgent"
        if verification.get("requires_human_review"):
            return "VerificationAgent"
        if analysis.get("correo"):
            return "EmailDraftAgent"
        return "AuditAgent"

    def email_draft(self, analysis: dict[str, Any], verification: dict[str, Any]) -> dict[str, Any]:
        if not analysis.get("correo"):
            return {"status": "blocked", "reason": "correo no identificado"}
        subject = "Seguimiento de documento recibido"
        body = (
            f"Hola {analysis.get('cliente') or ''},\n\n"
            "Recibimos y revisamos el documento enviado.\n\n"
            f"Resumen: {analysis.get('summary')}\n"
            f"Monto detectado: {analysis.get('monto') if analysis.get('monto') is not None else 'no detectado'}\n"
            f"Concepto: {analysis.get('concepto') or 'no detectado'}\n\n"
            "Quedamos atentos para cualquier aclaración.\n"
        )
        return {
            "status": "draft_only" if not verification.get("safe_to_email") else "ready_to_send_with_approval",
            "to": analysis.get("correo"),
            "subject": subject,
            "body": body,
            "auto_send_enabled": False,
        }

    def _frame_extraction(self, path: Path, source_type: str, sheets: dict[str, pd.DataFrame]) -> ExtractionResult:
        tables = []
        text_parts = []
        for sheet, df in sheets.items():
            safe_df = df.fillna("")
            preview = safe_df.head(20).to_dict(orient="records")
            tables.append({
                "name": str(sheet),
                "rows": int(len(df)),
                "columns": [str(c) for c in df.columns],
                "preview": preview,
            })
            text_parts.append(f"Hoja {sheet}: {len(df)} filas x {len(df.columns)} columnas")
            text_parts.append(safe_df.head(50).to_csv(index=False, quoting=csv.QUOTE_MINIMAL))
        return ExtractionResult(source_type, "\n".join(text_parts), tables, {"sheets": list(sheets), "filename": path.name})

    def _write_artifacts(self, document_id: str, extraction: ExtractionResult, result: dict[str, Any]) -> None:
        (self.extracted_dir / f"{document_id}.txt").write_text(extraction.text or "", encoding="utf-8")
        (self.analysis_dir / f"{document_id}.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        audit = {
            "document_id": document_id,
            "created_at": result["created_at"],
            "status": result["status"],
            "next_agent": result["next_agent"],
            "confidence": result["verification"]["confidence"],
            "safe_to_email": result["verification"]["safe_to_email"],
            "source": result["source"],
            "prompt_provided": bool((result.get("guidance") or {}).get("extraction_prompt")),
            "requested_fields": (result.get("guidance") or {}).get("requested_fields", []),
        }
        path = self.audit_dir / f"{datetime.now(UTC).date().isoformat()}.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(audit, ensure_ascii=False) + "\n")

    def _summary(self, text: str, extraction: ExtractionResult) -> str:
        if not text:
            return "No se pudo extraer texto legible del documento."
        first = self._clean_space(text[:500])
        if extraction.tables:
            return f"Documento tabular con {len(extraction.tables)} tabla(s). {first[:240]}"
        return first[:300]

    def _observations(self, extraction: ExtractionResult) -> list[str]:
        items = [f"source_type={extraction.source_type}"]
        if extraction.tables:
            items.append(f"tables={len(extraction.tables)}")
        for key, value in extraction.metadata.items():
            items.append(f"{key}={value}")
        return items

    def _guess_client(self, text: str, emails: list[str], rfcs: list[str]) -> str | None:
        patterns = [
            r"(?:cliente|raz[oó]n social|nombre)\s*[:\-]\s*([^\n\r,;]{3,120})",
            r"(?:empresa)\s*[:\-]\s*([^\n\r,;]{3,120})",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                return self._clean_space(match.group(1))
        if emails:
            local = emails[0].split("@", 1)[0]
            return self._clean_space(re.sub(r"[._-]+", " ", local)).title()
        if rfcs:
            return rfcs[0]
        return None

    def _guess_concept(self, text: str) -> str | None:
        match = re.search(r"(?:concepto|asunto|descripci[oó]n)\s*[:\-]\s*([^\n\r]{3,180})", text, flags=re.IGNORECASE)
        return self._clean_space(match.group(1)) if match else None

    def _parse_money(self, value: str) -> float:
        return float(value.replace(",", ""))

    def _clean_space(self, value: str) -> str:
        return " ".join(str(value or "").strip().split())

    def _normalize_prompt(self, prompt: str | None) -> str | None:
        if prompt is None:
            return None
        cleaned = self._clean_space(prompt)
        if not cleaned:
            return None
        return cleaned[:MAX_EXTRACTION_PROMPT_CHARS]

    def _requested_fields_from_prompt(self, prompt: str | None) -> list[str]:
        if not prompt:
            return []
        lowered = prompt.lower()
        requested = [
            field
            for field, aliases in PROMPT_FIELD_ALIASES.items()
            if any(alias in lowered for alias in aliases)
        ]
        requested_set = set(requested)
        if "fecha_vencimiento" in requested_set:
            requested_set.discard("fecha")
        return sorted(requested_set)

    def _field_has_value(self, analysis: dict[str, Any], field: str, text: str) -> bool:
        if field == "telefono":
            return bool(analysis.get("telefonos"))
        if field == "fecha":
            return bool(analysis.get("fechas"))
        if field == "fecha_vencimiento":
            return bool(re.search(r"(vencimiento|vence|due date)", text, flags=re.IGNORECASE) and analysis.get("fechas"))
        if field == "folio":
            return bool(re.search(r"(folio|factura|invoice|n[uú]mero)", text, flags=re.IGNORECASE))
        if field == "riesgos":
            return bool(analysis.get("riesgos") or analysis.get("observaciones"))
        return analysis.get(field) not in (None, "", [], {})

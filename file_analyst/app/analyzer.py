from __future__ import annotations

import json
import logging
import re
from collections import Counter
from typing import Any

import requests

from .config import settings
from .models import ActionItem, AnalysisResponse, Observation

logger = logging.getLogger(__name__)


SYSTEM_PROMPTS = {
    "base": """
Eres File Analyst de QuantLabs. Analiza documentos privados usando salida JSON estricta.
No uses markdown ni texto fuera del JSON.
Devuelve exactamente:
{
  "summary": "Resumen ejecutivo en 2-3 oraciones.",
  "interpretation": "Tipo de documento, propósito, audiencia y contexto.",
  "observations": [{"type":"risk|note|inconsistency|highlight","severity":"high|medium|low|info","detail":"..."}],
  "conclusions": ["..."],
  "action_plan": [{"priority":1,"action":"...","responsible":"...","success_criteria":"..."}]
}
Prioriza hechos del documento. Si falta información, dilo como riesgo o nota. Responde en el idioma solicitado.
""",
    "specialist": """
Eres File Analyst senior de QuantLabs para documentos legales, financieros, técnicos y operativos.
Analiza riesgos, ambigüedades, inconsistencias, obligaciones, responsables, fechas, montos y acciones.
Devuelve JSON estricto con mínimo 5 observaciones, 5 conclusiones y 5 acciones cuando el contenido lo permita.
""",
    "automata": """
Eres File Analyst en modo automata. Produce JSON estable, breve y accionable para procesos batch.
Devuelve solo JSON válido.
""",
}


def analyze_document(doc_data: dict[str, Any], mode: str = "chatbot", language: str = "es") -> AnalysisResponse:
    text = str(doc_data.get("raw_text") or "")
    structure = doc_data.get("structure") or {}
    file_type = doc_data.get("file_type", "unknown")
    word_count = int(structure.get("word_count") or len(text.split()))

    if word_count < 10:
        return AnalysisResponse(
            summary="Documento vacío o con contenido insuficiente para análisis.",
            interpretation="No se pudo determinar el propósito del documento.",
            observations=[Observation(type="note", severity="info", detail="El documento no contiene texto analizable suficiente.")],
            conclusions=["El documento no contiene suficiente información."],
            action_plan=[ActionItem(priority=1, action="Verificar que el archivo cargado sea correcto y tenga contenido.")],
            metadata={"word_count": word_count, "file_type": file_type, "analysis_engine": "extractive_fallback"},
        )

    chunks = _split_chunks(text)
    partials = []
    for index, chunk in enumerate(chunks, start=1):
        prompt = _build_prompt(chunk, structure, file_type, word_count, index, len(chunks), mode, language)
        partials.append(_analyze_chunk(prompt, chunk, mode))

    merged = _synthesize_chunks(partials)
    return _response_from_dict(
        merged,
        metadata={
            "word_count": word_count,
            "chunk_count": len(chunks),
            "file_type": file_type,
            "structure": {k: v for k, v in structure.items() if k != "sections"},
            "analysis_engine": merged.get("_engine", "local_llm"),
            "model": merged.get("_model") or settings.model_name,
        },
    )


def _build_prompt(chunk: str, structure: dict[str, Any], file_type: str, word_count: int, index: int, total: int, mode: str, language: str) -> str:
    section_note = ""
    sections = structure.get("sections") or []
    if index == 1 and sections:
        headings = ", ".join(str(item.get("heading") or "") for item in sections[:8] if isinstance(item, dict))
        section_note = f"\nEstructura detectada: {len(sections)} secciones. Primeras: {headings}"
    return (
        f"Documento tipo {file_type}, {word_count} palabras, chunk {index}/{total}.\n"
        f"Modo: {mode}. Idioma de salida: {language}.{section_note}\n\n"
        "Analiza el siguiente contenido y devuelve solo JSON estricto:\n"
        f"---\n{chunk}\n---"
    )


def _analyze_chunk(prompt: str, chunk: str, mode: str) -> dict[str, Any]:
    if settings.llm_enabled:
        try:
            raw = _call_local_llm(prompt, mode)
            parsed = _parse_json_response(raw)
            if not str(parsed.get("summary") or "").strip() or not parsed.get("conclusions"):
                raise ValueError("LLM JSON incompleto")
            parsed["_engine"] = "local_llm"
            parsed["_model"] = settings.model_name
            return parsed
        except Exception as exc:
            logger.warning("LLM analysis failed; using fallback: %s", str(exc)[:200])
    fallback = _extractive_analysis(chunk)
    fallback["_engine"] = "extractive_fallback"
    return fallback


def _call_local_llm(prompt: str, mode: str) -> str:
    system = SYSTEM_PROMPTS.get(mode, SYSTEM_PROMPTS["base"])
    if mode not in {"specialist", "automata"}:
        system = SYSTEM_PROMPTS["base"]
    payload = {
        "model": settings.model_name,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "temperature": settings.temperature,
        "max_tokens": settings.max_tokens,
        "stream": False,
    }
    response = requests.post(settings.llm_api_url, json=payload, timeout=settings.llm_timeout_seconds)
    response.raise_for_status()
    data = response.json()
    return data["choices"][0]["message"]["content"].strip()


def _parse_json_response(raw: str) -> dict[str, Any]:
    clean = re.sub(r"```json|```", "", str(raw or "")).strip()
    try:
        return json.loads(clean)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]+\}", clean)
        if match:
            return json.loads(match.group(0))
    raise ValueError("El LLM no devolvió JSON válido.")


def _split_chunks(text: str) -> list[str]:
    if len(text) <= settings.chunk_size:
        return [text]
    paragraphs = re.split(r"\n\n+", text)
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if len(current) + len(paragraph) + 2 <= settings.chunk_size:
            current = (current + "\n\n" + paragraph).strip()
            continue
        if current:
            chunks.append(current)
        if len(paragraph) > settings.chunk_size:
            step = max(1000, settings.chunk_size - settings.chunk_overlap)
            chunks.extend(paragraph[i:i + settings.chunk_size] for i in range(0, len(paragraph), step))
            current = ""
        else:
            current = paragraph
    if current:
        chunks.append(current)
    return chunks[: settings.max_chunks]


def _synthesize_chunks(results: list[dict[str, Any]]) -> dict[str, Any]:
    if len(results) == 1:
        return results[0]
    observations = _dedupe_dicts([item for result in results for item in result.get("observations", [])], "detail")
    conclusions = _dedupe_strings([item for result in results for item in result.get("conclusions", [])])
    actions = _dedupe_dicts([item for result in results for item in result.get("action_plan", [])], "action")
    for index, action in enumerate(actions, start=1):
        action["priority"] = index
    summary = " ".join(str(result.get("summary") or "") for result in results[:2]).strip()
    return {
        "summary": summary[:700] or "Análisis consolidado de documento.",
        "interpretation": results[0].get("interpretation") or "Documento procesado por partes.",
        "observations": observations[:20],
        "conclusions": conclusions[:20],
        "action_plan": actions[:20],
        "_engine": "local_llm" if any(result.get("_engine") == "local_llm" for result in results) else "extractive_fallback",
        "_model": next((result.get("_model") for result in results if result.get("_model")), settings.model_name),
    }


def _response_from_dict(data: dict[str, Any], metadata: dict[str, Any]) -> AnalysisResponse:
    observations = [
        Observation(type=str(item.get("type") or "note"), severity=str(item.get("severity") or "info"), detail=str(item.get("detail") or ""))
        for item in data.get("observations", [])
        if isinstance(item, dict) and str(item.get("detail") or "").strip()
    ]
    actions = [
        ActionItem(
            priority=int(item.get("priority") or index + 1),
            action=str(item.get("action") or ""),
            responsible=item.get("responsible"),
            success_criteria=item.get("success_criteria"),
        )
        for index, item in enumerate(data.get("action_plan", []))
        if isinstance(item, dict) and str(item.get("action") or "").strip()
    ]
    return AnalysisResponse(
        summary=str(data.get("summary") or "Sin resumen disponible."),
        interpretation=str(data.get("interpretation") or "Sin interpretación disponible."),
        observations=observations or [Observation(type="note", severity="info", detail="No se detectaron observaciones específicas.")],
        conclusions=[str(item) for item in data.get("conclusions", []) if str(item).strip()] or ["No se detectaron conclusiones específicas."],
        action_plan=sorted(actions, key=lambda item: item.priority) or [ActionItem(priority=1, action="Revisar el documento manualmente.")],
        metadata=metadata,
    )


def _extractive_analysis(text: str) -> dict[str, Any]:
    sentences = _sentences(text)
    keyword_hits = _keyword_hits(text)
    observations = []
    for severity, keywords in (
        ("high", ["penalización", "incumplimiento", "mora", "confidencialidad", "responsabilidad", "terminación", "riesgo"]),
        ("medium", ["plazo", "pago", "obligación", "entrega", "garantía", "auditoría"]),
    ):
        for sentence in sentences:
            if any(word in sentence.lower() for word in keywords):
                observations.append({"type": "risk" if severity == "high" else "note", "severity": severity, "detail": sentence[:500]})
                break
    if not observations:
        observations.append({"type": "note", "severity": "info", "detail": "Análisis extractivo generado porque el LLM local no respondió con JSON válido."})
    top_terms = ", ".join(term for term, _ in keyword_hits[:8]) or "sin términos dominantes"
    return {
        "summary": " ".join(sentences[:3])[:700] or "Documento procesado con análisis extractivo.",
        "interpretation": f"Documento con {len(text.split())} palabras. Términos dominantes: {top_terms}.",
        "observations": observations[:8],
        "conclusions": sentences[:5] or ["No se detectó contenido suficiente para conclusiones."],
        "action_plan": [
            {"priority": 1, "action": "Validar las observaciones marcadas por File Analyst.", "responsible": "Responsable del documento", "success_criteria": "Riesgos confirmados o descartados."},
            {"priority": 2, "action": "Solicitar revisión especializada si el documento tiene impacto legal, financiero u operativo.", "responsible": "Área dueña", "success_criteria": "Revisión documentada."},
        ],
    }


def _sentences(text: str) -> list[str]:
    cleaned = re.sub(r"\s+", " ", text).strip()
    parts = re.split(r"(?<=[.!?])\s+", cleaned)
    return [part.strip() for part in parts if len(part.split()) >= 6][:20]


def _keyword_hits(text: str):
    words = re.findall(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]{4,}", text.lower())
    stop = {"para", "como", "este", "esta", "estos", "estas", "documento", "entre", "sobre", "donde", "cuando", "porque", "tiene", "será", "sera"}
    return Counter(word for word in words if word not in stop).most_common(20)


def _dedupe_strings(values: list[Any]) -> list[str]:
    seen = set()
    output = []
    for value in values:
        text = str(value or "").strip()
        key = text[:100].lower()
        if text and key not in seen:
            output.append(text)
            seen.add(key)
    return output


def _dedupe_dicts(values: list[dict[str, Any]], key_name: str) -> list[dict[str, Any]]:
    seen = set()
    output = []
    for value in values:
        if not isinstance(value, dict):
            continue
        key = str(value.get(key_name) or value)[:100].lower()
        if key and key not in seen:
            output.append(value)
            seen.add(key)
    return output

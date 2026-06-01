"""file_analyst — QuantLabs private document analysis microservice."""

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import tempfile
import os
import time
import logging

from .extractor import extract_document
from .analyzer import analyze_document
from .models import AnalysisResponse, HealthResponse, StatusResponse
from .config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(
    title="file_analyst — QuantLabs Document Analyst",
    description="Private document pipeline: Extraction → Structure → Local LLM Analysis → Findings → Action Plan → JSON",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

SUPPORTED_EXTENSIONS = {
    ".docx": "word",
    ".pdf":  "pdf",
    ".txt":  "text",
    ".xls":  "excel",
    ".xlsx": "excel",
    ".csv":  "csv",
    ".json": "json",
    ".md": "text",
}


@app.get("/", response_model=HealthResponse, tags=["Health"])
async def root():
    return HealthResponse(
        status="ok",
        skill="file_analyst",
        version="1.0.0",
        supported_formats=list(SUPPORTED_EXTENSIONS.keys()),
        modes=["chatbot", "live", "automata", "specialist"],
    )


@app.get("/health", response_model=HealthResponse, tags=["Health"])
async def health():
    return HealthResponse(
        status="ok",
        skill="file_analyst",
        version="1.0.0",
        supported_formats=list(SUPPORTED_EXTENSIONS.keys()),
        modes=["chatbot", "live", "automata", "specialist"],
    )


@app.post("/analyze", response_model=AnalysisResponse, tags=["Analysis"])
async def analyze(
    file: UploadFile = File(..., description="Documento a analizar"),
    mode: str = "chatbot",
    language: str = "es",
):
    """
    Pipeline completo: recibe un archivo, extrae contenido,
    analiza con IA y devuelve JSON estructurado.
    """
    start = time.time()

    # Validar extensión
    _, ext = os.path.splitext(file.filename.lower())
    if ext not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=415,
            detail=f"Formato '{ext}' no soportado. Use: {list(SUPPORTED_EXTENSIONS.keys())}",
        )

    content = await file.read()
    max_bytes = settings.max_upload_mb * 1024 * 1024
    if len(content) > max_bytes:
        raise HTTPException(status_code=413, detail=f"Archivo demasiado grande. Máximo {settings.max_upload_mb} MB.")

    logger.info(f"[{mode}] Procesando: {file.filename} ({len(content):,} bytes)")

    # Guardar temp y procesar
    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        # ── Stage 1 & 2: Extraction + Structure Analysis
        doc_data = extract_document(tmp_path, SUPPORTED_EXTENSIONS[ext])

        # ── Stages 3-6: Interpretation → Findings → Action Plan → Output
        result = analyze_document(doc_data, mode=mode, language=language)

    finally:
        os.unlink(tmp_path)

    elapsed = round(time.time() - start, 2)
    result.metadata.update({
        "filename": file.filename,
        "format": ext,
        "mode": mode,
        "processing_time_s": elapsed,
    })

    logger.info(f"[{mode}] Completado en {elapsed}s → {file.filename}")
    return result


@app.post("/analyze/text", response_model=AnalysisResponse, tags=["Analysis"])
async def analyze_text(body: dict):
    """
    Análisis directo de texto plano (útil para Chatbot/Live sin adjunto).
    Body: { "text": "...", "mode": "chatbot", "language": "es" }
    """
    text = body.get("text", "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Campo 'text' requerido y no puede estar vacío.")

    mode = body.get("mode", "chatbot")
    language = body.get("language", "es")

    doc_data = {
        "raw_text": text,
        "structure": {"sections": [], "word_count": len(text.split()), "char_count": len(text)},
        "file_type": "text",
    }

    result = analyze_document(doc_data, mode=mode, language=language)
    result.metadata["mode"] = mode
    return result


if __name__ == "__main__":
    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=settings.reload)

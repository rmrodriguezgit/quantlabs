"""
Modelos de datos para el Document Skill.
"""

from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any


class HealthResponse(BaseModel):
    status: str
    skill: str
    version: str
    supported_formats: List[str]
    modes: List[str]


class StatusResponse(BaseModel):
    status: str
    message: str


class Observation(BaseModel):
    type: str = Field(..., description="risk | note | inconsistency | highlight")
    severity: str = Field(..., description="high | medium | low | info")
    detail: str


class ActionItem(BaseModel):
    priority: int = Field(..., description="1 = más urgente")
    action: str
    responsible: Optional[str] = None
    success_criteria: Optional[str] = None


class AnalysisResponse(BaseModel):
    # Stage 3 — Interpretation
    summary: str = Field(..., description="Resumen ejecutivo del documento")
    interpretation: str = Field(..., description="Propósito, audiencia y contexto detectados")

    # Stage 4 — Findings
    observations: List[Observation] = Field(default_factory=list)
    conclusions: List[str] = Field(default_factory=list, description="Puntos clave finales")

    # Stage 5 — Action Plan
    action_plan: List[ActionItem] = Field(default_factory=list)

    # Stage 6 — Metadata / Output Formatter
    metadata: Dict[str, Any] = Field(default_factory=dict)

    class Config:
        json_schema_extra = {
            "example": {
                "summary": "Contrato de prestación de servicios entre Empresa A y Empresa B por 12 meses.",
                "interpretation": "Documento legal de tipo contractual. Audiencia: equipos legal y directivo.",
                "observations": [
                    {"type": "risk", "severity": "high", "detail": "Cláusula de penalización no especifica monto máximo."}
                ],
                "conclusions": ["Las partes acuerdan confidencialidad por 5 años.", "Pago mensual de $10,000 USD."],
                "action_plan": [
                    {"priority": 1, "action": "Revisar cláusula 8.3 con asesor legal", "responsible": "Legal", "success_criteria": "Monto de penalización acotado y aprobado"}
                ],
                "metadata": {"filename": "contrato.docx", "format": ".docx", "mode": "specialist", "processing_time_s": 2.4},
            }
        }

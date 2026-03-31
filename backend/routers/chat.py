"""
APEX Chat Router — /api/chat endpoints.

POST /api/chat/query    — Ask APEX IA about analysis results (fully local)
GET  /api/chat/status   — Check service status
POST /api/chat/unload   — No-op (kept for API compat)
"""

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional

logger = logging.getLogger("apex.chat.router")
router = APIRouter()

_service = None


def _get_service():
    global _service
    if _service is None:
        from ..services import local_chat_service
        _service = local_chat_service
    return _service


class ChatRequest(BaseModel):
    question: str = Field(..., description="Pregunta en texto libre")
    job_id: Optional[str] = Field(None, description="Job ID para contexto")
    job_results: Optional[dict] = Field(None, description="Resultados del análisis")
    image_base64: Optional[str] = Field(None, description="Imagen base64 (opcional)")


class ChatResponse(BaseModel):
    answer: str
    model: str
    mode: str
    context_used: bool


@router.post("/chat/query", response_model=ChatResponse)
async def chat_query(req: ChatRequest):
    """Ask APEX IA about analysis results (fully local)."""
    if not req.question or not req.question.strip():
        raise HTTPException(400, "La pregunta no puede estar vacía")

    svc = _get_service()
    try:
        result = svc.chat_query(
            question=req.question.strip(),
            job_id=req.job_id,
            job_results=req.job_results,
            image_base64=req.image_base64,
        )
        return ChatResponse(**result)
    except Exception as exc:
        logger.error("Chat query failed: %s", exc)
        raise HTTPException(500, f"Error en consulta: {exc}")


@router.get("/chat/status")
async def chat_status():
    """Return APEX IA service status."""
    svc = _get_service()
    try:
        return svc.get_status()
    except Exception as exc:
        logger.error("Status check failed: %s", exc)
        raise HTTPException(500, str(exc))


@router.post("/chat/unload")
async def chat_unload():
    """No-op — kept for API compatibility."""
    svc = _get_service()
    try:
        svc.unload_model()
        return {"status": "ok", "message": "APEX IA local — sin modelo que descargar"}
    except Exception as exc:
        logger.error("Unload failed: %s", exc)
        raise HTTPException(500, str(exc))

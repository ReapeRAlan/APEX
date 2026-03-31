"""
TEOChat Service — Temporal Earth Observation Conversational AI.

Provides natural-language Q&A over satellite imagery analysis results
using a vision-language model.  Uses TEOChat (LLaVA-based, temporal-aware)
with 4-bit quantization for RTX 4050 (5.5GB VRAM).

Reference: TEOChat: Large Language and Vision Assistant for Temporal
           Earth Observation Data (Wang et al., 2024)

The model auto-downloads from HuggingFace on first use.
Requires: torch, transformers, bitsandbytes, accelerate, pillow
"""
from __future__ import annotations

import base64
import io
import logging
import os
import json
from pathlib import Path

import numpy as np

logger = logging.getLogger("apex.teochat")

MODEL_ID = "ByteDance/TEOChat-7B"
DEVICE = "cuda"
MAX_NEW_TOKENS = 512
TEMPERATURE = 0.7

_model = None
_tokenizer = None
_processor = None
_loaded = False


def _ensure_model():
    """Lazy-load the TEOChat model with 4-bit quantization."""
    global _model, _tokenizer, _processor, _loaded
    if _loaded:
        return

    import torch
    if not torch.cuda.is_available():
        logger.warning("CUDA not available, TEOChat requires GPU")
        raise RuntimeError("TEOChat requiere GPU con CUDA")

    try:
        from transformers import (
            AutoModelForCausalLM,
            AutoTokenizer,
            AutoProcessor,
            BitsAndBytesConfig,
        )
    except ImportError:
        raise RuntimeError(
            "Instala dependencias: pip install transformers bitsandbytes accelerate"
        )

    logger.info("Loading TEOChat model %s (4-bit quantization)...", MODEL_ID)

    # Use HF token for gated models
    from ..config import settings
    hf_token = settings.HF_TOKEN or os.environ.get("HF_TOKEN") or None

    bnb_config = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.float16,
        bnb_4bit_use_double_quant=True,
    )

    try:
        _tokenizer = AutoTokenizer.from_pretrained(
            MODEL_ID, trust_remote_code=True, token=hf_token
        )
        _processor = AutoProcessor.from_pretrained(
            MODEL_ID, trust_remote_code=True, token=hf_token
        )
        _model = AutoModelForCausalLM.from_pretrained(
            MODEL_ID,
            quantization_config=bnb_config,
            device_map="auto",
            trust_remote_code=True,
            torch_dtype=torch.float16,
            token=hf_token,
        )
        _model.eval()
        _loaded = True
        logger.info("TEOChat loaded successfully (4-bit quantized)")
    except Exception as exc:
        logger.error("Failed to load TEOChat: %s", exc)
        _model = None
        _loaded = False
        raise RuntimeError(f"Error al cargar TEOChat: {exc}")


def unload_model():
    """Free GPU memory by unloading the model."""
    global _model, _tokenizer, _processor, _loaded
    if _model is not None:
        del _model
        _model = None
    if _tokenizer is not None:
        del _tokenizer
        _tokenizer = None
    if _processor is not None:
        del _processor
        _processor = None
    _loaded = False

    import torch
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    logger.info("TEOChat model unloaded")


def _build_context_from_results(job_results: dict) -> str:
    """Build a textual context summary from analysis results."""
    parts = []

    if "deforestation" in job_results:
        stats = job_results["deforestation"].get("stats", {})
        parts.append(
            f"Deforestación detectada: {stats.get('area_ha', 0)} ha, "
            f"{stats.get('n_features', 0)} polígonos."
        )

    if "biomass" in job_results:
        stats = job_results["biomass"]
        parts.append(
            f"Impacto en biomasa: {stats.get('total_co2_tonnes', 0):.1f} toneladas CO₂, "
            f"biomasa media: {stats.get('mean_agbd_mg_ha', 0):.1f} Mg/ha."
        )

    if "vegetation" in job_results:
        stats = job_results["vegetation"].get("stats", {})
        classes = stats.get("classes", {})
        if classes:
            top_classes = sorted(classes.items(), key=lambda x: x[1], reverse=True)[:5]
            summary = ", ".join(f"{k}: {v}%" for k, v in top_classes)
            parts.append(f"Vegetación: {summary}.")

    if "hansen" in job_results:
        stats = job_results["hansen"].get("stats", {})
        parts.append(
            f"Hansen Forest Loss: {stats.get('total_loss_ha', 0)} ha perdidas, "
            f"cobertura original: {stats.get('avg_treecover_pct', 0)}%."
        )

    if "fire" in job_results:
        stats = job_results["fire"].get("stats", {})
        parts.append(
            f"Incendios: {stats.get('total_burned_ha', 0)} ha quemadas, "
            f"{stats.get('fire_count', 0)} eventos."
        )

    if "alerts" in job_results:
        stats = job_results["alerts"].get("stats", {})
        parts.append(
            f"Alertas: {stats.get('total_alerts', 0)} alertas "
            f"({stats.get('glad_count', 0)} GLAD, {stats.get('radd_count', 0)} RADD)."
        )

    if "avocado" in job_results:
        stats = job_results["avocado"].get("stats", {})
        parts.append(
            f"Anomalías NDVI: {stats.get('n_anomalies', 0)} zonas anómalas, "
            f"{stats.get('total_area_ha', 0)} ha."
        )

    if "firms_hotspots" in job_results:
        stats = job_results["firms_hotspots"].get("stats", {})
        parts.append(
            f"FIRMS hotspots: {stats.get('hotspot_count', 0)} detecciones NRT."
        )

    if not parts:
        return "No hay resultados de análisis disponibles."

    return " ".join(parts)


def chat_query(
    question: str,
    job_id: str | None = None,
    job_results: dict | None = None,
    image_base64: str | None = None,
) -> dict:
    """
    Ask TEOChat a question about the analysis results.

    Parameters
    ----------
    question : Natural language question in Spanish or English
    job_id : Optional job ID for context
    job_results : Optional dict of engine results for context
    image_base64 : Optional base64-encoded image for visual Q&A

    Returns
    -------
    dict with 'answer', 'model', 'context_used'
    """
    # Build context
    context_parts = []
    if job_results:
        context_parts.append(_build_context_from_results(job_results))

    context = " ".join(context_parts) if context_parts else ""

    # First try with the VLM model
    try:
        _ensure_model()
        answer = _query_vlm(question, context, image_base64)
        return {
            "answer": answer,
            "model": MODEL_ID,
            "mode": "vlm",
            "context_used": bool(context),
        }
    except Exception as exc:
        logger.warning("VLM query failed: %s. Using fallback.", exc)
        # Fallback: generate a structured response from the context
        answer = _fallback_response(question, context)
        return {
            "answer": answer,
            "model": "fallback-contextual",
            "mode": "fallback",
            "context_used": bool(context),
        }


def _query_vlm(question: str, context: str, image_base64: str | None) -> str:
    """Query the VLM model."""
    import torch

    system_prompt = (
        "Eres un experto en percepción remota y análisis ambiental para PROFEPA México. "
        "Responde en español de manera técnica pero accesible. "
        "Basa tu respuesta en los datos proporcionados."
    )

    if context:
        prompt = (
            f"{system_prompt}\n\n"
            f"Datos del análisis:\n{context}\n\n"
            f"Pregunta: {question}\n\n"
            f"Respuesta:"
        )
    else:
        prompt = f"{system_prompt}\n\nPregunta: {question}\n\nRespuesta:"

    inputs = _tokenizer(prompt, return_tensors="pt").to(_model.device)

    with torch.no_grad():
        outputs = _model.generate(
            **inputs,
            max_new_tokens=MAX_NEW_TOKENS,
            temperature=TEMPERATURE,
            do_sample=True,
            top_p=0.9,
        )

    full_text = _tokenizer.decode(outputs[0], skip_special_tokens=True)

    # Extract only the answer part
    if "Respuesta:" in full_text:
        answer = full_text.split("Respuesta:")[-1].strip()
    else:
        answer = full_text[len(prompt):].strip()

    return answer


def _fallback_response(question: str, context: str) -> str:
    """Generate a structured response without the VLM model."""
    q_lower = question.lower()

    if not context:
        return (
            "No hay datos de análisis disponibles. Ejecuta primero un análisis "
            "en la pestaña de análisis para obtener resultados que pueda interpretar."
        )

    if any(w in q_lower for w in ["resumen", "resúmen", "resuma", "summary"]):
        return f"Resumen del análisis:\n\n{context}"

    if any(w in q_lower for w in ["deforest", "bosque", "forest", "pérdida"]):
        lines = [l for l in context.split(". ") if any(w in l.lower() for w in ["deforest", "hansen", "bosque"])]
        if lines:
            return ". ".join(lines) + "."
        return "No se detectó deforestación significativa en este análisis."

    if any(w in q_lower for w in ["incendio", "fuego", "fire", "quem"]):
        lines = [l for l in context.split(". ") if any(w in l.lower() for w in ["incendio", "quemad", "fire", "firms"])]
        if lines:
            return ". ".join(lines) + "."
        return "No se detectaron incendios significativos en este análisis."

    if any(w in q_lower for w in ["co2", "carbono", "biomasa", "carbon"]):
        lines = [l for l in context.split(". ") if any(w in l.lower() for w in ["co2", "carbono", "biomasa"])]
        if lines:
            return ". ".join(lines) + "."
        return "No hay datos de biomasa/CO₂ disponibles."

    if any(w in q_lower for w in ["alerta", "alert", "glad", "radd"]):
        lines = [l for l in context.split(". ") if any(w in l.lower() for w in ["alerta", "glad", "radd"])]
        if lines:
            return ". ".join(lines) + "."

    # General response
    return (
        f"Basándome en los datos del análisis:\n\n{context}\n\n"
        f"Para una respuesta más detallada sobre \"{question}\", "
        f"el modelo VLM TEOChat necesita estar cargado (requiere GPU)."
    )


def get_status() -> dict:
    """Return TEOChat service status."""
    import torch
    gpu_available = torch.cuda.is_available()
    gpu_name = torch.cuda.get_device_name(0) if gpu_available else None
    gpu_mem_gb = round(torch.cuda.get_device_properties(0).total_memory / 1e9, 1) if gpu_available else 0

    return {
        "service": "teochat",
        "model": MODEL_ID,
        "loaded": _loaded,
        "gpu_available": gpu_available,
        "gpu_name": gpu_name,
        "gpu_memory_gb": gpu_mem_gb,
        "quantization": "4-bit (nf4)" if _loaded else "N/A",
    }

"""
APEX Forecast Router — /api/forecast endpoints (v2).

POST /api/forecast/run     — Forecast from timeline results
POST /api/forecast/aoi     — Legacy alias (redirects to /run)
POST /api/forecast/train   — Train/retrain the ML model
GET  /api/forecast/status  — Check engine status & data info
"""

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional

logger = logging.getLogger("apex.forecast.router")
router = APIRouter()

# Lazy-load forecast engine to avoid import errors if deps missing
_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        from ..services import forecast_engine
        _engine = forecast_engine
    return _engine


# ── Request schemas ──

class ForecastRunRequest(BaseModel):
    job_id: Optional[str] = Field(None, description="Timeline job ID (auto-detects latest if omitted)")
    horizon: int = Field(3, ge=1, le=5, description="Years ahead (1-5)")
    method: str = Field("ensemble", description="trend | ml | pomdp | convlstm | ensemble")


class ForecastAOIRequest(BaseModel):
    aoi: Optional[dict] = Field(None, description="GeoJSON geometry (ignored in v2)")
    job_id: Optional[str] = Field(None, description="Timeline job ID")
    horizon: int = Field(3, ge=1, le=5, description="Years ahead (1-5)")
    method: str = Field("ensemble", description="trend | ml | pomdp | convlstm | ensemble")


# ── Endpoints ──

@router.post("/forecast/run")
async def forecast_run(req: ForecastRunRequest):
    """Run forecast using data from a completed timeline analysis."""
    engine = _get_engine()
    if engine is None:
        raise HTTPException(503, "Forecast engine not available")

    try:
        result = engine.forecast_from_timeline(req.job_id, req.horizon, req.method)
        return result
    except Exception as exc:
        logger.error("forecast_run failed: %s", exc, exc_info=True)
        raise HTTPException(500, f"Forecast error: {exc}")


@router.post("/forecast/aoi")
async def forecast_aoi(req: ForecastAOIRequest):
    """Legacy endpoint — redirects to forecast_from_timeline."""
    engine = _get_engine()
    if engine is None:
        raise HTTPException(503, "Forecast engine not available")

    try:
        result = engine.forecast_from_timeline(req.job_id, req.horizon, req.method)
        return result
    except Exception as exc:
        logger.error("forecast_aoi failed: %s", exc, exc_info=True)
        raise HTTPException(500, f"Forecast error: {exc}")


@router.post("/forecast/train")
async def train_model():
    """Train or retrain the RandomForest model on timeline data."""
    engine = _get_engine()
    if engine is None:
        raise HTTPException(503, "Forecast engine not available")

    try:
        result = engine.train_rf_model()
        return result
    except Exception as exc:
        logger.error("train_model failed: %s", exc, exc_info=True)
        raise HTTPException(500, f"Training error: {exc}")


@router.post("/forecast/train-convlstm")
async def train_convlstm():
    """Train or retrain the ConvLSTM spatiotemporal model on timeline data."""
    engine = _get_engine()
    if engine is None:
        raise HTTPException(503, "Forecast engine not available")

    try:
        result = engine.train_convlstm_model()
        return result
    except Exception as exc:
        logger.error("train_convlstm failed: %s", exc, exc_info=True)
        raise HTTPException(500, f"ConvLSTM training error: {exc}")


@router.get("/forecast/status")
async def forecast_status():
    """Check forecast engine status and data info."""
    engine = _get_engine()
    if engine is None:
        raise HTTPException(503, "Forecast engine not available")

    try:
        return engine.get_forecast_status()
    except Exception as exc:
        logger.error("forecast_status failed: %s", exc, exc_info=True)
        raise HTTPException(500, f"Status error: {exc}")

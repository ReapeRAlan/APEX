"""
APEX Beliefs Router — Bayesian fusion API endpoints.
"""

import logging

from fastapi import APIRouter, Query
from pydantic import BaseModel

from ..services.bayesian_fusion import bayesian_fusion

logger = logging.getLogger("apex.beliefs")
router = APIRouter()


class UpdateBeliefRequest(BaseModel):
    motor_id: str
    h3_index: str
    detection_probability: float
    confidence: float = 1.0


@router.get("/beliefs/{h3_index}")
async def get_belief(h3_index: str):
    """Get current belief state for an H3 cell."""
    return bayesian_fusion.get_belief(h3_index)


@router.post("/beliefs/update")
async def update_belief(req: UpdateBeliefRequest):
    """Update the belief state for a cell with a new motor observation."""
    result = bayesian_fusion.update_beliefs(
        motor_id=req.motor_id,
        h3_index=req.h3_index,
        detection_probability=req.detection_probability,
        confidence=req.confidence,
    )
    return result


@router.post("/beliefs/degrade")
async def degrade_all_beliefs():
    """Manually trigger belief degradation for all active cells."""
    count = bayesian_fusion.degrade_all_active()
    return {"degraded_cells": count}


@router.get("/beliefs/map")
async def get_belief_map(
    min_lat: float = Query(...),
    max_lat: float = Query(...),
    min_lng: float = Query(...),
    max_lng: float = Query(...),
    min_risk: float = Query(0.0, description="Minimum P(any illicit) to include"),
):
    """Get belief map for a bounding box — used for heatmap rendering."""
    from sqlalchemy import func
    from ..db.session import SessionLocal
    from ..db.models import BeliefState, GridCell

    with SessionLocal() as session:
        # Get latest belief per cell within bbox
        subq = (
            session.query(
                BeliefState.h3_index,
                func.max(BeliefState.timestamp).label("max_ts"),
            )
            .group_by(BeliefState.h3_index)
            .subquery()
        )

        beliefs = (
            session.query(BeliefState)
            .join(
                subq,
                (BeliefState.h3_index == subq.c.h3_index)
                & (BeliefState.timestamp == subq.c.max_ts),
            )
            .join(GridCell, GridCell.h3_index == BeliefState.h3_index)
            .filter(
                GridCell.lat >= min_lat,
                GridCell.lat <= max_lat,
                GridCell.lng >= min_lng,
                GridCell.lng <= max_lng,
            )
            .all()
        )

    cells = []
    for b in beliefs:
        p_illicit = 1.0 - b.p_sin_ilicito
        if p_illicit >= min_risk:
            cells.append({
                "h3_index": b.h3_index,
                "p_illicit": round(p_illicit, 4),
                "p_tala": round(b.p_tala, 4),
                "p_cus": round(b.p_cus_inmobiliario, 4),
                "p_agri": round(b.p_frontera_agricola, 4),
                "ci": round(b.confidence_index, 3),
                "acquire_image": b.acquire_commercial_image,
            })

    return {"count": len(cells), "cells": cells}


@router.get("/news/latest")
async def get_latest_news():
    """Get the latest news monitoring results."""
    from ..services.news_monitor import news_monitor
    return news_monitor.run_pipeline()

"""
APEX Strategic Router — Briefs, reports, and strategic panel endpoints.
"""

import logging

from fastapi import APIRouter, Query
from pydantic import BaseModel

from ..services.generative_ai import genai_service
from ..services.bayesian_fusion import bayesian_fusion

logger = logging.getLogger("apex.strategic")
router = APIRouter()


@router.get("/brief/{alert_id}")
async def get_alert_brief(alert_id: str):
    """
    Generate an AI-powered brief for a high-priority alert.

    The alert_id can be an H3 cell index or a monitoring alert ID.
    """
    # Get belief for this cell
    belief = bayesian_fusion.get_belief(alert_id)

    # Get grid cell metadata
    from ..db.session import SessionLocal
    from ..db.models import GridCell

    grid_data = {}
    with SessionLocal() as session:
        cell = session.query(GridCell).filter(GridCell.h3_index == alert_id).first()
        if cell:
            grid_data = {
                "estado": cell.estado,
                "municipio": cell.municipio,
                "ecosistema": cell.tipo_ecosistema,
                "en_anp": cell.en_anp,
                "nombre_anp": cell.nombre_anp,
            }

    alert_data = {
        "h3_index": alert_id,
        "probabilities": belief["probabilities"],
        "ci": belief["confidence_index"],
        "engines_triggered": belief.get("source_motors", []),
        **grid_data,
    }

    brief = genai_service.generate_alert_brief(alert_data)
    return brief


@router.get("/reports/weekly/{subdelegacion_id}")
async def get_weekly_report(subdelegacion_id: str):
    """Generate a weekly report for a subdelegation."""
    # In production, filter alerts by subdelegation zone
    # For now, return a report with all active alerts

    from sqlalchemy import func
    from ..db.session import SessionLocal
    from ..db.models import BeliefState

    with SessionLocal() as session:
        subq = (
            session.query(
                BeliefState.h3_index,
                func.max(BeliefState.timestamp).label("max_ts"),
            )
            .group_by(BeliefState.h3_index)
            .subquery()
        )

        high_risk = (
            session.query(BeliefState)
            .join(
                subq,
                (BeliefState.h3_index == subq.c.h3_index)
                & (BeliefState.timestamp == subq.c.max_ts),
            )
            .filter(BeliefState.p_sin_ilicito < 0.5)  # P(illicit) > 50%
            .order_by(BeliefState.p_sin_ilicito.asc())
            .limit(20)
            .all()
        )

    alerts = [
        {
            "h3_index": b.h3_index,
            "p_illicit": round(1.0 - b.p_sin_ilicito, 4),
            "ci": round(b.confidence_index, 3),
        }
        for b in high_risk
    ]

    stats = {
        "area_monitored_ha": len(high_risk) * 122,  # ~1.22 km² * 100 ha/km²
        "total_damage_ha": sum(
            (1.0 - b.p_sin_ilicito) * 15.0  # Estimated damage per cell
            for b in high_risk
        ),
        "trend": "⬆️ incremento" if len(high_risk) > 10 else "➡️ estable",
    }

    report = genai_service.generate_weekly_report(subdelegacion_id, alerts, stats)
    return report

"""
APEX Strategic Router — Briefs, reports, and strategic panel endpoints.
"""

import logging

from fastapi import APIRouter

logger = logging.getLogger("apex.strategic")
router = APIRouter()

# Lazy imports — these services may fail if optional deps aren't installed
_genai = None
_bayesian = None


def _get_genai():
    global _genai
    if _genai is None:
        try:
            from ..services.generative_ai import genai_service
            _genai = genai_service
        except Exception as exc:
            logger.warning("Generative AI service not available: %s", exc)
    return _genai


def _get_bayesian():
    global _bayesian
    if _bayesian is None:
        try:
            from ..services.bayesian_fusion import bayesian_fusion
            _bayesian = bayesian_fusion
        except Exception as exc:
            logger.warning("Bayesian fusion service not available: %s", exc)
    return _bayesian


@router.get("/strategic/overview")
async def strategic_overview():
    """High-level strategic overview for the dashboard."""
    from ..db.session import get_session
    from ..db.models import MonitoringAlert, MonitoringArea, Job, BeliefState, GridCell
    from sqlalchemy import func

    try:
        with get_session() as session:
            active_alerts = session.query(func.count(MonitoringAlert.id)).scalar() or 0
            active_areas = (
                session.query(func.count(MonitoringArea.id))
                .filter(MonitoringArea.active == True)  # noqa: E712
                .scalar()
            ) or 0

            # High risk zones: cells with P(illicit) > 50%
            high_risk_zones = (
                session.query(func.count(BeliefState.id))
                .filter(BeliefState.p_sin_ilicito < 0.5)
                .scalar()
            ) or 0

            # Pending inspections: jobs queued or running
            pending_inspections = (
                session.query(func.count(Job.id))
                .filter(Job.status.in_(["queued", "running"]))
                .scalar()
            ) or 0

            # Top regions from grid cells with belief states
            top_regions_raw = (
                session.query(
                    GridCell.estado,
                    func.count(BeliefState.id).label("cnt"),
                )
                .join(BeliefState, GridCell.h3_index == BeliefState.h3_index)
                .filter(BeliefState.p_sin_ilicito < 0.5)
                .group_by(GridCell.estado)
                .order_by(func.count(BeliefState.id).desc())
                .limit(5)
                .all()
            )
            top_regions = [
                {"name": r.estado or "Desconocido", "risk_score": round(r.cnt / max(high_risk_zones, 1), 3)}
                for r in top_regions_raw
            ]

    except Exception as exc:
        logger.error("strategic_overview error: %s", exc)
        active_alerts = 0
        active_areas = 0
        high_risk_zones = 0
        pending_inspections = 0
        top_regions = []

    return {
        "high_risk_zones": high_risk_zones,
        "active_alerts": active_alerts,
        "active_monitoring_areas": active_areas,
        "pending_inspections": pending_inspections,
        "weekly_trend": "estable",
        "top_regions": top_regions,
    }


@router.get("/brief/{alert_id}")
async def get_alert_brief(alert_id: str):
    """
    Generate an AI-powered brief for a high-priority alert.

    The alert_id can be an H3 cell index or a monitoring alert ID.
    """
    bf = _get_bayesian()
    if not bf:
        return {"error": "Bayesian fusion service not available", "alert_id": alert_id}

    try:
        belief = bf.get_belief(alert_id)
    except Exception as exc:
        logger.error("get_belief failed for %s: %s", alert_id, exc)
        return {"error": str(exc), "alert_id": alert_id}

    from ..db.session import get_session
    from ..db.models import GridCell

    grid_data = {}
    try:
        with get_session() as session:
            cell = session.query(GridCell).filter(GridCell.h3_index == alert_id).first()
            if cell:
                grid_data = {
                    "estado": cell.estado,
                    "municipio": cell.municipio,
                    "ecosistema": cell.tipo_ecosistema,
                    "en_anp": cell.en_anp,
                    "nombre_anp": cell.nombre_anp,
                }
    except Exception:
        pass

    alert_data = {
        "h3_index": alert_id,
        "probabilities": belief["probabilities"],
        "ci": belief["confidence_index"],
        "engines_triggered": belief.get("source_motors", []),
        **grid_data,
    }

    genai = _get_genai()
    if genai:
        brief = genai.generate_alert_brief(alert_data)
    else:
        brief = {"alert_id": alert_id, "summary": "IA generativa no disponible", "data": alert_data}
    return brief


@router.get("/reports/weekly/{subdelegacion_id}")
async def get_weekly_report(subdelegacion_id: str):
    """Generate a weekly report for a subdelegation."""
    from sqlalchemy import func
    from ..db.session import get_session
    from ..db.models import BeliefState

    high_risk = []
    try:
        with get_session() as session:
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
                .filter(BeliefState.p_sin_ilicito < 0.5)
                .order_by(BeliefState.p_sin_ilicito.asc())
                .limit(20)
                .all()
            )
    except Exception as exc:
        logger.error("weekly report query error: %s", exc)

    alerts = [
        {
            "h3_index": b.h3_index,
            "p_illicit": round(1.0 - b.p_sin_ilicito, 4),
            "ci": round(b.confidence_index, 3),
        }
        for b in high_risk
    ]

    stats = {
        "area_monitored_ha": len(high_risk) * 122,
        "total_damage_ha": sum(
            (1.0 - b.p_sin_ilicito) * 15.0
            for b in high_risk
        ),
        "trend": "⬆️ incremento" if len(high_risk) > 10 else "➡️ estable",
    }

    genai = _get_genai()
    if genai:
        report = genai.generate_weekly_report(subdelegacion_id, alerts, stats)
    else:
        report = {
            "subdelegacion": subdelegacion_id,
            "alerts": alerts,
            "stats": stats,
            "summary": "IA generativa no disponible — datos crudos devueltos.",
        }
    return report

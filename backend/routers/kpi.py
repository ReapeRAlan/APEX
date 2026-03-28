"""
APEX Phase 4 — KPI Dashboard Router.

Endpoints for operational impact metrics, model performance,
and retraining status. Powers the ImpactDashboard frontend.
"""

from fastapi import APIRouter, Query
from datetime import datetime, timedelta, timezone
from typing import Optional
import logging

logger = logging.getLogger("apex.kpi")
router = APIRouter(tags=["kpi"])


@router.get("/kpi/summary")
async def kpi_summary(
    days: int = Query(30, ge=1, le=365),
    subdelegacion: Optional[str] = None,
):
    """Overall KPI summary for the dashboard."""
    from ..db.session import get_session
    from sqlalchemy import text

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    filters = "WHERE j.created_at >= :cutoff"
    params: dict = {"cutoff": cutoff.isoformat()}

    if subdelegacion:
        filters += " AND j.parameters::text LIKE :subdel"
        params["subdel"] = f"%{subdelegacion}%"

    with get_session() as session:
        # Total jobs
        r = session.execute(
            text(f"SELECT COUNT(*), SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) FROM jobs j {filters}"),
            params,
        ).fetchone()
        total_jobs = r[0] or 0
        completed_jobs = r[1] or 0

        # Alerts generated
        r2 = session.execute(
            text(f"SELECT COUNT(*) FROM monitoring_alerts WHERE created_at >= :cutoff"),
            {"cutoff": cutoff.isoformat()},
        ).fetchone()
        total_alerts = r2[0] or 0

        # Validated detections
        r3 = session.execute(
            text(
                "SELECT COUNT(*), "
                "SUM(CASE WHEN validated=true THEN 1 ELSE 0 END) "
                "FROM analysis_results WHERE created_at >= :cutoff"
            ),
            {"cutoff": cutoff.isoformat()},
        ).fetchone()
        total_detections = r3[0] or 0
        validated_detections = r3[1] or 0

        # Average response time (jobs)
        r4 = session.execute(
            text(
                "SELECT AVG(EXTRACT(EPOCH FROM (updated_at - created_at))) "
                "FROM jobs j "
                f"{filters} AND j.status = 'completed'"
            ),
            params,
        ).fetchone()
        avg_response_secs = r4[0] or 0

    return {
        "period_days": days,
        "total_jobs": total_jobs,
        "completed_jobs": completed_jobs,
        "completion_rate": completed_jobs / max(total_jobs, 1),
        "total_alerts": total_alerts,
        "total_detections": total_detections,
        "validated_detections": validated_detections,
        "validation_rate": validated_detections / max(total_detections, 1),
        "avg_response_seconds": round(avg_response_secs, 1),
    }


@router.get("/kpi/engines")
async def kpi_engines(days: int = Query(30, ge=1, le=365)):
    """Per-engine performance metrics."""
    from ..db.session import get_session
    from sqlalchemy import text

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    with get_session() as session:
        rows = session.execute(
            text(
                "SELECT engine, "
                "COUNT(*) as total, "
                "SUM(CASE WHEN validated=true THEN 1 ELSE 0 END) as validated, "
                "SUM(CASE WHEN validated=false THEN 1 ELSE 0 END) as rejected "
                "FROM analysis_results "
                "WHERE created_at >= :cutoff "
                "GROUP BY engine ORDER BY total DESC"
            ),
            {"cutoff": cutoff.isoformat()},
        ).fetchall()

    engines = []
    for r in rows:
        total = r[1]
        validated = r[2] or 0
        rejected = r[3] or 0
        reviewed = validated + rejected
        engines.append({
            "engine": r[0],
            "total_detections": total,
            "validated": validated,
            "rejected": rejected,
            "precision": validated / max(reviewed, 1),
            "review_rate": reviewed / max(total, 1),
        })

    return {"period_days": days, "engines": engines}


@router.get("/kpi/timeline")
async def kpi_timeline(
    days: int = Query(30, ge=1, le=365),
    granularity: str = Query("day", pattern="^(day|week|month)$"),
):
    """Time series of detections and alerts."""
    from ..db.session import get_session
    from sqlalchemy import text

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    trunc_fn = {
        "day": "DATE(created_at)",
        "week": "DATE(date_trunc('week', created_at))",
        "month": "DATE(date_trunc('month', created_at))",
    }[granularity]

    with get_session() as session:
        # Detections over time
        det_rows = session.execute(
            text(
                f"SELECT {trunc_fn} as period, COUNT(*) "
                "FROM analysis_results WHERE created_at >= :cutoff "
                f"GROUP BY period ORDER BY period"
            ),
            {"cutoff": cutoff.isoformat()},
        ).fetchall()

        # Alerts over time
        alert_rows = session.execute(
            text(
                f"SELECT {trunc_fn} as period, COUNT(*) "
                "FROM monitoring_alerts WHERE created_at >= :cutoff "
                f"GROUP BY period ORDER BY period"
            ),
            {"cutoff": cutoff.isoformat()},
        ).fetchall()

    det_map = {str(r[0]): r[1] for r in det_rows}
    alert_map = {str(r[0]): r[1] for r in alert_rows}
    all_periods = sorted(set(det_map.keys()) | set(alert_map.keys()))

    timeline = [
        {
            "period": p,
            "detections": det_map.get(p, 0),
            "alerts": alert_map.get(p, 0),
        }
        for p in all_periods
    ]

    return {"granularity": granularity, "timeline": timeline}


@router.get("/kpi/retraining")
async def kpi_retraining():
    """MLflow retraining pipeline status for all engines."""
    from ..services.mlflow_pipeline import RetrainingPipeline

    pipeline = RetrainingPipeline()
    experiments = await pipeline.get_experiment_status()

    # Label counts per engine
    from ..db.session import get_session
    from sqlalchemy import text
    with get_session() as session:
        label_rows = session.execute(
            text(
                "SELECT engine, COUNT(*) "
                "FROM analysis_results WHERE validated = true "
                "GROUP BY engine"
            )
        ).fetchall()

    label_map = {r[0]: r[1] for r in label_rows}

    engines_status = []
    for exp in experiments:
        engine = exp["engine"]
        labels = label_map.get(engine, 0)
        engines_status.append({
            **exp,
            "validated_labels": labels,
            "ready_to_retrain": labels >= pipeline.LABEL_THRESHOLD,
            "label_threshold": pipeline.LABEL_THRESHOLD,
        })

    # Add engines with labels but no experiment yet
    for engine, count in label_map.items():
        if not any(e["engine"] == engine for e in engines_status):
            engines_status.append({
                "engine": engine,
                "experiment": f"apex-{engine}",
                "total_runs": 0,
                "best_f1": None,
                "last_run": None,
                "validated_labels": count,
                "ready_to_retrain": count >= pipeline.LABEL_THRESHOLD,
                "label_threshold": pipeline.LABEL_THRESHOLD,
            })

    return {"engines": engines_status}


@router.post("/kpi/retrain/{engine_name}")
async def trigger_retrain(engine_name: str):
    """Manually trigger retraining for an engine."""
    from ..services.mlflow_pipeline import RetrainingPipeline

    pipeline = RetrainingPipeline()
    result = await pipeline.check_and_retrain(engine_name)
    if result is None:
        return {"status": "skipped", "reason": "insufficient labels"}
    return result


@router.get("/kpi/coverage")
async def kpi_coverage():
    """Grid coverage stats — how much of the national grid has been analyzed."""
    from ..db.session import get_session
    from sqlalchemy import text

    with get_session() as session:
        r = session.execute(
            text(
                "SELECT COUNT(*), "
                "SUM(CASE WHEN priority_score > 0 THEN 1 ELSE 0 END), "
                "AVG(priority_score) "
                "FROM grid_cells"
            )
        ).fetchone()

        total_cells = r[0] or 0
        analyzed_cells = r[1] or 0
        avg_priority = r[2] or 0

        # Belief coverage
        r2 = session.execute(
            text("SELECT COUNT(*) FROM belief_states")
        ).fetchone()
        belief_cells = r2[0] or 0

    return {
        "total_grid_cells": total_cells,
        "analyzed_cells": analyzed_cells,
        "coverage_rate": analyzed_cells / max(total_cells, 1),
        "avg_priority": round(float(avg_priority), 4),
        "belief_states_active": belief_cells,
    }

"""
APEX Phase 4 — KPI Dashboard Router.

Endpoints for operational impact metrics, model performance,
and retraining status. Powers the ImpactDashboard frontend.

All queries use the SQLAlchemy ORM to remain dialect-agnostic
(works on both SQLite and PostgreSQL).
"""

from fastapi import APIRouter, Query
from datetime import datetime, timedelta, timezone
from typing import Optional
import logging

from sqlalchemy import func, case

from ..db.session import get_session
from ..db.models import Job, AnalysisResult, MonitoringAlert, GridCell, BeliefState

logger = logging.getLogger("apex.kpi")
router = APIRouter(tags=["kpi"])


@router.get("/kpi/summary")
async def kpi_summary(
    days: int = Query(30, ge=1, le=365),
    subdelegacion: Optional[str] = None,
):
    """Overall KPI summary for the dashboard."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    try:
        with get_session() as session:
            # Total / completed jobs
            q = session.query(
                func.count(Job.id),
                func.sum(case((Job.status == "completed", 1), else_=0)),
            ).filter(Job.created_at >= cutoff)
            if subdelegacion:
                q = q.filter(Job.engines.like(f"%{subdelegacion}%"))
            r = q.one()
            total_jobs = r[0] or 0
            completed_jobs = r[1] or 0

            # Alerts generated
            total_alerts = (
                session.query(func.count(MonitoringAlert.id))
                .filter(MonitoringAlert.detected_at >= cutoff)
                .scalar()
            ) or 0

            # Detections
            det = session.query(
                func.count(AnalysisResult.id),
                func.sum(case((AnalysisResult.validated == True, 1), else_=0)),  # noqa: E712
            ).filter(AnalysisResult.created_at >= cutoff).one()
            total_detections = det[0] or 0
            validated_detections = det[1] or 0

            # Avg response (completed_at - created_at) in seconds
            avg_response_secs = 0
            completed_pairs = (
                session.query(Job.created_at, Job.completed_at)
                .filter(Job.created_at >= cutoff, Job.status == "completed", Job.completed_at.isnot(None))
                .all()
            )
            if completed_pairs:
                deltas = [(c.completed_at - c.created_at).total_seconds() for c in completed_pairs if c.created_at and c.completed_at]
                avg_response_secs = sum(deltas) / len(deltas) if deltas else 0
    except Exception as exc:
        logger.error("kpi_summary error: %s", exc)
        total_jobs = completed_jobs = total_alerts = total_detections = validated_detections = 0
        avg_response_secs = 0

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
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    try:
        with get_session() as session:
            rows = (
                session.query(
                    AnalysisResult.engine,
                    func.count(AnalysisResult.id).label("total"),
                    func.sum(case((AnalysisResult.validated == True, 1), else_=0)).label("validated"),  # noqa: E712
                    func.sum(case((AnalysisResult.validated == False, 1), else_=0)).label("rejected"),  # noqa: E712
                )
                .filter(AnalysisResult.created_at >= cutoff)
                .group_by(AnalysisResult.engine)
                .order_by(func.count(AnalysisResult.id).desc())
                .all()
            )
    except Exception as exc:
        logger.error("kpi_engines error: %s", exc)
        rows = []

    engines = []
    for r in rows:
        total = r.total or 0
        validated = r.validated or 0
        rejected = r.rejected or 0
        reviewed = validated + rejected
        engines.append({
            "engine": r.engine,
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
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    try:
        with get_session() as session:
            # Use func.date for grouping — works on both SQLite and PostgreSQL
            if granularity == "day":
                det_group = func.date(AnalysisResult.created_at)
                alert_group = func.date(MonitoringAlert.detected_at)
            elif granularity == "week":
                det_group = func.strftime("%Y-W%W", AnalysisResult.created_at)
                alert_group = func.strftime("%Y-W%W", MonitoringAlert.detected_at)
            else:  # month
                det_group = func.strftime("%Y-%m", AnalysisResult.created_at)
                alert_group = func.strftime("%Y-%m", MonitoringAlert.detected_at)

            det_rows = (
                session.query(det_group.label("period"), func.count(AnalysisResult.id))
                .filter(AnalysisResult.created_at >= cutoff)
                .group_by("period")
                .order_by("period")
                .all()
            )

            alert_rows = (
                session.query(alert_group.label("period"), func.count(MonitoringAlert.id))
                .filter(MonitoringAlert.detected_at >= cutoff)
                .group_by("period")
                .order_by("period")
                .all()
            )
    except Exception as exc:
        logger.error("kpi_timeline error: %s", exc)
        det_rows = []
        alert_rows = []

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
    experiments = []
    pipeline = None
    label_threshold = 50

    try:
        from ..services.mlflow_pipeline import RetrainingPipeline
        pipeline = RetrainingPipeline()
        label_threshold = pipeline.LABEL_THRESHOLD
        experiments = await pipeline.get_experiment_status()
    except Exception as exc:
        logger.warning("MLflow not available: %s", exc)

    # Label counts per engine (ORM)
    try:
        with get_session() as session:
            label_rows = (
                session.query(AnalysisResult.engine, func.count(AnalysisResult.id))
                .filter(AnalysisResult.validated == True)  # noqa: E712
                .group_by(AnalysisResult.engine)
                .all()
            )
    except Exception as exc:
        logger.error("kpi_retraining label query error: %s", exc)
        label_rows = []

    label_map = {r[0]: r[1] for r in label_rows}

    engines_status = []
    for exp in experiments:
        engine = exp["engine"]
        labels = label_map.get(engine, 0)
        engines_status.append({
            **exp,
            "validated_labels": labels,
            "ready_to_retrain": labels >= label_threshold,
            "label_threshold": label_threshold,
        })

    for engine, count in label_map.items():
        if not any(e["engine"] == engine for e in engines_status):
            engines_status.append({
                "engine": engine,
                "experiment": f"apex-{engine}",
                "total_runs": 0,
                "best_f1": None,
                "last_run": None,
                "validated_labels": count,
                "ready_to_retrain": count >= label_threshold,
                "label_threshold": label_threshold,
            })

    return {"engines": engines_status}


@router.post("/kpi/retrain/{engine_name}")
async def trigger_retrain(engine_name: str):
    """Manually trigger retraining for an engine."""
    try:
        from ..services.mlflow_pipeline import RetrainingPipeline
        pipeline = RetrainingPipeline()
        result = await pipeline.check_and_retrain(engine_name)
        if result is None:
            return {"status": "skipped", "reason": "insufficient labels"}
        return result
    except Exception as exc:
        logger.error("Retrain trigger failed: %s", exc)
        return {"status": "error", "reason": str(exc)}


@router.get("/kpi/coverage")
async def kpi_coverage():
    """Grid coverage stats — how much of the national grid has been analyzed."""
    try:
        with get_session() as session:
            total_cells = session.query(func.count(GridCell.id)).scalar() or 0
            belief_cells = session.query(func.count(BeliefState.id)).scalar() or 0
    except Exception as exc:
        logger.error("kpi_coverage error: %s", exc)
        total_cells = belief_cells = 0

    return {
        "total_grid_cells": total_cells,
        "analyzed_cells": belief_cells,
        "coverage_rate": belief_cells / max(total_cells, 1),
        "belief_states_active": belief_cells,
    }

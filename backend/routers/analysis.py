import logging
import json
import uuid

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel
from typing import List, Optional

from ..db.database import db
from ..pipeline import run_pipeline, run_timeline_pipeline, get_job_logs
from ..services.firms_service import check_key_status as firms_key_status

logger = logging.getLogger("apex.analysis")
router = APIRouter()


class AOI(BaseModel):
    type: str = "Polygon"
    coordinates: List[List[List[float]]]


class AnalyzeRequest(BaseModel):
    aoi: AOI
    engines: List[str]
    date_range: List[str]
    reference_date: Optional[str] = None
    notify_email: Optional[str] = None


class TimelineRequest(BaseModel):
    aoi: AOI
    start_year: int = 2018
    end_year: int = 2025
    engines: List[str] = [
        "deforestation", "vegetation", "urban_expansion",
        "hansen", "alerts", "drivers", "fire", "sar",
        "structures", "firms_hotspots",
    ]
    season: str = "dry"
    notify_email: Optional[str] = None


class SendReportRequest(BaseModel):
    email: str
    area_name: Optional[str] = None


@router.post("/analyze")
async def analyze_area(req: AnalyzeRequest, background_tasks: BackgroundTasks):
    job_id = str(uuid.uuid4())
    jid = job_id[:8]

    # ── Log incoming request ──
    coords = req.aoi.coordinates[0] if req.aoi.coordinates else []
    n_verts = len(coords)
    bbox_str = ""
    if coords:
        lons = [c[0] for c in coords]
        lats = [c[1] for c in coords]
        bbox_str = f" bbox=[{min(lons):.4f},{min(lats):.4f}]->[{max(lons):.4f},{max(lats):.4f}]"

    logger.info(
        "[%s] POST /analyze — engines=%s dates=%s notify=%s verts=%d%s",
        jid, req.engines, req.date_range, req.notify_email or "(none)", n_verts, bbox_str,
    )

    try:
        with db.get_connection() as conn:
            conn.execute(
                """INSERT INTO jobs (id, status, aoi_geojson, engines, date_range_start, date_range_end, notify_email)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (job_id, "queued", json.dumps(req.aoi.dict()), json.dumps(req.engines),
                 req.date_range[0], req.date_range[1], req.notify_email or None)
            )
            conn.commit()
        logger.info("[%s] Job inserted into DB — status=queued", jid)
    except Exception as exc:
        logger.error("[%s] DB insert failed: %s", jid, exc)
        raise HTTPException(status_code=500, detail=f"Error creando job: {exc}")

    background_tasks.add_task(run_pipeline, job_id, req.dict())
    logger.info("[%s] Pipeline queued in background thread", jid)

    return {
        "job_id": job_id,
        "status": "queued",
        "estimated_seconds": 120
    }


@router.post("/timeline")
async def analyze_timeline(req: TimelineRequest, background_tasks: BackgroundTasks):
    """Analiza el mismo AOI en multiples anos consecutivos."""
    job_id = str(uuid.uuid4())
    jid = job_id[:8]

    logger.info(
        "[%s] POST /timeline — engines=%s years=%d-%d notify=%s",
        jid, req.engines, req.start_year, req.end_year, req.notify_email or "(none)",
    )

    try:
        with db.get_connection() as conn:
            conn.execute(
                """INSERT INTO jobs (id, status, aoi_geojson, engines, date_range_start, date_range_end, notify_email)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (job_id, "queued", json.dumps(req.aoi.dict()),
                 json.dumps(req.engines), f"{req.start_year}-01-01", f"{req.end_year}-12-31",
                 req.notify_email or None),
            )
            conn.commit()
        logger.info("[%s] Timeline job inserted into DB", jid)
    except Exception as exc:
        logger.error("[%s] DB insert failed: %s", jid, exc)
        raise HTTPException(status_code=500, detail=f"Error creando job: {exc}")

    background_tasks.add_task(run_timeline_pipeline, job_id, req.dict())
    logger.info("[%s] Timeline pipeline queued in background", jid)

    return {
        "job_id": job_id,
        "status": "queued",
        "estimated_seconds": len(range(req.start_year, req.end_year + 1)) * 60
    }


@router.get("/jobs/{job_id}")
async def get_job_status(job_id: str):
    with db.get_connection() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Job not found")

        return {
            "job_id": row["id"],
            "status": row["status"],
            "progress": row["progress"],
            "current_step": row["current_step"],
            "logs": get_job_logs(job_id),
        }


@router.get("/results/{job_id}")
async def get_job_results(job_id: str):
    jid = job_id[:8]
    with db.get_connection() as conn:
        job_row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not job_row:
            raise HTTPException(status_code=404, detail="Job not found")

        if job_row["status"] != "completed":
            raise HTTPException(status_code=400, detail="Job not yet completed")

        results = conn.execute("SELECT * FROM analysis_results WHERE job_id = ?", (job_id,)).fetchall()

        layers = {}
        for row in results:
            layers[row["engine"]] = {
                "geojson": json.loads(row["geojson"]),
                "stats": json.loads(row["stats_json"])
            }

        logger.info("[%s] Results fetched — %d engines: %s", jid, len(layers), list(layers.keys()))

        return {
            "job_id": job_id,
            "status": "completed",
            "layers": layers
        }


@router.post("/results/{job_id}/send-report")
async def send_report_email(job_id: str, req: SendReportRequest):
    """Send analysis results as a formatted report email with PDF attachment."""
    jid = job_id[:8]
    logger.info("[%s] POST /send-report — to=%s area=%s", jid, req.email, req.area_name or "(none)")

    with db.get_connection() as conn:
        job_row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not job_row:
            raise HTTPException(status_code=404, detail="Job no encontrado")

        if job_row["status"] != "completed":
            raise HTTPException(status_code=400, detail="El analisis aun no ha completado")

        results = conn.execute(
            "SELECT * FROM analysis_results WHERE job_id = ?", (job_id,)
        ).fetchall()

    layers = {}
    for row in results:
        layers[row["engine"]] = {
            "geojson": json.loads(row["geojson"]),
            "stats": json.loads(row["stats_json"])
        }

    # Determine analysis type
    analysis_type = "manual"
    if "timeline_summary" in layers:
        analysis_type = "timeline"

    # Extract date range from job
    date_range = None
    try:
        ds = job_row["date_range_start"]
        de = job_row["date_range_end"]
        if ds and de:
            date_range = [ds, de]
    except (IndexError, KeyError):
        pass

    logger.info("[%s] Sending report — type=%s layers=%s date_range=%s",
                jid, analysis_type, list(layers.keys()), date_range)

    from ..services.alert_service import AlertService
    svc = AlertService()
    success = svc.send_analysis_report_email(
        to_email=req.email,
        job_id=job_id,
        results=layers,
        analysis_type=analysis_type,
        area_name=req.area_name,
        date_range=date_range,
    )

    if not success:
        logger.error("[%s] Email send FAILED to %s", jid, req.email)
        raise HTTPException(status_code=500, detail="Error al enviar el correo. Verifique la configuracion SMTP.")

    logger.info("[%s] Email sent OK to %s", jid, req.email)
    return {
        "status": "sent",
        "to": req.email,
        "job_id": job_id,
        "folio": f"PROFEPA-APEX-{job_id[:8].upper()}"
    }


@router.get("/firms/status")
async def firms_status():
    """Check if the FIRMS MAP_KEY is configured and valid."""
    return firms_key_status()

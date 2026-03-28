from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Optional, Tuple

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse, StreamingResponse

from ..db.database import db

router = APIRouter()
log = logging.getLogger(__name__)


def _load_summary(job_id: str) -> dict:
    """Fetch timeline_summary + all engine results and merge into one dict."""
    with db.get_connection() as conn:
        job_row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not job_row:
            raise HTTPException(status_code=404, detail="Job no encontrado")
        row = conn.execute(
            "SELECT geojson FROM analysis_results WHERE job_id = ? AND engine = ?",
            (job_id, "timeline_summary"),
        ).fetchone()

        # Also load individual engine results (stored as separate rows)
        extra_engines = (
            "hansen", "alerts", "drivers", "fire", "sar",
            "crossval", "legal_context",
        )
        extras = conn.execute(
            f"SELECT engine, geojson, stats_json FROM analysis_results "
            f"WHERE job_id = ? AND engine IN ({','.join('?' for _ in extra_engines)})",
            (job_id, *extra_engines),
        ).fetchall()

    if not row:
        raise HTTPException(status_code=404, detail="Timeline summary no encontrado")

    summary = json.loads(row["geojson"])

    # Merge extra engine results into the summary dict so the report
    # generator can access summary["hansen"], summary["fire"], etc.
    for erow in extras:
        engine = erow["engine"]
        try:
            geojson_data = json.loads(erow["geojson"]) if erow["geojson"] else {}
            stats_data = json.loads(erow["stats_json"]) if erow["stats_json"] else {}
        except (json.JSONDecodeError, TypeError):
            continue

        # Merge stats into geojson data (stats has the summary numbers)
        merged = {**geojson_data, **stats_data}
        summary[engine] = merged

    return summary


def _load_aoi(job_id: str) -> Optional[dict]:
    """Fetch AOI GeoJSON from the jobs table."""
    try:
        with db.get_connection() as conn:
            row = conn.execute(
                "SELECT aoi_geojson FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
        if row and row["aoi_geojson"]:
            aoi = row["aoi_geojson"]
            return json.loads(aoi) if isinstance(aoi, str) else aoi
    except Exception:
        log.debug("Could not load AOI for %s", job_id)
    return None


def _fetch_thumbnails(aoi_geojson: Optional[dict], timeline: dict) -> Tuple[dict, Optional[dict]]:
    """Try to fetch Earth Engine satellite thumbnails; return ({}, None) on failure."""
    thumbnails: dict = {}
    overview = None
    if not aoi_geojson:
        return thumbnails, overview
    try:
        from ..services.gee_thumbnails import GEEThumbnailService
        svc = GEEThumbnailService()
        years = sorted(timeline.keys()) if timeline else []
        if years:
            thumbnails = svc.fetch_yearly_thumbnails(aoi_geojson, years)
        overview = svc.fetch_overview_thumbnail(aoi_geojson)
    except Exception as exc:
        log.warning("Thumbnail fetch failed (non-fatal): %s", exc)
    return thumbnails, overview


@router.get("/export/{job_id}/report")
async def export_timeline_report(
    job_id: str,
    format: str = Query("json", pattern="^(json|pdf|docx)$"),
):
    """Genera reporte institucional PROFEPA en JSON, PDF o Word."""
    summary = _load_summary(job_id)

    # ── PDF ──
    if format == "pdf":
        from ..modules.report_generator import APEXPDFReportGenerator

        aoi = _load_aoi(job_id)
        thumbs, overview = _fetch_thumbnails(aoi, summary.get("timeline", {}))

        gen = APEXPDFReportGenerator()
        buf = gen.generate(summary, job_id,
                           aoi_geojson=aoi,
                           thumbnails=thumbs,
                           overview_thumb=overview)
        return StreamingResponse(
            buf,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="APEX_reporte_{job_id[:8]}.pdf"'
            },
        )

    # ── Word ──
    if format == "docx":
        from ..modules.report_generator import APEXWordReportGenerator

        aoi = _load_aoi(job_id)
        thumbs, overview = _fetch_thumbnails(aoi, summary.get("timeline", {}))

        gen = APEXWordReportGenerator()
        buf = gen.generate(summary, job_id,
                           aoi_geojson=aoi,
                           thumbnails=thumbs,
                           overview_thumb=overview)
        return StreamingResponse(
            buf,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={
                "Content-Disposition": f'attachment; filename="APEX_reporte_{job_id[:8]}.docx"'
            },
        )

    # ── JSON (default) ──
    cumulative = summary.get("cumulative", {})
    anomalies = summary.get("anomalies", [])

    report = {
        "folio": f"PROFEPA-APEX-{job_id[:8].upper()}",
        "fecha_generacion": datetime.now().isoformat(),
        "periodo_analizado": cumulative.get("period"),
        "resumen_ejecutivo": {
            "total_deforestacion_ha": cumulative.get("total_deforestation_ha"),
            "total_expansion_urbana_ha": cumulative.get("total_urban_expansion_ha"),
            "cambio_bosque_denso_pct": cumulative.get("bosque_denso_change_pct"),
            "anomalias_detectadas": len(anomalies),
        },
        "alertas": anomalies,
        "datos_anuales": summary.get("timeline", {}),
        "hansen": summary.get("hansen"),
        "fire": summary.get("fire"),
        "sar": summary.get("sar"),
        "alerts_glad_radd": summary.get("alerts"),
        "drivers": summary.get("drivers"),
        "crossval": summary.get("crossval"),
        "legal_context": summary.get("legal_context"),
    }

    return JSONResponse(
        content=report,
        headers={
            "Content-Disposition": f'attachment; filename="APEX_reporte_{job_id[:8]}.json"'
        },
    )

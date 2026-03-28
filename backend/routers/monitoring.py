"""
APEX Monitoring Router — REST endpoints for area monitoring management.

Provides CRUD operations for monitored areas, alert history,
test-email, on-demand analysis, toggle, and purge functionality.
"""

from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import List, Optional
import json

from ..db.database import db
from ..services.monitoring_service import MonitoringService
from ..services.alert_service import AlertService

router = APIRouter()

# Instantiate services
_monitoring_service = MonitoringService(db)


# ------------------------------------------------------------------
# Request / Response models
# ------------------------------------------------------------------

class MonitoringAreaRequest(BaseModel):
    name: str
    aoi_geojson: dict
    engines: List[str]
    alert_email: Optional[str] = None
    threshold_ha: float = 1.0
    interval_hours: int = 168
    notes: Optional[str] = None


class TestEmailRequest(BaseModel):
    email: str
    area_name: str = "Area de prueba APEX"


# ------------------------------------------------------------------
# Static routes FIRST (before parameterized {area_id})
# ------------------------------------------------------------------

@router.post("/monitoring")
async def register_monitoring_area(req: MonitoringAreaRequest):
    """Register a new area for periodic monitoring."""
    try:
        area_id = _monitoring_service.add_area(
            name=req.name,
            aoi_geojson=json.dumps(req.aoi_geojson),
            engines=json.dumps(req.engines),
            alert_email=req.alert_email,
            threshold_ha=req.threshold_ha,
            interval_hours=req.interval_hours,
        )
        return {"id": area_id, "status": "registered"}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/monitoring")
async def list_monitoring_areas():
    """List all monitored areas with alert counts."""
    try:
        areas = _monitoring_service.list_areas()
        # Enrich with alert count
        for area in areas:
            area["alert_count"] = _monitoring_service.get_alert_count(area["id"])
        return {"areas": areas}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/monitoring/test-email")
async def test_email(req: TestEmailRequest):
    """Send a test email to verify SMTP configuration."""
    try:
        svc = AlertService()
        svc.send_test_email(req.email, req.area_name)
        return {"status": "sent", "to": req.email}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ------------------------------------------------------------------
# Parameterized routes
# ------------------------------------------------------------------

@router.delete("/monitoring/{area_id}")
async def delete_monitoring_area(area_id: int):
    """Permanently delete a monitored area and all its alerts."""
    try:
        _monitoring_service.delete_area(area_id)
        return {"id": area_id, "status": "deleted"}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.patch("/monitoring/{area_id}/toggle")
async def toggle_monitoring_area(area_id: int):
    """Toggle active/inactive state of a monitored area."""
    try:
        new_active = _monitoring_service.toggle_area(area_id)
        return {"id": area_id, "active": new_active}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/monitoring/{area_id}/history")
async def get_alert_history(area_id: int):
    """Retrieve the alert history for a specific monitored area."""
    with db.get_connection() as conn:
        row = conn.execute(
            "SELECT id FROM monitoring_areas WHERE id = ?", (area_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Monitoring area not found")

    try:
        alerts = _monitoring_service.get_alert_history(area_id)
        return {"area_id": area_id, "alerts": alerts}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.delete("/monitoring/{area_id}/alerts")
async def purge_alerts(area_id: int):
    """Delete all alerts for a monitored area."""
    try:
        count = _monitoring_service.purge_alerts(area_id)
        return {"area_id": area_id, "deleted": count}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/monitoring/{area_id}/analyze")
async def analyze_now(area_id: int, background_tasks: BackgroundTasks):
    """Force an immediate analysis for the given monitored area."""
    area = _monitoring_service._get_area_by_id(area_id)
    if not area:
        raise HTTPException(status_code=404, detail="Area no encontrada")

    background_tasks.add_task(_monitoring_service.analyze_now, area_id)
    return {"status": "analysis_started", "area_id": area_id}

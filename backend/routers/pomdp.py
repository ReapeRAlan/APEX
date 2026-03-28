"""
APEX POMDP Router — Enforcement planning API endpoints.
"""

import logging
from dataclasses import asdict

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..services.pomdp_optimizer import pomdp_optimizer, route_optimizer

logger = logging.getLogger("apex.pomdp")
router = APIRouter()


class WeeklyPlanRequest(BaseModel):
    inspectors_available: int = 10
    budget_images_usd: float = 5000.0


class SimulateRequest(BaseModel):
    inspectors: int = 10
    budget_images_usd: float = 5000.0
    threshold_ha: float = 1.0


@router.post("/pomdp/weekly-plan")
async def generate_weekly_plan(req: WeeklyPlanRequest):
    """Generate a POMDP-optimized weekly enforcement plan."""
    plan = pomdp_optimizer.generate_weekly_plan(
        inspectors_available=req.inspectors_available,
        budget_images_usd=req.budget_images_usd,
    )
    return asdict(plan)


@router.post("/pomdp/simulate")
async def simulate_scenario(req: SimulateRequest):
    """Simulate an enforcement scenario and return projections."""
    result = pomdp_optimizer.simulate_scenario(
        inspectors=req.inspectors,
        budget_images_usd=req.budget_images_usd,
        threshold_ha=req.threshold_ha,
    )
    return result


@router.get("/pomdp/routes/{plan_id}")
async def get_routes(
    plan_id: str,
    n_inspectors: int = Query(5, ge=1, le=50),
    days: int = Query(5, ge=1, le=14),
    format: str = Query("geojson", pattern="^(geojson|json)$"),
):
    """
    Get optimized inspection routes for a plan.
    Returns GeoJSON by default (for map rendering).
    """
    # Re-generate plan to get cells
    plan = pomdp_optimizer.generate_weekly_plan(
        inspectors_available=n_inspectors,
    )

    if not plan.cells_to_inspect:
        return {"routes": [], "message": "No cells to inspect in this plan."}

    routes = route_optimizer.optimize_routes(
        cells=plan.cells_to_inspect,
        n_inspectors=n_inspectors,
        days=days,
    )

    if format == "geojson":
        geojson = route_optimizer.routes_to_geojson(routes)
        return JSONResponse(content=geojson)

    return {"plan_id": plan_id, "routes": routes}


@router.get("/pomdp/voi/{h3_index}")
async def get_cell_voi(h3_index: str):
    """Calculate Value of Information for a specific cell."""
    from ..services.bayesian_fusion import bayesian_fusion

    belief = bayesian_fusion.get_belief(h3_index)
    probs = belief["probabilities"]

    belief_dict = {
        "p_sin_ilicito": probs["sin_ilicito"],
        "p_tala": probs["tala"],
        "p_cus": probs["cus_inmobiliario"],
        "p_agri": probs["frontera_agricola"],
        "ci": belief["confidence_index"],
    }

    voi = pomdp_optimizer.calculate_voi(belief_dict)

    return {
        "h3_index": h3_index,
        "voi": round(voi, 4),
        "belief": belief,
        "recommendation": (
            "ACQUIRE_IMAGE" if voi > 0.5 else
            "INSPECT" if (1.0 - probs["sin_ilicito"]) > 0.4 else
            "MONITOR"
        ),
    }

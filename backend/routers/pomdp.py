"""
APEX POMDP Router — Enforcement planning API endpoints.
"""

import logging
from dataclasses import asdict

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

logger = logging.getLogger("apex.pomdp")
router = APIRouter()

# Lazy imports — POMDP optimizer may fail if optional deps missing
_pomdp = None
_route = None


def _get_pomdp():
    global _pomdp
    if _pomdp is None:
        try:
            from ..services.pomdp_optimizer import pomdp_optimizer
            _pomdp = pomdp_optimizer
        except Exception as exc:
            logger.warning("POMDP optimizer not available: %s", exc)
    return _pomdp


def _get_route():
    global _route
    if _route is None:
        try:
            from ..services.pomdp_optimizer import route_optimizer
            _route = route_optimizer
        except Exception as exc:
            logger.warning("Route optimizer not available: %s", exc)
    return _route


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
    optimizer = _get_pomdp()
    if not optimizer:
        return {"error": "POMDP optimizer not available", "cells_to_inspect": [], "total_budget": 0}
    try:
        plan = optimizer.generate_weekly_plan(
            inspectors_available=req.inspectors_available,
            budget_images_usd=req.budget_images_usd,
        )
        return asdict(plan)
    except Exception as exc:
        logger.error("weekly-plan error: %s", exc)
        return {"error": str(exc), "cells_to_inspect": [], "total_budget": 0}


@router.post("/pomdp/simulate")
async def simulate_scenario(req: SimulateRequest):
    """Simulate an enforcement scenario and return projections."""
    optimizer = _get_pomdp()
    if not optimizer:
        return {"error": "POMDP optimizer not available", "projections": []}
    try:
        result = optimizer.simulate_scenario(
            inspectors=req.inspectors,
            budget_images_usd=req.budget_images_usd,
            threshold_ha=req.threshold_ha,
        )
        return result
    except Exception as exc:
        logger.error("simulate error: %s", exc)
        return {"error": str(exc), "projections": []}


@router.get("/pomdp/routes/{plan_id}")
async def get_routes(
    plan_id: str,
    n_inspectors: int = Query(5, ge=1, le=50),
    days: int = Query(5, ge=1, le=14),
    format: str = Query("geojson", pattern="^(geojson|json)$"),
):
    """Get optimized inspection routes for a plan."""
    optimizer = _get_pomdp()
    route_opt = _get_route()
    if not optimizer or not route_opt:
        return {"routes": [], "message": "Optimizer not available."}

    try:
        plan = optimizer.generate_weekly_plan(inspectors_available=n_inspectors)
        if not plan.cells_to_inspect:
            return {"routes": [], "message": "No cells to inspect in this plan."}

        routes = route_opt.optimize_routes(
            cells=plan.cells_to_inspect,
            n_inspectors=n_inspectors,
            days=days,
        )

        if format == "geojson":
            geojson = route_opt.routes_to_geojson(routes)
            return JSONResponse(content=geojson)

        return {"plan_id": plan_id, "routes": routes}
    except Exception as exc:
        logger.error("routes error: %s", exc)
        return {"routes": [], "message": str(exc)}


@router.get("/pomdp/voi/{h3_index}")
async def get_cell_voi(h3_index: str):
    """Calculate Value of Information for a specific cell."""
    try:
        from ..services.bayesian_fusion import bayesian_fusion
        belief = bayesian_fusion.get_belief(h3_index)
    except Exception as exc:
        logger.error("VOI error: %s", exc)
        return {"h3_index": h3_index, "voi": 0, "recommendation": "MONITOR", "error": str(exc)}

    probs = belief["probabilities"]
    belief_dict = {
        "p_sin_ilicito": probs["sin_ilicito"],
        "p_tala": probs["tala"],
        "p_cus": probs["cus_inmobiliario"],
        "p_agri": probs["frontera_agricola"],
        "ci": belief["confidence_index"],
    }

    optimizer = _get_pomdp()
    voi = optimizer.calculate_voi(belief_dict) if optimizer else 0

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

"""
APEX Grid Router — H3 territorial grid API endpoints.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Query

from ..db.session import SessionLocal
from ..db.models import GridCell

logger = logging.getLogger("apex.grid")
router = APIRouter()


@router.get("/grid")
async def get_grid_cells(
    min_lat: float = Query(..., description="Minimum latitude"),
    max_lat: float = Query(..., description="Maximum latitude"),
    min_lng: float = Query(..., description="Minimum longitude"),
    max_lng: float = Query(..., description="Maximum longitude"),
    en_anp: Optional[bool] = Query(None, description="Filter by ANP status"),
    estado: Optional[str] = Query(None, description="Filter by state name"),
    limit: int = Query(10000, le=50000, description="Max cells to return"),
):
    """Return H3 grid cells within a bounding box."""
    with SessionLocal() as session:
        q = session.query(GridCell).filter(
            GridCell.lat >= min_lat,
            GridCell.lat <= max_lat,
            GridCell.lng >= min_lng,
            GridCell.lng <= max_lng,
        )

        if en_anp is not None:
            q = q.filter(GridCell.en_anp == en_anp)

        if estado:
            q = q.filter(GridCell.estado == estado)

        cells = q.limit(limit).all()

        return {
            "count": len(cells),
            "cells": [
                {
                    "h3_index": c.h3_index,
                    "lat": c.lat,
                    "lng": c.lng,
                    "estado": c.estado,
                    "municipio": c.municipio,
                    "tipo_ecosistema": c.tipo_ecosistema,
                    "en_anp": c.en_anp,
                    "nombre_anp": c.nombre_anp,
                    "cuenca_id": c.cuenca_id,
                }
                for c in cells
            ],
        }


@router.get("/grid/stats")
async def get_grid_stats():
    """Return summary statistics for the H3 grid."""
    with SessionLocal() as session:
        total = session.query(GridCell).count()
        in_anp = session.query(GridCell).filter(GridCell.en_anp.is_(True)).count()

        # Count by state
        from sqlalchemy import func
        states = (
            session.query(GridCell.estado, func.count(GridCell.id))
            .group_by(GridCell.estado)
            .order_by(func.count(GridCell.id).desc())
            .all()
        )

        return {
            "total_cells": total,
            "cells_in_anp": in_anp,
            "by_state": {s: c for s, c in states if s},
        }

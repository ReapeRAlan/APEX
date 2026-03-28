"""
APEX — Generate H3 National Grid for Mexico.

Creates an H3 resolution-6 grid (~1 km² per cell) covering Mexico's territory.
Indexes each cell against:
  - WDPA (ANPs from GEE)
  - States/municipalities (INEGI)
  - Ecosystem types

Usage:
    python -m backend.services.h3_grid_generator

Stores results in PostGIS table `grid_cells`.
"""

import logging

import h3

logger = logging.getLogger("apex.h3_grid")

# Mexico bounding box (generous padding)
MEXICO_BBOX = {
    "min_lat": 14.5,
    "max_lat": 33.0,
    "min_lng": -118.5,
    "max_lng": -86.5,
}

H3_RESOLUTION = 6  # ~1.22 km² average area


def generate_h3_cells(
    min_lat: float = MEXICO_BBOX["min_lat"],
    max_lat: float = MEXICO_BBOX["max_lat"],
    min_lng: float = MEXICO_BBOX["min_lng"],
    max_lng: float = MEXICO_BBOX["max_lng"],
    resolution: int = H3_RESOLUTION,
) -> list[dict]:
    """
    Generate H3 cell indices covering the specified bounding box.

    Returns list of dicts with h3_index, lat, lng for each cell center.
    """
    logger.info(
        "Generating H3 res-%d grid for bbox [%.2f,%.2f]->[%.2f,%.2f]...",
        resolution, min_lat, min_lng, max_lat, max_lng,
    )

    # Build a polygon covering Mexico
    _polygon = [  # noqa: F841
        (min_lat, min_lng),
        (min_lat, max_lng),
        (max_lat, max_lng),
        (max_lat, min_lng),
    ]

    # Get all H3 cells that intersect the polygon
    h3_cells = h3.polyfill_geojson(
        {
            "type": "Polygon",
            "coordinates": [[
                [min_lng, min_lat],
                [max_lng, min_lat],
                [max_lng, max_lat],
                [min_lng, max_lat],
                [min_lng, min_lat],
            ]],
        },
        resolution,
    )

    cells = []
    for h3_idx in h3_cells:
        lat, lng = h3.h3_to_geo(h3_idx)
        cells.append({
            "h3_index": h3_idx,
            "lat": lat,
            "lng": lng,
        })

    logger.info("Generated %d H3 cells at resolution %d.", len(cells), resolution)
    return cells


def index_cells_with_anp(cells: list[dict]) -> list[dict]:
    """
    Cross-reference cells against WDPA protected areas using GEE.
    Adds: en_anp (bool), nombre_anp (str or None)
    """
    try:
        import ee
        ee.Initialize()

        wdpa = ee.FeatureCollection("WCMC/WDPA/current/polygons").filter(
            ee.Filter.eq("ISO3", "MEX")
        )

        # Process in batches of 1000
        batch_size = 1000
        for i in range(0, len(cells), batch_size):
            batch = cells[i:i + batch_size]

            for cell in batch:
                point = ee.Geometry.Point(cell["lng"], cell["lat"])
                intersects = wdpa.filterBounds(point)
                count = intersects.size().getInfo()

                if count > 0:
                    cell["en_anp"] = True
                    first = intersects.first().getInfo()
                    props = first.get("properties", {})
                    cell["nombre_anp"] = props.get("NAME", "Unknown ANP")
                else:
                    cell["en_anp"] = False
                    cell["nombre_anp"] = None

            logger.info("ANP indexing: %d/%d cells processed.", min(i + batch_size, len(cells)), len(cells))

    except Exception as e:
        logger.warning("Could not index ANPs via GEE: %s. Setting all to False.", e)
        for cell in cells:
            cell.setdefault("en_anp", False)
            cell.setdefault("nombre_anp", None)

    return cells


def index_cells_with_states(cells: list[dict]) -> list[dict]:
    """
    Cross-reference cells against Mexico state/municipality boundaries.
    Uses GEE FAO GAUL dataset or local shapefiles.
    """
    try:
        import ee
        ee.Initialize()

        # FAO GAUL Level 1 (states) and Level 2 (municipalities)
        states = ee.FeatureCollection("FAO/GAUL/2015/level1").filter(
            ee.Filter.eq("ADM0_NAME", "Mexico")
        )
        municipalities = ee.FeatureCollection("FAO/GAUL/2015/level2").filter(
            ee.Filter.eq("ADM0_NAME", "Mexico")
        )

        batch_size = 500
        for i in range(0, len(cells), batch_size):
            batch = cells[i:i + batch_size]

            for cell in batch:
                point = ee.Geometry.Point(cell["lng"], cell["lat"])

                # State
                state_result = states.filterBounds(point).first()
                try:
                    state_info = state_result.getInfo()
                    cell["estado"] = state_info["properties"].get("ADM1_NAME", None)
                except Exception:
                    cell["estado"] = None

                # Municipality
                muni_result = municipalities.filterBounds(point).first()
                try:
                    muni_info = muni_result.getInfo()
                    cell["municipio"] = muni_info["properties"].get("ADM2_NAME", None)
                except Exception:
                    cell["municipio"] = None

            logger.info("State indexing: %d/%d cells processed.", min(i + batch_size, len(cells)), len(cells))

    except Exception as e:
        logger.warning("Could not index states via GEE: %s", e)
        for cell in cells:
            cell.setdefault("estado", None)
            cell.setdefault("municipio", None)

    return cells


def save_cells_to_db(cells: list[dict]):
    """Save grid cells to the PostGIS grid_cells table."""
    from ..db.session import engine, init_db
    from ..db.models import GridCell
    from sqlalchemy.orm import Session

    init_db()

    with Session(engine) as session:
        batch_size = 5000
        for i in range(0, len(cells), batch_size):
            batch = cells[i:i + batch_size]
            records = [
                GridCell(
                    h3_index=c["h3_index"],
                    lat=c["lat"],
                    lng=c["lng"],
                    estado=c.get("estado"),
                    municipio=c.get("municipio"),
                    tipo_ecosistema=c.get("tipo_ecosistema"),
                    en_anp=c.get("en_anp", False),
                    nombre_anp=c.get("nombre_anp"),
                    cuenca_id=c.get("cuenca_id"),
                )
                for c in batch
            ]
            session.bulk_save_objects(records)
            session.commit()
            logger.info("Saved %d/%d cells to DB.", min(i + batch_size, len(cells)), len(cells))

    logger.info("All %d grid cells saved to PostGIS.", len(cells))


def main():
    """Full pipeline: generate cells → index → save to DB."""
    logging.basicConfig(level=logging.INFO)

    cells = generate_h3_cells()
    logger.info("Step 1/3: %d cells generated.", len(cells))

    cells = index_cells_with_anp(cells)
    logger.info("Step 2/3: ANP indexing complete.")

    cells = index_cells_with_states(cells)
    logger.info("Step 3/3: State/municipality indexing complete.")

    save_cells_to_db(cells)
    logger.info("Done! Grid saved to PostGIS.")


if __name__ == "__main__":
    main()

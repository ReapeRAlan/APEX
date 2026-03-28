"""
NASA FIRMS Active Fire / Hotspot Service for APEX.

Queries the FIRMS REST API for active fire detections (VIIRS & MODIS)
within a given AOI and date range.

Key design decisions (adapted from MACOF's proven firms.py):
  - NRT sources (*_NRT) → only last ~7 days from today
  - SP sources (*_SP)   → historical / archived data (weeks to years)
  - Auto-select NRT vs SP based on how recent the query dates are
  - Max 10 days per API request; longer ranges batched automatically
  - Total requests capped to avoid hammering the API (rate limit: 5000/10min)
"""

import os
import csv
import io
import logging
import time
from datetime import datetime, timedelta, date as dt_date
from typing import Optional

import requests
from shapely.geometry import shape, Point

logger = logging.getLogger(__name__)

FIRMS_API_BASE = "https://firms.modaps.eosdis.nasa.gov/api"
FIRMS_MAPSERVER_BASE = "https://firms.modaps.eosdis.nasa.gov/mapserver/"

SUPPORTED_SOURCES = {
    "LANDSAT_NRT", "MODIS_NRT", "MODIS_SP",
    "VIIRS_NOAA20_NRT", "VIIRS_NOAA20_SP",
    "VIIRS_NOAA21_NRT", "VIIRS_SNPP_NRT", "VIIRS_SNPP_SP",
}

# NRT sources — only last ~7 days
NRT_SOURCES = ["VIIRS_SNPP_NRT", "VIIRS_NOAA20_NRT", "VIIRS_NOAA21_NRT"]

# SP (Standard Processing) sources — historical archive
SP_SOURCES = ["VIIRS_SNPP_SP", "VIIRS_NOAA20_SP", "MODIS_SP"]

# How many days back NRT data is typically available
NRT_MAX_AGE_DAYS = 7

# Max days per API request (SP=5 per FIRMS docs, NRT=10)
MAX_DAYS_SP = 5
MAX_DAYS_NRT = 10

# Max API requests per FIRMS call to avoid rate-limit / timeout
# FIRMS rate limit is 5000 requests per 10 minutes
MAX_API_REQUESTS = 30

# For full-year timeline queries: 365/5 = 73 batches per source
MAX_API_REQUESTS_TIMELINE = 80


def _get_map_key() -> str:
    """Get FIRMS MAP_KEY from environment."""
    return os.environ.get("FIRMS_MAP_KEY", "").strip()


def check_key_status() -> dict:
    """Validate the FIRMS MAP_KEY and return status."""
    key = _get_map_key()
    if not key:
        return {"valid": False, "error": "FIRMS_MAP_KEY not set"}
    try:
        resp = requests.get(
            FIRMS_MAPSERVER_BASE + "mapkey_status/",
            params={"MAP_KEY": key},
            timeout=10,
        )
        resp.raise_for_status()
        return {"valid": True, **resp.json()}
    except Exception as e:
        return {"valid": False, "error": str(e)}


def _parse_csv(text: str) -> list[dict]:
    """Parse FIRMS CSV response into list of dicts."""
    text = (text or "").strip()
    if not text:
        return []
    if text.lower().startswith("invalid"):
        raise ValueError(f"FIRMS API error: {text[:200]}")
    rows = list(csv.DictReader(io.StringIO(text)))
    if rows and "latitude" not in rows[0]:
        return []
    return rows


def _select_sources(date_end: datetime, total_days: int = 10) -> list[str]:
    """Auto-select NRT or SP sources based on how recent the end date is.
    
    For large date ranges (>60 days), use fewer sources to stay within API limits.
    """
    now = datetime.utcnow()
    days_ago = (now - date_end).days

    if days_ago <= NRT_MAX_AGE_DAYS:
        logger.info("[FIRMS] Fechas recientes (%d dias) → usando fuentes NRT", days_ago)
        return list(NRT_SOURCES)
    else:
        # For large ranges (e.g. full year), pick fewer sources to stay within API limits
        # SP sources: max 5 days per request
        # 365 days / 5 days per batch = 73 batches; cap=80 allows 1 source
        if total_days > 180:
            # Full year: VIIRS_SNPP_SP (best coverage from 2012)
            sources = ["VIIRS_SNPP_SP"]
            logger.info(
                "[FIRMS] Rango anual (%d dias, %d dias atras) → usando %s (SP max=5d/req)",
                total_days, days_ago, sources,
            )
        elif total_days > 60:
            # Medium range: VIIRS_SNPP_SP (73 batches would exceed cap with 2 sources)
            sources = ["VIIRS_SNPP_SP"]
            logger.info(
                "[FIRMS] Rango medio (%d dias, %d dias atras) → usando %s",
                total_days, days_ago, sources,
            )
        else:
            # Short range: 2 SP sources fit within cap
            sources = ["VIIRS_SNPP_SP", "MODIS_SP"]
            logger.info(
                "[FIRMS] Fechas historicas (%d dias atras, %d dias) → usando %s",
                days_ago, total_days, sources,
            )
        return sources


def _fetch_single(
    map_key: str,
    source: str,
    area_part: str,
    day_range: int,
    date_str: Optional[str],
) -> list[dict]:
    """Single API request to FIRMS. Returns rows or empty list on error."""
    url = f"{FIRMS_API_BASE}/area/csv/{map_key}/{source}/{area_part}/{day_range}"
    if date_str:
        url += f"/{date_str}"

    try:
        resp = requests.get(url, timeout=45)

        if resp.status_code == 400:
            # 400 = Bad Request — typically means source doesn't cover this date
            body_preview = (resp.text or "")[:200].replace("\n", " ")
            logger.warning(
                "[FIRMS] 400 para %s fecha=%s — fuente no disponible para estas fechas (%s)",
                source, date_str or "latest", body_preview,
            )
            return []

        if resp.status_code == 404:
            logger.warning("[FIRMS] 404 para %s — sin datos", source)
            return []

        resp.raise_for_status()
        rows = _parse_csv(resp.text)
        for r in rows:
            r["_source_key"] = source
        if rows:
            logger.info("[FIRMS] %s fecha=%s → %d detecciones", source, date_str or "latest", len(rows))
        return rows

    except requests.exceptions.RequestException as e:
        logger.error("[FIRMS] Error de red %s: %s", source, e)
        return []
    except Exception as e:
        logger.error("[FIRMS] Error inesperado %s: %s", source, e)
        return []


def fetch_hotspots_for_bbox(
    bbox: tuple[float, float, float, float],
    sources: list[str],
    day_range: int = 2,
    date: Optional[str] = None,
) -> list[dict]:
    """
    Fetch active fire detections from FIRMS for a bounding box.

    Args:
        bbox: (west, south, east, north) in EPSG:4326
        sources: list of FIRMS source identifiers
        day_range: 1-5 days for SP sources, 1-10 for NRT (clamped per source)
        date: optional YYYY-MM-DD end date

    Returns:
        List of detection dicts with lat, lon, confidence, frp, etc.
    """
    map_key = _get_map_key()
    if not map_key:
        raise ValueError(
            "FIRMS_MAP_KEY no configurado. "
            "Obtén tu clave en https://firms.modaps.eosdis.nasa.gov/api/area/ "
            "y agrégala al archivo .env de APEX."
        )

    west, south, east, north = bbox
    area_part = f"{west:.6f},{south:.6f},{east:.6f},{north:.6f}"

    all_rows: list[dict] = []
    for source in sources:
        if source not in SUPPORTED_SOURCES:
            logger.warning("[FIRMS] Fuente no soportada: %s", source)
            continue
        # SP sources accept max 5 days, NRT accepts max 10
        is_sp = source.endswith("_SP")
        max_days = MAX_DAYS_SP if is_sp else MAX_DAYS_NRT
        clamped = max(1, min(max_days, int(day_range)))
        rows = _fetch_single(map_key, source, area_part, clamped, date)
        all_rows.extend(rows)

    return all_rows


def fetch_hotspots_for_aoi(
    aoi_geojson: dict,
    date_start: str,
    date_end: str,
    sources: Optional[list[str]] = None,
) -> list[dict]:
    """
    Fetch FIRMS hotspots for a GeoJSON AOI geometry within a date range.

    Automatically selects NRT vs SP sources based on date recency.
    Batches requests in 5-day windows (SP) or 10-day (NRT) with a request cap.

    Args:
        aoi_geojson: GeoJSON geometry (Polygon or MultiPolygon)
        date_start: YYYY-MM-DD
        date_end: YYYY-MM-DD
        sources: FIRMS sources to query (auto-detected if None)

    Returns:
        List of detection dicts filtered to the AOI polygon interior.
    """
    aoi_shape = shape(aoi_geojson)
    bounds = aoi_shape.bounds  # (minx, miny, maxx, maxy)
    bbox = (bounds[0] - 0.01, bounds[1] - 0.01, bounds[2] + 0.01, bounds[3] + 0.01)

    # Parse dates
    try:
        d_start = datetime.strptime(date_start, "%Y-%m-%d")
        d_end = datetime.strptime(date_end, "%Y-%m-%d")
    except (ValueError, TypeError):
        logger.error("[FIRMS] Fechas invalidas: %s - %s", date_start, date_end)
        return []

    total_days = (d_end - d_start).days + 1
    if total_days <= 0:
        logger.warning("[FIRMS] Rango de fechas vacío o invertido")
        return []

    # Auto-select sources if not specified
    if sources is None:
        sources = _select_sources(d_end, total_days)
    logger.info(
        "[FIRMS] Consultando %s → %s (%d dias) con fuentes: %s",
        date_start, date_end, total_days, sources,
    )

    # For large ranges, use higher API cap
    request_cap = MAX_API_REQUESTS_TIMELINE if total_days > 60 else MAX_API_REQUESTS

    # Determine batch size: SP sources max 5 days, NRT max 10
    is_sp = any(s.endswith("_SP") for s in sources)
    batch_days = MAX_DAYS_SP if is_sp else MAX_DAYS_NRT

    all_rows: list[dict] = []
    api_calls = 0

    if total_days <= batch_days:
        all_rows = fetch_hotspots_for_bbox(
            bbox, sources=sources, day_range=total_days, date=date_end
        )
        api_calls += len(sources)
    else:
        cursor = d_end
        remaining = total_days
        while remaining > 0 and api_calls < request_cap:
            chunk = min(remaining, batch_days)
            rows = fetch_hotspots_for_bbox(
                bbox, sources=sources, day_range=chunk,
                date=cursor.strftime("%Y-%m-%d"),
            )
            all_rows.extend(rows)
            api_calls += len(sources)
            cursor -= timedelta(days=chunk)
            remaining -= chunk
            # Throttle to avoid FIRMS "Exceeding allowed transaction limit"
            if remaining > 0:
                time.sleep(0.5)

        if remaining > 0:
            logger.warning(
                "[FIRMS] Limite de peticiones alcanzado (%d/%d). "
                "Quedan %d dias sin consultar. "
                "Considere reducir el rango de fechas.",
                api_calls, request_cap, remaining,
            )

    logger.info("[FIRMS] Total respuestas API: %d llamadas, %d filas brutas", api_calls, len(all_rows))

    # Filter points inside the AOI polygon
    filtered = []
    for row in all_rows:
        try:
            lat = float(row.get("latitude", 0))
            lon = float(row.get("longitude", 0))
            if aoi_shape.contains(Point(lon, lat)):
                filtered.append(row)
        except (ValueError, TypeError):
            continue

    # Deduplicate by (lat, lon, acq_date, acq_time, satellite)
    seen = set()
    unique = []
    for row in filtered:
        key = (
            row.get("latitude"), row.get("longitude"),
            row.get("acq_date"), row.get("acq_time"),
            row.get("satellite"),
        )
        if key not in seen:
            seen.add(key)
            unique.append(row)

    logger.info(
        "[FIRMS] Filtro AOI: %d brutas → %d dentro AOI → %d únicas",
        len(all_rows), len(filtered), len(unique),
    )
    return unique

"""
FIRMS NRT Active-Fire Hotspot Engine for APEX.

Converts raw FIRMS CSV detections into a GeoJSON FeatureCollection
with per-detection points (confidence, FRP, satellite) and optional
clustering of nearby detections into fire-event polygons.
"""

import numpy as np
from datetime import datetime
from shapely.geometry import Point, MultiPoint, mapping
from shapely.ops import unary_union


# Cluster radius in degrees (~375 m VIIRS pixel at equator ≈ 0.004°)
CLUSTER_RADIUS_DEG = 0.005
MIN_CLUSTER_SIZE = 1
MAX_FEATURES = 500


def _safe_float(val, default=0.0):
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _parse_acq_datetime(row: dict) -> str:
    """Parse acq_date + acq_time into ISO datetime string."""
    acq_date = row.get("acq_date", "")
    acq_time = str(row.get("acq_time", "")).strip().zfill(4)
    if len(acq_time) == 4 and acq_time.isdigit():
        return f"{acq_date}T{acq_time[:2]}:{acq_time[2:]}Z"
    return f"{acq_date}T00:00Z"


def _confidence_numeric(conf) -> float:
    """Normalize confidence to 0-1. VIIRS uses 'low'/'nominal'/'high',
    MODIS uses 0-100 integer."""
    if conf is None:
        return 0.5
    s = str(conf).strip().lower()
    if s == "high" or s == "h":
        return 0.9
    if s == "nominal" or s == "n":
        return 0.6
    if s == "low" or s == "l":
        return 0.3
    try:
        v = float(s)
        return v / 100.0 if v > 1 else v
    except (ValueError, TypeError):
        return 0.5


class FIRMSEngine:
    """Processes raw FIRMS detections into analysis-ready GeoJSON."""

    def process_detections(self, rows: list[dict]) -> tuple[dict, dict]:
        """
        Convert FIRMS CSV rows into GeoJSON FeatureCollection + stats.

        Returns:
            (geojson_fc, stats_dict)
        """
        if not rows:
            return (
                {"type": "FeatureCollection", "features": []},
                {
                    "hotspot_count": 0,
                    "total_frp_mw": 0,
                    "satellites": [],
                    "date_range": "",
                    "source": "NASA FIRMS (VIIRS/MODIS NRT)",
                },
            )

        features = []
        total_frp = 0.0
        satellites = set()
        dates = []

        for row in rows:
            lat = _safe_float(row.get("latitude"))
            lon = _safe_float(row.get("longitude"))
            if lat == 0 and lon == 0:
                continue

            frp = _safe_float(row.get("frp", 0))
            conf = _confidence_numeric(row.get("confidence"))
            sat = row.get("satellite", "unknown")
            instrument = row.get("instrument", "")
            bright_ti4 = _safe_float(row.get("bright_ti4", 0))
            bright_ti5 = _safe_float(row.get("bright_ti5", 0))
            acq_dt = _parse_acq_datetime(row)
            source_key = row.get("_source_key", "")

            total_frp += frp
            satellites.add(sat)
            if row.get("acq_date"):
                dates.append(row["acq_date"])

            feat = {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {
                    "type": "hotspot",
                    "frp_mw": round(frp, 1),
                    "confidence": round(conf, 2),
                    "confidence_label": row.get("confidence", ""),
                    "satellite": sat,
                    "instrument": instrument,
                    "bright_ti4": round(bright_ti4, 1),
                    "bright_ti5": round(bright_ti5, 1),
                    "acq_datetime": acq_dt,
                    "acq_date": row.get("acq_date", ""),
                    "source": source_key,
                },
            }
            features.append(feat)

        # Sort by FRP descending and limit
        features.sort(key=lambda f: f["properties"]["frp_mw"], reverse=True)
        features = features[:MAX_FEATURES]

        # Build stats
        date_sorted = sorted(set(dates)) if dates else []
        date_range = (
            f"{date_sorted[0]} → {date_sorted[-1]}"
            if len(date_sorted) > 1
            else (date_sorted[0] if date_sorted else "")
        )

        high_conf = sum(
            1 for f in features if f["properties"]["confidence"] >= 0.7
        )

        stats = {
            "hotspot_count": len(features),
            "high_confidence_count": high_conf,
            "total_frp_mw": round(total_frp, 1),
            "avg_frp_mw": round(total_frp / len(features), 1) if features else 0,
            "max_frp_mw": round(max((f["properties"]["frp_mw"] for f in features), default=0), 1),
            "satellites": sorted(satellites),
            "date_range": date_range,
            "source": "NASA FIRMS (VIIRS/MODIS NRT)",
        }

        geojson = {"type": "FeatureCollection", "features": features}
        return geojson, stats

    def cluster_detections(
        self, rows: list[dict], radius_deg: float = CLUSTER_RADIUS_DEG
    ) -> list[dict]:
        """
        Cluster nearby detections into fire-event polygons using
        simple spatial buffering and union.

        Returns list of cluster dicts with geometry, total FRP,
        detection count, and date range.
        """
        if not rows:
            return []

        points = []
        for row in rows:
            lat = _safe_float(row.get("latitude"))
            lon = _safe_float(row.get("longitude"))
            if lat == 0 and lon == 0:
                continue
            points.append((lon, lat, row))

        if not points:
            return []

        # Buffer each point and union overlapping ones
        buffered = [Point(lon, lat).buffer(radius_deg) for lon, lat, _ in points]
        union = unary_union(buffered)

        # Extract individual cluster polygons
        if union.geom_type == "Polygon":
            cluster_polys = [union]
        elif union.geom_type == "MultiPolygon":
            cluster_polys = list(union.geoms)
        else:
            return []

        clusters = []
        for cpoly in cluster_polys:
            # Find which detections belong to this cluster
            members = [
                row for lon, lat, row in points
                if cpoly.contains(Point(lon, lat))
            ]
            if len(members) < MIN_CLUSTER_SIZE:
                continue

            total_frp = sum(_safe_float(m.get("frp", 0)) for m in members)
            confs = [_confidence_numeric(m.get("confidence")) for m in members]
            dates = sorted(set(m.get("acq_date", "") for m in members if m.get("acq_date")))
            sats = sorted(set(m.get("satellite", "") for m in members))

            # Use convex hull of member points instead of buffer circle
            member_points = [
                Point(float(m["longitude"]), float(m["latitude"])) for m in members
            ]
            if len(member_points) >= 3:
                hull = MultiPoint(member_points).convex_hull.buffer(radius_deg * 0.5)
            else:
                hull = cpoly

            clusters.append({
                "type": "Feature",
                "geometry": mapping(hull),
                "properties": {
                    "type": "fire_cluster",
                    "detection_count": len(members),
                    "total_frp_mw": round(total_frp, 1),
                    "avg_confidence": round(float(np.mean(confs)), 2),
                    "satellites": sats,
                    "date_range": f"{dates[0]} → {dates[-1]}" if len(dates) > 1 else (dates[0] if dates else ""),
                    "area_ha": round(hull.area * (111_320 ** 2) * np.cos(np.radians(20)) / 10_000, 2),
                },
            })

        clusters.sort(key=lambda c: c["properties"]["total_frp_mw"], reverse=True)
        return clusters

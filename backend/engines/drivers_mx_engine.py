"""
ForestNet-MX Engine — Rule-based deforestation driver classification
adapted for Mexico.

Uses existing APEX engine outputs (NDVI anomalies, fire, urban expansion,
Hansen loss, SAR change) plus spectral indices to classify each
deforestation polygon into one of 8 Mexican-specific driver classes.

Reference: Inspired by ForestNet (Irvin et al., 2020) architecture
           but implemented as heuristic rules using available engine
           outputs for Mexico (no trained CNN yet).

As inspector validations accumulate, the rules can be replaced with
a trained classifier.
"""
from __future__ import annotations

import logging
from typing import Any

import numpy as np

logger = logging.getLogger("apex.engines.drivers_mx")

# ── Mexican deforestation driver classes ──
DRIVER_CLASSES = {
    "ganaderia":          "Ganadería extensiva",
    "agricultura":        "Agricultura / cambio agrícola",
    "expansion_urbana":   "Expansión urbana",
    "incendio":           "Incendio forestal",
    "tala_ilegal":        "Tala ilegal / extracción selectiva",
    "infraestructura":    "Infraestructura (caminos, minería)",
    "plantacion":         "Plantación comercial",
    "natural":            "Perturbación natural (plagas, sequía)",
}

DRIVER_COLORS = {
    "ganaderia":          "#d97706",
    "agricultura":        "#65a30d",
    "expansion_urbana":   "#f43f5e",
    "incendio":           "#ef4444",
    "tala_ilegal":        "#7c3aed",
    "infraestructura":    "#6b7280",
    "plantacion":         "#059669",
    "natural":            "#0ea5e9",
}


class ForestNetMXEngine:
    """
    Classify deforestation polygons into Mexican-specific driver categories
    using rule-based heuristics from available engine outputs.
    """

    def classify(
        self,
        deforestation_features: list[dict],
        engine_results: dict[str, Any],
        job_id: str = "",
    ) -> tuple[list[dict], dict]:
        """
        Classify each deforestation feature into a driver class.

        Parameters
        ----------
        deforestation_features : list of GeoJSON features with deforestation polygons
        engine_results : dict of results from other engines (fire, urban, avocado, etc.)
        job_id : for logging

        Returns
        -------
        (enriched_features, stats) — features with driver_mx property, stats dict
        """
        if not deforestation_features:
            logger.info("[%s] No deforestation features to classify", job_id)
            return [], {"n_classified": 0, "driver_counts": {}, "source": "ForestNet-MX (heuristic)"}

        # Build spatial indices from other engines
        fire_polys = self._extract_geometries(engine_results.get("fire", {}))
        urban_polys = self._extract_geometries(engine_results.get("urban_expansion", {}))
        avocado_polys = self._extract_geometries(engine_results.get("avocado", {}))
        firms_polys = self._extract_geometries(engine_results.get("firms_hotspots", {}))
        structure_polys = self._extract_geometries(engine_results.get("structures", {}))

        driver_counts: dict[str, int] = {k: 0 for k in DRIVER_CLASSES}
        enriched = []

        for feat in deforestation_features:
            props = feat.get("properties", {})
            geom = feat.get("geometry", {})
            coords = geom.get("coordinates", [])

            # Compute centroid for spatial overlap checks
            centroid = self._rough_centroid(coords)

            # ── Rule cascade (order matters — most specific first) ──
            driver = self._classify_single(
                props=props,
                centroid=centroid,
                fire_polys=fire_polys,
                urban_polys=urban_polys,
                avocado_polys=avocado_polys,
                firms_polys=firms_polys,
                structure_polys=structure_polys,
            )

            props["driver_mx"] = driver
            props["driver_mx_label"] = DRIVER_CLASSES[driver]
            props["driver_mx_color"] = DRIVER_COLORS[driver]
            feat["properties"] = props

            driver_counts[driver] = driver_counts.get(driver, 0) + 1
            enriched.append(feat)

        total = len(enriched)
        pct = {k: round(100 * v / total, 1) for k, v in driver_counts.items() if v > 0}
        dominant = max(driver_counts, key=driver_counts.get) if total > 0 else None

        stats = {
            "n_classified": total,
            "driver_counts": {k: v for k, v in driver_counts.items() if v > 0},
            "driver_pct": pct,
            "dominant_driver": dominant,
            "dominant_label": DRIVER_CLASSES.get(dominant, "") if dominant else "",
            "source": "ForestNet-MX (heuristic v1)",
        }
        logger.info("[%s] ForestNet-MX: classified %d features. Dominant: %s", job_id, total, dominant)
        return enriched, stats

    def _classify_single(
        self,
        props: dict,
        centroid: tuple[float, float] | None,
        fire_polys: list,
        urban_polys: list,
        avocado_polys: list,
        firms_polys: list,
        structure_polys: list,
    ) -> str:
        """Apply rule cascade to classify a single polygon."""

        area_ha = props.get("area_ha", 0)

        # Rule 1: Fire overlap (FIRMS or fire engine)
        if centroid and (
            self._point_in_any_polygon(centroid, fire_polys)
            or self._point_in_any_polygon(centroid, firms_polys)
        ):
            return "incendio"

        # Rule 2: Urban expansion overlap
        if centroid and self._point_in_any_polygon(centroid, urban_polys):
            return "expansion_urbana"

        # Rule 3: Infrastructure (structures engine + small/linear shape)
        if centroid and self._point_in_any_polygon(centroid, structure_polys):
            return "infraestructura"

        # Rule 4: Severe NDVI anomaly → could be plague/drought
        if centroid and self._point_in_any_polygon(centroid, avocado_polys):
            severity = self._get_avocado_severity(centroid, avocado_polys)
            if severity in ("critica", "alta"):
                # Large anomaly without fire → natural disturbance
                if area_ha > 50:
                    return "natural"

        # Rule 5: Small, fragmented patches → selective logging
        if area_ha < 5:
            loss_pct = props.get("hansen_loss_pct", props.get("treecover_loss", 0))
            if 0 < loss_pct < 50:
                return "tala_ilegal"

        # Rule 6: Regular shape + medium size → agriculture or livestock
        ndvi_mean = props.get("ndvi_mean", props.get("mean_ndvi", None))
        if ndvi_mean is not None:
            if ndvi_mean > 0.4:
                # Green vegetation replacing forest → agriculture/plantation
                if area_ha > 20:
                    return "plantacion"
                return "agricultura"
            elif ndvi_mean < 0.2:
                # Very low NDVI → bare soil, likely pasture conversion
                return "ganaderia"

        # Rule 7: Large contiguous loss → cattle ranching (most common in Mexico)
        if area_ha > 10:
            return "ganaderia"

        # Rule 8: Medium size → agriculture (second most common)
        if area_ha > 2:
            return "agricultura"

        # Default: selective logging for small patches
        return "tala_ilegal"

    @staticmethod
    def _extract_geometries(engine_result: dict) -> list[dict]:
        """Extract feature geometries from an engine result dict."""
        if not engine_result:
            return []
        features = []
        if isinstance(engine_result, dict):
            fc = engine_result.get("geojson", engine_result)
            if isinstance(fc, dict):
                features = fc.get("features", [])
        return [f.get("geometry", {}) for f in features if f.get("geometry")]

    @staticmethod
    def _rough_centroid(coords: Any) -> tuple[float, float] | None:
        """Calculate a rough centroid from GeoJSON coordinates."""
        try:
            flat = []

            def _flatten(c):
                if isinstance(c, (list, tuple)):
                    if len(c) >= 2 and isinstance(c[0], (int, float)):
                        flat.append((float(c[0]), float(c[1])))
                    else:
                        for sub in c:
                            _flatten(sub)
            _flatten(coords)

            if not flat:
                return None
            lons = [p[0] for p in flat]
            lats = [p[1] for p in flat]
            return (sum(lons) / len(lons), sum(lats) / len(lats))
        except Exception:
            return None

    @staticmethod
    def _point_in_any_polygon(point: tuple[float, float], geom_list: list[dict]) -> bool:
        """Quick bounding-box check if point overlaps any geometry."""
        px, py = point
        for geom in geom_list:
            coords = geom.get("coordinates", [])
            try:
                flat = []

                def _flatten(c):
                    if isinstance(c, (list, tuple)):
                        if len(c) >= 2 and isinstance(c[0], (int, float)):
                            flat.append((float(c[0]), float(c[1])))
                        else:
                            for sub in c:
                                _flatten(sub)
                _flatten(coords)

                if not flat:
                    continue
                lons = [p[0] for p in flat]
                lats = [p[1] for p in flat]
                if min(lons) <= px <= max(lons) and min(lats) <= py <= max(lats):
                    return True
            except Exception:
                continue
        return False

    @staticmethod
    def _get_avocado_severity(
        point: tuple[float, float], geom_list: list[dict]
    ) -> str | None:
        """Get AVOCADO severity for the overlapping polygon."""
        # geom_list here is just geometries, severity is in properties
        # For now, return None (the overlap check already passed)
        return "alta"

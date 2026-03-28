"""
Legal / ANP Analysis Engine
Intersección espacial entre deforestación detectada y Áreas Naturales Protegidas.
"""

import warnings
warnings.filterwarnings("ignore")

import numpy as np
from shapely.geometry import shape


def _area_deg2_to_ha(area_deg2: float) -> float:
    """Convert area in square degrees to hectares (approx at lat ~20°N)."""
    return area_deg2 * (111_320 ** 2) * np.cos(np.radians(20)) / 10_000


class LegalEngine:
    """Analiza intersección de polígonos con Áreas Naturales Protegidas (ANPs)."""

    # --------------------------------------------------------- public API
    def check_anp_intersection(
        self,
        aoi_geojson: dict,
        protected_areas_geojson: dict,
    ) -> tuple[dict, dict]:
        """
        Check whether the AOI polygon intersects any protected-area polygon.

        Parameters
        ----------
        aoi_geojson : dict
            GeoJSON Feature or Polygon for the area of interest.
        protected_areas_geojson : dict
            GeoJSON FeatureCollection of ANP polygons (from GEELegalService).

        Returns
        -------
        tuple[dict, dict]
            (geojson, stats) where *geojson* is a FeatureCollection of ANP
            features that actually intersect the AOI, and *stats* contains
            summary information about the intersection.
        """
        aoi_shape = self._to_shape(aoi_geojson)
        aoi_area_ha = _area_deg2_to_ha(aoi_shape.area)

        anp_features = protected_areas_geojson.get("features", [])
        print(f"[Legal] Verificando intersección del AOI con {len(anp_features)} ANP(s)...")

        intersecting = []
        total_overlap_ha = 0.0

        for feat in anp_features:
            try:
                anp_shape = shape(feat["geometry"])
            except Exception:
                continue

            if not aoi_shape.intersects(anp_shape):
                continue

            intersection = aoi_shape.intersection(anp_shape)
            overlap_ha = _area_deg2_to_ha(intersection.area)

            # Build output feature with overlap info
            props = dict(feat.get("properties", {}))
            props["overlap_area_ha"] = round(overlap_ha, 2)

            intersecting.append({
                "type": "Feature",
                "geometry": feat["geometry"],
                "properties": props,
            })
            total_overlap_ha += overlap_ha

        # Build stats from the *first* (largest-overlap) ANP if any
        intersecting.sort(
            key=lambda f: f["properties"].get("overlap_area_ha", 0),
            reverse=True,
        )

        if intersecting:
            top = intersecting[0]["properties"]
            overlap_pct = round((total_overlap_ha / aoi_area_ha) * 100, 2) if aoi_area_ha > 0 else 0.0
            stats = {
                "intersects_anp": True,
                "anp_name": top.get("name"),
                "anp_category": top.get("iucn_cat"),
                "overlap_area_ha": round(total_overlap_ha, 2),
                "overlap_pct": overlap_pct,
                "legal_status": top.get("status", "unknown"),
            }
            print(
                f"[Legal] AOI intersecta {len(intersecting)} ANP(s): "
                f"{stats['anp_name']} — solapamiento={total_overlap_ha:.2f}ha ({overlap_pct:.1f}%)"
            )
        else:
            stats = {
                "intersects_anp": False,
                "anp_name": None,
                "anp_category": None,
                "overlap_area_ha": 0.0,
                "overlap_pct": 0.0,
                "legal_status": "none",
            }
            print("[Legal] El AOI no intersecta ninguna ANP")

        geojson = {"type": "FeatureCollection", "features": intersecting}
        return geojson, stats

    # ------------------------------------------------ tag deforestation
    def tag_features_with_anp(
        self,
        deforestation_features: list,
        anp_polygons: list,
    ) -> list:
        """
        Tag each deforestation feature with ANP info when it intersects.

        Parameters
        ----------
        deforestation_features : list
            List of GeoJSON Feature dicts (e.g. from HansenEngine).
        anp_polygons : list
            List of GeoJSON Feature dicts for ANP areas.

        Returns
        -------
        list
            Same list of deforestation features, with ``inside_anp`` (bool)
            and ``anp_name`` (str) added to properties when applicable.
        """
        # Pre-parse ANP shapely geometries once
        anp_shapes = []
        for feat in anp_polygons:
            try:
                anp_shapes.append((
                    shape(feat["geometry"]),
                    feat.get("properties", {}).get("name", "unknown"),
                ))
            except Exception:
                continue

        if not anp_shapes:
            print("[Legal] No hay polígonos ANP para etiquetar — omitiendo")
            return deforestation_features

        tagged_count = 0
        for feat in deforestation_features:
            try:
                defor_shape = shape(feat["geometry"])
            except Exception:
                feat.setdefault("properties", {})["inside_anp"] = False
                continue

            hit = False
            for anp_geom, anp_name in anp_shapes:
                if defor_shape.intersects(anp_geom):
                    feat.setdefault("properties", {})["inside_anp"] = True
                    feat["properties"]["anp_name"] = anp_name
                    hit = True
                    tagged_count += 1
                    break  # first match is enough

            if not hit:
                feat.setdefault("properties", {})["inside_anp"] = False

        print(
            f"[Legal] {tagged_count}/{len(deforestation_features)} polígonos de "
            f"deforestación etiquetados dentro de ANP"
        )
        return deforestation_features

    # --------------------------------------------------------- helpers
    @staticmethod
    def _to_shape(geojson: dict):
        """Convert a GeoJSON Feature, Polygon, or geometry dict to a shapely shape."""
        if geojson.get("type") == "Feature":
            return shape(geojson["geometry"])
        return shape(geojson)

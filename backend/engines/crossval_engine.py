"""
Cross-validation Engine  --  DW vs MapBiomas LULC
Compara detecciones de deforestacion de Dynamic World contra clasificacion
LULC de MapBiomas para identificar desacuerdos (posibles falsos positivos).
"""

import warnings
warnings.filterwarnings("ignore")

import numpy as np
import rasterio
from pathlib import Path
from shapely.geometry import shape, mapping, Point

# --------------------------------------------------------------------------- #
# MapBiomas LULC class groupings (approximate)
# --------------------------------------------------------------------------- #
MB_FOREST_CODES = [1, 2, 3, 4, 5, 49]  # Forest classes
MB_NONFOREST_CODES = [
    14, 15, 18, 19, 20, 21, 22, 23, 24, 25,
    29, 30, 31, 32, 33, 36, 39, 40, 41, 46,
    47, 48, 62,
]

MAX_FEATURES = 500  # safety cap on features to compare


def _area_deg2_to_ha(area_deg2: float) -> float:
    """Convert area in square degrees to approximate hectares (Mexico ~20 N)."""
    return area_deg2 * (111_320 ** 2) * np.cos(np.radians(20)) / 10_000


class CrossValEngine:
    """
    Cross-validate Dynamic World deforestation detections against
    MapBiomas LULC raster to find agreement/disagreement zones.
    """

    def cross_validate(
        self,
        dw_geojson: dict,
        mapbiomas_raster_path: Path,
    ) -> tuple[dict, dict]:
        """
        Compare DW deforestation features against MapBiomas LULC classes.

        Parameters
        ----------
        dw_geojson : dict
            GeoJSON FeatureCollection from Dynamic World deforestation analysis.
            Each feature represents a zone flagged as "was forest, now non-forest".
        mapbiomas_raster_path : Path
            Path to single-band MapBiomas LULC GeoTIFF (categorical class codes).

        Returns
        -------
        tuple[dict, dict]
            (disagreement_geojson, stats)
            - disagreement_geojson: FeatureCollection of features where DW and
              MapBiomas disagree (potential false positives).
            - stats: dict with agreement_pct, disagreement_zones, total_compared,
              and source.
        """
        print("[CrossVal] Iniciando validacion cruzada DW vs MapBiomas...")

        # ----- open MapBiomas raster -----
        with rasterio.open(str(mapbiomas_raster_path)) as src:
            mb_data = src.read(1).astype(np.uint8)
            mb_transform = src.transform
            mb_bounds = src.bounds
            mb_height, mb_width = mb_data.shape

        print(
            f"[CrossVal] MapBiomas raster: {mb_width}x{mb_height}px, "
            f"bounds=({mb_bounds.left:.4f}, {mb_bounds.bottom:.4f}, "
            f"{mb_bounds.right:.4f}, {mb_bounds.top:.4f})"
        )

        # ----- extract DW features -----
        dw_features = dw_geojson.get("features", [])
        if not dw_features:
            print("[CrossVal] No hay features DW para comparar")
            empty_fc = {"type": "FeatureCollection", "features": []}
            stats = {
                "agreement_pct": 0.0,
                "disagreement_zones": 0,
                "total_compared": 0,
                "source": "MapBiomas Mexico v1.0",
            }
            return empty_fc, stats

        # Limit features for performance
        dw_features = dw_features[:MAX_FEATURES]
        print(f"[CrossVal] Comparando {len(dw_features)} detecciones DW...")

        disagreement_features = []
        agreement_count = 0
        disagreement_count = 0
        skipped = 0

        for feat in dw_features:
            geom = feat.get("geometry")
            props = feat.get("properties", {})
            if geom is None:
                skipped += 1
                continue

            poly = shape(geom)

            # Sample the MapBiomas raster at the centroid and across the polygon
            mb_classes = self._sample_raster_at_polygon(
                poly, mb_data, mb_transform, mb_width, mb_height
            )

            if len(mb_classes) == 0:
                # Feature falls outside raster extent
                skipped += 1
                continue

            # Determine dominant MapBiomas class in this zone
            unique, counts = np.unique(mb_classes, return_counts=True)
            dominant_class = int(unique[np.argmax(counts)])

            # DW says: "was forest, now non-forest" (deforestation detection)
            # If MapBiomas says this location IS forest -> disagreement
            # (DW says deforested, but MapBiomas still shows forest)
            #
            # If MapBiomas says non-forest -> agreement
            # (both agree the area is non-forest / was converted)
            mb_is_forest = dominant_class in MB_FOREST_CODES
            mb_is_nonforest = dominant_class in MB_NONFOREST_CODES

            if mb_is_forest:
                # Disagreement: DW says deforested, MapBiomas says still forest
                disagreement_count += 1
                area_ha = _area_deg2_to_ha(poly.area)
                disagreement_features.append({
                    "type": "Feature",
                    "geometry": mapping(poly),
                    "properties": {
                        **props,
                        "crossval_status": "disagreement",
                        "mb_dominant_class": dominant_class,
                        "mb_class_label": "forest",
                        "dw_says": "deforested",
                        "mb_says": "forest",
                        "area_ha": round(area_ha, 2),
                        "note": "Potential false positive: MapBiomas classifies as forest",
                    },
                })
            elif mb_is_nonforest:
                # Agreement: both indicate non-forest / conversion
                agreement_count += 1
            else:
                # Unknown / unmapped class -- count as partial agreement
                agreement_count += 1

        total_compared = agreement_count + disagreement_count
        agreement_pct = (
            round(100.0 * agreement_count / total_compared, 1)
            if total_compared > 0
            else 0.0
        )

        print(
            f"[CrossVal] Resultados: {agreement_count} acuerdos, "
            f"{disagreement_count} desacuerdos, {skipped} omitidos"
        )
        print(
            f"[CrossVal] Tasa de acuerdo: {agreement_pct}% "
            f"({total_compared} zonas comparadas)"
        )

        disagreement_geojson = {
            "type": "FeatureCollection",
            "features": disagreement_features,
        }

        stats = {
            "agreement_pct": agreement_pct,
            "disagreement_zones": disagreement_count,
            "total_compared": total_compared,
            "source": "MapBiomas Mexico v1.0",
        }

        return disagreement_geojson, stats

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _sample_raster_at_polygon(
        poly,
        raster_data: np.ndarray,
        transform,
        width: int,
        height: int,
        max_samples: int = 50,
    ) -> np.ndarray:
        """
        Sample raster values within a polygon.

        Uses the centroid plus a grid of interior points to get a
        representative set of LULC classes under the polygon.

        Returns array of class codes (may be empty if polygon is outside raster).
        """
        samples = []

        # Always sample centroid
        centroid = poly.centroid
        col, row = ~transform * (centroid.x, centroid.y)
        col, row = int(round(col)), int(round(row))
        if 0 <= row < height and 0 <= col < width:
            samples.append(raster_data[row, col])

        # Sample a grid across the polygon bounding box
        minx, miny, maxx, maxy = poly.bounds
        n_side = min(int(np.sqrt(max_samples)), 7)
        xs = np.linspace(minx, maxx, n_side + 2)[1:-1]
        ys = np.linspace(miny, maxy, n_side + 2)[1:-1]

        for x in xs:
            for y in ys:
                pt = Point(x, y)
                if not poly.contains(pt):
                    continue
                c, r = ~transform * (x, y)
                c, r = int(round(c)), int(round(r))
                if 0 <= r < height and 0 <= c < width:
                    samples.append(raster_data[r, c])

        return np.array(samples, dtype=np.uint8)

"""
Hansen Global Forest Change v1.12 — Analysis Engine
Vectoriza pérdida forestal por año, calcula estadísticas históricas.
"""

import warnings
warnings.filterwarnings("ignore")

import numpy as np
import rasterio
from rasterio.features import shapes
from scipy.ndimage import binary_opening, binary_closing
from shapely.geometry import shape, mapping
from pathlib import Path

MIN_AREA_HA = 0.5
MAX_FEATURES = 200


def _area_deg2_to_ha(area_deg2: float) -> float:
    return area_deg2 * (111_320 ** 2) * np.cos(np.radians(20)) / 10_000


class HansenEngine:
    """Analiza pérdida forestal histórica a partir de rasters Hansen GFC."""

    def analyze_historical_loss(
        self,
        raster_path: Path,
        start_year: int = 2018,
        end_year: int = 2024,
    ) -> tuple[dict, dict]:
        """
        Vectoriza zonas de pérdida forestal del raster Hansen.

        Raster bands (from GEEHansenService):
          Band 1: treecover2000 (uint8, 0-100)
          Band 2: loss_filtered (uint8, 0/1 — masked by year+forest threshold)
          Band 3: lossyear_filtered (uint8, year offset since 2000)

        Returns (geojson_fc, stats_dict).
        """
        with rasterio.open(raster_path) as src:
            treecover = src.read(1).astype(np.float32)
            loss = src.read(2).astype(np.uint8)
            lossyear = src.read(3).astype(np.uint8)
            transform = src.transform

        # Binary mask: pixels with confirmed loss
        loss_mask = loss > 0

        # Morphological cleanup
        struct = np.ones((3, 3), dtype=bool)
        clean = binary_opening(loss_mask, structure=struct)
        clean = binary_closing(clean, structure=struct).astype(np.uint8)

        # Vectorize
        features = []
        for geom, val in shapes(clean, transform=transform):
            if val != 1:
                continue
            poly = shape(geom)
            area_ha = _area_deg2_to_ha(poly.area)
            if area_ha < MIN_AREA_HA:
                continue

            # Extract dominant loss year within polygon
            from rasterio.features import rasterize
            poly_rast = rasterize(
                [(geom, 1)], out_shape=loss.shape,
                transform=transform, dtype=np.uint8,
            )
            pmask = poly_rast == 1

            year_vals = lossyear[pmask]
            year_vals = year_vals[year_vals > 0]
            if len(year_vals) > 0:
                dominant_year = int(np.argmax(np.bincount(year_vals.astype(int))))
                loss_year = 2000 + dominant_year
            else:
                loss_year = 0

            # Average original tree cover within polygon
            tc_vals = treecover[pmask]
            tc_vals = tc_vals[tc_vals >= 0]
            avg_treecover = round(float(np.nanmean(tc_vals)), 1) if len(tc_vals) > 0 else 0

            # Confidence based on tree cover density (higher cover = more confident)
            confidence = round(min(avg_treecover / 100.0, 1.0), 3)

            features.append({
                "type": "Feature",
                "geometry": mapping(poly),
                "properties": {
                    "loss_year": loss_year,
                    "area_ha": round(area_ha, 2),
                    "original_treecover_pct": avg_treecover,
                    "confidence": confidence,
                },
            })

        # Sort by area descending, limit to top N
        features.sort(key=lambda f: f["properties"]["area_ha"], reverse=True)
        features = features[:MAX_FEATURES]

        total_loss_ha = sum(f["properties"]["area_ha"] for f in features)
        avg_confidence = (
            round(float(np.mean([f["properties"]["confidence"] for f in features])), 3)
            if features else 0
        )

        # Loss by year
        loss_by_year = {}
        for f in features:
            yr = f["properties"]["loss_year"]
            if yr > 0:
                loss_by_year[str(yr)] = round(
                    loss_by_year.get(str(yr), 0) + f["properties"]["area_ha"], 2
                )

        # Average tree cover across all loss polygons
        all_tc = [f["properties"]["original_treecover_pct"] for f in features if f["properties"]["original_treecover_pct"] > 0]
        avg_tc = round(float(np.mean(all_tc)), 1) if all_tc else 0

        geojson = {"type": "FeatureCollection", "features": features}
        stats = {
            "total_loss_ha": round(total_loss_ha, 1),
            "n_features": len(features),
            "loss_by_year": loss_by_year,
            "avg_treecover_pct": avg_tc,
            "confidence": avg_confidence,
            "start_year": start_year,
            "end_year": end_year,
            "source": "Hansen GFC v1.12 (UMD)",
        }

        print(f"[Hansen] {len(features)} polígonos, pérdida total={total_loss_ha:.1f}ha, conf={avg_confidence}")
        return geojson, stats

    def get_loss_by_year(self, raster_path: Path) -> dict:
        """Return raw pixel-count-based loss area by year."""
        with rasterio.open(raster_path) as src:
            loss = src.read(2).astype(np.uint8)
            lossyear = src.read(3).astype(np.uint8)
            transform = src.transform

        # Approximate pixel area in hectares
        pixel_deg = abs(transform.a)
        pixel_m = pixel_deg * 111_320 * np.cos(np.radians(20))
        pixel_ha = (pixel_m ** 2) / 10_000

        loss_mask = loss > 0
        year_vals = lossyear[loss_mask]

        result = {}
        for yr_offset in np.unique(year_vals):
            if yr_offset == 0:
                continue
            year = 2000 + int(yr_offset)
            count = int(np.sum(year_vals == yr_offset))
            result[str(year)] = round(count * pixel_ha, 2)

        return result

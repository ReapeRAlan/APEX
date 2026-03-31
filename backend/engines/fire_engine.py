"""
MODIS MCD64A1 Burned Area — Analysis Engine
Vectoriza areas quemadas, correlaciona incendios con deforestación.
"""

import warnings
warnings.filterwarnings("ignore")

import numpy as np
import rasterio
from rasterio.features import shapes
from scipy.ndimage import binary_opening, binary_closing
from shapely.geometry import shape, mapping
from pathlib import Path

MIN_AREA_HA = 0.25
MAX_FEATURES = 200


def _area_deg2_to_ha(area_deg2: float) -> float:
    return area_deg2 * (111_320 ** 2) * np.cos(np.radians(20)) / 10_000


class FireEngine:
    """Analiza áreas quemadas a partir de rasters MODIS MCD64A1."""

    def detect_burned_areas(
        self,
        raster_path: Path,
    ) -> tuple[dict, dict]:
        """
        Vectoriza zonas quemadas del raster MODIS MCD64A1.

        Raster band:
          Band 1: BurnDate (uint16, 0=no burn, 1-366=day of year)

        Returns (geojson_fc, stats_dict).
        """
        with rasterio.open(raster_path) as src:
            burndate = src.read(1).astype(np.int16)
            transform = src.transform

        # Binary mask: pixels with confirmed burn
        burn_mask = burndate > 0

        # Morphological cleanup
        struct = np.ones((3, 3), dtype=bool)
        clean = binary_opening(burn_mask, structure=struct)
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

            # Extract dominant burn day-of-year within polygon
            from rasterio.features import rasterize
            poly_rast = rasterize(
                [(geom, 1)], out_shape=burndate.shape,
                transform=transform, dtype=np.uint8,
            )
            pmask = poly_rast == 1

            doy_vals = burndate[pmask]
            doy_vals = doy_vals[doy_vals > 0]
            if len(doy_vals) > 0:
                burn_date_doy = int(np.argmax(np.bincount(doy_vals.astype(int))))
            else:
                burn_date_doy = 0

            # Confidence: proportion of pixels within the polygon that actually
            # burned — a fully burned polygon gets confidence ~1.0
            total_px = int(np.sum(pmask))
            burned_px = int(np.sum(burndate[pmask] > 0))
            confidence = round(burned_px / total_px, 3) if total_px > 0 else 0.0

            features.append({
                "type": "Feature",
                "geometry": mapping(poly),
                "properties": {
                    "burn_date_doy": burn_date_doy,
                    "area_ha": round(area_ha, 2),
                    "confidence": confidence,
                },
            })

        # Sort by area descending, limit to top N
        features.sort(key=lambda f: f["properties"]["area_ha"], reverse=True)
        features = features[:MAX_FEATURES]

        total_burned_ha = sum(f["properties"]["area_ha"] for f in features)
        fire_count = len(features)

        geojson = {"type": "FeatureCollection", "features": features}
        stats = {
            "total_burned_ha": round(total_burned_ha, 1),
            "fire_count": fire_count,
            "source": "MODIS MCD64A1",
        }

        print(f"[Fire] {fire_count} areas quemadas, {total_burned_ha:.1f} ha")
        return geojson, stats

    def correlate_fire_deforestation(
        self,
        fire_features: list,
        deforestation_features: list,
    ) -> dict:
        """
        Correlaciona incendios con deforestación.

        Para cada feature de deforestación, verifica si algún incendio
        lo intersecta. Retorna el porcentaje de deforestación posiblemente
        causada por fuego.
        """
        if not deforestation_features:
            print("[Fire] Sin features de deforestación para correlacionar")
            return {"fire_related_deforestation_pct": 0.0, "n_correlated": 0}

        fire_shapes = [shape(f["geometry"]) for f in fire_features]

        n_correlated = 0
        for defor_feat in deforestation_features:
            defor_poly = shape(defor_feat["geometry"])
            for fire_poly in fire_shapes:
                if defor_poly.intersects(fire_poly):
                    n_correlated += 1
                    break

        total = len(deforestation_features)
        pct = round((n_correlated / total) * 100, 1)

        print(f"[Fire] Correlación fuego-deforestación: {n_correlated}/{total} ({pct}%)")
        return {
            "fire_related_deforestation_pct": pct,
            "n_correlated": n_correlated,
        }

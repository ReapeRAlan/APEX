import warnings
warnings.filterwarnings("ignore")

import numpy as np
from rasterio.features import shapes
from shapely.geometry import shape, mapping
from pathlib import Path

from ..services.spectral_indices import SpectralBands, SpectralIndices


class DeforestationEngine:
    """Motor 1: Deteccion de deforestacion multi-indice (NDVI+BSI+SAVI+NBR+EVI+NDRE)."""

    MIN_AREA_DEG2 = 5e-8  # ~0.5 ha filtro de ruido

    def predict_from_raster(self, raster_path: Path, aoi_geojson: dict) -> tuple[dict, dict]:
        bands = SpectralBands.from_raster(raster_path)
        idx = SpectralIndices.compute_all(bands)
        mask, confidence = SpectralIndices.deforestation_mask(idx)

        mask_u8 = mask.astype(np.uint8)
        total_deforested_px = int(mask_u8.sum())
        total_px = mask_u8.size

        polygons = []
        for geom, val in shapes(mask_u8, transform=bands.transform):
            if val != 1:
                continue
            poly = shape(geom)
            if poly.area < self.MIN_AREA_DEG2:
                continue
            area_ha = poly.area * (111_320 ** 2) * np.cos(np.radians(20)) / 10_000
            # Confianza media de los pixeles dentro de este poligono
            # (usar media global como proxy — vectorizar fino es costoso)
            mean_conf = float(np.nanmean(confidence[mask])) if total_deforested_px > 0 else 0.5
            mean_ndvi = float(np.nanmean(idx["NDVI"][mask])) if total_deforested_px > 0 else 0.0
            polygons.append({
                "type": "Feature",
                "geometry": mapping(poly),
                "properties": {
                    "area_ha": round(area_ha, 2),
                    "ndvi_mean": round(mean_ndvi, 4),
                    "confidence": round(mean_conf, 2),
                    "type": "suelo_expuesto",
                },
            })

        polygons.sort(key=lambda f: f["properties"]["area_ha"], reverse=True)
        polygons = polygons[:50]

        total_area = sum(f["properties"]["area_ha"] for f in polygons)
        pct = round(100 * total_deforested_px / max(total_px, 1), 1)
        avg_conf = round(float(np.nanmean(confidence[mask])), 2) if total_deforested_px > 0 else 0.0

        geojson = {"type": "FeatureCollection", "features": polygons}
        stats = {
            "area_ha": round(total_area, 1),
            "percent_lost": pct,
            "confidence": avg_conf,
            "n_polygons": len(polygons),
        }
        print(f"[Deforestation] {len(polygons)} poligonos, {total_area:.1f} ha, {pct}% area, conf={avg_conf}")
        return geojson, stats
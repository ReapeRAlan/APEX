"""
Drivers of Forest Loss — Analysis Engine
Clasifica y vectoriza drivers de deforestación a partir del raster
WRI / Google DeepMind (1km categorical).
"""

import warnings
warnings.filterwarnings("ignore")

import numpy as np
import rasterio
from rasterio.features import shapes
from scipy.ndimage import binary_opening, binary_closing
from shapely.geometry import shape, mapping
from pathlib import Path

MIN_AREA_HA = 1.0  # 1km resolution → larger minimum area
MAX_FEATURES = 200

DRIVER_LABELS = {
    1: "Agricultura permanente",
    2: "Commodities (mineria/energia)",
    3: "Cultivo rotacional",
    4: "Tala",
    5: "Incendios",
    6: "Asentamientos e infraestructura",
    7: "Perturbacion natural",
}


def _area_deg2_to_ha(area_deg2: float) -> float:
    return area_deg2 * (111_320 ** 2) * np.cos(np.radians(20)) / 10_000


class DriversEngine:
    """Analiza drivers de deforestación a partir de rasters WRI/Google DeepMind."""

    def classify_drivers(
        self,
        raster_path: Path,
    ) -> tuple[dict, dict]:
        """
        Vectoriza zonas por clase de driver del raster categorical.

        Raster band (from GEEDriversService):
          Band 1: primary classification (uint8, 1-7 categorical)

        Returns (geojson_fc, stats_dict).
        """
        with rasterio.open(raster_path) as src:
            data = src.read(1).astype(np.uint8)
            transform = src.transform

        # Count pixels per class for stats
        unique_classes, counts = np.unique(data, return_counts=True)
        class_counts = {int(c): int(n) for c, n in zip(unique_classes, counts) if c > 0}
        total_classified = sum(class_counts.values())

        if total_classified == 0:
            print("[Drivers] No se encontraron píxeles clasificados en el raster")
            geojson = {"type": "FeatureCollection", "features": []}
            stats = {
                "drivers": {},
                "total_pixels": 0,
                "dominant_driver": "N/A",
                "n_features": 0,
                "source": "WRI / Google DeepMind",
            }
            return geojson, stats

        # Percentage distribution
        driver_pcts = {}
        for cls, cnt in class_counts.items():
            label = DRIVER_LABELS.get(cls, f"Clase {cls}")
            driver_pcts[label] = round(cnt / total_classified * 100, 2)

        print(f"[Drivers] {total_classified} píxeles clasificados en {len(class_counts)} clases")
        for label, pct in sorted(driver_pcts.items(), key=lambda x: x[1], reverse=True):
            print(f"[Drivers]   {label}: {pct}%")

        # Vectorize each driver class with morphological cleanup
        struct = np.ones((3, 3), dtype=bool)
        features = []

        for driver_code in sorted(class_counts.keys()):
            label = DRIVER_LABELS.get(driver_code, f"Clase {driver_code}")

            # Binary mask for this class
            class_mask = (data == driver_code)

            # Morphological cleanup
            clean = binary_opening(class_mask, structure=struct)
            clean = binary_closing(clean, structure=struct).astype(np.uint8)

            # Vectorize
            for geom, val in shapes(clean, transform=transform):
                if val != 1:
                    continue
                poly = shape(geom)
                area_ha = _area_deg2_to_ha(poly.area)
                if area_ha < MIN_AREA_HA:
                    continue

                features.append({
                    "type": "Feature",
                    "geometry": mapping(poly),
                    "properties": {
                        "driver_class": label,
                        "driver_code": int(driver_code),
                        "area_ha": round(area_ha, 2),
                    },
                })

        # Sort by area descending, limit to MAX_FEATURES
        features.sort(key=lambda f: f["properties"]["area_ha"], reverse=True)
        features = features[:MAX_FEATURES]

        # Determine dominant driver
        dominant_driver = max(driver_pcts, key=driver_pcts.get) if driver_pcts else "N/A"

        geojson = {"type": "FeatureCollection", "features": features}
        stats = {
            "drivers": driver_pcts,
            "total_pixels": total_classified,
            "dominant_driver": dominant_driver,
            "n_features": len(features),
            "source": "WRI / Google DeepMind",
        }

        print(f"[Drivers] {len(features)} polígonos vectorizados, driver dominante: {dominant_driver}")
        return geojson, stats

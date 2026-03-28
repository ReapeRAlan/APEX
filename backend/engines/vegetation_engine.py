import warnings
warnings.filterwarnings("ignore")

import numpy as np
from rasterio.features import shapes
from shapely.geometry import shape, mapping
from pathlib import Path

from ..services.spectral_indices import SpectralBands, SpectralIndices


CLASS_NAMES = {
    0: "agua", 1: "bosque_denso", 2: "bosque_ralo",
    3: "pastizal", 4: "suelo", 5: "urbano", 6: "quemado",
}


class VegetationEngine:
    """Motor 3: Clasificacion de vegetacion por indices espectrales (7 clases)."""

    MIN_AREA_DEG2 = 1e-8  # ~0.1 ha
    MAX_FEATURES = 200

    def classify_from_raster(self, raster_path: Path) -> tuple[dict, dict]:
        bands = SpectralBands.from_raster(raster_path)
        idx = SpectralIndices.compute_all(bands)
        class_map = SpectralIndices.classify_vegetation(idx)

        H, W = class_map.shape
        total_valid = max(H * W, 1)

        # Porcentajes por clase
        class_pcts = {}
        for code, name in CLASS_NAMES.items():
            pct = round(100 * float(np.sum(class_map == code)) / total_valid, 1)
            class_pcts[name] = pct

        # Vectorizar por clase
        features = []
        for code, clase in CLASS_NAMES.items():
            if class_pcts[clase] < 0.1:
                continue
            mask = (class_map == code).astype(np.uint8)
            for geom, val in shapes(mask, transform=bands.transform):
                if val != 1:
                    continue
                poly = shape(geom)
                if poly.area < self.MIN_AREA_DEG2:
                    continue
                area_ha = poly.area * (111_320 ** 2) * np.cos(np.radians(20)) / 10_000
                features.append({
                    "type": "Feature",
                    "geometry": mapping(poly),
                    "properties": {
                        "class": clase,
                        "area_ha": round(area_ha, 2),
                    },
                })

        features.sort(key=lambda f: f["properties"]["area_ha"], reverse=True)
        features = features[:self.MAX_FEATURES]

        geojson = {"type": "FeatureCollection", "features": features}
        stats = {"classes": class_pcts}

        print(f"[Vegetation] {len(features)} poligonos, clases: {class_pcts}")
        return geojson, stats
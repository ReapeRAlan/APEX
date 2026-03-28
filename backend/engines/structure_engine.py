import warnings
warnings.filterwarnings("ignore")


class StructureEngine:
    """Motor 2: Deteccion de estructuras.
    Nota: Sentinel-2 (10-30m/px) no tiene resolucion suficiente para detectar
    edificios individuales. Este motor requiere imagenes de alta resolucion (<1m/px).
    """

    def predict_from_raster(self, raster_path, aoi_geojson: dict) -> tuple[dict, dict]:
        print("[Structures] Motor deshabilitado: Sentinel-2 no tiene resolucion suficiente (<1m/px requerido)")
        geojson = {
            "type": "FeatureCollection",
            "features": [],
        }
        stats = {
            "count": 0,
            "note": "Requiere imagenes de alta resolucion (<1m/px). Sentinel-2 a 10-30m/px es insuficiente para deteccion de estructuras individuales.",
        }
        return geojson, stats
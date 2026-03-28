"""
CCDC Engine — Continuous Change Detection and Classification
Runs Google Earth Engine's ee.Algorithms.TemporalSegmentation.Ccdc() server-side
on a Sentinel-2 NDVI time series, downloads only change magnitude + break date,
and interprets breakpoints as gradual degradation vs sudden clearing.
"""

import warnings
warnings.filterwarnings("ignore")

import ee
import hashlib
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import rasterio
from rasterio.features import shapes, rasterize
from scipy.ndimage import binary_opening, binary_closing
from shapely.geometry import shape, mapping

from ..config import settings

logger = logging.getLogger(__name__)

SENTINEL2_COLLECTION = "COPERNICUS/S2_SR_HARMONIZED"
SCALE = 10          # 10m — Sentinel-2 native resolution
MAX_TILE_PX = 1800  # stay under computePixels 2048px limit


def _area_deg2_to_ha(area_deg2: float) -> float:
    """Convert area in squared degrees to hectares (approximate at lat ~20)."""
    return area_deg2 * (111_320 ** 2) * np.cos(np.radians(20)) / 10_000


class CCDCEngine:
    """
    Motor CCDC: deteccion continua de cambios en series temporales Sentinel-2.

    Ejecuta ee.Algorithms.TemporalSegmentation.Ccdc() en GEE y descarga
    unicamente las bandas de magnitud de cambio y fecha de quiebre (tBreak).
    Clasifica los quiebres en:
      - desmonte_subito     (magnitud NDVI < -0.3)
      - degradacion_gradual (-0.3 <= magnitud < -0.1)
      - sin_cambio          (-0.1 <= magnitud < 0.1)
      - recuperacion        (magnitud >= 0.1)
    """

    MAX_FEATURES = 200
    MIN_AREA_HA = 0.5

    _HV_URL = "https://earthengine-highvolume.googleapis.com"

    def __init__(self):
        self.logger = logging.getLogger(__name__)

    # ------------------------------------------------------------------
    # GEE initialization
    # ------------------------------------------------------------------
    def _initialize_gee(self):
        """Initialize Google Earth Engine with try/except fallback chain."""
        project = os.getenv("GEE_PROJECT", "profepa-deforestation")
        try:
            ee.Initialize(project=project, opt_url=self._HV_URL)
            self.logger.info("GEE inicializado con high-volume endpoint (proyecto=%s)", project)
        except Exception:
            try:
                ee.Initialize(project=project)
                self.logger.info("GEE inicializado sin high-volume (proyecto=%s)", project)
            except Exception:
                self.logger.warning("Token GEE no encontrado, abriendo autenticacion interactiva")
                ee.Authenticate()
                ee.Initialize(project=project)
                self.logger.info("GEE inicializado post-auth (proyecto=%s)", project)

    # ------------------------------------------------------------------
    # Cache helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _cache_hash(coords: list, start_date: str, end_date: str) -> str:
        """MD5 hash of AOI coords + date range for cache key."""
        payload = json.dumps(
            {"coords": coords, "start": start_date, "end": end_date},
            sort_keys=True,
        )
        return hashlib.md5(payload.encode()).hexdigest()[:12]

    @staticmethod
    def _extract_coords(aoi_geojson: dict) -> list:
        """Extract coordinate ring from various GeoJSON structures."""
        if aoi_geojson.get("type") == "Polygon":
            return aoi_geojson["coordinates"][0]
        if aoi_geojson.get("type") == "Feature":
            return aoi_geojson["geometry"]["coordinates"][0]
        return aoi_geojson.get("coordinates", [[]])[0]

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------
    def run_ccdc(
        self,
        aoi_geojson: dict,
        start_date: str,
        end_date: str,
        job_id: str,
    ) -> tuple[dict, dict]:
        """
        Run CCDC on a Sentinel-2 NDVI time series within GEE and return
        vectorized breakpoints with change classification.

        Parameters
        ----------
        aoi_geojson : dict   GeoJSON geometry (Polygon or Feature)
        start_date  : str    ISO date, e.g. "2019-01-01"
        end_date    : str    ISO date, e.g. "2023-12-31"
        job_id      : str    Unique job identifier for caching/filenames

        Returns
        -------
        (geojson_featurecollection, stats_dict)
        """
        self.logger.info(
            "[CCDC] Iniciando run_ccdc job=%s  %s -> %s", job_id, start_date, end_date
        )

        # --- Initialize GEE ---
        self._initialize_gee()

        coords = self._extract_coords(aoi_geojson)

        # --- Cache check ---
        output_dir = Path(settings.DATA_DIR)
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{job_id}_ccdc.tif"
        meta_path = output_dir / f"{job_id}_ccdc.json"
        cache_key = self._cache_hash(coords, start_date, end_date)

        if output_path.exists() and meta_path.exists():
            try:
                cached = json.loads(meta_path.read_text())
                if cached.get("cache_key") == cache_key:
                    self.logger.info(
                        "[CCDC] Cache valido (%s) -> %s", cache_key[:8], output_path
                    )
                    return self._vectorize_breakpoints(str(output_path))
            except Exception:
                pass
            output_path.unlink(missing_ok=True)
            meta_path.unlink(missing_ok=True)

        # --- Build Sentinel-2 SR collection ---
        aoi_ee = ee.Geometry.Polygon(coords)

        def mask_s2_clouds(image):
            scl = image.select("SCL")
            clear = scl.neq(9).And(scl.neq(10)).And(scl.neq(3))
            return image.updateMask(clear)

        def add_ndvi(image):
            ndvi = image.normalizedDifference(["B8", "B4"]).rename("NDVI")
            return image.addBands(ndvi)

        s2_collection = (
            ee.ImageCollection(SENTINEL2_COLLECTION)
            .filterDate(start_date, end_date)
            .filterBounds(aoi_ee)
            .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 20))
            .map(mask_s2_clouds)
            .map(add_ndvi)
            .select(["B2", "B3", "B4", "B8", "B11", "B12", "NDVI"])
        )

        n_images = s2_collection.size().getInfo()
        self.logger.info("[CCDC] %d imagenes Sentinel-2 encontradas", n_images)

        if n_images < 6:
            raise RuntimeError(
                f"CCDC requiere al menos 6 observaciones, solo se encontraron {n_images} "
                f"para el periodo {start_date} -> {end_date}"
            )

        # --- Run CCDC server-side ---
        self.logger.info("[CCDC] Ejecutando ee.Algorithms.TemporalSegmentation.Ccdc ...")
        ccdc = ee.Algorithms.TemporalSegmentation.Ccdc(
            collection=s2_collection,
            breakpointBands=["NDVI"],
            minObservations=6,
            chiSquareProbability=0.99,
            numTimePeriods=1,
        )

        # Extract only change magnitude and break time bands
        ccdc_result = ccdc.select([".*_magnitude", ".*_tBreak"])

        # --- Download as GeoTIFF via computePixels ---
        lons = [c[0] for c in coords]
        lats = [c[1] for c in coords]
        min_lon, max_lon = min(lons), max(lons)
        min_lat, max_lat = min(lats), max(lats)

        scale_deg = SCALE / 111_320.0
        total_w = max(1, int(round((max_lon - min_lon) / scale_deg)))
        total_h = max(1, int(round((max_lat - min_lat) / scale_deg)))

        # Clamp to tile limit
        total_w = min(total_w, MAX_TILE_PX)
        total_h = min(total_h, MAX_TILE_PX)

        self.logger.info(
            "[CCDC] Descargando %dx%dpx, lon=[%.4f,%.4f], lat=[%.4f,%.4f]",
            total_w, total_h, min_lon, max_lon, min_lat, max_lat,
        )

        request = {
            "expression": ccdc_result.clip(aoi_ee),
            "fileFormat": "GEO_TIFF",
            "grid": {
                "dimensions": {"width": total_w, "height": total_h},
                "affineTransform": {
                    "scaleX": scale_deg,
                    "shearX": 0,
                    "translateX": min_lon,
                    "shearY": 0,
                    "scaleY": -scale_deg,
                    "translateY": max_lat,
                },
                "crsCode": "EPSG:4326",
            },
        }

        try:
            tif_bytes = ee.data.computePixels(request)
        except Exception as e:
            self.logger.error("[CCDC] computePixels fallo: %s", e)
            raise RuntimeError(f"Error descargando resultado CCDC: {e}") from e

        output_path.write_bytes(tif_bytes)
        self.logger.info("[CCDC] GeoTIFF descargado -> %s", output_path)

        # Save cache metadata
        meta_path.write_text(json.dumps({
            "cache_key": cache_key,
            "start_date": start_date,
            "end_date": end_date,
            "n_images": n_images,
            "job_id": job_id,
        }))

        # --- Vectorize breakpoints and return ---
        return self._vectorize_breakpoints(str(output_path))

    # ------------------------------------------------------------------
    # Change type classification
    # ------------------------------------------------------------------
    @staticmethod
    def classify_change_type(magnitude: float) -> str:
        """
        Classify NDVI change magnitude into human-readable change type.

        Parameters
        ----------
        magnitude : float   NDVI change magnitude (negative = vegetation loss)

        Returns
        -------
        str  one of: desmonte_subito, degradacion_gradual, sin_cambio, recuperacion
        """
        if magnitude < -0.3:
            return "desmonte_subito"
        elif magnitude < -0.1:
            return "degradacion_gradual"
        elif magnitude < 0.1:
            return "sin_cambio"
        else:
            return "recuperacion"

    # ------------------------------------------------------------------
    # Vectorization of breakpoints from downloaded raster
    # ------------------------------------------------------------------
    def _vectorize_breakpoints(self, raster_path: str) -> tuple[dict, dict]:
        """
        Read CCDC magnitude and tBreak bands, threshold significant changes,
        apply morphological cleanup, and vectorize to GeoJSON features.

        Parameters
        ----------
        raster_path : str  Path to CCDC GeoTIFF with magnitude and tBreak bands.

        Returns
        -------
        (geojson_featurecollection, stats_dict)
        """
        self.logger.info("[CCDC] Vectorizando breakpoints desde %s", raster_path)

        with rasterio.open(raster_path) as src:
            n_bands = src.count
            transform = src.transform

            # CCDC output bands follow pattern: NDVI_magnitude, NDVI_tBreak
            # With numTimePeriods=1, we expect 2 bands: magnitude (band 1), tBreak (band 2)
            if n_bands < 2:
                raise ValueError(
                    f"CCDC raster esperaba al menos 2 bandas (magnitude + tBreak), "
                    f"encontro {n_bands}"
                )

            magnitude = src.read(1).astype(np.float64)
            t_break = src.read(2).astype(np.float64)

        # Replace nodata / inf with NaN for clean processing
        magnitude = np.where(np.isfinite(magnitude), magnitude, np.nan)
        t_break = np.where(np.isfinite(t_break), t_break, np.nan)

        # --- Threshold: only significant changes (|magnitude| > 0.1) ---
        significant = np.abs(magnitude) > 0.1
        significant = np.where(np.isnan(magnitude), False, significant)

        n_significant = int(np.sum(significant))
        total_valid = int(np.sum(np.isfinite(magnitude)))
        self.logger.info(
            "[CCDC] Pixeles significativos: %d / %d (%.1f%%)",
            n_significant,
            total_valid,
            100 * n_significant / max(total_valid, 1),
        )

        if n_significant == 0:
            self.logger.info("[CCDC] Sin cambios significativos detectados")
            geojson = {"type": "FeatureCollection", "features": []}
            stats = {
                "total_breakpoints": 0,
                "area_ha": 0.0,
                "avg_magnitude": 0.0,
                "by_type": {},
                "source": "CCDC (Sentinel-2 NDVI)",
            }
            return geojson, stats

        # --- Morphological cleanup ---
        struct = np.ones((3, 3), dtype=bool)
        clean = binary_opening(significant, structure=struct)
        clean = binary_closing(clean, structure=struct).astype(np.uint8)

        # --- Vectorize ---
        features = []
        for geom, val in shapes(clean, transform=transform):
            if val != 1:
                continue

            poly = shape(geom)
            area_ha = _area_deg2_to_ha(poly.area)
            if area_ha < self.MIN_AREA_HA:
                continue

            # Rasterize polygon to extract per-polygon statistics
            poly_rast = rasterize(
                [(geom, 1)],
                out_shape=magnitude.shape,
                transform=transform,
                dtype=np.uint8,
            )
            pmask = (poly_rast == 1) & np.isfinite(magnitude)

            if not np.any(pmask):
                continue

            mean_magnitude = float(np.nanmean(magnitude[pmask]))

            tbreak_mask = pmask & np.isfinite(t_break)
            mean_tbreak = (
                float(np.nanmean(t_break[tbreak_mask]))
                if np.any(tbreak_mask) else np.nan
            )

            # Classify change type
            change_type = self.classify_change_type(mean_magnitude)

            # Skip "sin_cambio" polygons — they passed morphological filter
            # but their mean magnitude is below significance
            if change_type == "sin_cambio":
                continue

            # Convert tBreak (fractional year / epoch millis) to date string
            break_date = self._tbreak_to_date(mean_tbreak)

            features.append({
                "type": "Feature",
                "geometry": mapping(poly),
                "properties": {
                    "change_type": change_type,
                    "magnitude": round(mean_magnitude, 4),
                    "break_date": break_date,
                    "area_ha": round(area_ha, 2),
                },
            })

        # Sort by absolute magnitude descending (most dramatic changes first)
        features.sort(key=lambda f: abs(f["properties"]["magnitude"]), reverse=True)
        features = features[: self.MAX_FEATURES]

        # --- Aggregate statistics ---
        type_counts: dict[str, int] = {}
        type_areas: dict[str, float] = {}
        for f in features:
            ct = f["properties"]["change_type"]
            type_counts[ct] = type_counts.get(ct, 0) + 1
            type_areas[ct] = type_areas.get(ct, 0.0) + f["properties"]["area_ha"]

        total_area = sum(f["properties"]["area_ha"] for f in features)
        avg_magnitude = (
            round(float(np.mean([f["properties"]["magnitude"] for f in features])), 4)
            if features else 0.0
        )

        by_type: dict[str, dict] = {}
        for ct in type_counts:
            by_type[ct] = {
                "count": type_counts[ct],
                "area_ha": round(type_areas[ct], 1),
            }

        geojson = {"type": "FeatureCollection", "features": features}
        stats = {
            "total_breakpoints": len(features),
            "area_ha": round(total_area, 1),
            "avg_magnitude": avg_magnitude,
            "by_type": by_type,
            "source": "CCDC (Sentinel-2 NDVI)",
        }

        self.logger.info(
            "[CCDC] %d breakpoints vectorizados, area total=%.1fha, tipos=%s",
            len(features), total_area, type_counts,
        )
        return geojson, stats

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _tbreak_to_date(tbreak_value: float) -> str:
        """
        Convert CCDC tBreak value to ISO date string.

        GEE CCDC tBreak is expressed as fractional years (e.g. 2021.456)
        or as milliseconds since epoch depending on the GEE version.
        We handle both cases.
        """
        if np.isnan(tbreak_value):
            return "unknown"

        # If the value looks like a fractional year (e.g., 2019.5)
        if 1970 < tbreak_value < 2100:
            year = int(tbreak_value)
            frac = tbreak_value - year
            try:
                start_of_year = datetime(year, 1, 1, tzinfo=timezone.utc)
                days_in_year = (
                    datetime(year + 1, 1, 1, tzinfo=timezone.utc) - start_of_year
                ).days
                target = start_of_year + timedelta(days=frac * days_in_year)
                return target.strftime("%Y-%m-%d")
            except (ValueError, OverflowError):
                return f"{year}-01-01"

        # If the value looks like epoch milliseconds (large number)
        if tbreak_value > 1e9:
            try:
                dt = datetime.fromtimestamp(tbreak_value / 1000.0, tz=timezone.utc)
                return dt.strftime("%Y-%m-%d")
            except (ValueError, OverflowError, OSError):
                return "unknown"

        return "unknown"

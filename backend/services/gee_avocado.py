"""
GEE AVOCADO Service — Anomalous Vegetation Change Detection.

Computes per-pixel NDVI percentile rank against a multi-year historical
baseline using GEE server-side computation, then downloads the anomaly
raster and vectorizes locally (same pattern as all other APEX engines).
"""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from io import BytesIO
from typing import Callable, Optional

import ee
import numpy as np
import rasterio
from rasterio.features import shapes
from shapely.geometry import shape

logger = logging.getLogger("apex.avocado")

SENTINEL2 = "COPERNICUS/S2_SR_HARMONIZED"
BASELINE_YEARS = 5          # annual medians — 5 is enough for p5
ANOMALY_PERCENTILE = 5      # flag pixels below this
DOWNLOAD_SCALE = 30         # metres — coarser for speed
_GEE_TIMEOUT = 180          # per computePixels call
_GEE_PROJ_TIMEOUT = 30
_MIN_AREA_HA = 0.05         # filter tiny polygons


def _gee_call_with_timeout(fn, timeout: int, label: str = "AVOCADO"):
    """Run a blocking GEE call in a thread with a timeout."""
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(fn)
        try:
            return future.result(timeout=timeout)
        except FuturesTimeout:
            raise TimeoutError(f"[{label}] GEE call timed out after {timeout}s")


class GEEAvocadoService:
    """Server-side NDVI anomaly detection via GEE, local vectorization."""

    def __init__(self):
        self._initialised = False

    def _init_gee(self):
        if self._initialised:
            return
        try:
            ee.Initialize(opt_url="https://earthengine-highvolume.googleapis.com")
        except Exception:
            ee.Initialize()
        self._initialised = True

    @staticmethod
    def _cloud_mask(img):
        scl = img.select("SCL")
        clear = scl.neq(3).And(scl.neq(9)).And(scl.neq(10))
        return img.updateMask(clear)

    # ── raster download via computePixels ──────────────────────────
    def _download_raster(self, image: ee.Image, aoi_bbox: dict,
                         band_count: int, jid: str) -> bytes:
        """Download an ee.Image as GeoTIFF bytes via computePixels."""
        proj_info = _gee_call_with_timeout(
            lambda: ee.Projection("EPSG:4326").atScale(DOWNLOAD_SCALE).getInfo(),
            _GEE_PROJ_TIMEOUT, f"AVOCADO-proj-{jid}",
        )
        scale_x = proj_info["transform"][0]

        min_lon, min_lat = aoi_bbox["min_lon"], aoi_bbox["min_lat"]
        max_lon, max_lat = aoi_bbox["max_lon"], aoi_bbox["max_lat"]

        width = max(1, int(round(abs(max_lon - min_lon) / abs(scale_x))))
        height = max(1, int(round(abs(max_lat - min_lat) / abs(scale_x))))
        width = min(width, 2048)
        height = min(height, 2048)

        region = ee.Geometry.Rectangle([min_lon, min_lat, max_lon, max_lat])
        request = {
            "expression": image.clip(region),
            "fileFormat": "GEO_TIFF",
            "grid": {
                "dimensions": {"width": width, "height": height},
                "affineTransform": {
                    "scaleX": abs(scale_x),
                    "shearX": 0,
                    "translateX": min_lon,
                    "shearY": 0,
                    "scaleY": -abs(scale_x),
                    "translateY": max_lat,
                },
                "crsCode": "EPSG:4326",
            },
        }
        tif_bytes = _gee_call_with_timeout(
            lambda: ee.data.computePixels(request),
            _GEE_TIMEOUT, f"AVOCADO-{jid}",
        )
        return tif_bytes

    # ── main entry point ───────────────────────────────────────────
    def detect_anomalies(
        self,
        aoi_geojson: dict,
        analysis_date: str,
        job_id: str = "test",
        on_progress: Optional[Callable[[str], None]] = None,
    ) -> tuple[dict, dict]:
        """
        Detect NDVI anomalies relative to a multi-year baseline.

        Strategy (optimised):
        1. Build *annual median NDVI* images for BASELINE_YEARS (5 images, not hundreds)
        2. Server-side: compute p5 threshold + current vs baseline diff
        3. Download anomaly mask + delta as raster via computePixels
        4. Vectorize locally with rasterio.features.shapes()
        """
        self._init_gee()
        jid = job_id[:8]
        _t0 = time.time()

        def _prog(msg: str):
            elapsed = time.time() - _t0
            full = f"[AVOCADO {elapsed:.0f}s] {msg}"
            logger.info("[%s] %s", jid, full)
            if on_progress:
                on_progress(full)

        # ── Parse AOI ──
        if aoi_geojson.get("type") == "FeatureCollection":
            coords = aoi_geojson["features"][0]["geometry"]["coordinates"]
            geom_type = aoi_geojson["features"][0]["geometry"]["type"]
        elif aoi_geojson.get("type") == "Feature":
            coords = aoi_geojson["geometry"]["coordinates"]
            geom_type = aoi_geojson["geometry"]["type"]
        else:
            coords = aoi_geojson["coordinates"]
            geom_type = aoi_geojson["type"]

        aoi = ee.Geometry({"type": geom_type, "coordinates": coords})

        # Compute bounding box for raster download
        from shapely.geometry import shape as shapely_shape
        aoi_shape = shapely_shape({"type": geom_type, "coordinates": coords})
        b = aoi_shape.bounds  # (minx, miny, maxx, maxy)
        aoi_bbox = {
            "min_lon": b[0], "min_lat": b[1],
            "max_lon": b[2], "max_lat": b[3],
        }

        # ── Date ranges ──
        from datetime import datetime, timedelta
        end_dt = datetime.strptime(analysis_date, "%Y-%m-%d")
        # Current season: ±45 days around analysis_date
        current_start = (end_dt - timedelta(days=45)).strftime("%Y-%m-%d")
        current_end = analysis_date

        # DOY window for baseline (same season)
        doy_start = (end_dt - timedelta(days=45)).timetuple().tm_yday
        doy_end = end_dt.timetuple().tm_yday
        if doy_start > doy_end:
            doy_start, doy_end = 1, 366  # wrap-around → use whole year

        _prog(f"Baseline: {BASELINE_YEARS} años, DOY {doy_start}-{doy_end}")

        # ── Build annual median NDVI composites (server-side, lazy) ──
        annual_medians = []
        for y_offset in range(1, BASELINE_YEARS + 1):
            yr = end_dt.year - y_offset
            col = (
                ee.ImageCollection(SENTINEL2)
                .filterBounds(aoi)
                .filterDate(f"{yr}-01-01", f"{yr}-12-31")
                .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 30))
                .filter(ee.Filter.calendarRange(doy_start, doy_end, "day_of_year"))
                .map(self._cloud_mask)
                .map(lambda img: img.normalizedDifference(["B8", "B4"]).rename("NDVI"))
            )
            annual_medians.append(col.median())

        _prog(f"{len(annual_medians)} composites anuales construidos (lazy)")

        # Stack annual medians → compute p5 from just 5 images
        baseline_stack = ee.ImageCollection(annual_medians)
        p5 = baseline_stack.reduce(ee.Reducer.percentile([ANOMALY_PERCENTILE])).rename("p5")
        baseline_median = baseline_stack.median().rename("baseline")

        # ── Current NDVI ──
        _prog(f"NDVI actual: {current_start} → {current_end}")
        current_col = (
            ee.ImageCollection(SENTINEL2)
            .filterBounds(aoi)
            .filterDate(current_start, current_end)
            .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 30))
            .map(self._cloud_mask)
            .map(lambda img: img.normalizedDifference(["B8", "B4"]).rename("NDVI"))
        )

        # Guard: if no images found, widen the window progressively
        n_current = _gee_call_with_timeout(
            lambda: current_col.size().getInfo(),
            _GEE_PROJ_TIMEOUT, f"AVOCADO-count-{jid}",
        )
        if n_current == 0:
            for extra_days in [90, 180, 365]:
                wider_start = (end_dt - timedelta(days=extra_days)).strftime("%Y-%m-%d")
                _prog(f"Sin imágenes en ventana original, ampliando a {extra_days} días ({wider_start} → {current_end})")
                current_col = (
                    ee.ImageCollection(SENTINEL2)
                    .filterBounds(aoi)
                    .filterDate(wider_start, current_end)
                    .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 50))
                    .map(self._cloud_mask)
                    .map(lambda img: img.normalizedDifference(["B8", "B4"]).rename("NDVI"))
                )
                n_current = _gee_call_with_timeout(
                    lambda: current_col.size().getInfo(),
                    _GEE_PROJ_TIMEOUT, f"AVOCADO-count2-{jid}",
                )
                if n_current > 0:
                    _prog(f"Encontradas {n_current} imágenes con ventana de {extra_days} días")
                    break

        if n_current == 0:
            _prog("⚠ Sin imágenes S2 disponibles para NDVI actual — sin anomalías")
            return {"type": "FeatureCollection", "features": []}, {
                "n_anomalies": 0, "total_area_ha": 0, "by_severity": {},
                "baseline_years": BASELINE_YEARS,
                "percentile_threshold": ANOMALY_PERCENTILE,
                "source": "AVOCADO (S2 NDVI percentile)",
                "error": "No S2 images available for current period",
            }

        current_ndvi = current_col.median().rename("current")

        # ── Anomaly mask + delta (2-band image) ──
        anomaly_mask = current_ndvi.lt(p5).selfMask().toInt8().rename("anomaly")
        ndvi_delta = current_ndvi.subtract(baseline_median).toFloat().rename("delta")
        result_image = anomaly_mask.addBands(ndvi_delta)

        # ── Download via computePixels ──
        _prog("Descargando raster anomalías (computePixels)...")
        try:
            tif_bytes = self._download_raster(result_image, aoi_bbox, 2, jid)
            _prog(f"Raster descargado: {len(tif_bytes)/1024:.0f} KB")
        except (TimeoutError, Exception) as exc:
            _prog(f"⚠ Descarga falló: {str(exc)[:120]}")
            logger.warning("[%s] AVOCADO raster download failed: %s", jid, exc)
            return {"type": "FeatureCollection", "features": []}, {
                "n_anomalies": 0, "total_area_ha": 0, "by_severity": {},
                "baseline_years": BASELINE_YEARS,
                "percentile_threshold": ANOMALY_PERCENTILE,
                "source": "AVOCADO (S2 NDVI percentile)",
                "error": str(exc)[:200],
            }

        # ── Local vectorization ──
        _prog("Vectorizando localmente (rasterio.features)...")
        features = []
        try:
            with rasterio.open(BytesIO(tif_bytes)) as src:
                anomaly_band = src.read(1)  # anomaly mask
                delta_band = src.read(2)    # NDVI delta
                transform = src.transform
                nodata = src.nodata

                # Create valid mask
                valid = anomaly_band == 1
                if not np.any(valid):
                    _prog("Sin píxeles anómalos — resultado limpio")
                else:
                    mask_u8 = valid.astype(np.uint8)
                    lat_center = (aoi_bbox["min_lat"] + aoi_bbox["max_lat"]) / 2

                    for geom, val in shapes(mask_u8, transform=transform):
                        if val != 1:
                            continue
                        poly = shape(geom)
                        if not poly.is_valid:
                            poly = poly.buffer(0)

                        # Area in hectares (approximate)
                        area_ha = (
                            poly.area
                            * (111_320 ** 2)
                            * np.cos(np.radians(lat_center))
                            / 10_000
                        )
                        if area_ha < _MIN_AREA_HA:
                            continue

                        # Sample mean delta in this polygon
                        from rasterio.features import rasterize
                        poly_mask = rasterize(
                            [(geom, 1)],
                            out_shape=delta_band.shape,
                            transform=transform,
                            fill=0,
                            dtype="uint8",
                        )
                        delta_vals = delta_band[poly_mask == 1]
                        mean_delta = float(np.nanmean(delta_vals)) if len(delta_vals) > 0 else 0.0

                        # Severity classification
                        if mean_delta < -0.3:
                            severity = "critica"
                        elif mean_delta < -0.15:
                            severity = "alta"
                        elif mean_delta < -0.05:
                            severity = "media"
                        else:
                            severity = "baja"

                        features.append({
                            "type": "Feature",
                            "geometry": geom,
                            "properties": {
                                "area_ha": round(area_ha, 3),
                                "mean_delta": round(mean_delta, 4),
                                "severity": severity,
                            },
                        })
        except Exception as exc:
            _prog(f"⚠ Error vectorizando: {str(exc)[:120]}")
            logger.exception("[%s] AVOCADO vectorization error", jid)

        # ── Stats ──
        total_area = sum(f["properties"]["area_ha"] for f in features)
        by_severity = {}
        for f in features:
            sev = f["properties"]["severity"]
            by_severity[sev] = by_severity.get(sev, 0) + 1

        stats = {
            "n_anomalies": len(features),
            "total_area_ha": round(total_area, 2),
            "by_severity": by_severity,
            "baseline_years": BASELINE_YEARS,
            "percentile_threshold": ANOMALY_PERCENTILE,
            "source": "AVOCADO (S2 NDVI percentile)",
        }

        _prog(f"Listo: {len(features)} anomalías, {total_area:.1f} ha")
        return {"type": "FeatureCollection", "features": features}, stats

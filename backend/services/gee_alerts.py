"""
GEE Deforestation Alerts — Download Service
Datasets:
  - GLAD-S2:  projects/glad/alert/UpdResult    (Sentinel-2, 10m)
  - GLAD-S2b: projects/glad/S2alert/alert       (Sentinel-2 alt)
  - Fallback: UMD/GLAD/PRIMARY_HUMID_2001
  - RADD:     projects/radar-wur/raddalert/v1  (Sentinel-1 SAR, 10m)
  - MODIS BA: MODIS/061/MCD64A1               (500m monthly burned area)
"""
from __future__ import annotations

import ee
import hashlib
import json
import os
from datetime import datetime, date
from pathlib import Path

import numpy as np
import rasterio
from rasterio.merge import merge as rio_merge
from rasterio.transform import from_bounds

from ..config import settings

# ---------------------------------------------------------------------------
# Asset constants
# ---------------------------------------------------------------------------
GLAD_ASSET_PRIMARY = "projects/glad/alert/UpdResult"
GLAD_ASSET_FALLBACK_1 = "projects/glad/S2alert/alert"
GLAD_ASSET_FALLBACK_2 = "UMD/GLAD/PRIMARY_HUMID_2001"

RADD_ASSET = "projects/radar-wur/raddalert/v1"
MODIS_BA_ASSET = "MODIS/061/MCD64A1"

SCALE_S2 = 10       # 10m for Sentinel-2 / GLAD-S2 alerts
SCALE_RADD = 10     # 10m for RADD (Sentinel-1)
SCALE_MODIS = 500   # 500m for MODIS burned area
MAX_TILE_PX = 3000  # max pixels per tile side


class GEEAlertsService:
    """Download deforestation alert rasters from GEE."""

    _HV_URL = "https://earthengine-highvolume.googleapis.com"

    # ------------------------------------------------------------------
    # Initialisation (same pattern as GEEHansenService)
    # ------------------------------------------------------------------
    def initialize(self):
        project = os.getenv("GEE_PROJECT", "profepa-deforestation")
        try:
            ee.Initialize(project=project, opt_url=self._HV_URL)
            print(f"[Alerts-GEE] Inicializado con high-volume endpoint (proyecto={project})")
        except Exception:
            try:
                ee.Initialize(project=project)
                print(f"[Alerts-GEE] Inicializado sin high-volume (proyecto={project})")
            except Exception:
                ee.Authenticate()
                ee.Initialize(project=project)
                print(f"[Alerts-GEE] Inicializado post-auth (proyecto={project})")

    # ------------------------------------------------------------------
    # Helpers (identical to GEEHansenService)
    # ------------------------------------------------------------------
    @staticmethod
    def _aoi_hash(coords: list) -> str:
        sorted_coords = sorted(coords, key=lambda c: (c[0], c[1]))
        return hashlib.md5(json.dumps(sorted_coords).encode()).hexdigest()[:12]

    @staticmethod
    def _extract_coords(aoi_geojson: dict) -> list:
        if aoi_geojson.get("type") == "Polygon":
            return aoi_geojson["coordinates"][0]
        if aoi_geojson.get("type") == "Feature":
            return aoi_geojson["geometry"]["coordinates"][0]
        return aoi_geojson.get("coordinates", [[]])[0]

    # ------------------------------------------------------------------
    # Internal: tiled computePixels download + merge
    # ------------------------------------------------------------------
    def _download_tiled(
        self,
        image: ee.Image,
        coords: list,
        scale: float,
        output_path: Path,
        job_id: str,
        tag: str,
        n_bands: int = 1,
    ) -> Path:
        """Download *image* over the bbox of *coords* using tiled computePixels.

        If no tiles can be downloaded (e.g. dataset has no coverage for
        this region), a zero-filled GeoTIFF is written so downstream
        processing sees 0 alerts instead of crashing.
        """

        output_dir = output_path.parent
        lons = [c[0] for c in coords]
        lats = [c[1] for c in coords]
        min_lon, max_lon = min(lons), max(lons)
        min_lat, max_lat = min(lats), max(lats)

        scale_deg = scale / 111_320.0
        total_w = max(1, int((max_lon - min_lon) / scale_deg))
        total_h = max(1, int((max_lat - min_lat) / scale_deg))

        n_cols = max(1, (total_w + MAX_TILE_PX - 1) // MAX_TILE_PX)
        n_rows = max(1, (total_h + MAX_TILE_PX - 1) // MAX_TILE_PX)
        tile_w = (total_w + n_cols - 1) // n_cols
        tile_h = (total_h + n_rows - 1) // n_rows

        print(f"[{tag}] Total {total_w}x{total_h}px -> {n_cols}x{n_rows} tiles ({tile_w}x{tile_h}px cada uno)")

        tile_paths: list[Path] = []
        for row in range(n_rows):
            for col in range(n_cols):
                t_min_lon = min_lon + col * tile_w * scale_deg
                t_max_lat = max_lat - row * tile_h * scale_deg
                cur_w = min(tile_w, total_w - col * tile_w)
                cur_h = min(tile_h, total_h - row * tile_h)
                if cur_w <= 0 or cur_h <= 0:
                    continue

                request = {
                    "expression": image,
                    "fileFormat": "GEO_TIFF",
                    "grid": {
                        "dimensions": {"width": cur_w, "height": cur_h},
                        "affineTransform": {
                            "scaleX": scale_deg,
                            "shearX": 0,
                            "translateX": t_min_lon,
                            "shearY": 0,
                            "scaleY": -scale_deg,
                            "translateY": t_max_lat,
                        },
                        "crsCode": "EPSG:4326",
                    },
                }

                try:
                    pixels = ee.data.computePixels(request)
                    tile_path = output_dir / f"{job_id}_{tag}_tile_{row}_{col}.tif"
                    tile_path.write_bytes(pixels)
                    tile_paths.append(tile_path)
                    print(f"[{tag}] Tile {row},{col} descargado ({cur_w}x{cur_h}px)")
                except Exception as e:
                    print(f"[{tag}] Error tile {row},{col}: {e}")

        if not tile_paths:
            # No data for this region — write a zero-filled raster
            # so downstream processing sees 0 alerts (not an error).
            print(f"[{tag}] Sin cobertura de datos para esta región — generando raster vacío (0 alertas)")
            w = min(total_w, 64)
            h = min(total_h, 64)
            transform = from_bounds(min_lon, min_lat, max_lon, max_lat, w, h)
            profile = {
                "driver": "GTiff",
                "dtype": "uint16",
                "width": w,
                "height": h,
                "count": n_bands,
                "crs": "EPSG:4326",
                "transform": transform,
            }
            with rasterio.open(str(output_path), "w", **profile) as dst:
                for b in range(1, n_bands + 1):
                    dst.write(np.zeros((h, w), dtype=np.uint16), b)
            return output_path

        # Merge tiles
        if len(tile_paths) == 1:
            tile_paths[0].rename(output_path)
        else:
            datasets = [rasterio.open(str(p)) for p in tile_paths]
            mosaic, mosaic_transform = rio_merge(datasets)
            for ds in datasets:
                ds.close()

            profile = {
                "driver": "GTiff",
                "dtype": mosaic.dtype.name,
                "width": mosaic.shape[2],
                "height": mosaic.shape[1],
                "count": mosaic.shape[0],
                "crs": "EPSG:4326",
                "transform": mosaic_transform,
            }
            with rasterio.open(str(output_path), "w", **profile) as dst:
                dst.write(mosaic)

            for p in tile_paths:
                p.unlink(missing_ok=True)

        return output_path

    # ------------------------------------------------------------------
    # Cache helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _check_cache(output_path: Path, meta_path: Path, cache_key: str, tag: str) -> bool:
        """Return True if a valid cached file exists."""
        if output_path.exists() and meta_path.exists():
            try:
                cached = json.loads(meta_path.read_text())
                if cached.get("cache_key") == cache_key:
                    print(f"[{tag}] Cache valido ({cache_key[:8]}) -> {output_path}")
                    return True
            except Exception:
                pass
            output_path.unlink(missing_ok=True)
            meta_path.unlink(missing_ok=True)
        return False

    @staticmethod
    def _write_meta(meta_path: Path, cache_key: str, aoi_hash: str, extra: dict | None = None):
        payload = {"cache_key": cache_key, "aoi_hash": aoi_hash}
        if extra:
            payload.update(extra)
        meta_path.write_text(json.dumps(payload))

    # ------------------------------------------------------------------
    # Date conversion helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _date_to_glad_s2_offset(date_str: str) -> int:
        """GLAD-S2 encodes alert dates as days since 2018-01-01."""
        ref = date(2018, 1, 1)
        d = datetime.strptime(date_str, "%Y-%m-%d").date()
        return (d - ref).days

    @staticmethod
    def _date_to_radd_offset(date_str: str) -> int:
        """RADD encodes alert dates as days since 2018-12-31."""
        ref = date(2018, 12, 31)
        d = datetime.strptime(date_str, "%Y-%m-%d").date()
        return (d - ref).days

    # ==================================================================
    # 1. GLAD Alerts  (Sentinel-2 / Landsat)
    # ==================================================================
    def get_glad_alerts(
        self,
        aoi_geojson: dict,
        start_date: str,
        end_date: str,
        job_id: str = "test",
    ) -> Path:
        """
        Download GLAD deforestation alerts as a 2-band GeoTIFF:
          Band 1: alertBinary  (uint16, 0/1/2 — 2=confirmed, 1=probable)
          Band 2: alertDate    (uint16, days since 2018-01-01 for GLAD-S2)

        Parameters
        ----------
        aoi_geojson : dict   GeoJSON Polygon / Feature
        start_date  : str    "YYYY-MM-DD"
        end_date    : str    "YYYY-MM-DD"
        job_id      : str    unique job identifier

        Returns
        -------
        Path to the downloaded GeoTIFF.
        """
        coords = self._extract_coords(aoi_geojson)

        # Cache -------------------------------------------------------
        output_dir = Path(settings.DATA_DIR)
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{job_id}_glad_alerts.tif"
        meta_path = output_dir / f"{job_id}_glad_alerts.json"
        aoi_hash = self._aoi_hash(coords)
        cache_key = f"{aoi_hash}_{start_date}_{end_date}_glad"

        if self._check_cache(output_path, meta_path, cache_key, "GLAD-GEE"):
            return output_path

        print(f"[GLAD-GEE] Descargando alertas GLAD {start_date} a {end_date}...")

        aoi_ee = ee.Geometry.Polygon(coords)

        # Try primary asset, then fallbacks
        glad_image = None
        used_asset = None
        asset_bands = None
        for asset in [GLAD_ASSET_PRIMARY, GLAD_ASSET_FALLBACK_1, GLAD_ASSET_FALLBACK_2]:
            try:
                candidate = ee.Image(asset)
                # Force a server-side check by requesting band names
                bands = candidate.bandNames().getInfo()
                glad_image = candidate
                used_asset = asset
                asset_bands = bands
                print(f"[GLAD-GEE] Usando asset: {asset}  (bandas: {bands[:6]})")
                break
            except Exception as e:
                print(f"[GLAD-GEE] Asset {asset} no disponible: {e}")

        if glad_image is None:
            raise RuntimeError("No se pudo acceder a ningun asset GLAD")

        # Select bands — names vary per GLAD product
        if "alertBinary" in asset_bands:
            alert_binary = glad_image.select("alertBinary")
        elif "alert" in asset_bands:
            alert_binary = glad_image.select("alert")
        else:
            # Pick first band as alert indicator
            alert_binary = glad_image.select(0)
            print(f"[GLAD-GEE] Usando primera banda como alerta: {asset_bands[0]}")

        if "alertDate" in asset_bands:
            alert_date = glad_image.select("alertDate")
        else:
            # Some GLAD datasets use year-specific alertDateYYYY bands
            date_bands = [b for b in asset_bands if b.startswith("alertDate")]
            if date_bands:
                alert_date = glad_image.select(date_bands[-1])
                print(f"[GLAD-GEE] Usando banda de fecha: {date_bands[-1]}")
            else:
                # No date band — use alert band as placeholder
                alert_date = alert_binary.multiply(0)
                print("[GLAD-GEE] Sin banda de fecha, usando placeholder")

        # Date mask
        start_offset = self._date_to_glad_s2_offset(start_date)
        end_offset = self._date_to_glad_s2_offset(end_date)
        # Only apply date mask if we have a real date band
        if any(b.startswith("alertDate") for b in asset_bands):
            date_mask = alert_date.gte(start_offset).And(alert_date.lte(end_offset))
        else:
            # No date filtering possible — keep all alerts
            date_mask = alert_binary.gte(0)

        # Build 2-band image: alertBinary (masked), alertDate (masked)
        image = (
            alert_binary.updateMask(date_mask)
            .addBands(alert_date.updateMask(date_mask))
            .clip(aoi_ee)
            .unmask(0)
            .toUint16()
        )

        # Download tiled (2-band: alert + date)
        self._download_tiled(image, coords, SCALE_S2, output_path, job_id, "GLAD-GEE", n_bands=2)

        # Save cache metadata
        self._write_meta(meta_path, cache_key, aoi_hash, {
            "start_date": start_date,
            "end_date": end_date,
            "asset": used_asset,
            "source": "GLAD deforestation alerts",
        })

        print(f"[GLAD-GEE] Alertas GLAD guardadas: {output_path}")
        return output_path

    # ==================================================================
    # 2. RADD Alerts  (Sentinel-1 SAR — penetrates clouds)
    # ==================================================================
    def get_radd_alerts(
        self,
        aoi_geojson: dict,
        start_date: str,
        end_date: str,
        job_id: str = "test",
    ) -> Path:
        """
        Download RADD deforestation alerts as a 3-band GeoTIFF:
          Band 1: Alert      (uint16, 0/1 — binary alert flag)
          Band 2: Date       (uint16, days since 2018-12-31)
          Band 3: Confidence (uint16, 1=nominal, 2=high)

        RADD uses Sentinel-1 SAR — penetrates clouds, ideal for
        tropical monitoring in persistently cloudy regions.

        Parameters
        ----------
        aoi_geojson : dict   GeoJSON Polygon / Feature
        start_date  : str    "YYYY-MM-DD"
        end_date    : str    "YYYY-MM-DD"
        job_id      : str    unique job identifier

        Returns
        -------
        Path to the downloaded GeoTIFF.
        """
        coords = self._extract_coords(aoi_geojson)

        # Cache -------------------------------------------------------
        output_dir = Path(settings.DATA_DIR)
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{job_id}_radd_alerts.tif"
        meta_path = output_dir / f"{job_id}_radd_alerts.json"
        aoi_hash = self._aoi_hash(coords)
        cache_key = f"{aoi_hash}_{start_date}_{end_date}_radd"

        if self._check_cache(output_path, meta_path, cache_key, "RADD-GEE"):
            return output_path

        print(f"[RADD-GEE] Descargando alertas RADD {start_date} a {end_date}...")

        aoi_ee = ee.Geometry.Polygon(coords)

        try:
            radd = ee.Image(RADD_ASSET)
            radd_bands = radd.bandNames().getInfo()
            print(f"[RADD-GEE] Asset: {RADD_ASSET}  (bandas: {radd_bands[:6]})")
        except Exception as e:
            raise RuntimeError(f"Asset RADD no disponible: {e}")

        # Select bands (with flexible name matching)
        alert = radd.select("Alert") if "Alert" in radd_bands else radd.select(0)
        alert_date = radd.select("Date") if "Date" in radd_bands else radd.select(1)
        confidence = radd.select("Confidence") if "Confidence" in radd_bands else radd.select(2)

        # Date mask (RADD: days since 2018-12-31)
        start_offset = self._date_to_radd_offset(start_date)
        end_offset = self._date_to_radd_offset(end_date)
        date_mask = alert_date.gte(start_offset).And(alert_date.lte(end_offset))

        # Combine: only keep pixels where alert==1 AND within date range
        valid_mask = alert.eq(1).And(date_mask)

        # Build 3-band image: Alert, Date, Confidence
        image = (
            alert.updateMask(valid_mask)
            .addBands(alert_date.updateMask(valid_mask))
            .addBands(confidence.updateMask(valid_mask))
            .clip(aoi_ee)
            .unmask(0)
            .toUint16()
        )

        # Download tiled (3-band: Alert, Date, Confidence)
        self._download_tiled(image, coords, SCALE_RADD, output_path, job_id, "RADD-GEE", n_bands=3)

        # Save cache metadata
        self._write_meta(meta_path, cache_key, aoi_hash, {
            "start_date": start_date,
            "end_date": end_date,
            "asset": RADD_ASSET,
            "source": "RADD deforestation alerts (Sentinel-1 SAR)",
        })

        print(f"[RADD-GEE] Alertas RADD guardadas: {output_path}")
        return output_path

    # ==================================================================
    # 3. MODIS Burned Area  (MCD64A1, 500m monthly)
    # ==================================================================
    def get_modis_burned_area(
        self,
        aoi_geojson: dict,
        start_date: str,
        end_date: str,
        job_id: str = "test",
    ) -> Path:
        """
        Download MODIS MCD64A1 burned-area composite as a 1-band GeoTIFF:
          Band 1: BurnDate  (uint16, day of burn within year; 0=no burn)

        Uses max-value composite across the requested date range so that
        the most recent burn date per pixel is retained.

        Parameters
        ----------
        aoi_geojson : dict   GeoJSON Polygon / Feature
        start_date  : str    "YYYY-MM-DD"
        end_date    : str    "YYYY-MM-DD"
        job_id      : str    unique job identifier

        Returns
        -------
        Path to the downloaded GeoTIFF.
        """
        coords = self._extract_coords(aoi_geojson)

        # Cache -------------------------------------------------------
        output_dir = Path(settings.DATA_DIR)
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{job_id}_modis_burned.tif"
        meta_path = output_dir / f"{job_id}_modis_burned.json"
        aoi_hash = self._aoi_hash(coords)
        cache_key = f"{aoi_hash}_{start_date}_{end_date}_modis_ba"

        if self._check_cache(output_path, meta_path, cache_key, "MODIS-BA"):
            return output_path

        print(f"[MODIS-BA] Descargando area quemada MODIS {start_date} a {end_date}...")

        aoi_ee = ee.Geometry.Polygon(coords)

        # Filter ImageCollection by date, mosaic via max composite
        collection = (
            ee.ImageCollection(MODIS_BA_ASSET)
            .filterDate(start_date, end_date)
            .filterBounds(aoi_ee)
            .select("BurnDate")
        )

        # Max composite: keeps the latest BurnDate across all months
        image = collection.max().clip(aoi_ee).unmask(0).toUint16()

        # Download tiled (MODIS at 500m needs fewer tiles)
        self._download_tiled(image, coords, SCALE_MODIS, output_path, job_id, "MODIS-BA")

        # Save cache metadata
        self._write_meta(meta_path, cache_key, aoi_hash, {
            "start_date": start_date,
            "end_date": end_date,
            "asset": MODIS_BA_ASSET,
            "source": "MODIS MCD64A1 Burned Area (500m monthly)",
        })

        print(f"[MODIS-BA] Area quemada MODIS guardada: {output_path}")
        return output_path

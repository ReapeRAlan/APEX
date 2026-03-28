"""
GEE Sentinel-1 SAR Composite — Download Service
Dataset: COPERNICUS/S1_GRD (Ground Range Detected, 10m)
Bands: VV_median, VH_median, VV_VH_ratio
100% cloud penetration — key advantage over optical sensors.
"""

import ee
import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from pathlib import Path

import rasterio
from rasterio.merge import merge as rio_merge

from ..config import settings

S1_ASSET = "COPERNICUS/S1_GRD"
SCALE = 10  # 10m Sentinel-1 resolution
MAX_TILE_PX = 3000  # max pixels per tile side

_GEE_GETINFO_TIMEOUT = 60
_GEE_COMPUTE_TIMEOUT = 180


def _gee_call_with_timeout(fn, timeout: int, label: str = "GEE"):
    """Run a blocking GEE call in a thread with a timeout."""
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(fn)
        try:
            return future.result(timeout=timeout)
        except FuturesTimeout:
            raise TimeoutError(f"[{label}] GEE call timed out after {timeout}s")


class GEESARService:
    """Download Sentinel-1 SAR composites from GEE."""

    _HV_URL = "https://earthengine-highvolume.googleapis.com"

    def initialize(self):
        project = os.getenv("GEE_PROJECT", "profepa-deforestation")
        try:
            ee.Initialize(project=project, opt_url=self._HV_URL)
            print(f"[SAR-GEE] Inicializado con high-volume endpoint (proyecto={project})")
        except Exception:
            try:
                ee.Initialize(project=project)
                print(f"[SAR-GEE] Inicializado sin high-volume (proyecto={project})")
            except Exception:
                ee.Authenticate()
                ee.Initialize(project=project)
                print(f"[SAR-GEE] Inicializado post-auth (proyecto={project})")

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

    def get_sentinel1_composite(
        self,
        aoi_geojson: dict,
        start_date: str,
        end_date: str,
        job_id: str = "test",
    ) -> Path:
        """
        Download a Sentinel-1 SAR median composite for the AOI and date range.

        Sentinel-1 C-band SAR provides 100% cloud penetration, making it ideal
        for tropical deforestation monitoring where optical sensors are frequently
        obscured by persistent cloud cover.

        Returns path to 3-band GeoTIFF:
          Band 1: VV_median   (float32, dB backscatter)
          Band 2: VH_median   (float32, dB backscatter)
          Band 3: VV_VH_ratio (float32, VH - VV in dB = 10*log10(VH/VV) linear)
        """
        coords = self._extract_coords(aoi_geojson)

        # Cache
        output_dir = Path(settings.DATA_DIR)
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{job_id}_sar.tif"
        meta_path = output_dir / f"{job_id}_sar.json"
        aoi_hash = self._aoi_hash(coords)
        cache_key = f"{aoi_hash}_{start_date}_{end_date}"

        if output_path.exists() and meta_path.exists():
            try:
                cached = json.loads(meta_path.read_text())
                if cached.get("cache_key") == cache_key:
                    print(f"[SAR-GEE] Cache valido ({aoi_hash[:8]}) -> {output_path}")
                    return output_path
            except Exception:
                pass
            output_path.unlink(missing_ok=True)
            meta_path.unlink(missing_ok=True)

        print(f"[SAR-GEE] Descargando Sentinel-1 SAR composite {start_date} -> {end_date}...")

        aoi_ee = ee.Geometry.Polygon(coords)

        # Build Sentinel-1 collection: IW mode, dual-pol VV+VH
        s1_collection = (
            ee.ImageCollection(S1_ASSET)
            .filter(ee.Filter.eq("instrumentMode", "IW"))
            .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VV"))
            .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VH"))
            .filterDate(start_date, end_date)
            .filterBounds(aoi_ee)
        )

        n_scenes = _gee_call_with_timeout(
            lambda: s1_collection.size().getInfo(),
            _GEE_GETINFO_TIMEOUT, "SAR-GEE",
        )
        print(f"[SAR-GEE] {n_scenes} escenas Sentinel-1 encontradas")

        if n_scenes == 0:
            raise RuntimeError(
                f"No se encontraron escenas Sentinel-1 para el periodo {start_date} -> {end_date}"
            )

        # Median composite of VV and VH bands
        vv_median = s1_collection.select("VV").median().rename("VV_median")
        vh_median = s1_collection.select("VH").median().rename("VH_median")

        # Cross-ratio band: VH - VV in dB domain (equivalent to 10*log10(VH/VV) in linear)
        vv_vh_ratio = vh_median.subtract(vv_median).rename("VV_VH_ratio")

        # Build 3-band image
        image = (
            vv_median
            .addBands(vh_median)
            .addBands(vv_vh_ratio)
            .clip(aoi_ee)
            .toFloat()
        )

        # Compute bbox and tiles
        lons = [c[0] for c in coords]
        lats = [c[1] for c in coords]
        min_lon, max_lon = min(lons), max(lons)
        min_lat, max_lat = min(lats), max(lats)

        scale_deg = SCALE / 111320.0
        total_w = int((max_lon - min_lon) / scale_deg)
        total_h = int((max_lat - min_lat) / scale_deg)
        total_w = max(total_w, 1)
        total_h = max(total_h, 1)

        n_cols = max(1, (total_w + MAX_TILE_PX - 1) // MAX_TILE_PX)
        n_rows = max(1, (total_h + MAX_TILE_PX - 1) // MAX_TILE_PX)
        tile_w = (total_w + n_cols - 1) // n_cols
        tile_h = (total_h + n_rows - 1) // n_rows

        print(f"[SAR-GEE] Total {total_w}x{total_h}px -> {n_cols}x{n_rows} tiles ({tile_w}x{tile_h}px cada uno)")

        tile_paths = []
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
                    pixels = _gee_call_with_timeout(
                        lambda req=request: ee.data.computePixels(req),
                        _GEE_COMPUTE_TIMEOUT, "SAR-GEE",
                    )
                    tile_path = output_dir / f"{job_id}_sar_tile_{row}_{col}.tif"
                    tile_path.write_bytes(pixels)
                    tile_paths.append(tile_path)
                    print(f"[SAR-GEE] Tile {row},{col} descargado ({cur_w}x{cur_h}px)")
                except Exception as e:
                    print(f"[SAR-GEE] Error tile {row},{col}: {e}")

        if not tile_paths:
            raise RuntimeError("No se descargaron tiles de Sentinel-1 SAR")

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
                "dtype": "float32",
                "width": mosaic.shape[2],
                "height": mosaic.shape[1],
                "count": mosaic.shape[0],
                "crs": "EPSG:4326",
                "transform": mosaic_transform,
            }
            with rasterio.open(str(output_path), "w", **profile) as dst:
                dst.write(mosaic)

            # Cleanup tiles
            for p in tile_paths:
                p.unlink(missing_ok=True)

        # Save cache metadata
        meta_path.write_text(json.dumps({
            "cache_key": cache_key,
            "aoi_hash": aoi_hash,
            "start_date": start_date,
            "end_date": end_date,
            "n_scenes": n_scenes,
        }))

        print(f"[SAR-GEE] Sentinel-1 SAR composite guardado: {output_path}")
        return output_path

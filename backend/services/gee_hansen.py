"""
GEE Hansen Global Forest Change v1.12 — Download Service
Dataset: UMD/hansen/global_forest_change_2024_v1_12
Resolución: 30m
Bandas: treecover2000, loss, lossyear
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

_GEE_COMPUTE_TIMEOUT = 180


def _gee_call_with_timeout(fn, timeout: int, label: str = "GEE"):
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(fn)
        try:
            return future.result(timeout=timeout)
        except FuturesTimeout:
            raise TimeoutError(f"[{label}] GEE call timed out after {timeout}s")

HANSEN_ASSET = "UMD/hansen/global_forest_change_2024_v1_12"
SCALE = 30  # 30m resolution
MAX_TILE_PX = 3000  # max pixels per tile side


class GEEHansenService:
    """Download Hansen GFC loss data from GEE."""

    _HV_URL = "https://earthengine-highvolume.googleapis.com"

    def initialize(self):
        project = os.getenv("GEE_PROJECT", "profepa-deforestation")
        try:
            ee.Initialize(project=project, opt_url=self._HV_URL)
            print(f"[Hansen-GEE] Inicializado con high-volume endpoint (proyecto={project})")
        except Exception:
            try:
                ee.Initialize(project=project)
                print(f"[Hansen-GEE] Inicializado sin high-volume (proyecto={project})")
            except Exception:
                ee.Authenticate()
                ee.Initialize(project=project)
                print(f"[Hansen-GEE] Inicializado post-auth (proyecto={project})")

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

    def get_hansen_forest_loss(
        self,
        aoi_geojson: dict,
        job_id: str = "test",
        start_year: int = 2018,
        end_year: int = 2024,
    ) -> Path:
        """
        Download Hansen GFC bands for the AOI.
        Returns path to multi-band GeoTIFF:
          Band 0: treecover2000 (uint8, 0-100)
          Band 1: loss (uint8, 0/1)
          Band 2: lossyear (uint8, 0-24 → year 2001-2024)
        """
        coords = self._extract_coords(aoi_geojson)

        # Cache
        output_dir = Path(settings.DATA_DIR)
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{job_id}_hansen.tif"
        meta_path = output_dir / f"{job_id}_hansen.json"
        aoi_hash = self._aoi_hash(coords)
        cache_key = f"{aoi_hash}_{start_year}_{end_year}"

        if output_path.exists() and meta_path.exists():
            try:
                cached = json.loads(meta_path.read_text())
                if cached.get("cache_key") == cache_key:
                    print(f"[Hansen-GEE] Cache válido ({aoi_hash[:8]}) → {output_path}")
                    return output_path
            except Exception:
                pass
            output_path.unlink(missing_ok=True)
            meta_path.unlink(missing_ok=True)

        print(f"[Hansen-GEE] Descargando Hansen GFC para años {start_year}-{end_year}...")

        aoi_ee = ee.Geometry.Polygon(coords)
        hansen = ee.Image(HANSEN_ASSET)

        # Select and filter
        treecover = hansen.select("treecover2000")
        loss = hansen.select("loss")
        lossyear = hansen.select("lossyear")

        # Mask: only where loss occurred in year range AND original forest >= 30%
        year_mask = (lossyear.gte(start_year - 2000)
                     .And(lossyear.lte(end_year - 2000)))
        forest_mask = treecover.gte(30)
        valid_loss = loss.And(year_mask).And(forest_mask)

        # Build 3-band image: treecover, filtered_loss, lossyear
        image = (treecover
                 .addBands(valid_loss.rename("loss_filtered"))
                 .addBands(lossyear.multiply(valid_loss).rename("lossyear_filtered"))
                 .clip(aoi_ee)
                 .toUint8())

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

        print(f"[Hansen-GEE] Total {total_w}x{total_h}px → {n_cols}x{n_rows} tiles ({tile_w}x{tile_h}px cada uno)")

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
                        _GEE_COMPUTE_TIMEOUT, "Hansen-GEE",
                    )
                    tile_path = output_dir / f"{job_id}_hansen_tile_{row}_{col}.tif"
                    tile_path.write_bytes(pixels)
                    tile_paths.append(tile_path)
                    print(f"[Hansen-GEE] Tile {row},{col} descargado ({cur_w}x{cur_h}px)")
                except Exception as e:
                    print(f"[Hansen-GEE] Error tile {row},{col}: {e}")

        if not tile_paths:
            raise RuntimeError("No se descargaron tiles de Hansen")

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
                "dtype": "uint8",
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
            "start_year": start_year,
            "end_year": end_year,
        }))

        print(f"[Hansen-GEE] Hansen GFC guardado: {output_path}")
        return output_path

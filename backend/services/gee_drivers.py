"""
GEE Drivers of Forest Loss — Download Service
Dataset: WRI / Google DeepMind drivers of forest loss classification (1km)
Asset:   projects/landandcarbon/assets/wri_gdm_drivers_forest_loss_1km/v1_2_2001_2024
Resolución: 1km  (categorical, single-band)
"""

import ee
import hashlib
import json
import os
from pathlib import Path

import numpy as np
import rasterio
from rasterio.merge import merge as rio_merge

from ..config import settings

DRIVERS_ASSET = "projects/landandcarbon/assets/wri_gdm_drivers_forest_loss_1km/v1_2_2001_2024"
SCALE = 1000  # 1km resolution
MAX_TILE_PX = 3000  # max pixels per tile side


class GEEDriversService:
    """Download WRI/Google DeepMind drivers of forest loss from GEE."""

    _HV_URL = "https://earthengine-highvolume.googleapis.com"

    def initialize(self):
        project = os.getenv("GEE_PROJECT", "profepa-deforestation")
        try:
            ee.Initialize(project=project, opt_url=self._HV_URL)
            print(f"[Drivers-GEE] Inicializado con high-volume endpoint (proyecto={project})")
        except Exception:
            try:
                ee.Initialize(project=project)
                print(f"[Drivers-GEE] Inicializado sin high-volume (proyecto={project})")
            except Exception:
                ee.Authenticate()
                ee.Initialize(project=project)
                print(f"[Drivers-GEE] Inicializado post-auth (proyecto={project})")

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

    def get_conversion_drivers(
        self,
        aoi_geojson: dict,
        job_id: str = "test",
    ) -> Path:
        """
        Download WRI/Google DeepMind drivers of forest loss for the AOI.
        Returns path to single-band categorical GeoTIFF.
        """
        coords = self._extract_coords(aoi_geojson)

        # Cache
        output_dir = Path(settings.DATA_DIR)
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{job_id}_drivers.tif"
        meta_path = output_dir / f"{job_id}_drivers.json"
        aoi_hash = self._aoi_hash(coords)
        cache_key = f"{aoi_hash}_drivers"

        if output_path.exists() and meta_path.exists():
            try:
                cached = json.loads(meta_path.read_text())
                if cached.get("cache_key") == cache_key:
                    print(f"[Drivers-GEE] Cache válido ({aoi_hash[:8]}) → {output_path}")
                    return output_path
            except Exception:
                pass
            output_path.unlink(missing_ok=True)
            meta_path.unlink(missing_ok=True)

        print("[Drivers-GEE] Descargando WRI/Google DeepMind drivers of forest loss...")

        aoi_ee = ee.Geometry.Polygon(coords)
        drivers_img = ee.Image(DRIVERS_ASSET)

        # Select the primary classification band (first band)
        image = drivers_img.select(0).clip(aoi_ee).toUint8()

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

        print(f"[Drivers-GEE] Total {total_w}x{total_h}px → {n_cols}x{n_rows} tiles ({tile_w}x{tile_h}px cada uno)")

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
                    pixels = ee.data.computePixels(request)
                    tile_path = output_dir / f"{job_id}_drivers_tile_{row}_{col}.tif"
                    tile_path.write_bytes(pixels)
                    tile_paths.append(tile_path)
                    print(f"[Drivers-GEE] Tile {row},{col} descargado ({cur_w}x{cur_h}px)")
                except Exception as e:
                    print(f"[Drivers-GEE] Error tile {row},{col}: {e}")

        if not tile_paths:
            raise RuntimeError("No se descargaron tiles de drivers")

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
        }))

        print(f"[Drivers-GEE] Drivers guardado: {output_path}")
        return output_path

"""
GEE CCDC (Continuous Change Detection and Classification) Service
Uses ee.Algorithms.TemporalSegmentation.Ccdc() server-side.
Downloads change magnitude + date of change bands.
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

SCALE_CCDC = 30       # 30m — Landsat native resolution
MAX_TILE_PX = 3000


class GEECCDCService:
    """Run CCDC on Landsat time-series via GEE and download breakpoint rasters."""

    _HV_URL = "https://earthengine-highvolume.googleapis.com"

    def initialize(self):
        project = os.getenv("GEE_PROJECT", "profepa-deforestation")
        try:
            ee.Initialize(project=project, opt_url=self._HV_URL)
            print(f"[CCDC-GEE] Inicializado con high-volume endpoint (proyecto={project})")
        except Exception:
            try:
                ee.Initialize(project=project)
                print(f"[CCDC-GEE] Inicializado sin high-volume (proyecto={project})")
            except Exception:
                ee.Authenticate()
                ee.Initialize(project=project)
                print(f"[CCDC-GEE] Inicializado post-auth (proyecto={project})")

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
    # Tiled download
    # ------------------------------------------------------------------
    def _download_tiled(
        self, image: ee.Image, coords: list, scale: float,
        output_path: Path, job_id: str, tag: str,
    ) -> Path:
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

        print(f"[{tag}] Total {total_w}x{total_h}px -> {n_cols}x{n_rows} tiles")

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
                            "scaleX": scale_deg, "shearX": 0,
                            "translateX": t_min_lon,
                            "shearY": 0, "scaleY": -scale_deg,
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
                except Exception as e:
                    print(f"[{tag}] Error tile {row},{col}: {e}")

        if not tile_paths:
            raise RuntimeError(f"No se descargaron tiles de {tag}")

        if len(tile_paths) == 1:
            tile_paths[0].rename(output_path)
        else:
            datasets = [rasterio.open(str(p)) for p in tile_paths]
            mosaic, mosaic_transform = rio_merge(datasets)
            for ds in datasets:
                ds.close()
            profile = {
                "driver": "GTiff", "dtype": mosaic.dtype.name,
                "width": mosaic.shape[2], "height": mosaic.shape[1],
                "count": mosaic.shape[0], "crs": "EPSG:4326",
                "transform": mosaic_transform,
            }
            with rasterio.open(str(output_path), "w", **profile) as dst:
                dst.write(mosaic)
            for p in tile_paths:
                p.unlink(missing_ok=True)

        return output_path

    # ==================================================================
    # Public API
    # ==================================================================
    def get_ccdc_breakpoints(
        self,
        aoi_geojson: dict,
        start_date: str,
        end_date: str,
        job_id: str = "test",
    ) -> Path:
        """
        Run CCDC on Landsat time-series and download breakpoint info.

        Returns a 3-band GeoTIFF:
          Band 1: tBreak     (float32, fractional year of most recent break)
          Band 2: changeMag  (float32, SWIR magnitude of change)
          Band 3: numBreaks  (uint8, number of breaks detected)
        """
        coords = self._extract_coords(aoi_geojson)

        output_dir = Path(settings.DATA_DIR)
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{job_id}_ccdc.tif"
        meta_path = output_dir / f"{job_id}_ccdc.json"
        aoi_hash = self._aoi_hash(coords)
        cache_key = f"{aoi_hash}_{start_date}_{end_date}_ccdc"

        if output_path.exists() and meta_path.exists():
            try:
                cached = json.loads(meta_path.read_text())
                if cached.get("cache_key") == cache_key:
                    print(f"[CCDC-GEE] Cache valido -> {output_path}")
                    return output_path
            except Exception:
                pass
            output_path.unlink(missing_ok=True)
            meta_path.unlink(missing_ok=True)

        print(f"[CCDC-GEE] Ejecutando CCDC {start_date} -> {end_date}...")
        aoi_ee = ee.Geometry.Polygon(coords)

        # Build Landsat composite collection (L8 + L9 harmonized SR)
        l8 = (
            ee.ImageCollection("LANDSAT/LC08/C02/T1_L2")
            .filterDate(start_date, end_date)
            .filterBounds(aoi_ee)
            .select(
                ["SR_B2", "SR_B3", "SR_B4", "SR_B5", "SR_B6", "SR_B7"],
                ["BLUE", "GREEN", "RED", "NIR", "SWIR1", "SWIR2"],
            )
        )
        l9 = (
            ee.ImageCollection("LANDSAT/LC09/C02/T1_L2")
            .filterDate(start_date, end_date)
            .filterBounds(aoi_ee)
            .select(
                ["SR_B2", "SR_B3", "SR_B4", "SR_B5", "SR_B6", "SR_B7"],
                ["BLUE", "GREEN", "RED", "NIR", "SWIR1", "SWIR2"],
            )
        )
        collection = l8.merge(l9)

        n_images = collection.size().getInfo()
        print(f"[CCDC-GEE] {n_images} imagenes Landsat disponibles")

        if n_images < 12:
            raise RuntimeError(
                f"Insuficientes imagenes Landsat ({n_images}) para CCDC (minimo 12)"
            )

        # Run CCDC server-side
        ccdc = ee.Algorithms.TemporalSegmentation.Ccdc(
            collection=collection,
            breakpointBands=["GREEN", "RED", "NIR", "SWIR1", "SWIR2"],
            minObservations=6,
            chiSquareProbability=0.99,
            minNumOfYearsScaler=1.33,
            lambda_=20,
            maxIterations=25000,
        )

        # Extract most recent breakpoint info
        # tBreak: time of last break (fractional year)
        # changeMag: magnitude of SWIR1 change at last break
        t_break = ccdc.select("tBreak").arrayGet(-1).rename("tBreak")
        change_mag = (
            ccdc.select("SWIR1_magnitude").arrayGet(-1).rename("changeMag")
        )
        num_breaks = ccdc.select("tBreak").arrayLength(0).rename("numBreaks")

        image = (
            t_break.addBands(change_mag)
            .addBands(num_breaks)
            .clip(aoi_ee)
            .unmask(0)
            .toFloat()
        )

        self._download_tiled(image, coords, SCALE_CCDC, output_path, job_id, "CCDC-GEE")

        meta_path.write_text(json.dumps({
            "cache_key": cache_key, "aoi_hash": aoi_hash,
            "start_date": start_date, "end_date": end_date,
            "n_images": n_images,
            "source": "CCDC (Landsat 8+9, 30m)",
        }))

        print(f"[CCDC-GEE] Breakpoints guardados: {output_path}")
        return output_path

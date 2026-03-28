"""
GEE Legal / Protected Areas Service — WDPA (World Database on Protected Areas)
Dataset: WCMC/WDPA/current/polygons
Consulta vectorial de Áreas Naturales Protegidas (ANPs) que intersectan el AOI.
"""

import ee
import hashlib
import json
import os
from pathlib import Path

import rasterio
from rasterio.merge import merge as rio_merge

from ..config import settings

WDPA_ASSET = "WCMC/WDPA/current/polygons"
MAX_FEATURES = 50  # limit to avoid GEE timeout on .getInfo()

# Properties to keep from WDPA dataset
KEEP_PROPS = ["NAME", "DESIG", "DESIG_TYPE", "STATUS", "IUCN_CAT", "REP_AREA"]

# MapBiomas Mexico LULC
MAPBIOMAS_ASSET_MX = "projects/mapbiomas-public/assets/mexico/lulc/mexico_coverage_v1-0"
# Fallbacks — try multiple collections in case one is deprecated
MAPBIOMAS_FALLBACKS = [
    "projects/mapbiomas-public/assets/brazil/lulc/collection9/mapbiomas_collection90_integration_v1",
    "projects/mapbiomas-workspace/public/collection9/mapbiomas_collection90_integration_v1",
    "projects/mapbiomas-workspace/public/collection8/mapbiomas_collection80_integration_v1",
]
MAPBIOMAS_SCALE = 30       # 30m resolution
MAPBIOMAS_MAX_TILE_PX = 3000


class GEELegalService:
    """Query WDPA protected-area polygons that intersect a given AOI via GEE."""

    _HV_URL = "https://earthengine-highvolume.googleapis.com"

    # ------------------------------------------------------------------ init
    def initialize(self):
        """Initialize Earth Engine, same pattern as GEEHansenService."""
        project = os.getenv("GEE_PROJECT", "profepa-deforestation")
        try:
            ee.Initialize(project=project, opt_url=self._HV_URL)
            print(f"[Legal-GEE] Inicializado con high-volume endpoint (proyecto={project})")
        except Exception:
            try:
                ee.Initialize(project=project)
                print(f"[Legal-GEE] Inicializado sin high-volume (proyecto={project})")
            except Exception:
                ee.Authenticate()
                ee.Initialize(project=project)
                print(f"[Legal-GEE] Inicializado post-auth (proyecto={project})")

    # --------------------------------------------------------- helpers
    @staticmethod
    def _extract_coords(aoi_geojson: dict) -> list:
        """Extract outer-ring coordinate list from various GeoJSON shapes."""
        if aoi_geojson.get("type") == "Polygon":
            return aoi_geojson["coordinates"][0]
        if aoi_geojson.get("type") == "Feature":
            return aoi_geojson["geometry"]["coordinates"][0]
        return aoi_geojson.get("coordinates", [[]])[0]

    @staticmethod
    def _aoi_hash(coords: list) -> str:
        """Deterministic hash of AOI coordinates for caching."""
        sorted_coords = sorted(coords, key=lambda c: (c[0], c[1]))
        return hashlib.md5(json.dumps(sorted_coords).encode()).hexdigest()[:12]

    # ------------------------------------------------- public API
    def get_protected_areas(self, aoi_geojson: dict) -> dict:
        """
        Query WDPA polygons that intersect the AOI within Mexico.

        Parameters
        ----------
        aoi_geojson : dict
            GeoJSON Polygon, Feature, or geometry dict representing the area
            of interest.

        Returns
        -------
        dict
            GeoJSON FeatureCollection with properties:
            name, desig, desig_type, status, iucn_cat, rep_area.
            Empty FeatureCollection if no protected areas intersect.
        """
        coords = self._extract_coords(aoi_geojson)
        aoi_ee = ee.Geometry.Polygon(coords)

        print("[Legal-GEE] Consultando WDPA para ANPs en el AOI...")

        wdpa = (
            ee.FeatureCollection(WDPA_ASSET)
            .filter(ee.Filter.eq("ISO3", "MEX"))
            .filterBounds(aoi_ee)
        )

        # Limit number of features to avoid timeout on .getInfo()
        wdpa = wdpa.limit(MAX_FEATURES)

        # Select only the properties we need
        def _pick_props(feat):
            """Server-side map function: keep only relevant properties."""
            return ee.Feature(
                feat.geometry(),
                {
                    "name": feat.get("NAME"),
                    "desig": feat.get("DESIG"),
                    "desig_type": feat.get("DESIG_TYPE"),
                    "status": feat.get("STATUS"),
                    "iucn_cat": feat.get("IUCN_CAT"),
                    "rep_area": feat.get("REP_AREA"),
                },
            )

        wdpa = wdpa.map(_pick_props)

        # Fetch to client
        try:
            fc_info = wdpa.getInfo()
        except Exception as e:
            print(f"[Legal-GEE] Error al consultar WDPA: {e}")
            return {"type": "FeatureCollection", "features": []}

        features = fc_info.get("features", [])
        n = len(features)

        if n == 0:
            print("[Legal-GEE] No se encontraron ANPs que intersecten el AOI")
        else:
            names = [f["properties"].get("name", "?") for f in features[:5]]
            preview = ", ".join(names)
            suffix = f" ... (+{n - 5} más)" if n > 5 else ""
            print(f"[Legal-GEE] {n} ANP(s) encontradas: {preview}{suffix}")

        return {"type": "FeatureCollection", "features": features}

    # ------------------------------------------------- MapBiomas LULC download
    def get_mapbiomas(self, aoi_geojson: dict, year: int, job_id: str = "test") -> Path:
        """
        Download MapBiomas Mexico LULC raster for a given year clipped to AOI.

        Parameters
        ----------
        aoi_geojson : dict
            GeoJSON Polygon, Feature, or geometry dict.
        year : int
            Target year for LULC classification.
        job_id : str
            Job identifier used for file naming and caching.

        Returns
        -------
        Path
            Path to single-band categorical GeoTIFF (LULC class codes).
        """
        coords = self._extract_coords(aoi_geojson)

        # ----- cache check -----
        output_dir = Path(settings.DATA_DIR)
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{job_id}_mapbiomas.tif"
        meta_path = output_dir / f"{job_id}_mapbiomas.json"
        aoi_hash = self._aoi_hash(coords)
        cache_key = f"{aoi_hash}_{year}"

        if output_path.exists() and meta_path.exists():
            try:
                cached = json.loads(meta_path.read_text())
                if cached.get("cache_key") == cache_key:
                    print(f"[Legal-GEE] MapBiomas cache válido ({aoi_hash[:8]}) → {output_path}")
                    return output_path
            except Exception:
                pass
            output_path.unlink(missing_ok=True)
            meta_path.unlink(missing_ok=True)

        print(f"[Legal-GEE] Descargando MapBiomas LULC para año {year}...")

        aoi_ee = ee.Geometry.Polygon(coords)

        # Try Mexico asset first, then fallback collections
        collection = None
        asset_used = None

        # 1. Try primary Mexico asset
        try:
            cand = (
                ee.ImageCollection(MAPBIOMAS_ASSET_MX)
                .filter(ee.Filter.calendarRange(year, year, "year"))
            )
            count = cand.size().getInfo()
            if count > 0:
                collection = cand
                asset_used = MAPBIOMAS_ASSET_MX
                print(f"[Legal-GEE] Usando asset México ({count} imagen(es) para {year})")
        except Exception as e:
            print(f"[Legal-GEE] Asset México no disponible: {e}")

        # 2. Try fallback collections
        if collection is None:
            for fb_asset in MAPBIOMAS_FALLBACKS:
                try:
                    cand = (
                        ee.ImageCollection(fb_asset)
                        .filter(ee.Filter.calendarRange(year, year, "year"))
                    )
                    count = cand.size().getInfo()
                    if count > 0:
                        collection = cand
                        asset_used = fb_asset
                        print(f"[Legal-GEE] Usando fallback {fb_asset} ({count} imagen(es))")
                        break
                except Exception as e:
                    print(f"[Legal-GEE] Fallback {fb_asset} no disponible: {e}")

        # 3. No data available — create zero-filled raster for graceful degradation
        if collection is None:
            print(f"[Legal-GEE] Sin datos MapBiomas para año {year} — generando raster vacío")
            import numpy as _np
            lons = [c[0] for c in coords]
            lats = [c[1] for c in coords]
            min_lon, max_lon = min(lons), max(lons)
            min_lat, max_lat = min(lats), max(lats)
            w, h = 64, 64
            transform = rasterio.transform.from_bounds(min_lon, min_lat, max_lon, max_lat, w, h)
            profile = {
                "driver": "GTiff", "dtype": "uint8",
                "width": w, "height": h, "count": 1,
                "crs": "EPSG:4326", "transform": transform,
            }
            with rasterio.open(str(output_path), "w", **profile) as dst:
                dst.write(_np.zeros((h, w), dtype=_np.uint8), 1)
            meta_path.write_text(json.dumps({"cache_key": cache_key, "aoi_hash": aoi_hash, "empty": True}))
            return output_path

        # Mosaic, take first band (categorical LULC), clip to AOI
        image = collection.mosaic().clip(aoi_ee).toUint8()

        # ----- compute bbox and tiling -----
        lons = [c[0] for c in coords]
        lats = [c[1] for c in coords]
        min_lon, max_lon = min(lons), max(lons)
        min_lat, max_lat = min(lats), max(lats)

        scale_deg = MAPBIOMAS_SCALE / 111_320.0
        total_w = max(1, int((max_lon - min_lon) / scale_deg))
        total_h = max(1, int((max_lat - min_lat) / scale_deg))

        n_cols = max(1, (total_w + MAPBIOMAS_MAX_TILE_PX - 1) // MAPBIOMAS_MAX_TILE_PX)
        n_rows = max(1, (total_h + MAPBIOMAS_MAX_TILE_PX - 1) // MAPBIOMAS_MAX_TILE_PX)
        tile_w = (total_w + n_cols - 1) // n_cols
        tile_h = (total_h + n_rows - 1) // n_rows

        print(
            f"[Legal-GEE] MapBiomas {total_w}x{total_h}px → "
            f"{n_cols}x{n_rows} tiles ({tile_w}x{tile_h}px cada uno)"
        )

        # ----- download tiles -----
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
                    tile_path = output_dir / f"{job_id}_mapbiomas_tile_{row}_{col}.tif"
                    tile_path.write_bytes(pixels)
                    tile_paths.append(tile_path)
                    print(f"[Legal-GEE] MapBiomas tile {row},{col} descargado ({cur_w}x{cur_h}px)")
                except Exception as e:
                    print(f"[Legal-GEE] Error tile MapBiomas {row},{col}: {e}")

        if not tile_paths:
            # No tiles downloaded — create zero-filled raster for graceful degradation
            print("[Legal-GEE] Sin tiles MapBiomas descargados — generando raster vacío")
            import numpy as _np2
            w, h = min(total_w, 64), min(total_h, 64)
            transform = rasterio.transform.from_bounds(min_lon, min_lat, max_lon, max_lat, w, h)
            profile = {
                "driver": "GTiff", "dtype": "uint8",
                "width": w, "height": h, "count": 1,
                "crs": "EPSG:4326", "transform": transform,
            }
            with rasterio.open(str(output_path), "w", **profile) as dst:
                dst.write(_np2.zeros((h, w), dtype=_np2.uint8), 1)
            meta_path.write_text(json.dumps({"cache_key": cache_key, "aoi_hash": aoi_hash, "asset": asset_used, "empty": True}))
            return output_path

        # ----- merge tiles -----
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

        # ----- save cache metadata -----
        meta_path.write_text(json.dumps({
            "cache_key": cache_key,
            "aoi_hash": aoi_hash,
            "year": year,
            "asset": asset_used,
        }))

        print(f"[Legal-GEE] MapBiomas LULC guardado: {output_path}")
        return output_path

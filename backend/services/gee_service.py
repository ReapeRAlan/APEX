import ee
import hashlib
import json
import math
import os
import requests
import shutil
import tempfile
import zipfile
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from pathlib import Path

import numpy as np
import rasterio
from rasterio.mask import mask as rio_mask
from rasterio.merge import merge as rio_merge

from ..config import settings

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------
SENTINEL2_COLLECTION = "COPERNICUS/S2_SR_HARMONIZED"
BANDS = ["B2", "B3", "B4", "B5", "B6", "B7", "B8", "B8A", "B11", "B12"]
SCALE = 10  # 10m — resolución nativa de Sentinel-2 bandas B2/B3/B4/B8
MAX_TILE_BYTES = 12_000_000  # 12 MB estimado — margen amplio bajo el límite real de 48 MiB
BYTES_PER_PIXEL_BAND = 8  # GEE usa float64 internamente para el cálculo de tamaño

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


class GEEService:
    """Servicio de descarga de imágenes Sentinel-2 con estrategia de tiles."""

    # ------------------------------------------------------------------
    # AOI splitting for large polygons
    # ------------------------------------------------------------------
    @staticmethod
    def split_aoi_into_groups(aoi_geojson: dict, max_area_km2: float = 150) -> list[dict]:
        """
        Divide un AOI grande en sub-polígonos usando una grilla regular.
        Retorna lista de GeoJSON geometries (siempre Polygon, nunca MultiPolygon).
        Si es pequeño, retorna [aoi_geojson].
        """
        from shapely.geometry import shape, box, mapping

        aoi_shape = shape(aoi_geojson)
        bounds = aoi_shape.bounds  # (min_lon, min_lat, max_lon, max_lat)

        lon_span = bounds[2] - bounds[0]
        lat_span = bounds[3] - bounds[1]
        cos_lat = abs(math.cos(math.radians((bounds[1] + bounds[3]) / 2)))
        area_km2 = (lon_span * 111.32 * cos_lat) * (lat_span * 111.32)

        if area_km2 <= max_area_km2:
            return [aoi_geojson]

        n = math.ceil(math.sqrt(area_km2 / max_area_km2))
        n = min(n, 5)  # max 5x5 = 25 groups

        step_lon = lon_span / n
        step_lat = lat_span / n

        groups = []
        for i in range(n):
            for j in range(n):
                cell = box(
                    bounds[0] + i * step_lon,
                    bounds[1] + j * step_lat,
                    bounds[0] + (i + 1) * step_lon,
                    bounds[1] + (j + 1) * step_lat,
                )
                intersection = aoi_shape.intersection(cell)
                if intersection.is_empty or intersection.area <= 0:
                    continue
                # Ensure we always return Polygon, not MultiPolygon/GeometryCollection
                if intersection.geom_type == "MultiPolygon":
                    # Take the largest polygon component
                    intersection = max(intersection.geoms, key=lambda g: g.area)
                elif intersection.geom_type == "GeometryCollection":
                    polys = [g for g in intersection.geoms if g.geom_type == "Polygon" and g.area > 0]
                    if not polys:
                        continue
                    intersection = max(polys, key=lambda g: g.area)
                elif intersection.geom_type != "Polygon":
                    continue
                groups.append(mapping(intersection))

        print(f"[GEE] AOI dividido en {len(groups)} grupos ({n}x{n} grilla, {area_km2:.0f}km²)", flush=True)
        return groups

    # ------------------------------------------------------------------
    # Inicialización
    # ------------------------------------------------------------------
    # High-volume endpoint for faster parallel downloads
    _HV_URL = "https://earthengine-highvolume.googleapis.com"

    def initialize(self):
        project = os.getenv("GEE_PROJECT", "profepa-deforestation")
        if settings.GEE_AUTH_MODE == "interactive":
            try:
                ee.Initialize(project=project, opt_url=self._HV_URL)
                print(f"[GEE] Inicializado con high-volume endpoint (proyecto={project})")
            except Exception:
                try:
                    ee.Initialize(project=project)
                    print(f"[GEE] Inicializado sin high-volume (proyecto={project})")
                except Exception:
                    print("[GEE] Token no encontrado. Abriendo autenticación interactiva…")
                    ee.Authenticate()
                    ee.Initialize(project=project)
                    print(f"[GEE] Inicializado post-auth (proyecto={project})")
        elif (
            settings.GEE_SERVICE_ACCOUNT_EMAIL
            and settings.GEE_KEY_FILE
            and os.path.exists(settings.GEE_KEY_FILE)
        ):
            creds = ee.ServiceAccountCredentials(
                email=settings.GEE_SERVICE_ACCOUNT_EMAIL,
                key_file=settings.GEE_KEY_FILE,
            )
            ee.Initialize(creds, project=project, opt_url=self._HV_URL)
            print(f"[GEE] Inicializado con service account + high-volume (proyecto={project})")
        else:
            ee.Initialize(project=project, opt_url=self._HV_URL)
            print(f"[GEE] Inicializado directo + high-volume (proyecto={project})")

    # ------------------------------------------------------------------
    # Composite principal — descarga por tiles
    # ------------------------------------------------------------------
    def get_sentinel2_composite(
        self,
        aoi_geojson: dict,
        start_date: str,
        end_date: str,
        job_id: str = "test",
        cloud_cover_threshold: int = 20,
    ) -> Path:
        import time as _time
        _t0 = _time.monotonic()
        coords = self._extract_coords(aoi_geojson)

        # ── AOI-aware cache ──
        output_dir = Path(settings.DATA_DIR)
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{job_id}_composite.tif"
        meta_path = output_dir / f"{job_id}_composite.json"
        aoi_hash = hashlib.md5(json.dumps(coords, sort_keys=True).encode()).hexdigest()
        print(f"[GEE-Cache] S2 buscando: {output_path}")
        print(f"[GEE-Cache] S2 existe: {output_path.exists()} | meta existe: {meta_path.exists()} | job_id={job_id} | aoi_hash={aoi_hash[:8]}")
        if output_path.exists() and meta_path.exists():
            try:
                cached = json.loads(meta_path.read_text())
                if cached.get("aoi_hash") == aoi_hash and cached.get("start_date") == start_date and cached.get("end_date") == end_date:
                    print(f"[GEE] S2 cache válido (AOI hash {aoi_hash[:8]}…) → {output_path}")
                    return output_path
                print(f"[GEE] S2 cache INVÁLIDO (hash {cached.get('aoi_hash','?')[:8]} ≠ {aoi_hash[:8]}), re-descargando")
            except Exception:
                pass
            output_path.unlink(missing_ok=True)
            meta_path.unlink(missing_ok=True)

        aoi = ee.Geometry.Polygon(coords)

        def mask_s2_clouds(image):
            scl = image.select("SCL")
            return image.updateMask(scl.neq(9).And(scl.neq(10)).And(scl.neq(3)))

        collection = (
            ee.ImageCollection(SENTINEL2_COLLECTION)
            .filterDate(start_date, end_date)
            .filterBounds(aoi)
            .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", cloud_cover_threshold))
            .map(mask_s2_clouds)
            .select(BANDS)
        )

        composite = collection.median().clip(aoi)

        # Índices espectrales
        ndvi = composite.normalizedDifference(["B8", "B4"]).rename("NDVI")
        gndvi = composite.normalizedDifference(["B8", "B3"]).rename("GNDVI")
        ndwi = composite.normalizedDifference(["B3", "B8"]).rename("NDWI")
        nbr = composite.normalizedDifference(["B8", "B12"]).rename("NBR")
        composite = composite.addBands([ndvi, gndvi, ndwi, nbr])

        n_bands = len(BANDS) + 4  # 10 + 4 índices

        # Partir el AOI en tiles manejables — ahora a 10m nativo
        tiles = self._split_bbox(coords, n_bands)
        print(f"[GEE] S2@{SCALE}m — AOI dividida en {len(tiles)} tile(s)")

        if len(tiles) == 1:
            # Un solo tile, descarga directa
            self._download_tile(composite, tiles[0], output_path)
        else:
            # Descargar cada tile y fusionar con rasterio
            tile_paths = []
            tile_dir = output_dir / f"{job_id}_tiles"
            tile_dir.mkdir(parents=True, exist_ok=True)
            for idx, tile_geom in enumerate(tiles):
                tp = tile_dir / f"tile_{idx:03d}.tif"
                print(f"[GEE]   Descargando tile {idx+1}/{len(tiles)} …")
                self._download_tile(composite, tile_geom, tp)
                tile_paths.append(tp)

            # Merge
            print("[GEE] Fusionando tiles …")
            datasets = [rasterio.open(p) for p in tile_paths]
            mosaic, out_transform = rio_merge(datasets)
            out_meta = datasets[0].meta.copy()
            out_meta.update(
                {
                    "height": mosaic.shape[1],
                    "width": mosaic.shape[2],
                    "transform": out_transform,
                }
            )
            for ds in datasets:
                ds.close()

            with rasterio.open(output_path, "w", **out_meta) as dest:
                dest.write(mosaic)

            # Limpiar tiles temporales
            for tp in tile_paths:
                tp.unlink(missing_ok=True)
            shutil.rmtree(tile_dir, ignore_errors=True)

        # ---- Clip to AOI polygon (not just bbox) ----
        clipped_path = output_path.parent / (output_path.stem + "_clipped.tif")
        with rasterio.open(output_path) as src:
            out_image, out_transform = rio_mask(src, [aoi_geojson], crop=True, nodata=0)
            out_meta = src.meta.copy()
            out_meta.update({
                "height": out_image.shape[1],
                "width": out_image.shape[2],
                "transform": out_transform,
                "nodata": 0,
            })
        with rasterio.open(clipped_path, "w", **out_meta) as dest:
            dest.write(out_image)
        output_path.unlink(missing_ok=True)
        clipped_path.rename(output_path)
        print(f"[GEE] Clip al polígono AOI → {out_image.shape[2]}x{out_image.shape[1]}px", flush=True)

        # Write cache metadata
        meta_path.write_text(json.dumps({
            "aoi_hash": aoi_hash, "start_date": start_date,
            "end_date": end_date, "job_id": job_id,
        }))
        print(f"[GEE] Composite guardado → {output_path} (AOI hash {aoi_hash[:8]}…)", flush=True)
        self._verify_raster_bbox(output_path, aoi_geojson, job_id)
        print(f"[GEE-TIMING] S2 composite descargado en {_time.monotonic()-_t0:.1f}s", flush=True)
        return output_path

    # ------------------------------------------------------------------
    # Dynamic World — clasificacion land-cover con deep learning
    # ------------------------------------------------------------------
    DW_SCALE = 10  # 10m — resolución nativa de Dynamic World
    DW_MAX_TILE_PX = 1800  # margen de seguridad bajo el límite de 2048px de computePixels

    def get_dynamic_world_classification(
        self, aoi_geojson: dict, start_date: str, end_date: str, job_id: str = "test"
    ) -> Path:
        """
        Descarga clasificacion Dynamic World de Google para el AOI.
        Usa argmax de probabilidades medias (evita artefacto agua en nodata).
        """
        import time as _time
        _t0 = _time.monotonic()
        self.initialize()

        # Log de diagnóstico — coordenadas solicitadas
        raw_coords = aoi_geojson.get("coordinates", [[]])[0]
        if raw_coords:
            lons = [c[0] for c in raw_coords]
            lats = [c[1] for c in raw_coords]
            print("[DW] get_dynamic_world_classification llamado:")
            print(f"[DW]   job_id={job_id}")
            print(f"[DW]   AOI lon=[{min(lons):.4f},{max(lons):.4f}] lat=[{min(lats):.4f},{max(lats):.4f}]")
            print(f"[DW]   dates={start_date} → {end_date}")

        output_dir = Path(os.getenv("DATA_DIR", "./data/tiles"))
        output_dir.mkdir(parents=True, exist_ok=True)
        final_path = output_dir / f"{job_id}_dw.tif"
        meta_path = output_dir / f"{job_id}_dw.json"

        coords = self._extract_coords(aoi_geojson)
        aoi_hash = hashlib.md5(json.dumps(coords, sort_keys=True).encode()).hexdigest()
        print(f"[GEE-Cache] DW buscando: {final_path}")
        print(f"[GEE-Cache] DW existe: {final_path.exists()} | meta existe: {meta_path.exists()} | job_id={job_id} | aoi_hash={aoi_hash[:8]}")

        # ── AOI-aware cache ──
        if final_path.exists() and meta_path.exists():
            try:
                cached = json.loads(meta_path.read_text())
                if cached.get("aoi_hash") == aoi_hash and cached.get("start_date") == start_date and cached.get("end_date") == end_date:
                    print(f"[DW] Cache válido (AOI hash {aoi_hash[:8]}…) → {final_path}")
                    return final_path
                print(f"[DW] Cache INVÁLIDO (hash {cached.get('aoi_hash','?')[:8]} ≠ {aoi_hash[:8]}), re-descargando")
            except Exception:
                pass
            final_path.unlink(missing_ok=True)
            meta_path.unlink(missing_ok=True)
        aoi_ee = ee.Geometry.Polygon(coords)
        col_filter = ee.Filter.And(
            ee.Filter.bounds(aoi_ee),
            ee.Filter.date(start_date, end_date),
        )
        prob_bands = [
            "water", "trees", "grass", "flooded_vegetation",
            "crops", "shrub_and_scrub", "built", "bare", "snow_and_ice",
        ]
        dw_col = (
            ee.ImageCollection("GOOGLE/DYNAMICWORLD/V1")
            .filter(col_filter)
            .select(prob_bands)
        )
        # Verificar que hay imágenes en el rango solicitado
        count = _gee_call_with_timeout(
            lambda: dw_col.size().getInfo(), _GEE_GETINFO_TIMEOUT, "DW",
        )
        if count == 0:
            print(f"[DW] Sin imágenes en {start_date}–{end_date}, ampliando a ±30 días")
            start_ee = ee.Date(start_date).advance(-30, "day")
            end_ee = ee.Date(end_date).advance(30, "day")
            dw_col = (
                ee.ImageCollection("GOOGLE/DYNAMICWORLD/V1")
                .filter(ee.Filter.And(ee.Filter.bounds(aoi_ee), ee.Filter.date(start_ee, end_ee)))
                .select(prob_bands)
            )
            count = _gee_call_with_timeout(
                lambda: dw_col.size().getInfo(), _GEE_GETINFO_TIMEOUT, "DW",
            )
            if count == 0:
                raise ValueError(f"Sin imágenes DW para {start_date}–{end_date} ni con margen de 30 días")
        print(f"[DW] {count} imagenes disponibles para el periodo")
        # Probabilidad media por clase
        prob_composite = dw_col.reduce(ee.Reducer.mean())
        # Enmascarar pixeles con probabilidad maxima < 0.2 (nubes/nodata)
        max_prob = prob_composite.reduce(ee.Reducer.max())
        composite = prob_composite.clip(aoi_ee)
        composite = composite.updateMask(max_prob.gt(0.2))

        n_bands = len(prob_bands)  # 9 probs (label computed post-download in numpy)
        tiles = self._split_bbox_dw(coords)

        tile_dir = output_dir / f"{job_id}_dw_tiles"
        tile_dir.mkdir(exist_ok=True)

        if len(tiles) == 1:
            try:
                self._download_tile_computepixels(
                    composite, tiles[0], tile_dir, job_id, 0, self.DW_SCALE
                )
                tp = list(tile_dir.glob("*.tif"))[0]
                shutil.move(str(tp), str(final_path))
                shutil.rmtree(tile_dir, ignore_errors=True)
            except Exception as e:
                print(f"[DW] computePixels falló ({e}), fallback a getDownloadURL")
                shutil.rmtree(tile_dir, ignore_errors=True)
                tiles_legacy = self._split_bbox_at_scale(coords, n_bands, self.DW_SCALE)
                self._download_tile_at_scale(composite, tiles_legacy[0], final_path, self.DW_SCALE)
        else:
            tile_paths = []
            for i, tile_dict in enumerate(tiles):
                try:
                    tp = self._download_tile_computepixels(
                        composite, tile_dict, tile_dir, job_id, i, self.DW_SCALE
                    )
                    tile_paths.append(tp)
                except Exception as e:
                    print(f"[DW] computePixels tile {i} falló ({e}), fallback")
                    tp = tile_dir / f"tile_fb_{i}.tif"
                    region = ee.Geometry.Rectangle([
                        tile_dict["min_lon"], tile_dict["min_lat"],
                        tile_dict["max_lon"], tile_dict["max_lat"],
                    ])
                    self._download_tile_at_scale(composite, region, tp, self.DW_SCALE)
                    tile_paths.append(tp)
                print(f"[DW@{self.DW_SCALE}m] Tile {i+1}/{len(tiles)} descargado")

            datasets = [rasterio.open(tp) for tp in tile_paths]
            mosaic, out_transform = rio_merge(datasets)
            out_meta = datasets[0].meta.copy()
            out_meta.update({
                "height": mosaic.shape[1],
                "width": mosaic.shape[2],
                "transform": out_transform,
            })
            for ds in datasets:
                ds.close()
            with rasterio.open(final_path, "w", **out_meta) as dest:
                dest.write(mosaic)
            for tp in tile_paths:
                tp.unlink(missing_ok=True)
            shutil.rmtree(tile_dir, ignore_errors=True)

        # Write cache metadata
        meta_path.write_text(json.dumps({
            "aoi_hash": aoi_hash, "start_date": start_date,
            "end_date": end_date, "job_id": job_id,
        }))
        print(f"[DW] Dynamic World guardado -> {final_path} (AOI hash {aoi_hash[:8]}…)", flush=True)
        # Post-download: compute label (argmax) in numpy and prepend as band 0
        self._inject_argmax_label(final_path)

        # ---- Clip to AOI polygon (not just bbox) ----
        clipped_path = final_path.parent / (final_path.stem + "_clipped.tif")
        with rasterio.open(final_path) as src:
            out_image, out_transform = rio_mask(src, [aoi_geojson], crop=True, nodata=0)
            out_meta = src.meta.copy()
            out_meta.update({
                "height": out_image.shape[1],
                "width": out_image.shape[2],
                "transform": out_transform,
                "nodata": 0,
            })
        with rasterio.open(clipped_path, "w", **out_meta) as dest:
            dest.write(out_image)
        final_path.unlink(missing_ok=True)
        clipped_path.rename(final_path)
        print(f"[DW] Clip al polígono AOI → {out_image.shape[2]}x{out_image.shape[1]}px", flush=True)

        self._verify_raster_bbox(final_path, aoi_geojson, job_id)
        print(f"[GEE-TIMING] DW clasificacion descargada en {_time.monotonic()-_t0:.1f}s (job={job_id})", flush=True)
        return final_path

    def _split_bbox_dw(self, coords: list) -> list:
        """
        Calcula tiles para Dynamic World a escala nativa (10m).
        Usa DW_MAX_TILE_PX para respetar el límite de 2048px de computePixels.
        Retorna dicts con min/max lon/lat (no ee.Geometry).
        """
        ring = coords[0] if isinstance(coords[0][0], (list, tuple)) else coords
        lons = [p[0] for p in ring]
        lats = [p[1] for p in ring]
        min_lon, max_lon = min(lons), max(lons)
        min_lat, max_lat = min(lats), max(lats)

        px_per_deg = 111_320 / self.DW_SCALE
        total_lon_px = (max_lon - min_lon) * px_per_deg
        total_lat_px = (max_lat - min_lat) * px_per_deg

        n_lon = max(1, int(np.ceil(total_lon_px / self.DW_MAX_TILE_PX)))
        n_lat = max(1, int(np.ceil(total_lat_px / self.DW_MAX_TILE_PX)))

        step_lon = (max_lon - min_lon) / n_lon
        step_lat = (max_lat - min_lat) / n_lat

        tiles = []
        for i in range(n_lon):
            for j in range(n_lat):
                tiles.append({
                    "min_lon": min_lon + i * step_lon,
                    "max_lon": min_lon + (i + 1) * step_lon,
                    "min_lat": min_lat + j * step_lat,
                    "max_lat": min_lat + (j + 1) * step_lat,
                })
        total_mb = (total_lon_px * total_lat_px * 10 * 4) / (1024 * 1024)
        print(f"[DW@{self.DW_SCALE}m] {len(tiles)} tiles ({n_lon}×{n_lat}), "
              f"{total_lon_px:.0f}×{total_lat_px:.0f}px, ~{total_mb:.1f}MB est.")
        return tiles

    def _download_tile_computepixels(
        self, composite, tile: dict, output_dir: Path,
        job_id: str, idx: int, scale: int = 10
    ) -> Path:
        """
        Descarga un tile usando ee.data.computePixels — API moderna de GEE.
        Más rápida que getDownloadURL y sin overhead de ZIP.
        Retorna path al GeoTIFF generado.
        """
        region = ee.Geometry.Rectangle([
            tile["min_lon"], tile["min_lat"],
            tile["max_lon"], tile["max_lat"],
        ])

        proj = _gee_call_with_timeout(
            lambda: ee.Projection("EPSG:4326").atScale(scale).getInfo(),
            _GEE_GETINFO_TIMEOUT, "DW",
        )
        scale_x = proj["transform"][0]   # siempre positivo

        width = max(1, int(round(abs(tile["max_lon"] - tile["min_lon"]) / abs(scale_x))))
        height = max(1, int(round(abs(tile["max_lat"] - tile["min_lat"]) / abs(scale_x))))
        width = min(width, 1800)
        height = min(height, 1800)

        # --- DEBUG logging detallado ---
        print(f"[computePixels-DEBUG] job={job_id} tile={idx}", flush=True)
        print(
            f"[computePixels-DEBUG] Tile bbox: "
            f"lon=[{tile['min_lon']:.5f},{tile['max_lon']:.5f}] "
            f"lat=[{tile['min_lat']:.5f},{tile['max_lat']:.5f}]",
            flush=True,
        )
        print(
            f"[computePixels-DEBUG] Grid: {width}x{height}px, scale={scale}m, "
            f"scaleX={scale_x:.8f}",
            flush=True,
        )
        print(
            f"[computePixels-DEBUG] translateX={tile['min_lon']:.5f} "
            f"translateY={tile['max_lat']:.5f} scaleY={-abs(scale_x):.8f}",
            flush=True,
        )

        request = {
            "expression": composite.clip(region),
            "fileFormat": "GEO_TIFF",
            "grid": {
                "dimensions": {"width": width, "height": height},
                "affineTransform": {
                    "scaleX":     abs(scale_x),
                    "shearX":     0,
                    "translateX": tile["min_lon"],
                    "shearY":     0,
                    "scaleY":     -abs(scale_x),   # NEGATIVO obligatorio
                    "translateY": tile["max_lat"],
                },
                "crsCode": "EPSG:4326",
            },
        }

        tif_bytes = _gee_call_with_timeout(
            lambda: ee.data.computePixels(request),
            _GEE_COMPUTE_TIMEOUT, "DW",
        )
        tif_path = output_dir / f"{job_id}_tile_{idx}.tif"
        tif_path.write_bytes(tif_bytes)

        # Verificar bounds del tile descargado
        with rasterio.open(tif_path) as src:
            b = src.bounds
            print(
                f"[computePixels-DEBUG] Descargado bounds: "
                f"lon=[{b.left:.5f},{b.right:.5f}] "
                f"lat=[{b.bottom:.5f},{b.top:.5f}]",
                flush=True,
            )
            lon_ok = abs(b.left - tile["min_lon"]) < 0.001 and abs(b.right - tile["max_lon"]) < 0.001
            lat_ok = abs(b.bottom - tile["min_lat"]) < 0.001 and abs(b.top - tile["max_lat"]) < 0.001
            print(
                f"[computePixels-DEBUG] Cobertura: lon_ok={lon_ok}, lat_ok={lat_ok}",
                flush=True,
            )

        return tif_path

    def clear_cache(self, job_id_prefix: str = None):
        """Limpia tiles cacheados. Si se pasa prefix, solo borra los de ese job."""
        tiles_dir = Path(os.getenv("DATA_DIR", "./data/tiles"))
        if not tiles_dir.exists():
            return
        pattern = f"{job_id_prefix}*" if job_id_prefix else "*"
        cleaned = [f for f in tiles_dir.glob(pattern) if f.is_file()]
        for f in cleaned:
            f.unlink()
        if cleaned:
            print(f"[Cache] {len(cleaned)} archivo(s) eliminados")

    def _verify_raster_bbox(self, raster_path: Path, aoi_geojson: dict, job_id: str) -> bool:
        """Verifica que el raster descargado cubre el AOI usando intersección de bboxes."""
        from shapely.geometry import shape as shp_shape

        # Extraer bbox del AOI (robusto a Polygon/MultiPolygon)
        try:
            aoi_shape = shp_shape(aoi_geojson)
            min_lon, min_lat, max_lon, max_lat = aoi_shape.bounds
        except Exception:
            coords = aoi_geojson.get("coordinates", [[]])[0]
            if not coords:
                return True
            lons = [c[0] for c in coords]
            lats = [c[1] for c in coords]
            min_lon, max_lon = min(lons), max(lons)
            min_lat, max_lat = min(lats), max(lats)

        aoi_center_lon = (min_lon + max_lon) / 2
        aoi_center_lat = (min_lat + max_lat) / 2

        with rasterio.open(raster_path) as src:
            b = src.bounds

        print(
            f"[verify-bbox] Raster {raster_path.name}: "
            f"lon=[{b.left:.4f},{b.right:.4f}] lat=[{b.bottom:.4f},{b.top:.4f}]",
            flush=True,
        )
        print(
            f"[verify-bbox] AOI bbox: "
            f"lon=[{min_lon:.4f},{max_lon:.4f}] lat=[{min_lat:.4f},{max_lat:.4f}]",
            flush=True,
        )
        print(
            f"[verify-bbox] AOI centro: ({aoi_center_lon:.4f}, {aoi_center_lat:.4f})",
            flush=True,
        )

        # Verificar intersección de bounding boxes con tolerancia (~1km)
        TOL = 0.01
        intersects = (
            b.left   <= max_lon + TOL and
            b.right  >= min_lon - TOL and
            b.bottom <= max_lat + TOL and
            b.top    >= min_lat - TOL
        )

        if not intersects:
            print(
                f"[verify-bbox] FALLO - raster NO intersecta el AOI (job={job_id})",
                flush=True,
            )
            raster_path.unlink(missing_ok=True)
            meta = raster_path.with_suffix(".json")
            if meta.exists():
                meta.unlink()
            raise ValueError(
                f"Raster descargado ({raster_path.name}) no cubre el AOI - revisar coordenadas"
            )

        print("[verify-bbox] OK - raster cubre el AOI", flush=True)
        return True

    def _inject_argmax_label(self, tif_path: Path):
        """
        Lee un GeoTIFF de probabilidades DW (9 bands), calcula argmax en numpy,
        y reescribe el archivo con label como band 0 + 9 probs = 10 bands.
        Esto evita el bug de arrayArgmax() + computePixels que produce -inf.
        """
        with rasterio.open(tif_path) as src:
            probs = src.read()  # (9, H, W)
            meta = src.meta.copy()

        # Compute argmax: class 0..8 (water, trees, grass, ...)
        label = np.argmax(probs, axis=0).astype(np.float64)  # (H, W)
        # Mask nodata: where ALL prob bands are -inf or nan → set label to -inf
        all_nodata = ~np.any(np.isfinite(probs), axis=0)
        label[all_nodata] = float("-inf")

        # Stack: label + probs = 10 bands (matches expected format)
        out = np.concatenate([label[np.newaxis, ...], probs], axis=0)
        meta.update({"count": out.shape[0]})

        with rasterio.open(tif_path, "w", **meta) as dst:
            dst.write(out)
        print(f"[DW] Argmax label inyectado (numpy) — {out.shape[0]} bands, {tif_path.name}")

    def _split_bbox_at_scale(self, coords, n_bands: int, scale: int) -> list:
        """Divide bbox en tiles que quepan bajo MAX_TILE_BYTES a la escala dada (legacy fallback)."""
        ring = coords[0] if isinstance(coords[0][0], (list, tuple)) else coords
        lons = [p[0] for p in ring]
        lats = [p[1] for p in ring]
        min_lon, max_lon = min(lons), max(lons)
        min_lat, max_lat = min(lats), max(lats)

        deg_per_pixel = scale / 111_320
        width_px = (max_lon - min_lon) / deg_per_pixel
        height_px = (max_lat - min_lat) / deg_per_pixel
        total_bytes = width_px * height_px * n_bands * BYTES_PER_PIXEL_BAND
        print(f"[GEE] Descargando a {scale}m — {width_px:.0f}×{height_px:.0f}px — {total_bytes/1e6:.1f}MB estimado")

        if total_bytes <= MAX_TILE_BYTES:
            return [ee.Geometry.Rectangle([min_lon, min_lat, max_lon, max_lat])]

        n_tiles = math.ceil(total_bytes / MAX_TILE_BYTES)
        n_cols = math.ceil(math.sqrt(n_tiles))
        n_rows = math.ceil(n_tiles / n_cols)
        d_lon = (max_lon - min_lon) / n_cols
        d_lat = (max_lat - min_lat) / n_rows

        tiles = []
        for r in range(n_rows):
            for c in range(n_cols):
                tiles.append(ee.Geometry.Rectangle([
                    min_lon + c * d_lon, min_lat + r * d_lat,
                    min_lon + (c + 1) * d_lon, min_lat + (r + 1) * d_lat,
                ]))
        print(f"[DW] Grid: {n_cols}x{n_rows} = {len(tiles)} tiles ({total_bytes/1e6:.1f} MB est.)")
        return tiles

    def _download_tile_at_scale(self, image, region, output_path: Path, scale: int):
        """Descarga un tile de imagen a la escala indicada."""
        url = image.getDownloadURL({
            "region": region, "scale": scale, "format": "GEO_TIFF",
        })
        resp = requests.get(url, stream=True, timeout=300)
        resp.raise_for_status()
        with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp:
            shutil.copyfileobj(resp.raw, tmp)
            tmp_path = tmp.name
        try:
            with zipfile.ZipFile(tmp_path, "r") as zf:
                tiffs = [n for n in zf.namelist() if n.endswith(".tif")]
                if tiffs:
                    with zf.open(tiffs[0]) as src, open(output_path, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                else:
                    shutil.move(tmp_path, output_path)
                    return
        except zipfile.BadZipFile:
            shutil.move(tmp_path, output_path)
            return
        os.remove(tmp_path)

    # ------------------------------------------------------------------
    # Temporal stack (4 estaciones)
    # ------------------------------------------------------------------
    def get_temporal_stack(
        self, aoi_geojson: dict, year: int = 2023, job_id: str = "test"
    ) -> list:
        seasons = [
            (f"{year}-01-01", f"{year}-03-31", "primavera"),
            (f"{year}-04-01", f"{year}-06-30", "verano"),
            (f"{year}-07-01", f"{year}-09-30", "otono"),
            (f"{year}-10-01", f"{year}-12-31", "invierno"),
        ]
        paths = []
        for start, end, name in seasons:
            path = self.get_sentinel2_composite(
                aoi_geojson, start, end, job_id=f"{job_id}_{name}"
            )
            paths.append(path)
        return paths

    # ------------------------------------------------------------------
    # Helpers privados
    # ------------------------------------------------------------------
    @staticmethod
    def _extract_coords(aoi_geojson: dict):
        coords = aoi_geojson.get("coordinates")
        if not coords:
            if aoi_geojson.get("type") == "Feature" and "geometry" in aoi_geojson:
                coords = aoi_geojson["geometry"]["coordinates"]
        return coords

    def _split_bbox(self, coords, n_bands: int) -> list:
        """Divide el bounding-box del AOI en sub-rectángulos que quepan bajo MAX_TILE_BYTES."""
        ring = coords[0] if isinstance(coords[0][0], (list, tuple)) else coords
        lons = [p[0] for p in ring]
        lats = [p[1] for p in ring]
        min_lon, max_lon = min(lons), max(lons)
        min_lat, max_lat = min(lats), max(lats)

        # Tamaño aproximado en píxeles a SCALE (10m)
        deg_per_pixel = SCALE / 111_320
        width_px = (max_lon - min_lon) / deg_per_pixel
        height_px = (max_lat - min_lat) / deg_per_pixel
        total_bytes = width_px * height_px * n_bands * BYTES_PER_PIXEL_BAND
        print(f"[GEE] Descargando a {SCALE}m — {width_px:.0f}×{height_px:.0f}px — {total_bytes/1e6:.1f}MB estimado")

        if total_bytes <= MAX_TILE_BYTES:
            return [ee.Geometry.Rectangle([min_lon, min_lat, max_lon, max_lat])]

        # Calcular cuántos tiles necesitamos
        n_tiles = math.ceil(total_bytes / MAX_TILE_BYTES)
        n_cols = math.ceil(math.sqrt(n_tiles))
        n_rows = math.ceil(n_tiles / n_cols)

        d_lon = (max_lon - min_lon) / n_cols
        d_lat = (max_lat - min_lat) / n_rows

        tiles = []
        for r in range(n_rows):
            for c in range(n_cols):
                t_min_lon = min_lon + c * d_lon
                t_max_lon = min_lon + (c + 1) * d_lon
                t_min_lat = min_lat + r * d_lat
                t_max_lat = min_lat + (r + 1) * d_lat
                tiles.append(
                    ee.Geometry.Rectangle([t_min_lon, t_min_lat, t_max_lon, t_max_lat])
                )
        print(f"[GEE] Grid: {n_cols}x{n_rows} = {len(tiles)} tiles  ({total_bytes/1e6:.1f} MB estimados)")
        return tiles

    def _download_tile(self, image, region, output_path: Path):
        """Descarga un recorte de la imagen como GeoTIFF."""
        url = image.getDownloadURL(
            {"region": region, "scale": SCALE, "format": "GEO_TIFF"}
        )
        resp = requests.get(url, stream=True, timeout=300)
        resp.raise_for_status()

        with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp:
            shutil.copyfileobj(resp.raw, tmp)
            tmp_path = tmp.name

        # GEE puede responder con un ZIP o directamente con un TIFF
        try:
            with zipfile.ZipFile(tmp_path, "r") as zf:
                tiffs = [n for n in zf.namelist() if n.endswith(".tif")]
                if tiffs:
                    with zf.open(tiffs[0]) as src, open(output_path, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                else:
                    # No hay TIF dentro del ZIP — usar el propio archivo
                    shutil.move(tmp_path, output_path)
                    return
        except zipfile.BadZipFile:
            # No era ZIP, asumir que es directamente un GeoTIFF
            shutil.move(tmp_path, output_path)
            return

        os.remove(tmp_path)

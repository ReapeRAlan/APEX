"""
SAM-Geo Polygon Refiner -- optional post-processing service.

Takes rough polygons (GeoJSON features) produced by detection engines and
refines their boundaries using the Segment Anything Model (SAM) via the
samgeo library, guided by a Sentinel-2 RGB composite raster.

This is a **post-processing service**, not an engine.  It is controlled by
a config flag and degrades gracefully: if samgeo is not installed or GPU is
unavailable the original features are returned unchanged.

Requires:  samgeo>=0.12.0, torch, rasterio, shapely
Config:    TORCH_DEVICE (cuda/cpu), MAX_VRAM_GB
"""

import logging
import warnings
from pathlib import Path
from typing import Optional

import numpy as np

try:
    import rasterio
    from rasterio.features import shapes as rio_shapes
    from rasterio.windows import Window
    from rasterio.transform import array_bounds  # noqa: F401
except ImportError:
    rasterio = None  # type: ignore[assignment]

try:
    from shapely.geometry import shape, mapping, box
    from shapely.ops import unary_union
except ImportError:
    shape = mapping = box = unary_union = None  # type: ignore[assignment,misc]

# Graceful import -- samgeo is a large optional dependency
try:
    from samgeo import SamGeo
    _HAS_SAMGEO = True
except Exception:
    _HAS_SAMGEO = False
    SamGeo = None  # type: ignore[assignment,misc]

from ..config import settings

warnings.filterwarnings("ignore", module="samgeo")

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------
MIN_AREA_PX: int = 100    # skip features whose bbox is smaller than this (px)
MAX_FEATURES: int = 50     # SAM is slow -- cap per-call batch size


class PolygonRefiner:
    """Refine rough detection polygons using SAM (Segment Anything Model).

    Workflow for each feature:
        1. Crop the Sentinel-2 RGB composite to the feature bbox + buffer.
        2. Run SAM segmentation on the cropped chip.
        3. Find the SAM mask that overlaps the original polygon the most.
        4. Vectorise that mask back to a GeoJSON polygon.
        5. Return the refined geometry, preserving original properties and
           adding ``refined: true``.

    If SAM is unavailable (missing library or no GPU), every method degrades
    safely and returns the original features with a warning.
    """

    # SAM checkpoint -- vit_h gives best quality, vit_b is lighter
    _MODEL_TYPE = "vit_h"

    def __init__(
        self,
        device: Optional[str] = None,
        max_vram_gb: Optional[float] = None,
    ) -> None:
        self._device: str = device or settings.TORCH_DEVICE
        self._max_vram_gb: float = max_vram_gb or settings.MAX_VRAM_GB
        self._model: Optional["SamGeo"] = None  # lazy-loaded
        logger.info(
            "PolygonRefiner created  device=%s  max_vram=%.1f GB  samgeo=%s",
            self._device,
            self._max_vram_gb,
            "available" if _HAS_SAMGEO else "NOT installed",
        )

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def is_available(self) -> bool:
        """Return True if samgeo is importable **and** a GPU is reachable."""
        if not _HAS_SAMGEO:
            return False
        try:
            import torch
            if self._device.startswith("cuda") and not torch.cuda.is_available():
                return False
            return True
        except ImportError:
            return False

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def refine_polygons(
        self,
        features: list[dict],
        composite_path: str,
    ) -> list[dict]:
        """Refine a list of GeoJSON Feature dicts using SAM.

        Parameters
        ----------
        features:
            GeoJSON Feature dicts with Polygon/MultiPolygon geometries.
        composite_path:
            Path to a Sentinel-2 RGB GeoTIFF (3-band, uint8/uint16).

        Returns
        -------
        list[dict]
            Features with refined geometries where possible.  Each
            successfully refined feature gets ``properties.refined = true``.
            Features that cannot be refined keep their original geometry.
        """
        if not features:
            return features

        # Guard: samgeo not installed --------------------------------
        if not _HAS_SAMGEO:
            logger.warning(
                "samgeo is not installed -- returning %d features unrefined. "
                "Install with:  pip install samgeo",
                len(features),
            )
            return features

        # Guard: GPU check -------------------------------------------
        if not self.is_available():
            logger.warning(
                "SAM requires GPU but device '%s' is not available -- "
                "returning features unrefined.",
                self._device,
            )
            return features

        # Guard: missing hard deps -----------------------------------
        if rasterio is None or shape is None:
            logger.warning(
                "rasterio/shapely not installed -- returning features unrefined."
            )
            return features

        # Lazy-load the SAM model ------------------------------------
        try:
            self._ensure_model()
        except Exception as exc:
            logger.error("Failed to load SAM model: %s", exc, exc_info=True)
            return features

        composite_path_obj = Path(composite_path)
        if not composite_path_obj.exists():
            logger.error("Composite raster not found: %s", composite_path)
            return features

        # Cap batch size so we don't run forever ---------------------
        to_refine = features[: MAX_FEATURES]
        skipped = features[MAX_FEATURES:]
        if skipped:
            logger.info(
                "Capping SAM refinement at %d features (%d skipped).",
                MAX_FEATURES,
                len(skipped),
            )

        logger.info(
            "Starting SAM polygon refinement for %d features with %s ...",
            len(to_refine),
            composite_path_obj.name,
        )

        refined: list[dict] = []

        for idx, feat in enumerate(to_refine):
            try:
                refined_feat = self._refine_single(feat, composite_path, idx)
                refined.append(refined_feat)
            except Exception as exc:
                logger.warning(
                    "SAM refinement failed for feature %d/%d: %s",
                    idx + 1,
                    len(to_refine),
                    exc,
                )
                refined.append(feat)  # keep original on failure

        # Append the features that were beyond the cap
        refined.extend(skipped)

        n_ok = sum(
            1 for f in refined
            if f.get("properties", {}).get("refined") is True
        )
        logger.info(
            "SAM refinement complete: %d/%d features refined successfully.",
            n_ok,
            len(to_refine),
        )
        return refined

    # ------------------------------------------------------------------
    # Internal: per-feature refinement
    # ------------------------------------------------------------------

    def _refine_single(self, feat: dict, raster_path: str, idx: int) -> dict:
        """Refine a single GeoJSON feature against the composite raster."""
        geom = shape(feat["geometry"])
        bbox = geom.bounds  # (minx, miny, maxx, maxy)

        # Crop the raster to the bbox + pixel buffer
        crop_array, crop_transform, crop_crs = self._crop_raster(
            raster_path, bbox, buffer_px=50,
        )

        if crop_array is None:
            logger.debug("Feature %d: crop returned None -- skipping.", idx)
            return feat

        # Sanity: skip very small chips
        _, h, w = crop_array.shape
        if h * w < MIN_AREA_PX:
            logger.debug(
                "Feature %d: chip too small (%dx%d < %d px) -- skipping.",
                idx, w, h, MIN_AREA_PX,
            )
            return feat

        # Transpose to HWC uint8 for SAM (expects 3-channel image)
        img_hwc = np.transpose(crop_array[:3], (1, 2, 0)).astype(np.uint8)

        # Compute pixel-space bbox of the original polygon inside the crop
        inv_transform = ~crop_transform
        px_minx, px_miny = inv_transform * (bbox[0], bbox[3])  # upper-left
        px_maxx, px_maxy = inv_transform * (bbox[2], bbox[1])  # lower-right
        # Ensure correct ordering after transform
        px_minx, px_maxx = sorted([px_minx, px_maxx])
        px_miny, px_maxy = sorted([px_miny, px_maxy])
        sam_box = np.array([
            max(0, px_minx),
            max(0, px_miny),
            min(w, px_maxx),
            min(h, px_maxy),
        ])

        # Centre point of original polygon as foreground prompt
        centroid = geom.centroid
        cx, cy = inv_transform * (centroid.x, centroid.y)
        point_coords = np.array([[cx, cy]])
        point_labels = np.array([1])  # foreground

        # Run SAM prediction
        self._model.set_image(img_hwc)
        masks = self._model.predict(
            point_coords=point_coords,
            point_labels=point_labels,
            box=sam_box,
        )

        if masks is None or len(masks) == 0:
            logger.debug("Feature %d: SAM returned no masks.", idx)
            return feat

        # Pick the mask with best IoU against the original polygon
        best_mask = self._select_best_mask(masks, geom, crop_transform)

        if best_mask is None:
            logger.debug("Feature %d: no suitable mask found.", idx)
            return feat

        # Vectorise the mask back to geo-coordinates
        refined_geom = self._mask_to_geojson(best_mask, crop_transform)

        if refined_geom is None or refined_geom.is_empty:
            return feat

        # Build the refined feature -- keep original properties
        refined_feat = {
            "type": "Feature",
            "geometry": mapping(refined_geom),
            "properties": dict(feat.get("properties", {})),
        }
        refined_feat["properties"]["refined"] = True
        refined_feat["properties"]["refined_area_m2"] = round(refined_geom.area, 2)
        return refined_feat

    # ------------------------------------------------------------------
    # Raster cropping
    # ------------------------------------------------------------------

    def _crop_raster(
        self,
        raster_path: str,
        bbox: tuple,
        buffer_px: int = 50,
    ):
        """Crop a raster to *bbox* plus a pixel buffer.

        Parameters
        ----------
        raster_path:
            Path to a GeoTIFF.
        bbox:
            (minx, miny, maxx, maxy) in the raster's CRS.
        buffer_px:
            Extra pixels to add on every side of the bbox window.

        Returns
        -------
        (array, transform, crs) or (None, None, None) on failure.
            *array* has shape (bands, height, width).
        """
        try:
            with rasterio.open(raster_path) as src:
                # Convert geographic bbox to pixel row/col
                row_min, col_min = src.index(bbox[0], bbox[3])
                row_max, col_max = src.index(bbox[2], bbox[1])

                # Ensure min < max
                row_min, row_max = sorted([row_min, row_max])
                col_min, col_max = sorted([col_min, col_max])

                # Apply pixel buffer
                row_min = max(0, row_min - buffer_px)
                col_min = max(0, col_min - buffer_px)
                row_max = min(src.height, row_max + buffer_px)
                col_max = min(src.width, col_max + buffer_px)

                if row_min >= row_max or col_min >= col_max:
                    return None, None, None

                window = Window.from_slices(
                    (row_min, row_max),
                    (col_min, col_max),
                )
                data = src.read(window=window)
                win_transform = src.window_transform(window)
                return data, win_transform, src.crs

        except Exception as exc:
            logger.warning("Failed to crop raster at bbox %s: %s", bbox, exc)
            return None, None, None

    # ------------------------------------------------------------------
    # Mask selection & vectorisation helpers
    # ------------------------------------------------------------------

    def _select_best_mask(
        self,
        masks: np.ndarray,
        original_geom,
        transform,
    ):
        """Choose the SAM mask with the highest IoU vs *original_geom*.

        Parameters
        ----------
        masks:
            Array of binary masks from SAM, shape (N, H, W).
        original_geom:
            Shapely geometry of the original polygon.
        transform:
            Affine transform for the crop window.

        Returns
        -------
        np.ndarray or None
            Best mask as a 2-D uint8 array, or None if nothing matches.
        """
        best_iou = 0.0
        best_mask = None

        # If masks is 2-D (single mask), promote to 3-D
        if masks.ndim == 2:
            masks = masks[np.newaxis, ...]

        for i in range(masks.shape[0]):
            mask_2d = masks[i].astype(np.uint8)

            mask_geom = self._mask_to_geojson(mask_2d, transform)
            if mask_geom is None or mask_geom.is_empty:
                continue

            try:
                intersection = original_geom.intersection(mask_geom).area
                union = original_geom.union(mask_geom).area
                iou = intersection / union if union > 0 else 0.0
            except Exception:
                continue

            if iou > best_iou:
                best_iou = iou
                best_mask = mask_2d

        if best_mask is not None:
            logger.debug("Best mask IoU: %.3f", best_iou)

        return best_mask

    @staticmethod
    def _mask_to_geojson(mask: np.ndarray, transform):
        """Convert a binary mask to a Shapely geometry using rasterio.

        Parameters
        ----------
        mask:
            2-D uint8 array (0/1).
        transform:
            Affine transform mapping pixel coords to CRS coords.

        Returns
        -------
        shapely.geometry.BaseGeometry or None
        """
        try:
            polygons = []
            for geom_dict, value in rio_shapes(
                mask, mask=(mask == 1), transform=transform,
            ):
                if value == 1:
                    poly = shape(geom_dict)
                    if poly.is_valid and not poly.is_empty:
                        polygons.append(poly)

            if not polygons:
                return None

            merged = unary_union(polygons)
            return merged if merged.is_valid else merged.buffer(0)
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Lazy model loading
    # ------------------------------------------------------------------

    def _ensure_model(self) -> None:
        """Lazy-load the SAM model on first use."""
        if self._model is not None:
            return

        logger.info(
            "Loading SAM model (%s) on device=%s  (VRAM cap %.1f GB) ...",
            self._MODEL_TYPE,
            self._device,
            self._max_vram_gb,
        )

        self._model = SamGeo(
            model_type=self._MODEL_TYPE,
            device=self._device,
            automatic=False,
        )

        logger.info("SAM model loaded successfully.")

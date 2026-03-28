"""
Prithvi-EO Foundation Model engine for multi-class land use change classification.

Uses the NASA + IBM Prithvi-EO-2.0 geospatial foundation model with patch-based
inference on Sentinel-2 imagery.  Supports loading via terratorch (preferred) or
direct HuggingFace transformers as fallback.

NOTE: The project already has a Tiler class in services/tiler.py that handles
generic patch creation with configurable overlap.  This engine uses its own
_create_patches helper with patch_size=224 (Prithvi's native input resolution)
and stride=patch_size (no overlap), which differs from the Tiler defaults.
"""
from __future__ import annotations

import logging

import numpy as np
import rasterio
import torch
from rasterio.features import shapes
from shapely.geometry import shape, mapping

from ..config import settings

logger = logging.getLogger(__name__)

# ── Class labels (Prithvi multi-class land-use) ─────────────────────────
CLASS_LABELS: dict[int, str] = {
    0: "sin_datos",
    1: "agua",
    2: "bosque",
    3: "pastizal",
    4: "humedal",
    5: "cultivo",
    6: "matorral",
    7: "urbano",
    8: "suelo_desnudo",
    9: "nieve_hielo",
}

# Sentinel-2 reflectance scale factor
_S2_SCALE = 10_000.0

# ── Optional dependency flags ────────────────────────────────────────────
_HAS_TERRATORCH = False
_HAS_TRANSFORMERS = False

try:
    import terratorch  # noqa: F401
    _HAS_TERRATORCH = True
except ImportError:
    pass

try:
    from transformers import AutoModel, AutoImageProcessor  # noqa: F401
    _HAS_TRANSFORMERS = True
except ImportError:
    pass


def _area_deg2_to_ha(area_deg2: float) -> float:
    """Convert an area expressed in squared degrees to hectares (approximate)."""
    return area_deg2 * (111_320 ** 2) * np.cos(np.radians(20)) / 10_000


class PrithviEngine:
    """Patch-based inference engine for the Prithvi-EO foundation model.

    Reads a Sentinel-2 composite (GeoTIFF), splits it into fixed-size patches,
    runs classification through the model, stitches the prediction map, and
    vectorises the result into a GeoJSON FeatureCollection with per-class stats.
    """

    MIN_AREA_DEG2: float = 5e-8  # ~0.5 ha noise filter
    MAX_FEATURES: int = 300

    # ------------------------------------------------------------------
    # Init
    # ------------------------------------------------------------------
    def __init__(self, device: str | None = None, max_vram_gb: float | None = None):
        self.device: str = device or settings.TORCH_DEVICE
        self.max_vram_gb: float = max_vram_gb or settings.MAX_VRAM_GB
        self.batch_size: int = settings.INFERENCE_BATCH_SIZE
        self.model = None
        self.processor = None
        self._backend: str | None = None  # "terratorch" | "transformers"

        # Validate CUDA availability; fall back to CPU silently
        if self.device == "cuda" and not torch.cuda.is_available():
            logger.warning("CUDA requested but not available — falling back to CPU")
            self.device = "cpu"

        logger.info(
            "PrithviEngine initialised  device=%s  max_vram=%.1f GB  batch=%d",
            self.device, self.max_vram_gb, self.batch_size,
        )

    # ------------------------------------------------------------------
    # Availability check
    # ------------------------------------------------------------------
    def is_available(self) -> bool:
        """Return True when at least one model backend can be imported."""
        return _HAS_TERRATORCH or _HAS_TRANSFORMERS

    # ------------------------------------------------------------------
    # Model loading
    # ------------------------------------------------------------------
    def load_model(
        self,
        model_id: str = "ibm-nasa-geospatial/Prithvi-EO-2.0-300M",
    ) -> None:
        """Load the Prithvi-EO model weights.

        Strategy:
            1. Try terratorch (dedicated geospatial toolkit) first.
            2. Fall back to HuggingFace transformers ``AutoModel``.
            3. Raise RuntimeError if neither backend is usable.
        """
        if self.model is not None:
            logger.debug("Model already loaded (%s) — skipping", self._backend)
            return

        # ── Attempt 1: terratorch ────────────────────────────────────
        if _HAS_TERRATORCH:
            try:
                from terratorch.models import PrithviModelFactory

                logger.info("Loading Prithvi via terratorch: %s", model_id)
                factory = PrithviModelFactory()
                self.model = factory.build_model(
                    model_id,
                    task="classification",
                    num_classes=len(CLASS_LABELS),
                )
                self.model.to(self.device).eval()
                self.processor = None  # terratorch handles preprocessing
                self._backend = "terratorch"
                logger.info("Prithvi loaded successfully via terratorch")
                return
            except Exception as exc:
                logger.warning(
                    "terratorch load failed (%s) — trying transformers fallback", exc,
                )

        # ── Attempt 2: HuggingFace transformers ──────────────────────
        if _HAS_TRANSFORMERS:
            try:
                from transformers import AutoModel, AutoImageProcessor

                logger.info("Loading Prithvi via transformers: %s", model_id)
                self.processor = AutoImageProcessor.from_pretrained(model_id)
                self.model = AutoModel.from_pretrained(
                    model_id,
                    trust_remote_code=True,
                    num_labels=len(CLASS_LABELS),
                )
                self.model.to(self.device).eval()
                self._backend = "transformers"
                logger.info("Prithvi loaded successfully via transformers")
                return
            except Exception as exc:
                logger.warning("transformers load failed: %s", exc)

        raise RuntimeError(
            "Cannot load Prithvi-EO model — install terratorch or "
            "huggingface transformers with the appropriate model card."
        )

    # ------------------------------------------------------------------
    # Public inference entry-point
    # ------------------------------------------------------------------
    def predict_patches(
        self,
        composite_path: str,
        patch_size: int = 224,
    ) -> tuple[dict, dict]:
        """Run patch-based classification on a Sentinel-2 composite GeoTIFF.

        Parameters
        ----------
        composite_path : str
            Path to a multi-band Sentinel-2 GeoTIFF (reflectance values).
        patch_size : int
            Spatial size of each square patch fed to the model (default 224).

        Returns
        -------
        tuple[dict, dict]
            (geojson_featurecollection, stats_dict)
        """
        empty_geojson: dict = {"type": "FeatureCollection", "features": []}
        empty_stats: dict = {}

        if not self.is_available():
            logger.warning(
                "Prithvi model dependencies not available — returning empty results"
            )
            return empty_geojson, empty_stats

        # Ensure model is loaded
        if self.model is None:
            try:
                self.load_model()
            except RuntimeError as exc:
                logger.error("Failed to load Prithvi model: %s", exc)
                return empty_geojson, empty_stats

        # ── Read raster ──────────────────────────────────────────────
        composite_path = str(composite_path)
        with rasterio.open(composite_path) as src:
            raster = src.read()  # shape: (C, H, W)
            transform = src.transform
            crs = src.crs

        C, H, W = raster.shape
        logger.info(
            "Raster loaded: %s  bands=%d  size=%dx%d  crs=%s",
            composite_path, C, H, W, crs,
        )

        # ── Create patches ───────────────────────────────────────────
        patches_info = self._create_patches(raster, patch_size)
        logger.info("Created %d patches (%dx%d)", len(patches_info), patch_size, patch_size)

        # ── Batch inference ──────────────────────────────────────────
        predictions: list[np.ndarray] = []
        offsets: list[tuple[int, int]] = []

        for batch_start in range(0, len(patches_info), self.batch_size):
            batch = patches_info[batch_start : batch_start + self.batch_size]
            batch_arrays = []

            for patch_arr, row_off, col_off in batch:
                normed = self._normalize_patch(patch_arr)
                batch_arrays.append(normed)
                offsets.append((row_off, col_off))

            # Stack into (B, C, pH, pW) tensor
            batch_tensor = torch.from_numpy(
                np.stack(batch_arrays, axis=0)
            ).float().to(self.device)

            with torch.no_grad():
                if self._backend == "terratorch":
                    output = self.model(batch_tensor)
                    # terratorch returns an object with .output attribute
                    logits = (
                        output.output
                        if hasattr(output, "output")
                        else output
                    )
                else:
                    # transformers backend — may need pixel_values kwarg
                    if self.processor is not None:
                        # Processor expects (B, C, H, W) numpy; convert back
                        inputs = self.processor(
                            images=batch_tensor.cpu().numpy(),
                            return_tensors="pt",
                        )
                        inputs = {k: v.to(self.device) for k, v in inputs.items()}
                        logits = self.model(**inputs).logits
                    else:
                        logits = self.model(batch_tensor).logits

            # logits shape: (B, num_classes, pH, pW) or (B, num_classes)
            if logits.dim() == 4:
                # Per-pixel classification
                preds = logits.argmax(dim=1).cpu().numpy()  # (B, pH, pW)
            elif logits.dim() == 2:
                # Scene-level: broadcast single label to full patch
                labels = logits.argmax(dim=1).cpu().numpy()  # (B,)
                preds = np.stack(
                    [np.full((patch_size, patch_size), lbl, dtype=np.int32) for lbl in labels],
                    axis=0,
                )
            else:
                # Unexpected shape — collapse to argmax over last dim
                preds = logits.argmax(dim=-1).cpu().numpy()

            for i in range(preds.shape[0]):
                predictions.append(preds[i])

        # ── Stitch into full-size class map ──────────────────────────
        class_map = self._stitch_predictions(
            predictions, offsets, (H, W), patch_size,
        )
        logger.info("Prediction map stitched: shape=%s", class_map.shape)

        # ── Per-class statistics ─────────────────────────────────────
        total_valid = int(np.sum(class_map > 0))  # exclude sin_datos (0)
        total_pixels = max(H * W, 1)
        class_pcts: dict[str, float] = {}
        for code, name in CLASS_LABELS.items():
            count = int(np.sum(class_map == code))
            class_pcts[name] = round(100.0 * count / total_pixels, 2)

        # ── Vectorise class map to GeoJSON ───────────────────────────
        features: list[dict] = []
        class_map_u8 = class_map.astype(np.uint8)

        for code, clase in CLASS_LABELS.items():
            if code == 0:
                continue  # skip sin_datos
            if class_pcts.get(clase, 0) < 0.05:
                continue

            mask = (class_map_u8 == code).astype(np.uint8)
            for geom, val in shapes(mask, transform=transform):
                if val != 1:
                    continue
                poly = shape(geom)
                area_ha = _area_deg2_to_ha(poly.area)
                if poly.area < self.MIN_AREA_DEG2:
                    continue

                features.append({
                    "type": "Feature",
                    "geometry": mapping(poly),
                    "properties": {
                        "class": clase,
                        "class_code": code,
                        "area_ha": round(area_ha, 2),
                    },
                })

        features.sort(key=lambda f: f["properties"]["area_ha"], reverse=True)
        features = features[: self.MAX_FEATURES]

        total_area_ha = sum(f["properties"]["area_ha"] for f in features)

        geojson: dict = {"type": "FeatureCollection", "features": features}
        stats: dict = {
            "classes": class_pcts,
            "n_features": len(features),
            "total_area_ha": round(total_area_ha, 1),
            "coverage_valid_pct": round(100.0 * total_valid / total_pixels, 2),
            "source": "Prithvi-EO-2.0-300M (NASA/IBM)",
            "backend": self._backend,
            "patch_size": patch_size,
        }

        logger.info(
            "[Prithvi] %d features, area=%.1f ha, backend=%s",
            len(features), total_area_ha, self._backend,
        )
        return geojson, stats

    # ------------------------------------------------------------------
    # Patch creation
    # ------------------------------------------------------------------
    def _create_patches(
        self,
        raster: np.ndarray,
        patch_size: int,
    ) -> list[tuple[np.ndarray, int, int]]:
        """Split a (C, H, W) raster into non-overlapping patches.

        The last row/column of patches is zero-padded when the raster
        dimensions are not evenly divisible by *patch_size*.

        Returns
        -------
        list[tuple[np.ndarray, int, int]]
            Each element is ``(patch_array, row_offset, col_offset)`` where
            *patch_array* has shape ``(C, patch_size, patch_size)``.
        """
        C, H, W = raster.shape
        stride = patch_size  # no overlap

        patches: list[tuple[np.ndarray, int, int]] = []

        for row in range(0, H, stride):
            for col in range(0, W, stride):
                row_end = row + patch_size
                col_end = col + patch_size

                patch = raster[:, row:row_end, col:col_end]

                # Pad if the patch is smaller than expected (image edge)
                _, pH, pW = patch.shape
                if pH < patch_size or pW < patch_size:
                    padded = np.zeros((C, patch_size, patch_size), dtype=raster.dtype)
                    padded[:, :pH, :pW] = patch
                    patch = padded

                patches.append((patch, row, col))

        return patches

    # ------------------------------------------------------------------
    # Sentinel-2 normalisation
    # ------------------------------------------------------------------
    @staticmethod
    def _normalize_patch(patch: np.ndarray) -> np.ndarray:
        """Normalise a Sentinel-2 reflectance patch to [0, 1].

        Sentinel-2 L2A reflectance values are stored as integers scaled by
        10 000.  This method divides by 10 000 and clips to the valid range.

        Parameters
        ----------
        patch : np.ndarray
            Raw reflectance array of shape ``(C, H, W)``.

        Returns
        -------
        np.ndarray
            Normalised array in ``[0, 1]``, same shape.
        """
        normalised = patch.astype(np.float32) / _S2_SCALE
        np.clip(normalised, 0.0, 1.0, out=normalised)
        return normalised

    # ------------------------------------------------------------------
    # Stitch predictions
    # ------------------------------------------------------------------
    @staticmethod
    def _stitch_predictions(
        predictions: list[np.ndarray],
        offsets: list[tuple[int, int]],
        full_shape: tuple[int, int],
        patch_size: int,
    ) -> np.ndarray:
        """Reconstruct a full-size prediction raster from classified patches.

        Parameters
        ----------
        predictions : list[np.ndarray]
            List of 2-D arrays, each ``(patch_size, patch_size)``, containing
            integer class codes.
        offsets : list[tuple[int, int]]
            Corresponding ``(row_offset, col_offset)`` for each prediction.
        full_shape : tuple[int, int]
            ``(H, W)`` of the original raster.
        patch_size : int
            Spatial size of each patch.

        Returns
        -------
        np.ndarray
            Integer class map of shape ``(H, W)``.
        """
        H, W = full_shape
        class_map = np.zeros((H, W), dtype=np.int32)

        for pred, (row, col) in zip(predictions, offsets):
            # Crop prediction to valid region (handles edge padding)
            h_valid = min(patch_size, H - row)
            w_valid = min(patch_size, W - col)
            class_map[row : row + h_valid, col : col + w_valid] = pred[:h_valid, :w_valid]

        return class_map

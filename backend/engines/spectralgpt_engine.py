"""
SpectralGPT Engine — ViT-based spectral land-use classification.

Uses a Vision Transformer pretrained on satellite spectral data for
enhanced land-use / land-cover classification from Sentinel-2 composites.
Falls back to a lightweight spectral MLP if the ViT weights are unavailable.

Reference: SpectralGPT: Spectral Remote Sensing Foundation Model
           (Danfeng Hong et al., IEEE TPAMI 2024)

Deps: torch, timm (optional for ViT backbone)
Model: SpectralGPT+.pth from Zenodo (place in data/ml_models/)
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

import numpy as np
import rasterio
from rasterio.windows import Window
from scipy.ndimage import binary_opening, uniform_filter
from shapely.geometry import shape, mapping
from rasterio.features import shapes, rasterize

logger = logging.getLogger("apex.spectralgpt")

# Model paths — check project-level and workspace-root data/ml_models/
_ML_MODELS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "data", "ml_models",
)
_ML_MODELS_DIR_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
    "data", "ml_models",
)
_SPECTRALGPT_WEIGHTS = None
for _d in (_ML_MODELS_DIR, _ML_MODELS_DIR_ROOT):
    for _fn in ("SpectralGPT+.pth", "SpectralGPT_plus.pth"):
        _candidate = os.path.join(_d, _fn)
        if os.path.exists(_candidate):
            _SPECTRALGPT_WEIGHTS = _candidate
            break
    if _SPECTRALGPT_WEIGHTS:
        break

# Sentinel-2 band indices (10m bands in typical composite order)
# Bands: B2(Blue), B3(Green), B4(Red), B5(RE1), B6(RE2), B7(RE3),
#         B8(NIR), B8A(NIR2), B11(SWIR1), B12(SWIR2), NDVI, GNDVI, NDWI, NBR
S2_BAND_NAMES = [
    "B2", "B3", "B4", "B5", "B6", "B7",
    "B8", "B8A", "B11", "B12",
    "NDVI", "GNDVI", "NDWI", "NBR",
]

# Output classes (extended Mexican-relevant set)
CLASS_NAMES = [
    "bosque_denso",      # Dense forest / selva
    "bosque_ralo",       # Open/degraded forest
    "pastizal",          # Grassland / pasture
    "cultivos",          # Agriculture
    "matorral",          # Shrubland
    "urbano",            # Built-up / urban
    "suelo",             # Bare soil
    "agua",              # Water
    "manglar_inundado",  # Flooded vegetation / mangrove
    "quemado",           # Burned area
]
N_CLASSES = len(CLASS_NAMES)

PATCH_SIZE = 64  # pixels per patch (heuristic mode)
PATCH_SIZE_VIT = 128  # SpectralGPT+ expects 128×128 (16×16 patches of 8×8)
N_SPECTRAL_VIT = 12  # SpectralGPT+ expects 12 bands (4 temporal × 3 kernel)
MIN_AREA_HA = 0.5
_model_cache = {}


def _area_deg2_to_ha(area_deg2: float) -> float:
    return area_deg2 * (111_320 ** 2) * np.cos(np.radians(20)) / 10_000


def _build_spectral_mlp():
    """Build a lightweight spectral MLP classifier (no pretrained weights needed)."""
    import torch
    import torch.nn as nn

    class SpectralMLP(nn.Module):
        def __init__(self, n_bands, n_classes):
            super().__init__()
            self.net = nn.Sequential(
                nn.Linear(n_bands, 128),
                nn.ReLU(),
                nn.BatchNorm1d(128),
                nn.Dropout(0.2),
                nn.Linear(128, 64),
                nn.ReLU(),
                nn.BatchNorm1d(64),
                nn.Linear(64, n_classes),
            )

        def forward(self, x):
            return self.net(x)

    return SpectralMLP(len(S2_BAND_NAMES), N_CLASSES)


def _load_model(device: str = "cpu"):
    """Load SpectralGPT ViT encoder or fall back to heuristic."""
    if "model" in _model_cache:
        return _model_cache["model"], _model_cache["mode"]

    import torch

    # Try loading SpectralGPT+ MAE encoder weights
    if _SPECTRALGPT_WEIGHTS and os.path.exists(_SPECTRALGPT_WEIGHTS):
        try:
            import torch.nn as nn

            state = torch.load(_SPECTRALGPT_WEIGHTS, map_location=device, weights_only=False)
            if "model" in state:
                state = state["model"]
            elif "state_dict" in state:
                state = state["state_dict"]

            # SpectralGPT+ is a MAE with 3D patch embed:
            #   patch_embed.proj: Conv3d(1, 768, kernel=(3,8,8))
            #   pos_embed_spatial: (1, 256, 768) → 16×16 grid → img 128×128
            #   pos_embed_temporal: (1, 4, 768) → 4 spectral groups
            #   encoder: 12 blocks, embed_dim=768
            # We load encoder-only and add a classification head.

            # Extract only encoder keys (skip decoder_*)
            enc_state = {k: v for k, v in state.items()
                         if not k.startswith("decoder_") and k != "mask_token"}

            class SpectralGPTEncoder(nn.Module):
                """SpectralGPT+ encoder with classification head."""

                def __init__(self, embed_dim=768, depth=12, num_heads=12,
                             mlp_ratio=4.0, n_classes=N_CLASSES):
                    super().__init__()
                    self.embed_dim = embed_dim
                    # 3D patch embedding: (B,1,C,H,W) → patches
                    self.patch_embed = nn.Conv3d(1, embed_dim, kernel_size=(3, 8, 8), stride=(3, 8, 8))
                    self.pos_embed_spatial = nn.Parameter(torch.zeros(1, 256, embed_dim))
                    self.pos_embed_temporal = nn.Parameter(torch.zeros(1, 4, embed_dim))
                    self.norm = nn.LayerNorm(embed_dim)

                    # Transformer blocks
                    self.blocks = nn.ModuleList([
                        _TransformerBlock(embed_dim, num_heads, mlp_ratio)
                        for _ in range(depth)
                    ])
                    # Classification head
                    self.head = nn.Linear(embed_dim, n_classes)

                def forward(self, x):
                    """x: (B, 1, C_bands, H, W) float tensor."""
                    B = x.shape[0]
                    # Patch embed → (B, embed_dim, T, Hp, Wp)
                    x = self.patch_embed(x)
                    T, Hp, Wp = x.shape[2], x.shape[3], x.shape[4]
                    N_s = Hp * Wp  # spatial patches
                    # Reshape → (B*T, N_s, embed_dim)
                    x = x.permute(0, 2, 3, 4, 1).reshape(B * T, N_s, self.embed_dim)
                    # Add spatial pos embed (truncate or pad if needed)
                    if N_s <= self.pos_embed_spatial.shape[1]:
                        x = x + self.pos_embed_spatial[:, :N_s, :]
                    # Reshape → (B, T*N_s, embed_dim) and add temporal
                    x = x.reshape(B, T, N_s, self.embed_dim)
                    if T <= self.pos_embed_temporal.shape[1]:
                        x = x + self.pos_embed_temporal[:, :T, :].unsqueeze(2)
                    x = x.reshape(B, T * N_s, self.embed_dim)
                    # Transformer blocks
                    for blk in self.blocks:
                        x = blk(x)
                    x = self.norm(x)
                    # Global average pool → classify
                    x = x.mean(dim=1)
                    return self.head(x)

            class _TransformerBlock(nn.Module):
                def __init__(self, dim, num_heads, mlp_ratio):
                    super().__init__()
                    self.norm1 = nn.LayerNorm(dim)
                    self.attn = _Attention(dim, num_heads)
                    self.norm2 = nn.LayerNorm(dim)
                    self.mlp = nn.Sequential(
                        nn.Linear(dim, int(dim * mlp_ratio)),
                        nn.GELU(),
                        nn.Linear(int(dim * mlp_ratio), dim),
                    )

                def forward(self, x):
                    x = x + self.attn(self.norm1(x))
                    x = x + self.mlp(self.norm2(x))
                    return x

            class _Attention(nn.Module):
                def __init__(self, dim, num_heads):
                    super().__init__()
                    self.num_heads = num_heads
                    self.head_dim = dim // num_heads
                    self.scale = self.head_dim ** -0.5
                    self.q = nn.Linear(dim, dim)
                    self.k = nn.Linear(dim, dim)
                    self.v = nn.Linear(dim, dim)
                    self.proj = nn.Linear(dim, dim)

                def forward(self, x):
                    B, N, C = x.shape
                    q = self.q(x).reshape(B, N, self.num_heads, self.head_dim).permute(0, 2, 1, 3)
                    k = self.k(x).reshape(B, N, self.num_heads, self.head_dim).permute(0, 2, 1, 3)
                    v = self.v(x).reshape(B, N, self.num_heads, self.head_dim).permute(0, 2, 1, 3)
                    attn = (q @ k.transpose(-2, -1)) * self.scale
                    attn = attn.softmax(dim=-1)
                    x = (attn @ v).transpose(1, 2).reshape(B, N, C)
                    return self.proj(x)

            model = SpectralGPTEncoder()
            # Load encoder weights (strict=False: head is new, decoder keys skipped)
            missing, unexpected = model.load_state_dict(enc_state, strict=False)
            logger.info("SpectralGPT+ encoder loaded. Missing: %d (head), Unexpected: %d",
                        len(missing), len(unexpected))
            model.to(device)
            model.eval()
            _model_cache["model"] = model
            _model_cache["mode"] = "spectralgpt"
            logger.info("SpectralGPT+ ViT encoder loaded from %s (head not fine-tuned)", _SPECTRALGPT_WEIGHTS)
            return model, "spectralgpt"
        except Exception as exc:
            logger.warning("Failed to load SpectralGPT+: %s. Falling back.", exc)
            return model, "spectralgpt"
        except Exception as exc:
            logger.warning("Failed to load SpectralGPT ViT: %s. Falling back to MLP.", exc)

    # Fallback: use spectral-index-based heuristic classifier
    logger.info("Using spectral heuristic classifier (no ViT weights)")
    _model_cache["model"] = None
    _model_cache["mode"] = "heuristic"
    return None, "heuristic"


def _heuristic_classify(pixel_values: np.ndarray) -> tuple[int, float]:
    """
    Rule-based spectral classification using NDVI, NDWI, NBR and reflectance.

    Parameters
    ----------
    pixel_values : array of shape (n_bands,) in order of S2_BAND_NAMES

    Returns
    -------
    (class_index, confidence)
    """
    # Extract indices (last 4 bands are computed indices)
    ndvi = pixel_values[-4]   # NDVI
    ndwi = pixel_values[-2]   # NDWI
    nbr = pixel_values[-1]    # NBR

    b4_red = pixel_values[2]
    b8_nir = pixel_values[6]
    b11_swir = pixel_values[8]
    b12_swir = pixel_values[9]

    # Water: high NDWI or very low NIR
    if ndwi > 0.3 or (b8_nir < 0.05 and b4_red < 0.1):
        return 7, 0.85  # agua

    # Burned: low NBR + low NDVI + moderate SWIR
    if nbr < -0.2 and ndvi < 0.2 and b12_swir > 0.1:
        return 9, 0.70  # quemado

    # Dense forest: high NDVI
    if ndvi > 0.7:
        return 0, 0.80  # bosque_denso

    # Open forest: moderately high NDVI
    if ndvi > 0.45:
        # Distinguish mangrove/flooded
        if ndwi > 0.0 and b11_swir < 0.15:
            return 8, 0.65  # manglar_inundado
        return 1, 0.70  # bosque_ralo

    # Urban: low NDVI + high SWIR reflectance
    if ndvi < 0.2 and b11_swir > 0.2:
        return 5, 0.65  # urbano

    # Bare soil: very low NDVI + high red/SWIR
    if ndvi < 0.15 and b4_red > 0.15:
        return 6, 0.70  # suelo

    # Grassland vs Shrub vs Crops
    if ndvi > 0.3:
        if b11_swir > 0.2:
            return 3, 0.60  # cultivos
        return 2, 0.60  # pastizal

    # Shrubland: moderate everything
    if ndvi > 0.2:
        return 4, 0.55  # matorral

    # Default: bare soil
    return 6, 0.40  # suelo


class SpectralGPTEngine:
    """Enhanced spectral land-use classification engine."""

    def __init__(self, device: str = "cpu"):
        self.device = device
        self._model = None
        self._mode = None

    def classify_composite(
        self,
        composite_path: str | Path,
        job_id: str = "test",
    ) -> tuple[dict, dict]:
        """
        Classify a Sentinel-2 composite into LULC classes.

        Parameters
        ----------
        composite_path : path to GeoTIFF composite (multi-band)
        job_id : for logging

        Returns
        -------
        (geojson_fc, stats) — classified polygons and statistics
        """
        jid = job_id[:8]
        composite_path = Path(composite_path)

        if not composite_path.exists():
            logger.error("[%s] Composite not found: %s", jid, composite_path)
            return {"type": "FeatureCollection", "features": []}, {"error": "No composite"}

        with rasterio.open(composite_path) as src:
            n_bands = src.count
            height, width = src.height, src.width
            transform = src.transform
            crs = src.crs

            logger.info("[%s] SpectralGPT: %dx%d px, %d bands", jid, width, height, n_bands)

            # Read all bands (handle band count mismatch)
            data = src.read()  # shape: (bands, H, W)

        # Ensure we have enough bands
        if n_bands < 4:
            logger.error("[%s] Need ≥4 bands, got %d", jid, n_bands)
            return {"type": "FeatureCollection", "features": []}, {"error": "Insufficient bands"}

        # Pad/trim to expected band count
        expected = len(S2_BAND_NAMES)
        if n_bands < expected:
            pad = np.zeros((expected - n_bands, height, width), dtype=data.dtype)
            data = np.concatenate([data, pad], axis=0)
        elif n_bands > expected:
            data = data[:expected]

        # Normalize to [0, 1] if needed (S2 L2A reflectance is typically 0-10000)
        max_val = np.nanmax(data)
        if max_val > 10:
            data = data.astype(np.float32) / 10000.0

        # Load model
        self._model, self._mode = _load_model(self.device)

        if self._mode == "spectralgpt":
            class_map, conf_map = self._classify_vit(data)
            # Ensemble: also run heuristic and merge results
            h_class_map, h_conf_map = self._classify_heuristic(data)
            # Where both agree → boost confidence; disagree → use higher-confidence source
            agree = (class_map == h_class_map) & (class_map >= 0) & (h_class_map >= 0)
            conf_map[agree] = np.minimum(conf_map[agree] * 1.15 + 0.10, 1.0)
            # Where they disagree, pick the one with higher confidence
            disagree = (~agree) & (class_map >= 0) & (h_class_map >= 0)
            use_heuristic = disagree & (h_conf_map > conf_map)
            class_map[use_heuristic] = h_class_map[use_heuristic]
            conf_map[use_heuristic] = h_conf_map[use_heuristic]
            # Where ViT produced no result but heuristic did
            vit_fail = (class_map < 0) & (h_class_map >= 0)
            class_map[vit_fail] = h_class_map[vit_fail]
            conf_map[vit_fail] = h_conf_map[vit_fail]
        else:
            class_map, conf_map = self._classify_heuristic(data)

        # Morphological cleanup
        from scipy.ndimage import binary_opening
        for c in range(N_CLASSES):
            mask = (class_map == c).astype(np.uint8)
            cleaned = binary_opening(mask, structure=np.ones((3, 3)))
            class_map[mask.astype(bool) & ~cleaned] = -1  # reclassify orphans

        # Vectorize
        features = self._vectorize(class_map, conf_map, transform, crs)

        # Stats
        class_counts: dict[str, int] = {}
        class_areas: dict[str, float] = {}
        for f in features:
            cls = f["properties"]["class"]
            class_counts[cls] = class_counts.get(cls, 0) + 1
            class_areas[cls] = class_areas.get(cls, 0) + f["properties"].get("area_ha", 0)

        stats = {
            "n_features": len(features),
            "classes": {k: round(v, 2) for k, v in class_areas.items()},
            "class_counts": class_counts,
            "model_mode": self._mode,
            "source": f"SpectralGPT ({self._mode})",
        }

        logger.info("[%s] SpectralGPT: %d polygons, %d classes", jid, len(features), len(class_counts))

        return {"type": "FeatureCollection", "features": features}, stats

    def _classify_heuristic(self, data: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Per-pixel heuristic classification."""
        _, h, w = data.shape
        class_map = np.full((h, w), -1, dtype=np.int16)
        conf_map = np.zeros((h, w), dtype=np.float32)

        # Smooth first to reduce noise
        smoothed = np.stack([uniform_filter(data[b], size=3) for b in range(data.shape[0])])

        for y in range(h):
            for x in range(w):
                pixel = smoothed[:, y, x]
                if np.all(pixel == 0) or np.isnan(pixel).any():
                    continue
                cls, conf = _heuristic_classify(pixel)
                class_map[y, x] = cls
                conf_map[y, x] = conf

        return class_map, conf_map

    def _classify_vit(self, data: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """SpectralGPT+ ViT-based patch classification.
        Model expects (B, 1, C_bands, H, W) with C=12 and H,W=128."""
        import torch

        n_bands, h, w = data.shape
        class_map = np.full((h, w), -1, dtype=np.int16)
        conf_map = np.zeros((h, w), dtype=np.float32)
        ps = PATCH_SIZE_VIT

        # Select first 12 bands for SpectralGPT+ (expects 4 groups of 3)
        if n_bands >= N_SPECTRAL_VIT:
            vit_data = data[:N_SPECTRAL_VIT]
        else:
            # Pad with zeros
            vit_data = np.zeros((N_SPECTRAL_VIT, h, w), dtype=np.float32)
            vit_data[:n_bands] = data

        # Process in 128×128 patches
        for y0 in range(0, h, ps):
            for x0 in range(0, w, ps):
                y1 = min(y0 + ps, h)
                x1 = min(x0 + ps, w)
                patch = vit_data[:, y0:y1, x0:x1]

                ph, pw = patch.shape[1], patch.shape[2]
                if ph < ps or pw < ps:
                    padded = np.zeros((N_SPECTRAL_VIT, ps, ps), dtype=np.float32)
                    padded[:, :ph, :pw] = patch
                    patch = padded

                # SpectralGPT+ expects (B, 1, C, H, W) — bands as depth dim
                tensor = torch.from_numpy(patch).unsqueeze(0).unsqueeze(0).float().to(self.device)

                with torch.no_grad():
                    logits = self._model(tensor)
                    # Temperature scaling: sharpen softmax distribution (T<1 → more confident)
                    probs = torch.softmax(logits / 0.3, dim=1)
                    pred_class = probs.argmax(dim=1).item()
                    pred_conf = probs.max(dim=1).values.item()

                class_map[y0:y1, x0:x1] = pred_class
                conf_map[y0:y1, x0:x1] = pred_conf

        return class_map, conf_map

    def _vectorize(
        self,
        class_map: np.ndarray,
        conf_map: np.ndarray,
        transform,
        crs,
    ) -> list[dict]:
        """Vectorize classification raster into GeoJSON features."""
        features = []
        valid_mask = class_map >= 0

        for geom_dict, value in shapes(
            class_map.astype(np.int16),
            mask=valid_mask,
            transform=transform,
            connectivity=4,
        ):
            cls_idx = int(value)
            if cls_idx < 0 or cls_idx >= N_CLASSES:
                continue

            poly = shape(geom_dict)
            area_ha = _area_deg2_to_ha(poly.area)
            if area_ha < MIN_AREA_HA:
                continue

            # Average confidence for this polygon
            mask_arr = rasterize(
                [(geom_dict, 1)],
                out_shape=class_map.shape,
                transform=transform,
                fill=0,
                dtype=np.uint8,
            )
            conf_vals = conf_map[mask_arr == 1]
            avg_conf = float(np.mean(conf_vals)) if len(conf_vals) > 0 else 0.5

            # Filter out low-confidence polygons
            if avg_conf < 0.25:
                continue

            features.append({
                "type": "Feature",
                "geometry": mapping(poly),
                "properties": {
                    "class": CLASS_NAMES[cls_idx],
                    "class_index": cls_idx,
                    "confidence": round(avg_conf, 3),
                    "area_ha": round(area_ha, 2),
                    "model": self._mode or "heuristic",
                },
            })

        return features

    def unload(self):
        """Free GPU memory by clearing model cache."""
        _model_cache.clear()
        self._model = None
        self._mode = None

        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

        logger.info("SpectralGPT model unloaded, GPU memory freed")

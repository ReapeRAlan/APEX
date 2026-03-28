"""
SAR Change Detection Engine
Log-ratio change detection on Sentinel-1 VV/VH composites.
Fuses SAR detections with optical (Dynamic World / Hansen) results
for cloud-robust deforestation monitoring.
"""

import warnings
warnings.filterwarnings("ignore")

import numpy as np
import rasterio
from rasterio.features import shapes, rasterize
from scipy.ndimage import binary_opening, binary_closing
from shapely.geometry import shape, mapping
from shapely.strtree import STRtree
from pathlib import Path

MIN_AREA_HA = 0.5
MAX_FEATURES = 200
CHANGE_THRESHOLD_DB = 3.0  # dB — loss exceeding 3dB indicates deforestation


def _area_deg2_to_ha(area_deg2: float) -> float:
    """Convert area in squared degrees to hectares (approximate at lat ~20)."""
    return area_deg2 * (111_320 ** 2) * np.cos(np.radians(20)) / 10_000


class SAREngine:
    """Detecta cambios forestales a partir de composites SAR bi-temporales."""

    def detect_change_sar(
        self,
        sar_t1_path: Path,
        sar_t2_path: Path,
    ) -> tuple[dict, dict]:
        """
        Log-ratio change detection between two SAR composites.

        Each composite is a 3-band GeoTIFF (from GEESARService):
          Band 1: VV_median   (float32, dB)
          Band 2: VH_median   (float32, dB)
          Band 3: VV_VH_ratio (float32, dB)

        Change metric: T2_dB - T1_dB (subtraction in dB = log-ratio in linear).
        Negative values = backscatter decrease = potential deforestation
        (loss of volume scattering from forest canopy).

        Returns (geojson_fc, stats_dict).
        """
        print(f"[SAR-Engine] Leyendo SAR t1: {sar_t1_path}")
        with rasterio.open(sar_t1_path) as src1:
            vv_t1 = src1.read(1).astype(np.float32)
            vh_t1 = src1.read(2).astype(np.float32)
            transform = src1.transform

        print(f"[SAR-Engine] Leyendo SAR t2: {sar_t2_path}")
        with rasterio.open(sar_t2_path) as src2:
            vv_t2 = src2.read(1).astype(np.float32)
            vh_t2 = src2.read(2).astype(np.float32)

        # Log-ratio change detection in dB domain
        # Values already in dB, so difference = log-ratio in linear
        # Negative change means backscatter decreased (potential deforestation)
        change_vv = vv_t2 - vv_t1
        change_vh = vh_t2 - vh_t1

        # Combined change magnitude
        change_magnitude = (np.abs(change_vv) + np.abs(change_vh)) / 2.0

        # Deforestation mask: significant backscatter LOSS exceeding threshold
        # Forest removal causes decrease in radar backscatter
        deforestation_mask = (
            (change_vv < -CHANGE_THRESHOLD_DB)
            | (change_vh < -CHANGE_THRESHOLD_DB)
        )

        # Also flag areas with high combined magnitude where at least one band drops
        deforestation_mask = deforestation_mask | (
            (change_magnitude > CHANGE_THRESHOLD_DB)
            & ((change_vv < 0) | (change_vh < 0))
        )

        print(f"[SAR-Engine] Pixeles con cambio detectado: {np.sum(deforestation_mask)}")

        # Morphological cleanup
        struct = np.ones((3, 3), dtype=bool)
        clean = binary_opening(deforestation_mask, structure=struct)
        clean = binary_closing(clean, structure=struct).astype(np.uint8)

        # Vectorize
        features = []
        for geom, val in shapes(clean, transform=transform):
            if val != 1:
                continue
            poly = shape(geom)
            area_ha = _area_deg2_to_ha(poly.area)
            if area_ha < MIN_AREA_HA:
                continue

            # Extract mean change values within polygon
            poly_rast = rasterize(
                [(geom, 1)],
                out_shape=vv_t1.shape,
                transform=transform,
                dtype=np.uint8,
            )
            pmask = poly_rast == 1

            mean_change_vv = float(np.nanmean(change_vv[pmask]))
            mean_change_vh = float(np.nanmean(change_vh[pmask]))
            mean_magnitude = float(np.nanmean(change_magnitude[pmask]))

            # Confidence based on change magnitude
            # 3dB -> ~0.3, 6dB -> ~0.6, >=10dB -> 1.0
            confidence = min(mean_magnitude / 10.0, 1.0)
            confidence = round(max(confidence, 0.1), 3)

            features.append({
                "type": "Feature",
                "geometry": mapping(poly),
                "properties": {
                    "change_db_vv": round(mean_change_vv, 2),
                    "change_db_vh": round(mean_change_vh, 2),
                    "area_ha": round(area_ha, 2),
                    "confidence": confidence,
                    "type": "sar_change",
                },
            })

        # Sort by area descending, limit to top N
        features.sort(key=lambda f: f["properties"]["area_ha"], reverse=True)
        features = features[:MAX_FEATURES]

        total_change_ha = sum(f["properties"]["area_ha"] for f in features)
        avg_confidence = (
            round(float(np.mean([f["properties"]["confidence"] for f in features])), 3)
            if features else 0
        )

        geojson = {"type": "FeatureCollection", "features": features}
        stats = {
            "total_change_ha": round(total_change_ha, 1),
            "n_features": len(features),
            "avg_confidence": avg_confidence,
            "threshold_db": CHANGE_THRESHOLD_DB,
            "source": "Sentinel-1 SAR Change Detection",
        }

        print(
            f"[SAR-Engine] {len(features)} poligonos, "
            f"cambio total={total_change_ha:.1f}ha, conf={avg_confidence}"
        )
        return geojson, stats

    def fuse_optical_sar(
        self,
        dw_features: list,
        sar_features: list,
    ) -> list:
        """
        Fuse optical (Dynamic World / Hansen) detections with SAR change detections.

        Fusion strategy:
        - Both agree (spatial overlap): boost confidence = 0.6 * dw + 0.4 * sar
        - Only optical detected: keep as is
        - Only SAR detected: add as "preliminary_alert" with lower confidence

        Parameters
        ----------
        dw_features  : list  optical deforestation features (Dynamic World / Hansen)
        sar_features : list  SAR change features from detect_change_sar()

        Returns
        -------
        Fused feature list.
        """
        print(
            f"[SAR-Engine] Fusionando {len(dw_features)} optical + "
            f"{len(sar_features)} SAR features"
        )

        if not dw_features and not sar_features:
            print("[SAR-Engine] Sin features para fusionar")
            return []

        # Build shapely geometries for SAR features
        sar_geoms = []
        sar_shapes = []
        for feat in sar_features:
            try:
                geom = shape(feat["geometry"])
                if geom.is_valid:
                    sar_geoms.append(geom)
                    sar_shapes.append(feat)
            except Exception:
                continue

        # Spatial index for fast overlap queries
        sar_tree = STRtree(sar_geoms) if sar_geoms else None

        # Track which SAR features were matched to an optical detection
        matched_sar_indices = set()
        fused = []

        for dw_feat in dw_features:
            try:
                dw_geom = shape(dw_feat["geometry"])
            except Exception:
                fused.append(dw_feat)
                continue

            if not dw_geom.is_valid:
                fused.append(dw_feat)
                continue

            dw_conf = dw_feat.get("properties", {}).get("confidence", 0.5)
            if isinstance(dw_conf, str):
                dw_conf = {"high": 0.9, "medium": 0.6, "low": 0.3}.get(dw_conf, 0.5)

            best_sar_conf = None
            best_sar_idx = None

            # Check spatial overlap with SAR features
            if sar_tree is not None:
                candidate_indices = sar_tree.query(dw_geom)
                for idx in candidate_indices:
                    sar_geom = sar_geoms[idx]
                    if dw_geom.intersects(sar_geom):
                        sar_conf = sar_shapes[idx].get("properties", {}).get(
                            "confidence", 0.3
                        )
                        if best_sar_conf is None or sar_conf > best_sar_conf:
                            best_sar_conf = sar_conf
                            best_sar_idx = idx

            if best_sar_conf is not None:
                # Both optical and SAR agree — boost confidence
                fused_conf = round(0.6 * dw_conf + 0.4 * best_sar_conf, 3)
                fused_feat = {
                    "type": "Feature",
                    "geometry": dw_feat["geometry"],
                    "properties": {
                        **dw_feat.get("properties", {}),
                        "confidence": fused_conf,
                        "sar_confirmed": True,
                        "sar_change_db_vv": sar_shapes[best_sar_idx]["properties"].get(
                            "change_db_vv"
                        ),
                        "sar_change_db_vh": sar_shapes[best_sar_idx]["properties"].get(
                            "change_db_vh"
                        ),
                    },
                }
                fused.append(fused_feat)
                matched_sar_indices.add(best_sar_idx)
            else:
                # Only optical detected — keep as is
                fused_feat = {
                    "type": "Feature",
                    "geometry": dw_feat["geometry"],
                    "properties": {
                        **dw_feat.get("properties", {}),
                        "sar_confirmed": False,
                    },
                }
                fused.append(fused_feat)

        # Add SAR-only detections as preliminary alerts (cloud-obscured in optical)
        for idx, sar_feat in enumerate(sar_shapes):
            if idx in matched_sar_indices:
                continue
            sar_conf = sar_feat.get("properties", {}).get("confidence", 0.3)
            preliminary_feat = {
                "type": "Feature",
                "geometry": sar_feat["geometry"],
                "properties": {
                    **sar_feat.get("properties", {}),
                    "confidence": round(sar_conf * 0.7, 3),
                    "type": "preliminary_alert",
                    "sar_confirmed": True,
                    "optical_confirmed": False,
                },
            }
            fused.append(preliminary_feat)

        # Sort by confidence descending, limit
        fused.sort(key=lambda f: f["properties"].get("confidence", 0), reverse=True)
        fused = fused[:MAX_FEATURES]

        n_confirmed = sum(
            1 for f in fused
            if f["properties"].get("sar_confirmed", False)
            and f["properties"].get("type") != "preliminary_alert"
        )
        n_preliminary = sum(
            1 for f in fused
            if f["properties"].get("type") == "preliminary_alert"
        )

        print(
            f"[SAR-Engine] Fusion completa: {len(fused)} features "
            f"({n_confirmed} confirmados SAR, {n_preliminary} alertas preliminares)"
        )
        return fused

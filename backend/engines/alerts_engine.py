"""
Deforestation Alerts — Analysis Engine
Vectoriza alertas GLAD / RADD, fusiona con deduplicacion espacial.
"""

import warnings
warnings.filterwarnings("ignore")

from datetime import date, timedelta  # noqa: E402
from pathlib import Path  # noqa: E402

import numpy as np  # noqa: E402
import rasterio  # noqa: E402
from rasterio.features import shapes, rasterize  # noqa: E402
from scipy.ndimage import binary_opening, binary_closing  # noqa: E402
from shapely.geometry import shape, mapping  # noqa: E402

MIN_AREA_HA = 0.25   # alerts can be smaller than Hansen loss polygons
MAX_FEATURES = 300


def _area_deg2_to_ha(area_deg2: float) -> float:
    """Convert area in squared degrees to hectares (approximate at lat ~20)."""
    return area_deg2 * (111_320 ** 2) * np.cos(np.radians(20)) / 10_000


class AlertsEngine:
    """Analiza alertas de deforestacion a partir de rasters GLAD / RADD."""

    # ==================================================================
    # 1. GLAD alerts
    # ==================================================================
    def process_glad_alerts(
        self,
        raster_path: Path,
        min_confidence: int = 2,
    ) -> tuple[dict, dict]:
        """
        Vectoriza alertas GLAD desde un raster de 2 bandas.

        Raster bands (from GEEAlertsService.get_glad_alerts):
          Band 1: alertBinary  (uint16, 0/1/2 — 2=confirmed, 1=probable)
          Band 2: alertDate    (uint16, days since 2018-01-01)

        Parameters
        ----------
        raster_path    : Path  raster downloaded by GEEAlertsService
        min_confidence : int   minimum alertBinary value to keep (2=confirmed only,
                               1=confirmed+probable)

        Returns
        -------
        (geojson_fc, stats_dict)
        """
        print(f"[GLAD-Engine] Procesando alertas GLAD: {raster_path}")

        with rasterio.open(raster_path) as src:
            alert_binary = src.read(1).astype(np.uint16)
            alert_date = src.read(2).astype(np.uint16)
            transform = src.transform

        # Binary mask: pixels at or above minimum confidence
        alert_mask = alert_binary >= min_confidence

        # Morphological cleanup
        struct = np.ones((3, 3), dtype=bool)
        clean = binary_opening(alert_mask, structure=struct)
        clean = binary_closing(clean, structure=struct).astype(np.uint8)

        # Reference date for GLAD-S2
        glad_ref = date(2018, 1, 1)

        features = []
        for geom, val in shapes(clean, transform=transform):
            if val != 1:
                continue
            poly = shape(geom)
            area_ha = _area_deg2_to_ha(poly.area)
            if area_ha < MIN_AREA_HA:
                continue

            # Extract dominant alert date within polygon
            poly_rast = rasterize(
                [(geom, 1)],
                out_shape=alert_binary.shape,
                transform=transform,
                dtype=np.uint8,
            )
            pmask = poly_rast == 1

            date_vals = alert_date[pmask]
            date_vals = date_vals[date_vals > 0]
            if len(date_vals) > 0:
                dominant_offset = int(np.median(date_vals))
                alert_date_str = (glad_ref + timedelta(days=dominant_offset)).isoformat()
            else:
                alert_date_str = "unknown"

            # Confidence label from dominant alertBinary value
            conf_vals = alert_binary[pmask]
            conf_vals = conf_vals[conf_vals > 0]
            if len(conf_vals) > 0:
                dominant_conf = int(np.argmax(np.bincount(conf_vals.astype(int))))
                confidence_label = "confirmed" if dominant_conf >= 2 else "probable"
            else:
                confidence_label = "probable"

            features.append({
                "type": "Feature",
                "geometry": mapping(poly),
                "properties": {
                    "alert_type": "glad",
                    "alert_date": alert_date_str,
                    "confidence": confidence_label,
                    "area_ha": round(area_ha, 3),
                },
            })

        # Sort by area descending, limit to top N
        features.sort(key=lambda f: f["properties"]["area_ha"], reverse=True)
        features = features[:MAX_FEATURES]

        total_area = sum(f["properties"]["area_ha"] for f in features)
        confirmed_count = sum(
            1 for f in features if f["properties"]["confidence"] == "confirmed"
        )

        geojson = {"type": "FeatureCollection", "features": features}
        stats = {
            "glad_count": len(features),
            "confirmed_count": confirmed_count,
            "probable_count": len(features) - confirmed_count,
            "total_area_ha": round(total_area, 2),
            "source": "GLAD deforestation alerts",
        }

        print(
            f"[GLAD-Engine] {len(features)} alertas GLAD, "
            f"area total={total_area:.2f}ha, confirmadas={confirmed_count}"
        )
        return geojson, stats

    # ==================================================================
    # 2. RADD alerts
    # ==================================================================
    def process_radd_alerts(
        self,
        raster_path: Path,
    ) -> tuple[dict, dict]:
        """
        Vectoriza alertas RADD desde un raster de 3 bandas.

        Raster bands (from GEEAlertsService.get_radd_alerts):
          Band 1: Alert      (uint16, 0/1)
          Band 2: Date       (uint16, days since 2018-12-31)
          Band 3: Confidence (uint16, 1=nominal, 2=high)

        Returns
        -------
        (geojson_fc, stats_dict)
        """
        print(f"[RADD-Engine] Procesando alertas RADD: {raster_path}")

        with rasterio.open(raster_path) as src:
            alert = src.read(1).astype(np.uint16)
            alert_date_band = src.read(2).astype(np.uint16)
            confidence_band = src.read(3).astype(np.uint16)
            transform = src.transform

        # Binary mask
        alert_mask = alert > 0

        # Morphological cleanup
        struct = np.ones((3, 3), dtype=bool)
        clean = binary_opening(alert_mask, structure=struct)
        clean = binary_closing(clean, structure=struct).astype(np.uint8)

        # Reference date for RADD
        radd_ref = date(2018, 12, 31)

        features = []
        for geom, val in shapes(clean, transform=transform):
            if val != 1:
                continue
            poly = shape(geom)
            area_ha = _area_deg2_to_ha(poly.area)
            if area_ha < MIN_AREA_HA:
                continue

            # Extract dominant date and confidence within polygon
            poly_rast = rasterize(
                [(geom, 1)],
                out_shape=alert.shape,
                transform=transform,
                dtype=np.uint8,
            )
            pmask = poly_rast == 1

            # Date
            date_vals = alert_date_band[pmask]
            date_vals = date_vals[date_vals > 0]
            if len(date_vals) > 0:
                dominant_offset = int(np.median(date_vals))
                alert_date_str = (radd_ref + timedelta(days=dominant_offset)).isoformat()
            else:
                alert_date_str = "unknown"

            # Confidence
            conf_vals = confidence_band[pmask]
            conf_vals = conf_vals[conf_vals > 0]
            if len(conf_vals) > 0:
                dominant_conf = int(np.argmax(np.bincount(conf_vals.astype(int))))
                confidence_label = "high" if dominant_conf >= 2 else "nominal"
            else:
                confidence_label = "nominal"

            features.append({
                "type": "Feature",
                "geometry": mapping(poly),
                "properties": {
                    "alert_type": "radd",
                    "alert_date": alert_date_str,
                    "confidence": confidence_label,
                    "area_ha": round(area_ha, 3),
                },
            })

        # Sort by area descending, limit to top N
        features.sort(key=lambda f: f["properties"]["area_ha"], reverse=True)
        features = features[:MAX_FEATURES]

        total_area = sum(f["properties"]["area_ha"] for f in features)
        high_count = sum(
            1 for f in features if f["properties"]["confidence"] == "high"
        )

        geojson = {"type": "FeatureCollection", "features": features}
        stats = {
            "radd_count": len(features),
            "high_confidence_count": high_count,
            "nominal_confidence_count": len(features) - high_count,
            "total_area_ha": round(total_area, 2),
            "source": "RADD deforestation alerts (Sentinel-1 SAR)",
        }

        print(
            f"[RADD-Engine] {len(features)} alertas RADD, "
            f"area total={total_area:.2f}ha, alta_confianza={high_count}"
        )
        return geojson, stats

    # ==================================================================
    # 3. Merge GLAD + RADD (spatial deduplication)
    # ==================================================================
    def merge_alerts(
        self,
        glad_features: list,
        radd_features: list,
    ) -> tuple[dict, dict]:
        """
        Combine GLAD and RADD alert features with spatial deduplication.

        If a GLAD alert overlaps >50% with a RADD alert, keep only the
        higher-confidence one.  Confidence ranking:
            confirmed > high > probable > nominal

        Parameters
        ----------
        glad_features : list  features from process_glad_alerts result
        radd_features : list  features from process_radd_alerts result

        Returns
        -------
        (merged geojson_fc, merged stats_dict)
        """
        print(
            f"[Alerts-Merge] Fusionando {len(glad_features)} GLAD + "
            f"{len(radd_features)} RADD alertas..."
        )

        CONFIDENCE_RANK = {
            "confirmed": 4,
            "high": 3,
            "probable": 2,
            "nominal": 1,
        }

        def _conf_rank(feature):
            return CONFIDENCE_RANK.get(
                feature["properties"].get("confidence", "nominal"), 0
            )

        # Build shapely geometries for all features
        glad_shapes = []
        for f in glad_features:
            try:
                glad_shapes.append((shape(f["geometry"]), f))
            except Exception:
                continue

        radd_shapes = []
        for f in radd_features:
            try:
                radd_shapes.append((shape(f["geometry"]), f))
            except Exception:
                continue

        # Track which RADD features are consumed by deduplication
        radd_used = set()
        merged = []

        for g_geom, g_feat in glad_shapes:
            duplicate_found = False
            for r_idx, (r_geom, r_feat) in enumerate(radd_shapes):
                if r_idx in radd_used:
                    continue
                if not g_geom.intersects(r_geom):
                    continue

                try:
                    inter = g_geom.intersection(r_geom)
                    overlap_ratio = inter.area / g_geom.area if g_geom.area > 0 else 0
                except Exception:
                    overlap_ratio = 0

                if overlap_ratio > 0.50:
                    # Keep the higher confidence feature
                    if _conf_rank(r_feat) > _conf_rank(g_feat):
                        merged.append(r_feat)
                    else:
                        merged.append(g_feat)
                    radd_used.add(r_idx)
                    duplicate_found = True
                    break

            if not duplicate_found:
                merged.append(g_feat)

        # Add remaining (non-duplicate) RADD features
        for r_idx, (r_geom, r_feat) in enumerate(radd_shapes):
            if r_idx not in radd_used:
                merged.append(r_feat)

        # Sort by area descending, cap at MAX_FEATURES
        merged.sort(key=lambda f: f["properties"]["area_ha"], reverse=True)
        merged = merged[:MAX_FEATURES]

        # Compute stats
        glad_count = sum(1 for f in merged if f["properties"]["alert_type"] == "glad")
        radd_count = sum(1 for f in merged if f["properties"]["alert_type"] == "radd")
        confirmed_count = sum(
            1 for f in merged
            if f["properties"].get("confidence") in ("confirmed", "high")
        )
        total_area = sum(f["properties"]["area_ha"] for f in merged)

        geojson = {"type": "FeatureCollection", "features": merged}
        stats = {
            "total_alerts": len(merged),
            "glad_count": glad_count,
            "radd_count": radd_count,
            "confirmed_count": confirmed_count,
            "total_area_ha": round(total_area, 2),
            "duplicates_removed": (
                len(glad_features) + len(radd_features) - len(merged)
            ),
            "source": "GLAD + RADD merged alerts",
        }

        print(
            f"[Alerts-Merge] Resultado: {len(merged)} alertas fusionadas "
            f"(GLAD={glad_count}, RADD={radd_count}, confirmadas={confirmed_count}, "
            f"area={total_area:.2f}ha, duplicados_removidos={stats['duplicates_removed']})"
        )
        return geojson, stats

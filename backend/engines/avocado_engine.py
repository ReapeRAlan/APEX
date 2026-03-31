"""
AVOCADO Engine — Anomalous Vegetation Change Detection and Outlier Detection.

Detects NDVI anomalies by comparing current values against a 10-year
historical baseline.  Pixels below the 5th percentile of their seasonal
distribution are flagged as anomalous.

Reference: "AVOCADO: Anomalous Vegetation Change Detection for
Operational forest change monitoring" (ESA Phi-Lab, 2023).
"""
from __future__ import annotations

import logging
from typing import Callable, Optional

from ..services.gee_avocado import GEEAvocadoService

logger = logging.getLogger("apex.avocado_engine")


class AvocadoEngine:
    """Vegetation anomaly detection using NDVI percentile ranking."""

    def __init__(self):
        self._service = GEEAvocadoService()

    def run(
        self,
        aoi_geojson: dict,
        end_date: str,
        job_id: str = "test",
        on_progress: Optional[Callable[[str], None]] = None,
    ) -> tuple[dict, dict]:
        """
        Run AVOCADO anomaly detection.

        Parameters
        ----------
        aoi_geojson : GeoJSON geometry / Feature / FeatureCollection
        end_date : ISO date string — analysis reference date
        job_id : for logging
        on_progress : optional callback for progress messages

        Returns
        -------
        (geojson_fc, stats)
        """
        jid = job_id[:8]
        logger.info("[%s] Running AVOCADO anomaly detection...", jid)

        geojson, stats = self._service.detect_anomalies(
            aoi_geojson=aoi_geojson,
            analysis_date=end_date,
            job_id=job_id,
            on_progress=on_progress,
        )

        logger.info(
            "[%s] AVOCADO complete: %d anomalies, %.1f ha",
            jid,
            stats.get("n_anomalies", 0),
            stats.get("total_area_ha", 0),
        )

        return geojson, stats

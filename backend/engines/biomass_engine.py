"""
Biomass Engine — Calculates carbon stock loss and CO₂ emissions
from deforestation polygons using NASA GEDI L4B biomass data.

This engine auto-runs as a post-processing step after deforestation
detection. It enriches deforestation features with biomass & CO₂ metrics
and produces aggregate statistics for reports.

Carbon calculation (IPCC Tier 1 defaults):
  carbon = AGBD (Mg/ha) × area (ha) × 0.47
  CO₂    = carbon × 3.6667  (44/12 molecular weight ratio)
"""

import logging

from ..services.gee_biomass import GEEBiomassService, CARBON_FRACTION, CO2_PER_CARBON

log = logging.getLogger("apex.biomass_engine")


class BiomassEngine:
    """Enriches deforestation features with GEDI biomass + CO₂ estimates."""

    def __init__(self):
        self._svc = GEEBiomassService()

    def enrich_deforestation(
        self,
        features: list[dict],
        job_id: str = "",
    ) -> tuple[list[dict], dict]:
        """
        Enrich deforestation features with biomass/CO₂ and return stats.

        Parameters
        ----------
        features : list[dict]
            GeoJSON Feature dicts (must have geometry + properties.area_ha).
        job_id : str
            Job identifier for logging.

        Returns
        -------
        (features, stats) where features are mutated in place and stats is
        an aggregate dict.
        """
        jid = (job_id or "")[:8]

        if not features:
            return features, {"biomass_enriched": 0}

        # Enrich features via GEE
        try:
            features = self._svc.sample_biomass_at_polygons(features, job_id)
        except Exception as e:
            log.error("[%s] Biomass enrichment failed: %s", jid, e)
            return features, {"biomass_enriched": 0, "error": str(e)[:200]}

        # Compute aggregate stats
        total_co2 = 0.0
        total_carbon = 0.0
        total_agbd_area = 0.0
        enriched_count = 0
        agbd_values = []

        for feat in features:
            props = feat.get("properties", {})
            co2 = props.get("co2_tonnes")
            carbon = props.get("carbon_tonnes")
            agbd = props.get("agbd_mg_ha")

            if co2 is not None and co2 > 0:
                total_co2 += co2
                total_carbon += (carbon or 0)
                total_agbd_area += props.get("area_ha", 0)
                enriched_count += 1
            if agbd is not None and agbd > 0:
                agbd_values.append(agbd)

        mean_agbd = round(sum(agbd_values) / len(agbd_values), 2) if agbd_values else 0

        stats = {
            "biomass_enriched": enriched_count,
            "total_features": len(features),
            "total_co2_tonnes": round(total_co2, 2),
            "total_carbon_tonnes": round(total_carbon, 2),
            "total_deforested_ha_with_biomass": round(total_agbd_area, 2),
            "mean_agbd_mg_ha": mean_agbd,
            "carbon_fraction": CARBON_FRACTION,
            "co2_per_carbon": CO2_PER_CARBON,
            "source": "NASA GEDI L4B (LARSE/GEDI/GEDI04_B_002)",
        }

        log.info(
            "[%s] Biomass: %d/%d features enriched, %.1f tCO₂ total",
            jid, enriched_count, len(features), total_co2,
        )
        return features, stats

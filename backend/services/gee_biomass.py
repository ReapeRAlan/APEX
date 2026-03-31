"""
GEE Biomass Service — Fetches NASA GEDI L4B gridded biomass data from GEE.

GEDI L4B (LARSE/GEDI/GEDI04_B_002):
  - 1 km × 1 km gridded mean Aboveground Biomass Density (AGBD)
  - Band "MU" = mean AGBD in Mg/ha
  - Band "SE" = standard error
  - Coverage: 51.6°N – 51.6°S (Mexico fully covered)
  - Temporal range: 2019-04-18 to 2021-08-04 (single composite)
"""

import ee
import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

log = logging.getLogger("apex.gee_biomass")

_GEE_TIMEOUT = 60  # seconds per getInfo call

GEDI_L4B = "LARSE/GEDI/GEDI04_B_002"

# Carbon conversion factors (IPCC defaults for tropical forest)
CARBON_FRACTION = 0.47        # biomass → carbon
CO2_PER_CARBON = 3.6667       # carbon → CO₂ (molecular weight ratio 44/12)


def _call_with_timeout(fn, timeout, label="GEE"):
    """Run a blocking GEE call in a thread with a timeout."""
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(fn)
        try:
            return future.result(timeout=timeout)
        except FuturesTimeout:
            raise TimeoutError(f"{label}: timeout after {timeout}s")


class GEEBiomassService:
    """Samples GEDI L4B biomass at arbitrary geometries via GEE."""

    def __init__(self):
        self._initialized = False

    def initialize(self):
        if self._initialized:
            return
        try:
            ee.Number(1).getInfo()
        except Exception:
            ee.Initialize()
        self._initialized = True

    # ------------------------------------------------------------------ #
    #  Public API                                                         #
    # ------------------------------------------------------------------ #

    def sample_biomass_at_polygons(
        self,
        features: list[dict],
        job_id: str = "",
    ) -> list[dict]:
        """
        Enrich GeoJSON features with GEDI L4B biomass + CO₂ estimates.

        For each feature with geometry + area_ha, adds:
          - agbd_mg_ha    : Aboveground Biomass Density (Mg/ha)
          - agbd_se       : Standard error of AGBD
          - carbon_tonnes : Estimated carbon stock lost
          - co2_tonnes    : Estimated CO₂ equivalent released
          - biomass_source: "GEDI_L4B"

        Returns the same features list, mutated in place.
        """
        self.initialize()

        if not features:
            return features

        gedi = ee.Image(GEDI_L4B)
        jid = (job_id or "")[:8]

        # Build a server-side FeatureCollection for batch reduceRegion
        ee_features = []
        valid_indices = []
        for idx, feat in enumerate(features):
            geom = feat.get("geometry")
            if not geom:
                continue
            try:
                ee_geom = ee.Geometry(geom)
                ee_feat = ee.Feature(ee_geom, {"_idx": idx})
                ee_features.append(ee_feat)
                valid_indices.append(idx)
            except Exception:
                continue

        if not ee_features:
            log.warning("[%s] No valid geometries for GEDI sampling", jid)
            return features

        ee_fc = ee.FeatureCollection(ee_features)

        # Server-side: sample GEDI MU and SE at each polygon
        def _sample(feat):
            geom = feat.geometry()
            stats = gedi.select(["MU", "SE"]).reduceRegion(
                reducer=ee.Reducer.mean(),
                geometry=geom,
                scale=1000,
                bestEffort=True,
            )
            return feat.set({
                "agbd_mu": stats.get("MU"),
                "agbd_se": stats.get("SE"),
            })

        sampled = ee_fc.map(_sample)

        # Download results
        try:
            result_list = _call_with_timeout(
                lambda: sampled.getInfo(),
                timeout=_GEE_TIMEOUT * 2,
                label=f"GEDI-L4B-{jid}",
            )
        except Exception as e:
            log.error("[%s] GEDI L4B batch sample failed: %s", jid, e)
            return features

        # Merge results back into original features
        sampled_feats = result_list.get("features", [])
        enriched = 0
        for sf in sampled_feats:
            props = sf.get("properties", {})
            idx = props.get("_idx")
            if idx is None or idx >= len(features):
                continue

            mu = props.get("agbd_mu")
            se = props.get("agbd_se")

            target = features[idx]
            target_props = target.setdefault("properties", {})
            area_ha = target_props.get("area_ha", 0)

            if mu is not None and mu > 0:
                target_props["agbd_mg_ha"] = round(mu, 2)
                target_props["agbd_se"] = round(se, 2) if se else None
                carbon = mu * area_ha * CARBON_FRACTION
                co2 = carbon * CO2_PER_CARBON
                target_props["carbon_tonnes"] = round(carbon, 2)
                target_props["co2_tonnes"] = round(co2, 2)
                target_props["biomass_source"] = "GEDI_L4B"
                enriched += 1
            else:
                target_props["agbd_mg_ha"] = None
                target_props["carbon_tonnes"] = None
                target_props["co2_tonnes"] = None

        log.info("[%s] GEDI L4B: enriched %d/%d features", jid, enriched, len(features))
        return features

    def get_aoi_biomass_stats(
        self,
        aoi: dict,
        job_id: str = "",
    ) -> dict:
        """
        Get aggregate biomass stats for an entire AOI polygon.

        Returns dict with:
          - mean_agbd_mg_ha: Mean AGB density across AOI
          - se_agbd: Mean standard error
          - area_with_data_pct: Approximate % of AOI with GEDI coverage
        """
        self.initialize()
        jid = (job_id or "")[:8]

        gedi = ee.Image(GEDI_L4B)
        ee_geom = ee.Geometry(aoi)

        stats = gedi.select(["MU", "SE"]).reduceRegion(
            reducer=ee.Reducer.mean().combine(
                ee.Reducer.count(), sharedInputs=True
            ),
            geometry=ee_geom,
            scale=1000,
            bestEffort=True,
        )

        try:
            info = _call_with_timeout(
                lambda: stats.getInfo(),
                timeout=_GEE_TIMEOUT,
                label=f"GEDI-AOI-{jid}",
            )
        except Exception as e:
            log.error("[%s] GEDI AOI stats failed: %s", jid, e)
            return {}

        return {
            "mean_agbd_mg_ha": round(info.get("MU_mean", 0) or 0, 2),
            "se_agbd": round(info.get("SE_mean", 0) or 0, 2),
            "pixel_count": info.get("MU_count", 0) or 0,
        }

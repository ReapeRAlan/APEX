"""
Satellite Thumbnail Service for APEX PDF Reports
=================================================
Fetches RGB satellite thumbnails from Earth Engine for each year
in the timeline analysis, to embed in PDF/Word reports with real
satellite imagery (like MACOF does).
"""

import io
import logging
import os
from typing import Dict, Optional

logger = logging.getLogger(__name__)

try:
    import ee
    HAS_EE = True
except ImportError:
    HAS_EE = False


class GEEThumbnailService:
    """Fetch satellite thumbnails for report map generation."""

    def __init__(self):
        self._initialized = False

    def _ensure_init(self):
        if self._initialized or not HAS_EE:
            return
        try:
            ee.Number(1).getInfo()
            self._initialized = True
        except Exception:
            try:
                project = os.getenv("GEE_PROJECT", "profepa-deforestation")
                ee.Initialize(project=project)
                self._initialized = True
            except Exception as exc:
                logger.warning("GEE not available for thumbnails: %s", exc)

    def fetch_yearly_thumbnails(
        self,
        aoi_geojson: dict,
        years: list[str],
        dimensions: int = 512,
    ) -> Dict[str, dict]:
        """
        Fetch RGB + NDVI satellite thumbnails for each year.

        Returns:
            {year: {'rgb': bytes, 'ndvi': bytes, 'bounds': [W, S, E, N]}}
        """
        if not HAS_EE:
            logger.warning("Earth Engine not available — skipping thumbnails")
            return {}

        self._ensure_init()
        if not self._initialized:
            return {}

        try:
            region = ee.Geometry(aoi_geojson)
            bounds = region.bounds().getInfo()["coordinates"][0]
            west = min(c[0] for c in bounds)
            south = min(c[1] for c in bounds)
            east = max(c[0] for c in bounds)
            north = max(c[1] for c in bounds)
            bounds_list = [west, south, east, north]
        except Exception as exc:
            logger.error("Failed to compute bounds: %s", exc)
            return {}

        results = {}
        for year_str in years:
            year = int(year_str)
            try:
                thumbnails = self._fetch_year(region, year, dimensions, bounds_list)
                if thumbnails:
                    results[year_str] = thumbnails
            except Exception as exc:
                logger.warning("Thumbnail for %s failed: %s", year_str, exc)

        logger.info("Fetched thumbnails for %d/%d years", len(results), len(years))
        return results

    def fetch_overview_thumbnail(
        self,
        aoi_geojson: dict,
        dimensions: int = 768,
    ) -> Optional[dict]:
        """
        Fetch a single high-res RGB thumbnail for the location overview map.
        Uses the most recent cloud-free Sentinel-2 composite.
        """
        if not HAS_EE:
            return None

        self._ensure_init()
        if not self._initialized:
            return None

        try:
            region = ee.Geometry(aoi_geojson)
            bounds = region.bounds().getInfo()["coordinates"][0]
            west = min(c[0] for c in bounds)
            south = min(c[1] for c in bounds)
            east = max(c[0] for c in bounds)
            north = max(c[1] for c in bounds)

            img = (
                ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
                .filterBounds(region)
                .filterDate("2024-01-01", "2025-12-31")
                .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 20))
                .median()
                .clip(region)
            )

            rgb_params = {
                "bands": ["B4", "B3", "B2"],
                "min": 0,
                "max": 3000,
                "dimensions": dimensions,
                "region": region,
                "format": "png",
            }

            rgb_url = img.getThumbURL(rgb_params)
            import urllib.request
            with urllib.request.urlopen(rgb_url, timeout=60) as resp:
                rgb_bytes = resp.read()

            return {
                "rgb": rgb_bytes,
                "bounds": [west, south, east, north],
            }
        except Exception as exc:
            logger.warning("Overview thumbnail failed: %s", exc)
            return None

    def _fetch_year(
        self, region, year: int, dimensions: int, bounds_list: list
    ) -> Optional[dict]:
        """Fetch RGB + NDVI thumbnails for a single year."""
        # Use dry season (Nov-Apr) for clearer imagery
        start = f"{year - 1}-11-01"
        end = f"{year}-04-30"

        img = (
            ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
            .filterBounds(region)
            .filterDate(start, end)
            .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 30))
            .median()
            .clip(region)
        )

        result = {"bounds": bounds_list}

        # RGB
        try:
            rgb_url = img.getThumbURL({
                "bands": ["B4", "B3", "B2"],
                "min": 0,
                "max": 3000,
                "dimensions": dimensions,
                "region": region,
                "format": "png",
            })
            import urllib.request
            with urllib.request.urlopen(rgb_url, timeout=60) as resp:
                result["rgb"] = resp.read()
        except Exception as exc:
            logger.warning("RGB thumb %d failed: %s", year, exc)

        # NDVI
        try:
            ndvi = img.normalizedDifference(["B8", "B4"]).rename("NDVI")
            ndvi_url = ndvi.getThumbURL({
                "min": -0.1,
                "max": 0.8,
                "palette": ["#d73027", "#fc8d59", "#fee08b", "#d9ef8b", "#91cf60", "#1a9850"],
                "dimensions": dimensions,
                "region": region,
                "format": "png",
            })
            import urllib.request
            with urllib.request.urlopen(ndvi_url, timeout=60) as resp:
                result["ndvi"] = resp.read()
        except Exception as exc:
            logger.warning("NDVI thumb %d failed: %s", year, exc)

        return result if ("rgb" in result or "ndvi" in result) else None

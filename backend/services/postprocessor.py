import numpy as np
import json
import uuid

class Postprocessor:
    def process_deforestation(self, mask_array, coords, original_shape, aoi_geojson):
        # mock creation of geojson
        return {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": aoi_geojson, # placeholder mock
                    "properties": {"loss": True}
                }
            ]
        }, {"area_ha": 234.5, "percent_lost": 12.3, "confidence": 0.94}

    def _shrink_polygon(self, aoi_geojson, factor):
        """Shrink AOI polygon toward its centroid by factor (0-1)."""
        if aoi_geojson.get("type") != "Polygon" or not aoi_geojson.get("coordinates"):
            return aoi_geojson
        coords = aoi_geojson["coordinates"][0]
        cx = sum(c[0] for c in coords) / len(coords)
        cy = sum(c[1] for c in coords) / len(coords)
        shrunk = [[cx + (x - cx) * factor, cy + (y - cy) * factor] for x, y in coords]
        return {"type": "Polygon", "coordinates": [shrunk]}

    def process_structures(self, predictions_list, coords, original_shape, aoi_geojson):
        # mock: small polygon inside AOI representing detected structures
        return {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": self._shrink_polygon(aoi_geojson, 0.3),
                    "properties": {"type": "building"}
                }
            ]
        }, {"count": 47, "types": {"building": 32, "solar_panel": 15}}

    def process_vegetation(self, class_array, coords, original_shape, aoi_geojson):
        # mock: polygon covering most of AOI representing vegetation class
        return {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": self._shrink_polygon(aoi_geojson, 0.8),
                    "properties": {"class": "bosque_denso"}
                }
            ]
        }, {
            "classes": {
                "bosque_denso": 45.2,
                "bosque_ralo": 23.1,
                "pastizal": 18.6,
                "suelo": 8.4,
                "agua": 4.7
            }
        }

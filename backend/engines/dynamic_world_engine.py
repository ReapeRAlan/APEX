from __future__ import annotations

import warnings
warnings.filterwarnings("ignore")

import numpy as np
from rasterio.features import shapes, rasterize
from shapely.geometry import shape, mapping
from pathlib import Path
import rasterio
from scipy.ndimage import binary_opening, binary_closing, uniform_filter

# Mapeo Dynamic World -> APEX
DW_LABEL_TO_APEX = {
    0: "agua",
    1: "bosque_denso",
    2: "pastizal",
    3: "manglar_inundado",
    4: "cultivos",
    5: "matorral",
    6: "urbano",
    7: "suelo",
    8: "nieve",
}

DW_COLORS = {
    "agua": "#3b82f6",
    "bosque_denso": "#166534",
    "pastizal": "#86efac",
    "manglar_inundado": "#7a87c6",
    "cultivos": "#e49635",
    "matorral": "#dfc35a",
    "urbano": "#6b21a8",
    "suelo": "#92400e",
    "nieve": "#b39fe1",
}

DW_PROB_BANDS = [
    "water", "trees", "grass", "flooded_vegetation",
    "crops", "shrub_and_scrub", "built", "bare", "snow_and_ice",
]

MIN_AREA_HA = 0.5  # filtro de area minima — elimina ruido pixelado


def _area_deg2_to_ha(area_deg2: float) -> float:
    return area_deg2 * (111_320 ** 2) * np.cos(np.radians(20)) / 10_000


def _log_features_bbox(tag: str, features: list):
    """Log bounding box of result features for debugging."""
    if not features:
        return
    all_lons, all_lats = [], []
    for f in features:
        rings = f["geometry"].get("coordinates", [[]])
        for ring in rings:
            if isinstance(ring[0], (list, tuple)):
                for c in ring:
                    all_lons.append(c[0])
                    all_lats.append(c[1])
    if all_lons:
        print(
            f"[{tag}] Resultados bbox: "
            f"lon=[{min(all_lons):.4f},{max(all_lons):.4f}] "
            f"lat=[{min(all_lats):.4f},{max(all_lats):.4f}]"
        )


class DynamicWorldEngine:
    """Clasificacion de vegetacion / cobertura usando Google Dynamic World V1."""

    MAX_FEATURES = 300

    # ------------------------------------------------------------------
    # Clasificacion a partir del raster DW descargado
    # ------------------------------------------------------------------
    def classify_from_raster(self, dw_raster_path: Path) -> tuple[dict, dict]:
        with rasterio.open(dw_raster_path) as src:
            label_band = src.read(1)  # banda 'label' (argmax composite)
            transform = src.transform
            nodata = src.nodata

            n_bands = src.count
            prob_bands = None
            if n_bands >= 10:
                prob_bands = np.stack([src.read(i) for i in range(2, 11)], axis=0)

        # Mascara de validez: excluir nodata y NaN
        valid_mask = np.isfinite(label_band)
        if nodata is not None:
            valid_mask &= (label_band != nodata)
        label_int = np.where(valid_mask, label_band.astype(np.int8), -1)

        # ── Refinamiento contextual: suelo→urbano ──
        label_int = self._refine_labels(label_int, prob_bands)

        total_valid = max(int(np.sum(valid_mask)), 1)

        # Porcentajes por clase
        class_pcts = {}
        for code, name in DW_LABEL_TO_APEX.items():
            pct = round(100 * float(np.sum(label_int == code)) / total_valid, 1)
            class_pcts[name] = pct

        # Vectorizar por clase con filtrado morfologico
        struct = np.ones((3, 3), dtype=bool)
        features = []
        for code, clase in DW_LABEL_TO_APEX.items():
            if class_pcts.get(clase, 0) < 0.1:
                continue
            raw_mask = (label_int == code)
            # Limpiar ruido con opening+closing
            clean = binary_opening(raw_mask, structure=struct)
            clean = binary_closing(clean, structure=struct).astype(np.uint8)

            for geom, val in shapes(clean, transform=transform):
                if val != 1:
                    continue
                poly = shape(geom)
                area_ha = _area_deg2_to_ha(poly.area)
                if area_ha < MIN_AREA_HA:
                    continue

                # Confianza promedio por poligono
                confidence = 0.0
                if prob_bands is not None:
                    poly_rast = rasterize(
                        [(geom, 1)], out_shape=label_band.shape,
                        transform=transform, dtype=np.uint8,
                    )
                    vals = prob_bands[code][poly_rast == 1]
                    if len(vals) > 0:
                        confidence = round(float(np.nanmean(vals)), 3)

                features.append({
                    "type": "Feature",
                    "geometry": mapping(poly),
                    "properties": {
                        "class": clase,
                        "area_ha": round(area_ha, 2),
                        "confidence": confidence,
                    },
                })

        features.sort(key=lambda f: f["properties"]["area_ha"], reverse=True)
        features = features[: self.MAX_FEATURES]

        geojson = {"type": "FeatureCollection", "features": features}
        stats = {"classes": class_pcts, "source": "Google Dynamic World V1"}

        print(f"[DW-Vegetation] {len(features)} poligonos, clases: {class_pcts}")
        _log_features_bbox("DW-Vegetation", features)
        return geojson, stats

    # ------------------------------------------------------------------
    # Deteccion de deforestacion por cambio T1 vs T2
    # ------------------------------------------------------------------
    def detect_deforestation(
        self,
        dw_t1_path: Path,
        dw_t2_path: Path,
    ) -> tuple[dict, dict]:
        """Compara cobertura forestal entre T1 y T2 usando Dynamic World."""
        with rasterio.open(dw_t1_path) as src1:
            label_t1 = src1.read(1)
            transform = src1.transform
            n1 = src1.count
            prob_t1 = None
            if n1 >= 10:
                prob_t1 = np.stack([src1.read(i) for i in range(2, 11)], axis=0)

        with rasterio.open(dw_t2_path) as src2:
            label_t2 = src2.read(1)
            n2 = src2.count
            prob_t2 = None
            if n2 >= 10:
                prob_t2 = np.stack([src2.read(i) for i in range(2, 11)], axis=0)  # noqa: F841

        # Validez
        valid = np.isfinite(label_t1) & np.isfinite(label_t2)
        t1 = np.where(valid, label_t1.astype(np.int8), -1)
        t2 = np.where(valid, label_t2.astype(np.int8), -1)

        # Clases forestales: trees(1), flooded_vegetation(3), shrub_and_scrub(5)
        FOREST_CODES = [1, 3, 5]
        forest_t1 = np.isin(t1, FOREST_CODES)
        forest_t2 = np.isin(t2, FOREST_CODES)

        # Deforestacion = era forestal en T1, ya no en T2
        deforest_raw = forest_t1 & ~forest_t2

        # Limpieza morfologica
        struct = np.ones((3, 3), dtype=bool)
        deforest_clean = binary_opening(deforest_raw, structure=struct)
        deforest_clean = binary_closing(deforest_clean, structure=struct).astype(np.uint8)

        total_valid = max(int(np.sum(valid)), 1)
        pix_deforest = int(np.sum(deforest_clean))
        pct_lost = round(100.0 * pix_deforest / total_valid, 2)
        pix_forest_t1 = int(np.sum(forest_t1))

        features = []
        for geom, val in shapes(deforest_clean, transform=transform):
            if val != 1:
                continue
            poly = shape(geom)
            area_ha = _area_deg2_to_ha(poly.area)
            if area_ha < MIN_AREA_HA:
                continue

            # Rasterizar geometria del poligono para extraer estadisticas
            poly_rast = rasterize(
                [(geom, 1)], out_shape=label_t1.shape,
                transform=transform, dtype=np.uint8,
            )
            pmask = poly_rast == 1

            # Confianza: max de probabilidades forestales (trees, flooded_veg, shrub) en T1
            confidence = 0.0
            if prob_t1 is not None:
                # indices: 1=trees, 3=flooded_vegetation, 5=shrub_and_scrub
                forest_probs = np.stack([prob_t1[idx][pmask] for idx in [1, 3, 5]], axis=0)
                max_forest = np.max(forest_probs, axis=0)
                if len(max_forest) > 0:
                    confidence = round(float(np.nanmean(max_forest)), 3)

            # Clase destino dominante en T2 dentro del poligono
            t2_vals = t2[pmask]
            t2_vals = t2_vals[t2_vals >= 0]
            if len(t2_vals) > 0:
                dominant = int(np.argmax(np.bincount(t2_vals)))
                dest_class = DW_LABEL_TO_APEX.get(dominant, "desconocido")
            else:
                dest_class = "desconocido"

            features.append({
                "type": "Feature",
                "geometry": mapping(poly),
                "properties": {
                    "area_ha": round(area_ha, 2),
                    "confidence": confidence,
                    "type": "deforestation_dw",
                    "transition_to": dest_class,
                },
            })

        # Filtrar features de baja confianza
        features = [f for f in features if f["properties"]["confidence"] >= 0.25]
        features.sort(key=lambda f: f["properties"]["area_ha"], reverse=True)
        features = features[:200]

        total_area_ha = sum(f["properties"]["area_ha"] for f in features)

        geojson = {"type": "FeatureCollection", "features": features}
        stats = {
            "area_ha": round(total_area_ha, 1),
            "percent_lost": pct_lost,
            "forest_pct_t1": round(100 * pix_forest_t1 / total_valid, 1),
            "n_features": len(features),
            "confidence": round(float(np.mean([f["properties"]["confidence"] for f in features])), 3) if features else 0,
            "source": "Google Dynamic World V1 (T1 vs T2)",
        }

        print(f"[DW-Deforestation] {len(features)} poligonos, area={total_area_ha:.1f}ha, perdida={pct_lost}%, conf={stats['confidence']}")
        _log_features_bbox("DW-Deforestation", features)
        return geojson, stats

    # ------------------------------------------------------------------
    # Refinamiento contextual de etiquetas suelo→urbano
    # ------------------------------------------------------------------
    def _refine_labels(self, labels: np.ndarray, prob_bands: np.ndarray | None) -> np.ndarray:
        """
        Aplica refinamiento contextual suelo→urbano a un array de etiquetas.
        DW confunde "suelo" (7) con "urbano" (6) en zonas de construccion:
          - Si pixel es suelo pero >35% del vecindario 5x5 es urbano → reclasificar
          - Si pixel es suelo y prob_built >= prob_bare * 0.5 → reclasificar
          - Si pixel es suelo rodeado de suelo+urbano y prob_built > 0.15 → probable construccion
        """
        refined = labels.copy()
        CODE_URBANO = 6
        CODE_SUELO = 7

        suelo_mask = (refined == CODE_SUELO)
        if not np.any(suelo_mask):
            return refined

        # Fraccion de vecinos urbanos en ventana 5x5
        urbano_mask = (refined == CODE_URBANO).astype(np.float32)
        urban_frac = uniform_filter(urbano_mask, size=5, mode='constant', cval=0.0)

        # Fraccion de vecinos que son suelo+urbano (zona urbanizada/en construccion)
        urban_or_bare = ((refined == CODE_URBANO) | (refined == CODE_SUELO)).astype(np.float32)
        urban_bare_frac = uniform_filter(urban_or_bare, size=7, mode='constant', cval=0.0)

        # Criterio 1: >35% vecinos directos son urbanos
        criteria_neighbors = suelo_mask & (urban_frac > 0.35)

        # Criterio 2: >60% de vecindario 7x7 es suelo+urbano (zona de desarrollo)
        criteria_development = suelo_mask & (urban_bare_frac > 0.60)

        reclassify_mask = criteria_neighbors | criteria_development

        if prob_bands is not None:
            prob_built = prob_bands[CODE_URBANO]
            prob_bare = prob_bands[CODE_SUELO]
            # Para C1: prob_built al menos 50% de prob_bare
            prob_check_strict = (prob_built >= prob_bare * 0.5)
            # Para C2 (zona de desarrollo): prob_built > 0.15 es suficiente
            prob_check_loose = (prob_built > 0.15)
            reclassify_mask = (criteria_neighbors & prob_check_strict) | \
                              (criteria_development & prob_check_loose)

        n_reclassified = int(np.sum(reclassify_mask))
        if n_reclassified > 0:
            refined[reclassify_mask] = CODE_URBANO
            print(f"[DW] Refinamiento suelo→urbano: {n_reclassified} pixeles reclasificados")

        return refined

    # ------------------------------------------------------------------
    # Deteccion de expansion urbana T1 vs T2
    # ------------------------------------------------------------------
    def detect_urban_expansion(
        self,
        dw_t1_path: Path,
        dw_t2_path: Path,
    ) -> tuple[dict, dict]:
        """
        Detecta areas que transitaron de cobertura natural/suelo a urbano/construido
        entre T1 y T2. Incluye fraccionamientos, bodegas, caminos, industria.

        Detecta DOS tipos de transicion:
          1. natural → urbano/suelo (desbosque/desmonte para construccion)
          2. suelo → urbano (terreno despejado que se urbanizó)
        Aplica refinamiento contextual para que DW no confunda suelo con urbano.
        """
        with rasterio.open(dw_t1_path) as src1:
            label_t1 = src1.read(1)
            transform = src1.transform
            prob_t1 = np.stack([src1.read(i) for i in range(2, 11)], axis=0) if src1.count >= 10 else None

        with rasterio.open(dw_t2_path) as src2:
            label_t2 = src2.read(1)
            prob_t2 = np.stack([src2.read(i) for i in range(2, 11)], axis=0) if src2.count >= 10 else None

        valid = np.isfinite(label_t1) & np.isfinite(label_t2)
        t1_raw = np.where(valid, label_t1.astype(np.int8), -1)
        t2_raw = np.where(valid, label_t2.astype(np.int8), -1)

        # Aplicar refinamiento contextual suelo→urbano en T2
        # Esto captura zonas donde DW dice "suelo" pero ya hay casas
        t2 = self._refine_labels(t2_raw, prob_t2)
        t1 = self._refine_labels(t1_raw, prob_t1)

        CODE_URBANO = 6
        CODE_SUELO = 7

        # ── Transicion 1: natural → urbano/suelo (nueva expansion) ──
        NATURAL_T1 = [1, 2, 3, 4, 5]  # trees, grass, flooded_veg, crops, shrub
        URBAN_T2 = [CODE_URBANO, CODE_SUELO]  # built, bare
        was_natural = np.isin(t1, NATURAL_T1)
        now_urban = np.isin(t2, URBAN_T2)
        expansion_from_natural = was_natural & now_urban

        # ── Transicion 2: suelo → urbano (terreno despejado se urbanizó) ──
        was_bare = (t1 == CODE_SUELO)
        now_built = (t2 == CODE_URBANO)
        expansion_from_bare = was_bare & now_built

        # Combinar ambas transiciones
        expansion_raw = expansion_from_natural | expansion_from_bare

        struct = np.ones((3, 3), dtype=bool)
        expansion = binary_opening(expansion_raw, structure=struct)
        expansion = binary_closing(expansion, structure=struct).astype(np.uint8)

        total_valid = max(int(np.sum(valid)), 1)
        features = []

        for geom, val in shapes(expansion, transform=transform):
            if val != 1:
                continue
            poly = shape(geom)
            area_ha = _area_deg2_to_ha(poly.area)
            if area_ha < MIN_AREA_HA:
                continue

            poly_rast = rasterize(
                [(geom, 1)], out_shape=label_t1.shape,
                transform=transform, dtype=np.uint8,
            )
            pmask = poly_rast == 1

            # Clase origen en T1 (antes del refinamiento, para reportar clase real)
            t1_orig_vals = t1_raw[pmask]
            t1_orig_vals = t1_orig_vals[t1_orig_vals >= 0]
            origin_label = int(np.argmax(np.bincount(t1_orig_vals))) if len(t1_orig_vals) > 0 else 7
            origin_class = DW_LABEL_TO_APEX.get(origin_label, "desconocido")

            # Clase destino en T2 (refinada)
            t2_vals = t2[pmask]
            t2_vals = t2_vals[t2_vals >= 0]
            dest_label = int(np.argmax(np.bincount(t2_vals))) if len(t2_vals) > 0 else 7
            dest_class = DW_LABEL_TO_APEX.get(dest_label, "desconocido")

            # Confianza: probabilidad de built/bare en T2
            confidence = 0.0
            if prob_t2 is not None:
                built_probs = prob_t2[6][pmask]
                bare_probs = prob_t2[7][pmask]
                if len(built_probs) > 0:
                    confidence = round(float(np.nanmean(
                        np.maximum(built_probs, bare_probs)
                    )), 3)

            # Determinar tipo de expansion
            # Verificar que proporcion del poligono era natural vs suelo en T1
            n_from_bare = int(np.sum(t1_raw[pmask] == CODE_SUELO))
            n_from_natural = int(np.sum(np.isin(t1_raw[pmask], NATURAL_T1)))  # noqa: F841
            n_now_built = int(np.sum(t2[pmask] == CODE_URBANO))
            total_px = max(int(np.sum(pmask)), 1)

            if n_now_built / total_px > 0.4:
                expansion_type = "urbanizacion"
            elif n_from_bare / total_px > 0.5 and n_now_built / total_px > 0.2:
                expansion_type = "urbanizacion_reciente"  # bare→built
            elif dest_label == CODE_URBANO:
                expansion_type = "urbanizacion"
            else:
                expansion_type = "preparacion_terreno"

            features.append({
                "type": "Feature",
                "geometry": mapping(poly),
                "properties": {
                    "area_ha": round(area_ha, 2),
                    "confidence": confidence,
                    "expansion_type": expansion_type,
                    "from_class": origin_class,
                    "to_class": dest_class,
                    "alerta": "Posible fraccionamiento o construccion sin permiso"
                              if area_ha > 1.0 else "Cambio menor",
                },
            })

        features = [f for f in features if f["properties"]["confidence"] >= 0.2]
        features.sort(key=lambda f: f["properties"]["area_ha"], reverse=True)
        features = features[:100]

        total_area = sum(f["properties"]["area_ha"] for f in features)

        # Count by expansion type
        n_urbanization = sum(1 for f in features if f["properties"]["expansion_type"] == "urbanizacion")
        n_recent = sum(1 for f in features if f["properties"]["expansion_type"] == "urbanizacion_reciente")
        n_prep = sum(1 for f in features if f["properties"]["expansion_type"] == "preparacion_terreno")

        geojson = {"type": "FeatureCollection", "features": features}
        stats = {
            "area_ha": round(total_area, 1),
            "percent_changed": round(100 * int(np.sum(expansion)) / total_valid, 2),
            "n_features": len(features),
            "n_urbanizacion": n_urbanization,
            "n_urbanizacion_reciente": n_recent,
            "n_preparacion_terreno": n_prep,
            "source": "Dynamic World T1->T2 urban expansion (refined)",
        }

        print(f"[DW-UrbanExpansion] {len(features)} poligonos, area={total_area:.1f}ha "
              f"(urb={n_urbanization}, reciente={n_recent}, prep={n_prep})")
        _log_features_bbox("DW-UrbanExpansion", features)
        return geojson, stats

    # ── Anomaly detector ──────────────────────────────────────────
    def detect_anomalies(self, timeline_stats: dict) -> list:
        """
        Identifica anios donde el cambio fue estadisticamente anomalo
        (mas de 2 desviaciones estandar sobre la media).
        """
        import statistics

        years = sorted(timeline_stats.keys())
        def_values = [timeline_stats[y].get("deforestation", {}).get("stats", {}).get("area_ha", 0) for y in years]
        ue_values = [timeline_stats[y].get("urban_expansion", {}).get("stats", {}).get("area_ha", 0) for y in years]

        alerts: list = []

        for values, engine_name, color in [
            (def_values, "deforestation", "#ef4444"),
            (ue_values, "urban_expansion", "#f97316"),
        ]:
            if len(values) < 3:
                continue
            mean = statistics.mean(values)
            stdev = statistics.stdev(values) if len(values) > 1 else 0
            if stdev == 0:
                continue
            for year, val in zip(years, values):
                z_score = (val - mean) / stdev
                if z_score > 2.0:
                    alerts.append({
                        "year": int(year),
                        "engine": engine_name,
                        "area_ha": val,
                        "z_score": round(z_score, 2),
                        "mean_ha": round(mean, 1),
                        "severity": "alta" if z_score > 3 else "media",
                        "color": color,
                        "message": f"Cambio {engine_name} anomalo en {year}: {val}ha vs media {round(mean, 1)}ha",
                    })

        return sorted(alerts, key=lambda a: a["z_score"], reverse=True)
"""
APEX Forecast Engine v2 â€” 3-layer deforestation prediction (1-5 yr horizon).

Layer 1 (Trend): Weighted linear regression on per-year timeline data.
Layer 2 (ML):    RandomForest trained on multi-feature vectors.
Layer 3 (POMDP): Forward rollout under WAIT action using transition matrix.

Works directly from timeline analysis results â€” no H3 grid required.
"""
from __future__ import annotations

import json
import logging
import math
import os
import pickle
import sqlite3

import numpy as np
from shapely.geometry import mapping, shape
from shapely.ops import unary_union
from shapely.affinity import translate

from ..db.session import DATABASE_URL
from .convlstm_model import forecast_convlstm as _run_convlstm, train_convlstm as _train_convlstm_model

logger = logging.getLogger("apex.forecast")

# â”€â”€ Risk levels â”€â”€
RISK_CRITICAL = "CRITICAL"
RISK_HIGH = "HIGH"
RISK_MEDIUM = "MEDIUM"
RISK_LOW = "LOW"

# â”€â”€ Transition matrix T(s'|s, WAIT) â€” annual under no-action â”€â”€
TRANSITION_WAIT = np.array([
    [0.92, 0.04, 0.02, 0.02],
    [0.05, 0.85, 0.05, 0.05],
    [0.02, 0.03, 0.90, 0.05],
    [0.03, 0.04, 0.03, 0.90],
])

SEVERITY = {"tala": 15.0, "cus_inmobiliario": 25.0, "frontera_agricola": 20.0}
IRREVERSIBILITY = {"tala": 0.7, "cus_inmobiliario": 0.95, "frontera_agricola": 0.6}

_MODEL_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "models")
_RF_MODEL_PATH = os.path.join(_MODEL_DIR, "forecast_rf.pkl")
_CONVLSTM_MODEL_PATH = os.path.join(_MODEL_DIR, "forecast_convlstm.pt")

# 4-layer ensemble weights: (trend, ml/RF, pomdp, convlstm)
# ConvLSTM becomes dominant at longer horizons (spatial-temporal awareness)
ENSEMBLE_WEIGHTS = {
    1: (0.35, 0.20, 0.10, 0.35),
    2: (0.25, 0.20, 0.15, 0.40),
    3: (0.15, 0.15, 0.25, 0.45),
    4: (0.08, 0.12, 0.30, 0.50),
    5: (0.05, 0.10, 0.35, 0.50),
}

FEATURE_COLS = [
    "deforestation_ha", "urban_expansion_ha", "hansen_loss_ha",
    "sar_change_ha", "fire_burned_ha", "firms_hotspots",
    "firms_frp_mw", "alerts_count",
]


def _get_db_path() -> str:
    # Check workspace root/db first (where pipeline stores results),
    # then APEX/db as fallback, then DATABASE_URL
    apex_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    candidates = [
        os.path.join(os.path.dirname(apex_root), "db", "apex.sqlite"),
        os.path.join(apex_root, "db", "apex.sqlite"),
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    if DATABASE_URL.startswith("sqlite:///"):
        return DATABASE_URL.replace("sqlite:///", "")
    return candidates[-1]


def _risk_level(ha: float) -> str:
    if ha > 10:
        return RISK_CRITICAL
    if ha > 5:
        return RISK_HIGH
    if ha > 1:
        return RISK_MEDIUM
    return RISK_LOW


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
#  Extract per-year series from timeline results in DB
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

def _extract_timeline_series(job_id: str) -> list[dict]:
    """
    Read timeline_{year} rows from analysis_results for a given job_id.
    Data is stored in the `geojson` column as {"timeline": {engine: {stats:...}}}.
    Returns sorted list of per-year feature dicts.
    """
    db_path = _get_db_path()
    series = []
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT engine, geojson FROM analysis_results "
            "WHERE job_id = ? AND engine LIKE 'timeline_%' "
            "AND engine != 'timeline_summary' "
            "ORDER BY engine",
            (job_id,),
        ).fetchall()
        conn.close()
    except Exception as exc:
        logger.error("Failed to read timeline results: %s", exc)
        return []

    for row in rows:
        engine_name = row["engine"]
        try:
            year = int(engine_name.replace("timeline_", ""))
        except ValueError:
            continue

        raw = row["geojson"]
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue

        # geojson = {"timeline": {engine: {geojson, stats}, year, baseline_year}}
        tl = data.get("timeline", data)

        defor = tl.get("deforestation", {}).get("stats", {})
        urban = tl.get("urban_expansion", {}).get("stats", {})
        sar = tl.get("sar", {}).get("stats", {})
        firms = tl.get("firms_hotspots", {}).get("stats", {})
        hansen = tl.get("hansen", {}).get("stats", {})
        fire = tl.get("fire", {}).get("stats", {})
        alerts = tl.get("alerts", {}).get("stats", {})

        entry = {
            "year": year,
            "deforestation_ha": float(defor.get("area_ha", 0)),
            "urban_expansion_ha": float(urban.get("area_ha", 0)),
            "hansen_loss_ha": float(hansen.get("loss_ha", 0)),
            "sar_change_ha": float(sar.get("total_change_ha", 0)),
            "fire_burned_ha": float(fire.get("total_burned_ha", 0)),
            "firms_hotspots": int(firms.get("hotspot_count", 0)),
            "firms_frp_mw": float(firms.get("total_frp_mw", 0)),
            "alerts_count": int(alerts.get("total_alerts", 0)),
        }
        series.append(entry)

    series.sort(key=lambda x: x["year"])
    logger.info("Extracted %d year entries from job %s", len(series), job_id[:8])
    return series


def _find_latest_timeline_job() -> str | None:
    """Find the most recent completed timeline job."""
    db_path = _get_db_path()
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT DISTINCT j.id FROM jobs j "
            "INNER JOIN analysis_results ar ON ar.job_id = j.id "
            "WHERE j.status = 'completed' AND ar.engine LIKE 'timeline_%' "
            "AND ar.engine != 'timeline_summary' "
            "ORDER BY j.created_at DESC LIMIT 1",
        ).fetchall()
        conn.close()
    except Exception as exc:
        logger.error("Failed to find timeline jobs: %s", exc)
        return None

    return rows[0]["id"] if rows else None


# ╔═══════════════════════════════════════════════════════════════╗
#  Spatial forecast — predict WHERE changes will expand
# ╚═══════════════════════════════════════════════════════════════╝

def _extract_spatial_features(job_id: str) -> dict:
    """
    Extract actual polygon GeoJSON per year per engine type.
    Returns {year: {"deforestation": [shapely geoms], "urban_expansion": [shapely geoms]}}.
    """
    db_path = _get_db_path()
    result: dict[int, dict[str, list]] = {}
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT engine, geojson FROM analysis_results "
            "WHERE job_id = ? AND engine LIKE 'timeline_%' "
            "AND engine != 'timeline_summary' ORDER BY engine",
            (job_id,),
        ).fetchall()
        conn.close()
    except Exception as exc:
        logger.error("Failed to read spatial features: %s", exc)
        return result

    for row in rows:
        try:
            year = int(row["engine"].replace("timeline_", ""))
        except ValueError:
            continue
        raw = row["geojson"]
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue

        tl = data.get("timeline", data)
        year_data: dict[str, list] = {"deforestation": [], "urban_expansion": []}

        for engine_key in ("deforestation", "urban_expansion"):
            gjson = tl.get(engine_key, {}).get("geojson")
            if not gjson or not gjson.get("features"):
                continue
            for feat in gjson["features"]:
                geom = feat.get("geometry")
                if not geom:
                    continue
                try:
                    s = shape(geom)
                    if s.is_valid and not s.is_empty:
                        year_data[engine_key].append(s)
                except Exception:
                    continue

        result[year] = year_data

    return result


def _compute_growth_vector(spatial: dict, engine_key: str) -> tuple[float, float]:
    """
    Compute average centroid movement direction across years.
    Returns (dx, dy) in coordinate units per year.
    """
    years = sorted(spatial.keys())
    centroids = []
    for y in years:
        geoms = spatial[y].get(engine_key, [])
        if not geoms:
            continue
        union = unary_union(geoms)
        c = union.centroid
        centroids.append((y, c.x, c.y))

    if len(centroids) < 2:
        return (0.0, 0.0)

    # Weighted: more recent years count more
    dx_sum, dy_sum, w_sum = 0.0, 0.0, 0.0
    for i in range(1, len(centroids)):
        dt = centroids[i][0] - centroids[i - 1][0]
        if dt == 0:
            continue
        dx = (centroids[i][1] - centroids[i - 1][1]) / dt
        dy = (centroids[i][2] - centroids[i - 1][2]) / dt
        weight = float(i)  # recent transitions weigh more
        dx_sum += dx * weight
        dy_sum += dy * weight
        w_sum += weight

    if w_sum < 1e-12:
        return (0.0, 0.0)

    return (dx_sum / w_sum, dy_sum / w_sum)


def _generate_spatial_forecast(
    job_id: str,
    predictions: list[dict],
    series: list[dict],
) -> dict:
    """
    Generate predicted expansion polygons for each forecast year.
    Returns {"deforestation": FeatureCollection, "urban_expansion": FeatureCollection}.
    """
    spatial = _extract_spatial_features(job_id)
    if not spatial:
        return {}

    years = sorted(spatial.keys())
    output: dict[str, dict] = {}

    for engine_key in ("deforestation", "urban_expansion"):
        features: list[dict] = []
        ha_key = "deforestation_ha" if engine_key == "deforestation" else "urban_expansion_ha"

        # Collect recent geometries (last 3 years with data)
        recent_geoms: list = []
        recent_years: list[int] = []
        for y in reversed(years):
            geoms = spatial[y].get(engine_key, [])
            if geoms:
                recent_geoms.extend(geoms)
                recent_years.append(y)
            if len(recent_years) >= 3:
                break

        if not recent_geoms:
            continue

        # Compute base union and growth vector
        base_union = unary_union(recent_geoms)
        base_area = base_union.area  # in degree² (approximate)
        dx, dy = _compute_growth_vector(spatial, engine_key)

        # Historical avg ha for scaling
        hist_ha = [s.get(ha_key, 0) for s in series if s.get(ha_key, 0) > 0]
        avg_ha = sum(hist_ha) / max(len(hist_ha), 1) if hist_ha else 1.0

        last_year = years[-1]

        for pred in predictions:
            pred_year = pred.get("year", 0)
            pred_ha = pred.get("deforestation_ha", 0)
            if pred_year <= last_year or pred_ha <= 0:
                continue

            years_ahead = pred_year - last_year

            # Buffer radius: proportional to predicted ha relative to historical
            # Use sqrt scaling (area ∝ radius²) in degrees (~111km per degree)
            scale_factor = (pred_ha / max(avg_ha, 0.1)) ** 0.5
            # Base buffer ~0.001 degrees (~111m), scale with prediction magnitude
            buffer_dist = 0.001 * max(scale_factor, 0.5) * years_ahead

            # Translate in growth direction
            shifted = translate(base_union, xoff=dx * years_ahead, yoff=dy * years_ahead)

            # Buffer to create expansion zone
            expanded = shifted.buffer(buffer_dist)

            # Subtract the original to show only NEW predicted areas
            try:
                new_area = expanded.difference(base_union)
            except Exception:
                new_area = expanded

            if new_area.is_empty:
                new_area = expanded

            geojson_geom = mapping(new_area)
            risk = pred.get("risk", "MEDIUM")

            features.append({
                "type": "Feature",
                "geometry": geojson_geom,
                "properties": {
                    "year": pred_year,
                    "predicted_ha": round(pred_ha, 2),
                    "risk": risk,
                    "type": f"{engine_key}_forecast",
                    "years_ahead": years_ahead,
                    "label": f"Predicción {pred_year}: {pred_ha:.1f} ha [{risk}]",
                },
            })

        if features:
            output[engine_key] = {
                "type": "FeatureCollection",
                "features": features,
            }

    return output


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
#  Layer 1 â€” Trend (Weighted Linear Regression)
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

def _forecast_trend(series: list[dict], horizon: int) -> dict:
    if len(series) < 3:
        return {"available": False, "reason": f"Necesita â‰¥3 aÃ±os, tiene {len(series)}"}

    years = np.array([s["year"] for s in series], dtype=float)
    values = np.array([s["deforestation_ha"] for s in series], dtype=float)

    coeffs = np.polyfit(years, values, 1)
    slope, intercept = coeffs

    fitted = np.polyval(coeffs, years)
    residuals = values - fitted
    sigma = float(np.std(residuals)) if len(residuals) > 2 else 0.0

    last_year = int(years[-1])
    predictions = []
    for h in range(1, horizon + 1):
        target_year = last_year + h
        pred = max(slope * target_year + intercept, 0.0)
        predictions.append({
            "year": target_year,
            "deforestation_ha": round(float(pred), 3),
            "ci_lower": round(max(float(pred - 2 * sigma), 0.0), 3),
            "ci_upper": round(float(pred + 2 * sigma), 3),
            "risk": _risk_level(pred),
        })

    return {
        "available": True,
        "method": "weighted_linear_regression",
        "slope_ha_yr": round(float(slope), 4),
        "r_squared": round(float(
            1 - np.var(residuals) / (np.var(values) + 1e-9)
        ), 4),
        "predictions": predictions,
    }


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
#  Layer 2 â€” ML (RandomForest)
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

def _build_feature_vector(row: dict) -> list[float]:
    return [float(row.get(c, 0.0)) for c in FEATURE_COLS]


def _load_rf_model():
    if os.path.exists(_RF_MODEL_PATH):
        with open(_RF_MODEL_PATH, "rb") as f:
            return pickle.load(f)  # noqa: S301
    return None


def _forecast_ml(series: list[dict], horizon: int) -> dict:
    model = _load_rf_model()
    if model is None:
        return {"available": False, "reason": "Modelo no entrenado aÃºn"}
    if len(series) < 2:
        return {"available": False, "reason": f"Necesita â‰¥2 aÃ±os, tiene {len(series)}"}

    last = max(series, key=lambda s: s["year"])
    features = np.array(_build_feature_vector(last)).reshape(1, -1)

    predictions = []
    for h in range(1, horizon + 1):
        pred = max(float(model.predict(features)[0]), 0.0)
        predictions.append({
            "year": last["year"] + h,
            "deforestation_ha": round(pred, 3),
            "risk": _risk_level(pred),
        })
        features[0, 0] = pred

    return {"available": True, "method": "random_forest", "predictions": predictions}


def train_rf_model() -> dict:
    """Train RF on all available timeline data."""
    from sklearn.ensemble import RandomForestRegressor
    from sklearn.metrics import mean_absolute_error, r2_score

    db_path = _get_db_path()
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        job_rows = conn.execute(
            "SELECT DISTINCT j.id FROM jobs j "
            "INNER JOIN analysis_results ar ON ar.job_id = j.id "
            "WHERE j.status = 'completed' AND ar.engine LIKE 'timeline_%' "
            "AND ar.engine != 'timeline_summary'"
        ).fetchall()
        conn.close()
    except Exception:
        return {"status": "error", "detail": "No se pudo acceder a la base de datos"}

    job_ids = [r["id"] for r in job_rows]
    if not job_ids:
        return {"status": "error", "detail": "No hay anÃ¡lisis timeline para entrenar"}

    all_series = []
    for jid in job_ids:
        s = _extract_timeline_series(jid)
        if len(s) >= 2:
            all_series.append(s)

    if not all_series:
        return {"status": "error", "detail": "No hay datos temporales suficientes"}

    X_all, y_all = [], []
    for series in all_series:
        for i in range(len(series) - 1):
            X_all.append(_build_feature_vector(series[i]))
            y_all.append(series[i + 1]["deforestation_ha"])

    if len(X_all) < 3:
        return {"status": "error", "detail": f"Solo {len(X_all)} muestras, necesita â‰¥3"}

    X = np.array(X_all)
    y = np.array(y_all)

    split = max(int(len(X) * 0.7), 1)
    X_train, X_val = X[:split], X[split:]
    y_train, y_val = y[:split], y[split:]

    model = RandomForestRegressor(
        n_estimators=100, max_depth=8, min_samples_leaf=2,
        random_state=42, n_jobs=-1,
    )
    model.fit(X_train, y_train)

    if len(X_val) > 0:
        y_pred = model.predict(X_val)
        mae = round(float(mean_absolute_error(y_val, y_pred)), 4)
        r2 = round(float(r2_score(y_val, y_pred)), 4)
    else:
        mae, r2 = 0.0, 0.0

    os.makedirs(_MODEL_DIR, exist_ok=True)
    with open(_RF_MODEL_PATH, "wb") as f:
        pickle.dump(model, f)

    logger.info("RF trained: %d samples, MAE=%.3f, RÂ²=%.3f", len(X), mae, r2)
    return {
        "status": "ok",
        "samples": len(X),
        "train_size": len(X_train),
        "val_size": len(X_val),
        "mae": mae,
        "r2": r2,
        "feature_importance": {
            FEATURE_COLS[i]: round(float(v), 4)
            for i, v in enumerate(model.feature_importances_)
        },
    }


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
#  Layer 3 â€” POMDP Rollout (from timeline data)
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

def _forecast_pomdp(series: list[dict], horizon: int) -> dict:
    if not series:
        return {"available": False, "reason": "Sin datos"}

    last = series[-1]
    total_ha = last["deforestation_ha"] + last["urban_expansion_ha"] + last.get("fire_burned_ha", 0)

    # Estimate initial belief from observed data
    if total_ha < 0.5:
        b = np.array([0.90, 0.04, 0.03, 0.03])
    elif last["deforestation_ha"] > last["urban_expansion_ha"]:
        p_tala = min(0.15 + last["deforestation_ha"] / 100, 0.6)
        p_agri = min(0.05 + last.get("fire_burned_ha", 0) / 50, 0.3)
        p_cus = min(0.05 + last["urban_expansion_ha"] / 100, 0.3)
        p_sin = max(1.0 - p_tala - p_agri - p_cus, 0.05)
        b = np.array([p_sin, p_tala, p_cus, p_agri])
    else:
        p_cus = min(0.15 + last["urban_expansion_ha"] / 100, 0.6)
        p_tala = min(0.05 + last["deforestation_ha"] / 100, 0.3)
        p_agri = min(0.05 + last.get("fire_burned_ha", 0) / 50, 0.3)
        p_sin = max(1.0 - p_tala - p_agri - p_cus, 0.05)
        b = np.array([p_sin, p_tala, p_cus, p_agri])

    b = b / b.sum()
    last_year = last["year"]
    predictions = []

    for h in range(1, horizon + 1):
        b = b @ TRANSITION_WAIT
        b = b / (b.sum() + 1e-12)

        expected_damage = (
            b[1] * SEVERITY["tala"] * IRREVERSIBILITY["tala"]
            + b[2] * SEVERITY["cus_inmobiliario"] * IRREVERSIBILITY["cus_inmobiliario"]
            + b[3] * SEVERITY["frontera_agricola"] * IRREVERSIBILITY["frontera_agricola"]
        )

        predictions.append({
            "year": last_year + h,
            "deforestation_ha": round(float(expected_damage), 3),
            "p_sin_ilicito": round(float(b[0]), 4),
            "p_tala": round(float(b[1]), 4),
            "p_cus_inmobiliario": round(float(b[2]), 4),
            "p_frontera_agricola": round(float(b[3]), 4),
            "risk": _risk_level(expected_damage),
        })

    return {"available": True, "method": "pomdp_rollout", "predictions": predictions}




# ═══════════════════════════════════════════════════════════════════════════
#  Layer 4 — ConvLSTM Spatiotemporal Forecast
# ═══════════════════════════════════════════════════════════════════════════

def _forecast_convlstm(series: list[dict], horizon: int) -> dict:
    """Run ConvLSTM spatiotemporal forecast."""
    if not series or len(series) < 4:
        return {"available": False, "reason": "Se necesitan al menos 4 años para ConvLSTM"}

    if not os.path.exists(_CONVLSTM_MODEL_PATH):
        return {"available": False, "reason": "Modelo ConvLSTM no entrenado. Ejecuta /forecast/train-convlstm primero."}

    try:
        result = _run_convlstm(
            series=series,
            horizon=horizon,
        )
        return {"available": True, "method": "convlstm", "predictions": result["predictions"]}
    except Exception as exc:
        logger.warning("ConvLSTM forecast failed: %s", exc)
        return {"available": False, "reason": str(exc)}


def train_convlstm_model(job_id: str = None) -> dict:
    """Train the ConvLSTM model from timeline data."""
    if not job_id:
        job_id = _find_latest_timeline_job()
    if not job_id:
        return {"status": "error", "detail": "No hay análisis temporal disponible."}

    series = _extract_timeline_series(job_id)
    if not series or len(series) < 5:
        return {"status": "error", "detail": f"Insuficientes datos: {len(series or [])} años (mínimo 5)."}

    try:
        os.makedirs(_MODEL_DIR, exist_ok=True)
        result = _train_convlstm_model(
            all_series=[series],
        )
        logger.info("ConvLSTM trained: %s", result)
        return {"status": "ok", **result}
    except Exception as exc:
        logger.error("ConvLSTM training failed: %s", exc)
        return {"status": "error", "detail": str(exc)}

# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
#  Ensemble
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

def _compute_ensemble(layers: dict, horizon: int, base_year: int) -> dict:
    predictions = []
    for h in range(1, horizon + 1):
        values = {}
        for name in ("trend", "ml", "pomdp", "convlstm"):
            layer = layers.get(name, {})
            if layer.get("available") and layer.get("predictions"):
                preds = layer["predictions"]
                if h - 1 < len(preds):
                    values[name] = preds[h - 1]["deforestation_ha"]

        if not values:
            predictions.append({"year": base_year + h, "deforestation_ha": 0, "risk": RISK_LOW})
            continue

        weights = ENSEMBLE_WEIGHTS.get(h, (0.25, 0.25, 0.25, 0.25))
        w_map = {"trend": weights[0], "ml": weights[1], "pomdp": weights[2], "convlstm": weights[3]}
        total_w = sum(w_map[k] for k in values)
        if total_w < 1e-9:
            total_w = 1.0

        val = max(sum(values[k] * w_map[k] / total_w for k in values), 0.0)
        predictions.append({
            "year": base_year + h,
            "deforestation_ha": round(val, 3),
            "risk": _risk_level(val),
            "layer_contributions": {k: round(v, 3) for k, v in values.items()},
        })

    return {"available": True, "method": "ensemble", "predictions": predictions}


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
#  Main entry â€” forecast from timeline job
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

def forecast_from_timeline(
    job_id: str = None,
    horizon: int = 3,
    method: str = "ensemble",
) -> dict:
    """
    Generate forecast using data from a completed timeline job.
    Finds the latest timeline job automatically if none given.
    """
    horizon = min(max(horizon, 1), 5)

    if not job_id:
        job_id = _find_latest_timeline_job()
        if not job_id:
            return {
                "status": "no_data",
                "detail": "No hay an\u00e1lisis temporal completado. "
                          "Ejecuta primero un an\u00e1lisis Timeline.",
            }

    # Check if job is still running
    try:
        db_path = _get_db_path()
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
        conn.close()
        if row and row["status"] in ("queued", "running"):
            return {
                "status": "no_data",
                "detail": f"El timeline {job_id[:8]} a\u00fan est\u00e1 en proceso. "
                          "Espera a que termine antes de ejecutar la predicci\u00f3n.",
            }
    except Exception:
        pass

    series = _extract_timeline_series(job_id)
    if not series:
        return {
            "status": "no_data",
            "detail": "No se encontraron datos de timeline para "
                      f"job {job_id[:8]}. Ejecuta un an\u00e1lisis Timeline primero.",
        }

    base_year = series[-1]["year"]
    result: dict = {
        "status": "ok",
        "job_id": job_id,
        "horizon": horizon,
        "method": method,
        "years_analyzed": len(series),
        "period": f"{series[0]['year']}-{series[-1]['year']}",
        "historical": series,
    }

    if method in ("trend", "ensemble"):
        result["trend"] = _forecast_trend(series, horizon)

    if method in ("ml", "ensemble"):
        result["ml"] = _forecast_ml(series, horizon)

    if method in ("pomdp", "ensemble"):
        result["pomdp"] = _forecast_pomdp(series, horizon)

    if method in ("convlstm", "ensemble"):
        result["convlstm"] = _forecast_convlstm(series, horizon)

    if method == "ensemble":
        result["ensemble"] = _compute_ensemble(result, horizon, base_year)

    # Flatten predictions from primary method
    src_key = method if method != "ensemble" else "ensemble"
    preds = result.get(src_key, {}).get("predictions", [])
    result["predictions"] = preds
    result["total_deforestation_ha"] = [
        round(p.get("deforestation_ha", 0), 2) for p in preds
    ]
    if preds:
        result["overall_risk"] = preds[-1].get("risk", RISK_LOW)

    # â"€â"€ Spatial forecast: predict WHERE changes expand â"€â"€
    try:
        spatial = _generate_spatial_forecast(job_id, preds, series)
        if spatial:
            result["spatial_forecast"] = spatial
            logger.info("Spatial forecast generated: %s", list(spatial.keys()))
    except Exception as exc:
        logger.warning("Spatial forecast failed (non-fatal): %s", exc)

    return result


def get_forecast_status() -> dict:
    """Engine status summary."""
    model_exists = os.path.exists(_RF_MODEL_PATH)
    model_size = os.path.getsize(_RF_MODEL_PATH) if model_exists else 0

    db_path = _get_db_path()
    timeline_jobs = 0
    total_year_records = 0
    try:
        conn = sqlite3.connect(db_path)
        timeline_jobs = conn.execute(
            "SELECT COUNT(DISTINCT job_id) FROM analysis_results "
            "WHERE engine LIKE 'timeline_%' AND engine != 'timeline_summary'"
        ).fetchone()[0]
        total_year_records = conn.execute(
            "SELECT COUNT(*) FROM analysis_results "
            "WHERE engine LIKE 'timeline_%' AND engine != 'timeline_summary'"
        ).fetchone()[0]
        conn.close()
    except Exception:
        pass

    convlstm_exists = os.path.exists(_CONVLSTM_MODEL_PATH)
    convlstm_size = os.path.getsize(_CONVLSTM_MODEL_PATH) if convlstm_exists else 0

    return {
        "engine": "forecast_v2",
        "layers": ["trend", "ml", "pomdp", "convlstm"],
        "ml_model_trained": model_exists,
        "ml_model_size_kb": round(model_size / 1024, 1) if model_exists else 0,
        "convlstm_model_trained": convlstm_exists,
        "convlstm_model_size_kb": round(convlstm_size / 1024, 1) if convlstm_exists else 0,
        "timeline_jobs": timeline_jobs,
        "year_records": total_year_records,
    }

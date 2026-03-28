import json
import sys
import os
import traceback
from pathlib import Path
from datetime import datetime, timedelta

# Fix Windows console encoding for Unicode chars (arrows, emojis in logs)
if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import numpy as np
import rasterio

from .db.database import db
from .services.gee_service import GEEService
from .services.logger import log
from .engines.deforestation_engine import DeforestationEngine
from .engines.dynamic_world_engine import DynamicWorldEngine
from .engines.structure_engine import StructureEngine
from .engines.vegetation_engine import VegetationEngine
from .engines.hansen_engine import HansenEngine
from .engines.alerts_engine import AlertsEngine
from .engines.drivers_engine import DriversEngine
from .engines.fire_engine import FireEngine
from .engines.legal_engine import LegalEngine
from .services.gee_hansen import GEEHansenService
from .services.gee_alerts import GEEAlertsService
from .services.gee_drivers import GEEDriversService
from .services.gee_legal import GEELegalService
from .services.gee_sar import GEESARService
from .engines.sar_engine import SAREngine
from .engines.crossval_engine import CrossValEngine
from .services.firms_service import fetch_hotspots_for_aoi
from .engines.firms_engine import FIRMSEngine


# ── Per-job logging — persisted to DB so it survives --reload ──

def job_log(job_id: str, msg: str):
    """Append a log message to the job's log array in the DB + stderr."""
    ts = datetime.now().strftime("%H:%M:%S")
    entry = f"[{ts}] {msg}"
    print(f"[APEX] {entry}", flush=True)
    log.info(msg)
    try:
        with db.get_connection() as conn:
            row = conn.execute("SELECT logs FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if row:
                logs = json.loads(row["logs"] or "[]")
                logs.append(entry)
                conn.execute("UPDATE jobs SET logs = ? WHERE id = ?",
                             (json.dumps(logs, ensure_ascii=False), job_id))
                conn.commit()
    except Exception:
        pass  # never break pipeline for logging

def get_job_logs(job_id: str) -> list[str]:
    """Read logs from DB."""
    try:
        with db.get_connection() as conn:
            row = conn.execute("SELECT logs FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if row and row["logs"]:
                return json.loads(row["logs"])
    except Exception:
        pass
    return []


def update_job_status(job_id: str, status: str, progress: int, current_step: str):
    print(f"[APEX] Job {job_id} -- {progress}% -- {current_step}", flush=True)
    with db.get_connection() as conn:
        conn.execute(
            """UPDATE jobs SET status = ?, progress = ?, current_step = ? WHERE id = ?""",
            (status, progress, current_step, job_id),
        )
        if status in ("completed", "failed"):
            conn.execute(
                "UPDATE jobs SET completed_at = CURRENT_TIMESTAMP WHERE id = ?",
                (job_id,),
            )
        conn.commit()


def save_analysis_result(job_id: str, engine_name: str, geojson: dict, stats: dict):
    with db.get_connection() as conn:
        conn.execute(
            """INSERT INTO analysis_results (job_id, engine, geojson, stats_json)
               VALUES (?, ?, ?, ?)""",
            (job_id, engine_name, json.dumps(geojson), json.dumps(stats)),
        )
        conn.commit()


def _log_raster_bounds(jid: str, label: str, raster_path, aoi_bbox: dict, job_id: str = None):
    """Log raster bounds and verify intersection with AOI bbox."""
    try:
        with rasterio.open(raster_path) as src:
            b = src.bounds
            res_m = src.res[0] * 111320
            msg = (
                f"[{jid}] {label} raster bounds: lon=[{b.left:.4f},{b.right:.4f}] "
                f"lat=[{b.bottom:.4f},{b.top:.4f}] | {src.width}x{src.height}px @ {res_m:.1f}m"
            )
            if job_id:
                job_log(job_id, msg)
            else:
                log.info(msg)

            # Log AOI bbox para comparacion
            aoi_msg = (
                f"[{jid}] {label} AOI bbox: lon=[{aoi_bbox['min_lon']:.4f},{aoi_bbox['max_lon']:.4f}] "
                f"lat=[{aoi_bbox['min_lat']:.4f},{aoi_bbox['max_lat']:.4f}]"
            )
            if job_id:
                job_log(job_id, aoi_msg)

            # Verificar interseccion de bboxes completas (no solo centro)
            TOL = 0.01
            intersects = (
                b.left   <= aoi_bbox["max_lon"] + TOL and
                b.right  >= aoi_bbox["min_lon"] - TOL and
                b.bottom <= aoi_bbox["max_lat"] + TOL and
                b.top    >= aoi_bbox["min_lat"] - TOL
            )
            if not intersects:
                err_msg = (
                    f"[{jid}] RASTER {label} NO INTERSECTA EL AOI bbox "
                    f"(posible cache stale)"
                )
                if job_id:
                    job_log(job_id, err_msg)
                log.error(err_msg)
    except Exception as e:
        log.warning(f"[{jid}] No se pudo verificar raster {label}: {e}")


def _year_ago(date_str: str) -> str:
    d = datetime.strptime(date_str, "%Y-%m-%d")
    return (d - timedelta(days=365)).strftime("%Y-%m-%d")


def run_pipeline(job_id: str, req_data: dict):
    try:
        update_job_status(job_id, "running", 5, "Inicializando servicios...")

        aoi = req_data["aoi"]
        date_range = req_data["date_range"]
        engines_to_run = req_data["engines"]

        jid = job_id[:8]
        coords = aoi.get("coordinates", [[]])[0]
        bbox = {
            "min_lon": min(c[0] for c in coords),
            "max_lon": max(c[0] for c in coords),
            "min_lat": min(c[1] for c in coords),
            "max_lat": max(c[1] for c in coords),
        }
        job_log(job_id, f"[{jid}] Pipeline iniciado")
        job_log(job_id, f"[{jid}] AOI bbox: lon=[{bbox['min_lon']:.4f}, {bbox['max_lon']:.4f}] lat=[{bbox['min_lat']:.4f}, {bbox['max_lat']:.4f}]")
        job_log(job_id, f"[{jid}] AOI vértices: {len(coords)}")
        job_log(job_id, f"[{jid}] Engines: {engines_to_run} | Dates: {date_range}")

        gee = GEEService()
        gee.initialize()
        job_log(job_id, f"[{jid}] GEE inicializado OK")

        # ── Detect large AOI and split into groups ──
        aoi_groups = gee.split_aoi_into_groups(aoi)
        n_groups = len(aoi_groups)

        if n_groups > 1:
            job_log(job_id, f"[{jid}] AOI grande — dividido en {n_groups} grupos")
            update_job_status(job_id, "running", 8,
                              f"AOI grande: analizando en {n_groups} partes...")
            for gi, ga in enumerate(aoi_groups):
                gc = ga.get("coordinates", [[]])[0]
                if gc:
                    glo = [c[0] for c in gc]
                    gla = [c[1] for c in gc]
                    job_log(job_id,
                        f"[{jid}]   Grupo {gi+1}: type={ga.get('type','?')} "
                        f"lon=[{min(glo):.4f},{max(glo):.4f}] lat=[{min(gla):.4f},{max(gla):.4f}] "
                        f"vertices={len(gc)}")
        else:
            job_log(job_id, f"[{jid}] AOI dentro de limites — analisis directo")

        # ── Accumulators ──
        all_def_features: list = []
        all_veg_features: list = []
        all_ue_features: list = []
        all_str_features: list = []
        all_hansen_features: list = []
        all_alert_features: list = []
        all_driver_features: list = []
        all_fire_features: list = []
        all_sar_features: list = []
        all_firms_features: list = []
        all_firms_rows: list = []
        total_def_ha = 0.0
        total_ue_ha = 0.0
        total_hansen_ha = 0.0
        total_alert_ha = 0.0
        total_burned_ha = 0.0
        total_sar_ha = 0.0
        veg_class_totals: dict = {}
        groups_ok = 0

        for g_idx, group_aoi in enumerate(aoi_groups):
            g_label = f"Grupo {g_idx+1}/{n_groups}" if n_groups > 1 else ""
            base_pct = int(10 + (g_idx / n_groups) * 80)
            g_jid = f"{jid}" + (f" G{g_idx+1}" if n_groups > 1 else "")
            g_job_id = f"{job_id}_g{g_idx}"

            # Compute group-specific bbox for raster verification
            g_coords = group_aoi.get("coordinates", [[]])[0]
            if g_coords and n_groups > 1:
                g_bbox = {
                    "min_lon": min(c[0] for c in g_coords),
                    "max_lon": max(c[0] for c in g_coords),
                    "min_lat": min(c[1] for c in g_coords),
                    "max_lat": max(c[1] for c in g_coords),
                }
            else:
                g_bbox = bbox

            try:
                _g_start = datetime.now()
                # ── S2 Composite ──
                step_label = f"{g_label} Descargando Sentinel-2..." if g_label else "Descargando Sentinel-2..."
                update_job_status(job_id, "running", base_pct, step_label)
                job_log(job_id, f"[{g_jid}] Descargando S2 composite...")
                raster_path = gee.get_sentinel2_composite(
                    group_aoi, date_range[0], date_range[1],
                    job_id=f"{g_job_id}_s2"
                )
                _log_raster_bounds(g_jid, "S2", raster_path, g_bbox, job_id)

                # ── DW T1/T2 ──
                needs_dw = ("deforestation" in engines_to_run or
                            "urban_expansion" in engines_to_run or
                            "vegetation" in engines_to_run)
                dw_t1_path = None
                dw_t2_path = None

                if needs_dw:
                    t1_start = _year_ago(date_range[0])
                    t1_end = _year_ago(date_range[1])

                    step_label = f"{g_label} Descargando DW T1..." if g_label else "Descargando Dynamic World T1..."
                    update_job_status(job_id, "running", base_pct + 5, step_label)
                    job_log(job_id, f"[{g_jid}] DW T1: {t1_start} → {t1_end}")
                    dw_t1_path = gee.get_dynamic_world_classification(
                        group_aoi, t1_start, t1_end,
                        job_id=f"{g_job_id}_dw_t1"
                    )
                    _log_raster_bounds(g_jid, "DW-T1", dw_t1_path, g_bbox, job_id)

                    step_label = f"{g_label} Descargando DW T2..." if g_label else "Descargando Dynamic World T2..."
                    update_job_status(job_id, "running", base_pct + 10, step_label)
                    job_log(job_id, f"[{g_jid}] DW T2: {date_range[0]} → {date_range[1]}")
                    dw_t2_path = gee.get_dynamic_world_classification(
                        group_aoi, date_range[0], date_range[1],
                        job_id=f"{g_job_id}_dw_t2"
                    )
                    _log_raster_bounds(g_jid, "DW-T2", dw_t2_path, g_bbox, job_id)

                # ── Engines ──
                if "deforestation" in engines_to_run and dw_t1_path and dw_t2_path:
                    step_label = f"{g_label} Deforestación..." if g_label else "Ejecutando Deforestación..."
                    update_job_status(job_id, "running", base_pct + 15, step_label)
                    dw_engine = DynamicWorldEngine()
                    geo, stats = dw_engine.detect_deforestation(dw_t1_path, dw_t2_path)
                    all_def_features.extend(geo.get("features", []))
                    total_def_ha += stats.get("area_ha", 0)
                    job_log(job_id, f"[{g_jid}] ✓ Deforestación: {len(geo.get('features',[]))} feat, {stats.get('area_ha',0):.1f} ha")

                if "urban_expansion" in engines_to_run and dw_t1_path and dw_t2_path:
                    step_label = f"{g_label} Expansión urbana..." if g_label else "Ejecutando Expansión urbana..."
                    update_job_status(job_id, "running", base_pct + 18, step_label)
                    dw_engine = DynamicWorldEngine()
                    geo, stats = dw_engine.detect_urban_expansion(dw_t1_path, dw_t2_path)
                    all_ue_features.extend(geo.get("features", []))
                    total_ue_ha += stats.get("area_ha", 0)
                    job_log(job_id, f"[{g_jid}] ✓ Expansión urbana: {len(geo.get('features',[]))} feat, {stats.get('area_ha',0):.1f} ha")

                if "vegetation" in engines_to_run and dw_t2_path:
                    step_label = f"{g_label} Vegetación..." if g_label else "Ejecutando Vegetación..."
                    update_job_status(job_id, "running", base_pct + 20, step_label)
                    dw_engine = DynamicWorldEngine()
                    geo, stats = dw_engine.classify_from_raster(dw_t2_path)
                    all_veg_features.extend(geo.get("features", []))
                    for cls, pct in stats.get("classes", {}).items():
                        veg_class_totals[cls] = veg_class_totals.get(cls, 0) + pct
                    job_log(job_id, f"[{g_jid}] ✓ Vegetación: {len(geo.get('features',[]))} feat")

                if "structures" in engines_to_run:
                    step_label = f"{g_label} Estructuras..." if g_label else "Ejecutando Estructuras..."
                    update_job_status(job_id, "running", base_pct + 22, step_label)
                    engine = StructureEngine()
                    geo, stats = engine.predict_from_raster(raster_path, group_aoi)
                    all_str_features.extend(geo.get("features", []))
                    job_log(job_id, f"[{g_jid}] ✓ Estructuras: {len(geo.get('features',[]))} feat")

                # ── Hansen Global Forest Change ──
                if "hansen" in engines_to_run:
                    step_label = f"{g_label} Hansen Forest Loss..." if g_label else "Ejecutando Hansen Forest Loss..."
                    update_job_status(job_id, "running", base_pct + 25, step_label)
                    job_log(job_id, f"[{g_jid}] Descargando Hansen GFC...")
                    try:
                        hansen_svc = GEEHansenService()
                        hansen_svc.initialize()
                        hansen_path = hansen_svc.get_hansen_forest_loss(
                            group_aoi, job_id=f"{g_job_id}_hansen"
                        )
                        hansen_engine = HansenEngine()
                        geo, stats = hansen_engine.analyze_historical_loss(hansen_path)
                        all_hansen_features.extend(geo.get("features", []))
                        total_hansen_ha += stats.get("total_loss_ha", 0)
                        job_log(job_id, f"[{g_jid}] ✓ Hansen: {len(geo.get('features',[]))} feat, {stats.get('total_loss_ha',0):.1f} ha")
                    except Exception as e:
                        job_log(job_id, f"[{g_jid}] ⚠ Hansen error: {str(e)[:150]}")

                # ── Alerts GLAD/RADD ──
                if "alerts" in engines_to_run:
                    step_label = f"{g_label} Alertas GLAD/RADD..." if g_label else "Ejecutando Alertas GLAD/RADD..."
                    update_job_status(job_id, "running", base_pct + 28, step_label)
                    job_log(job_id, f"[{g_jid}] Descargando alertas GLAD/RADD...")
                    try:
                        alerts_svc = GEEAlertsService()
                        alerts_svc.initialize()
                        alerts_engine = AlertsEngine()

                        glad_geo, glad_stats = {"type": "FeatureCollection", "features": []}, {}
                        radd_geo, radd_stats = {"type": "FeatureCollection", "features": []}, {}

                        try:
                            glad_path = alerts_svc.get_glad_alerts(
                                group_aoi, date_range[0], date_range[1],
                                job_id=f"{g_job_id}_glad"
                            )
                            glad_geo, glad_stats = alerts_engine.process_glad_alerts(glad_path)
                            job_log(job_id, f"[{g_jid}] ✓ GLAD: {glad_stats.get('glad_count', 0)} alertas")
                        except Exception as e:
                            job_log(job_id, f"[{g_jid}] ⚠ GLAD error: {str(e)[:100]}")

                        try:
                            radd_path = alerts_svc.get_radd_alerts(
                                group_aoi, date_range[0], date_range[1],
                                job_id=f"{g_job_id}_radd"
                            )
                            radd_geo, radd_stats = alerts_engine.process_radd_alerts(radd_path)
                            job_log(job_id, f"[{g_jid}] ✓ RADD: {radd_stats.get('radd_count', 0)} alertas")
                        except Exception as e:
                            job_log(job_id, f"[{g_jid}] ⚠ RADD error: {str(e)[:100]}")

                        merged_geo, merged_stats = alerts_engine.merge_alerts(
                            glad_geo.get("features", []),
                            radd_geo.get("features", [])
                        )
                        all_alert_features.extend(merged_geo.get("features", []))
                        total_alert_ha += merged_stats.get("total_area_ha", 0)
                        job_log(job_id, f"[{g_jid}] ✓ Alertas merged: {merged_stats.get('total_alerts', 0)}")
                    except Exception as e:
                        job_log(job_id, f"[{g_jid}] ⚠ Alerts error: {str(e)[:150]}")

                # ── WRI Drivers of Forest Loss ──
                if "drivers" in engines_to_run:
                    step_label = f"{g_label} Drivers WRI..." if g_label else "Ejecutando Drivers WRI..."
                    update_job_status(job_id, "running", base_pct + 32, step_label)
                    job_log(job_id, f"[{g_jid}] Descargando WRI Drivers...")
                    try:
                        drivers_svc = GEEDriversService()
                        drivers_svc.initialize()
                        drivers_path = drivers_svc.get_conversion_drivers(
                            group_aoi, job_id=f"{g_job_id}_drivers"
                        )
                        drivers_engine = DriversEngine()
                        geo, stats = drivers_engine.classify_drivers(drivers_path)
                        all_driver_features.extend(geo.get("features", []))
                        job_log(job_id, f"[{g_jid}] ✓ Drivers: {len(geo.get('features',[]))} feat")
                    except Exception as e:
                        job_log(job_id, f"[{g_jid}] ⚠ Drivers error: {str(e)[:150]}")

                # ── MODIS Fire / Burned Area ──
                if "fire" in engines_to_run:
                    step_label = f"{g_label} Incendios MODIS..." if g_label else "Ejecutando Incendios MODIS..."
                    update_job_status(job_id, "running", base_pct + 35, step_label)
                    job_log(job_id, f"[{g_jid}] Descargando MODIS burned area...")
                    try:
                        fire_svc = GEEAlertsService()
                        fire_svc.initialize()
                        fire_path = fire_svc.get_modis_burned_area(
                            group_aoi, date_range[0], date_range[1],
                            job_id=f"{g_job_id}_fire"
                        )
                        fire_engine = FireEngine()
                        geo, stats = fire_engine.detect_burned_areas(fire_path)
                        all_fire_features.extend(geo.get("features", []))
                        total_burned_ha += stats.get("total_burned_ha", 0)
                        job_log(job_id, f"[{g_jid}] ✓ Incendios: {len(geo.get('features',[]))} feat, {stats.get('total_burned_ha',0):.1f} ha")
                    except Exception as e:
                        job_log(job_id, f"[{g_jid}] ⚠ Fire error: {str(e)[:150]}")

                # ── Sentinel-1 SAR Change Detection ──
                if "sar" in engines_to_run:
                    step_label = f"{g_label} SAR Sentinel-1..." if g_label else "Ejecutando SAR Sentinel-1..."
                    update_job_status(job_id, "running", base_pct + 38, step_label)
                    job_log(job_id, f"[{g_jid}] Descargando composites SAR...")
                    try:
                        sar_svc = GEESARService()
                        sar_svc.initialize()
                        sar_engine = SAREngine()

                        # T1 (baseline) and T2 (analysis) composites
                        t1_start = _year_ago(date_range[0])
                        t1_end = _year_ago(date_range[1])

                        sar_t1_path = sar_svc.get_sentinel1_composite(
                            group_aoi, t1_start, t1_end,
                            job_id=f"{g_job_id}_sar_t1"
                        )
                        job_log(job_id, f"[{g_jid}] SAR T1 descargado: {t1_start} -> {t1_end}")

                        sar_t2_path = sar_svc.get_sentinel1_composite(
                            group_aoi, date_range[0], date_range[1],
                            job_id=f"{g_job_id}_sar_t2"
                        )
                        job_log(job_id, f"[{g_jid}] SAR T2 descargado: {date_range[0]} -> {date_range[1]}")

                        geo, stats = sar_engine.detect_change_sar(sar_t1_path, sar_t2_path)
                        all_sar_features.extend(geo.get("features", []))
                        total_sar_ha += stats.get("total_change_ha", 0)
                        job_log(job_id, f"[{g_jid}] ✓ SAR: {len(geo.get('features',[]))} cambios, {stats.get('total_area_ha',0):.1f} ha")
                    except Exception as e:
                        job_log(job_id, f"[{g_jid}] ⚠ SAR error: {str(e)[:150]}")

                # ── NASA FIRMS NRT Active Fire Hotspots ──
                if "firms_hotspots" in engines_to_run:
                    step_label = f"{g_label} FIRMS hotspots NRT..." if g_label else "Consultando FIRMS hotspots NRT..."
                    update_job_status(job_id, "running", base_pct + 42, step_label)
                    job_log(job_id, f"[{g_jid}] Consultando NASA FIRMS NRT...")
                    try:
                        rows = fetch_hotspots_for_aoi(
                            group_aoi, date_range[0], date_range[1]
                        )
                        all_firms_rows.extend(rows)
                        job_log(job_id, f"[{g_jid}] ✓ FIRMS: {len(rows)} hotspots en AOI")
                    except Exception as e:
                        job_log(job_id, f"[{g_jid}] ⚠ FIRMS error: {str(e)[:150]}")

                _g_elapsed = (datetime.now() - _g_start).total_seconds()
                job_log(job_id, f"[{g_jid}] Grupo completado en {_g_elapsed:.1f}s")
                groups_ok += 1

            except Exception as e:
                job_log(job_id, f"[{g_jid}] ⚠ Error: {str(e)[:150]}")
                print(f"[APEX] Grupo {g_idx} falló: {e}", flush=True)
                if n_groups == 1:
                    raise  # single group = propagate
                continue  # multi-group = skip failed, continue

        # ── Normalize veg percentages across groups ──
        if veg_class_totals and n_groups > 1 and groups_ok > 0:
            veg_class_totals = {k: round(v / groups_ok, 1) for k, v in veg_class_totals.items()}

        # ── Save merged results ──
        if "deforestation" in engines_to_run:
            save_analysis_result(job_id, "deforestation",
                {"type": "FeatureCollection", "features": all_def_features},
                {"area_ha": round(total_def_ha, 1), "n_features": len(all_def_features), "n_groups": n_groups})

        if "vegetation" in engines_to_run:
            save_analysis_result(job_id, "vegetation",
                {"type": "FeatureCollection", "features": all_veg_features},
                {"classes": veg_class_totals, "n_features": len(all_veg_features), "n_groups": n_groups})

        if "urban_expansion" in engines_to_run:
            save_analysis_result(job_id, "urban_expansion",
                {"type": "FeatureCollection", "features": all_ue_features},
                {"area_ha": round(total_ue_ha, 1), "n_features": len(all_ue_features), "n_groups": n_groups})

        if "structures" in engines_to_run:
            save_analysis_result(job_id, "structures",
                {"type": "FeatureCollection", "features": all_str_features},
                {"count": len(all_str_features), "n_features": len(all_str_features), "n_groups": n_groups})

        if "hansen" in engines_to_run:
            hansen_loss_by_year = {}
            for f in all_hansen_features:
                yr = f.get("properties", {}).get("loss_year", 0)
                if yr > 0:
                    key = str(yr)
                    hansen_loss_by_year[key] = round(
                        hansen_loss_by_year.get(key, 0) + f["properties"]["area_ha"], 2
                    )
            all_tc = [f["properties"]["original_treecover_pct"]
                      for f in all_hansen_features
                      if f.get("properties", {}).get("original_treecover_pct", 0) > 0]
            avg_tc = round(float(np.mean(all_tc)), 1) if all_tc else 0
            save_analysis_result(job_id, "hansen",
                {"type": "FeatureCollection", "features": all_hansen_features},
                {"total_loss_ha": round(total_hansen_ha, 1),
                 "n_features": len(all_hansen_features),
                 "loss_by_year": hansen_loss_by_year,
                 "avg_treecover_pct": avg_tc,
                 "confidence": round(float(np.mean([
                     f["properties"]["confidence"] for f in all_hansen_features
                 ])), 3) if all_hansen_features else 0,
                 "n_groups": n_groups,
                 "source": "Hansen GFC v1.12 (UMD)"})

        if "alerts" in engines_to_run:
            save_analysis_result(job_id, "alerts",
                {"type": "FeatureCollection", "features": all_alert_features},
                {"total_alerts": len(all_alert_features),
                 "glad_count": sum(1 for f in all_alert_features if f.get("properties", {}).get("alert_type") == "glad"),
                 "radd_count": sum(1 for f in all_alert_features if f.get("properties", {}).get("alert_type") == "radd"),
                 "total_area_ha": round(total_alert_ha, 1),
                 "confirmed_count": sum(1 for f in all_alert_features if f.get("properties", {}).get("confidence") == "confirmed"),
                 "n_groups": n_groups,
                 "source": "GLAD-S2 + RADD (WUR)"})

        if "drivers" in engines_to_run and all_driver_features:
            driver_areas: dict = {}
            for f in all_driver_features:
                dc = f.get("properties", {}).get("driver_class", "?")
                driver_areas[dc] = driver_areas.get(dc, 0) + f["properties"].get("area_ha", 0)
            total_driver_area = sum(driver_areas.values()) or 1
            driver_pcts = {k: round(100 * v / total_driver_area, 1) for k, v in driver_areas.items()}
            save_analysis_result(job_id, "drivers",
                {"type": "FeatureCollection", "features": all_driver_features},
                {"drivers": driver_pcts,
                 "n_features": len(all_driver_features),
                 "dominant_driver": max(driver_areas, key=driver_areas.get) if driver_areas else "?",
                 "n_groups": n_groups,
                 "source": "WRI / Google DeepMind"})

        if "fire" in engines_to_run:
            fire_deforest_pct = 0
            if all_fire_features and all_def_features:
                try:
                    fire_eng = FireEngine()
                    corr = fire_eng.correlate_fire_deforestation(all_fire_features, all_def_features)
                    fire_deforest_pct = corr.get("fire_related_deforestation_pct", 0)
                except Exception:
                    pass
            save_analysis_result(job_id, "fire",
                {"type": "FeatureCollection", "features": all_fire_features},
                {"total_burned_ha": round(total_burned_ha, 1),
                 "fire_count": len(all_fire_features),
                 "fire_related_deforestation_pct": fire_deforest_pct,
                 "n_groups": n_groups,
                 "source": "MODIS MCD64A1"})

        if "sar" in engines_to_run:
            # Fusion with optical deforestation results
            if all_sar_features and all_def_features:
                try:
                    sar_eng = SAREngine()
                    fused_features = sar_eng.fuse_optical_sar(
                        all_def_features, all_sar_features
                    )
                    # Update deforestation features with fusion info
                    all_def_features = fused_features
                    n_sar_confirmed = sum(1 for f in fused_features if f.get("properties", {}).get("sar_confirmed"))
                    job_log(job_id, f"[{jid}] ✓ SAR-Optical fusion: {n_sar_confirmed} SAR-confirmed")
                except Exception as e:
                    job_log(job_id, f"[{jid}] ⚠ SAR fusion error: {str(e)[:100]}")

            save_analysis_result(job_id, "sar",
                {"type": "FeatureCollection", "features": all_sar_features},
                {"total_change_ha": round(total_sar_ha, 1),
                 "sar_change_count": len(all_sar_features),
                 "high_confidence_count": sum(
                     1 for f in all_sar_features
                     if isinstance(f.get("properties", {}).get("confidence"), (int, float))
                     and f["properties"]["confidence"] >= 0.7
                 ),
                 "n_groups": n_groups,
                 "source": "Sentinel-1 SAR (log-ratio change detection)"})

        # ── NASA FIRMS NRT results ──
        if "firms_hotspots" in engines_to_run:
            try:
                firms_eng = FIRMSEngine()
                firms_geo, firms_stats = firms_eng.process_detections(all_firms_rows)

                # Also generate cluster polygons
                clusters = firms_eng.cluster_detections(all_firms_rows)
                if clusters:
                    firms_stats["cluster_count"] = len(clusters)
                    firms_stats["clusters"] = clusters  # stored in stats for optional use
                    # Append cluster features to the feature collection
                    firms_geo["features"].extend(clusters)
                    job_log(job_id, f"[{jid}] FIRMS clusters: {len(clusters)} agrupaciones")

                # Cross-correlate FIRMS hotspots with MODIS burned area if both ran
                if all_fire_features and firms_geo.get("features"):
                    hotspot_pts = [
                        f for f in firms_geo["features"]
                        if f["geometry"]["type"] == "Point"
                    ]
                    corr_count = 0
                    from shapely.geometry import shape as shp, Point as Pt
                    for fire_feat in all_fire_features:
                        fire_poly = shp(fire_feat["geometry"])
                        for hp in hotspot_pts:
                            hp_pt = Pt(
                                hp["geometry"]["coordinates"][0],
                                hp["geometry"]["coordinates"][1],
                            )
                            if fire_poly.contains(hp_pt):
                                corr_count += 1
                                break
                    firms_stats["modis_crossmatch_count"] = corr_count
                    firms_stats["modis_crossmatch_pct"] = round(
                        100 * corr_count / len(all_fire_features), 1
                    ) if all_fire_features else 0
                    job_log(job_id, f"[{jid}] FIRMS↔MODIS match: {corr_count}/{len(all_fire_features)} burned areas")

                save_analysis_result(job_id, "firms_hotspots", firms_geo, firms_stats)
                job_log(job_id, f"[{jid}] ✓ FIRMS NRT: {firms_stats.get('hotspot_count', 0)} hotspots, {firms_stats.get('total_frp_mw', 0)} MW FRP")
            except Exception as e:
                job_log(job_id, f"[{jid}] ⚠ FIRMS post-processing error: {str(e)[:150]}")

        # ── Post-processing: MapBiomas cross-validation ──
        if "deforestation" in engines_to_run and all_def_features:
            try:
                job_log(job_id, f"[{jid}] Validación cruzada DW vs MapBiomas...")
                legal_svc_cv = GEELegalService()
                legal_svc_cv.initialize()
                analysis_year = int(date_range[1][:4])
                mb_path = legal_svc_cv.get_mapbiomas(aoi, analysis_year, job_id=f"{job_id}_mb")
                crossval = CrossValEngine()
                cv_geo, cv_stats = crossval.cross_validate(
                    {"type": "FeatureCollection", "features": all_def_features},
                    mb_path,
                )
                save_analysis_result(job_id, "crossval", cv_geo, cv_stats)
                job_log(job_id, f"[{jid}] ✓ CrossVal: acuerdo={cv_stats.get('agreement_pct', 0)}%")
            except Exception as e:
                job_log(job_id, f"[{jid}] ⚠ CrossVal error: {str(e)[:150]}")

        # ── Post-processing: Legal context (ANP intersection) ──
        if any(e in engines_to_run for e in ["deforestation", "urban_expansion", "hansen"]):
            try:
                job_log(job_id, f"[{jid}] Verificando contexto legal (ANPs)...")
                legal_svc = GEELegalService()
                legal_svc.initialize()
                anps = legal_svc.get_protected_areas(aoi)

                legal_engine = LegalEngine()
                legal_geo, legal_stats = legal_engine.check_anp_intersection(aoi, anps)

                if all_def_features and anps.get("features"):
                    all_def_features = legal_engine.tag_features_with_anp(
                        all_def_features, anps.get("features", [])
                    )
                    if "deforestation" in engines_to_run:
                        save_analysis_result(job_id, "deforestation",
                            {"type": "FeatureCollection", "features": all_def_features},
                            {"area_ha": round(total_def_ha, 1), "n_features": len(all_def_features), "n_groups": n_groups})

                save_analysis_result(job_id, "legal_context", legal_geo, legal_stats)
                job_log(job_id, f"[{jid}] ✓ Legal: intersects_anp={legal_stats.get('intersects_anp', False)}")
            except Exception as e:
                job_log(job_id, f"[{jid}] ⚠ Legal context error: {str(e)[:150]}")

        summary = (f"{n_groups} grupo(s), {groups_ok} exitoso(s)"
                   if n_groups > 1 else "Analisis completado exitosamente!")

        # ── Final summary logging ──
        results_summary = []
        if all_def_features: results_summary.append(f"deforestacion={len(all_def_features)}feat/{total_def_ha:.1f}ha")
        if all_veg_features: results_summary.append(f"vegetacion={len(all_veg_features)}feat")
        if all_ue_features: results_summary.append(f"expansion_urbana={len(all_ue_features)}feat/{total_ue_ha:.1f}ha")
        if all_hansen_features: results_summary.append(f"hansen={len(all_hansen_features)}feat/{total_hansen_ha:.1f}ha")
        if all_alert_features: results_summary.append(f"alertas={len(all_alert_features)}")
        if all_fire_features: results_summary.append(f"incendios={len(all_fire_features)}feat/{total_burned_ha:.1f}ha")
        if all_sar_features: results_summary.append(f"sar={len(all_sar_features)}feat/{total_sar_ha:.1f}ha")
        if all_firms_rows: results_summary.append(f"firms_nrt={len(all_firms_rows)}hotspots")
        if all_driver_features: results_summary.append(f"drivers={len(all_driver_features)}feat")
        if results_summary:
            job_log(job_id, f"[{jid}] Resultados: {', '.join(results_summary)}")

        job_log(job_id, f"[{jid}] Pipeline completado -- {summary}")
        update_job_status(job_id, "completed", 100, summary)

        # ── Auto-send email report if notify_email was provided ──
        _send_completion_email(job_id, "manual")

    except Exception as e:
        tb = traceback.format_exc()
        print(f"Pipeline error: {tb}", flush=True)
        job_log(job_id, f"[{job_id[:8]}] ❌ ERROR: {str(e)}")
        update_job_status(job_id, "failed", 0, f"Error: {str(e)}")


def _send_completion_email(job_id: str, analysis_type: str = "manual"):
    """
    Send an email report when the pipeline finishes, if notify_email is set.
    Called at the end of run_pipeline and run_timeline_pipeline.
    """
    jid = job_id[:8]
    log.info("[%s] _send_completion_email called (type=%s)", jid, analysis_type)
    try:
        with db.get_connection() as conn:
            job_row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if not job_row:
                log.warning("[%s] Email skip — job not found in DB", jid)
                return

            notify_email = None
            try:
                notify_email = job_row["notify_email"]
            except (IndexError, KeyError):
                log.info("[%s] Email skip — notify_email column missing", jid)
                return

            if not notify_email:
                log.info("[%s] Email skip — notify_email is empty/null", jid)
                return

            log.info("[%s] notify_email=%s — preparing report...", jid, notify_email)

            # Extract date range from job
            date_range = None
            try:
                ds = job_row["date_range_start"]
                de = job_row["date_range_end"]
                if ds and de:
                    date_range = [ds, de]
            except (IndexError, KeyError):
                pass

            results = conn.execute(
                "SELECT * FROM analysis_results WHERE job_id = ?", (job_id,)
            ).fetchall()

        if not results:
            log.warning("[%s] Email skip — no analysis_results rows for this job", jid)
            return

        layers = {}
        for row in results:
            layers[row["engine"]] = {
                "geojson": json.loads(row["geojson"]) if row["geojson"] else {},
                "stats": json.loads(row["stats_json"]) if row["stats_json"] else {},
            }

        log.info("[%s] Building report — %d engines: %s, date_range=%s",
                 jid, len(layers), list(layers.keys()), date_range)

        from .services.alert_service import AlertService
        svc = AlertService()
        success = svc.send_analysis_report_email(
            to_email=notify_email,
            job_id=job_id,
            results=layers,
            analysis_type=analysis_type,
            date_range=date_range,
        )

        if success:
            log.info("[%s] Completion email SENT to %s", jid, notify_email)
            job_log(job_id, f"[{jid}] Email de reporte enviado a {notify_email}")
        else:
            log.warning("[%s] Completion email FAILED to %s", jid, notify_email)
            job_log(job_id, f"[{jid}] Error al enviar email a {notify_email}")

    except Exception as exc:
        log.error("[%s] _send_completion_email error: %s", jid, exc, exc_info=True)

def run_timeline_pipeline(job_id: str, req_data: dict):
    """
    Analiza el mismo AOI para cada año en el rango usando TODOS los motores
    seleccionados. Guarda resultados por año con engine='timeline_{year}'.
    """
    try:
        update_job_status(job_id, "running", 5, "Inicializando timeline...")

        aoi = req_data["aoi"]
        start_year = req_data.get("start_year", 2018)
        end_year = req_data.get("end_year", 2025)
        season = req_data.get("season", "dry")
        engines = req_data.get("engines", ["deforestation", "urban_expansion"])

        jid = job_id[:8]
        job_log(job_id, f"[{jid}] Timeline iniciado: {start_year}→{end_year}, season={season}")
        job_log(job_id, f"[{jid}] Engines: {engines}")

        SEASONS = {
            "dry": ("01-01", "03-31"),
            "wet": ("06-01", "09-30"),
            "annual": ("01-01", "12-31"),
        }
        s_start, s_end = SEASONS.get(season, SEASONS["dry"])

        gee = GEEService()
        gee.initialize()
        dw_engine = DynamicWorldEngine()

        # Check which engine categories are needed
        needs_dw = any(e in engines for e in ["deforestation", "urban_expansion", "vegetation"])
        needs_s2 = "structures" in engines
        needs_sar = "sar" in engines

        years = list(range(start_year, end_year + 1))
        total_steps = len(years)
        timeline_results = {}

        # ── Phase 1: Download DW rasters per year (needed for deforestation/veg/urban) ──
        rasters_dw = {}
        if needs_dw:
            for i, year in enumerate(years):
                pct = int(8 + (i / total_steps) * 20)
                update_job_status(job_id, "running", pct,
                                  f"DW {year} ({i + 1}/{total_steps})...")
                try:
                    path = gee.get_dynamic_world_classification(
                        aoi,
                        f"{year}-{s_start}",
                        f"{year}-{s_end}",
                        job_id=f"{job_id}_dw_{year}",
                    )
                    rasters_dw[year] = path
                    job_log(job_id, f"[{jid}] DW {year} descargado")
                except Exception as e:
                    job_log(job_id, f"[{jid}] ⚠ DW {year} error: {e}")
                    continue

        # ── Phase 1b: Hansen (one-time download, data already per-year) ──
        hansen_features_by_year: dict[int, list] = {}
        hansen_total_loss = 0.0
        if "hansen" in engines:
            update_job_status(job_id, "running", 30, "Descargando Hansen GFC...")
            try:
                hansen_svc = GEEHansenService()
                hansen_svc.initialize()
                hansen_path = hansen_svc.get_hansen_forest_loss(
                    aoi, job_id=f"{job_id}_hansen"
                )
                hansen_eng = HansenEngine()
                hansen_geo, hansen_stats = hansen_eng.analyze_historical_loss(hansen_path)
                hansen_total_loss = hansen_stats.get("total_loss_ha", 0)
                # Group features by loss_year
                for f in hansen_geo.get("features", []):
                    yr = f.get("properties", {}).get("loss_year", 0)
                    # Hansen loss_year is offset from 2000
                    if yr > 0:
                        actual_year = 2000 + yr if yr < 100 else yr
                        if actual_year not in hansen_features_by_year:
                            hansen_features_by_year[actual_year] = []
                        hansen_features_by_year[actual_year].append(f)
                job_log(job_id, f"[{jid}] Hansen descargado: {len(hansen_geo.get('features',[]))} feat total")
            except Exception as e:
                job_log(job_id, f"[{jid}] ⚠ Hansen error: {e}")

        # ── Phase 1c: Drivers WRI (one-time, static dataset) ──
        driver_features_all = []
        if "drivers" in engines:
            update_job_status(job_id, "running", 32, "Descargando Drivers WRI...")
            try:
                drivers_svc = GEEDriversService()
                drivers_svc.initialize()
                drivers_path = drivers_svc.get_conversion_drivers(
                    aoi, job_id=f"{job_id}_drivers"
                )
                drivers_eng = DriversEngine()
                drv_geo, drv_stats = drivers_eng.classify_drivers(drivers_path)
                driver_features_all = drv_geo.get("features", [])
                job_log(job_id, f"[{jid}] Drivers descargado: {len(driver_features_all)} feat")
            except Exception as e:
                job_log(job_id, f"[{jid}] ⚠ Drivers error: {e}")

        # ── Phase 2: Per-year analysis with ALL engines ──
        sorted_years = sorted(rasters_dw.keys()) if needs_dw else list(range(start_year, end_year + 1))
        for i, year in enumerate(sorted_years):
            pct = int(35 + (i / len(sorted_years)) * 55)
            update_job_status(job_id, "running", pct,
                              f"Analizando {year} ({i + 1}/{len(sorted_years)})...")

            year_start = f"{year}-{s_start}"
            year_end = f"{year}-{s_end}"
            year_result: dict = {"year": year}

            # ── DW-based: deforestation, urban_expansion (need T1 year) ──
            if i > 0 and needs_dw:
                t1_year = sorted_years[i - 1]
                year_result["baseline_year"] = t1_year

                if "deforestation" in engines and t1_year in rasters_dw and year in rasters_dw:
                    try:
                        geo_def, stats_def = dw_engine.detect_deforestation(
                            rasters_dw[t1_year], rasters_dw[year]
                        )
                        year_result["deforestation"] = {"geojson": geo_def, "stats": stats_def}
                        job_log(job_id, f"[{jid}] {year} Deforestación: {stats_def.get('area_ha', 0):.1f} ha")
                    except Exception as e:
                        job_log(job_id, f"[{jid}] {year} ⚠ Deforest error: {e}")

                if "urban_expansion" in engines and t1_year in rasters_dw and year in rasters_dw:
                    try:
                        geo_ue, stats_ue = dw_engine.detect_urban_expansion(
                            rasters_dw[t1_year], rasters_dw[year]
                        )
                        year_result["urban_expansion"] = {"geojson": geo_ue, "stats": stats_ue}
                        job_log(job_id, f"[{jid}] {year} Exp. urbana: {stats_ue.get('area_ha', 0):.1f} ha")
                    except Exception as e:
                        job_log(job_id, f"[{jid}] {year} ⚠ Urban exp error: {e}")

            # ── Vegetation classification for this year ──
            if needs_dw and year in rasters_dw:
                try:
                    geo_veg, stats_veg = dw_engine.classify_from_raster(rasters_dw[year])
                    year_result["vegetation"] = {"geojson": geo_veg, "stats": stats_veg}
                except Exception as e:
                    job_log(job_id, f"[{jid}] {year} ⚠ Veg error: {e}")

            # ── Hansen (filter pre-computed features by year) ──
            if "hansen" in engines and year in hansen_features_by_year:
                yr_feats = hansen_features_by_year[year]
                yr_ha = sum(f.get("properties", {}).get("area_ha", 0) for f in yr_feats)
                year_result["hansen"] = {
                    "geojson": {"type": "FeatureCollection", "features": yr_feats},
                    "stats": {"loss_ha": round(yr_ha, 2), "n_features": len(yr_feats)},
                }
                job_log(job_id, f"[{jid}] {year} Hansen: {len(yr_feats)} feat, {yr_ha:.1f} ha")

            # ── Drivers WRI (same for all years — static) ──
            if "drivers" in engines and driver_features_all:
                year_result["drivers"] = {
                    "geojson": {"type": "FeatureCollection", "features": driver_features_all},
                    "stats": {"n_features": len(driver_features_all)},
                }

            # ── Alerts GLAD/RADD (per year) ──
            if "alerts" in engines:
                try:
                    alerts_svc = GEEAlertsService()
                    alerts_svc.initialize()
                    alerts_eng = AlertsEngine()
                    glad_feats, radd_feats = [], []

                    try:
                        glad_path = alerts_svc.get_glad_alerts(
                            aoi, year_start, year_end,
                            job_id=f"{job_id}_glad_{year}"
                        )
                        glad_geo, glad_stats = alerts_eng.process_glad_alerts(glad_path)
                        glad_feats = glad_geo.get("features", [])
                    except Exception as e_glad:
                        job_log(job_id, f"[{jid}] {year} GLAD: sin datos ({e_glad})")
                    try:
                        radd_path = alerts_svc.get_radd_alerts(
                            aoi, year_start, year_end,
                            job_id=f"{job_id}_radd_{year}"
                        )
                        radd_geo, radd_stats = alerts_eng.process_radd_alerts(radd_path)
                        radd_feats = radd_geo.get("features", [])
                    except Exception as e_radd:
                        job_log(job_id, f"[{jid}] {year} RADD: sin datos ({e_radd})")

                    merged_geo, merged_stats = alerts_eng.merge_alerts(glad_feats, radd_feats)
                    n_alerts = merged_stats.get('total_alerts', 0)
                    if merged_geo.get("features"):
                        year_result["alerts"] = {"geojson": merged_geo, "stats": merged_stats}
                    job_log(job_id, f"[{jid}] {year} Alertas: {n_alerts}")
                except Exception as e:
                    job_log(job_id, f"[{jid}] {year} ⚠ Alerts error: {e}")

            # ── MODIS Fire / Burned Area (full year — fires happen year-round) ──
            if "fire" in engines:
                try:
                    fire_svc = GEEAlertsService()
                    fire_svc.initialize()
                    fire_year_start = f"{year}-01-01"
                    fire_year_end = f"{year}-12-31"
                    fire_path = fire_svc.get_modis_burned_area(
                        aoi, fire_year_start, fire_year_end,
                        job_id=f"{job_id}_fire_{year}"
                    )
                    fire_eng = FireEngine()
                    fire_geo, fire_stats = fire_eng.detect_burned_areas(fire_path)
                    burned_ha = fire_stats.get('total_burned_ha', 0)
                    if fire_geo.get("features"):
                        year_result["fire"] = {"geojson": fire_geo, "stats": fire_stats}
                    job_log(job_id, f"[{jid}] {year} Incendios MODIS (año completo): {burned_ha:.1f} ha, {fire_stats.get('fire_count',0)} zonas")
                except Exception as e:
                    job_log(job_id, f"[{jid}] {year} ⚠ Fire error: {e}")

            # ── SAR Sentinel-1 Change Detection (per year) ──
            if "sar" in engines and i > 0:
                try:
                    sar_svc = GEESARService()
                    sar_svc.initialize()
                    sar_eng = SAREngine()

                    prev_year = sorted_years[i - 1]
                    sar_t1_path = sar_svc.get_sentinel1_composite(
                        aoi, f"{prev_year}-{s_start}", f"{prev_year}-{s_end}",
                        job_id=f"{job_id}_sar_t1_{year}"
                    )
                    sar_t2_path = sar_svc.get_sentinel1_composite(
                        aoi, year_start, year_end,
                        job_id=f"{job_id}_sar_t2_{year}"
                    )
                    sar_geo, sar_stats = sar_eng.detect_change_sar(sar_t1_path, sar_t2_path)
                    sar_ha = sar_stats.get('total_change_ha', 0)
                    if sar_geo.get("features"):
                        year_result["sar"] = {"geojson": sar_geo, "stats": sar_stats}
                    job_log(job_id, f"[{jid}] {year} SAR: {sar_ha:.1f} ha")
                except Exception as e:
                    job_log(job_id, f"[{jid}] {year} ⚠ SAR error: {e}")

            # ── Structures — motor deshabilitado (requiere <1m/px, S2=10m) ──
            # Se omite en timeline para evitar descarga S2 innecesaria

            # ── NASA FIRMS Hotspots (full year — fire detections year-round) ──
            if "firms_hotspots" in engines:
                try:
                    firms_year_start = f"{year}-01-01"
                    firms_year_end = f"{year}-12-31"
                    rows = fetch_hotspots_for_aoi(aoi, firms_year_start, firms_year_end)
                    if rows:
                        firms_eng = FIRMSEngine()
                        firms_geo, firms_stats = firms_eng.process_detections(rows)
                        clusters = firms_eng.cluster_detections(rows)
                        if clusters:
                            firms_stats["cluster_count"] = len(clusters)
                            firms_geo["features"].extend(clusters)
                        year_result["firms_hotspots"] = {"geojson": firms_geo, "stats": firms_stats}
                    job_log(job_id, f"[{jid}] {year} FIRMS (año completo): {len(rows)} hotspots")
                except Exception as e:
                    job_log(job_id, f"[{jid}] {year} ⚠ FIRMS error: {e}")

            timeline_results[str(year)] = year_result
            save_analysis_result(job_id, f"timeline_{year}",
                                 {"timeline": year_result}, {})

        # ── Anomaly detection (for DW-based engines) ──
        anomalies = dw_engine.detect_anomalies(timeline_results)
        job_log(job_id, f"[{jid}] {len(anomalies)} anomalías detectadas: {[a['year'] for a in anomalies]}")

        # ── Cumulative stats ──
        all_years_sorted = sorted(int(k) for k in timeline_results.keys())
        total_def_ha = sum(
            v.get("deforestation", {}).get("stats", {}).get("area_ha", 0)
            for v in timeline_results.values()
        )
        total_ue_ha = sum(
            v.get("urban_expansion", {}).get("stats", {}).get("area_ha", 0)
            for v in timeline_results.values()
        )
        total_burned_ha = sum(
            v.get("fire", {}).get("stats", {}).get("total_burned_ha", 0)
            for v in timeline_results.values()
        )
        total_firms_hotspots = sum(
            v.get("firms_hotspots", {}).get("stats", {}).get("hotspot_count", 0)
            for v in timeline_results.values()
        )
        total_hansen_ha_timeline = sum(
            v.get("hansen", {}).get("stats", {}).get("loss_ha", 0)
            for v in timeline_results.values()
        )
        total_sar_ha = sum(
            v.get("sar", {}).get("stats", {}).get("total_change_ha", 0)
            for v in timeline_results.values()
        )
        total_alerts_count = sum(
            v.get("alerts", {}).get("stats", {}).get("total_alerts", 0)
            for v in timeline_results.values()
        )

        first_yr_key = str(all_years_sorted[1]) if len(all_years_sorted) > 1 else str(all_years_sorted[0])
        last_yr_key = str(all_years_sorted[-1])
        first_year_veg = timeline_results.get(first_yr_key, {}).get(
            "vegetation", {}).get("stats", {}).get("classes", {})
        last_year_veg = timeline_results.get(last_yr_key, {}).get(
            "vegetation", {}).get("stats", {}).get("classes", {})

        cumulative = {
            "total_deforestation_ha": round(total_def_ha, 1),
            "total_urban_expansion_ha": round(total_ue_ha, 1),
            "total_burned_ha": round(total_burned_ha, 1),
            "total_firms_hotspots": total_firms_hotspots,
            "total_hansen_loss_ha": round(total_hansen_ha_timeline, 1),
            "total_sar_change_ha": round(total_sar_ha, 1),
            "total_alerts": total_alerts_count,
            "bosque_denso_change_pct": round(
                last_year_veg.get("bosque_denso", 0) - first_year_veg.get("bosque_denso", 0), 1
            ),
            "urbano_change_pct": round(
                last_year_veg.get("urbano", 0) - first_year_veg.get("urbano", 0), 1
            ),
            "years_analyzed": len(all_years_sorted) - 1 if len(all_years_sorted) > 1 else len(all_years_sorted),
            "period": f"{all_years_sorted[0]}-{all_years_sorted[-1]}" if len(all_years_sorted) > 1 else str(all_years_sorted[0]),
            "engines_used": engines,
        }

        # Guardar resumen de la serie temporal
        summary = {
            "years": all_years_sorted,
            "season": season,
            "timeline": timeline_results,
            "anomalies": anomalies,
            "cumulative": cumulative,
        }
        save_analysis_result(job_id, "timeline_summary", summary, {})
        job_log(job_id, f"[{jid}] ✅ Timeline completado: {len(timeline_results)} años, engines={engines}")
        update_job_status(job_id, "completed", 100,
                          f"Timeline completado: {len(timeline_results)} años con {len(engines)} motores")

        # ── Auto-send email report if notify_email was provided ──
        _send_completion_email(job_id, "timeline")

    except Exception as e:
        tb = traceback.format_exc()
        print(f"Timeline pipeline error: {tb}", flush=True)
        job_log(job_id, f"[{job_id[:8]}] ❌ ERROR: {str(e)}")
        update_job_status(job_id, "failed", 0, f"Error: {str(e)}")
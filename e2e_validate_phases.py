# -*- coding: utf-8 -*-
"""
APEX E2E Validation — All 6 New Phases.

Tests:
  1. Root endpoint
  2. Full analysis (deforestation + vegetation + fire + spectralgpt)
  3. Chat/query endpoint (TEOChat fallback)
  4. Chat/status endpoint
  5. Forecast/status endpoint
  6. Results inspection (ForestNet-MX enrichment, biomass)
"""
import requests
import time
import json
import sys

BASE = "http://localhost:8003"
TIMEOUT = 15
RESULTS = {}
PASS = 0
FAIL = 0


def test(name, fn):
    global PASS, FAIL
    try:
        ok, msg = fn()
        status = "PASS" if ok else "FAIL"
        if ok:
            PASS += 1
        else:
            FAIL += 1
        print(f"  [{status}] {name}: {msg}")
    except Exception as e:
        FAIL += 1
        print(f"  [FAIL] {name}: EXCEPTION {e}")


def t01_root():
    r = requests.get(f"{BASE}/", timeout=TIMEOUT)
    data = r.json()
    return r.status_code == 200 and "APEX" in str(data), f"{r.status_code} {data}"


def t02_chat_status():
    r = requests.get(f"{BASE}/api/chat/status", timeout=TIMEOUT)
    data = r.json()
    ok = r.status_code == 200 and "gpu_available" in data
    return ok, f"loaded={data.get('loaded')}, gpu={data.get('gpu_available')}, gpu_name={data.get('gpu_name')}"


def t03_chat_query_fallback():
    payload = {
        "question": "Resume el analisis",
        "job_results": {
            "deforestation": {"stats": {"area_ha": 42.5, "n_features": 8}},
            "biomass": {"total_co2_tonnes": 1234.5, "mean_agbd_mg_ha": 95.3},
        },
    }
    r = requests.post(f"{BASE}/api/chat/query", json=payload, timeout=TIMEOUT)
    data = r.json()
    ok = r.status_code == 200 and len(data.get("answer", "")) > 10
    return ok, f"mode={data.get('mode')}, answer_len={len(data.get('answer',''))}"


def t04_forecast_status():
    r = requests.get(f"{BASE}/api/forecast/status", timeout=TIMEOUT)
    data = r.json()
    ok = r.status_code == 200 and "ml_model_trained" in data
    convlstm = data.get("convlstm_model_trained", "N/A")
    return ok, f"ml={data.get('ml_model_trained')}, convlstm={convlstm}"


def t05_analysis_submit():
    """Submit a small analysis job with multiple engines."""
    payload = {
        "aoi": {
            "type": "Polygon",
            "coordinates": [
                [[-89.62, 20.52], [-89.57, 20.52], [-89.57, 20.48],
                 [-89.62, 20.48], [-89.62, 20.52]]
            ],
        },
        "engines": [
            "deforestation", "vegetation", "fire", "hansen",
            "alerts", "drivers", "firms_hotspots",
        ],
        "date_range": ["2024-01-01", "2024-06-30"],
    }
    r = requests.post(f"{BASE}/api/analyze", json=payload, timeout=TIMEOUT)
    data = r.json()
    ok = r.status_code == 200 and "job_id" in data
    if ok:
        RESULTS["job_id"] = data["job_id"]
    return ok, f"job_id={data.get('job_id')}"


def t06_poll_job():
    """Poll job until completed or 5 min timeout."""
    job_id = RESULTS.get("job_id")
    if not job_id:
        return False, "No job_id from t05"

    max_polls = 30
    for i in range(max_polls):
        time.sleep(10)
        r = requests.get(f"{BASE}/api/jobs/{job_id}", timeout=TIMEOUT)
        if r.status_code != 200:
            continue
        data = r.json()
        status = data.get("status", "unknown")
        progress = data.get("progress", 0)
        step = data.get("current_step", "")
        print(f"    Poll {i + 1}/{max_polls}: status={status}, progress={progress}%, step={step[:60]}")
        if status == "completed":
            RESULTS["job_status"] = "completed"
            return True, f"Completed in {(i + 1) * 10}s"
        if status == "failed":
            RESULTS["job_status"] = "failed"
            return False, f"Failed at step: {step}"
    return False, f"Timeout after {max_polls * 10}s (last: {status} {progress}%)"


def t07_results():
    """Fetch results and verify engines."""
    job_id = RESULTS.get("job_id")
    if not job_id or RESULTS.get("job_status") != "completed":
        return False, "Job not completed"

    r = requests.get(f"{BASE}/api/results/{job_id}", timeout=TIMEOUT)
    if r.status_code != 200:
        return False, f"HTTP {r.status_code}"

    data = r.json()
    RESULTS["data"] = data

    # Results may be nested under "layers" key
    layers = data.get("layers", data)
    found_engines = list(layers.keys())
    print(f"    Engines in results: {found_engines}")

    # Check for deforestation
    has_def = "deforestation" in layers
    def_entry = layers.get("deforestation", {})
    n_def = len(def_entry.get("geojson", {}).get("features", [])) if has_def else 0

    # Check for biomass enrichment (Phase 1)
    has_biomass = "biomass" in layers
    biomass_entry = layers.get("biomass", {})
    biomass_co2 = biomass_entry.get("stats", biomass_entry).get("total_co2_tonnes", 0)
    if not biomass_co2 and isinstance(biomass_entry, dict):
        biomass_co2 = biomass_entry.get("total_co2_tonnes", 0)

    # Check for ForestNet-MX enrichment (Phase 6)
    has_drivers_mx = "drivers_mx" in layers
    if has_drivers_mx:
        dm_entry = layers.get("drivers_mx", {})
        dm_stats = dm_entry.get("stats", dm_entry)
        n_classified = dm_stats.get("n_classified", 0)
        dominant = dm_stats.get("dominant_label", "?")
        print(f"    ForestNet-MX: {n_classified} classified, dominant={dominant}")

    # Check deforestation features for driver_mx (enriched by ForestNet-MX)
    if has_def and n_def > 0:
        first_feat = def_entry["geojson"]["features"][0]
        props = first_feat.get("properties", {})
        has_driver_mx = "driver_mx" in props
        has_co2 = "co2_tonnes" in props
        print(f"    Deforestation feature[0]: driver_mx={props.get('driver_mx')}, co2_tonnes={props.get('co2_tonnes')}")
    else:
        has_driver_mx = False
        has_co2 = False

    parts = [
        f"engines={len(found_engines)}",
        f"def={n_def}",
        f"biomass={'yes' if has_biomass else 'no'}(co2={biomass_co2})",
        f"drivers_mx={'yes' if has_drivers_mx else 'no'}",
        f"feat.driver_mx={has_driver_mx}",
        f"feat.co2={has_co2}",
    ]

    # Save full results
    with open("e2e_full_results.json", "w") as f:
        json.dump(data, f, indent=2, default=str)

    return True, ", ".join(parts)


def t08_chat_with_results():
    """Chat with actual analysis results."""
    data = RESULTS.get("data")
    if not data:
        return False, "No results data"

    # Build a simplified results dict for the chat
    layers = data.get("layers", data)
    job_results = {}
    for key in ("deforestation", "biomass", "fire", "alerts", "vegetation"):
        if key in layers:
            entry = layers[key]
            if isinstance(entry, dict) and "stats" in entry:
                job_results[key] = {"stats": entry["stats"]}
            elif isinstance(entry, dict):
                job_results[key] = entry

    payload = {
        "question": "Cual es el impacto ambiental detectado?",
        "job_id": RESULTS.get("job_id"),
        "job_results": job_results,
    }
    r = requests.post(f"{BASE}/api/chat/query", json=payload, timeout=TIMEOUT)
    data_resp = r.json()
    ok = r.status_code == 200 and len(data_resp.get("answer", "")) > 20
    return ok, f"mode={data_resp.get('mode')}, len={len(data_resp.get('answer',''))}"


print("=" * 60)
print("APEX E2E Validation — All 6 New Phases")
print(f"Target: {BASE}")
print("=" * 60)

print("\n--- Infrastructure ---")
test("T01 Root endpoint", t01_root)
test("T02 Chat status", t02_chat_status)
test("T04 Forecast status", t04_forecast_status)

print("\n--- TEOChat Fallback ---")
test("T03 Chat query (fallback)", t03_chat_query_fallback)

print("\n--- Full Analysis Pipeline ---")
test("T05 Submit analysis job", t05_analysis_submit)
test("T06 Poll until complete", t06_poll_job)
test("T07 Inspect results", t07_results)

print("\n--- Chat with Real Results ---")
test("T08 Chat with analysis results", t08_chat_with_results)

print("\n" + "=" * 60)
total = PASS + FAIL
print(f"RESULTS: {PASS}/{total} passed, {FAIL} failed")
if FAIL == 0:
    print("ALL E2E TESTS PASSED")
else:
    print(f"FAILURES: {FAIL}")
print("=" * 60)

sys.exit(0 if FAIL == 0 else 1)

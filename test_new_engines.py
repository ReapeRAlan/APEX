import sys, os, json, time, requests
sys.path.insert(0, r'd:\MACOV\APEX')

BASE = "http://127.0.0.1:8007"

# AOI pequeno Yucatan (~1.2 km2)
AOI = {
    "type": "Polygon",
    "coordinates": [[
        [-89.62, 20.505], [-89.61, 20.505],
        [-89.61, 20.495], [-89.62, 20.495],
        [-89.62, 20.505]
    ]]
}

def wait_job(job_id, timeout=300):
    for i in range(timeout // 2):
        r = requests.get(f"{BASE}/api/jobs/{job_id}").json()
        status = r.get("status")
        step = r.get("current_step", "")
        if i % 5 == 0:
            print(f"  [{status}] {step}")
        if status == "completed": return r
        if status == "failed": raise Exception(f"Job fallo: {step}")
        time.sleep(2)
    raise TimeoutError("Job timeout")

print("=== TEST 1: Motores nuevos ===")
engines_to_test = [
    ["deforestation", "vegetation", "urban_expansion"],         # baseline
    ["deforestation", "hansen", "alerts"],                      # nuevos GEE
    ["deforestation", "fire", "drivers"],                       # MODIS + WRI
    ["deforestation", "sar"],                                   # SAR fusion
]

for engines in engines_to_test:
    print(f"\nProbando engines: {engines}")
    try:
        resp = requests.post(f"{BASE}/api/analyze", json={
            "aoi": AOI,
            "engines": engines,
            "date_range": ["2023-01-01", "2023-03-31"]
        })
        if resp.status_code != 200:
            print(f"  ERROR HTTP {resp.status_code}: {resp.text[:200]}")
            continue

        job_id = resp.json()["job_id"]
        print(f"  Job: {job_id[:8]}...")
        result = wait_job(job_id)

        # Verificar resultados
        results_resp = requests.get(f"{BASE}/api/results/{job_id}").json()
        layers = results_resp.get("layers", {})
        for engine_name, engine_data in layers.items():
            if isinstance(engine_data, dict) and "geojson" in engine_data:
                features = engine_data.get("geojson", {}).get("features", [])
                stats = engine_data.get("stats", {})
                print(f"  {engine_name}: {len(features)} features | {json.dumps(stats, default=str)[:150]}")
            else:
                print(f"  {engine_name}: {engine_data}")

        print(f"  PASS")
    except Exception as e:
        print(f"  FAIL: {e}")

print("\n=== TEST 2: Endpoint de monitoreo ===")
try:
    # Crear area monitoreada
    resp = requests.post(f"{BASE}/api/monitoring", json={
        "name": "Test ANP Yucatan",
        "aoi_geojson": AOI,
        "engines": ["deforestation"],
        "alert_email": "test@profepa.gob.mx",
        "threshold_ha": 1.0,
        "interval_hours": 168
    })
    print(f"  POST /api/monitoring: {resp.status_code} {resp.text[:200]}")

    # Listar areas
    resp2 = requests.get(f"{BASE}/api/monitoring")
    data2 = resp2.json()
    areas = data2.get("areas", data2) if isinstance(data2, dict) else data2
    print(f"  GET /api/monitoring: {resp2.status_code} - {len(areas)} areas")
    print("  PASS")
except Exception as e:
    print(f"  FAIL: {e}")

print("\n=== TEST 3: Export con contexto legal ===")
try:
    resp = requests.post(f"{BASE}/api/analyze", json={
        "aoi": AOI,
        "engines": ["deforestation"],
        "date_range": ["2023-01-01", "2023-03-31"]
    })
    job_id = resp.json()["job_id"]
    print(f"  Job: {job_id[:8]}...")
    wait_job(job_id)

    for fmt in ["json", "pdf", "docx"]:
        try:
            r = requests.get(f"{BASE}/api/export/{job_id}/report?format={fmt}", timeout=60)
            size = len(r.content)
            status_str = "OK" if size > 1000 else "VACIO"
            print(f"  Export {fmt}: {size:,} bytes - {status_str}")
        except Exception as e:
            print(f"  Export {fmt}: ERROR - {e}")
    print("  PASS")
except Exception as e:
    print(f"  FAIL: {e}")

print("\nDone.")

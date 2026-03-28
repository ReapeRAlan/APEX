import requests, time, json

BASE = "http://localhost:8007"

# 1. Crear job — AOI pequeño ~5x4 km en Yucatán, TODOS los motores
payload = {
    "aoi": {
        "type": "Polygon",
        "coordinates": [[[-89.62,20.52],[-89.57,20.52],[-89.57,20.48],[-89.62,20.48],[-89.62,20.52]]]
    },
    "engines": ["deforestation", "vegetation", "urban_expansion"],
    "date_range": ["2023-06-01","2023-09-30"]
}
r = requests.post(f"{BASE}/api/analyze", json=payload)
data = r.json()
job_id = data["job_id"]
print(f"Job creado: {job_id}")

# 2. Polling cada 10s
for i in range(30):
    time.sleep(10)
    r2 = requests.get(f"{BASE}/api/jobs/{job_id}")
    j = r2.json()
    status = j["status"]
    progress = j["progress"]
    step = j["current_step"]
    print(f"Poll {i+1}: status={status}, progress={progress}, step={step}")
    if status in ("completed", "failed"):
        break

# 3. Obtener resultados
if status == "completed":
    r3 = requests.get(f"{BASE}/api/results/{job_id}")
    results = r3.json()
    print("\n=== RESULTADOS ===")
    for engine_name, engine_data in results.items():
        if isinstance(engine_data, dict) and "geojson" in engine_data:
            n = len(engine_data["geojson"].get("features", []))
            print(f"  {engine_name}: {n} features, stats={engine_data.get('stats', {})}")
        else:
            print(f"  {engine_name}: {engine_data}")
    print(f"\nJSON completo guardado en e2e_results.json")
    with open("e2e_results.json", "w") as f:
        json.dump(results, f, indent=2)
else:
    print(f"Job terminó con status: {status}")

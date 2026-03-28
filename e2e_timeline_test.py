import requests, time, json, sys

BASE = "http://localhost:8007"

# 1. Crear job de timeline — AOI pequeño ~5x4 km en Yucatán, solo 2 años para velocidad
payload = {
    "aoi": {
        "type": "Polygon",
        "coordinates": [[[-89.62,20.52],[-89.57,20.52],[-89.57,20.48],[-89.62,20.48],[-89.62,20.52]]]
    },
    "start_year": 2023,
    "end_year": 2024,
    "engines": ["deforestation", "urban_expansion"],
    "season": "dry"
}

print("=== E2E Timeline Test (con vegetación) ===")
r = requests.post(f"{BASE}/api/timeline", json=payload)
data = r.json()
job_id = data["job_id"]
print(f"Job creado: {job_id}")
print(f"Estimated: {data['estimated_seconds']}s")

# 2. Polling cada 10s
for i in range(60):
    time.sleep(10)
    r2 = requests.get(f"{BASE}/api/jobs/{job_id}")
    j = r2.json()
    status = j["status"]
    progress = j["progress"]
    step = j["current_step"]
    print(f"Poll {i+1}: status={status}, progress={progress}, step={step}")
    if status in ("completed", "failed"):
        break

if status != "completed":
    print(f"FAIL: job terminó con status={status}")
    sys.exit(1)

# 3. Obtener resultados
r3 = requests.get(f"{BASE}/api/results/{job_id}")
results = r3.json()

print("\n=== RESULTADOS TIMELINE ===")
layers = results.get("layers", {})
print(f"Capas encontradas: {list(layers.keys())}")

# Timeline summary
summary = layers.get("timeline_summary", {})
summary_data = summary.get("geojson", {})
years = summary_data.get("years", [])
timeline = summary_data.get("timeline", {})
season = summary_data.get("season", "?")

print(f"\nTemporada: {season}")
print(f"Años disponibles: {years}")
print(f"\n{'Año':>6} | {'Def (ha)':>10} | {'UE (ha)':>10} | {'Veg feat':>10} | {'Veg classes':>20}")
print("-" * 75)

errors = []
for yr_str in sorted(timeline.keys()):
    yr_data = timeline[yr_str]
    def_ha = yr_data.get("deforestation", {}).get("stats", {}).get("area_ha", 0)
    ue_ha = yr_data.get("urban_expansion", {}).get("stats", {}).get("area_ha", 0)
    baseline = yr_data.get("baseline_year", "?")

    # CHECK: vegetation must be present
    has_veg = "vegetation" in yr_data
    veg_n = len(yr_data.get("vegetation", {}).get("geojson", {}).get("features", [])) if has_veg else 0
    veg_classes = list(yr_data.get("vegetation", {}).get("stats", {}).get("classes", {}).keys()) if has_veg else []

    veg_status = f"{veg_n} feat" if has_veg else "MISSING"
    print(f"{yr_str:>6} | {def_ha:>10} | {ue_ha:>10} | {veg_status:>10} | {veg_classes}  (vs {baseline})")

    if not has_veg:
        errors.append(f"Year {yr_str}: vegetation key MISSING")
    elif veg_n == 0:
        errors.append(f"Year {yr_str}: vegetation has 0 features")

# Verificar que 2024 tiene datos
print(f"\n2024 tiene datos: {'2024' in timeline}")

if errors:
    print(f"\nFAIL: {len(errors)} errors:")
    for e in errors:
        print(f"  - {e}")
    sys.exit(1)
else:
    print("\nPASS: Todos los anos tienen vegetacion OK")

# 4. Verificar anomalias y cumulative
anomalies = summary_data.get("anomalies", [])
cumulative = summary_data.get("cumulative", {})

print(f"\n=== ANOMALIAS ({len(anomalies)}) ===")
if anomalies:
    for a in anomalies:
        print(f"  {a['year']} {a['engine']}: {a['area_ha']}ha (z={a['z_score']}, severity={a['severity']})")
else:
    print("  (ninguna con solo 1 comparacion, esperado)")

print(f"\n=== RESUMEN ACUMULADO ===")
if cumulative:
    print(f"  Periodo: {cumulative.get('period')}")
    print(f"  Deforestacion total: {cumulative.get('total_deforestation_ha')} ha")
    print(f"  Expansion urbana total: {cumulative.get('total_urban_expansion_ha')} ha")
    print(f"  Cambio bosque denso: {cumulative.get('bosque_denso_change_pct')}%")
    print(f"  Cambio urbano: {cumulative.get('urbano_change_pct')}%")
    print(f"  Anos analizados: {cumulative.get('years_analyzed')}")
    has_cumulative = True
else:
    print("  MISSING cumulative data!")
    has_cumulative = False
    errors.append("cumulative data MISSING in summary")

# 5. Verificar endpoint de export
print(f"\n=== EXPORT ENDPOINT ===")
r4 = requests.get(f"{BASE}/api/export/{job_id}/report?type=timeline")
if r4.status_code == 200:
    report = r4.json()
    print(f"  folio: {report.get('folio')}")
    print(f"  periodo: {report.get('periodo_analizado')}")
    print(f"  resumen: {report.get('resumen_ejecutivo')}")
    print(f"  alertas: {len(report.get('alertas', []))}")
    print("  PASS: Export endpoint OK")
else:
    print(f"  FAIL: Export returned {r4.status_code}")
    errors.append(f"Export endpoint returned {r4.status_code}")

if errors:
    print(f"\nFAIL: {len(errors)} errors:")
    for e in errors:
        print(f"  - {e}")
    sys.exit(1)
else:
    print("\nPASS: All checks passed OK")

with open("e2e_timeline_results.json", "w") as f:
    json.dump(results, f, indent=2)
print(f"JSON guardado en e2e_timeline_results.json")

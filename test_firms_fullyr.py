"""Quick test: FIRMS full-year timeline for Aguascalientes AOI."""
import requests, time, json, sys

API = "http://127.0.0.1:8008/api"

aoi = {
    "type": "Polygon",
    "coordinates": [[
        [-102.35, 21.90], [-102.15, 21.90], [-102.15, 22.05],
        [-102.35, 22.05], [-102.35, 21.90]
    ]]
}

r = requests.post(f"{API}/timeline", json={
    "aoi": aoi,
    "engines": ["firms_hotspots"],
    "start_year": 2018,
    "end_year": 2025,
    "season": "dry"
})
job = r.json()
job_id = job["job_id"]
print(f"Job: {job_id}")

for i in range(120):
    time.sleep(5)
    s = requests.get(f"{API}/jobs/{job_id}").json()
    pct = s.get("progress", 0)
    step = s.get("current_step", "")
    status = s.get("status", "")
    print(f"  [{pct}%] {status} -- {step}")
    if status in ("completed", "failed"):
        break

if status != "completed":
    print(f"FAILED: {status}")
    sys.exit(1)

res = requests.get(f"{API}/results/{job_id}").json()
tl = res.get("layers", {}).get("timeline_summary", {}).get("geojson", {}).get("timeline", {})
cum = res.get("layers", {}).get("timeline_summary", {}).get("geojson", {}).get("cumulative", {})

print("\n=== CUMULATIVE ===")
print(f"  total_firms_hotspots: {cum.get('total_firms_hotspots', 'N/A')}")
print(f"  total_frp_mw: {cum.get('total_frp_mw', 'N/A')}")

print("\n=== PER YEAR ===")
for yr in sorted(tl.keys()):
    fs = tl[yr].get("firms_hotspots", {}).get("stats", {})
    hc = fs.get("hotspot_count", 0)
    frp = fs.get("total_frp_mw", 0)
    hi = fs.get("high_confidence_count", 0)
    cl = fs.get("cluster_count", 0)
    dr = fs.get("date_range", "")
    sats = fs.get("satellites", [])
    print(f"  {yr}: hotspots={hc}, high_conf={hi}, clusters={cl}, frp={frp}MW, sats={sats}, range={dr}")

print("\nDONE")

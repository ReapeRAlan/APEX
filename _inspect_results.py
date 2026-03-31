import json

data = json.load(open("e2e_full_results.json"))
layers = data["layers"]

# Check deforestation feature enrichment
def_feats = layers.get("deforestation", {}).get("geojson", {}).get("features", [])
print(f"Deforestation features: {len(def_feats)}")
if def_feats:
    props = def_feats[0].get("properties", {})
    print(f"Feature[0] keys: {list(props.keys())}")
    print(f"  driver_mx: {props.get('driver_mx', 'NOT SET')}")
    print(f"  driver_mx_label: {props.get('driver_mx_label', 'NOT SET')}")
    print(f"  co2_tonnes: {props.get('co2_tonnes', 'NOT SET')}")
    print(f"  agbd_mg_ha: {props.get('agbd_mg_ha', 'NOT SET')}")
    print(f"  area_ha: {props.get('area_ha', 'NOT SET')}")

# Check biomass stats
bio = layers.get("biomass", {})
print(f"Biomass stats: {bio.get('stats', {})}")

# Check drivers_mx stats
dmx = layers.get("drivers_mx", {})
print(f"ForestNet-MX stats: {dmx.get('stats', {})}")

# Check FIRMS
firms_feats = layers.get("firms_hotspots", {}).get("geojson", {}).get("features", [])
print(f"FIRMS hotspots: {len(firms_feats)}")

# Hansen
hansen = layers.get("hansen", {})
print(f"Hansen: {len(hansen.get('geojson',{}).get('features',[]))} feats, stats={hansen.get('stats',{})}")

# Vegetation
veg = layers.get("vegetation", {})
veg_feats = veg.get("geojson", {}).get("features", [])
print(f"Vegetation: {len(veg_feats)} features")
if veg_feats:
    print(f"  classes: {set(f['properties'].get('class','?') for f in veg_feats[:20])}")

# Drivers WRI
drv = layers.get("drivers", {})
drv_feats = drv.get("geojson", {}).get("features", [])
print(f"Drivers WRI: {len(drv_feats)} features, stats={drv.get('stats', {})}")

from backend.engines.dynamic_world_engine import DynamicWorldEngine

e = DynamicWorldEngine()
fake = {
    "2018": {"deforestation": {"stats": {"area_ha": 2}}, "urban_expansion": {"stats": {"area_ha": 1}}},
    "2019": {"deforestation": {"stats": {"area_ha": 3}}, "urban_expansion": {"stats": {"area_ha": 1}}},
    "2020": {"deforestation": {"stats": {"area_ha": 2}}, "urban_expansion": {"stats": {"area_ha": 1}}},
    "2021": {"deforestation": {"stats": {"area_ha": 50}}, "urban_expansion": {"stats": {"area_ha": 1}}},
    "2022": {"deforestation": {"stats": {"area_ha": 2}}, "urban_expansion": {"stats": {"area_ha": 1}}},
    "2023": {"deforestation": {"stats": {"area_ha": 3}}, "urban_expansion": {"stats": {"area_ha": 1}}},
    "2024": {"deforestation": {"stats": {"area_ha": 2}}, "urban_expansion": {"stats": {"area_ha": 1}}},
}

alerts = e.detect_anomalies(fake)
print(f"{len(alerts)} anomalies detected:")
for a in alerts:
    print(f"  year={a['year']} engine={a['engine']} area={a['area_ha']}ha z={a['z_score']} severity={a['severity']}")
    print(f"    {a['message']}")

if len(alerts) == 0:
    print("WARNING: Expected anomaly at 2021 but none detected")
else:
    print("PASS: Anomaly detection working")

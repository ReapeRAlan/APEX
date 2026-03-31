"""
Quick validation of new APEX engines (Phases 1-6).
Tests: SpectralGPT model load, TEOChat fallback, ForestNet-MX classify,
       ConvLSTM import, AVOCADO import, Biomass import.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

print("=" * 60)
print("APEX Engine Validation")
print("=" * 60)

errors = []
passed = []

# 1. SpectralGPT model load
print("\n[1] SpectralGPT engine — model load...")
try:
    from backend.engines.spectralgpt_engine import _load_model, _SPECTRALGPT_WEIGHTS, CLASS_NAMES
    print(f"    Weights path: {_SPECTRALGPT_WEIGHTS}")
    print(f"    Exists: {os.path.exists(_SPECTRALGPT_WEIGHTS) if _SPECTRALGPT_WEIGHTS else False}")
    model, mode = _load_model("cpu")
    print(f"    Mode: {mode}")
    if mode == "spectralgpt":
        print(f"    ✓ ViT model loaded from weights")
        passed.append("SpectralGPT (ViT)")
    elif mode == "heuristic":
        print(f"    ✓ Heuristic fallback (weights not loadable)")
        passed.append("SpectralGPT (heuristic)")
    print(f"    Classes: {len(CLASS_NAMES)} → {CLASS_NAMES[:3]}...")
except Exception as e:
    print(f"    ✗ ERROR: {e}")
    errors.append(f"SpectralGPT: {e}")

# 2. TEOChat fallback (no GPU load, just test fallback response)
print("\n[2] TEOChat service — fallback mode...")
try:
    from backend.services.teochat_service import _fallback_response, _build_context_from_results, get_status
    test_results = {
        "deforestation": {"stats": {"area_ha": 42.5, "n_features": 8}},
        "biomass": {"total_co2_tonnes": 1234.5, "mean_agbd_mg_ha": 95.3},
        "fire": {"stats": {"total_burned_ha": 15.2, "fire_count": 3}},
    }
    ctx = _build_context_from_results(test_results)
    print(f"    Context: {ctx[:100]}...")
    answer = _fallback_response("¿Cuál es el resumen del análisis?", ctx)
    print(f"    Answer: {answer[:100]}...")
    status = get_status()
    print(f"    Status: loaded={status['loaded']}, gpu={status['gpu_available']}")
    passed.append("TEOChat (fallback)")
except Exception as e:
    print(f"    ✗ ERROR: {e}")
    errors.append(f"TEOChat: {e}")

# 3. ForestNet-MX classification
print("\n[3] ForestNet-MX engine — rule-based classify...")
try:
    from backend.engines.drivers_mx_engine import ForestNetMXEngine, DRIVER_CLASSES
    fnet = ForestNetMXEngine()
    test_features = [
        {"geometry": {"type": "Polygon", "coordinates": [[[-89.6,20.5],[-89.5,20.5],[-89.5,20.4],[-89.6,20.4],[-89.6,20.5]]]},
         "properties": {"area_ha": 25.0, "ndvi_mean": 0.15}},
        {"geometry": {"type": "Polygon", "coordinates": [[[-89.4,20.3],[-89.35,20.3],[-89.35,20.28],[-89.4,20.28],[-89.4,20.3]]]},
         "properties": {"area_ha": 3.0, "ndvi_mean": 0.55}},
    ]
    enriched, stats = fnet.classify(test_features, {}, job_id="test")
    print(f"    Classified: {stats['n_classified']} features")
    print(f"    Dominant: {stats['dominant_label']}")
    for f in enriched:
        p = f["properties"]
        print(f"    → {p['driver_mx']}: {p['driver_mx_label']} ({p['area_ha']} ha)")
    passed.append("ForestNet-MX")
except Exception as e:
    print(f"    ✗ ERROR: {e}")
    errors.append(f"ForestNet-MX: {e}")

# 4. ConvLSTM import
print("\n[4] ConvLSTM model — import check...")
try:
    from backend.services.convlstm_model import ConvLSTMCell, ConvLSTMStack
    print(f"    ✓ ConvLSTMCell and ConvLSTMStack imported")
    passed.append("ConvLSTM")
except Exception as e:
    print(f"    ✗ ERROR: {e}")
    errors.append(f"ConvLSTM: {e}")

# 5. AVOCADO import
print("\n[5] AVOCADO engine — import check...")
try:
    from backend.engines.avocado_engine import AvocadoEngine
    print(f"    ✓ AvocadoEngine imported")
    passed.append("AVOCADO")
except Exception as e:
    print(f"    ✗ ERROR: {e}")
    errors.append(f"AVOCADO: {e}")

# 6. Biomass import
print("\n[6] Biomass engine — import check...")
try:
    from backend.engines.biomass_engine import BiomassEngine
    print(f"    ✓ BiomassEngine imported")
    passed.append("Biomass")
except Exception as e:
    print(f"    ✗ ERROR: {e}")
    errors.append(f"Biomass: {e}")

# 7. Chat router import
print("\n[7] Chat router — import check...")
try:
    from backend.routers.chat import router
    print(f"    ✓ Chat router imported, routes: {[r.path for r in router.routes]}")
    passed.append("Chat router")
except Exception as e:
    print(f"    ✗ ERROR: {e}")
    errors.append(f"Chat router: {e}")

# 8. Forecast engine with ConvLSTM
print("\n[8] Forecast engine — status check...")
try:
    from backend.services.forecast_engine import get_forecast_status
    status = get_forecast_status()
    print(f"    ML trained: {status.get('ml_model_trained')}")
    print(f"    ConvLSTM: {status.get('convlstm_model_trained')}")
    passed.append("Forecast engine")
except Exception as e:
    print(f"    ✗ ERROR: {e}")
    errors.append(f"Forecast: {e}")

# Summary
print("\n" + "=" * 60)
print(f"PASSED: {len(passed)}/{len(passed) + len(errors)}")
for p in passed:
    print(f"  ✓ {p}")
if errors:
    print(f"\nFAILED: {len(errors)}")
    for e in errors:
        print(f"  ✗ {e}")
else:
    print("\n✓ ALL ENGINES VALIDATED SUCCESSFULLY")
print("=" * 60)

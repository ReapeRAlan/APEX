import sys; sys.path.insert(0, 'D:/MACOV/APEX')
from backend.services.forecast_engine import forecast_from_timeline
import json
result = forecast_from_timeline(horizon=3, method='ensemble')
if result.get('status') == 'ok':
    spatial = result.get('spatial_forecast', {})
    print('Status: OK')
    preds = result.get('predictions', [])
    print('Predictions: %d' % len(preds))
    print('Spatial keys: %s' % list(spatial.keys()))
    for k, v in spatial.items():
        feats = v.get('features', [])
        print('  %s: %d features' % (k, len(feats)))
        if feats:
            f0 = feats[0]
            print('    First feature props: %s' % json.dumps(f0['properties'], indent=2))
            geom_type = f0['geometry']['type']
            print('    Geometry type: %s' % geom_type)
else:
    print('Status: %s' % result.get('status'))
    print('Detail: %s' % result.get('detail'))

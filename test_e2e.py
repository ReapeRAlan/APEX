import urllib.request
import json
import time

job_id = "23d0b39c-22c4-4086-a8c2-af0782c3dd4a"
base = "http://127.0.0.1:8003"

print("=== PASO 2: Polling job status ===")
for i in range(12):
    try:
        r = urllib.request.urlopen(f"{base}/api/jobs/{job_id}")
        data = json.loads(r.read().decode())
        print(f"Poll {i+1}: status={data['status']}, progress={data.get('progress')}, step={data.get('current_step')}")
        if data["status"] in ("completed", "failed"):
            break
    except Exception as e:
        print(f"Poll {i+1}: ERROR {e}")
    time.sleep(5)

print()
print("=== PASO 3: Get results ===")
try:
    r = urllib.request.urlopen(f"{base}/api/results/{job_id}")
    result = json.loads(r.read().decode())
    print(json.dumps(result, indent=2, ensure_ascii=False))
except urllib.error.HTTPError as e:
    body = e.read().decode()
    print(f"HTTP {e.code}: {body}")
except Exception as e:
    print(f"ERROR: {e}")




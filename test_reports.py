"""Test report generation for all formats."""
import requests

job_id = "4ef7aebe-2c28-4e33-ab0a-3d739c4b0d58"
API = "http://127.0.0.1:8008/api"

for fmt in ["json", "pdf", "docx"]:
    try:
        r = requests.get(f"{API}/export/{job_id}/report?format={fmt}", timeout=300)
        ct = r.headers.get("content-type", "?")[:60]
        print(f"{fmt}: status={r.status_code}, size={len(r.content)} bytes, type={ct}")
        if r.status_code != 200:
            print(f"  ERROR: {r.text[:300]}")
        elif fmt == "json":
            data = r.json()
            print(f"  Keys: {list(data.keys())}")
        elif fmt == "pdf":
            with open(f"test_report.pdf", "wb") as f:
                f.write(r.content)
            print(f"  Saved: test_report.pdf")
        elif fmt == "docx":
            with open(f"test_report.docx", "wb") as f:
                f.write(r.content)
            print(f"  Saved: test_report.docx")
    except Exception as e:
        print(f"{fmt}: EXCEPTION: {e}")

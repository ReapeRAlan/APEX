"""Fetch CI job annotations (error messages) from GitHub Actions."""
import urllib.request, json, subprocess, sys

proc = subprocess.run(
    ["git", "credential", "fill"],
    input="protocol=https\nhost=github.com\n\n",
    capture_output=True, text=True
)
creds = dict(line.split("=", 1) for line in proc.stdout.strip().split("\n") if "=" in line)
token = creds.get("password", "")

headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github+json"}

def api(path):
    req = urllib.request.Request(f"https://api.github.com/repos/ReapeRAlan/APEX{path}", headers=headers)
    return json.loads(urllib.request.urlopen(req).read())

# Get annotations for the latest CI run
run_id = 23694695487
annotations = api(f"/check-runs?filter=latest")

# Get check runs for the commit
commit_sha = api("/actions/runs/23694695487")["head_sha"]
print(f"Commit: {commit_sha}\n")

# Get check runs
checks = api(f"/commits/{commit_sha}/check-runs")
for cr in checks["check_runs"]:
    name = cr["name"]
    conclusion = cr["conclusion"]
    output = cr.get("output", {})
    print(f"=== {name} ({conclusion}) ===")
    if output.get("summary"):
        print(f"Summary: {output['summary'][:500]}")
    if output.get("text"):
        print(f"Text: {output['text'][:1000]}")
    # Annotations
    anns = cr.get("output", {}).get("annotations", [])
    if anns:
        for a in anns[:10]:
            print(f"  {a.get('annotation_level')}: {a.get('path')}:{a.get('start_line')} - {a.get('message')}")
    print()

# Also try to get the run log via zip download
print("\n--- Attempting to download run logs ---")
import io, zipfile
log_url = f"https://api.github.com/repos/ReapeRAlan/APEX/actions/runs/{run_id}/logs"
req = urllib.request.Request(log_url, headers=headers)
try:
    resp = urllib.request.urlopen(req)
    zdata = resp.read()
    zf = zipfile.ZipFile(io.BytesIO(zdata))
    for name in zf.namelist():
        if any(k in name.lower() for k in ["lint", "install", "dependencies", "fail"]):
            print(f"\n--- {name} ---")
            content = zf.read(name).decode("utf-8", errors="replace")
            lines = content.split("\n")
            # Show last 50 lines
            for line in lines[-50:]:
                print(line)
except Exception as e:
    print(f"Error: {e}")

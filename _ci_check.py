"""Fetch CI run logs from GitHub Actions."""
import urllib.request, json, subprocess, sys

# Get git credentials
proc = subprocess.run(
    ["git", "credential", "fill"],
    input="protocol=https\nhost=github.com\n\n",
    capture_output=True, text=True
)
creds = dict(line.split("=", 1) for line in proc.stdout.strip().split("\n") if "=" in line)
token = creds.get("password", "")
if not token:
    print("ERROR: No GitHub token found"); sys.exit(1)

headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github+json"}

def api(path):
    req = urllib.request.Request(f"https://api.github.com/repos/ReapeRAlan/APEX{path}", headers=headers)
    return json.loads(urllib.request.urlopen(req).read())

# 1. List recent runs
runs = api("/actions/runs?per_page=5")
for r in runs["workflow_runs"]:
    print(f"Run {r['id']} | {r['name']} | {r['conclusion']} | {r['created_at']}")

# 2. Get the latest CI run (not Deploy)
ci_runs = [r for r in runs["workflow_runs"] if r["name"] == "CI"]
if not ci_runs:
    print("No CI runs found"); sys.exit(0)

latest = ci_runs[0]
print(f"\n=== Latest CI run: {latest['id']} ({latest['conclusion']}) ===")

# 3. Get jobs for that run
jobs = api(f"/actions/runs/{latest['id']}/jobs")
for j in jobs["jobs"]:
    print(f"\nJob: {j['name']} | {j['conclusion']}")
    for step in j["steps"]:
        status = "✅" if step["conclusion"] == "success" else "❌" if step["conclusion"] == "failure" else "⏭"
        print(f"  {status} {step['name']} ({step['conclusion']})")

# 4. Get logs for failed jobs
for j in jobs["jobs"]:
    if j["conclusion"] == "failure":
        print(f"\n{'='*60}")
        print(f"LOGS for failed job: {j['name']}")
        print(f"{'='*60}")
        # Download log (redirects to a zip)
        log_url = f"https://api.github.com/repos/ReapeRAlan/APEX/actions/jobs/{j['id']}/logs"
        req = urllib.request.Request(log_url, headers=headers)
        try:
            resp = urllib.request.urlopen(req)
            # Logs come as plain text
            log_text = resp.read().decode("utf-8", errors="replace")
            # Print last 80 lines
            lines = log_text.split("\n")
            print(f"(showing last 80 of {len(lines)} lines)")
            print("\n".join(lines[-80:]))
        except Exception as e:
            print(f"Could not fetch logs: {e}")

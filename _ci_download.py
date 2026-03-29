"""Download CI run logs from GitHub Actions."""
import urllib.request, json, subprocess, sys, io, zipfile

proc = subprocess.run(
    ["git", "credential", "fill"],
    input="protocol=https\nhost=github.com\n\n",
    capture_output=True, text=True
)
creds = dict(line.split("=", 1) for line in proc.stdout.strip().split("\n") if "=" in line)
token = creds.get("password", "")

headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github+json"}

run_id = 23694695487
log_url = f"https://api.github.com/repos/ReapeRAlan/APEX/actions/runs/{run_id}/logs"
req = urllib.request.Request(log_url, headers=headers)
try:
    resp = urllib.request.urlopen(req)
    zdata = resp.read()
    zf = zipfile.ZipFile(io.BytesIO(zdata))
    
    print("Files in log archive:")
    for name in sorted(zf.namelist()):
        print(f"  {name}")
    
    # Print all log files
    for name in sorted(zf.namelist()):
        content = zf.read(name).decode("utf-8", errors="replace")
        lines = content.strip().split("\n")
        # For failed steps, show more
        print(f"\n{'='*70}")
        print(f"FILE: {name} ({len(lines)} lines)")
        print(f"{'='*70}")
        # Show last 40 lines of each
        for line in lines[-40:]:
            print(line)
except urllib.error.HTTPError as e:
    body = e.read().decode()
    print(f"HTTP {e.code}: {body}")
except Exception as e:
    print(f"Error: {type(e).__name__}: {e}")

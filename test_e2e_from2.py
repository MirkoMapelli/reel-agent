#!/usr/bin/env python3
"""E2E da step 2: curate -> EDL -> render (analyze_raw gia' fatto, job 185)."""
import urllib.request, json, ssl, time, sys

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE
BASE = "https://127.0.0.1:5000"
PROFILE_ID = 16

def req(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method,
                                headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, context=ctx, timeout=60) as resp:
        return json.loads(resp.read().decode())

def wait(jid, label, poll=5):
    print(f"\n[{label}] job_id={jid}")
    last = -1
    while True:
        j = req("GET", f"/api/jobs/{jid}")
        pct = j.get("progress", 0)
        st = j.get("status")
        if pct != last:
            print(f"  {st:10} {pct:3}%  {(j.get('step_label') or '')[:60]}")
            last = pct
        if st in ("done", "error"):
            if st == "error":
                print(f"  ERROR: {j.get('error')}")
                return None
            return j
        time.sleep(poll)

print("="*60); print("STEP 2: curate (profilo 16)")
r = req("POST", "/api/curate/run", {"profile_id": PROFILE_ID})
print(f"  launched: {r}")
if not wait(r["job_id"], "curate", poll=3): sys.exit(1)

print("="*60); print("STEP 3: generate EDL")
r = req("POST", "/api/apply/generate", {"profile_id": PROFILE_ID, "venue_name": "Wing Stop Milano"})
print(f"  launched: {r}")
edl_job = r["job_id"]
if not wait(edl_job, "generate_edl", poll=3): sys.exit(1)

print("="*60); print("STEP 4: render")
r = req("POST", "/api/render/run", {"profile_id": PROFILE_ID})
print(f"  launched: {r}")
res = wait(r["job_id"], "render_final", poll=10)
if not res: sys.exit(1)

print("\n" + "="*60)
print("REPORT FINALE")
print("="*60)
result = res.get("result_json") or res.get("result") or {}
if isinstance(result, str): result = json.loads(result)
print(f"EDL job : {edl_job}")
print(f"output  : {result.get('output')}")
print(f"srt     : {result.get('srt')}")
print(f"size_mb : {result.get('size_mb')}")
print(f"duration: {result.get('duration')}s")
print(f"n_parts : {result.get('n_parts')}  <-- clip montate")
n = result.get('n_parts', 0) or 1
d = result.get('duration', 0) or 1
print(f"\nCONFRONTO vs job 181: 21 clip / 66.97s / 3.19s per clip")
print(f"NUOVO              : {n} clip / {d}s / {d/n:.2f}s per clip")

#!/usr/bin/env python3
"""End-to-end test: raw -> analyze -> curate -> EDL -> render."""
import urllib.request, json, ssl, time, sys

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE
BASE = "https://127.0.0.1:5000"
PROFILE_ID = 16   # WIngStop

def req(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method,
                                headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, context=ctx, timeout=60) as resp:
        return json.loads(resp.read().decode())

def wait(jid, label, poll=5):
    print(f"\n[{label}] job_id={jid}")
    last_pct = -1
    while True:
        j = req("GET", f"/api/jobs/{jid}")
        pct = j.get("progress", 0)
        st = j.get("status")
        if pct != last_pct:
            lbl = (j.get("step_label") or "")[:60]
            print(f"  {st:10} {pct:3}%  {lbl}")
            last_pct = pct
        if st in ("done", "error"):
            if st == "error":
                print(f"  ERROR: {j.get('error')}")
                return None
            return j
        time.sleep(poll)

# ---- STEP 1: analyze raw ----
print("="*60); print("STEP 1: analyze raw")
r = req("POST", "/api/raw/analyze")
print(f"  launched: {r}")
if not wait(r["job_id"], "analyze_raw"): sys.exit(1)

# ---- STEP 2: curate ----
print("="*60); print("STEP 2: curate")
r = req("POST", "/api/curate/run", {"profile_id": PROFILE_ID})
print(f"  launched: {r}")
if not wait(r["job_id"], "curate"): sys.exit(1)

# ---- STEP 3: generate EDL ----
print("="*60); print("STEP 3: generate EDL")
r = req("POST", "/api/apply/generate", {"profile_id": PROFILE_ID, "venue_name": "Wing Stop Milano"})
print(f"  launched: {r}")
edl_job = r["job_id"]
if not wait(edl_job, "generate_edl"): sys.exit(1)

# ---- STEP 4: render ----
print("="*60); print("STEP 4: render")
r = req("POST", "/api/render/run", {"profile_id": PROFILE_ID})
print(f"  launched: {r}")
render_job = r["job_id"]
res = wait(render_job, "render_final", poll=10)
if not res:
    sys.exit(1)

# ---- REPORT ----
print("\n" + "="*60)
print("REPORT FINALE")
print("="*60)
result = res.get("result_json") or res.get("result") or {}
if isinstance(result, str):
    result = json.loads(result)
print(f"EDL job   : {edl_job}")
print(f"Render job: {render_job}")
print(f"output    : {result.get('output')}")
print(f"srt       : {result.get('srt')}")
print(f"size_mb   : {result.get('size_mb')}")
print(f"duration  : {result.get('duration')}s")
print(f"n_parts   : {result.get('n_parts')}  <-- clip montate (era 21 nel 181)")

# Confronto con job 181
print()
print(f"CONFRONTO: 181 aveva 21 clip / 66.97s / 3.19s per clip")
n = result.get('n_parts', 0) or 1
d = result.get('duration', 0) or 1
print(f"NUOVO    : {n} clip / {d}s / {d/n:.2f}s per clip")

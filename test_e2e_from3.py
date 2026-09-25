#!/usr/bin/env python3
"""E2E da step 3: generate_edl + render (analyze+curate ok)."""
import urllib.request, json, ssl, time, sys
ctx = ssl.create_default_context(); ctx.check_hostname=False; ctx.verify_mode=ssl.CERT_NONE
BASE="https://127.0.0.1:5000"; PROFILE_ID=16
def req(m,p,b=None):
    d=json.dumps(b).encode() if b is not None else None
    r=urllib.request.Request(BASE+p,data=d,method=m,headers={"Content-Type":"application/json"})
    with urllib.request.urlopen(r,context=ctx,timeout=60) as x: return json.loads(x.read().decode())
def wait(j,l,poll=3):
    print(f"\n[{l}] job_id={j}"); last=-1
    while True:
        x=req("GET",f"/api/jobs/{j}"); p=x.get("progress",0); s=x.get("status")
        if p!=last: print(f"  {s:10} {p:3}%  {(x.get('step_label') or '')[:60]}"); last=p
        if s in ("done","error"):
            if s=="error": print(f"  ERROR: {x.get('error')}"); return None
            return x
        time.sleep(poll)

print("="*60); print("STEP 3: generate EDL")
r=req("POST","/api/apply/generate",{"profile_id":PROFILE_ID,"venue_name":"Wing Stop Milano"})
edl_job=r["job_id"]; print(f"  launched: {r}")
if not wait(edl_job,"generate_edl"): sys.exit(1)

print("="*60); print("STEP 4: render")
r=req("POST","/api/render/run",{"profile_id":PROFILE_ID})
res=wait(r["job_id"],"render_final",poll=10)
if not res: sys.exit(1)

result=res.get("result_json") or res.get("result") or {}
if isinstance(result,str): result=json.loads(result)
print(f"\nEDL job: {edl_job}")
print(f"output : {result.get('output')}")
print(f"n_parts: {result.get('n_parts')}  duration: {result.get('duration')}s")

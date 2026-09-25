#!/usr/bin/env python3
"""Blind test A/B: 6 originali @jay.emme + 6 cloni generati dal brain.
Genera i cloni, mescola con gli originali, crea pagina HTML per il giudizio."""
import os, sys, json, time, glob, random, shutil, subprocess
from pathlib import Path
from dotenv import load_dotenv

sys.path.insert(0, '/opt/reel-agent/app')
sys.path.insert(0, '/opt/reel-agent')
load_dotenv('/opt/reel-agent/.env')

BLIND = Path('/opt/reel-agent/blind_test')
VIDEOS = BLIND / 'videos'
LOG = BLIND / 'blind.log'
BLIND.mkdir(exist_ok=True, parents=True)
VIDEOS.mkdir(exist_ok=True)


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG, 'a') as f:
        f.write(line + '\n')


def pick_originals(n=6, exclude_count=6):
    """Scegli n originali dal style_ref, escludendo i primi (piu' visti dal brain)."""
    files = sorted(glob.glob('/opt/reel-agent/media/style_ref/*.mp4'))
    # escludiamo i primi 20 per ridurre bias del brain, e prendiamo dalla meta' in poi
    pool = files[len(files)//2:]
    random.seed(42)
    return random.sample(pool, n)


def generate_clones(n=6):
    """Genera n cloni usando brain_phase_e con seed diversi."""
    cloni = []
    for i in range(n):
        log(f"genero clone {i+1}/{n}...")
        # Chiama brain_phase_e come subprocess con seed
        env = os.environ.copy()
        env['BLIND_SEED'] = str(100 + i)
        r = subprocess.run(
            ['/opt/reel-agent/venv/bin/python', '/opt/reel-agent/brain_phase_e.py'],
            capture_output=True, text=True, timeout=300, env=env,
        )
        # Trova l'ultimo render
        renders = sorted(glob.glob('/opt/reel-agent/media/renders/render_999.mp4'),
                         key=os.path.getmtime)
        if renders:
            dest = VIDEOS / f"clone_{i+1:02d}.mp4"
            shutil.copy(renders[-1], dest)
            cloni.append(dest)
            log(f"  clone {i+1}: {dest}")
        else:
            log(f"  clone {i+1}: FALLITO")
    return cloni


def main():
    log("=== BLIND TEST START ===")
    VIDEOS.mkdir(exist_ok=True)

    # 1. Originali
    log("[1] scelgo 6 originali...")
    originals = pick_originals(6)
    for i, src in enumerate(originals, 1):
        dest = VIDEOS / f"orig_{i:02d}.mp4"
        shutil.copy(src, dest)
        log(f"  orig {i}: {Path(src).name}")

    # 2. Cloni (via curl verso server, come al solito)
    log("[2] genero 6 cloni dal brain...")
    import urllib.request, ssl
    ctx = ssl.create_default_context(); ctx.check_hostname=False; ctx.verify_mode=ssl.CERT_NONE
    BASE = "https://127.0.0.1:5000"

    def req(m, p, b=None):
        d = json.dumps(b).encode() if b is not None else None
        r = urllib.request.Request(BASE+p, data=d, method=m,
                                    headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(r, context=ctx, timeout=30) as x:
            return json.loads(x.read().decode())

    cloni = []
    for i in range(6):
        log(f"  clone {i+1}/6: genero EDL...")
        # Genera EDL nuovo (brain_phase_e crea job_999)
        subprocess.run(['/opt/reel-agent/venv/bin/python', '/opt/reel-agent/brain_phase_e.py'],
                       capture_output=True, timeout=180)
        # Render via HTTP
        r = req("POST", "/api/render/run", {"profile_id": 16})
        jid = r["job_id"]
        # Aspetta
        while True:
            j = req("GET", f"/api/jobs/{jid}")
            if j.get("status") in ("done", "error"):
                break
            time.sleep(8)
        # Copia il render
        render_path = '/opt/reel-agent/media/renders/render_999.mp4'
        if os.path.exists(render_path):
            dest = VIDEOS / f"clone_{i+1:02d}.mp4"
            shutil.copy(render_path, dest)
            cloni.append(dest)
            log(f"    clone {i+1} pronto")

    # 3. Mescola e rinomina
    log("[3] mescolo...")
    all_files = list(VIDEOS.glob('orig_*.mp4')) + list(VIDEOS.glob('clone_*.mp4'))
    random.seed(123)
    random.shuffle(all_files)
    mapping = {}
    for i, f in enumerate(all_files):
        letter = chr(ord('A') + i)
        new_name = VIDEOS / f"video_{letter}.mp4"
        shutil.move(str(f), str(new_name))
        mapping[letter] = 'original' if 'orig_' in f.name else 'clone'

    # Salva la soluzione (per verificare dopo)
    (BLIND / 'solution.json').write_text(json.dumps(mapping, indent=2))
    log(f"  soluzione salvata (NON guardare): {BLIND}/solution.json")

    # 4. HTML
    log("[4] genero pagina HTML...")
    html = '''<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Blind Test</title>
<style>
body { font-family: sans-serif; background: #111; color: #eee; padding: 20px; }
h1 { color: #fff; }
.grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 20px; margin-top: 20px; }
.card { background: #222; padding: 10px; border-radius: 8px; }
video { width: 100%; max-height: 500px; background: #000; }
.label { font-size: 20px; font-weight: bold; margin-bottom: 8px; color: #ffcc00; }
.btn-row { margin-top: 8px; display: flex; gap: 10px; }
button { flex: 1; padding: 10px; border: none; border-radius: 6px; cursor: pointer; font-weight: bold; }
.orig { background: #2e7d32; color: white; }
.clone { background: #c62828; color: white; }
button.selected { outline: 3px solid #ffcc00; }
.verdict { margin-top: 40px; padding: 20px; background: #222; border-radius: 8px; font-size: 18px; }
</style></head><body>
<h1>Blind Test — Originale o Clone?</h1>
<p>Guarda ogni video. Scegli: <b>Originale @jay.emme</b> o <b>Clone AI</b>.</p>
<div class="grid" id="grid"></div>
<div class="verdict" id="verdict">Risposte: 0/12</div>
<script>
const videos = ['A','B','C','D','E','F','G','H','I','J','K','L'];
const answers = {};
const grid = document.getElementById('grid');
videos.forEach(v => {
  const card = document.createElement('div');
  card.className = 'card';
  card.innerHTML = `<div class="label">VIDEO ${v}</div>
    <video controls preload="metadata" src="/blind_test/videos/video_${v}.mp4"></video>
    <div class="btn-row">
      <button class="orig" onclick="pick('${v}','orig', this)">ORIGINALE</button>
      <button class="clone" onclick="pick('${v}','clone', this)">CLONE</button>
    </div>`;
  grid.appendChild(card);
});
function pick(v, choice, btn) {
  answers[v] = choice;
  const row = btn.parentElement;
  row.querySelectorAll('button').forEach(b => b.classList.remove('selected'));
  btn.classList.add('selected');
  document.getElementById('verdict').textContent = `Risposte: ${Object.keys(answers).length}/12`;
  if (Object.keys(answers).length === 12) {
    localStorage.setItem('blind_answers', JSON.stringify(answers));
    document.getElementById('verdict').innerHTML = '✅ Fatto! Scrivimi i risultati o copia qui: <br><code>' + JSON.stringify(answers) + '</code>';
  }
}
</script></body></html>'''
    (BLIND / 'index.html').write_text(html)
    log(f"  HTML: {BLIND}/index.html")

    log("=== DONE ===")
    log(f"Apri: https://192.168.1.31:5000/blind_test (dopo aver montato la route)")


if __name__ == '__main__':
    main()

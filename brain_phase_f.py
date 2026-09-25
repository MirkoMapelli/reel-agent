#!/usr/bin/env python3
"""Fase F: re-describe scene con vision multi-frame.
Focalizzato su venue detection (muri, scale, monitor, insegne, murales)."""
import os, sys, json, time, glob, base64
from pathlib import Path
from dotenv import load_dotenv

sys.path.insert(0, '/opt/reel-agent/app')
load_dotenv('/opt/reel-agent/.env')
from openai import OpenAI

BRAIN = Path('/opt/reel-agent/brain')
MEDIA = Path('/opt/reel-agent/media')
LOG = BRAIN / 'phase_f.log'
TMP = Path('/tmp/phase_f_frames')
TMP.mkdir(parents=True, exist_ok=True)

client = OpenAI(base_url='https://openrouter.ai/api/v1', api_key=os.environ['OPENROUTER_API_KEY'])
MODEL = 'google/gemini-2.5-flash'


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG, 'a') as f:
        f.write(line + '\n')


def extract_frame(video, ts, out):
    import subprocess
    cmd = ['ffmpeg', '-y', '-v', 'error', '-ss', f'{ts:.3f}', '-i', video,
           '-frames:v', '1', '-vf', 'scale=720:1280:force_original_aspect_ratio=decrease',
           '-q:v', '3', str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    return r.returncode == 0 and out.exists()


def img_uri(p):
    with open(p, 'rb') as f:
        return 'data:image/jpeg;base64,' + base64.b64encode(f.read()).decode()


PROMPT = """Analizza questi 3 frame (inizio, centro, fine) di UNO STESSO SHOT.

Rispondi con un JSON unico. NON un array. Un solo oggetto con questi campi:

{
  "role": "<uno di: food_closeup, food_detail, food_plated, drink_detail, drink_cheers, person_eating, person_talking, hands_gesture, venue_interior_wide, venue_interior_detail, venue_exterior, menu_board, claw_game, detail, other>",
  "subject": "<2-5 parole: soggetto principale>",
  "description": "<1-2 frasi dettagliate su cosa si vede, IN ITALIANO>",
  "key_props": ["<3-6 oggetti visibili: es. 'monitor', 'scale', 'insegna', 'murale', 'tavolo'>"],
  "venue_elements": ["<elementi architettonici visibili: muro, parete, scala, monitor, insegna, murale, vetrina, tavolo, bancone, soffitto, ingresso, esterno>"],
  "food_visible": <true|false>,
  "presence_human": <true|false>,
  "presence_logo": <true|false>
}

REGOLE PER IL ROLE:
- Se vedi ELEMENTI ARCHITETTONICI dominanti (muri, scale, monitor, insegne, murales, bancone, vetrina, sala ampia): usa `venue_interior_wide` o `venue_interior_detail`
- `claw_game` SOLO se la scena e' chiaramente la macchina claw con peluche
- Se il soggetto principale e' una PERSONA che mangia: `person_eating`
- Se e' solo una parte del corpo (mani): `hands_gesture`
- Se e' cibo in primo piano: `food_closeup` o `food_detail`
- Se e' un piatto completo: `food_plated`
- Se e' un bicchiere/bevanda: `drink_detail`
- Se e' un'inquadratura larga dell'ambiente: `venue_interior_wide`
- Se e' un dettaglio dell'ambiente: `venue_interior_detail`
- Se e' l'esterno: `venue_exterior`
- Altrimenti: `detail`

Sii SPECIFICO nella description. Scrivi in italiano.
Rispondi SOLO con JSON, niente markdown.
"""


def re_describe(scene):
    video = scene['clip_path']
    start = float(scene.get('start', 0))
    end = float(scene.get('end', 0))
    dur = max(0.3, end - start)

    ts_list = [start + dur * 0.2, start + dur * 0.5, start + dur * 0.8]
    frames = []
    for i, ts in enumerate(ts_list):
        fp = TMP / f"{scene['clip_name']}_{scene.get('id', 'x')}_{i}.jpg"
        if extract_frame(video, ts, fp):
            frames.append(fp)

    if not frames:
        log(f"  scene {scene.get('id')}: no frames estratti")
        return None

    content = [{'type': 'text', 'text': PROMPT}]
    for f in frames:
        content.append({'type': 'image_url', 'image_url': {'url': img_uri(f)}})

    try:
        r = client.chat.completions.create(
            model=MODEL,
            messages=[{'role': 'user', 'content': content}],
            max_tokens=800, temperature=0.2,
        )
        raw = r.choices[0].message.content.strip()
        if raw.startswith('```'):
            raw = raw.split('\n', 1)[1]
            if raw.endswith('```'):
                raw = raw[:-3]
        i, j = raw.find('{'), raw.rfind('}')
        if i >= 0 and j > i:
            raw = raw[i:j+1]
        return json.loads(raw)
    except Exception as e:
        log(f"  scene {scene.get('id')}: vision error {type(e).__name__}: {str(e)[:100]}")
        return None


def main():
    log("=== Fase F START ===")
    t0 = time.time()

    # Carica curated piu' recente
    cur_files = sorted(glob.glob(str(MEDIA / 'curated' / 'job_*_curated.json')),
                       key=os.path.getmtime, reverse=True)
    cur_path = cur_files[0]
    log(f"curated: {cur_path}")
    c = json.load(open(cur_path))

    scenes = c.get('scenes', [])
    log(f"scene totali: {len(scenes)}")

    # Trova quelle da re-descrivere
    targets = []
    for s in scenes:
        if s.get('decision') != 'keep':
            continue
        desc = (s.get('description') or '').strip()
        role = s.get('role', '')
        if len(desc) < 20 or role in ('detail', 'other', ''):
            targets.append(s)
    log(f"da re-descrivere: {len(targets)}")

    fixed = 0
    for i, s in enumerate(targets, 1):
        log(f"[{i}/{len(targets)}] {s.get('clip_name')} scene_id={s.get('id')}")
        result = re_describe(s)
        if result:
            old_role = s.get('role')
            s['role'] = result.get('role', old_role)
            s['subject'] = result.get('subject', s.get('subject', ''))
            s['description'] = result.get('description', s.get('description', ''))
            s['key_props'] = result.get('key_props', s.get('key_props', []))
            s['venue_elements'] = result.get('venue_elements', [])
            s['food_visible'] = result.get('food_visible', False)
            fixed += 1
            log(f"    {old_role} -> {s['role']} | {s['description'][:70]}")
        time.sleep(0.5)

    # Salva nuovo curated
    c['re_described'] = fixed
    c['re_described_at'] = time.strftime('%Y-%m-%dT%H:%M:%S')
    new_path = MEDIA / 'curated' / 'job_997_curated.json'
    new_path.write_text(json.dumps(c, indent=2, ensure_ascii=False))
    log(f"=== DONE in {time.time()-t0:.0f}s: {fixed} scene fixate ===")
    log(f"nuovo curated: {new_path}")


if __name__ == '__main__':
    main()

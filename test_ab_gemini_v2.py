#!/usr/bin/env python3
"""Test A/B v2: Gemini valuta scene PRE-DEFINITE da PySceneDetect.

PySceneDetect trova i timestamp reali, Gemini guarda il video e
valuta ogni scena applicando il brain. Niente timestamp inventati.
"""
import os, sys, json, base64, subprocess, time
from pathlib import Path
from dotenv import load_dotenv

BASE = Path('/opt/reel-agent')
load_dotenv(BASE / '.env')
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / 'app'))
from app.workers.analyze_raw import detect_scenes
from openai import OpenAI

CLIENT = OpenAI(base_url='https://openrouter.ai/api/v1',
                api_key=os.environ['OPENROUTER_API_KEY'])
MODEL = 'google/gemini-2.5-flash-lite'
TMP = Path('/tmp')


def compress_video(src, dst):
    cmd = ['ffmpeg', '-y', '-v', 'error', '-i', str(src),
           '-vf', 'scale=720:-2', '-c:v', 'libx264', '-preset', 'veryfast',
           '-crf', '28', '-b:v', '1M', '-maxrate', '1.5M', '-an', str(dst)]
    r = subprocess.run(cmd, capture_output=True, timeout=60)
    if r.returncode != 0 or not dst.exists():
        raise RuntimeError(f'ffmpeg fail: {r.stderr[:300]}')
    return dst.stat().st_size / 1024 / 1024


def video_to_data_uri(path):
    with open(path, 'rb') as f:
        return 'data:video/mp4;base64,' + base64.b64encode(f.read()).decode()


def build_prompt(brain, settings, scenes):
    vm = brain.get('visual_memory') or {}
    nm = brain.get('narrative_memory') or {}
    lm = brain.get('lexical_memory') or {}
    notes = settings.get('user_notes', [])
    if isinstance(notes, str):
        notes = [n.strip() for n in notes.split('\n') if n.strip()]
    notes_block = '\n'.join(f'- {n}' for n in notes) if notes else '(nessuna)'

    # Elenco scene con timestamp reali
    scenes_txt = '\n'.join(
        f"SCENA {i}: {s:.2f}s → {e:.2f}s (durata {e-s:.2f}s)"
        for i, (s, e) in enumerate(scenes)
    )

    return f"""Sei il clone digitale del creator TikTok @jay.emme.

# BRAIN — VISUAL SIGNATURE
{vm.get('visual_signature','')}

# MOSSE VISIVE FIRMA
{chr(10).join('- ' + m for m in vm.get('signature_moves', [])[:8])}

# NARRATIVE
{nm.get('narrative_signature','')}

# NOTE UTENTE (PRIORITÀ MASSIMA)
{notes_block}

# REGOLE ASSOLUTE
- Evita smorfie, "espressione concentrata", "occhi chiusi", "testa china"
- NON descrivere l'azione della mano ("puccio", "mordo", "ne prendo")
- Preferisci scene con: morso ben a fuoco, cibo mostrato a camera, volto sorridente
- Evita scene sfocate o di transizione

# COMPITO
Guarda il video qui allegato. Un algoritmo di scene detection ha GIA' identificato
queste {len(scenes)} scene (timestamp reali, non inventare):

{scenes_txt}

Per OGNI scena, guarda il video nel range indicato e rispondi con un oggetto.
Restituisci JSON:

{{
  "evaluations": [
    {{
      "scene_idx": 0,
      "pick": true,
      "action": "bite|presentation|chewing|close_up|pouring|other",
      "focus": 0.85,
      "preferred_cut_rel": 1.2,
      "reason": "1 frase specifica (max 15 parole)"
    }},
    ...
  ]
}}

REGOLE:
- `scene_idx` DEVE essere 0..{len(scenes)-1}
- `focus` = nitidezza nel momento chiave (0=sfocato, 1=nitido)
- `preferred_cut_rel` = secondo RELATIVO all'inizio della scena dove inizia il taglio migliore (es. 1.2s = inizia a 1.2s dall'inizio scena). 0 se dall'inizio.
- `pick` = true se la useresti, false se la scarti
- `reason` = SPECIFICA, non generica. Es: "morso con sorriso" NON "alternanza ritmica"
- Se una scena è smorfia/occhi chiusi/sfocata → pick=false
- Devi valutare TUTTE le {len(scenes)} scene, non solo quelle che scegli

Rispondi SOLO con JSON valido.
"""


def main():
    brain = json.load(open(BASE / 'brain' / 'active_brain.json'))
    settings = json.load(open(BASE / 'brain' / 'settings.json'))

    raw = BASE / 'media' / 'raw' / 'IMG_6823.MOV'
    print(f'=== {raw.name} ===')

    print('  scene detection...')
    scenes = detect_scenes(str(raw))
    print(f'  {len(scenes)} scene reali: {[(round(s,1), round(e,1)) for s,e in scenes[:5]]}...')

    print('  compress...')
    comp = TMP / f'{raw.stem}_v2.mp4'
    size_mb = compress_video(raw, comp)
    print(f'  compressed: {size_mb:.2f} MB')

    data_uri = video_to_data_uri(comp)
    prompt = build_prompt(brain, settings, scenes)
    print(f'  prompt: {len(prompt)} char')

    print('  call Gemini...')
    t0 = time.time()
    r = CLIENT.chat.completions.create(
        model=MODEL,
        messages=[{'role': 'user', 'content': [
            {'type': 'text', 'text': prompt},
            {'type': 'image_url', 'image_url': {'url': data_uri}},
        ]}],
        max_tokens=4000, temperature=0.3,
    )
    print(f'  risposta in {time.time()-t0:.1f}s')

    raw_resp = r.choices[0].message.content
    t = raw_resp.strip()
    if t.startswith('```'):
        t = t.split('\n', 1)[1]
        if t.endswith('```'): t = t[:-3]
    i, j = t.find('{'), t.rfind('}')
    if i >= 0 and j > i: t = t[i:j+1]
    data = json.loads(t)

    out = TMP / f'test_ab_v2_{raw.stem}.json'
    out.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    print(f'  saved: {out}')

    evals = data.get('evaluations', [])
    picks = [e for e in evals if e.get('pick')]
    print(f'\n  --- {len(evals)} scene valutate, {len(picks)} pick ---')
    for e in evals:
        idx = e.get('scene_idx')
        s, en = scenes[idx] if idx is not None and idx < len(scenes) else (0, 0)
        mark = '✅' if e.get('pick') else '❌'
        print(f'  {mark} [{s:.1f}-{en:.1f}] {e.get("action","?"):12} focus={e.get("focus")} cut+{e.get("preferred_cut_rel")}s | {e.get("reason","")[:70]}')


if __name__ == '__main__':
    main()

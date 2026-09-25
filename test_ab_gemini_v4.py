#!/usr/bin/env python3
"""Test A/B v3: PySceneDetect + split ritmico → Gemini valuta i subclip.

Pipeline corretta:
1. detect_scenes → scene macro (spesso 1 sola)
2. _split_scene_with_rhythm → subclip di 2-3s (i veri candidati)
3. Gemini riceve video + lista subclip + brain → scegle
"""
import os, sys, json, base64, subprocess, time
from pathlib import Path
from dotenv import load_dotenv

BASE = Path('/opt/reel-agent')
load_dotenv(BASE / '.env')
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / 'app'))
from app.workers.analyze_raw import detect_scenes, _split_scene_with_rhythm
from openai import OpenAI

CLIENT = OpenAI(base_url='https://openrouter.ai/api/v1',
                api_key=os.environ['OPENROUTER_API_KEY'])
MODEL = 'google/gemini-2.5-flash'
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


def build_prompt(brain, settings, subclips):
    vm = brain.get('visual_memory') or {}
    nm = brain.get('narrative_memory') or {}
    notes = settings.get('user_notes', [])
    if isinstance(notes, str):
        notes = [n.strip() for n in notes.split('\n') if n.strip()]
    notes_block = '\n'.join(f'- {n}' for n in notes) if notes else '(nessuna)'

    subclips_txt = '\n'.join(
        f"CLIP {i}: {s:.2f}s → {e:.2f}s (durata {e-s:.2f}s)"
        for i, (s, e) in enumerate(subclips)
    )

    return f"""Sei il clone digitale del creator TikTok @jay.emme.

# VISUAL SIGNATURE
{vm.get('visual_signature','')}

# MOSSE FIRMA
{chr(10).join('- ' + m for m in vm.get('signature_moves', [])[:6])}

# NOTE UTENTE
{notes_block}

# COMPITO
Guarda il video allegato (raw del creator). Un algoritmo ha spezzato il video
in {len(subclips)} subclip CANDIDATI (timestamp reali):

{subclips_txt}

Per OGNI clip, guarda il video nel suo range e valuta applicando il brain.
Restituisci JSON:

{{
  "evaluations": [
    {{
      "clip_idx": 0,
      "pick": true,
      "action": "bite|presentation|chewing|close_up|pouring|other",
      "focus": 0.85,
      "reason": "frase specifica max 12 parole"
    }}
  ]
}}

REGOLE:
- `clip_idx`: 0..{len(subclips)-1}
- `focus`: 0=sfocato 1=nitido
- `pick` true se la useresti
- `action`: "bite" solo se il cibo viene morso IN QUESTA clip
- `reason`: SPECIFICA (max 12 parole). NON "alternanza ritmica". Es: "morso ala con sorriso"
- SMORFIA / occhi chiusi / testa china / espressione concentrata → pick=false
- Sfocato o transizione → pick=false
- Devi valutare TUTTE le {len(subclips)} clip

Rispondi SOLO con JSON.
"""


def main():
    brain = json.load(open(BASE / 'brain' / 'active_brain.json'))
    settings = json.load(open(BASE / 'brain' / 'settings.json'))

    raw = BASE / 'media' / 'raw' / 'IMG_6823.MOV'
    print(f'=== {raw.name} ===')

    print('  scene detection...')
    scenes = detect_scenes(str(raw))
    print(f'  {len(scenes)} scene macro')

    subclips = []
    for s, e in scenes:
        subclips.extend(_split_scene_with_rhythm(s, e, video_seed=1))
    print(f'  {len(subclips)} subclip dopo split ritmico')
    for i, (s, e) in enumerate(subclips):
        print(f'    clip {i}: {s:.2f} → {e:.2f} ({e-s:.2f}s)')

    print('  compress...')
    comp = TMP / f'{raw.stem}_v3.mp4'
    size_mb = compress_video(raw, comp)
    print(f'  compressed: {size_mb:.2f} MB')

    data_uri = video_to_data_uri(comp)
    prompt = build_prompt(brain, settings, subclips)
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

    out = TMP / f'test_ab_v3_{raw.stem}.json'
    out.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    print(f'  saved: {out}')

    evals = {e.get('clip_idx'): e for e in data.get('evaluations', [])}
    picks = [e for e in evals.values() if e.get('pick')]
    print(f'\n  --- {len(evals)}/{len(subclips)} valutate, {len(picks)} pick ---')
    for i, (s, e) in enumerate(subclips):
        ev = evals.get(i, {})
        mark = '✅' if ev.get('pick') else '❌'
        act = ev.get('action', '?')
        foc = ev.get('focus', '?')
        rsn = (ev.get('reason') or '')[:70]
        print(f'  {mark} [{s:.1f}-{e:.1f}] {act:12} focus={foc} | {rsn}')


if __name__ == '__main__':
    main()

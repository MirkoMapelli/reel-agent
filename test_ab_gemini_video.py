#!/usr/bin/env python3
"""Test A/B: frame-based (attuale) vs video+ brain (nuovo).

Per 2 raw, chiama Gemini con video intero + brain in context,
chiede di scegliere le scene da usare.
Output: /tmp/test_ab_<raw>.json
"""
import os, sys, json, base64, subprocess, time
from pathlib import Path
from dotenv import load_dotenv

BASE = Path('/opt/reel-agent')
load_dotenv(BASE / '.env')
from openai import OpenAI

CLIENT = OpenAI(
    base_url='https://openrouter.ai/api/v1',
    api_key=os.environ['OPENROUTER_API_KEY'],
)
MODEL = 'google/gemini-2.5-flash-lite'   # economico, video nativo
TMP = Path('/tmp')
TMP.mkdir(exist_ok=True)


def compress_video(src, dst, max_mb=5):
    """Comprimi raw a 720p ~1Mbps no audio."""
    cmd = [
        'ffmpeg', '-y', '-v', 'error',
        '-i', str(src),
        '-vf', 'scale=720:-2',
        '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '28',
        '-b:v', '1M', '-maxrate', '1.5M',
        '-an',
        str(dst),
    ]
    r = subprocess.run(cmd, capture_output=True, timeout=60)
    if r.returncode != 0 or not dst.exists():
        raise RuntimeError(f'ffmpeg fail: {r.stderr[:300]}')
    size_mb = dst.stat().st_size / 1024 / 1024
    print(f'  compressed: {size_mb:.2f} MB')
    return size_mb


def video_to_data_uri(path):
    with open(path, 'rb') as f:
        b = f.read()
    return 'data:video/mp4;base64,' + base64.b64encode(b).decode()


def build_brain_prompt(brain, settings):
    """Prompt = brain sintetico + user notes."""
    vm = brain.get('visual_memory') or {}
    nm = brain.get('narrative_memory') or {}
    lm = brain.get('lexical_memory') or {}

    notes = settings.get('user_notes', [])
    if isinstance(notes, str):
        notes = [n.strip() for n in notes.split('\n') if n.strip()]
    notes_block = '\n'.join(f'- {n}' for n in notes) if notes else '(nessuna)'

    return f"""Sei il clone digitale del creator TikTok @jay.emme.

# CHI SEI (visual)
{vm.get('visual_signature','')}

# MOSSE VISIVE FIRMA
{chr(10).join('- ' + m for m in vm.get('signature_moves', [])[:8])}

# APERTURA
{chr(10).join('- ' + r for r in vm.get('opening_rules', [])[:5])}

# CHIUSURA
{chr(10).join('- ' + r for r in vm.get('closing_rules', [])[:5])}

# COME RACCONTI
{nm.get('narrative_signature','')}

# STILE LINGUISTICO
{lm.get('language_signature','')}

# 🚨 NOTE UTENTE (PRIORITÀ MASSIMA)
{notes_block}

# 🚫 REGOLE ASSOLUTE
- Non descrivere l'azione della mano ("puccio", "mordo", "ne prendo")
- Commenta sempre il CIBO, la SALSA, il LOCALE
- Evita smorfie, "espressione concentrata", "occhi chiusi", "testa china"
- Evita "il locale ha X", "c'è anche X", "ci sono X" (descrizioni)
- Se un raw è scarso, puoi dire no_picks=true

# COMPITO
Guarda il video raw qui allegato. Contiene N scene girate dal creator.
Il creator monterà il video finale scegliendo ALCUNE scene.

Devi SELEZIONARE le scene che il creator userebbe, applicando il brain.
Restituisci JSON:

{{
  "raw_name": "...",
  "n_scenes_detected": <int>,
  "no_picks": <true|false>,
  "scenes": [
    {{
      "start": <float, secondo nel video raw>,
      "end": <float>,
      "pick": <true|false>,
      "priority": "high|medium|low",
      "reason": "breve motivazione",
      "cut_in": <float, secondo di inizio della clip da usare>,
      "cut_out": <float, secondo di fine>,
      "action": "presentation|bite|pouring|chewing|close_up|other",
      "focus": <0.0-1.0>,
      "desc": "breve descrizione"
    }}
  ]
}}

REGOLE:
- cut_in/cut_out devono essere dentro [start, end]
- Se c'è un morso, cut_in/cut_out attorno al morso
- Se c'è il momento di mostrare il cibo a camera, taglia lì
- Se una scena è "concentrata/smorfia/occhi chiusi", pick=false
- Se TUTTE le scene sono scarso: no_picks=true, scenes=[]

Rispondi SOLO con JSON valido.
"""


def test_raw(raw_path, brain, settings):
    raw = Path(raw_path)
    print(f'\n=== {raw.name} ===')
    print(f'  compress...')
    comp = TMP / f'{raw.stem}_720p.mp4'
    size_mb = compress_video(raw, comp)

    print(f'  encode base64...')
    data_uri = video_to_data_uri(comp)

    prompt = build_brain_prompt(brain, settings)
    print(f'  prompt: {len(prompt)} char')

    print(f'  call {MODEL}...')
    t0 = time.time()
    try:
        r = CLIENT.chat.completions.create(
            model=MODEL,
            messages=[{
                'role': 'user',
                'content': [
                    {'type': 'text', 'text': prompt},
                    {'type': 'image_url', 'image_url': {'url': data_uri}},
                ],
            }],
            max_tokens=4000,
            temperature=0.3,
        )
        raw_resp = r.choices[0].message.content
        print(f'  risposta in {time.time()-t0:.1f}s ({len(raw_resp)} char)')

        # Parse JSON
        t = raw_resp.strip()
        if t.startswith('```'):
            t = t.split('\n', 1)[1]
            if t.endswith('```'):
                t = t[:-3]
        i, j = t.find('{'), t.rfind('}')
        if i >= 0 and j > i:
            t = t[i:j+1]
        data = json.loads(t)

        out = TMP / f'test_ab_{raw.stem}.json'
        out.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        print(f'  saved: {out}')
        return data
    except Exception as e:
        print(f'  ERRORE: {type(e).__name__}: {str(e)[:300]}')
        return None


def main():
    brain = json.load(open(BASE / 'brain' / 'active_brain.json'))
    settings = json.load(open(BASE / 'brain' / 'settings.json'))
    print(f'brain schema: {brain.get("schema_version")}')
    print(f'settings note: {len(settings.get("user_notes", []))}')

    for raw in ['media/raw/IMG_6821.MOV', 'media/raw/IMG_6823.MOV']:
        data = test_raw(BASE / raw, brain, settings)
        if data:
            print(f'\n  --- RISULTATO ---')
            print(f'  n_scenes: {data.get("n_scenes_detected")}')
            print(f'  no_picks: {data.get("no_picks")}')
            picks = [s for s in data.get('scenes', []) if s.get('pick')]
            print(f'  picks: {len(picks)}')
            for s in picks[:6]:
                print(f'    [{s.get("start")}-{s.get("end")}] {s.get("action")} focus={s.get("focus")} | {s.get("reason","")[:80]}')


if __name__ == '__main__':
    main()

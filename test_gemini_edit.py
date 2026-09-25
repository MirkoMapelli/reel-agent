#!/usr/bin/env python3
"""Test: concat 5 raw + timestamp visibile -> Gemini edit con brain.

Output: /tmp/gemini_edit_test.json con cut list.
"""
import os, sys, json, base64, subprocess, time
from pathlib import Path
from dotenv import load_dotenv

BASE = Path('/opt/reel-agent')
load_dotenv(BASE / '.env')
from openai import OpenAI

CLIENT = OpenAI(base_url='https://openrouter.ai/api/v1',
                api_key=os.environ['OPENROUTER_API_KEY'])
MODEL = 'google/gemini-2.5-flash'  # full, non lite
TMP = Path('/tmp')

# 5 raw da testare (mix: cibo, persone, venue)
RAWS = [
    'IMG_6813.MOV',   # venue / totem
    'IMG_6820.MOV',   # cibo plated
    'IMG_6821.MOV',   # food closeup
    'IMG_6823.MOV',   # persona mangia
    'IMG_6835.MOV',   # drink
]


def probe_duration(path):
    r = subprocess.run(
        ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
         '-of', 'default=noprint_wrappers=1:nokey=1', str(path)],
        capture_output=True, text=True, timeout=10)
    try:
        return float(r.stdout.strip())
    except Exception:
        return 0


def build_master(raw_paths):
    """Concat con FILTER (decode+re-encode in RAM). Immune a freeze."""
    TMP = Path('/tmp')
    for pat in ('gemini_master*.mp4', 'norm_*.mp4', 'concat_list.txt'):
        for f in TMP.glob(pat):
            try: f.unlink()
            except: pass

    TARGET_W, TARGET_H = 480, 854
    n = len(raw_paths)

    print(f'[1/3] Concat filter su {n} raw (unica passata)...')

    # Offset cumulativi (per la mappa raw->master)
    offsets = {}
    cum = 0.0
    for raw in raw_paths:
        raw_name = Path(raw).name
        offsets[raw_name] = cum
        cum += probe_duration(raw)

    # Costruisci filter_complex
    inputs = []
    for raw in raw_paths:
        inputs += ['-i', str(raw)]

    fc_parts = []
    for i in range(n):
        fc_parts.append(
            f"[{i}:v]"
            f"fps=30,"
            f"scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=decrease,"
            f"pad={TARGET_W}:{TARGET_H}:(ow-iw)/2:(oh-ih)/2:color=black,"
            f"setsar=1,"
            f"format=yuv420p"
            f"[v{i}]"
        )
    # concat
    stream_spec = ''.join(f'[v{i}]' for i in range(n))
    fc_parts.append(f"{stream_spec}concat=n={n}:v=1:a=0[vc]")
    # drawtext sul concat
    drawtext = (
        "drawtext="
        "text='%{pts\\:hms}':"
        "fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf:"
        "x=w-tw-20:y=20:"
        "fontsize=28:"
        "fontcolor=white:"
        "borderw=3:bordercolor=black:"
        "box=1:boxcolor=black@0.5:boxborderw=8"
    )
    fc_parts.append(f"[vc]{drawtext}[vout]")

    filter_complex = ';'.join(fc_parts)

    master = TMP / 'gemini_master.mp4'
    cmd = ['ffmpeg', '-y', '-v', 'error'] + inputs + [
        '-filter_complex', filter_complex,
        '-map', '[vout]',
        '-an',
        '-c:v', 'libx264',
        '-preset', 'veryfast',
        '-profile:v', 'baseline',
        '-level', '3.1',
        '-r', '30',
        '-vsync', 'cfr',
        '-crf', '28',
        '-maxrate', '500k', '-bufsize', '1000k',
        '-pix_fmt', 'yuv420p',
        '-movflags', '+faststart',
        str(master),
    ]

    print(f'  running ffmpeg (potrebbe richiedere 2-4 minuti)...')
    import time as _t
    t0 = _t.time()
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    dt = _t.time() - t0
    if r.returncode != 0:
        print(f'  ✗ ffmpeg fail: {r.stderr[-500:]}')
        return None

    size_mb = master.stat().st_size / 1024 / 1024
    print(f'[2/3] Master creato in {dt:.0f}s ({size_mb:.1f} MB)')
    print(f'[3/3] Durata totale: {cum:.1f}s')
    return str(master), offsets, cum


def build_prompt(brain, settings, offsets, raw_names, master_dur):
    vm = brain.get('visual_memory') or {}
    nm = brain.get('narrative_memory') or {}
    lm = brain.get('lexical_memory') or {}

    notes = settings.get('user_notes', [])
    if isinstance(notes, str):
        notes = [n.strip() for n in notes.split('\n') if n.strip()]
    notes_block = '\n'.join(f'- {n}' for n in notes) if notes else '(nessuna)'

    offsets_txt = '\n'.join(
        f'- {name}: inizia a {off:.2f}s nel master'
        for name, off in offsets.items()
    )

    return f"""Sei il MONTATORE/EDITOR del creator TikTok @jay.emme.

Ricevi un video master di {master_dur:.1f}s che concatena {len(raw_names)} clip
girate dal creator. In ALTO A DESTRA di ogni frame c'è un TIMESTAMP che mostra
il tempo ASSOLUTO nel master (HH:MM:SS.CC).

# IL TUO BRAIN (chi sei come creator)

## VISUAL SIGNATURE
{vm.get('visual_signature','')}

## MOSSE VISIVE FIRMA
{chr(10).join('- ' + m for m in vm.get('signature_moves', [])[:6])}

## NARRATIVE
{nm.get('narrative_signature','')}

## STILE LINGUISTICO
{lm.get('language_signature','')}

## 🚨 NOTE UTENTE (PRIORITÀ MASSIMA)
{notes_block}

# 🚫 REGOLE ASSOLUTE
- Evita smorfie, "espressione concentrata", "occhi chiusi", "testa china"
- NON descrivere l'azione della mano ("puccio", "mordo")
- Commenta sempre CIBO, SALSA, LOCALE (mai azioni fisiche)
- Preferisci morsi ben a fuoco, cibo mostrato a camera, volti sorridenti
- Apri con shot del locale se possibile
- Chiudi con CTA (volto sorridente o venue)

# MAPPA RAW NEL MASTER
{offsets_txt}

# COMPITO
Guarda il master video. Costruisci una TIMELINE per un video TikTok finale
di ~60-70 secondi, applicando il brain.

Scegli 25-32 momenti (shot) da usare, con timestamp PRECISI letti dal master.
Ogni shot dura tra 1.5s e 3.5s.

Restituisci JSON:
{{
  "timeline": [
    {{
      "start_master": 3.45,
      "end_master": 5.12,
      "role": "food_closeup | food_detail | food_plated | drink_detail | person_eating | person_talking | hands_gesture | venue_interior_wide | venue_interior_detail | other",
      "comment": "commento del creator (max 12 parole, in italiano)",
      "reason": "breve motivazione (max 12 parole)",
      "priority": "high | medium | low"
    }}
  ],
  "total_duration": 65.3,
  "voiceover_script": "testo completo del voiceover",
  "opening_note": "cosa hai scelto come apertura",
  "closing_note": "cosa hai scelto come chiusura"
}}

REGOLE:
- start_master/end_master DEVONO essere numeri precisi (leggi dal timestamp visibile)
- Ordine cronologico NON necessario: puoi saltare tra i raw
- Preferisci varietà visiva (non due food_closeup di fila)
- `comment` = VOICEOVER effettivo, non descrizione
- Massimo 32 shot, minimo 25
- Devono essere entro {master_dur:.1f}s

Rispondi SOLO con JSON.
"""


def main():
    print('=== TEST GEMINI EDIT ===\n')

    raw_paths = [str(BASE / 'media' / 'raw' / r) for r in RAWS]
    for r in raw_paths:
        if not Path(r).exists():
            print(f'ERRORE: {r} non trovato')
            return

    # 1. Build master
    result = build_master(raw_paths)
    if not result:
        return
    master_path, offsets, master_dur = result

    # 2. Carica brain + settings
    print()
    print('[2/4] Carico brain...')
    brain = json.load(open(BASE / 'brain' / 'active_brain.json'))
    settings = json.load(open(BASE / 'brain' / 'settings.json'))
    print(f'  brain schema: {brain.get("schema_version")}')
    notes = settings.get('user_notes', [])
    if isinstance(notes, str): notes = [n.strip() for n in notes.split('\n') if n.strip()]
    print(f'  note utente: {len(notes)}')

    # 3. Base64 encode
    print()
    print('[3/4] Encode base64...')
    with open(master_path, 'rb') as f:
        video_data = f.read()
    data_uri = 'data:video/mp4;base64,' + base64.b64encode(video_data).decode()
    print(f'  base64 size: {len(data_uri) / 1024 / 1024:.1f} MB')

    # 4. Chiama Gemini
    print()
    print('[4/4] Chiamata Gemini (Flash)...')
    prompt = build_prompt(brain, settings, offsets, RAWS, master_dur)
    print(f'  prompt: {len(prompt)} char')

    t0 = time.time()
    try:
        r = CLIENT.chat.completions.create(
            model=MODEL,
            messages=[{'role': 'user', 'content': [
                {'type': 'text', 'text': prompt},
                {'type': 'image_url', 'image_url': {'url': data_uri}},
            ]}],
            max_tokens=8000,
            temperature=0.3,
        )
    except Exception as e:
        print(f'  ERRORE: {type(e).__name__}: {str(e)[:400]}')
        return

    dt = time.time() - t0
    raw_resp = r.choices[0].message.content
    print(f'  risposta in {dt:.1f}s ({len(raw_resp)} char)')

    # Parse
    t = raw_resp.strip()
    if t.startswith('```'):
        t = t.split('\n', 1)[1]
        if t.endswith('```'):
            t = t[:-3]
    i, j = t.find('{'), t.rfind('}')
    if i >= 0 and j > i:
        t = t[i:j+1]

    try:
        data = json.loads(t)
    except Exception as e:
        print(f'  PARSE ERRORE: {e}')
        print('  RAW (primi 800 char):')
        print(raw_resp[:800])
        # Salva raw per debug
        (TMP / 'gemini_edit_raw.txt').write_text(raw_resp)
        print(f'  salvato raw in /tmp/gemini_edit_raw.txt')
        return

    out = TMP / 'gemini_edit_test.json'
    out.write_text(json.dumps(data, indent=2, ensure_ascii=False))

    # Report
    print()
    print('=' * 70)
    print('RISULTATO')
    print('=' * 70)
    timeline = data.get('timeline', [])
    print(f'  n shot: {len(timeline)}')
    print(f'  durata totale: {data.get("total_duration")}s')
    print(f'  opening: {data.get("opening_note", "")[:80]}')
    print(f'  closing: {data.get("closing_note", "")[:80]}')
    print()
    print(f'  {"#":>3} {"start":>8} {"end":>8} {"dur":>5} {"role":<22} | comment')
    print('  ' + '-' * 90)
    for i, s in enumerate(timeline):
        dur = s.get('end_master', 0) - s.get('start_master', 0)
        print(f'  {i+1:>3} {s.get("start_master",0):>8.2f} {s.get("end_master",0):>8.2f} {dur:>5.2f} {s.get("role","?"):<22} | {s.get("comment","")[:60]}')

    print()
    print('VOICEOVER:')
    print(data.get('voiceover_script', '')[:600])

    print()
    print(f'saved: {out}')


if __name__ == '__main__':
    main()

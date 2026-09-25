#!/usr/bin/env python3
"""Test Gemini edit da master gia' pronto (/tmp/gemini_master.mp4).

Il master e':
  - 480x854 verticale
  - 30fps CFR
  - 5 clip concatenate (IMG_6813, 6820, 6821, 6823, 6835)
  - timestamp HH:MM:SS.CC visibile in alto a destra
  - durata ~92.3s
"""
import os, sys, json, base64, time
from pathlib import Path
from dotenv import load_dotenv

BASE = Path('/opt/reel-agent')
load_dotenv(BASE / '.env')
from openai import OpenAI

CLIENT = OpenAI(base_url='https://openrouter.ai/api/v1',
                api_key=os.environ['OPENROUTER_API_KEY'])
MODEL = 'google/gemini-2.5-flash'
TMP = Path('/tmp')

# Offset dei 5 raw nel master (dallo step precedente)
OFFSETS = {
    'IMG_6813.MOV': 0.00,
    'IMG_6820.MOV': 3.77,
    'IMG_6821.MOV': 11.57,
    'IMG_6823.MOV': 38.61,
    'IMG_6835.MOV': 65.87,
}
MASTER_DUR = 92.31


def build_prompt(brain, settings):
    vm = brain.get('visual_memory') or {}
    nm = brain.get('narrative_memory') or {}
    lm = brain.get('lexical_memory') or {}

    notes = settings.get('user_notes', [])
    if isinstance(notes, str):
        notes = [n.strip() for n in notes.split('\n') if n.strip()]
    notes_block = '\n'.join(f'- {n}' for n in notes) if notes else '(nessuna)'

    offsets_txt = '\n'.join(f'- {k}: da {v:.2f}s a ...' for k, v in OFFSETS.items())

    return f"""Sei il MONTATORE/EDITOR del creator TikTok @jay.emme.

Ricevi un video master di {MASTER_DUR:.1f}s che concatena 5 clip girate dal creator.
In ALTO A DESTRA di ogni frame c'è un TIMESTAMP con il tempo ASSOLUTO del master
(formato HH:MM:SS.CC). DEVI leggere il timestamp dal frame per determinare
i secondi esatti.

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
- Preferisci: morsi ben a fuoco, cibo mostrato a camera, volti sorridenti
- Apri con shot del locale se possibile
- Chiudi con CTA (volto sorridente o venue)

# MAPPA RAW NEL MASTER
{offsets_txt}

# COMPITO
Guarda il master video. Costruisci una TIMELINE per un video TikTok finale
di ~60-70 secondi, applicando il brain.

Scegli 25-32 momenti (shot) da usare. Ogni shot dura tra 1.5s e 3.5s.
Per ogni shot, leggi il TIMESTAMP in alto a destra e scrivi start/end ESATTI.

Restituisci JSON:
{{
  "timeline": [
    {{
      "start_master": 3.45,
      "end_master": 5.12,
      "role": "food_closeup | food_detail | food_plated | drink_detail | person_eating | person_talking | hands_gesture | venue_interior_wide | venue_interior_detail | other",
      "comment": "commento del creator (max 12 parole, italiano)",
      "reason": "breve motivazione (max 12 parole)",
      "priority": "high | medium | low"
    }}
  ],
  "total_duration": 65.3,
  "voiceover_script": "testo completo del voiceover",
  "opening_note": "cosa hai scelto come apertura",
  "closing_note": "cosa hai scelto come chiusura"
}}

REGOLE CRITICHE:
- start_master/end_master DEVONO essere precisi (leggi dal timestamp visibile!)
- start_master >= 0 e end_master <= {MASTER_DUR:.2f}
- Ogni shot 1.5-3.5s
- Ordine cronologico NON obbligatorio
- Preferisci varietà visiva (no due food_closeup di fila)
- `comment` = VOICEOVER effettivo (non descrizione)
- 25-32 shot totali

Rispondi SOLO con JSON valido.
"""


def main():
    master = TMP / 'gemini_master.mp4'
    if not master.exists():
        print(f'ERRORE: {master} non trovato')
        return

    size_mb = master.stat().st_size / 1024 / 1024
    print(f'=== TEST GEMINI EDIT ===')
    print(f'master: {master.name} ({size_mb:.1f} MB, {MASTER_DUR:.1f}s)')
    print()

    # Brain
    print('[1/3] Carico brain...')
    brain = json.load(open(BASE / 'brain' / 'active_brain.json'))
    settings = json.load(open(BASE / 'brain' / 'settings.json'))
    print(f'  schema: {brain.get("schema_version")}')
    notes = settings.get('user_notes', [])
    if isinstance(notes, str): notes = [n.strip() for n in notes.split('\n') if n.strip()]
    print(f'  note: {len(notes)}')

    # Base64
    print('[2/3] Encode base64...')
    with open(master, 'rb') as f:
        video_data = f.read()
    data_uri = 'data:video/mp4;base64,' + base64.b64encode(video_data).decode()
    print(f'  base64: {len(data_uri) / 1024 / 1024:.1f} MB')

    # Prompt
    prompt = build_prompt(brain, settings)
    print(f'  prompt: {len(prompt)} char')

    # Gemini
    print('[3/3] Chiamata Gemini Flash...')
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
    raw = r.choices[0].message.content
    print(f'  risposta in {dt:.1f}s ({len(raw)} char)')

    # Parse
    t = raw.strip()
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
        (TMP / 'gemini_edit_raw.txt').write_text(raw)
        print(f'  salvato in /tmp/gemini_edit_raw.txt')
        print('  primi 1500 char:')
        print(raw[:1500])
        return

    out = TMP / 'gemini_edit_result.json'
    out.write_text(json.dumps(data, indent=2, ensure_ascii=False))

    # Report
    print()
    print('=' * 80)
    print('RISULTATO')
    print('=' * 80)
    timeline = data.get('timeline', [])
    print(f'  n shot: {len(timeline)}')
    print(f'  durata dichiarata: {data.get("total_duration")}s')
    print(f'  apertura: {data.get("opening_note", "")[:80]}')
    print(f'  chiusura: {data.get("closing_note", "")[:80]}')
    print()
    print(f'  {"#":>3} {"start":>7} {"end":>7} {"dur":>5} {"role":<22} | comment')
    print('  ' + '-' * 95)
    for i, s in enumerate(timeline):
        dur = s.get('end_master', 0) - s.get('start_master', 0)
        print(f'  {i+1:>3} {s.get("start_master",0):>7.2f} {s.get("end_master",0):>7.2f} {dur:>5.2f} {s.get("role","?"):<22} | {s.get("comment","")[:55]}')

    # Analisi automatica
    from collections import Counter
    print()
    print('  --- ANALISI ---')
    roles = Counter(s.get('role','?') for s in timeline)
    print(f'  ruoli: {dict(roles)}')
    comments = [s.get('comment','') for s in timeline if s.get('comment')]
    print(f'  commenti unici: {len(set(comments))}/{len(comments)}')
    starts = sorted([s.get('start_master',0) for s in timeline])
    print(f'  range timestamp: {starts[0]:.2f}s → {starts[-1]:.2f}s')
    # Controllo se sono tutti nella stessa zona (sospetto "inventati")
    import statistics
    if len(starts) > 2:
        diffs = [starts[i+1] - starts[i] for i in range(len(starts)-1)]
        mean_d = statistics.mean(diffs)
        stdev = statistics.stdev(diffs) if len(diffs) > 1 else 0
        print(f'  intervalli tra shot: media {mean_d:.2f}s, dev std {stdev:.2f}s')
        if stdev < 0.3:
            print(f'  ⚠️  intervalli troppo regolari — timestamp probabilmente inventati')
        else:
            print(f'  ✓ intervalli irregolari — timestamp veri')

    print()
    print('VOICEOVER:')
    print((data.get('voiceover_script') or '')[:800])
    print()
    print(f'saved: {out}')


if __name__ == '__main__':
    main()

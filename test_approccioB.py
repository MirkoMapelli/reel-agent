#!/usr/bin/env python3
"""Approccio B: Gemini come EDITOR (senza timestamp), Python mappa.

- 10 raw concat in master 480x854 senza timestamp
- Gemini vede il master + brain, produce shot list con:
    source_raw + part_in_raw (start/middle/end) + role + comment + reason
- Python mappa: apre media/analysis/{raw}.json, trova la scena che matcha
  la part, calcola timestamp master = offset_raw + scene.start
"""
import os, sys, json, base64, subprocess, time
from pathlib import Path
from dotenv import load_dotenv

BASE = Path('/opt/reel-agent')
load_dotenv(BASE / '.env')
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / 'app'))
from openai import OpenAI

CLIENT = OpenAI(base_url='https://openrouter.ai/api/v1',
                api_key=os.environ['OPENROUTER_API_KEY'])
MODEL = 'google/gemini-2.5-flash'
TMP = Path('/tmp')

RAWS_10 = [
    'IMG_6813.MOV',  # venue: totem
    'IMG_6815.MOV',  # venue: scala/monitor
    'IMG_6816.MOV',  # venue: insegna ali
    'IMG_6820.MOV',  # food plated
    'IMG_6821.MOV',  # food closeup
    'IMG_6823.MOV',  # persona mangia
    'IMG_6825.MOV',  # hands with fries
    'IMG_6829.MOV',  # food detail / salsa
    'IMG_6835.MOV',  # drink
    'IMG_6836.MOV',  # drink
]


def probe_dur(p):
    r = subprocess.run(['ffprobe', '-v', 'error', '-show_entries',
                        'format=duration', '-of',
                        'default=noprint_wrappers=1:nokey=1', str(p)],
                       capture_output=True, text=True, timeout=10)
    try:
        return float(r.stdout.strip())
    except Exception:
        return 0


def build_master(raw_paths):
    TMP.mkdir(exist_ok=True)
    for pat in ('gemini_masterB*.mp4', 'normB_*.mp4', 'masterB_concat.txt'):
        for f in TMP.glob(pat):
            try: f.unlink()
            except: pass

    TARGET_W, TARGET_H = 480, 854
    n = len(raw_paths)

    print(f'[1/3] Concat filter su {n} raw...')
    offsets = {}
    cum = 0.0
    for raw in raw_paths:
        raw_name = Path(raw).name
        offsets[raw_name] = round(cum, 2)
        cum += probe_dur(raw)

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
            f"setsar=1,format=yuv420p[v{i}]"
        )
    stream_spec = ''.join(f'[v{i}]' for i in range(n))
    fc_parts.append(f"{stream_spec}concat=n={n}:v=1:a=0[vout]")
    fc = ';'.join(fc_parts)

    master = TMP / 'gemini_masterB.mp4'
    cmd = ['ffmpeg', '-y', '-v', 'error'] + inputs + [
        '-filter_complex', fc, '-map', '[vout]', '-an',
        '-c:v', 'libx264', '-preset', 'veryfast',
        '-profile:v', 'baseline', '-level', '3.1',
        '-r', '30', '-vsync', 'cfr',
        '-crf', '28', '-maxrate', '500k', '-bufsize', '1000k',
        '-pix_fmt', 'yuv420p', '-movflags', '+faststart',
        str(master),
    ]
    t0 = time.time()
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        print(f'  ✗ {r.stderr[-300:]}')
        return None
    dt = time.time() - t0
    size_mb = master.stat().st_size / 1024 / 1024
    print(f'[2/3] Master: {master.name} ({size_mb:.1f} MB, {cum:.1f}s, {dt:.0f}s)')
    print(f'[3/3] offset map:')
    for k, v in offsets.items():
        print(f'    {k}: {v:.2f}s')
    return str(master), offsets, cum


def load_raw_analysis(raw_name):
    """Carica analysis del raw (se esiste)."""
    stem = Path(raw_name).stem
    p = BASE / 'media' / 'analysis' / f'{stem}.json'
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def build_prompt(brain, settings, offsets, raw_names, master_dur, scene_counts=None):
    vm = brain.get('visual_memory') or {}
    nm = brain.get('narrative_memory') or {}
    lm = brain.get('lexical_memory') or {}

    notes = settings.get('user_notes', [])
    if isinstance(notes, str):
        notes = [n.strip() for n in notes.split('\n') if n.strip()]
    notes_block = '\n'.join(f'- {n}' for n in notes) if notes else '(nessuna)'

    # Parole firma del creator (dal brain)
    signature_words = lm.get('signature_words', [])
    if isinstance(signature_words, list):
        sw_str = ', '.join(signature_words[:15])
    else:
        sw_str = str(signature_words)[:200]

    # Pattern sintattici del creator
    patterns = lm.get('sentence_patterns', [])
    if isinstance(patterns, list):
        pat_str = '\n'.join(f'- {p}' for p in patterns[:8])
    else:
        pat_str = str(patterns)

    # Opening/closing tipici
    openings = lm.get('opening_phrases', [])
    closings = lm.get('closing_phrases', [])
    open_str = '\n'.join(f'- {p}' for p in openings[:4]) if isinstance(openings, list) else ''
    close_str = '\n'.join(f'- {p}' for p in closings[:4]) if isinstance(closings, list) else ''

    # Banned words
    banned = lm.get('banned_words', [])
    if isinstance(banned, list):
        banned_str = ', '.join(banned[:15])
    else:
        banned_str = str(banned)

    scene_counts = scene_counts or {}
    offset_lines = []
    for k, v in offsets.items():
        n = scene_counts.get(k)
        if n is not None:
            offset_lines.append(f'- {k}: inizia a {v:.2f}s (ha {n} scene disponibili)')
        else:
            offset_lines.append(f'- {k}: inizia a {v:.2f}s')
    offsets_lines = '\n'.join(offset_lines)

    return f"""Sei il MONTATORE/EDITOR del creator TikTok @jay.emme.

# IDENTITA' DEL CREATOR
Nei video appare SEMPRE lo stesso creator, @jay.emme. Chiamalo "il creator"
o "jay.emme", MAI "una persona" / "un uomo" / "il soggetto".

# IL SUO BRAIN COMPLETO (APPLICA OBBLIGATORIAMENTE)

## VISUAL SIGNATURE
{vm.get('visual_signature','')}

## MOSSE VISIVE FIRMA
{chr(10).join('- ' + m for m in vm.get('signature_moves', [])[:6])}

## NARRATIVE
{nm.get('narrative_signature','')}

## 🚨 LESSICO OBBLIGATORIO — jay.emme USA QUESTE PAROLE:
{sw_str}

## PATTERN SINTATTICI SUOI (usali nel VO):
{pat_str}

## 🔥 BIGRAMMI E TRIGRAMMI FIRMA (usane almeno 3 nel VO):
- "un sacco" / "un sacco di X"          (26x nel corpus)
- "devo dire che" / "devo dire"          (37x)
- "tra l'altro" / "che tra l'altro"      (56x)
- "ed infine"                             (31x)
- "per concludere"                        (23x)
- "mi ha stupito"                         (10x)
- "al punto forte"                        (11x)
- "in questo caso"                        (10x)

## ✅ LE SUE PAROLE FIRMA (usale SENZA limite):
ovviamente (183x), veramente (113x), comunque (89x), appunto (75x),
super (48x), praticamente (46x), infatti (41x), insomma (32x), guardate (24x)

Queste parole NON sono "ripetizioni da evitare": sono il SUO stile.
Usale anche 5-8 volte in 28 shot. È così che parla lui.

## APERTURE CHE USA:
{open_str}

## CHIUSURE CHE USA (usa UNA di queste come ultimo shot):
{close_str}

## ALTRE APERTURE REALI dal corpus:
- "Ciao ragazzi sono Jay e..."
- "Oggi ragazzi vi porto..."
- "Prova la cucina ... per la prima volta"

## ALTRE CHIUSURE REALI:
- "Insomma, ... decisamente da provare"
- "In generale, ... approvata"
- "Ed è proprio ciò che..."
- "Quindi se non siete soddisfatti... (call to action)

# 🚨 PAROLE VIETATE — jay.emme NON LE USA MAI:
{banned_str}
oltre a: "che figata", "pazzesco", "meraviglia", "mamma mia", "wow",
"capolavoro", "goduria", "esplosione di gusto", "incredibile", "fantastico",
"davvero" (il creator NON la usa mai).

# REGOLE ASSOLUTE
- Evita smorfie, "espressione concentrata", "occhi chiusi", "testa china"
- NON descrivere l'azione della mano ("puccio", "mordo")
- Commenta sempre CIBO, SALSA, LOCALE (mai azioni fisiche)
- Apri con shot del locale
- Chiudi con CTA (creator sorridente o venue)
- 🚨 VENUE: almeno 6 shot su 28 DEVONO essere venue_* (locale visibile)

# IL VIDEO MASTER (durata {master_dur:.1f}s)
E' un video unico che concatena {len(raw_names)} clip girate dal creator.
NON mostrare timestamp (non ce ne sono). Riconosci gli shot dal contenuto visivo.

# MAPPA RAW NEL MASTER
{offsets_lines}

# COMPITO CRITICO
Costruisci la TIMELINE per un video TikTok di ~65 secondi.
Scegli ESATTAMENTE **28 shot**.

⚠️ VINCOLI DI DISTRIBUZIONE (IMPORTANTISSIMI):
- Per ogni raw, il numero di shot che assegni NON puo' superare il numero di
  scene disponibili indicate nella mappa. Se IMG_6813 ha 2 scene, MAX 2 shot da IMG_6813.
- Distribuisci i 28 shot in proporzione alle scene:
  - raw con molte scene (8+) → 4-6 shot
  - raw con poche scene (1-3) → 1-2 shot max
- Cerca di coprire TUTTI i raw nella timeline se hanno almeno 1 scena
- Non concentrare troppi shot sullo stesso raw

⚠️ VINCOLI OBBLIGATORI SUL VOICEOVER:
1. Ogni `comment` DEVE contenere ALMENO UNA parola dalla lista LESSICO
   OBBLIGATORIO. Se non riesci, riformula.
2. USA ALMENO 5 volte la parola "ovviamente" sparsa nel video
3. USA ALMENO 3 volte "tra l'altro" o "devo dire" o "super"
4. NON usare MAI le parole della lista VIETATE
5. Tono: amico che consiglia, non venditore. Frasi corte (max 12 parole)
6. Commenta SEMPRE cibo/sapore/locale, mai azioni fisiche

🚨🚨 NO RIPETIZIONI (CRITICO):
- NESSUNA parola puo' apparire piu' di 2 volte in TUTTO il voiceover
- Questo vale per "veramente", "davvero", "proprio", "abbastanza", "super", "davvero"
- SE una parola ti viene naturale ripeterla, SOSTITUISCI con alternativa:
  * "veramente" → "davvero", "proprio", "super" (mai 2+ volte la stessa)
  * "abbastanza" → "piuttosto", "decisamente"
  * "buono" → "ottimo", "top", "una bomba"
- Ogni commento DEVE essere diverso dal precedente: niente pattern "veramente X" ripetuto
- Varia la STRUTTURA: un commento "Le ali sono X", un altro "Che Y le ali", un altro ancora "Queste Z sono X"

Per OGNI shot:
- `source_raw`: uno dei nomi dalla mappa
- `part_in_raw`: "start" (0-33%) | "middle" (33-66%) | "end" (66-100%)
- `role`: food_closeup | food_detail | food_plated | drink_detail | person_eating | person_talking | hands_gesture | venue_interior_wide | venue_interior_detail | other
- `description`: cosa si vede (max 10 parole)
- `comment`: VOICEOVER del creator (max 12 parole, ITALIANO, applica lessico)
- `reason`: breve motivazione (max 8 parole)

⚠️ IMPORTANTE: se scegli 2+ shot dalla stessa part_in_raw dello stesso raw,
varia la `part_in_raw` (es. shot1 middle, shot2 end) per non creare duplicati.

Restituisci JSON:
{{
  "timeline": [
    {{"source_raw": "IMG_6823.MOV", "part_in_raw": "middle", "role": "person_eating",
      "description": "il creator addenta un'ala", "comment": "ovviamente il sapore è pazzesco",
      "reason": "morso con volto sorridente"}},
    ...
  ],
  "voiceover_script": "testo completo del voiceover",
  "opening_note": "...",
  "closing_note": "..."
}}

REGOLE FINALI:
- ESATTAMENTE 28 shot
- source_raw DEVE essere nella mappa
- Devi alternare i raw (max 4 shot consecutivi dallo stesso raw)
- Almeno 3 shot venue_* (apertura con venue)
- Commenti DEVONO rispettare il lessico obbligatorio

Rispondi SOLO con JSON valido.
"""



def reduce_repetitions(data, max_occ=2):
    """Post-process: sostituisce parole che appaiono > max_occ volte."""
    import re
    from collections import Counter

    # Sostituzioni SOLO per parole NON firma del creator.
    # "veramente", "ovviamente", "super", "comunque", "appunto", "praticamente"
    # sono parole firma di jay.emme — NON ridurle.
    VARIANTS = {
        'abbastanza': ['piuttosto', 'decisamente'],
        'sempre': ['comunque', 'in ogni caso'],
        'tanto': ['così', 'molto'],
        'molto': ['parecchio', 'un sacco'],
    }

    def collect(text):
        return re.findall(r"\\b[a-zàèéìòù']+\\b", text.lower())

    all_text = ' '.join(
        [data.get('voiceover_script', '')] +
        [s.get('comment', '') for s in data.get('timeline', [])]
    )
    counts = Counter(collect(all_text))
    to_reduce = {w: n for w, n in counts.items() if n > max_occ and w in VARIANTS}
    if not to_reduce:
        return data

    print(f'  [riduco ripetizioni] {list(to_reduce.keys())}')

    # Prima passata: VO globale
    usage = {w: 0 for w in to_reduce}
    def replace_word(m):
        word = m.group(0)
        lw = word.lower()
        if lw not in to_reduce:
            return word
        usage[lw] += 1
        if usage[lw] <= max_occ:
            return word
        idx = (usage[lw] - max_occ - 1) % len(VARIANTS[lw])
        repl = VARIANTS[lw][idx]
        if word[0].isupper():
            repl = repl[0].upper() + repl[1:]
        return repl

    if data.get('voiceover_script'):
        data['voiceover_script'] = re.sub(
            r"\\b[a-zàèéìòù']+\\b", replace_word,
            data['voiceover_script'], flags=re.IGNORECASE)

    # Seconda passata: ogni comment della timeline (reset usage)
    usage = {w: 0 for w in to_reduce}
    for s in data.get('timeline', []):
        if s.get('comment'):
            s['comment'] = re.sub(
                r"\\b[a-zàéèìòù']+\\b", replace_word,
                s['comment'], flags=re.IGNORECASE)

    # Rigenera VO dai commenti
    data['voiceover_script'] = ' '.join(
        s.get('comment', '').strip() for s in data.get('timeline', [])
    ).strip()
    return data


def match_shot_to_scene(shot, raw_analysis, raw_offset, raw_dur, used_scenes=None):
    """Trova la scena reale del raw che corrisponde allo shot.
    Distribuisce su scene diverse quando piu' shot chiedono la stessa part.

    used_scenes: set di (raw_name, scene_idx) gia' usati per evitare duplicati.
    """
    if not raw_analysis:
        return None
    scenes = raw_analysis.get('scenes', [])
    if not scenes:
        return None

    if used_scenes is None:
        used_scenes = set()

    part = shot.get('part_in_raw', 'middle')
    target_pct = {'start': 0.15, 'middle': 0.5, 'end': 0.85}.get(part, 0.5)

    # Candidati: scene il cui centro cade nella zona della part
    if part == 'start':
        lo, hi = 0.0, 0.40
    elif part == 'end':
        lo, hi = 0.60, 1.0
    else:
        lo, hi = 0.30, 0.70

    candidates = []
    for i, s in enumerate(scenes):
        mid = (s['start'] + s['end']) / 2.0
        pct = mid / raw_dur if raw_dur > 0 else 0.5
        if lo <= pct <= hi:
            candidates.append((i, s, pct))

    # Se nessuna nella zona, prendi la piu' vicina in assoluto
    if not candidates:
        for i, s in enumerate(scenes):
            mid = (s['start'] + s['end']) / 2.0
            pct = mid / raw_dur if raw_dur > 0 else 0.5
            candidates.append((i, s, pct))

    # Ordina per distanza dal target (ma scarta quelle gia' usate)
    candidates.sort(key=lambda x: abs(x[2] - target_pct))

    # Tentativo 1: scena nella part richiesta, non usata
    best = None
    for idx, s, pct in candidates:
        key = (shot.get('source_raw', ''), idx)
        if key not in used_scenes:
            best = (idx, s)
            used_scenes.add(key)
            break

    # FALLBACK A: se esaurite nella part, prova TUTTE le scene del raw non usate
    # (qualsiasi part: start/middle/end)
    if best is None:
        for i, s in enumerate(scenes):
            key = (shot.get('source_raw', ''), i)
            if key not in used_scenes:
                best = (i, s)
                used_scenes.add(key)
                break

    # FALLBACK ESTREMO: riusa la scena piu' vicina al target (accetta duplicato)
    if best is None and candidates:
        best = (candidates[0][0], candidates[0][1])

    if not best:
        return None

    idx, scene = best
    master_start = round(raw_offset + scene['start'], 2)
    master_end = round(raw_offset + scene['end'], 2)
    return master_start, master_end


def main():
    print('=== TEST APPROCCIO B ===\n')

    raw_paths = [str(BASE / 'media' / 'raw' / r) for r in RAWS_10]
    missing = [r for r in raw_paths if not Path(r).exists()]
    if missing:
        print(f'ERRORE: mancano {missing}')
        return

    # 1. Master
    result = build_master(raw_paths)
    if not result:
        return
    master_path, offsets, master_dur = result

    # 2. Brain + settings
    print()
    print('[+] Carico brain...')
    brain = json.load(open(BASE / 'brain' / 'active_brain.json'))
    settings = json.load(open(BASE / 'brain' / 'settings.json'))
    notes = settings.get('user_notes', [])
    if isinstance(notes, str): notes = [n.strip() for n in notes.split('\n') if n.strip()]
    print(f'  schema: {brain.get("schema_version")}, note: {len(notes)}')

    # 3. Base64
    print('[+] Encode base64...')
    with open(master_path, 'rb') as f:
        data_uri = 'data:video/mp4;base64,' + base64.b64encode(f.read()).decode()
    print(f'  {len(data_uri) / 1024 / 1024:.1f} MB')

    # 4. Prompt (con conteggio scene per raw)
    # Carico analysis PRIMA del prompt per contare le scene
    scene_counts = {}
    for rn in RAWS_10:
        an = load_raw_analysis(rn)
        if an and an.get('scenes'):
            scene_counts[rn] = len(an['scenes'])
        else:
            scene_counts[rn] = 0
    print(f'[+] Scene disponibili per raw:')
    for rn in RAWS_10:
        print(f'    {rn}: {scene_counts[rn]} scene')

    prompt = build_prompt(brain, settings, offsets, RAWS_10, master_dur, scene_counts)
    print(f'[+] Prompt: {len(prompt)} char')

    # 5. Gemini
    print('[+] Chiamata Gemini Flash...')
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
        (TMP / 'approccioB_raw.txt').write_text(raw_resp)
        print('  salvato in /tmp/approccioB_raw.txt')
        print(raw_resp[:1500])
        return

    # Post-process: riduci ripetizioni di parole
    data = reduce_repetitions(data)
    (TMP / 'approccioB_gemini.json').write_text(json.dumps(data, indent=2, ensure_ascii=False))

    # 6. Python mapping
    print()
    print('=' * 85)
    print('RISULTATO — Gemini (shot list) + Python (timestamp mappati)')
    print('=' * 85)
    timeline = data.get('timeline', [])
    print(f'  n shot (Gemini): {len(timeline)}')
    print(f'  durata dichiarata: {data.get("total_duration", "?")}s')
    print(f'  apertura: {data.get("opening_note", "")[:70]}')
    print(f'  chiusura: {data.get("closing_note", "")[:70]}')
    print()

    # Cache analyses raw
    raw_cache = {}
    for rn in RAWS_10:
        raw_cache[rn] = load_raw_analysis(rn)
        if raw_cache[rn]:
            n_sc = len(raw_cache[rn].get('scenes', []))
            print(f'  analysis {rn}: {n_sc} scene')
        else:
            print(f'  analysis {rn}: MANCANTE (uso fallback)')

    print()
    print(f'  {"#":>3} {"source":<16} {"part":<7} {"master_start":>12} {"master_end":>11} {"dur":>5} {"role":<22} | comment')
    print('  ' + '-' * 110)

    final_timeline = []
    missing_analysis = 0
    used_scenes = set()
    for i, s in enumerate(timeline):
        src = s.get('source_raw', '?')
        part = s.get('part_in_raw', '?')
        raw_offset = offsets.get(src)
        raw_info = probe_dur(BASE / 'media' / 'raw' / src) if raw_offset is not None else 0
        m_start, m_end = None, None
        if raw_offset is not None and raw_cache.get(src):
            res = match_shot_to_scene(s, raw_cache[src], raw_offset, raw_info, used_scenes)
            if res:
                m_start, m_end = res
        elif raw_offset is None:
            print(f'  {i+1:>3} {src:<16} {part:<7} ERRORE: raw non nella mappa')
            continue
        else:
            missing_analysis += 1
            # Fallback: usa part_in_raw per stimare
            pct = {'start': 0.15, 'middle': 0.5, 'end': 0.85}.get(part, 0.5)
            m_start = round(raw_offset + raw_info * pct - 1, 2)
            m_end = round(m_start + 2.0, 2)

        dur = (m_end or 0) - (m_start or 0)
        print(f'  {i+1:>3} {src:<16} {part:<7} {m_start or 0:>12.2f} {m_end or 0:>11.2f} {dur:>5.2f} {s.get("role","?"):<22} | {s.get("comment","")[:60]}')
        final_timeline.append({
            **s,
            'start_master': m_start,
            'end_master': m_end,
            'dur': round(dur, 2),
        })

    # Analisi
    from collections import Counter
    print()
    print('  --- ANALISI ---')
    roles = Counter(s.get('role','?') for s in timeline)
    print(f'  ruoli Gemini: {dict(roles)}')
    srcs = Counter(s.get('source_raw','?') for s in timeline)
    print(f'  distribuzione raw: {dict(srcs)}')
    comments = [s.get('comment','') for s in timeline if s.get('comment')]
    print(f'  commenti unici: {len(set(comments))}/{len(comments)}')
    persons = [s for s in timeline if 'creator' in (s.get('description','') + s.get('comment','')).lower()]
    print(f'  shot con "creator" menzionato: {len(persons)}/{len(timeline)}')
    if missing_analysis:
        print(f'  ⚠️  {missing_analysis} shot senza analysis (usato fallback)')

    total_dur = sum(s['dur'] for s in final_timeline)
    print(f'  durata finale calcolata: {total_dur:.1f}s')

    print()
    print('VOICEOVER:')
    print((data.get('voiceover_script') or '')[:700])

    out = TMP / 'approccioB_result.json'
    out.write_text(json.dumps({
        'timeline': final_timeline,
        'voiceover_script': data.get('voiceover_script'),
        'opening_note': data.get('opening_note'),
        'closing_note': data.get('closing_note'),
    }, indent=2, ensure_ascii=False))
    print(f'\nsaved: {out}')


if __name__ == '__main__':
    main()

"""Approccio B per ReelAgent: Gemini editor + Python mapper.

Input: una lista di raw (path) + brain attivo
Output: EDL con timeline mappata + voiceover

Gemini vede il master concatenato (senza timestamp) e produce shot list con
source_raw + part_in_raw. Python mappa i timestamp reali dalle analysis.
"""
import os, sys, json, base64, subprocess, time, tempfile
from pathlib import Path
from dotenv import load_dotenv

BASE = Path('/opt/reel-agent')
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / 'app'))
load_dotenv(BASE / '.env')

from openai import OpenAI
from app.jobs import manager

CLIENT = OpenAI(base_url='https://openrouter.ai/api/v1',
                api_key=os.environ['OPENROUTER_API_KEY'])
MODEL = 'google/gemini-2.5-flash'
TMP = Path('/tmp')

VARIANTS = {
    'abbastanza': ['piuttosto', 'decisamente'],
    'sempre': ['comunque', 'in ogni caso'],
    'tanto': ['così', 'molto'],
    'molto': ['parecchio', 'un sacco'],
}


def _probe_dur(p):
    r = subprocess.run(['ffprobe', '-v', 'error', '-show_entries',
                        'format=duration', '-of',
                        'default=noprint_wrappers=1:nokey=1', str(p)],
                       capture_output=True, text=True, timeout=10)
    try:
        return float(r.stdout.strip())
    except Exception:
        return 0


def _load_raw_analysis(raw_name):
    stem = Path(raw_name).stem
    p = BASE / 'media' / 'analysis' / f'{stem}.json'
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def _load_brain():
    active = BASE / 'brain' / 'active_brain.json'
    if not active.exists():
        return None, None
    try:
        return json.loads(active.read_text()), 'active_brain.json'
    except Exception:
        return None, None


def _load_settings():
    p = BASE / 'brain' / 'settings.json'
    if not p.exists():
        return {'user_notes': [], 'subtitle_style': {}}
    try:
        return json.loads(p.read_text())
    except Exception:
        return {'user_notes': [], 'subtitle_style': {}}


def _build_master(raw_paths, job_dir, job_id):
    """Concat filter + master 480x854 senza timestamp."""
    TMP_job = job_dir / 'master_work'
    TMP_job.mkdir(exist_ok=True)

    TARGET_W, TARGET_H = 480, 854
    n = len(raw_paths)

    manager.log(job_id, f'  [master] concat filter su {n} raw...')
    offsets = {}
    cum = 0.0
    for raw in raw_paths:
        raw_name = Path(raw).name
        offsets[raw_name] = round(cum, 2)
        cum += _probe_dur(raw)

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

    master = TMP_job / 'master.mp4'
    cmd = ['ffmpeg', '-y', '-v', 'error'] + inputs + [
        '-filter_complex', fc, '-map', '[vout]', '-an',
        '-c:v', 'libx264', '-preset', 'veryfast',
        '-profile:v', 'baseline', '-level', '3.1',
        '-r', '30', '-vsync', 'cfr',
        '-crf', '28', '-maxrate', '500k', '-bufsize', '1000k',
        '-pix_fmt', 'yuv420p', '-movflags', '+faststart',
        str(master),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    if r.returncode != 0:
        return None, None, None
    return str(master), offsets, cum


def _build_prompt(brain, settings, offsets, raw_names, master_dur, scene_counts):
    vm = brain.get('visual_memory') or {}
    nm = brain.get('narrative_memory') or {}
    lm = brain.get('lexical_memory') or {}

    notes = settings.get('user_notes', [])
    if isinstance(notes, str):
        notes = [n.strip() for n in notes.split('\n') if n.strip()]
    notes_block = '\n'.join(f'- {n}' for n in notes) if notes else '(nessuna)'

    sw_str = ', '.join(lm.get('signature_words', [])[:15]) if isinstance(lm.get('signature_words'), list) else str(lm.get('signature_words', ''))
    patterns = lm.get('sentence_patterns', [])
    pat_str = '\n'.join(f'- {p}' for p in patterns[:8]) if isinstance(patterns, list) else ''
    openings = lm.get('opening_phrases', [])
    closings = lm.get('closing_phrases', [])
    open_str = '\n'.join(f'- {p}' for p in openings[:4]) if isinstance(openings, list) else ''
    close_str = '\n'.join(f'- {p}' for p in closings[:4]) if isinstance(closings, list) else ''
    banned = lm.get('banned_words', [])
    banned_str = ', '.join(banned[:15]) if isinstance(banned, list) else str(banned)

    offset_lines = []
    for k, v in offsets.items():
        n = scene_counts.get(k, 0)
        offset_lines.append(f'- {k}: inizia a {v:.2f}s (ha {n} scene disponibili)')
    offsets_lines = '\n'.join(offset_lines)

    return f"""Sei il MONTATORE/EDITOR del creator TikTok @jay.emme.

# IDENTITA' DEL CREATOR
Nei video appare SEMPRE lo stesso creator, @jay.emme. Chiamalo "il creator"
o "jay.emme", MAI "una persona" / "un uomo" / "il soggetto".

# IL SUO BRAIN COMPLETO

## VISUAL SIGNATURE
{vm.get('visual_signature','')}

## MOSSE VISIVE FIRMA
{chr(10).join('- ' + m for m in vm.get('signature_moves', [])[:6])}

## NARRATIVE
{nm.get('narrative_signature','')}

## 🚨 LESSICO OBBLIGATORIO (usa queste parole):
{sw_str}

## PATTERN SINTATTICI SUOI:
{pat_str}

## 🔥 BIGRAMMI E TRIGRAMMI FIRMA (usane almeno 4):
- "devo dire che" (37x), "un sacco" (26x), "tra l'altro" (56x)
- "ed infine", "per concludere", "mi ha stupito", "al punto forte"
- "oggi ragazzi vi", "ragazzi vi porto" (aperture tipiche)

## ✅ PAROLE FIRMA — USALE CON DENSITÀ CONTROLLATA:
Il creator usa queste parole, ma NON tutte insieme nello stesso video.
Nei SUOI video reali, la frequenza è circa:
- "ovviamente": 183x su 107 video → **MAX 2 volte** in un video da 28 shot
- "veramente": 113x su 107 video → **MAX 2 volte**
- "comunque": 89x → **MAX 1-2 volte**
- "appunto": 75x → **MAX 1-2 volte**
- "super": 48x → **MAX 2 volte**
- "praticamente": 46x → **MAX 1 volta**
- "infatti": 41x → **MAX 1 volta**
- "insomma": 32x → **MAX 1 volta** (solo in chiusura)
- "guardate": 24x → **MAX 2 volte**

🚨 REGOLA CRITICA: distribuisci queste parole su TUTTO il video.
NON metterle tutte nei primi shot. Se un commento ha già "ovviamente",
i successivi 3-4 commenti NON devono averla. Ripetile solo dopo 5-6 shot.

Esempio di distribuzione CORRETTA su 28 shot:
- shot 1: "Oggi ragazzi vi porto da..." (no firma)
- shot 2: "Il locale è super accogliente" (super #1)
- shot 3: "Un sacco di opzioni" (no firma)
- shot 4: "Le patatine sono veramente croccanti" (veramente #1)
- shot 5: "Ovviamente le provo subito" (ovviamente #1)
- shot 6-10: NIENTE parole firma, solo descrizioni/commenti naturali
- shot 11: "Tra l'altro anche le ali..." (tra l'altro #1)
- shot 12-16: niente firma
- shot 17: "Comunque un'esperienza top" (comunque #1)
...ecc

## VARIANTI PER DENSITÀ CONTROLLATA:
Se usi una parola firma > 2 volte, sostituiscila con una VARIANTE:
- "ovviamente" → "appunto", "infatti", "praticamente"
- "veramente" → "super", "proprio"
- "comunque" → "insomma", "in ogni caso"
- "appunto" → "esatto", "infatti"
- "super" → "molto", "proprio"

## APERTURE CHE USA:
{open_str}

## CHIUSURE CHE USA:
{close_str}

# PAROLE VIETATE:
{banned_str}, "davvero", "che figata", "pazzesco", "meraviglia",
"mamma mia", "wow", "capolavoro", "goduria", "esplosione di gusto"

# REGOLE
- Evita smorfie, "espressione concentrata", "occhi chiusi", "testa china"
- NON descrivere l'azione della mano ("puccio", "mordo")
- Commenta CIBO, SALSA, LOCALE
- Apri con shot del locale, chiudi con CTA
- 🚨 ALMENO 6 shot su 28 DEVONO essere venue_* (locale visibile)

# VIDEO MASTER ({master_dur:.1f}s)
Video unico che concatena {len(raw_names)} clip. Riconosci gli shot dal contenuto.

# MAPPA RAW
{offsets_lines}

# COMPITO
Costruisci la TIMELINE per un video TikTok di ~65 secondi.
Scegli ESATTAMENTE **28 shot**.

VINCOLI DISTRIBUZIONE:
- MAX shot da un raw = numero scene disponibili
- Distribuisci in proporzione: raw con 8+ scene → 4-6 shot, raw con 1-3 → 1-2
- Copri tutti i raw con almeno 1 scena

VINCOLI VOICEOVER:
- Ogni comment usa almeno una parola dal LESSICO
- USA 5+ volte "ovviamente", 3+ volte "tra l'altro"/"devo dire"/"super"
- NON usare parole vietate

Per OGNI shot:
- `source_raw`: uno dei nomi dalla mappa
- `part_in_raw`: "start" (0-33%) | "middle" (33-66%) | "end" (66-100%)
- `role`: food_closeup | food_detail | food_plated | drink_detail | person_eating | person_talking | hands_gesture | venue_interior_wide | venue_interior_detail | other
- `description`: cosa si vede (max 10 parole)
- `comment`: VOICEOVER (max 12 parole, ITALIANO, applica lessico)
- `reason`: motivazione (max 8 parole)

Restituisci JSON:
{{
  "timeline": [...],
  "voiceover_script": "...",
  "opening_note": "...",
  "closing_note": "..."
}}

Rispondi SOLO con JSON valido.
"""



def _cap_signature_density(data, max_occ=2):
    """Guardrail: se una parola firma appare > max_occ, sostituisce occorrenze in eccesso
    con varianti che il creator usa."""
    import re
    from collections import Counter

    ALTERNATIVES = {
        'ovviamente': ['appunto', 'infatti', 'praticamente'],
        'veramente': ['super', 'proprio', 'incredibilmente'],
        'comunque': ['insomma', 'in ogni caso'],
        'appunto': ['esatto', 'infatti'],
        'super': ['molto', 'davvero', 'proprio'],
        'praticamente': ['in pratica', 'fondamentalmente'],
        'infatti': ['appunto', 'esatto'],
        'guardate': ['guardate qua', 'vedete'],
        'insomma': ['alla fine', 'in conclusione'],
    }

    def collect(text):
        return re.findall(r"\b[a-zàèéìòù]+\b", text.lower())

    # Conta usage globale
    usage = {}
    for s in data.get('timeline', []):
        if not s.get('comment'):
            continue
        for w in collect(s['comment']):
            if w in ALTERNATIVES:
                usage[w] = usage.get(w, 0) + 1

    over = {w: n for w, n in usage.items() if n > max_occ}
    if not over:
        return data

    print(f'  [cap firma] riduco: {over}')

    counter = {w: 0 for w in over}
    def replace(m):
        w = m.group(0)
        lw = w.lower()
        if lw not in over:
            return w
        counter[lw] += 1
        if counter[lw] <= max_occ:
            return w
        alts = ALTERNATIVES[lw]
        idx = (counter[lw] - max_occ - 1) % len(alts)
        repl = alts[idx]
        if w[0].isupper():
            repl = repl[0].upper() + repl[1:]
        return repl

    for s in data.get('timeline', []):
        if s.get('comment'):
            s['comment'] = re.sub(r"\b[a-zàèéìòù]+\b", replace,
                                  s['comment'], flags=re.IGNORECASE)

    data['voiceover_script'] = ' '.join(
        s.get('comment', '').strip() for s in data.get('timeline', [])
    ).strip()
    return data


def _reduce_repetitions(data, max_occ=3):
    import re
    from collections import Counter

    def collect(text):
        return re.findall(r"\b[a-zàèéìòù']+\b", text.lower())

    all_text = ' '.join([data.get('voiceover_script', '')] +
                        [s.get('comment', '') for s in data.get('timeline', [])])
    counts = Counter(collect(all_text))
    to_reduce = {w: n for w, n in counts.items() if n > max_occ and w in VARIANTS}
    if not to_reduce:
        return data

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

    for s in data.get('timeline', []):
        if s.get('comment'):
            s['comment'] = re.sub(r"\b[a-zàèéìòù']+\b", replace_word,
                                  s['comment'], flags=re.IGNORECASE)
    data['voiceover_script'] = ' '.join(
        s.get('comment', '').strip() for s in data.get('timeline', [])
    ).strip()
    return data


def _match_shot_to_scene(shot, raw_analysis, raw_offset, raw_dur, used_scenes):
    if not raw_analysis:
        return None
    scenes = raw_analysis.get('scenes', [])
    if not scenes:
        return None
    part = shot.get('part_in_raw', 'middle')
    target_pct = {'start': 0.15, 'middle': 0.5, 'end': 0.85}.get(part, 0.5)
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
    if not candidates:
        for i, s in enumerate(scenes):
            mid = (s['start'] + s['end']) / 2.0
            pct = mid / raw_dur if raw_dur > 0 else 0.5
            candidates.append((i, s, pct))

    candidates.sort(key=lambda x: abs(x[2] - target_pct))
    for idx, s, pct in candidates:
        key = (shot.get('source_raw', ''), idx)
        if key not in used_scenes:
            used_scenes.add(key)
            return round(raw_offset + s['start'], 2), round(raw_offset + s['end'], 2)
    # fallback: prima scena non usata
    for i, s in enumerate(scenes):
        key = (shot.get('source_raw', ''), i)
        if key not in used_scenes:
            used_scenes.add(key)
            return round(raw_offset + s['start'], 2), round(raw_offset + s['end'], 2)
    if candidates:
        return round(raw_offset + candidates[0][1]['start'], 2), round(raw_offset + candidates[0][1]['end'], 2)
    return None


def run_brain_edit_from_master(job_id, raw_paths, profile_id=16, venue_name="Wing Stop Milano"):
    """Pipeline approccio B."""
    t0 = time.time()
    job_dir = BASE / 'brain' / 'edit_jobs' / str(job_id)
    job_dir.mkdir(parents=True, exist_ok=True)

    manager.log(job_id, f'[1/5] approccio B: {len(raw_paths)} raw')
    manager.update_progress(job_id, 5, 'carico brain')

    brain, brain_file = _load_brain()
    if not brain:
        manager.fail(job_id, 'brain non trovato')
        return
    settings = _load_settings()

    manager.update_progress(job_id, 15, 'concat master')
    master_path, offsets, master_dur = _build_master(raw_paths, job_dir, job_id)
    if not master_path:
        manager.fail(job_id, 'build master fallito')
        return
    manager.log(job_id, f'  master: {master_dur:.1f}s')

    # Scene counts
    scene_counts = {}
    for rp in raw_paths:
        rn = Path(rp).name
        an = _load_raw_analysis(rn)
        scene_counts[rn] = len(an.get('scenes', [])) if an else 0

    manager.update_progress(job_id, 35, 'chiamo Gemini')
    with open(master_path, 'rb') as f:
        data_uri = 'data:video/mp4;base64,' + base64.b64encode(f.read()).decode()
    prompt = _build_prompt(brain, settings, offsets, [Path(p).name for p in raw_paths],
                           master_dur, scene_counts)
    manager.log(job_id, f'  prompt: {len(prompt)} char, video: {len(data_uri)/1024/1024:.1f} MB')

    try:
        r = CLIENT.chat.completions.create(
            model=MODEL,
            messages=[{'role': 'user', 'content': [
                {'type': 'text', 'text': prompt},
                {'type': 'image_url', 'image_url': {'url': data_uri}},
            ]}],
            max_tokens=8000, temperature=0.3,
        )
    except Exception as e:
        manager.fail(job_id, f'Gemini: {type(e).__name__}: {str(e)[:200]}')
        return
    raw_resp = r.choices[0].message.content
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
        manager.fail(job_id, f'parse JSON: {e}')
        return

    data = _reduce_repetitions(data)
    data = _cap_signature_density(data, max_occ=2)

    manager.update_progress(job_id, 70, 'mappo timestamp')
    used_scenes = set()
    timeline_out = []
    for i, s in enumerate(data.get('timeline', [])):
        src = s.get('source_raw', '?')
        raw_offset = offsets.get(src)
        if raw_offset is None:
            continue
        raw_path = next((p for p in raw_paths if Path(p).name == src), None)
        if not raw_path:
            continue
        raw_dur = _probe_dur(raw_path)
        an = _load_raw_analysis(src)
        res = _match_shot_to_scene(s, an, raw_offset, raw_dur, used_scenes)
        if not res:
            continue
        m_start, m_end = res
        in_sec = max(0.0, m_start - raw_offset)
        out_sec = max(in_sec + 0.5, m_end - raw_offset)
        out_sec = min(out_sec, raw_dur)
        dur = round(out_sec - in_sec, 2)

        # keyframes
        stem = Path(src).stem
        kfs = []
        kf_dir = BASE / 'media' / 'frames' / 'curate_job_187'
        if kf_dir.exists():
            for sub in kf_dir.iterdir():
                if sub.name.startswith(stem + '_s'):
                    f0 = sub / 'f0.jpg'
                    if f0.exists():
                        kfs.append(str(f0))
                        break

        timeline_out.append({
            'scene_id': i,
            'clip_name': src,
            'clip_path': str(raw_path),
            'role': s.get('role', 'other'),
            'subject': s.get('description', '')[:40],
            'description': s.get('description', ''),
            'keyframes': kfs,
            'start': round(in_sec, 3),
            'end': round(out_sec, 3),
            'in_sec': round(in_sec, 3),
            'out_sec': round(out_sec, 3),
            'duration': dur,
            'requested_duration': round(m_end - m_start, 2),
            'beat_type': s.get('role', ''),
            'beat_moment': s.get('reason', '')[:60],
            'subtitle_text': s.get('comment', '').strip(),
        })

    edl = {
        'title': 'ApproccioB — ' + (data.get('opening_note') or '')[:30],
        'voiceover_script': data.get('voiceover_script', ''),
        'timeline': timeline_out,
        'total_duration': round(sum(t['duration'] for t in timeline_out), 2),
        'n_clips': len(timeline_out),
        'profile_id': profile_id,
        'venue_name': venue_name,
        'engine': 'brain_edit_from_master',
    }

    edl_path = BASE / 'media' / 'edl' / 'job_999_edl.json'
    edl_path.write_text(json.dumps(edl, indent=2, ensure_ascii=False))
    manager.update_progress(job_id, 100, 'completato')
    manager.finish(job_id, {
        'edl_path': str(edl_path),
        'n_clips': edl['n_clips'],
        'duration': edl['total_duration'],
        'title': edl['title'],
        'engine': 'brain_edit_from_master',
        'elapsed': round(time.time() - t0, 1),
    })

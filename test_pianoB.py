#!/usr/bin/env python3
"""Piano B test: 10 frame per clip + Gemini descrive ogni clip."""
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
TMP.mkdir(exist_ok=True)


def extract_frames(video, start, end, n=10):
    """Estrai n frame equidistanti dalla clip [start, end]."""
    dur = end - start
    paths = []
    for i in range(n):
        # 0%, 11%, 22%, ..., 100% (evita esattamente 100% per non andare oltre)
        pct = i / (n - 1) * 0.95
        ts = start + dur * pct
        fp = TMP / f'clip_{int(start*1000)}_{i:02d}.jpg'
        cmd = ['ffmpeg', '-y', '-v', 'error', '-ss', f'{ts:.3f}',
               '-i', str(video), '-frames:v', '1',
               '-vf', 'scale=480:-2', '-q:v', '4', str(fp)]
        r = subprocess.run(cmd, capture_output=True, timeout=10)
        if r.returncode == 0 and fp.exists():
            paths.append(fp)
    return paths


def img_uri(p):
    with open(p, 'rb') as f:
        return 'data:image/jpeg;base64,' + base64.b64encode(f.read()).decode()


PROMPT = """Analizza queste {n} immagini estratte da UNA clip video di 2-3 secondi
(in ordine temporale: la prima è l'inizio, l'ultima è la fine).

Restituisci UN SOLO oggetto JSON con questi campi:

{{
  "action": "<bite | presentation | chewing | close_up | pouring | other>",
  "bite_frame_idx": <indice 0-based del frame che mostra il MORSO, o null se non c'è>,
  "focus_score": <0.0-1.0, nitidezza nel momento chiave>,
  "subject": "<2-5 parole: es 'ala fritta glassata', 'cliente sorridente'>",
  "role": "<food_closeup | food_detail | food_plated | drink_detail | person_eating | person_talking | hands_gesture | venue_interior_wide | venue_interior_detail | other>",
  "has_person": <true|false>,
  "has_logo": <true|false>,
  "venue_elements": ["elementi architettonici visibili: muro, monitor, insegna, tavolo, murale, scala..."],
  "description": "<1-2 frasi in italiano su cosa si vede>",
  "comment": "<1 frase di COMMENTO del creator (opinione, non descrizione) — es 'sembra croccante', 'che vassoio ragazzi'>"
}}

REGOLE:
- "bite" = c'è un morso visibile in uno dei frame
- "bite_frame_idx" = quale dei {n} frame mostra il morso (0 = primo, {last} = ultimo)
- "focus_score" = nitidezza del momento chiave (0=sfocato, 1=nitido)
- "comment" = opinione tipo creator TikTok (non "il locale ha X", ma "che roba ragazzi")
- Rispondi SOLO con JSON valido, niente markdown.
"""


def analyze_clip(video, start, end, idx):
    print(f'  clip {idx}: {start:.2f} → {end:.2f} ({end-start:.2f}s)')
    frames = extract_frames(video, start, end, n=10)
    if len(frames) < 3:
        print(f'    ERRORE: solo {len(frames)} frame estratti')
        return None

    content = [{'type': 'text', 'text': PROMPT.format(n=len(frames), last=len(frames)-1)}]
    for fp in frames:
        content.append({'type': 'image_url', 'image_url': {'url': img_uri(fp)}})

    try:
        r = CLIENT.chat.completions.create(
            model=MODEL,
            messages=[{'role': 'user', 'content': content}],
            max_tokens=800, temperature=0.3,
        )
        raw = r.choices[0].message.content.strip()
        if raw.startswith('```'):
            raw = raw.split('\n', 1)[1]
            if raw.endswith('```'):
                raw = raw[:-3]
        i, j = raw.find('{'), raw.rfind('}')
        if i >= 0 and j > i:
            raw = raw[i:j+1]
        data = json.loads(raw)
        data['start'] = round(start, 3)
        data['end'] = round(end, 3)
        data['dur'] = round(end - start, 3)
        data['clip_idx'] = idx
        return data
    except Exception as e:
        print(f'    ERRORE Gemini: {type(e).__name__}: {str(e)[:200]}')
        return None


def main():
    raw = BASE / 'media' / 'raw' / 'IMG_6823.MOV'
    print(f'=== {raw.name} ===')

    scenes = detect_scenes(str(raw))
    print(f'scene macro: {len(scenes)}')

    subclips = []
    for s, e in scenes:
        subclips.extend(_split_scene_with_rhythm(s, e, video_seed=1))
    print(f'subclip: {len(subclips)}')
    print()

    t0 = time.time()
    results = []
    for i, (s, e) in enumerate(subclips):
        r = analyze_clip(str(raw), s, e, i)
        if r:
            results.append(r)
        time.sleep(0.5)

    dt = time.time() - t0
    print(f'\n=== done in {dt:.0f}s, {len(results)}/{len(subclips)} clip analizzate ===\n')

    # Tabella riassuntiva
    print(f'{"idx":>3} {"start":>7} {"dur":>5} {"action":<13} {"focus":>5} {"bite":>4} | reason')
    print('-' * 100)
    for r in results:
        bite = r.get('bite_frame_idx')
        bite_s = str(bite) if bite is not None else '-'
        print(f'{r["clip_idx"]:>3} {r["start"]:>7.2f} {r["dur"]:>5.2f} {r.get("action","?"):<13} {r.get("focus_score",0):>5.2f} {bite_s:>4} | {r.get("comment","")[:60]}')

    # Salva
    out = TMP / 'pianoB_test.json'
    out.write_text(json.dumps(results, indent=2, ensure_ascii=False))
    print(f'\nsaved: {out}')


if __name__ == '__main__':
    main()

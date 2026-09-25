#!/usr/bin/env python3
"""Fase B: descrizione visiva frame via OpenRouter (Gemini)."""
import os, sys, json, time, base64
from pathlib import Path
from dotenv import load_dotenv

load_dotenv('/opt/reel-agent/.env')
from openai import OpenAI

BRAIN = Path('/opt/reel-agent/brain')
FRAMES = BRAIN / 'frames'
OUT = BRAIN / 'reference_analyses'
LOG = BRAIN / 'phase_b.log'
OUT.mkdir(parents=True, exist_ok=True)

BATCH = 5
RATE_LIMIT_SEC = 1.5
MAX_RETRIES = 4
MODELS = [
    'google/gemini-2.5-flash',
    'anthropic/claude-haiku-4.5',
    'anthropic/claude-sonnet-4.5',
]

client = OpenAI(
    base_url='https://openrouter.ai/api/v1',
    api_key=os.environ['OPENROUTER_API_KEY'],
)


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG, 'a') as f:
        f.write(line + '\n')


PROMPT_TEMPLATE = """Analizza questi {n} frame consecutivi di un video TikTok del creator @jay.emme.

Per OGNI frame (nell'ordine in cui te li passo), rispondi con un oggetto JSON con ESATTAMENTE questi campi:

{{
  "idx": <numero 0-based del frame nella lista>,
  "role_visivo": "<una di: food_closeup, food_detail, food_plated, drink_detail, drink_cheers, person_eating, person_talking, hands_gesture, venue_interior_wide, venue_interior_detail, venue_exterior, menu_board, claw_game, other>",
  "soggetto": "<2-5 parole, es: 'ala fritta glassata', 'cliente sorridente', 'totem menu'>",
  "composizione": "<wide|medium|close_up|detail>",
  "angolo": "<dall_alto|orizzontale|dal_basso|pov>",
  "presenza_umana": <true|false>,
  "presenza_logo": <true|false>,
  "luce": "<naturale|artificiale_caldo|artificiale_freddo|mista>",
  "cosa_si_nota": "<1 frase breve su cosa colpisce l'occhio>"
}}

Output: SOLO un array JSON valido con {n} oggetti. Niente markdown, niente ```json, niente testo prima o dopo.
"""


def parse_json_array(raw):
    t = (raw or '').strip()
    if t.startswith('```'):
        t = t.split('\n', 1)[1] if '\n' in t else t
        if t.endswith('```'):
            t = t[:-3]
    t = t.strip()
    i = t.find('[')
    j = t.rfind(']')
    if i >= 0 and j > i:
        t = t[i:j+1]
    return json.loads(t)


def img_to_data_uri(path):
    with open(path, 'rb') as f:
        b = f.read()
    return 'data:image/jpeg;base64,' + base64.b64encode(b).decode()


def analyze_batch(frame_paths):
    prompt = PROMPT_TEMPLATE.format(n=len(frame_paths))
    content = [{'type': 'text', 'text': prompt}]
    for p in frame_paths:
        content.append({'type': 'image_url', 'image_url': {'url': img_to_data_uri(p)}})

    base_wait = 3
    for attempt in range(MAX_RETRIES):
        model = MODELS[attempt % len(MODELS)]
        try:
            r = client.chat.completions.create(
                model=model,
                messages=[{'role': 'user', 'content': content}],
                max_tokens=2000,
                temperature=0.2,
            )
            raw = r.choices[0].message.content
            parsed = parse_json_array(raw)
            if len(parsed) != len(frame_paths):
                log(f"    parse: attesi {len(frame_paths)}, ricevuti {len(parsed)}")
            return parsed
        except Exception as e:
            wait = base_wait * (2 ** attempt)
            log(f"    retry {attempt+1}/{MAX_RETRIES} model={model} wait={wait}s: {type(e).__name__}: {str(e)[:150]}")
            time.sleep(wait)
    return None


def process_video(ref_dir):
    ref_id = ref_dir.name
    index_path = ref_dir / '_index.json'
    out_path = OUT / f"{ref_id}_vision.json"

    if out_path.exists():
        log(f"  {ref_id}: SKIP")
        return 'skip'
    if not index_path.exists():
        log(f"  {ref_id}: no _index.json")
        return 'error'

    index = json.loads(index_path.read_text())
    shots = index.get('shots', [])
    if not shots:
        log(f"  {ref_id}: 0 shot")
        return 'empty'

    frames = [sh['frame'] for sh in shots]
    descriptions = []
    for b_start in range(0, len(frames), BATCH):
        batch = frames[b_start:b_start + BATCH]
        result = analyze_batch(batch)
        if result is None:
            log(f"  {ref_id}: batch {b_start//BATCH} FALLITO")
            return 'error'
        for i, d in enumerate(result):
            gi = b_start + i
            if gi < len(shots):
                d['shot_idx'] = shots[gi]['idx']
                d['start'] = shots[gi]['start']
                d['end'] = shots[gi]['end']
                d['dur'] = shots[gi]['dur']
                descriptions.append(d)
        time.sleep(RATE_LIMIT_SEC)

    out = {
        'ref_id': ref_id,
        'video': index['video'],
        'n_shots': len(shots),
        'total_dur': index['total_dur'],
        'descriptions': descriptions,
    }
    out_path.write_text(json.dumps(out, indent=2, ensure_ascii=False))
    log(f"  {ref_id}: {len(descriptions)}/{len(shots)} descritti")
    return 'ok'


def main():
    only = sys.argv[1] if len(sys.argv) > 1 else None
    if only:
        ref_dir = FRAMES / only
        if not ref_dir.exists():
            log(f"ERRORE: {ref_dir} non esiste")
            return
        log(f"=== Fase B TEST su {only} ===")
        r = process_video(ref_dir)
        log(f"=== TEST DONE: {r} ===")
        return

    dirs = sorted([d for d in FRAMES.iterdir() if d.is_dir()])
    log(f"=== Fase B START: {len(dirs)} video ===")
    t0 = time.time()
    stats = {'ok': 0, 'skip': 0, 'error': 0, 'empty': 0}
    for i, d in enumerate(dirs, 1):
        log(f"[{i}/{len(dirs)}] {d.name}")
        r = process_video(d)
        stats[r] = stats.get(r, 0) + 1
    dt = time.time() - t0
    log(f"=== Fase B DONE in {dt:.0f}s ({dt/60:.1f}min) ===")
    log(f"  stats: {stats}")


if __name__ == '__main__':
    main()

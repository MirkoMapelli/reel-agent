#!/usr/bin/env python3
"""Analisi incrementale per-video -> brain/corpus/{creator}/videos/{id}/analysis.json.

Pipeline:
  1. Shot detection (PySceneDetect, riusa analyze_raw)
  2. Estrazione 1 frame centrale per shot
  3. Vision via Gemini (5 frame per call)
  4. Trascritto via Whisper (small, word timestamps)
  5. Allineamento parole -> shot
  6. Salva analysis.json

Idempotente: salta video con analysis.json.
"""
import os, sys, json, time, base64, subprocess
from pathlib import Path
from dotenv import load_dotenv

BASE = Path('/opt/reel-agent')
CORPUS = BASE / 'brain' / 'corpus'
LOG = BASE / 'brain' / 'analyze.log'
load_dotenv(BASE / '.env')

sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / 'app'))
from app.workers.analyze_raw import detect_scenes

from openai import OpenAI
CLIENT = OpenAI(base_url='https://openrouter.ai/api/v1',
                api_key=os.environ['OPENROUTER_API_KEY'])
VISION_MODEL = 'google/gemini-2.5-flash'


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    LOG.parent.mkdir(exist_ok=True, parents=True)
    with open(LOG, 'a') as f:
        f.write(line + '\n')


def extract_frame(video, ts, out):
    cmd = ['ffmpeg', '-y', '-v', 'error', '-ss', f'{ts:.3f}', '-i', str(video),
           '-frames:v', '1', '-vf', 'scale=720:1280:force_original_aspect_ratio=decrease',
           '-q:v', '3', str(out)]
    r = subprocess.run(cmd, capture_output=True, timeout=20)
    return r.returncode == 0 and out.exists()


def img_uri(p):
    with open(p, 'rb') as f:
        return 'data:image/jpeg;base64,' + base64.b64encode(f.read()).decode()


VISION_PROMPT = """Analizza questi {n} frame consecutivi di un video TikTok.

Per OGNI frame (in ordine), rispondi con oggetto JSON con ESATTAMENTE questi campi:
{{
  "idx": <0-based>,
  "role": "<uno di: food_closeup, food_detail, food_plated, drink_detail, drink_cheers, person_eating, person_talking, hands_gesture, venue_interior_wide, venue_interior_detail, venue_exterior, menu_board, claw_game, detail, other>",
  "subject": "<2-5 parole, es: 'tonno tagliato', 'chef al banco'>",
  "description": "<1-2 frasi in italiano, cosa si vede>",
  "composition": "<wide|medium|close_up|detail>",
  "angle": "<dall_alto|orizzontale|dal_basso|pov>",
  "has_person": <true|false>,
  "has_logo": <true|false>,
  "lighting": "<naturale|artificiale_caldo|artificiale_freddo|mista>",
  "venue_elements": ["<elementi architettonici visibili: muro, monitor, insegna, tavolo, murale...>"]
}}

Output: SOLO array JSON con {n} oggetti. Niente markdown.
"""


def parse_json_array(raw):
    t = (raw or '').strip()
    if t.startswith('```'):
        t = t.split('\n', 1)[1]
        if t.endswith('```'):
            t = t[:-3]
    t = t.strip()
    i, j = t.find('['), t.rfind(']')
    if i >= 0 and j > i:
        t = t[i:j+1]
    return json.loads(t)


def vision_batch(frame_paths, retries=3):
    content = [{'type': 'text', 'text': VISION_PROMPT.format(n=len(frame_paths))}]
    for p in frame_paths:
        content.append({'type': 'image_url', 'image_url': {'url': img_uri(p)}})
    for attempt in range(retries):
        try:
            r = CLIENT.chat.completions.create(
                model=VISION_MODEL,
                messages=[{'role': 'user', 'content': content}],
                max_tokens=2500, temperature=0.2,
            )
            return parse_json_array(r.choices[0].message.content)
        except Exception as e:
            wait = 3 * (2 ** attempt)
            log(f"    vision retry {attempt+1}/{retries} wait={wait}s: {str(e)[:120]}")
            time.sleep(wait)
    return None


def whisper_transcribe(video_path):
    from faster_whisper import WhisperModel
    model = WhisperModel('small', device='cpu', compute_type='int8')
    segments, _ = model.transcribe(str(video_path), language='it', beam_size=1,
                                    word_timestamps=True)
    segs, words = [], []
    for s in segments:
        segs.append({'start': round(s.start, 3), 'end': round(s.end, 3),
                     'text': s.text.strip()})
        for w in (s.words or []):
            words.append({'start': round(w.start, 3), 'end': round(w.end, 3),
                          'word': w.word.strip()})
    return segs, words


def align_to_shots(words, shots):
    out = []
    for sh in shots:
        ws = [w['word'] for w in words
              if sh['start'] <= (w['start'] + w['end']) / 2.0 < sh['end']]
        out.append({'idx': sh['idx'], 'start': sh['start'], 'end': sh['end'],
                    'dur': sh['dur'], 'transcript': ' '.join(ws).strip()})
    return out


def analyze_video(video_dir: Path, force=False) -> dict:
    """Analizza 1 video. Ritorna {ok, video_dir, n_shots, skipped, error}."""
    video_dir = Path(video_dir)
    vpath = video_dir / 'video.mp4'
    out_path = video_dir / 'analysis.json'
    frames_dir = video_dir / 'frames'

    if not vpath.exists():
        return {'ok': False, 'error': 'video.mp4 mancante', 'video_dir': str(video_dir)}
    if out_path.exists() and not force:
        return {'ok': True, 'skipped': True, 'video_dir': str(video_dir)}

    # 1. shot detection
    log(f"[{video_dir.name}] shot detection...")
    try:
        scenes = detect_scenes(str(vpath))
    except Exception as e:
        return {'ok': False, 'error': f'scene detect: {e}', 'video_dir': str(video_dir)}
    if not scenes:
        return {'ok': False, 'error': 'nessuna scena', 'video_dir': str(video_dir)}

    # 2. frame centrale per shot
    frames_dir.mkdir(exist_ok=True)
    shots = []
    for i, (s, e) in enumerate(scenes):
        dur = e - s
        center = s + dur / 2.0
        fp = frames_dir / f'shot_{i:03d}.jpg'
        if extract_frame(vpath, center, fp):
            shots.append({'idx': i, 'start': round(s, 3), 'end': round(e, 3),
                          'dur': round(dur, 3), 'center': round(center, 3),
                          'frame': str(fp)})

    # 3. vision
    log(f"[{video_dir.name}] vision su {len(shots)} shot...")
    descriptions = []
    BATCH = 5
    for b in range(0, len(shots), BATCH):
        batch_frames = [sh['frame'] for sh in shots[b:b+BATCH]]
        result = vision_batch(batch_frames)
        if result is None:
            log(f"    batch {b//BATCH} fallito")
            continue
        for i, d in enumerate(result):
            gi = b + i
            if gi < len(shots):
                d['shot_idx'] = shots[gi]['idx']
                d['start'] = shots[gi]['start']
                d['end'] = shots[gi]['end']
                d['dur'] = shots[gi]['dur']
                descriptions.append(d)
        time.sleep(1.0)

    # 4. whisper
    log(f"[{video_dir.name}] trascrizione...")
    try:
        segments, words = whisper_transcribe(vpath)
    except Exception as e:
        log(f"    whisper errore: {e}")
        segments, words = [], []

    # 5. allineamento
    aligned = align_to_shots(words, shots) if words else []
    full_text = ' '.join(s['text'] for s in segments)

    # 6. salva
    meta = {}
    if (video_dir / 'meta.json').exists():
        try:
            meta = json.loads((video_dir / 'meta.json').read_text())
        except Exception:
            pass

    out = {
        'video_id': video_dir.name,
        'creator': video_dir.parent.parent.name,
        'meta': meta,
        'n_shots': len(shots),
        'total_dur': round(sum(s['dur'] for s in shots), 2),
        'shots': shots,
        'descriptions': descriptions,
        'segments': segments,
        'words': words,
        'aligned': aligned,
        'full_text': full_text,
        'analyzed_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
    }
    out_path.write_text(json.dumps(out, indent=2, ensure_ascii=False))
    log(f"[{video_dir.name}] ok: {len(shots)} shot, {len(descriptions)} descr, {len(words)} parole")
    return {'ok': True, 'skipped': False, 'n_shots': len(shots),
            'n_desc': len(descriptions), 'n_words': len(words),
            'video_dir': str(video_dir)}


def analyze_creator(creator: str, force=False) -> dict:
    """Analizza tutti i video del creator. Salta quelli con analysis.json."""
    slug = creator.strip().lstrip('@')
    cdir = CORPUS / slug / 'videos'
    if not cdir.exists():
        return {'ok': False, 'error': f'corpus/{slug}/videos non esiste'}
    vids = sorted([d for d in cdir.iterdir() if d.is_dir()])
    log(f"=== analyze @{slug}: {len(vids)} video ===")
    stats = {'ok': 0, 'skipped': 0, 'error': 0}
    for i, vd in enumerate(vids, 1):
        log(f"[{i}/{len(vids)}] {vd.name}")
        r = analyze_video(vd, force=force)
        if not r['ok']:
            stats['error'] += 1
            log(f"    ERRORE: {r.get('error')}")
        elif r.get('skipped'):
            stats['skipped'] += 1
        else:
            stats['ok'] += 1
    log(f"=== done: {stats} ===")
    return {'ok': True, 'creator': slug, **stats}


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--creator', required=True)
    ap.add_argument('--video', default=None, help='solo un video_id')
    ap.add_argument('--force', action='store_true')
    ap.add_argument('--only-missing', action='store_true',
                    help='analizza solo video senza analysis.json (veloce)')
    a = ap.parse_args()

    if a.video:
        vdir = CORPUS / a.creator.strip().lstrip('@') / 'videos' / a.video
        r = analyze_video(vdir, force=a.force)
    elif a.only_missing:
        slug = a.creator.strip().lstrip('@')
        vids_dir = CORPUS / slug / 'videos'
        missing = [d for d in sorted(vids_dir.iterdir())
                   if d.is_dir() and not (d / 'analysis.json').exists()]
        log(f"=== analyze ONLY-MISSING @{slug}: {len(missing)} video da analizzare ===")
        stats = {'ok': 0, 'error': 0}
        for i, vd in enumerate(missing, 1):
            log(f"[{i}/{len(missing)}] {vd.name}")
            rr = analyze_video(vd, force=False)
            if rr['ok']:
                stats['ok'] += 1
            else:
                stats['error'] += 1
                log(f"    ERRORE: {rr.get('error')}")
        r = {'ok': True, 'creator': slug, **stats}
    else:
        r = analyze_creator(a.creator, force=a.force)
    print(json.dumps(r, indent=2, ensure_ascii=False))

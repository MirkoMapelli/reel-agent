#!/usr/bin/env python3
"""Fase C: trascrizione ASR + allineamento con shot.
Input : media/style_ref/*.mp4 + brain/frames/{ref_id}/_index.json
Output: brain/reference_analyses/{ref_id}_transcript.json
Ripristinabile: salta video con _transcript.json gia' presente.
"""
import os, sys, json, time
from pathlib import Path

sys.path.insert(0, '/opt/reel-agent/app')
from workers.style_learn import transcribe_video  # riuso

BRAIN = Path('/opt/reel-agent/brain')
FRAMES = BRAIN / 'frames'
OUT = BRAIN / 'reference_analyses'
LOG = BRAIN / 'phase_c.log'
OUT.mkdir(parents=True, exist_ok=True)


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG, 'a') as f:
        f.write(line + '\n')


def transcribe_with_segments(video_path):
    """Whisper con segmenti E timestamp per-parola."""
    from faster_whisper import WhisperModel
    model = WhisperModel('small', device='cpu', compute_type='int8')
    segments, _ = model.transcribe(
        str(video_path), language='it', beam_size=1,
        word_timestamps=True,
    )
    out_segments = []
    out_words = []
    for s in segments:
        out_segments.append({
            'start': round(s.start, 3),
            'end': round(s.end, 3),
            'text': s.text.strip(),
        })
        for w in (s.words or []):
            out_words.append({
                'start': round(w.start, 3),
                'end': round(w.end, 3),
                'word': w.word.strip(),
            })
    return out_segments, out_words


def align_segments_to_shots(words, shots):
    """Per ogni shot, prendi SOLO le parole che cadono nel suo intervallo.

    Una parola appartiene allo shot se il suo centro temporale
    cade in [shot.start, shot.end).
    """
    aligned = []
    for sh in shots:
        s_start, s_end = sh['start'], sh['end']
        words_in_shot = []
        for w in words:
            w_center = (w['start'] + w['end']) / 2.0
            if s_start <= w_center < s_end:
                words_in_shot.append(w['word'])
        text = ' '.join(words_in_shot).strip()
        aligned.append({
            'shot_idx': sh['idx'],
            'start': s_start,
            'end': s_end,
            'dur': sh['dur'],
            'transcript': text,
        })
    return aligned


def process_video(ref_dir):
    ref_id = ref_dir.name
    index_path = ref_dir / '_index.json'
    out_path = OUT / f"{ref_id}_transcript.json"

    if out_path.exists():
        log(f"  {ref_id}: SKIP")
        return 'skip'
    if not index_path.exists():
        log(f"  {ref_id}: no _index.json")
        return 'error'

    index = json.loads(index_path.read_text())
    video_path = index['video']
    shots = index.get('shots', [])
    if not shots:
        log(f"  {ref_id}: 0 shot")
        return 'empty'

    if not Path(video_path).exists():
        log(f"  {ref_id}: video non trovato {video_path}")
        return 'error'

    try:
        segments, words = transcribe_with_segments(video_path)
    except Exception as e:
        log(f"  {ref_id}: ASR error: {type(e).__name__}: {str(e)[:120]}")
        return 'error'

    aligned = align_segments_to_shots(words, shots)

    full_text = ' '.join(s['text'] for s in segments)
    out = {
        'ref_id': ref_id,
        'video': video_path,
        'n_segments': len(segments),
        'n_words': len(words),
        'n_shots': len(shots),
        'full_text': full_text,
        'segments': segments,
        'words': words,
        'aligned_shots': aligned,
    }
    out_path.write_text(json.dumps(out, indent=2, ensure_ascii=False))
    log(f"  {ref_id}: {len(segments)} segmenti, {len(full_text)} char")
    return 'ok'


def main():
    only = sys.argv[1] if len(sys.argv) > 1 else None
    if only:
        ref_dir = FRAMES / only
        if not ref_dir.exists():
            log(f"ERRORE: {ref_dir} non esiste")
            return
        log(f"=== Fase C TEST su {only} ===")
        r = process_video(ref_dir)
        log(f"=== TEST DONE: {r} ===")
        return

    dirs = sorted([d for d in FRAMES.iterdir() if d.is_dir()])
    log(f"=== Fase C START: {len(dirs)} video ===")
    t0 = time.time()
    stats = {'ok': 0, 'skip': 0, 'error': 0, 'empty': 0}
    for i, d in enumerate(dirs, 1):
        log(f"[{i}/{len(dirs)}] {d.name}")
        r = process_video(d)
        stats[r] = stats.get(r, 0) + 1
    dt = time.time() - t0
    log(f"=== Fase C DONE in {dt:.0f}s ({dt/60:.1f}min) ===")
    log(f"  stats: {stats}")


if __name__ == '__main__':
    main()

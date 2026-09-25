#!/usr/bin/env python3
"""Fase A: shot detection + frame centrali per ogni shot.
Input : media/style_ref/*.mp4  (95 video)
Output: brain/frames/{ref_id}/shot_NN.jpg + _index.json
Ripristinabile: salta video con _index.json gia' presente.
"""
import os, sys, glob, json, subprocess, time
from pathlib import Path

# Aggiungi app al path
sys.path.insert(0, '/opt/reel-agent/app')
from workers.analyze_raw import detect_scenes  # riuso

SRC_DIR = Path('/opt/reel-agent/media/style_ref')
OUT_DIR = Path('/opt/reel-agent/brain/frames')
LOG = Path('/opt/reel-agent/brain/phase_a.log')
OUT_DIR.mkdir(parents=True, exist_ok=True)


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG, 'a') as f:
        f.write(line + '\n')


def extract_frame(video, ts, out_path):
    """Estrae 1 frame al timestamp ts, scala a 720x1280, jpeg q82."""
    cmd = [
        'ffmpeg', '-y', '-v', 'error',
        '-ss', f'{ts:.3f}',
        '-i', str(video),
        '-frames:v', '1',
        '-vf', 'scale=720:1280:force_original_aspect_ratio=decrease,pad=720:1280:(ow-iw)/2:(oh-ih)/2',
        '-q:v', '3',
        str(out_path),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    return r.returncode == 0 and os.path.exists(out_path)


def process_video(video_path):
    vid = Path(video_path)
    ref_id = vid.stem
    out_dir = OUT_DIR / ref_id
    index_path = out_dir / '_index.json'

    if index_path.exists():
        log(f"  {ref_id}: SKIP (index esiste)")
        return 'skip'

    out_dir.mkdir(exist_ok=True)

    try:
        scenes = detect_scenes(str(vid))
    except Exception as e:
        log(f"  {ref_id}: ERRORE detect_scenes: {e}")
        return 'error'

    if not scenes:
        log(f"  {ref_id}: nessuna scena")
        return 'empty'

    shots = []
    for i, (s, e) in enumerate(scenes):
        dur = e - s
        center = s + dur / 2.0
        frame_path = out_dir / f"shot_{i:03d}.jpg"
        ok = extract_frame(vid, center, frame_path)
        if ok:
            shots.append({
                'idx': i,
                'start': round(s, 3),
                'end': round(e, 3),
                'dur': round(dur, 3),
                'center': round(center, 3),
                'frame': str(frame_path),
            })

    index = {
        'ref_id': ref_id,
        'video': str(vid),
        'n_shots': len(shots),
        'total_dur': round(sum(sh['dur'] for sh in shots), 2),
        'shots': shots,
    }
    with open(index_path, 'w') as f:
        json.dump(index, f, indent=2)
    log(f"  {ref_id}: {len(shots)} shot, {index['total_dur']}s")
    return 'ok'


def main():
    videos = sorted(SRC_DIR.glob('*.mp4'))
    log(f"=== Fase A START: {len(videos)} video ===")
    t0 = time.time()
    stats = {'ok': 0, 'skip': 0, 'error': 0, 'empty': 0}
    for i, v in enumerate(videos, 1):
        log(f"[{i}/{len(videos)}] {v.name}")
        r = process_video(v)
        stats[r] = stats.get(r, 0) + 1
    dt = time.time() - t0
    log(f"=== Fase A DONE in {dt:.0f}s ===")
    log(f"  stats: {stats}")
    log(f"  output: {OUT_DIR}")


if __name__ == '__main__':
    main()

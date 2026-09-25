#!/usr/bin/env python3
"""Converte approccioB_result.json in EDL + renderizza.

Output: render_999.mp4 con timeline da approccioB.
"""
import os, sys, json, time
from pathlib import Path

BASE = Path('/opt/reel-agent')
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / 'app'))
from dotenv import load_dotenv
load_dotenv(BASE / '.env')

# Offset dei 10 raw nel master (dallo script precedente)
OFFSETS = {
    'IMG_6813.MOV': 0.00,
    'IMG_6815.MOV': 3.77,
    'IMG_6816.MOV': 6.74,
    'IMG_6820.MOV': 12.34,
    'IMG_6821.MOV': 20.14,
    'IMG_6823.MOV': 47.18,
    'IMG_6825.MOV': 74.44,
    'IMG_6829.MOV': 104.08,
    'IMG_6835.MOV': 126.25,
    'IMG_6836.MOV': 152.68,
}


def probe_dur(p):
    import subprocess
    r = subprocess.run(['ffprobe', '-v', 'error', '-show_entries',
                        'format=duration', '-of',
                        'default=noprint_wrappers=1:nokey=1', str(p)],
                       capture_output=True, text=True, timeout=10)
    try:
        return float(r.stdout.strip())
    except Exception:
        return 0


def convert_to_edl(approccio_result):
    """Trasforma la timeline approccioB in EDL per render_final."""
    timeline_out = []
    for i, s in enumerate(approccio_result['timeline']):
        src = s['source_raw']
        master_start = s.get('start_master', 0)
        master_end = s.get('end_master', 0)
        raw_offset = OFFSETS.get(src, 0)

        # Converti master -> coordinate nel raw
        in_sec = max(0.0, master_start - raw_offset)
        out_sec = max(in_sec + 0.5, master_end - raw_offset)

        raw_path = BASE / 'media' / 'raw' / src
        raw_dur = probe_dur(raw_path)
        # Clamp
        out_sec = min(out_sec, raw_dur)

        # Keyframe (se esistono)
        stem = Path(src).stem
        kf_dir = BASE / 'media' / 'frames' / 'curate_job_187'
        kfs = []
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
            'duration': round(out_sec - in_sec, 2),
            'requested_duration': round(master_end - master_start, 2),
            'beat_type': s.get('role', ''),
            'beat_moment': s.get('reason', '')[:60],
            'subtitle_text': s.get('comment', '').strip(),
        })

    edl = {
        'title': 'ApproccioB — Test',
        'voiceover_script': approccio_result.get('voiceover_script', ''),
        'timeline': timeline_out,
        'total_duration': round(sum(t['duration'] for t in timeline_out), 2),
        'n_clips': len(timeline_out),
        'profile_id': 16,
        'venue_name': 'Wing Stop Milano',
        'engine': 'approccioB_test',
    }
    return edl


def main():
    print('=== CONVERT approccioB -> EDL ===')
    res = json.loads((Path('/tmp') / 'approccioB_result.json').read_text())
    edl = convert_to_edl(res)
    print(f'  n shot: {edl["n_clips"]}')
    print(f'  durata: {edl["total_duration"]}s')

    # Verifica clip paths
    missing = 0
    for t in edl['timeline']:
        if not Path(t['clip_path']).exists():
            print(f'  ✗ MANCA: {t["clip_path"]}')
            missing += 1
    if missing:
        print(f'  {missing} clip mancanti')
        return

    # Salva come job_999
    edl_path = BASE / 'media' / 'edl' / 'job_999_edl.json'
    edl_path.write_text(json.dumps(edl, indent=2, ensure_ascii=False))
    print(f'  EDL salvato: {edl_path}')
    print()
    print('Ora lancia il render dalla webapp ReelAgent (porta 5000)')
    print('oppure esegui: curl POST /api/render/run')


if __name__ == '__main__':
    main()

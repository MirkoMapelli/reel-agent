#!/usr/bin/env python3
"""Importa i 95 style_ref + analisi esistenti dentro brain/corpus/jay.emme/.

Sorgenti:
  media/style_ref/*.mp4                          (95 video)
  brain/reference_analyses/*_vision.json         (descrizioni shot)
  brain/reference_analyses/*_transcript.json     (trascritti allineati)
  brain/frames/{ref_id}/_index.json              (indice shot)

Destinazione:
  brain/corpus/jay.emme/videos/{video_id}/
    video.mp4    (hardlink per non duplicare spazio)
    meta.json
    analysis.json (formato compatibile con brain_analyze.py)
"""
import os, sys, json, shutil, re, time
from pathlib import Path

BASE = Path('/opt/reel-agent')
STYLE_REF = BASE / 'media' / 'style_ref'
REFS = BASE / 'brain' / 'reference_analyses'
FRAMES_DIR = BASE / 'brain' / 'frames'
CORPUS = BASE / 'brain' / 'corpus' / 'jay.emme'
VIDEOS = CORPUS / 'videos'
LOG = BASE / 'brain' / 'import_legacy.log'


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    LOG.parent.mkdir(exist_ok=True, parents=True)
    with open(LOG, 'a') as f:
        f.write(line + '\n')


def parse_ref_id(fname: str):
    """ref_01_7685448205776997665.mp4 -> ('ref_01_7685448205776997665', '7685448205776997665')"""
    m = re.match(r'(ref_\d+_\d+)\.mp4$', fname)
    if m:
        full = m.group(1)
        vid = full.split('_')[-1]
        return full, vid
    return None, None


def main():
    VIDEOS.mkdir(parents=True, exist_ok=True)
    videos = sorted(STYLE_REF.glob('*.mp4'))
    log(f"=== import legacy: {len(videos)} video ===")

    imported = skipped = errors = 0
    for i, vpath in enumerate(videos, 1):
        ref_full, vid = parse_ref_id(vpath.name)
        if not vid:
            log(f"  [{i}/{len(videos)}] SKIP nome non valido: {vpath.name}")
            errors += 1
            continue

        vdir = VIDEOS / vid
        out_analysis = vdir / 'analysis.json'
        if out_analysis.exists():
            skipped += 1
            continue

        vdir.mkdir(exist_ok=True)

        # 1. hardlink video (risparmia spazio)
        dest_video = vdir / 'video.mp4'
        if not dest_video.exists():
            try:
                os.link(vpath, dest_video)
            except OSError:
                shutil.copy2(vpath, dest_video)

        # 2. meta.json
        meta = {
            'id': vid,
            'ref_id': ref_full,
            'title': '',
            'url': f'https://www.tiktok.com/@jay.emme/video/{vid}',
            'creator': 'jay.emme',
            'source': 'style_ref_legacy',
            'imported_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
        }
        (vdir / 'meta.json').write_text(json.dumps(meta, indent=2, ensure_ascii=False))

        # 3. carica vision
        vis_path = REFS / f'{ref_full}_vision.json'
        trs_path = REFS / f'{ref_full}_transcript.json'
        idx_path = FRAMES_DIR / ref_full / '_index.json'

        if not vis_path.exists() or not trs_path.exists():
            log(f"  [{i}/{len(videos)}] MISS analisi per {ref_full}")
            errors += 1
            continue

        vis = json.loads(vis_path.read_text())
        trs = json.loads(trs_path.read_text())

        # 4. costruisci analysis.json compatibile
        shots_raw = vis.get('shots') or []
        if not shots_raw and idx_path.exists():
            idx = json.loads(idx_path.read_text())
            shots_raw = idx.get('shots', [])

        descriptions = vis.get('descriptions', [])
        aligned = trs.get('aligned_shots', [])
        words = trs.get('words', [])
        segments = trs.get('segments', [])

        # Normalizza: descriptions hanno idx via shot_idx
        desc_by_idx = {d.get('shot_idx', d.get('idx')): d for d in descriptions}
        trs_by_idx = {a['shot_idx']: a for a in aligned}

        shots_out = []
        for sh in shots_raw:
            sidx = sh.get('idx', sh.get('shot_idx'))
            d = desc_by_idx.get(sidx, {})
            t = trs_by_idx.get(sidx, {})
            shots_out.append({
                'idx': sidx,
                'start': sh.get('start'),
                'end': sh.get('end'),
                'dur': sh.get('dur'),
                'center': sh.get('center'),
                'role': d.get('role_visivo') or d.get('role') or 'other',
                'subject': d.get('soggetto') or d.get('subject') or '',
                'description': d.get('cosa_si_nota') or d.get('description') or '',
                'composition': d.get('composizione') or d.get('composition') or '',
                'angle': d.get('angolo') or d.get('angle') or '',
                'has_person': d.get('presenza_umana', d.get('has_person', False)),
                'has_logo': d.get('presenza_logo', d.get('has_logo', False)),
                'lighting': d.get('luce') or d.get('lighting') or '',
                'transcript': t.get('transcript', ''),
            })

        analysis = {
            'video_id': vid,
            'creator': 'jay.emme',
            'source': 'style_ref_legacy',
            'meta': meta,
            'n_shots': len(shots_out),
            'total_dur': round(sum((s['dur'] or 0) for s in shots_out), 2),
            'shots': shots_out,
            'descriptions': descriptions,
            'segments': segments,
            'words': words,
            'aligned': aligned,
            'full_text': trs.get('full_text', ''),
            'analyzed_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
        }
        out_analysis.write_text(json.dumps(analysis, indent=2, ensure_ascii=False))
        imported += 1
        log(f"  [{i}/{len(videos)}] ✓ {vid}: {len(shots_out)} shot")

    # _meta.json
    cmeta = {
        'creator': 'jay.emme',
        'slug': 'jay.emme',
        'source': 'style_ref_legacy',
        'last_sync': time.strftime('%Y-%m-%dT%H:%M:%S'),
        'n_videos': len([d for d in VIDEOS.iterdir() if d.is_dir() and (d/'analysis.json').exists()]),
        'n_imported_this_run': imported,
        'n_skipped': skipped,
        'n_errors': errors,
    }
    (CORPUS / '_meta.json').write_text(json.dumps(cmeta, indent=2, ensure_ascii=False))
    log(f"=== done: imported={imported} skipped={skipped} errors={errors} ===")


if __name__ == '__main__':
    main()

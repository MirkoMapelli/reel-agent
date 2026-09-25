"""Downloader TikTok -> brain/corpus/{creator}/videos/{id}/.

Architettura:
  brain/corpus/{creator}/
    _meta.json                    # channel_id, last_sync, n_videos
    videos/{video_id}/
      video.mp4                   # il video
      meta.json                   # title, duration, upload_date, url

Idempotente: se un video esiste gia', lo salta.
"""
import os, sys, json, subprocess, time, re
from pathlib import Path

sys.path.insert(0, '/opt/reel-agent/app')
from app.config import CFG

BASE = Path('/opt/reel-agent')
CORPUS = BASE / 'brain' / 'corpus'
COOKIES = BASE / 'cookies.txt'


def _slug(s: str) -> str:
    return re.sub(r'[^A-Za-z0-9._-]+', '_', s).strip('_')[:64]


def _ytdlp_base():
    return [
        sys.executable, '-m', 'yt_dlp',
        '--cookies', str(COOKIES),
        '--impersonate', 'chrome',
        '--no-warnings',
        '--ignore-errors',
        '--user-agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                        '(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
        '--referer', 'https://www.tiktok.com/',
    ]


def _run(cmd, timeout=900):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def list_creator_videos(creator: str, limit: int = 30,
                        date_from: str = None, date_to: str = None) -> list[dict]:
    """Ritorna lista di {id, title, url, duration, timestamp} del creator.

    Se date_from/date_to presenti: usa --dateafter/--datebefore di yt-dlp
    (filtro nativo lato TikTok). Non usa --playlist-end.
    """
    creator = creator.strip().lstrip('@')
    cmd = _ytdlp_base() + [
        '--flat-playlist',
        '--dump-single-json',
    ]

    if date_from or date_to:
        # Filtro nativo yt-dlp (formato YYYYMMDD)
        if date_from:
            cmd += ['--dateafter', date_from.replace('-', '')]
        if date_to:
            cmd += ['--datebefore', date_to.replace('-', '')]
        # Safety cap: max 2000 video (evita enumerazioni infinite)
        cmd += ['--playlist-end', '2000']
    else:
        cmd += ['--playlist-end', str(limit)]

    cmd.append(f'https://www.tiktok.com/@{creator}')
    res = _run(cmd, timeout=300)
    if not res.stdout.strip():
        raise RuntimeError(f'yt-dlp vuoto: {res.stderr[:400]}')
    try:
        info = json.loads(res.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(f'JSON invalido: {res.stdout[:400]}')

    entries = info.get('entries') or []
    videos = []
    for e in entries:
        vid = e.get('id')
        if not vid:
            continue
        videos.append({
            'id': vid,
            'title': (e.get('title') or '')[:120],
            'url': f'https://www.tiktok.com/@{creator}/video/{vid}',
            'duration': e.get('duration'),
            'timestamp': e.get('timestamp'),
        })
    return videos


def _get_upload_date(video_url: str):
    """Ritorna 'YYYYMMDD' o None."""
    cmd = _ytdlp_base() + ['--skip-download', '--print', '%(upload_date)s', video_url]
    r = _run(cmd, timeout=30)
    if r.returncode == 0:
        return (r.stdout or '').strip() or None
    return None



# ============================================================
# CATALOGO VIDEO (cache metadati per scansioni veloci)
# ============================================================

INCREMENTAL_CHECK = 50   # quanti video recenti controllare in incremental


def _catalog_path(creator: str) -> Path:
    slug = _slug(creator)
    return CORPUS / slug / '_catalog.json'


def load_catalog(creator: str) -> dict:
    """Ritorna {creator, updated_at, last_full_scan, videos: [...]}."""
    p = _catalog_path(creator)
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            pass
    return {'creator': creator, 'updated_at': None, 'last_full_scan': None, 'videos': []}


def save_catalog(creator: str, videos: list):
    p = _catalog_path(creator)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = {
        'creator': creator,
        'updated_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
        'last_full_scan': time.strftime('%Y-%m-%dT%H:%M:%S'),
        'n_total': len(videos),
        'videos': videos,
    }
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    return data


def sync_catalog(creator: str, force_full: bool = False, log=print) -> dict:
    """Sincronizza il catalogo. Ritorna il catalogo aggiornato.

    - Se non esiste catalogo o force_full -> enumerazione completa
    - Altrimenti -> controlla solo i primi INCREMENTAL_CHECK per nuovi
    """
    creator = creator.strip().lstrip('@')
    catalog = load_catalog(creator)
    cached_ids = {v['id'] for v in catalog.get('videos', [])}

    if force_full or not catalog['videos']:
        log(f'[catalog] full scan @{creator}...')
        videos = list_creator_videos(creator, limit=2000)
        # Salva solo i campi necessari
        slim = [{
            'id': v['id'],
            'title': v.get('title', '')[:120],
            'url': v['url'],
            'duration': v.get('duration'),
            'timestamp': v.get('timestamp'),
        } for v in videos]
        save_catalog(creator, slim)
        log(f'[catalog] salvato: {len(slim)} video')
        return load_catalog(creator)

    # Incremental: enumera solo i primi N
    log(f'[catalog] incremental scan @{creator} (check primi {INCREMENTAL_CHECK})...')
    recent = list_creator_videos(creator, limit=INCREMENTAL_CHECK)
    new_videos = [v for v in recent if v['id'] not in cached_ids]

    if not new_videos:
        log(f'[catalog] nessun video nuovo ({len(recent)} controllati)')
        return catalog

    # Safety: se TUTTI i 50 sono nuovi, il catalogo e' sospetto -> full
    if len(new_videos) >= INCREMENTAL_CHECK:
        log(f'[catalog] tutti i {len(recent)} sono nuovi, forzo full scan')
        return sync_catalog(creator, force_full=True, log=log)

    # Aggiungi i nuovi in testa
    log(f'[catalog] {len(new_videos)} video nuovi trovati')
    slim_new = [{
        'id': v['id'],
        'title': v.get('title', '')[:120],
        'url': v['url'],
        'duration': v.get('duration'),
        'timestamp': v.get('timestamp'),
    } for v in new_videos]
    all_videos = slim_new + catalog['videos']
    save_catalog(creator, all_videos)
    return load_catalog(creator)


def catalog_filter(catalog: dict, date_from: str = None, date_to: str = None,
                   min_duration: float = 5.0, max_duration: float = 300.0) -> list:
    """Filtra il catalogo per data e durata. Ritorna lista di video."""
    df = (date_from or '').replace('-', '') or None
    dt_ = (date_to or '').replace('-', '') or None
    out = []
    for v in catalog.get('videos', []):
        d = v.get('duration')
        if d is not None and (d < min_duration or d > max_duration):
            continue
        ts = v.get('timestamp')
        if ts is not None:
            ds = time.strftime('%Y%m%d', time.localtime(ts))
        else:
            ds = None
        if df and ds and ds < df:
            continue
        if dt_ and ds and ds > dt_:
            continue
        out.append(v)
    return out


def download_creator(creator: str, limit: int = 30, min_duration: float = 5.0,
                     max_duration: float = 300.0, skip_existing: bool = True,
                     date_from: str = None, date_to: str = None, log=print) -> dict:
    """Scarica fino a `limit` video del creator in corpus/{creator}/videos/."""
    creator = creator.strip().lstrip('@')
    slug = _slug(creator)
    corpus_dir = CORPUS / slug
    videos_dir = corpus_dir / 'videos'
    videos_dir.mkdir(parents=True, exist_ok=True)

    log(f'[1/3] sync catalogo @{creator}...')
    try:
        catalog = sync_catalog(creator, force_full=False, log=log)
    except Exception as e:
        return {'ok': False, 'error': f'catalog: {e}'}

    log(f'[2/3] filtro su {catalog["n_total"]} video in cache (limit={limit}, date {date_from or "…"} -> {date_to or "…"})')
    listing = catalog_filter(catalog, date_from=date_from, date_to=date_to,
                             min_duration=min_duration, max_duration=max_duration)

    # Se filtro data attivo: prendi TUTTI quelli nel range (limit ignorato)
    # Se nessun filtro: prendi i primi `limit` (i piu' recenti)
    if not (date_from or date_to):
        listing = listing[:limit]

    log(f'      trovati {len(listing)} video dopo filtro')

    log(f'[2/3] download in {videos_dir}')
    downloaded = 0
    skipped = 0
    errors = 0
    for i, v in enumerate(listing, 1):
        vid = v['id']
        vdir = videos_dir / vid
        vpath = vdir / 'video.mp4'
        if vpath.exists() and vpath.stat().st_size > 1024 and skip_existing:
            skipped += 1
            continue

        vdir.mkdir(parents=True, exist_ok=True)
        out_tmpl = str(vdir / 'video.%(ext)s')

        cmd = _ytdlp_base() + [
            '-f', 'b[ext=mp4]/b',
            '--write-info-json',
            '-o', out_tmpl,
            v['url'],
        ]
        r = _run(cmd, timeout=300)

        # Trova file scaricato
        found = None
        for ext in ('mp4', 'mkv', 'webm'):
            p = vdir / f'video.{ext}'
            if p.exists() and p.stat().st_size > 1024:
                found = p
                break

        if not found:
            errors += 1
            log(f'   [{i}/{len(listing)}] ✗ {vid}: {r.stderr[:150]}')
            continue

        # Normalizza a video.mp4
        if found.name != 'video.mp4':
            found.rename(vpath)

        # Salva meta.json
        meta = {
            'id': vid,
            'title': v.get('title', ''),
            'url': v['url'],
            'creator': creator,
            'duration': v.get('duration'),
            'downloaded_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
        }
        (vdir / 'meta.json').write_text(json.dumps(meta, indent=2, ensure_ascii=False))

        # Rimuovi info.json di yt-dlp (ridondante)
        for extra in vdir.glob('video.info.json'):
            extra.unlink()

        downloaded += 1
        size_mb = vpath.stat().st_size / 1024 / 1024
        log(f'   [{i}/{len(listing)}] ✓ {vid} ({size_mb:.1f} MB) {v.get("title","")[:50]}')

    # Aggiorna _meta.json
    meta_path = corpus_dir / '_meta.json'
    existing_meta = {}
    if meta_path.exists():
        try:
            existing_meta = json.loads(meta_path.read_text())
        except Exception:
            pass

    all_videos = [d for d in videos_dir.iterdir() if d.is_dir() and (d / 'video.mp4').exists()]
    corpus_meta = {
        'creator': creator,
        'slug': slug,
        'channel_id': existing_meta.get('channel_id'),
        'last_sync': time.strftime('%Y-%m-%dT%H:%M:%S'),
        'n_videos': len(all_videos),
        'n_downloaded_this_run': downloaded,
        'n_skipped': skipped,
        'n_errors': errors,
    }
    meta_path.write_text(json.dumps(corpus_meta, indent=2, ensure_ascii=False))

    log(f'[3/3] corpus aggiornato: {meta_path}')
    log(f'      totale video in corpus: {len(all_videos)}')

    return {
        'ok': True,
        'creator': creator,
        'downloaded': downloaded,
        'skipped': skipped,
        'errors': errors,
        'total': len(all_videos),
        'corpus_dir': str(corpus_dir),
    }


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('creator')
    ap.add_argument('--limit', type=int, default=30)
    a = ap.parse_args()
    result = download_creator(a.creator, limit=a.limit)
    print(json.dumps(result, indent=2, ensure_ascii=False))

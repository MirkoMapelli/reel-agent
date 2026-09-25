"""Operazioni brain chiamabili dal job manager (per la webapp)."""
import os, sys, json, time, subprocess
from pathlib import Path

BASE = Path('/opt/reel-agent')
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / 'app'))

from app.jobs import manager
from app.workers import tiktok_downloader as dl


def _run_stream(cmd, job_id, timeout=3600, prefix=''):
    """Esegue subprocess streammando stdout riga per riga al manager.log().

    Mantiene viva la connessione SSE ed espone progresso reale.
    Ritorna (returncode, stderr_tail).
    """
    import subprocess, os, select, sys

    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, bufsize=1, universal_newlines=True,
    )

    stderr_lines = []
    start = time.time()

    # Leggi stdout riga per riga (bloccante su readline, ok)
    import threading
    def drain_stderr():
        for line in proc.stderr:
            stderr_lines.append(line.rstrip())

    t_err = threading.Thread(target=drain_stderr, daemon=True)
    t_err.start()

    for line in proc.stdout:
        line = line.rstrip()
        if line:
            manager.log(job_id, f'{prefix}{line}')
        if time.time() - start > timeout:
            proc.kill()
            manager.log(job_id, f'{prefix}process timeout, kill', level='error')
            break

    proc.wait()
    t_err.join(timeout=2)
    return proc.returncode, '\n'.join(stderr_lines[-30:])


def _run_brain_analyze(creator, job_id):
    """Esegue brain_analyze.py come subprocess (stream output)."""
    cmd = [sys.executable, '-u', str(BASE / 'brain_analyze.py'),
           '--creator', creator.strip().lstrip('@')]
    rc, err = _run_stream(cmd, job_id, timeout=3600, prefix='[analyze] ')
    class R: pass
    r = R(); r.returncode = rc; r.stderr = err
    return r


def _run_brain_build(job_id):
    cmd = [sys.executable, '-u', str(BASE / 'brain_build.py')]
    rc, err = _run_stream(cmd, job_id, timeout=900, prefix='[build] ')
    class R: pass
    r = R(); r.returncode = rc; r.stderr = err
    return r


def _delete_videos_for_creator(handle, job_id, manager):
    """Rimuove i video.mp4 del creator (mantiene analysis.json + meta.json)."""
    slug = handle.strip().lstrip('@')
    vids_dir = BASE / 'brain' / 'corpus' / slug / 'videos'
    if not vids_dir.exists():
        return 0, 0
    n_del = 0
    freed = 0
    for vdir in vids_dir.iterdir():
        if not vdir.is_dir():
            continue
        vpath = vdir / 'video.mp4'
        if vpath.exists():
            sz = vpath.stat().st_size
            vpath.unlink()
            n_del += 1
            freed += sz
    return n_del, freed


def run_delete_videos(job_id, handle):
    """Cancella i video di un creator (mantiene analisi)."""
    handle = (handle or '').strip().lstrip('@')
    if not handle:
        manager.fail(job_id, 'handle vuoto')
        return
    if handle == 'jay.emme':
        manager.fail(job_id, 'NON puoi cancellare video di jay.emme (creator primario)')
        return
    manager.log(job_id, f'[delete] video di @{handle}')
    try:
        n, freed = _delete_videos_for_creator(handle, job_id, manager)
    except Exception as e:
        manager.fail(job_id, f'delete error: {e}')
        return
    freed_mb = round(freed / 1024 / 1024, 1)
    manager.log(job_id, f'  cancellati {n} video, liberati {freed_mb} MB')
    manager.finish(job_id, {'ok': True, 'handle': handle, 'n_deleted': n, 'freed_mb': freed_mb})


def run_panic_cleanup(job_id):
    """Ferma tutto: kill processi, marca job zombie, pulisci file temporanei.

    NON uccide se stesso. Sicuro da chiamare in qualsiasi momento.
    Ritorna statistiche di cosa ha fatto.
    """
    import subprocess, signal, os as _os

    manager.log(job_id, '=== 🛑 PANIC STOP ===')
    manager.update_progress(job_id, 5, 'kill processi...')

    # 1. Kill processi brain/yt-dlp (NON se stesso, NON brain_ops)
    kill_patterns = [
        'brain_analyze.py',
        'brain_build.py',
        'yt-dlp',
        'yt_dlp',
    ]
    killed = {}
    for pat in kill_patterns:
        try:
            # pkill -f pattern, ma esclude PID corrente e parent
            r = subprocess.run(
                ['pkill', '-f', pat],
                capture_output=True, text=True, timeout=5
            )
            killed[pat] = (r.returncode == 0)
            if r.returncode == 0:
                manager.log(job_id, f'  killed: {pat}')
        except Exception as e:
            killed[pat] = False
            manager.log(job_id, f'  kill {pat} err: {e}')

    manager.update_progress(job_id, 30, 'marca job zombie...')

    # 2. Marca tutti i job 'running' come error (tranne se stesso)
    from datetime import datetime, timezone
    import sqlite3
    from app.config import CFG
    db_path = CFG['DB_PATH']
    n_zombie = 0
    try:
        conn = sqlite3.connect(db_path)
        now = datetime.now(timezone.utc).isoformat(timespec='seconds')
        n_zombie = conn.execute(
            "UPDATE jobs SET status='error', error='panic stop', updated_at=? "
            "WHERE status='running' AND id != ?",
            (now, job_id)
        ).rowcount
        conn.commit()
        conn.close()
        manager.log(job_id, f'  job marcati zombie: {n_zombie}')
    except Exception as e:
        manager.log(job_id, f'  DB error: {e}', level='error')

    manager.update_progress(job_id, 50, 'pulizia file temporanei...')

    # 3. Pulizia file temporanei (.part, .ytdl, .tmp)
    cleaned_files = 0
    freed_bytes = 0
    corpus_dir = BASE / 'brain' / 'corpus'
    for pattern in ('*.part', '*.ytdl', '*.tmp'):
        for f in corpus_dir.rglob(pattern):
            try:
                sz = f.stat().st_size
                f.unlink()
                cleaned_files += 1
                freed_bytes += sz
            except Exception:
                pass
    manager.log(job_id, f'  file temporanei puliti: {cleaned_files} ({round(freed_bytes/1024/1024,2)} MB)')

    manager.update_progress(job_id, 70, 'pulizia video senza analisi...')

    # 4. Cleanup video orfani (video.mp4 senza meta.json / troppo vecchi)
    # NON cancelliamo nulla di automatico: solo file vuoti/corrotti
    orphan_removed = 0
    for vdir in corpus_dir.glob('*/videos/*/'):
        vpath = vdir / 'video.mp4'
        if vpath.exists() and vpath.stat().st_size < 1024:
            # file corrotto o vuoto
            try:
                vpath.unlink()
                orphan_removed += 1
            except Exception:
                pass

    if orphan_removed:
        manager.log(job_id, f'  video corrotti rimossi: {orphan_removed}')

    manager.update_progress(job_id, 90, 'verifica...')

    # 5. Verifica: nessun processo brain rimasto
    import time
    time.sleep(2)
    remaining = 0
    for pat in kill_patterns:
        try:
            r = subprocess.run(
                ['pgrep', '-f', pat],
                capture_output=True, text=True, timeout=3
            )
            # Escludi il PID corrente
            pids = [p for p in (r.stdout or '').strip().split('\n') if p and int(p) != _os.getpid()]
            remaining += len(pids)
        except Exception:
            pass

    manager.log(job_id, f'  processi rimasti dopo kill: {remaining}')
    manager.update_progress(job_id, 100, 'completato')

    manager.finish(job_id, {
        'ok': True,
        'killed': killed,
        'zombie_jobs': n_zombie,
        'cleaned_files': cleaned_files,
        'freed_mb': round(freed_bytes / 1024 / 1024, 2),
        'corrupt_videos_removed': orphan_removed,
        'remaining_processes': remaining,
    })


def run_full_scan(job_id, handle):
    """Forza full scan del catalogo per un creator."""
    handle = (handle or '').strip().lstrip('@')
    if not handle:
        manager.fail(job_id, 'handle vuoto')
        return
    manager.log(job_id, f'=== FULL SCAN catalogo @{handle} ===')
    manager.update_progress(job_id, 10, 'full scan')
    try:
        cat = dl.sync_catalog(handle, force_full=True,
                              log=lambda m: manager.log(job_id, m))
    except Exception as e:
        manager.fail(job_id, f'full scan error: {e}')
        return
    manager.update_progress(job_id, 100, 'completato')
    manager.finish(job_id, {'ok': True, 'handle': handle, 'n_total': cat.get('n_total', 0)})


def run_download_creator(job_id, handle, limit=15, date_from=None, date_to=None):
    """Scarica video di un creator nel corpus."""
    handle = (handle or '').strip().lstrip('@')
    if not handle:
        manager.fail(job_id, 'handle vuoto')
        return
    manager.log(job_id, f'[1/3] enumerazione @{handle} (limit={limit}, data {date_from or "…"} -> {date_to or "…"})')
    manager.update_progress(job_id, 10, 'enumerazione')
    try:
        r = dl.download_creator(handle, limit=limit, date_from=date_from,
                                 date_to=date_to, log=lambda m: manager.log(job_id, m))
    except Exception as e:
        manager.fail(job_id, f'download error: {e}')
        return
    manager.update_progress(job_id, 100, 'completato')
    manager.finish(job_id, r)


def run_analyze_creator(job_id, handle):
    """Analizza tutti i video del creator (skip esistenti)."""
    handle = (handle or '').strip().lstrip('@')
    manager.log(job_id, f'[analyze] @{handle}')
    manager.update_progress(job_id, 10, 'analisi')
    try:
        r = _run_brain_analyze(handle, job_id)
        # Log tail
        tail = (r.stdout or '')[-1500:]
        for line in tail.split('\n')[-20:]:
            if line.strip():
                manager.log(job_id, line.strip())
        if r.returncode != 0:
            manager.fail(job_id, f'buffa exit {r.returncode}: {(r.stderr or "")[-300:]}')
            return
    except Exception as e:
        manager.fail(job_id, f'analyze exception: {e}')
        return
    manager.update_progress(job_id, 100, 'completato')
    manager.finish(job_id, {'ok': True, 'handle': handle})


def run_build_brain(job_id):
    """Rigenera il brain dal corpus (versioning + retention 3)."""
    manager.log(job_id, '[build] rigenerazione brain')
    manager.update_progress(job_id, 10, 'build')
    try:
        r = _run_brain_build(job_id)
        tail = (r.stdout or '')[-1500:]
        for line in tail.split('\n')[-20:]:
            if line.strip():
                manager.log(job_id, line.strip())
        if r.returncode != 0:
            manager.fail(job_id, f'build exit {r.returncode}: {(r.stderr or "")[-300:]}')
            return
    except Exception as e:
        manager.fail(job_id, f'build exception: {e}')
        return
    # Leggi versione attiva
    ver_file = BASE / 'brain' / 'active_version.txt'
    ver = ver_file.read_text().strip() if ver_file.exists() else '?'
    manager.update_progress(job_id, 100, 'completato')
    manager.finish(job_id, {'ok': True, 'version': ver})


def run_add_and_compare(job_id, handle, limit=15, date_from=None, date_to=None, auto_delete=True):
    """Scarica + analizza + rigenera brain. Se auto_delete: cancella i video a fine."""
    handle = (handle or '').strip().lstrip('@')
    if not handle:
        manager.fail(job_id, 'handle vuoto')
        return
    # Non cancellare MAI i video di jay.emme
    if handle == 'jay.emme':
        auto_delete = False
    manager.log(job_id, f'=== ADD + COMPARE @{handle} (limit={limit}, date {date_from or "…"} -> {date_to or "…"}) ===')

    # FASE 1: download
    manager.log(job_id, f'[1/3] download @{handle}')
    manager.update_progress(job_id, 5, 'download')
    try:
        r = dl.download_creator(handle, limit=limit, date_from=date_from,
                                 date_to=date_to, log=lambda m: manager.log(job_id, m))
    except Exception as e:
        manager.fail(job_id, f'download error: {e}')
        return
    if not r.get('ok'):
        manager.fail(job_id, f"download fallito: {r.get('error')}")
        return
    manager.log(job_id, f"      scaricati {r['downloaded']}, skip {r['skipped']}, err {r['errors']}")
    manager.update_progress(job_id, 30, 'download ok')

    # FASE 2: analyze
    manager.log(job_id, f'[2/3] analisi @{handle}')
    manager.update_progress(job_id, 35, 'analyze')
    r2 = _run_brain_analyze(handle, job_id)
    tail = (r2.stdout or '')[-1000:]
    for line in tail.split('\n')[-15:]:
        if line.strip():
            manager.log(job_id, line.strip())
    if r2.returncode != 0:
        manager.fail(job_id, f'analyze exit {r2.returncode}: {(r2.stderr or "")[-300:]}')
        return
    manager.update_progress(job_id, 75, 'analyze ok')

    # FASE 3: build
    manager.log(job_id, f'[3/3] build brain con contrasto @{handle}')
    manager.update_progress(job_id, 80, 'build')
    r3 = _run_brain_build(job_id)
    tail = (r3.stdout or '')[-1000:]
    for line in tail.split('\n')[-15:]:
        if line.strip():
            manager.log(job_id, line.strip())
    if r3.returncode != 0:
        manager.fail(job_id, f'build exit {r3.returncode}: {(r3.stderr or "")[-300:]}')
        return

    # 4. Cancellazione video (se auto_delete)
    if auto_delete and handle != 'jay.emme':
        manager.log(job_id, f'[4/4] cancellazione video @{handle} (auto_delete)')
        n, freed = _delete_videos_for_creator(handle, job_id, manager)
        freed_mb = round(freed / 1024 / 1024, 1)
        manager.log(job_id, f'  cancellati {n} video, liberati {freed_mb} MB')
    else:
        freed_mb = 0
        n = 0

    ver_file = BASE / 'brain' / 'active_version.txt'
    ver = ver_file.read_text().strip() if ver_file.exists() else '?'
    manager.update_progress(job_id, 100, 'completato')
    manager.finish(job_id, {
        'ok': True, 'handle': handle, 'version': ver,
        'downloaded': r['downloaded'], 'total_creator_videos': r['total'],
        'deleted_videos': n, 'freed_mb': freed_mb, 'auto_delete': auto_delete,
    })


def run_rollback(job_id, version):
    """Attiva una versione specifica del brain (visibile o archiviata)."""
    version = (version or '').strip()
    fname = f'creator_brain_{version}.json'
    versions_dir = BASE / 'brain' / 'versions'
    archive_dir = versions_dir / '_archive'

    # Cerca in versions/ e _archive/
    fpath = versions_dir / fname
    location = 'versions/'
    if not fpath.exists():
        fpath = archive_dir / fname
        location = '_archive/'
    if not fpath.exists():
        manager.fail(job_id, f'versione {version} non trovata in versions/ ne _archive/')
        return

    manager.log(job_id, f'trovata {version} in {location}')
    brain = json.loads(fpath.read_text())
    (BASE / 'brain' / 'active_brain.json').write_text(json.dumps(brain, indent=2, ensure_ascii=False))
    (BASE / 'brain' / 'active_version.txt').write_text(version + '\n')
    manager.log(job_id, f'rollback a {version} ok')
    manager.finish(job_id, {'ok': True, 'version': version, 'location': location})


if __name__ == '__main__':
    # Test manuale
    print('brain_ops importato')

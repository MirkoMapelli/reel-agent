"""Studio: carica/modifica EDL + musica."""
import os, sys, json, shutil, uuid
from pathlib import Path

BASE = Path('/opt/reel-agent')
MEDIA = BASE / 'media'
EDL_DIR = MEDIA / 'edl'
STUDIO_DIR = MEDIA / 'studio'
MUSIC_DIR = MEDIA / 'studio' / 'music'
STUDIO_DIR.mkdir(parents=True, exist_ok=True)
MUSIC_DIR.mkdir(parents=True, exist_ok=True)


def load_latest_edl():
    """Ritorna l'EDL piu' recente (job_999 di solito)."""
    edls = sorted(EDL_DIR.glob('job_*_edl.json'),
                  key=lambda p: p.stat().st_mtime, reverse=True)
    if not edls:
        return None, None
    p = edls[0]
    try:
        return json.loads(p.read_text()), p.name
    except Exception:
        return None, None


def save_edl(edl, job_id='999'):
    """Salva EDL modificato (sovrascrive job_999)."""
    p = EDL_DIR / f'job_{job_id}_edl.json'
    p.write_text(json.dumps(edl, indent=2, ensure_ascii=False))
    return str(p)


def list_music():
    out = []
    for f in sorted(MUSIC_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if f.suffix.lower() in ('.mp3', '.wav', '.m4a', '.aac', '.ogg'):
            out.append({
                'name': f.name,
                'size_mb': round(f.stat().st_size / 1024 / 1024, 2),
                'mtime': f.stat().st_mtime,
            })
    return out


def save_music(file_storage):
    safe = ''.join(c for c in file_storage.filename if c.isalnum() or c in '._-')[:80]
    if not safe:
        safe = f'music_{uuid.uuid4().hex[:8]}.mp3'
    p = MUSIC_DIR / safe
    file_storage.save(str(p))
    return {'name': safe, 'size_mb': round(p.stat().st_size / 1024 / 1024, 2)}


def delete_music(name):
    p = MUSIC_DIR / name
    if p.exists():
        p.unlink()
        return True
    return False


# ============================================================
# FILMSTRIP
# ============================================================
FILM_DIR = MEDIA / 'studio' / 'filmstrips'
FILM_DIR.mkdir(parents=True, exist_ok=True)


def _probe_dur(p):
    import subprocess
    try:
        r = subprocess.run(
            ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
             '-of', 'default=noprint_wrappers=1:nokey=1', str(p)],
            capture_output=True, text=True, timeout=10)
        return float(r.stdout.strip())
    except Exception:
        return 0


def ensure_filmstrip(clip_name, n=10):
    """Estrae n frame equidistanti dal raw. Ritorna {frames: [abs_path], dur}."""
    import subprocess
    from app.config import CFG
    safe = ''.join(c for c in clip_name if c.isalnum() or c in '._-')[:80]
    src = Path(CFG['RAW_DIR']) / clip_name
    if not src.exists():
        return {'frames': [], 'dur': 0}
    dur = _probe_dur(src)
    if dur <= 0:
        return {'frames': [], 'dur': 0}

    out_dir = FILM_DIR / safe
    out_dir.mkdir(parents=True, exist_ok=True)

    frames = []
    for i in range(n):
        pct = i / (n - 1) if n > 1 else 0
        ts = dur * pct
        fp = out_dir / f'f{i:02d}.jpg'
        if not fp.exists():
            cmd = ['ffmpeg', '-y', '-v', 'error',
                   '-ss', f'{ts:.3f}', '-i', str(src),
                   '-frames:v', '1',
                   '-vf', 'scale=100:-2',
                   '-q:v', '5', str(fp)]
            try:
                subprocess.run(cmd, capture_output=True, timeout=15)
            except Exception:
                pass
        if fp.exists():
            frames.append(str(fp))
    return {'frames': frames, 'dur': dur}


# ============================================================
# FRAME @ timestamp (per handle in/out della barra trim)
# ============================================================
FRAME_CACHE_DIR = MEDIA / 'studio' / 'frames_cache'
FRAME_CACHE_DIR.mkdir(parents=True, exist_ok=True)


def ensure_frame_at(clip_name, t):
    """Estrae (con cache) il frame del raw a t secondi. Ritorna path assoluto o None."""
    import subprocess
    from app.config import CFG
    safe = ''.join(c for c in clip_name if c.isalnum() or c in '._-')[:80]
    src = Path(CFG['RAW_DIR']) / clip_name
    if not src.exists():
        return None
    # Nome file: cached per arrotondamento a 0.05s per non generare 1000 file
    t_key = round(float(t) * 20) / 20
    fp = FRAME_CACHE_DIR / safe / f't_{t_key:.2f}.jpg'
    if fp.exists():
        return str(fp)
    fp.parent.mkdir(parents=True, exist_ok=True)
    cmd = ['ffmpeg', '-y', '-v', 'error',
           '-ss', f'{t_key:.3f}', '-i', str(src),
           '-frames:v', '1',
           '-vf', 'scale=120:-2',
           '-q:v', '4', str(fp)]
    try:
        subprocess.run(cmd, capture_output=True, timeout=15)
    except Exception:
        return None
    return str(fp) if fp.exists() else None


# ============================================================
# MIX FINALE: video + musica con volumi separati
# ============================================================
def apply_mix(video_in, out_path, music_path=None, voice_vol=1.0, music_vol=0.3):
    """Mixa video + musica con volumi separati.

    Gestisce automaticamente:
      - video CON audio (VO registrata/montata): mix voce + musica
      - video SENZA audio (render muto):          solo musica
      - nessuna musica:                           solo applica volume al video
    """
    import subprocess
    import os as _os

    video_in = str(video_in)
    out_path = str(out_path)
    if not _os.path.exists(video_in):
        return (False, f"video_in non esiste: {video_in}")

    has_music = music_path and _os.path.exists(music_path)

    # 1. Rileva se il video ha audio
    probe = subprocess.run(
        ['ffprobe', '-v', 'error', '-select_streams', 'a',
         '-show_entries', 'stream=index', '-of', 'csv=p=0', video_in],
        capture_output=True, text=True, timeout=10)
    has_audio = bool((probe.stdout or '').strip())

    # 2. Costruisci il comando in base ai 4 casi
    if has_audio and has_music:
        # Mix voce + musica, entrambi normalizzati poi volumi utente
        cmd = [
            'ffmpeg', '-y', '-v', 'error',
            '-i', video_in, '-i', str(music_path),
            '-filter_complex',
            f'[0:a]loudnorm=I=-16:TP=-1.5:LRA=11,volume={voice_vol:.3f}[v];'
            f'[1:a]loudnorm=I=-16:TP=-1.5:LRA=11,aloop=loop=-1:size=2e9,volume={music_vol:.3f}[m];'
            f'[v][m]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[aout]',
            '-map', '0:v', '-map', '[aout]',
            '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k',
            '-shortest',
            '-movflags', '+faststart',
            out_path,
        ]
    elif has_audio and not has_music:
        # Solo voce, applica volume
        cmd = [
            'ffmpeg', '-y', '-v', 'error',
            '-i', video_in,
            '-af', f'volume={voice_vol:.3f}',
            '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k',
            '-movflags', '+faststart',
            out_path,
        ]
    elif not has_audio and has_music:
        # Solo musica (video muto) — applica loudnorm per portare la musica
        # a livello standard anche se la sorgente è bassa
        cmd = [
            'ffmpeg', '-y', '-v', 'error',
            '-i', video_in, '-i', str(music_path),
            '-filter_complex',
            f'[1:a]loudnorm=I=-16:TP=-1.5:LRA=11,'
            f'aloop=loop=-1:size=2e9,volume={music_vol:.3f}[m]',
            '-map', '0:v', '-map', '[m]',
            '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k',
            '-shortest',
            '-movflags', '+faststart',
            out_path,
        ]
    else:
        # Né voce né musica: errore sensato
        return (False, 'video senza audio e nessuna musica: niente da mixare')

    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        return (r.returncode == 0, (r.stderr or '')[-800:])
    except Exception as e:
        return (False, str(e)[:300])

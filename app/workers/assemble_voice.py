"""Monta le registrazioni voce (una per scena) sul video renderizzato.
Ogni traccia viene ritardata al suo offset temporale nella timeline.
L'output mantiene la durata completa del video base (non tronca).
"""
import os
import json
import glob
import subprocess
from datetime import datetime

from app.config import CFG
from app.jobs import manager
from app.db import get_conn


# ============================================================
# HELPERS
# ============================================================

def _render_for_edl(edl_job_id: int) -> str | None:
    """Trova il render base specifico per un edl_job_id (es. render_86.mp4)."""
    target = os.path.join(CFG["RENDERS_DIR"], f"render_{edl_job_id}.mp4")
    if os.path.exists(target):
        return target
    return None


def _latest_render() -> str | None:
    """Fallback: trova il render base MUTO più recente."""
    files = glob.glob(os.path.join(CFG["RENDERS_DIR"], "render_*.mp4"))
    files = [f for f in files if "render_voice_" not in os.path.basename(f)]
    if not files:
        return None
    files.sort(key=os.path.getmtime, reverse=True)
    return files[0]


def _load_edl_by_job(edl_job_id: int):
    p = os.path.join(CFG["MEDIA_DIR"], "edl", f"job_{edl_job_id}_edl.json")
    if not os.path.exists(p):
        return None
    with open(p) as f:
        return json.load(f)


def _voice_dir(edl_job_id: int) -> str:
    d = os.path.join(CFG["VOICEOVER_DIR"], f"edl_{edl_job_id}")
    os.makedirs(d, exist_ok=True)
    return d


def _video_duration(path: str) -> float:
    """Durata in secondi di un video. Ritorna 0 se errore."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=10,
        )
        return float((r.stdout or "0").strip())
    except Exception:
        return 0.0


def _ffmpeg_convert_to_wav(src, dst):
    """Converte in WAV mono 44100Hz PCM. Ritorna (ok, err)."""
    cmd = ["ffmpeg", "-y", "-i", src,
           "-ac", "1", "-ar", "44100", "-c:a", "pcm_s16le", dst]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            return False, (r.stderr or "")[-400:]
        if not os.path.exists(dst):
            return False, "file wav non creato"
        return True, ""
    except subprocess.TimeoutExpired:
        return False, "timeout conversione"
    except Exception as e:
        return False, str(e)


# ============================================================
# ORCHESTRATORE
# ============================================================

def assemble_voiceover(job_id: int, edl_job_id: int):
    """Mixa le registrazioni voce sul render base."""

    # ---- 1. Carica EDL ----
    manager.log(job_id, f"Cerco EDL job_{edl_job_id}…")
    edl = _load_edl_by_job(edl_job_id)
    if not edl:
        manager.fail(job_id, f"EDL job_{edl_job_id} non trovato.")
        return

    timeline = edl.get("timeline", [])
    if not timeline:
        manager.fail(job_id, "Timeline vuota nell'EDL.")
        return

    # ---- 2. Trova il render base ----
    render = _render_for_edl(edl_job_id)
    if not render:
        render = _latest_render()
        if render:
            manager.log(job_id,
                f"⚠ render_{edl_job_id}.mp4 non trovato, uso fallback: {os.path.basename(render)}",
                level="error")
        else:
            manager.fail(job_id, f"Nessun render trovato. Esegui 'Renderizza video' per EDL {edl_job_id} prima.")
            return
    else:
        manager.log(job_id, f"Render base: {os.path.basename(render)} (specifico per EDL {edl_job_id})")

    video_dur = _video_duration(render)
    manager.log(job_id, f"Durata video base: {video_dur:.2f}s")

    # ---- 3. Trova le registrazioni ----
    vdir = _voice_dir(edl_job_id)
    recordings = {}
    # Priorità: webm/m4a/mp4 (originali) → wav (convertiti) ultimo
    for ext in ("webm", "m4a", "mp4", "wav"):
        for f in glob.glob(os.path.join(vdir, f"scene_*.{ext}")):
            try:
                sid = int(os.path.basename(f).replace("scene_", "").rsplit(".", 1)[0])
                if sid not in recordings:
                    recordings[sid] = f
            except Exception:
                pass

    if not recordings:
        manager.fail(job_id, f"Nessuna registrazione trovata in {vdir}.")
        return
    manager.log(job_id, f"Trovate {len(recordings)} registrazioni.")

    # ---- 4. Converti in WAV uniforme ----
    wav_files = {}
    failed = []
    for sid, src in recordings.items():
        if src.lower().endswith(".wav") and os.path.exists(src):
            wav_files[sid] = src
            continue
        dst = os.path.join(vdir, f"scene_{sid}.wav")
        ok, err = _ffmpeg_convert_to_wav(src, dst)
        if ok:
            wav_files[sid] = dst
        else:
            failed.append((sid, err))

    if not wav_files:
        msg = f"Conversione wav fallita su tutte le {len(recordings)} scene."
        if failed:
            msg += f"\nEsempio (scena {failed[0][0]}):\n{failed[0][1]}"
        manager.fail(job_id, msg)
        return
    manager.log(job_id, f"Convertite {len(wav_files)}/{len(recordings)} tracce in wav.")

    # ---- 5. Calcola offset temporali per ogni scena ----
    # Ogni scena parte dopo la somma delle durate precedenti
    offset = 0.0
    segments = []
    for seg in timeline:
        sid = seg.get("scene_id")
        dur = float(seg.get("duration", 0))
        segments.append({
            "scene_id": sid,
            "start": offset,
            "dur": dur,
        })
        offset += dur
    total_dur = offset
    manager.log(job_id, f"Timeline: {len(segments)} scene · durata totale {total_dur:.2f}s")

    # ---- 6. Costruisci il comando ffmpeg ----
    # input 0: video base
    inputs = ["-i", render]

    # input N: una traccia wav per ogni scena con registrazione
    filter_parts = []
    n_tracks = 0
    for seg in segments:
        sid = seg["scene_id"]
        if sid not in wav_files:
            continue
        n_tracks += 1
        inputs += ["-i", wav_files[sid]]
        delay_ms = int(seg["start"] * 1000)
        # aformat mono + adelay all'offset + volume unitario
        label = f"a{n_tracks}"
        filter_parts.append(
            f"[{n_tracks}:a]aformat=channel_layouts=mono,"
            f"adelay={delay_ms}|{delay_ms},volume=1.0[{label}]"
        )

    if n_tracks == 0:
        manager.fail(job_id, "Nessuna traccia da mixare.")
        return

    # amix unisce tutte le tracce
    labels = "".join(f"[a{i}]" for i in range(1, n_tracks + 1))
    filter_complex = ";".join(filter_parts) + ";" + \
                     labels + f"amix=inputs={n_tracks}:duration=longest:normalize=0[aout]"

    # ---- 7. Comando finale ----
    final = os.path.join(CFG["RENDERS_DIR"], f"render_voice_{edl_job_id}.mp4")

    cmd = ["ffmpeg", "-y", "-loglevel", "error"] + inputs + [
        "-filter_complex", filter_complex,
        "-map", "0:v", "-map", "[aout]",
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k",
    ]

    # Forza l'output ad avere la durata esatta del video (non troncare)
    if video_dur > 0:
        cmd += ["-t", f"{video_dur:.3f}"]

    cmd.append(final)

    # ---- 8. Esegui ----
    manager.update_progress(job_id, 40, "mix finale…")
    manager.log(job_id, f"Mixo {n_tracks} tracce su {total_dur:.2f}s → output atteso {video_dur:.2f}s")

    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    except subprocess.TimeoutExpired:
        manager.fail(job_id, "Timeout mix (>15 min).")
        return

    if r.returncode != 0 or not os.path.exists(final):
        manager.log(job_id, f"CMD: {' '.join(cmd)[:500]}", level="error")
        manager.log(job_id, f"FILTER: {filter_complex[:500]}", level="error")
        manager.fail(job_id, f"ffmpeg errore:\n{(r.stderr or '')[-1500:]}")
        return

    actual_dur = _video_duration(final)
    size_mb = round(os.path.getsize(final) / (1024 * 1024), 1)
    manager.log(job_id, f"✓ Video con voce: {os.path.basename(final)} ({size_mb} MB · {actual_dur:.2f}s)")

    # ---- 9. Salva in runs ----
    try:
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO runs (profile_id, status, output_path, voiceover_path) VALUES (?, 'done', ?, ?)",
                (edl.get("profile_id"), final, os.path.join(vdir, "mixed.wav")),
            )
    except Exception as e:
        manager.log(job_id, f"WARN: impossibile salvare in runs: {e}", level="error")

    manager.update_progress(job_id, 100, "completato")
    manager.finish(job_id, {
        "output": final,
        "size_mb": size_mb,
        "n_tracks": n_tracks,
        "duration": round(actual_dur, 2),
        "url": f"/renders/{os.path.basename(final)}",
    })

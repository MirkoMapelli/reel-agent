"""Analisi video raw: scene detection + split ritmico + contact sheet 12 frame +
metadata tecnici + audio WAV + trascrizione Whisper.

Schema manifest compatibile con lo Step 6.2 (curate_clips aggiornato).
"""
import os
import json
import subprocess
import sys
import math
from pathlib import Path
from typing import List, Tuple, Dict, Any

import cv2
import numpy as np

from app.config import CFG
from app.jobs import manager
from app.workers.scene_analyzer import analyze_scene_full


# ============================================================
# RITMO DA BIBLE — pattern durata subclip
# Media 2.15s, range 1.5-3.2s (dalla style_bible.json)
# Pattern variabile (non uniforme) per dare ritmo naturale
# ============================================================
_DURATION_PATTERN = [
    1.7, 2.3, 1.9, 3.0, 1.8, 2.2, 2.5, 2.0, 1.5, 2.4, 3.2, 2.1, 2.2, 1.6, 2.8
]


# ============================================================
# PROBE
# ============================================================
def probe_video(video_path: str) -> dict:
    """Ritorna durata, fps, dimensioni, rotazione e info audio."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_streams", "-show_format",
        "-of", "json", video_path,
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        data = json.loads(res.stdout or "{}")
        streams = data.get("streams", [])
        if not streams:
            return {}

        v = next((s for s in streams if s.get("codec_type") == "video"), None)
        a = next((s for s in streams if s.get("codec_type") == "audio"), None)
        if v is None:
            return {}

        fr = v.get("r_frame_rate", "0/1")
        try:
            num, den = fr.split("/")
            fps = float(num) / float(den) if float(den) else 30.0
        except Exception:
            fps = 30.0

        dur = float(v.get("duration") or data.get("format", {}).get("duration") or 0)

        rotation = 0
        rot = v.get("rotation")
        if rot is None:
            for sd in v.get("side_data_list", []) or []:
                if "rotation" in sd:
                    rot = sd["rotation"]
                    break
        if rot is None:
            rot = (v.get("tags") or {}).get("rotate")
        try:
            rotation = int(float(rot)) if rot is not None else 0
        except Exception:
            rotation = 0
        rotation = rotation % 360

        w = int(v.get("width", 0) or 0)
        h = int(v.get("height", 0) or 0)
        if rotation in (90, 270):
            w, h = h, w

        return {
            "width": w,
            "height": h,
            "fps": round(fps, 2),
            "duration": round(dur, 2),
            "rotation": rotation,
            "has_audio": a is not None,
            "audio_codec": (a or {}).get("codec_name") if a else None,
            "audio_channels": int((a or {}).get("channels") or 0) if a else 0,
            "audio_sample_rate": int((a or {}).get("sample_rate") or 0) if a else 0,
        }
    except Exception:
        return {}


def detect_scenes(video_path: str, threshold: float = 27.0) -> List[Tuple[float, float]]:
    """Scene detection con PySceneDetect."""
    from scenedetect import open_video, SceneManager
    from scenedetect.detectors import ContentDetector

    video = open_video(video_path)
    sm = SceneManager()
    sm.add_detector(ContentDetector(threshold=threshold))
    sm.detect_scenes(video, show_progress=False)
    scene_list = sm.get_scene_list()

    if not scene_list:
        info = probe_video(video_path)
        dur = info.get("duration", 0)
        return [(0.0, dur)] if dur > 0 else []

    return [(s.get_seconds(), e.get_seconds()) for s, e in scene_list]


# ============================================================
# SPLIT RITMICO (da bible)
# ============================================================
def _split_scene_with_rhythm(start: float, end: float, video_seed: int = 0) -> List[Tuple[float, float]]:
    """Split di una scena in subclip con pattern ritmico variabile.

    - durata media target: 2.15s (bible)
    - range: 1.5 - 3.2s
    - pattern variabile per evitare uniformità
    - se la scena è < 1.8s, mantieni intera
    """
    dur = end - start
    if dur <= 1.8:
        return [(start, end)]

    pattern = _DURATION_PATTERN
    offset = (video_seed * 7) % len(pattern)

    parts = []
    t = start
    i = 0

    while t < end - 0.5:
        remaining = end - t
        if remaining < 1.8:
            # Assorbi il resto nell'ultimo subclip
            if parts:
                s0, _ = parts[-1]
                parts[-1] = (s0, end)
            else:
                parts.append((t, end))
            break

        target = pattern[(offset + i) % len(pattern)]
        if target > remaining:
            target = remaining
        if 0 < remaining - target < 1.8:
            # Residuo troppo piccolo: se remaining > 3.2 lo spezzo in due
            # (entrambe in range 1.5-3.2), altrimenti prendo tutto.
            target = remaining / 2.0 if remaining > 3.2 else remaining

        sub_end = t + target
        parts.append((round(t, 3), round(sub_end, 3)))
        t = sub_end
        i += 1

    if not parts:
        return [(start, end)]
    return parts


# ============================================================
# CONTACT SHEET 12 FRAME (4x3)
# ============================================================
def _extract_contact_sheet(video_path: str, start: float, end: float,
                           out_path: str, n_frames: int = 12) -> bool:
    """Estrae 12 frame dal subclip e li dispone in griglia 4x3 (1080x1440)."""
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return False

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    dur = max(0.3, end - start)

    FW, FH = 270, 480  # 4x3 -> 1080 x 1440

    positions = [0.05 + i * (0.90 / (n_frames - 1)) for i in range(n_frames)]

    frames = []
    for p in positions:
        ts = start + dur * p
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(ts * fps))
        ok, fr = cap.read()
        if ok and fr is not None:
            fr = cv2.resize(fr, (FW, FH))
            frames.append(fr)
    cap.release()

    if len(frames) < 4:
        return False

    # Pad se mancano frame (raro)
    while len(frames) < n_frames:
        frames.append(frames[-1].copy())

    row1 = np.hstack(frames[0:4])
    row2 = np.hstack(frames[4:8])
    row3 = np.hstack(frames[8:12])
    sheet = np.vstack([row1, row2, row3])
    sheet = cv2.copyMakeBorder(sheet, 6, 6, 6, 6, cv2.BORDER_CONSTANT, value=(255, 255, 255))

    cv2.imwrite(out_path, sheet, [cv2.IMWRITE_JPEG_QUALITY, 82])
    return True


# ============================================================
# AUDIO EXTRACTION
# ============================================================
def extract_audio(video_path: str, out_path: str) -> bool:
    """Estrae audio mono 16kHz WAV dall'intero video."""
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", video_path,
        "-ac", "1", "-ar", "16000",
        "-c:a", "pcm_s16le",
        out_path,
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        return r.returncode == 0 and os.path.exists(out_path)
    except Exception:
        return False


# ============================================================
# TRASCRIZIONE
# ============================================================
def transcribe_with_timing(video_path: str) -> List[dict]:
    """Trascrizione Whisper con timing per segmento."""
    from faster_whisper import WhisperModel

    model = WhisperModel(
        CFG["WHISPER_MODEL"],
        device="cpu",
        compute_type=CFG["WHISPER_COMPUTE"],
        download_root=CFG["MODELS_DIR"],
    )
    segments, _ = model.transcribe(video_path, language="it", beam_size=1, vad_filter=True)
    out = []
    for s in segments:
        txt = (s.text or "").strip()
        if txt:
            out.append({
                "start": round(float(s.start), 2),
                "end": round(float(s.end), 2),
                "text": txt,
            })
    return out


# ============================================================
# SCENE PROCESSING (split + contact sheet + tech metadata)
# ============================================================
def _process_scenes(video_path: str, raw_scenes: List[Tuple[float, float]],
                    frames_root: str, video_seed: int,
                    job_id: int) -> List[dict]:
    """Da raw scene boundaries produce subclip con metadata."""
    # 1) Split
    subclips = []
    for start, end in raw_scenes:
        subs = _split_scene_with_rhythm(start, end, video_seed=video_seed)
        subclips.extend(subs)

    # 2) Per ogni subclip: contact sheet + metriche tecniche
    scenes_data = []
    for idx, (s, e) in enumerate(subclips):
        duration = round(e - s, 2)
        if duration < 1.0:
            continue

        sheet_path = os.path.join(frames_root, f"scene_{idx:03d}_sheet.jpg")

        # Contact sheet
        ok = _extract_contact_sheet(video_path, s, e, sheet_path, n_frames=12)

        # Metriche tecniche (motion, composition, brightness, blur, stability)
        try:
            metrics = analyze_scene_full(video_path, s, e)
        except Exception as ex:
            manager.log(job_id, f"      metrics error scene {idx}: {ex}", level="error")
            metrics = {}

        scenes_data.append({
            "idx": idx,
            "start": round(s, 2),
            "end": round(e, 2),
            "duration": duration,
            "contact_sheet_path": sheet_path if ok else None,
            "motion_type": metrics.get("motion_type", "unknown"),
            "motion_quality": metrics.get("motion_quality", "unknown"),
            "composition": metrics.get("composition", "unknown"),
            "stability_score": metrics.get("stability_score", 0),
            "brightness": metrics.get("brightness", 0),
            "contrast": metrics.get("contrast", 0),
            "blur_score": metrics.get("blur_score", 0),
        })

    return scenes_data


# ============================================================
# ORCHESTRATORE
# ============================================================
def run_analyze_raw(job_id: int, video_paths: List[str]):
    """Analizza ogni video raw, produce manifest con subclip + metadata."""
    total = len(video_paths)
    if total == 0:
        manager.fail(job_id, "Nessun video raw da analizzare.")
        return

    analysis_dir = os.path.join(CFG["MEDIA_DIR"], "analysis")
    frames_root_base = os.path.join(CFG["FRAMES_DIR"], f"raw_job_{job_id}")
    audio_dir = os.path.join(CFG["MEDIA_DIR"], "audio", f"raw_job_{job_id}")
    os.makedirs(analysis_dir, exist_ok=True)
    os.makedirs(frames_root_base, exist_ok=True)
    os.makedirs(audio_dir, exist_ok=True)

    manifest = {"videos": [], "total": total}

    for i, vp in enumerate(video_paths, 1):
        name = os.path.basename(vp)
        base = Path(name).stem
        manager.update_progress(job_id, int((i - 1) / total * 100), f"{i}/{total} · {name}")

        try:
            info = probe_video(vp)
            manager.log(job_id, f"[{i}/{total}] {name}  ({info.get('duration','?')}s, {info.get('width')}x{info.get('height')})")

            # 1) Scene detection (boundaries grezze)
            manager.log(job_id, f"   scene detection…")
            raw_scenes = detect_scenes(vp)
            manager.log(job_id, f"   → {len(raw_scenes)} scene grezze")

            # 2) Split + contact sheet + metadata per ogni subclip
            vfdir = os.path.join(frames_root_base, base)
            os.makedirs(vfdir, exist_ok=True)
            video_seed = abs(hash(name)) % 1000
            manager.log(job_id, f"   split ritmico + contact sheet…")
            scenes_data = _process_scenes(vp, raw_scenes, vfdir, video_seed, job_id)
            manager.log(job_id, f"   → {len(scenes_data)} subclip analizzati")

            # 3) Audio extraction
            audio_path = os.path.join(audio_dir, f"{base}.wav")
            manager.log(job_id, f"   estrazione audio…")
            audio_ok = extract_audio(vp, audio_path)
            manager.log(job_id, f"   → audio {'OK' if audio_ok else 'ERRORE'}")

            # 4) Trascrizione
            manager.log(job_id, f"   trascrizione Whisper…")
            transcript = transcribe_with_timing(vp)
            total_chars = sum(len(t["text"]) for t in transcript)
            manager.log(job_id, f"   → {len(transcript)} segmenti ({total_chars} char)")

            entry = {
                "name": name,
                "path": vp,
                "info": info,
                "scenes": scenes_data,
                "transcript": transcript,
                "audio_path": audio_path if audio_ok else None,
            }
            manifest["videos"].append(entry)

            # Salva manifest individuale
            per_file = os.path.join(analysis_dir, f"{base}.json")
            with open(per_file, "w", encoding="utf-8") as f:
                json.dump(entry, f, indent=2, ensure_ascii=False)

        except Exception as e:
            import traceback
            manager.log(job_id, f"[{i}/{total}] errore: {e}", level="error")
            manager.log(job_id, traceback.format_exc(), level="error")
            continue

        manager.update_progress(job_id, int(i / total * 100), f"{i}/{total} · {name}")

    # Salva manifest globale
    manifest_path = os.path.join(analysis_dir, f"job_{job_id}_manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    total_scenes = sum(len(v.get("scenes", [])) for v in manifest["videos"])
    manager.log(job_id, f"Manifest salvato: {manifest_path}")
    manager.log(job_id, f"Totale: {len(manifest['videos'])} video · {total_scenes} subclip")

    manager.finish(job_id, {
        "manifest": manifest_path,
        "videos": len(manifest["videos"]),
        "scenes": total_scenes,
        "total": total,
    })

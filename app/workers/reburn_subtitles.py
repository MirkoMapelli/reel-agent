"""Rigenera SRT dai testi EDL correnti e ri-brucia i sottotitoli sul merge persistente."""
import os
import json
import subprocess

from app.config import CFG
from app.jobs import manager
from app.db import get_conn


def _fmt_srt_time(t):
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}".replace(".", ",")


def _write_srt(segments, srt_path):
    with open(srt_path, "w", encoding="utf-8") as f:
        t = 0.0
        for i, seg in enumerate(segments, 1):
            start = t + 0.05
            end = t + seg["dur"] - 0.05
            t += seg["dur"]
            f.write(f"{i}\n")
            f.write(f"{_fmt_srt_time(start)} --> {_fmt_srt_time(end)}\n")
            f.write(f"{seg['text']}\n\n")


def _burn(merged_video, srt_path, style, out):
    font_name = style.get("font_name", "BebasNeue-Regular")
    font_clean = font_name.replace("-Regular", "").replace("-Bold", "").replace("-BoldSim", "")
    force_style = (
        f"FontName={font_clean},FontSize={int(style.get('font_size', 22))},"
        f"PrimaryColour={style.get('primary_color_ass', '&H00FFFFFF&')},"
        f"OutlineColour={style.get('outline_color_ass', '&H00000000&')},"
        f"BorderStyle=1,Outline={int(style.get('outline_width', 3))},Shadow=0,"
        f"Alignment={int(style.get('alignment', 2))},"
        f"MarginV={min(int(style.get('margin_v', 60)), 25)},"
        f"Bold={int(style.get('bold', 1))}"
    )
    srt_esc = srt_path.replace(":", "\\:")
    vf = f"subtitles='{srt_esc}':force_style='{force_style}',format=nv12,hwupload=extra_hw_frames=64"

    cmd = [
        "ffmpeg", "-y",
        "-init_hw_device", "qsv=hw:/dev/dri/renderD128",
        "-filter_hw_device", "hw",
        "-i", merged_video,
        "-vf", vf,
        "-c:v", "h264_qsv", "-preset", "veryfast", "-global_quality", "22",
        "-an",
        out,
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    except subprocess.TimeoutExpired:
        return False, "timeout burn-in (>15 min)"
    if r.returncode != 0 or not os.path.exists(out):
        return False, (r.stderr or "")[-1500:]
    return True, ""


def _load_profile_style(profile_id):
    if not profile_id:
        return {}
    with get_conn() as conn:
        row = conn.execute("SELECT style_json FROM profiles WHERE id=?", (profile_id,)).fetchone()
    if not row:
        return {}
    return json.loads(row["style_json"]).get("subtitle_style", {})


def reburn_subtitles(job_id, edl_job_id):
    manager.log(job_id, f"Reburn sottotitoli per EDL job_{edl_job_id}…")

    edl_path = os.path.join(CFG["MEDIA_DIR"], "edl", f"job_{edl_job_id}_edl.json")
    if not os.path.exists(edl_path):
        manager.fail(job_id, f"EDL job_{edl_job_id} non trovato.")
        return
    with open(edl_path) as f:
        edl = json.load(f)

    merged = os.path.join(CFG["MEDIA_DIR"], "renders", "_merged", f"merged_{edl_job_id}.mp4")
    if not os.path.exists(merged):
        manager.fail(job_id,
            f"Merge persistente non trovato: {merged}.\n"
            "Rilancia 'Renderizza video' per crearne uno nuovo (contiene i merge salvati).")
        return
    manager.log(job_id, f"Merge base: {merged}")

    # 1) Rigenera SRT includendo anchor (se presenti)
    srt_segments = []

    # Anchor primo
    import glob as _glob, json as _json
    anchor_cfg = {}
    cfg_path = os.path.join(CFG["MEDIA_DIR"], "anchors", "config.json")
    if os.path.exists(cfg_path):
        try:
            with open(cfg_path) as f:
                anchor_cfg = _json.load(f)
        except Exception:
            anchor_cfg = {}

    first_files = _glob.glob(os.path.join(CFG["MEDIA_DIR"], "anchors", "first_frame.*"))
    last_files = _glob.glob(os.path.join(CFG["MEDIA_DIR"], "anchors", "last_frame.*"))

    if first_files:
        dur_f = float(anchor_cfg.get("first", {}).get("duration", 1.0))
        txt_f = (anchor_cfg.get("first", {}).get("text") or "").strip()
        srt_segments.append({"dur": dur_f, "text": txt_f})

    # Scene
    for seg in edl.get("timeline", []):
        srt_segments.append({
            "dur": float(seg.get("duration", 0)),
            "text": (seg.get("subtitle_text") or "").strip() or "…",
        })

    # Anchor ultimo
    if last_files:
        dur_l = float(anchor_cfg.get("last", {}).get("duration", 1.0))
        txt_l = (anchor_cfg.get("last", {}).get("text") or "").strip()
        srt_segments.append({"dur": dur_l, "text": txt_l})

    srt_path = os.path.join(CFG["RENDERS_DIR"], f"render_{edl_job_id}.srt")
    _write_srt(srt_segments, srt_path)
    manager.log(job_id, f"SRT rigenerato: {srt_path} ({len(srt_segments)} righe)")

    # 2) Re-burn
    manager.update_progress(job_id, 30, "burn-in QSV…")
    style = _load_profile_style(edl.get("profile_id"))
    out_video = os.path.join(CFG["RENDERS_DIR"], f"render_{edl_job_id}.mp4")
    ok, err = _burn(merged, srt_path, style, out_video)
    if not ok:
        manager.fail(job_id, f"burn-in fallito:\n{err}")
        return
    size_mb = round(os.path.getsize(out_video) / (1024 * 1024), 1)
    manager.log(job_id, f"✓ Video senza voce aggiornato: {os.path.basename(out_video)} ({size_mb} MB)")

    # 3) Se esistono registrazioni voce, rimixa
    voice_dir = os.path.join(CFG["VOICEOVER_DIR"], f"edl_{edl_job_id}")
    has_voice = False
    if os.path.isdir(voice_dir):
        for f in os.listdir(voice_dir):
            if f.startswith("scene_") and f.endswith((".wav", ".webm", ".m4a")):
                has_voice = True
                break

    if has_voice:
        manager.log(job_id, "Rilevate registrazioni → rimonto anche la voce…")
        manager.update_progress(job_id, 60, "remix voce…")
        ok2, err2 = _remix_voice(edl, out_video, voice_dir, edl_job_id)
        if ok2:
            manager.log(job_id, f"✓ Video con voce: render_voice_{edl_job_id}.mp4")
        else:
            manager.log(job_id, f"Remix voce fallito: {err2}", level="error")

    manager.update_progress(job_id, 100, "completato")
    manager.finish(job_id, {
        "output": out_video,
        "srt": srt_path,
        "size_mb": size_mb,
        "url": f"/renders/{os.path.basename(out_video)}",
    })


def _remix_voice(edl, base_video, voice_dir, edl_job_id):
    """Rimixa la voce sul video appena ri-bruciato."""
    segments = []
    offset = 0.0
    for seg in edl.get("timeline", []):
        segments.append({
            "scene_id": seg.get("scene_id"),
            "start": offset,
            "dur": float(seg.get("duration", 0)),
        })
        offset += float(seg.get("duration", 0))

    # Trova file audio per scena (preferisci wav)
    wav_files = {}
    for f in os.listdir(voice_dir):
        try:
            sid = int(f.replace("scene_", "").rsplit(".", 1)[0])
        except Exception:
            continue
        full = os.path.join(voice_dir, f)
        if f.endswith(".wav"):
            wav_files[sid] = full
        elif sid not in wav_files:
            wav_files[sid] = full

    if not wav_files:
        return False, "nessuna traccia"

    inputs = ["-i", base_video]
    filter_parts = []
    n = 0
    for s in segments:
        if s["scene_id"] not in wav_files:
            continue
        n += 1
        inputs += ["-i", wav_files[s["scene_id"]]]
        delay_ms = int(s["start"] * 1000)
        filter_parts.append(
            f"[{n}:a]aformat=channel_layouts=mono,adelay={delay_ms}|{delay_ms},volume=1.0[a{n}]"
        )
    if n == 0:
        return False, "nessuna scena con voce"

    labels = "".join(f"[a{i}]" for i in range(1, n + 1))
    filter_complex = ";".join(filter_parts) + ";" + \
                     labels + f"amix=inputs={n}:duration=longest:normalize=0[aout]"

    final = os.path.join(CFG["RENDERS_DIR"], f"render_voice_{edl_job_id}.mp4")
    cmd = ["ffmpeg", "-y", "-loglevel", "error"] + inputs + [
        "-filter_complex", filter_complex,
        "-map", "0:v", "-map", "[aout]",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        final,
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        return False, "timeout remix"
    if r.returncode != 0:
        return False, (r.stderr or "")[-800:]
    return True, ""

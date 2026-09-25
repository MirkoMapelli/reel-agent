"""Render finale: taglio scene EDL, crop 9:16, concat, burn-in sottotitoli."""
import os
import json
import subprocess
import glob
from datetime import datetime

from app.config import CFG
from app.jobs import manager
from app.db import get_conn
import sys


TARGET_W = 1080
TARGET_H = 1920


def _load_profile(profile_id):
    with get_conn() as conn:
        row = conn.execute("SELECT style_json FROM profiles WHERE id=?", (profile_id,)).fetchone()
    if not row:
        raise RuntimeError(f"Profilo #{profile_id} non trovato.")
    return json.loads(row["style_json"])


def _load_latest_edl():
    d = os.path.join(CFG["MEDIA_DIR"], "edl")
    if not os.path.isdir(d):
        return None
    files = sorted(
        [os.path.join(d, f) for f in os.listdir(d)
         if f.startswith("job_") and f.endswith("_edl.json")],
        key=os.path.getmtime, reverse=True,
    )
    if not files:
        return None
    with open(files[0]) as f:
        return json.load(f), files[0]


def _probe_video(video_path):
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,r_frame_rate",
        "-of", "json", video_path,
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        s = json.loads(res.stdout or "{}").get("streams", [{}])[0]
        fr = s.get("r_frame_rate", "30/1")
        try:
            n, dd = fr.split("/")
            fps = float(n) / float(dd) if float(dd) else 30.0
        except Exception:
            fps = 30.0
        return int(s.get("width", 1920)), int(s.get("height", 1080)), round(fps, 2)
    except Exception:
        return 1920, 1080, 30.0


def _read_anchor_config():
    """Ritorna {'first': {'duration', 'text', 'file'}, 'last': {...}}."""
    d = os.path.join(CFG["MEDIA_DIR"], "anchors")
    cfg_path = os.path.join(d, "config.json")
    default = {
        "first": {"duration": 1.0, "text": "", "file": None},
        "last":  {"duration": 1.0, "text": "", "file": None},
    }
    if not os.path.exists(cfg_path):
        return default
    try:
        with open(cfg_path) as f:
            cfg = json.load(f)
        for k in ("first", "last"):
            if k not in cfg:
                cfg[k] = default[k]
            for kk, vv in default[k].items():
                cfg[k].setdefault(kk, vv)
        return cfg
    except Exception:
        return default


def _make_anchor_clip(img_path, duration, target_fps, out_path):
    """Crea un video di N secondi da un'immagine statica, con la risoluzione del primo raw."""
    # Estrai dimensioni dal video originale per matchare
    # Usiamo scale=1920:-2 come fallback, meglio: passa dims
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-loop", "1", "-i", img_path,
        "-t", f"{duration:.3f}",
        "-vf", f"scale=1080:1920:force_original_aspect_ratio=decrease,"
               f"pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black,"
               f"fps={target_fps},setsar=1",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-an",
        out_path,
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            return False, (r.stderr or "")[-400:]
        return os.path.exists(out_path), ""
    except Exception as e:
        return False, str(e)


def _get_anchor(kind):
    """Ritorna il path del frame anchor se esiste, altrimenti None."""
    import glob as _glob
    anchors_dir = os.path.join(CFG["MEDIA_DIR"], "anchors")
    if not os.path.isdir(anchors_dir):
        return None
    files = _glob.glob(os.path.join(anchors_dir, f"{kind}_frame.*"))
    return files[0] if files else None


def _cut_and_crop(src, in_sec, out_sec, dst):
    """Taglia la clip (in->out) SENZA crop né resize, encoder GPU QSV."""
    duration = max(0.1, out_sec - in_sec)
    vf = "fps=30,setsar=1,format=nv12,hwupload=extra_hw_frames=64"
    cmd = [
        "ffmpeg", "-y",
        "-init_hw_device", "qsv=hw:/dev/dri/renderD128",
        "-filter_hw_device", "hw",
        "-ss", f"{in_sec:.3f}",
        "-i", src,
        "-t", f"{duration:.3f}",
        "-vf", vf,
        "-an",
        "-c:v", "h264_qsv", "-preset", "veryfast", "-global_quality", "22",
        dst,
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired:
        return False, "cut TIMEOUT (>3 min)"
    return res.returncode == 0 and os.path.exists(dst), (res.stderr or "")[-600:]


def _video_duration(path):
    """Durata in secondi di un video (0 se errore)."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=10,
        )
        return float((r.stdout or "0").strip())
    except Exception:
        return 0.0


def _concat(parts, dst):
    lst = os.path.join(os.path.dirname(dst), "_concat.txt")
    with open(lst, "w") as f:
        for p in parts:
            f.write(f"file '{p}'\n")

    # Durata attesa = somma delle parti
    expected = sum(_video_duration(p) for p in parts)
    tolerance = max(0.5, 0.05 * expected)  # 5% o mezzo secondo

    # Tentativo 1: stream copy (veloce)
    cmd = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", lst, "-c", "copy", dst,
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if res.returncode == 0 and os.path.exists(dst):
            actual = _video_duration(dst)
            if abs(actual - expected) <= tolerance:
                return True, ""
            # Durata sbagliata → fallback
            print(f"[concat] copy ha prodotto {actual:.2f}s vs atteso {expected:.2f}s — fallback",
                  file=sys.stderr)
    except Exception:
        pass

    # Fallback: re-encoding QSV (robusto)
    cmd2 = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-init_hw_device", "qsv=hw:/dev/dri/renderD128",
        "-filter_hw_device", "hw",
        "-i", lst,
        "-vf", "format=nv12,hwupload=extra_hw_frames=64",
        "-c:v", "h264_qsv", "-preset", "veryfast", "-global_quality", "22",
        dst,
    ]
    try:
        res2 = subprocess.run(cmd2, capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        return False, "concat fallback TIMEOUT (>10 min)"
    if res2.returncode != 0 or not os.path.exists(dst):
        return False, (res2.stderr or "")[-400:]
    actual2 = _video_duration(dst)
    if abs(actual2 - expected) > tolerance:
        return False, f"concat fallback ha prodotto {actual2:.2f}s vs atteso {expected:.2f}s"
    return True, ""


def _load_brain_settings():
    """Legge brain/settings.json. Ritorna {} se non esiste."""
    bp = os.path.join(CFG["BASE_DIR"], "brain", "settings.json")
    if not os.path.exists(bp):
        return {}
    try:
        with open(bp) as f:
            return json.load(f)
    except Exception:
        return {}


def _effective_subtitle_style(profile_style=None):
    """Legge SOLO da brain/settings.json. profile_style ignorato (retro-compat)."""
    settings = _load_brain_settings()
    brain_style = settings.get("subtitle_style") or {}

    merged = dict(brain_style)

    # Defaults hardcoded se il brain non ha ancora una sezione
    merged.setdefault("font_name", "ObelixPro")
    merged.setdefault("font_size", 62)
    merged.setdefault("primary_color_ass", "&H00FFFFFF&")
    merged.setdefault("highlight_color_ass", "&H0000E6FF&")
    merged.setdefault("outline_color_ass", "&H00010101&")
    merged.setdefault("outline_width", 5)
    merged.setdefault("margin_v", 155)
    merged.setdefault("alignment", 2)
    merged.setdefault("bold", 1)
    merged.setdefault("karaoke", True)
    merged.setdefault("karaoke_mode", "word")
    return merged


def _fmt_srt_time(t):
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}".replace(".", ",")


def _write_srt(segments, srt_path):
    """segments = [{'dur': float, 'text': str}, ...]

    Emette sottotitoli WORD-BY-WORD (una cue per parola, uppercase forzato).
    La durata di ogni parola e' proporzionale alla sua lunghezza.
    Se text e' vuoto o placeholder, mantiene una cue unica per la durata intera.
    """
    MIN_WORD_DUR = 0.15   # secondi minimi per parola

    with open(srt_path, "w", encoding="utf-8") as f:
        t = 0.0
        n = 0
        for seg in segments:
            dur = float(seg.get("dur", 0))
            text = (seg.get("text") or "").strip()

            # Placeholder (es. anchor senza testo): una cue "…"
            if not text or text == "…":
                n += 1
                start = t + 0.05
                end = max(start + 0.3, t + dur - 0.05)
                f.write(f"{n}\n{_fmt_srt_time(start)} --> {_fmt_srt_time(end)}\n…\n\n")
                t += dur
                continue

            words = text.upper().split()
            if not words:
                t += dur
                continue

            if len(words) == 1:
                n += 1
                start = t + 0.05
                end = max(start + MIN_WORD_DUR, t + dur - 0.05)
                f.write(f"{n}\n{_fmt_srt_time(start)} --> {_fmt_srt_time(end)}\n{words[0]}\n\n")
                t += dur
                continue

            # Piu' parole: distribuzione proporzionale alla lunghezza
            total_chars = sum(len(w) for w in words) or 1
            cursor = t
            for i, w in enumerate(words):
                frac = len(w) / total_chars
                w_dur = max(MIN_WORD_DUR, dur * frac)
                # L'ultima parola prende tutto il residuo per non sforare
                if i == len(words) - 1:
                    w_dur = max(MIN_WORD_DUR, (t + dur) - cursor)
                start = cursor + 0.02
                end = cursor + w_dur - 0.02
                if end <= start:
                    end = start + MIN_WORD_DUR - 0.04
                n += 1
                f.write(f"{n}\n{_fmt_srt_time(start)} --> {_fmt_srt_time(end)}\n{w}\n\n")
                cursor += w_dur
            t += dur



def _fmt_ass_time(t):
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t % 60
    cs = int(round((s - int(s)) * 100))
    if cs == 100:
        cs = 99
    return f"{h:d}:{m:02d}:{int(s):02d}.{cs:02d}"


def _write_ass(segments, ass_path, style):
    """Karaoke: frase intera visibile + parola corrente evidenziata in giallo.
    Se style['karaoke']=False, mostra frase intera statica (no highlight).
    Formato ASS (ffmpeg/libass lo legge nativamente)."""
    font_name = style.get("font_name", "NotoSansDisplay-Condensed-BoldSim")
    font_clean = (font_name
                  .replace("-Regular", "").replace("-BoldSim", "").replace("-Bold", ""))
    raw_size = int(style.get("font_size", 15))
    # Font size 54: piu' piccolo per far stare 2 righe senza tagli laterali
    font_size = max(54, raw_size) if raw_size < 40 else raw_size
    primary_ass = style.get("primary_color_ass", "&H00FAFCFC&")
    outline_ass = style.get("outline_color_ass", "&H00050506&")
    outline_w = max(4, int(style.get("outline_width", 3)))
    margin_v = int(style.get("margin_v", 155))
    if margin_v < 100:
        margin_v = 155
    alignment = int(style.get("alignment", 2))
    bold = int(style.get("bold", 1))

    # Estrai BBGGRR dall'ASS &HAABBGGRR& rimuovendo H e alpha (2 cifre).
    # primary_ass es "&H00FDFDFD&" -> strip("&")="H00FDFDFD" -> [3:]="00FDFDFD"
    body = primary_ass.strip("&")  # "H00FDFDFD"
    if body.startswith("H"):
        body = body[1:]            # "00FDFDFD"
    if len(body) == 8:
        primary_inline = "&H" + body + "&"        # mantieni AABBGGRR
    elif len(body) == 6:
        primary_inline = "&H00" + body + "&"      # aggiungi alpha opaco
    else:
        primary_inline = "&H00FDFDFD&"            # fallback
    # Highlight colore: legge da settings, fallback giallo TikTok
    highlight_ass = style.get("highlight_color_ass", "&H0000E6FF&")
    hbody = highlight_ass.strip("&")
    if hbody.startswith("H"):
        hbody = hbody[1:]
    if len(hbody) == 8:
        highlight_inline = "&H" + hbody + "&"
    elif len(hbody) == 6:
        highlight_inline = "&H00" + hbody + "&"
    else:
        highlight_inline = "&H0000E6FF&"

    header = f"""[Script Info]
Title: reel-agent
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font_clean},{font_size},{primary_ass},{primary_ass},{outline_ass},&H00000000,{bold},0,0,0,100,100,0,0,1,{outline_w},0,{alignment},20,20,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    MIN_WORD_DUR = 0.15
    events = []
    t = 0.0
    for seg in segments:
        dur = float(seg.get("dur", 0))
        text = (seg.get("text") or "").strip()
        if not text or text == "…":
            events.append(
                f"Dialogue: 0,{_fmt_ass_time(t + 0.02)},{_fmt_ass_time(t + max(0.3, dur - 0.02))},Default,,0,0,0,,…"
            )
            t += dur
            continue
        words = text.upper().split()
        if not words:
            t += dur
            continue
        if len(words) == 1:
            events.append(
                f"Dialogue: 0,{_fmt_ass_time(t + 0.02)},{_fmt_ass_time(t + max(0.15, dur - 0.02))},Default,,0,0,0,,{words[0]}"
            )
            t += dur
            continue
        # Modalità karaoke da style
        karaoke_mode = (style.get("karaoke_mode") or "phrase").lower()

        # --- Modalità "word": una parola alla volta (si sostituisce) ---
        if karaoke_mode == "word":
            total_chars = sum(len(w) for w in words) or 1
            cursor = t
            GAP = 0.02  # gap di sicurezza tra cue consecutive
            for i, wj in enumerate(words):
                w_dur = max(MIN_WORD_DUR, dur * len(wj) / total_chars)
                if i == len(words) - 1:
                    w_dur = max(MIN_WORD_DUR, (t + dur) - cursor)
                start = cursor
                end = cursor + w_dur - GAP
                if end <= start:
                    end = start + MIN_WORD_DUR - GAP
                events.append(
                    f"Dialogue: 0,{_fmt_ass_time(start)},{_fmt_ass_time(end)},Default,,0,0,0,,{wj}"
                )
                cursor += w_dur
            t += dur
            continue

        # --- Modalità "static": frase intera senza highlight ---
        if karaoke_mode == "static":
            start = t + 0.02
            end = t + max(0.3, dur - 0.02)
            plain = " ".join(words)
            if len(plain) > 28 and len(words) >= 4:
                total_chars2 = sum(len(w) for w in words)
                acc = 0
                split_idx = 1
                for k, w in enumerate(words):
                    acc += len(w) + 1
                    if acc >= total_chars2 / 2:
                        split_idx = k + 1
                        break
                line_text = " ".join(words[:split_idx]) + "\\N" + " ".join(words[split_idx:])
            else:
                line_text = plain
            events.append(
                f"Dialogue: 0,{_fmt_ass_time(start)},{_fmt_ass_time(end)},Default,,0,0,0,,{line_text}"
            )
            t += dur
            continue

        # --- Modalità "phrase" (default): frase intera + parola evidenziata ---
        total_chars = sum(len(w) for w in words) or 1
        cursor = t
        for i in range(len(words)):
            w_dur = max(MIN_WORD_DUR, dur * len(words[i]) / total_chars)
            if i == len(words) - 1:
                w_dur = max(MIN_WORD_DUR, (t + dur) - cursor)
            start = cursor
            end = cursor + w_dur
            # frase intera, parola i in highlight, con wrap a 2 righe se lunga
            # costruisci le parti con marker di highlight
            parts = []
            for j, wj in enumerate(words):
                if j == i:
                    parts.append("{\\c" + highlight_inline + "}" + wj
                                 + "{\\c" + primary_inline + "}")
                else:
                    parts.append(wj)
            # Conta caratteri senza tag di markup per decidere il wrap
            plain = " ".join(words)
            if len(plain) > 28 and len(words) >= 4:
                # Wrap a 2 righe bilanciate (\N = line break ASS)
                total_chars = sum(len(w) for w in words)
                acc = 0
                split_idx = 1
                for k, w in enumerate(words):
                    acc += len(w) + 1
                    if acc >= total_chars / 2:
                        split_idx = k + 1
                        break
                # Ricostruisci con \N dopo split_idx
                left, right = [], []
                for j, wj in enumerate(words):
                    if j == i:
                        marked = "{\\c" + highlight_inline + "}" + wj + "{\\c" + primary_inline + "}"
                    else:
                        marked = wj
                    if j < split_idx:
                        left.append(marked)
                    else:
                        right.append(marked)
                line_text = " ".join(left) + "\\N" + " ".join(right)
            else:
                line_text = " ".join(parts)
            events.append(
                f"Dialogue: 0,{_fmt_ass_time(start)},{_fmt_ass_time(end)},Default,,0,0,0,,{line_text}"
            )
            cursor = end
        t += dur

    with open(ass_path, "w", encoding="utf-8") as f:
        f.write(header)
        f.write("\n".join(events))
        f.write("\n")


def _burn_subtitles(video_in, srt_path, style, video_out):
    font_name = style.get("font_name", "BebasNeue-Regular")
    # pulizia nome font: ffmpeg vuole nome "family" senza suffissi comuni
    font_clean = font_name.replace("-Regular", "").replace("-Bold", "").replace("-BoldSim", "")

    # Font size: il profilo storicamente ha valori piccoli (15). Bible dice
    # 325px su 1920 verticale, che in ASS con PlayResY=1920 equivale a ~78.
    # Floor a 60 per garantire leggibilita' TikTok.
    raw_size = int(style.get("font_size", 15))
    font_size = max(60, raw_size) if raw_size < 40 else raw_size

    primary = style.get("primary_color_ass", "&H00FFFFFF&")
    outline = style.get("outline_color_ass", "&H00000000&")
    outline_w = int(style.get("outline_width", 3))
    # Outline: minimo 4 per TikTok (bible: "spesso")
    if outline_w < 4:
        outline_w = 4

    # MarginV in pixel reali (grazie a PlayResY=1920)
    margin_v = int(style.get("margin_v", 155))
    # Profilo storicamente aveva valori tipo 63; bible dice 155
    if margin_v < 100:
        margin_v = 155

    alignment = int(style.get("alignment", 2))
    bold = int(style.get("bold", 1))

    # PlayResX/Y esplicito: senza, ffmpeg assume 384x288 e MarginV=155 sarebbe enorme
    force_style = (
        f"FontName={font_clean},FontSize={font_size},"
        f"PrimaryColour={primary},OutlineColour={outline},"
        f"BorderStyle=1,Outline={outline_w},Shadow=0,"
        f"Alignment={alignment},MarginV={margin_v},Bold={bold},"
        f"PlayResX=1080,PlayResY=1920"
    )
    # escape del path (ASS/SRT)
    sub_esc = srt_path.replace(":", "\\:")
    # ASS: stile embedded, niente force_style. SRT: forza lo stile inline.
    if srt_path.lower().endswith(".ass"):
        vf = f"subtitles='{sub_esc}',format=nv12,hwupload=extra_hw_frames=64"
    else:
        vf = f"subtitles='{sub_esc}':force_style='{force_style}',format=nv12,hwupload=extra_hw_frames=64"

    cmd = [
        "ffmpeg", "-y",
        "-init_hw_device", "qsv=hw:/dev/dri/renderD128",
        "-filter_hw_device", "hw",
        "-i", video_in,
        "-vf", vf,
        "-c:v", "h264_qsv", "-preset", "veryfast", "-global_quality", "22",
        "-an",
        video_out,
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    except subprocess.TimeoutExpired:
        return False, "ffmpeg burn-in TIMEOUT (>10 min)"
    except Exception as e:
        return False, f"ffmpeg burn-in exception: {e}"
    return res.returncode == 0 and os.path.exists(video_out), (res.stderr or "")[-2000:]


def render_final(job_id, profile_id):
    manager.log(job_id, f"Carico profilo #{profile_id}…")
    profile = _load_profile(profile_id)

    edl_data = _load_latest_edl()
    if not edl_data:
        manager.fail(job_id, "Nessun EDL trovato. Esegui 'Genera montaggio' prima.")
        return
    edl, edl_path = edl_data
    timeline = edl.get("timeline", [])
    if not timeline:
        manager.fail(job_id, "EDL senza timeline.")
        return

    # Estrai l'ID del job EDL dal filename (es. job_33_edl.json → 33)
    import re as _re
    m = _re.search(r"job_(\d+)_edl\.json", os.path.basename(edl_path))
    edl_job_id = int(m.group(1)) if m else job_id
    manager.log(job_id, f"EDL: {edl_path} (edl_job_id={edl_job_id})")
    manager.log(job_id, f"Segmenti: {len(timeline)} · durata: {edl.get('total_duration', '?')}s")

    # Directory di lavoro
    work = os.path.join(CFG["TRIMMED_DIR"], f"render_job_{job_id}")
    os.makedirs(work, exist_ok=True)
    renders_dir = CFG["RENDERS_DIR"]
    os.makedirs(renders_dir, exist_ok=True)

    # === Fase 1: taglia + crop ogni segmento ===
    parts = []
    srt_segments = []
    total = len(timeline)
    for i, seg in enumerate(timeline, 1):
        src = seg.get("clip_path")
        in_sec = float(seg.get("in_sec", 0))
        out_sec = float(seg.get("out_sec", 0))
        dur = max(0.1, out_sec - in_sec)
        if not src or not os.path.exists(src):
            manager.log(job_id, f"[{i}/{total}] salto (file mancante): {src}", level="error")
            continue

        dst = os.path.join(work, f"part_{i:03d}.mp4")
        ok, err = _cut_and_crop(src, in_sec, out_sec, dst)
        if ok:
            parts.append(dst)
            srt_segments.append({
                "dur": dur,
                "text": seg.get("subtitle_text", "").strip() or "…",
            })
            manager.log(job_id, f"[{i}/{total}] {seg.get('clip_name', '?')}  {dur:.2f}s  ✓")
        else:
            manager.log(job_id, f"[{i}/{total}] ffmpeg errore: {err}", level="error")

        manager.update_progress(job_id, int(i / total * 60), f"{i}/{total}")

    if not parts:
        manager.fail(job_id, "Nessun segmento renderizzato.")
        return

    # === Aggiungi anchor (primo/ultimo frame) ===
    first_anchor = _get_anchor("first")
    last_anchor = _get_anchor("last")
    anchor_cfg = _read_anchor_config()

    if first_anchor:
        dur_f = float(anchor_cfg["first"].get("duration", 1.0))
        txt_f = (anchor_cfg["first"].get("text") or "").strip()
        anchor_clip = os.path.join(work, "anchor_first.mp4")
        ok_a, err_a = _make_anchor_clip(first_anchor, dur_f, 30, anchor_clip)
        if ok_a:
            parts.insert(0, anchor_clip)
            srt_segments.insert(0, {"dur": dur_f, "text": txt_f})
            manager.log(job_id, f"✓ anchor PRIMO frame ({dur_f}s, testo={'sì' if txt_f else 'no'})")
        else:
            manager.log(job_id, f"⚠ anchor primo fallito: {err_a}", level="error")

    if last_anchor:
        dur_l = float(anchor_cfg["last"].get("duration", 1.0))
        txt_l = (anchor_cfg["last"].get("text") or "").strip()
        anchor_clip = os.path.join(work, "anchor_last.mp4")
        ok_b, err_b = _make_anchor_clip(last_anchor, dur_l, 30, anchor_clip)
        if ok_b:
            parts.append(anchor_clip)
            srt_segments.append({"dur": dur_l, "text": txt_l})
            manager.log(job_id, f"✓ anchor ULTIMO frame ({dur_l}s, testo={'sì' if txt_l else 'no'})")
        else:
            manager.log(job_id, f"⚠ anchor ultimo fallito: {err_b}", level="error")

    # === Fase 2: concat ===
    manager.update_progress(job_id, 65, "concat…")
    merged_temp = os.path.join(work, "merged.mp4")
    ok, err = _concat(parts, merged_temp)
    if not ok:
        manager.fail(job_id, f"concat fallito: {err}")
        return
    manager.log(job_id, f"Concat: {len(parts)} parti unite.")

    # Copia persistente del merge (senza sottotitoli) per re-burn futuri
    merged_persist_dir = os.path.join(CFG["MEDIA_DIR"], "renders", "_merged")
    os.makedirs(merged_persist_dir, exist_ok=True)
    merged = os.path.join(merged_persist_dir, f"merged_{edl_job_id}.mp4")
    import shutil
    shutil.copy2(merged_temp, merged)
    manager.log(job_id, f"Merge persistente: {merged}")

    # === Fase 3: ASS karaoke ===
    manager.update_progress(job_id, 75, "generazione ASS karaoke…")
    style = _effective_subtitle_style()  # solo dal brain
    manager.log(job_id, f"Subtitle style effective: font={style.get('font_name')} size={style.get('font_size')} karaoke={style.get('karaoke')}")
    ass_path = os.path.join(renders_dir, f"render_{edl_job_id}.ass")
    _write_ass(srt_segments, ass_path, style)
    manager.log(job_id, f"ASS karaoke salvato: {ass_path}")
    # manteniamo un SRT "umano" per debug (frase-level)
    srt_path = os.path.join(renders_dir, f"render_{edl_job_id}.srt")
    _write_srt(srt_segments, srt_path)

    # === Fase 4: burn-in ASS karaoke ===
    manager.update_progress(job_id, 82, "burn-in sottotitoli karaoke…")
    manager.log(job_id,
        f"Stile: font={style.get('font_name','?')} size={style.get('font_size')} "
        f"marginV={style.get('margin_v')} (override: size>=60, marginV>=155)")

    final = os.path.join(renders_dir, f"render_{edl_job_id}.mp4")
    ok, err = _burn_subtitles(merged, ass_path, style, final)
    if not ok:
        manager.fail(job_id, f"burn-in fallito: {err}")
        return

    # === Fase 5: salva stato in runs ===
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO runs (profile_id, status, output_path) VALUES (?, 'done', ?)",
            (profile_id, final),
        )

    manager.log(job_id, f"[DEBUG] File finale creato: {final}")

    size_mb = round(os.path.getsize(final) / (1024 * 1024), 1)
    manager.log(job_id, f"✓ Render finale: {final} ({size_mb} MB)")
    manager.log(job_id, f"Durata: {sum(s['dur'] for s in srt_segments):.2f}s")
    manager.log(job_id, f"Voiceover (per registrazione): {len(edl.get('voiceover_script',''))} char")

    # Salva anche il voiceover_script per lo step voce
    vo_path = os.path.join(renders_dir, f"render_{edl_job_id}_voiceover.txt")
    with open(vo_path, "w", encoding="utf-8") as f:
        f.write(edl.get("voiceover_script", ""))
    manager.log(job_id, f"Script voce salvato: {vo_path}")

    manager.update_progress(job_id, 100, "completato")
    manager.finish(job_id, {
        "output": final,
        "srt": srt_path,
        "voiceover_script_path": vo_path,
        "size_mb": size_mb,
        "n_parts": len(parts),
        "duration": round(sum(s['dur'] for s in srt_segments), 2),
    })

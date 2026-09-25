"""Style learning: analizza i video di riferimento e produce un profilo di stile."""
import os
import json
import math
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from skimage.metrics import structural_similarity as ssim

from app.config import CFG
from app.jobs import manager
from app.db import get_conn


# ============================================================
# PHASE A — Scene detection
# ============================================================
def _probe_duration(path):
    """Ritorna la durata in secondi del video."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=10,
        )
        return float((r.stdout or "0").strip())
    except Exception:
        return 0.0


def _extract_last_frame(video_path: str, out_path: str) -> bool:
    """Estrae l'ultimo frame 'utile' (a -0.5s dalla fine) per analizzare la chiusura."""
    try:
        dur = _probe_duration(video_path)
        ts = max(0, dur - 0.5)
        r = subprocess.run(
            ["ffmpeg", "-y", "-ss", f"{ts:.3f}", "-i", video_path,
             "-frames:v", "1", "-q:v", "4", "-vf", "scale=480:-1", out_path],
            capture_output=True, text=True, timeout=15,
        )
        return r.returncode == 0 and os.path.exists(out_path)
    except Exception:
        return False


def detect_scenes(video_path: str, threshold: float = 27.0) -> list[tuple[float, float]]:
    """Rileva i cambi scena, ritorna [(start_sec, end_sec), ...]."""
    from scenedetect import open_video, SceneManager
    from scenedetect.detectors import ContentDetector

    video = open_video(video_path)
    sm = SceneManager()
    sm.add_detector(ContentDetector(threshold=threshold))
    sm.detect_scenes(video, show_progress=False)
    scene_list = sm.get_scene_list()

    # Se non trova scene, considera tutto il video un'unica scena
    if not scene_list:
        cap = cv2.VideoCapture(video_path)
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        n = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
        cap.release()
        dur = n / fps if fps else 0.0
        return [(0.0, dur)] if dur > 0 else []

    return [(s.get_seconds(), e.get_seconds()) for s, e in scene_list]


# ============================================================
# PHASE B — Frame extraction
# ============================================================
def extract_key_frames(video_path: str, scenes: list[tuple[float, float]],
                       out_dir: str, max_frames: int = 30) -> list[str]:
    """Estrae un frame dal centro di ogni scena, salvandoli come JPG."""
    os.makedirs(out_dir, exist_ok=True)
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return []

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    paths = []

    # Se le scene sono più di max_frames, sottocampiona uniformemente
    step = max(1, len(scenes) // max_frames)
    selected = scenes[::step][:max_frames]

    for idx, (start, end) in enumerate(selected):
        mid = (start + end) / 2.0
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(mid * fps))
        ok, frame = cap.read()
        if not ok or frame is None:
            continue
        p = os.path.join(out_dir, f"frame_{idx:03d}.jpg")
        cv2.imwrite(p, frame)
        paths.append(p)
    cap.release()
    return paths


# ============================================================
# PHASE C — Whisper transcription
# ============================================================
def transcribe_video(video_path: str) -> str:
    from faster_whisper import WhisperModel
    model = WhisperModel(
        CFG["WHISPER_MODEL"],
        device="cpu",
        compute_type=CFG["WHISPER_COMPUTE"],
        download_root=CFG["MODELS_DIR"],
    )
    segments, _ = model.transcribe(video_path, language="it", beam_size=1)
    return " ".join(s.text.strip() for s in segments).strip()


# ============================================================
# PHASE D — Subtitle style extraction (OCR + clustering + colori)
# ============================================================
_OCR_ENGINE = None

def _get_ocr():
    global _OCR_ENGINE
    if _OCR_ENGINE is None:
        from rapidocr_onnxruntime import RapidOCR
        _OCR_ENGINE = RapidOCR()
    return _OCR_ENGINE


def _run_ocr(img_path: str) -> list[dict]:
    """Ritorna [{box, text, conf}] per ogni testo rilevato nel frame."""
    engine = _get_ocr()
    try:
        result, _ = engine(img_path)
    except Exception:
        return []
    if not result:
        return []
    out = []
    for item in result:
        box, text, conf = item[0], item[1], item[2]
        if not text or len(text.strip()) < 2:
            continue
        # Filtro qualità: confidenza minima + testo "vero"
        if conf is not None and float(conf) < 0.55:
            continue
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        out.append({
            "x0": int(min(xs)), "y0": int(min(ys)),
            "x1": int(max(xs)), "y1": int(max(ys)),
            "text": text.strip(),
            "conf": float(conf) if conf is not None else 0.0,
        })
    return out


def _sample_text_colors(frame: np.ndarray, box: dict) -> dict:
    """Stima colore primario (riempimento) e outline del testo in un box."""
    x0, y0, x1, y1 = box["x0"], box["y0"], box["x1"], box["y1"]
    pad = 2
    crop = frame[max(0, y0 - pad):y1 + pad, max(0, x0 - pad):x1 + pad]
    if crop.size == 0:
        return {"primary": [255, 255, 255], "outline": [0, 0, 0]}

    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    # Maschera testo = pixel più chiari del 75mo percentile
    thr = int(np.percentile(gray, 75))
    text_mask = gray >= thr
    if text_mask.sum() < 5:
        return {"primary": [255, 255, 255], "outline": [0, 0, 0]}

    text_pixels = crop[text_mask]
    primary = np.median(text_pixels, axis=0).astype(int).tolist()

    # Outline = pixel scuri attorno al testo (percentile 25)
    thr_low = int(np.percentile(gray, 25))
    outline_mask = (gray <= thr_low) & (gray < thr)
    if outline_mask.sum() >= 5:
        outline = np.median(crop[outline_mask], axis=0).astype(int).tolist()
    else:
        outline = [0, 0, 0]

    # BGR -> RGB
    return {
        "primary": [int(primary[2]), int(primary[1]), int(primary[0])],
        "outline": [int(outline[2]), int(outline[1]), int(outline[0])],
    }


def _bgr_to_ass_color(rgb: list[int]) -> str:
    """Converte RGB [0-255] in formato ASS &HAABBGGRR (AA=00 = opaco)."""
    r, g, b = rgb
    return f"&H00{b:02X}{g:02X}{r:02X}&"


def extract_subtitle_style(frames: list[str]) -> dict:
    """
    Analizza i frame per ricavare:
    - cluster di testo (probabili sottotitoli)
    - posizione media (frazione dal basso)
    - altezza media del testo (frazione dell'altezza frame)
    - colori primario + outline
    """
    if not frames:
        return {}

    clusters = []  # lista di dict {y_center_rel, height_rel, texts, boxes, frame_idx}
    frame_h = None
    frame_w = None

    for fi, fp in enumerate(frames):
        img = cv2.imread(fp)
        if img is None:
            continue
        h, w = img.shape[:2]
        frame_h, frame_w = h, w
        boxes = _run_ocr(fp)

        # Considera solo testo nella parte bassa (60-95% dell'immagine)
        for b in boxes:
            y_rel = (b["y0"] + b["y1"]) / 2.0 / h
            if not (0.55 <= y_rel <= 0.95):
                continue
            # Ignora box troppo piccoli (rumore)
            if (b["y1"] - b["y0"]) < h * 0.02:
                continue

            matched = None
            for c in clusters:
                if abs(c["y_center_rel"] - y_rel) < 0.04:
                    matched = c
                    break
            if matched is None:
                matched = {"y_center_rel": y_rel, "samples": []}
                clusters.append(matched)
            matched["samples"].append({
                "y0": b["y0"], "y1": b["y1"], "y_center_rel": y_rel,
                "height_rel": (b["y1"] - b["y0"]) / h,
                "x0": b["x0"], "x1": b["x1"],
                "text": b["text"], "box": b, "frame": img, "fi": fi,
            })

    if not clusters:
        return {}

    # Scegli il cluster con più occorrenze
    main = max(clusters, key=lambda c: len(c["samples"]))
    samples = main["samples"]

    # Medie posizione + dimensione
    avg_y_center_rel = float(np.mean([s["y_center_rel"] for s in samples]))
    avg_height_rel = float(np.mean([s["height_rel"] for s in samples]))

    # Colori: media sui colori stimati in ogni sample
    primaries, outlines = [], []
    for s in samples[:10]:
        c = _sample_text_colors(s["frame"], s["box"])
        primaries.append(c["primary"])
        outlines.append(c["outline"])
    primary = np.median(np.array(primaries), axis=0).astype(int).tolist() if primaries else [255, 255, 255]
    outline = np.median(np.array(outlines), axis=0).astype(int).tolist() if outlines else [0, 0, 0]

    # Converti in unità ASS (PlayResY = 288 default FFmpeg per SRT)
    font_size_ass = max(12, int(avg_height_rel * 288 * 1.05))
    margin_v_ass = max(20, int((1.0 - avg_y_center_rel) * 288))

    # Salva anche i crop per il font matching
    crops = []
    for s in samples[:12]:
        x0, y0, x1, y1 = s["box"]["x0"], s["box"]["y0"], s["box"]["x1"], s["box"]["y1"]
        crop = s["frame"][y0:y1, x0:x1]
        if crop.size > 0:
            crops.append({"crop": crop, "text": s["text"]})

    return {
        "alignment": 2,  # bottom center
        "font_size": font_size_ass,
        "margin_v": margin_v_ass,
        "primary_color_rgb": primary,
        "outline_color_rgb": outline,
        "primary_color_ass": _bgr_to_ass_color(primary),
        "outline_color_ass": _bgr_to_ass_color(outline),
        "avg_height_rel": avg_height_rel,
        "avg_y_center_rel": avg_y_center_rel,
        "num_subtitle_samples": len(samples),
        "sample_texts": [s["text"] for s in samples[:8]],
        "_crops": crops,  # uso interno per font matching, poi rimosso
    }

# ============================================================
# PHASE E — Font matching (robusto)
# ============================================================
import re as _re
import unicodedata as _unicodedata

def _clean_text_for_font(text: str) -> str:
    """Toglie emoji, punteggiatura e caratteri non alfabetici; uppercase."""
    # Rimuovi emoji e simboli
    text = "".join(c for c in text if not _unicodedata.category(c).startswith("So"))
    # Solo lettere + spazio
    text = _re.sub(r"[^A-Za-zÀ-ÿ\s]", " ", text)
    text = _re.sub(r"\s+", " ", text).strip()
    return text.upper()


def list_candidate_fonts() -> list[dict]:
    """Solo font latini sans-serif, niente script CJK/Lao/Thai/etc."""
    blacklist = (
        "Lao", "Thai", "Arabic", "Hebrew", "Devanagari", "Bengali", "Tamil",
        "Telugu", "Kannada", "Malayalam", "Gujarati", "Gurmukhi", "Oriya",
        "Sinhala", "Khmer", "Myanmar", "Ethiopic", "Cherokee", "Mongolian",
        "CJK", "JP", "KR", "SC", "TC", "HK",
        "Medefaidrin", "OldSouthArabian", "OldNorthArabian", "OldTurkic",
        "OldHungarian", "OldPersian", "OldItalic", "Phoenician", "Ugaritic",
        "Avestan", "Brahmi", "Bhaiksuki", "Carian", "Coptic", "Cuneiform",
        "Deseret", "Duployan", "Egyptian", "Elbasan", "Glagolitic", "Gothic",
        "Grantha", "Hanunoo", "Hatran", "ImperialAramaic", "Inscriptional",
        "Javanese", "Kaithi", "Kayah", "Kharoshthi", "Khojki", "Khudawadi",
        "Lepcha", "Limbu", "LinearA", "LinearB", "Lisu", "Lycian", "Lydian",
        "Mahajani", "Mandaic", "Manichaean", "Marchen", "Masaram", "Meetei",
        "Mende", "Meroitic", "Miao", "Modi", "Mro", "Multani", "Nabataean",
        "Newa", "Nko", "Nushu", "Ogham", "OlChiki", "OldPermic", "Osage",
        "Osmanya", "Pahawh", "Palmyrene", "PauCinHau", "PhagsPa", "Rejang",
        "Runic", "Samaritan", "Saurashtra", "Sharada", "Shavian", "Siddham",
        "SignWriting", "Sora", "Soyombo", "Sundanese", "Syloti", "Syriac",
        "Tagalog", "Tagbanwa", "Tai", "Takri", "Tibetan", "Tifinagh",
        "Tirhuta", "Vai", "Wancho", "Warang", "Yezidi", "Zanabazar",
        "Mono", "Serif", "Italic", "Oblique",
        "Light", "Thin", "ExtraLight", "SemiLight",
    )
    candidates = []
    seen = set()
    for d in ["/usr/local/share/fonts/google", "/usr/share/fonts/truetype"]:
        if not os.path.isdir(d):
            continue
        for root, _, files in os.walk(d):
            for f in files:
                if not f.lower().endswith((".ttf", ".otf")):
                    continue
                name = os.path.splitext(f)[0]
                if name in seen:
                    continue
                if any(b in name for b in blacklist):
                    continue
                seen.add(name)
                candidates.append({"name": name, "path": os.path.join(root, f)})
    return candidates


def _extract_text_mask(crop_bgr: np.ndarray) -> tuple[np.ndarray, float] | None:
    """
    Estrae una maschera binaria pulita del testo.
    Ritorna (mask, aspect_ratio) o None se il crop è inutilizzabile.
    """
    if crop_bgr is None or crop_bgr.size == 0:
        return None
    h, w = crop_bgr.shape[:2]
    if h < 8 or w < 20:
        return None

    gray = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2GRAY)

    # Otsu: assume testo più chiaro dello sfondo (tipico TikTok)
    _, mask = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    if mask.mean() > 127:
        mask = 255 - mask  # testo bianco su nero

    # Rimuovi componenti connesse piccole (bordi, emoji residue)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    if n <= 1:
        return None
    areas = stats[1:, cv2.CC_STAT_AREA]
    if len(areas) == 0:
        return None
    max_area = int(areas.max())
    if max_area < 30:
        return None

    keep = np.zeros_like(mask)
    for i in range(1, n):
        if stats[i, cv2.CC_STAT_AREA] >= max_area * 0.10:
            keep[labels == i] = 255

    # Ritaglio stretto sul bbox del testo
    ys, xs = np.where(keep > 0)
    if len(ys) < 10 or len(xs) < 10:
        return None
    y0, y1 = ys.min(), ys.max() + 1
    x0, x1 = xs.min(), xs.max() + 1
    tight = keep[y0:y1, x0:x1]

    if tight.shape[0] < 6 or tight.shape[1] < 12:
        return None

    aspect_ratio = tight.shape[1] / max(1, tight.shape[0])
    return tight, aspect_ratio


def _render_text_mask(text: str, font_path: str, bold_sim: bool = False):
    """Renderizza il testo col font dato. Se bold_sim=True, ispessisce i tratti."""
    if not text:
        return None
    try:
        base_size = 200
        font = ImageFont.truetype(font_path, base_size)
        dummy = Image.new("L", (10, 10), 0)
        d = ImageDraw.Draw(dummy)
        bbox = d.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        if tw <= 0 or th <= 0:
            return None

        pad = 20
        img = Image.new("L", (tw + pad * 2, th + pad * 2), 0)
        d2 = ImageDraw.Draw(img)
        d2.text((pad - bbox[0], pad - bbox[1]), text, fill=255, font=font)

        if bold_sim:
            # Dilatazione morfologica per simulare bold (kernel 3x3, 1 iter)
            arr = np.array(img)
            kernel = np.ones((3, 3), np.uint8)
            arr = cv2.dilate(arr, kernel, iterations=1)
            img = Image.fromarray(arr)

        arr = np.array(img)
        _, binary = cv2.threshold(arr, 128, 255, cv2.THRESH_BINARY)

        ys, xs = np.where(binary > 0)
        if len(ys) < 10:
            return None
        tight = binary[ys.min():ys.max()+1, xs.min():xs.max()+1]
        if tight.shape[0] < 6 or tight.shape[1] < 12:
            return None

        ar = tight.shape[1] / max(1, tight.shape[0])
        return tight, ar
    except Exception:
        return None

        ar = tight.shape[1] / max(1, tight.shape[0])
        return tight, ar
    except Exception:
        return None


def _normalize_mask(mask: np.ndarray, canvas=(240, 64)) -> np.ndarray:
    """Ridimensiona preservando aspect ratio, padda su canvas nero, applica Canny."""
    ch, cw = canvas
    h, w = mask.shape
    scale = min(cw / w, ch / h)
    nw = max(1, int(w * scale))
    nh = max(1, int(h * scale))
    resized = cv2.resize(mask, (nw, nh), interpolation=cv2.INTER_AREA)
    canvas_img = np.zeros((ch, cw), dtype=np.uint8)
    y = (ch - nh) // 2
    x = (cw - nw) // 2
    canvas_img[y:y+nh, x:x+nw] = resized
    return cv2.Canny(canvas_img, 50, 150)


def _ink_density(mask: np.ndarray) -> float:
    """Frazione di pixel 'accesi'."""
    return float((mask > 127).sum()) / mask.size


def match_font(crops: list[dict], max_candidates: int = 200) -> dict:
    """
    crops: lista di {"crop": ndarray BGR, "text": str}
    Ritorna {"best": {...}, "top3": [...], "evaluated": int}
    """
    # 1) Filtra crop di qualità
    samples = []
    for c in crops:
        text = _clean_text_for_font(c.get("text", ""))
        if len(text) < 4 or len(text) > 40:
            continue
        # Scarta testi con troppe cifre
        if sum(ch.isdigit() for ch in text) > len(text) * 0.3:
            continue
        extracted = _extract_text_mask(c["crop"])
        if extracted is None:
            continue
        mask, ar = extracted
        samples.append({"mask": mask, "ar": ar, "text": text})

    if not samples:
        return {"error": "nessun crop utilizzabile", "evaluated": 0}

    # Deduplica per testo (max 6 samples distinti)
    seen, uniq = set(), []
    for s in samples:
        key = s["text"][:20]
        if key in seen:
            continue
        seen.add(key)
        uniq.append(s)
        if len(uniq) >= 6:
            break
    samples = uniq

    # 2) Confronta con ogni font
    # 2) Confronta con ogni font, in 2 varianti (normale + bold simulato)
    candidates = list_candidate_fonts()[:max_candidates]
    scores = []

    for font in candidates:
      for bold_sim in (False, True):
        ssims, ar_scores, ink_scores = [], [], []
        for s in samples:
            rendered = _render_text_mask(s["text"], font["path"], bold_sim=bold_sim)
            if rendered is None:
                continue
            r_mask, r_ar = rendered
            e_target = _normalize_mask(s["mask"])
            e_rendered = _normalize_mask(r_mask)
            try:
                ssim_val = ssim(e_target, e_rendered, data_range=255)
            except Exception:
                continue
            ssims.append(ssim_val)

            ar_min, ar_max = min(s["ar"], r_ar), max(s["ar"], r_ar)
            ar_scores.append(ar_min / ar_max if ar_max > 0 else 0)

            d_target = _ink_density(s["mask"])
            d_rendered = _ink_density(r_mask)
            d_min, d_max = min(d_target, d_rendered), max(d_target, d_rendered)
            ink_scores.append(d_min / d_max if d_max > 0 else 0)

        if not ssims:
            continue

        avg_ssim = float(np.mean(ssims))
        avg_ar = float(np.mean(ar_scores)) if ar_scores else 0.0
        avg_ink = float(np.mean(ink_scores)) if ink_scores else 0.0
        combined = 0.70 * ((avg_ssim + 1) / 2) + 0.20 * avg_ar + 0.10 * avg_ink

        label = font["name"] + ("-BoldSim" if bold_sim else "")
        scores.append({
            "name": label,
            "path": font["path"],
            "score": round(combined, 4),
            "ssim": round(avg_ssim, 4),
            "ar": round(avg_ar, 4),
            "ink": round(avg_ink, 4),
            "bold_sim": bold_sim,
        })
        ssims, ar_scores, ink_scores = [], [], []
        for s in samples:
            rendered = _render_text_mask(s["text"], font["path"])
            if rendered is None:
                continue
            r_mask, r_ar = rendered

            # SSIM su edge maps normalizzate
            e_target = _normalize_mask(s["mask"])
            e_rendered = _normalize_mask(r_mask)
            try:
                ssim_val = ssim(e_target, e_rendered, data_range=255)
            except Exception:
                continue
            ssims.append(ssim_val)

            # Aspect ratio similarity (0..1)
            ar_min, ar_max = min(s["ar"], r_ar), max(s["ar"], r_ar)
            ar_scores.append(ar_min / ar_max if ar_max > 0 else 0)

            # Ink density similarity
            d_target = _ink_density(s["mask"])
            d_rendered = _ink_density(r_mask)
            d_min, d_max = min(d_target, d_rendered), max(d_target, d_rendered)
            ink_scores.append(d_min / d_max if d_max > 0 else 0)

        if not ssims:
            continue

        avg_ssim = float(np.mean(ssims))
        avg_ar = float(np.mean(ar_scores)) if ar_scores else 0.0
        avg_ink = float(np.mean(ink_scores)) if ink_scores else 0.0

        # Score composito pesato
        combined = 0.70 * ((avg_ssim + 1) / 2) + 0.20 * avg_ar + 0.10 * avg_ink

        scores.append({
            "name": font["name"],
            "path": font["path"],
            "score": round(combined, 4),
            "ssim": round(avg_ssim, 4),
            "ar": round(avg_ar, 4),
            "ink": round(avg_ink, 4),
        })

    scores.sort(key=lambda x: x["score"], reverse=True)

    # Dedup per nome (tieni il best per ogni font)
    seen_names = set()
    deduped = []
    for s in scores:
        base = s["name"].replace("-BoldSim", "")
        if base in seen_names:
            continue
        seen_names.add(base)
        deduped.append(s)

    if not deduped:
        return {"error": "nessun font valutabile", "evaluated": 0}

    return {
        "best": deduped[0],
        "top3": deduped[:3],
        "top20": deduped[:20],
        "evaluated": len(scores),
        "samples_used": len(samples),
        "sample_texts": [s["text"] for s in samples],
    }

# ============================================================
# PHASE F — Claude narrative analysis
# ============================================================
def _classify_transition(video_path, t_boundary, fps=30):
    """Classifica la transizione tra due scene (cut/dissolve/fade/none)."""
    try:
        import cv2
        import numpy as np
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return "none"
        # Frame a -6 / -3 / +3 / +6 rispetto al boundary
        ts = [t_boundary - 0.2, t_boundary - 0.1, t_boundary + 0.1, t_boundary + 0.2]
        frames = []
        for t in ts:
            if t < 0: continue
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(t * fps))
            ok, fr = cap.read()
            if ok and fr is not None:
                frames.append(cv2.resize(fr, (160, 90)))
        cap.release()
        if len(frames) < 3:
            return "none"
        # Diff medie consecutive
        diffs = []
        for i in range(len(frames) - 1):
            g1 = cv2.cvtColor(frames[i], cv2.COLOR_BGR2GRAY)
            g2 = cv2.cvtColor(frames[i+1], cv2.COLOR_BGR2GRAY)
            diffs.append(float(np.mean(cv2.absdiff(g1, g2))))
        max_d = max(diffs)
        avg_d = sum(diffs) / len(diffs)
        # Black frame detection (fade)
        last_mean = float(np.mean(cv2.cvtColor(frames[-1], cv2.COLOR_BGR2GRAY)))
        first_mean = float(np.mean(cv2.cvtColor(frames[0], cv2.COLOR_BGR2GRAY)))
        if last_mean < 15 or first_mean < 15:
            return "fade"
        if max_d > 60:
            return "cut"
        if avg_d > 25:
            return "dissolve"
        return "none"
    except Exception:
        return "none"


def _classify_camera_motion(video_path, t_start, t_end):
    """Stima il movimento camera nella scena (static/pan/zoom/handheld)."""
    try:
        import cv2
        import numpy as np
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return "unknown"
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        # Estrai 5 frame distribuiti
        n = 5
        frames = []
        for i in range(n):
            t = t_start + (t_end - t_start) * (i / max(1, n - 1))
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(t * fps))
            ok, fr = cap.read()
            if ok and fr is not None:
                gray = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY)
                gray = cv2.resize(gray, (160, 90))
                frames.append(gray)
        cap.release()
        if len(frames) < 3:
            return "unknown"

        # Optical flow (Farnebäck) tra frame consecutivi
        avg_dx, avg_dy, avg_mag = [], [], []
        for i in range(len(frames) - 1):
            flow = cv2.calcOpticalFlowFarneback(
                frames[i], frames[i+1], None,
                0.5, 3, 15, 3, 5, 1.2, 0,
            )
            dx = np.median(flow[..., 0])
            dy = np.median(flow[..., 1])
            mag = np.mean(np.sqrt(flow[..., 0]**2 + flow[..., 1]**2))
            avg_dx.append(dx); avg_dy.append(dy); avg_mag.append(mag)

        m_mag = float(np.mean(avg_mag))
        # Varianza del flusso = instabilità = handheld
        mag_var = float(np.var(avg_mag))

        # Zoom detection: divergenza del flusso (approssimata con la media dei dx/dy)
        # Semplificazione: se mag alta ma dx/dy medi bassi → zoom o shake
        if m_mag < 0.3:
            return "static"
        if m_mag > 6 and mag_var > 3:
            return "handheld"
        # Pan: dx o dy dominante e coerente
        m_dx = float(np.mean(avg_dx))
        m_dy = float(np.mean(avg_dy))
        if abs(m_dx) > abs(m_dy) * 1.5 and abs(m_dx) > 1.0:
            return "pan"
        if abs(m_dy) > abs(m_dx) * 1.5 and abs(m_dy) > 1.0:
            return "tilt"
        if m_mag > 1.0 and mag_var < 2:
            return "zoom"
        return "unknown"
    except Exception:
        return "unknown"


def _classify_composition(video_path, t_mid):
    """Classifica composizione: face/hands/detail/wide. Non si blocca se cascade manca."""
    try:
        import cv2
        import numpy as np

        # Path cascade assoluto (venv non li include)
        CASCADE_DIR = "/opt/reel-agent/models/cascades"
        FRONT = os.path.join(CASCADE_DIR, "haarcascade_frontalface_default.xml")
        PROFILE = os.path.join(CASCADE_DIR, "haarcascade_profileface.xml")

        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return "unknown"
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(t_mid * fps))
        ok, fr = cap.read()
        cap.release()
        if not ok or fr is None:
            return "unknown"

        h, w = fr.shape[:2]
        gray = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY)
        total_area = w * h

        # --- 1. Face detection (frontale + profilo), solo se cascade esiste ---
        faces = []
        if os.path.exists(FRONT):
            cascade = cv2.CascadeClassifier(FRONT)
            if not cascade.empty():
                f1 = cascade.detectMultiScale(gray, scaleFactor=1.15, minNeighbors=5,
                                              minSize=(50, 50), maxSize=(int(w*0.9), int(h*0.9)))
                faces.extend(f1)
        if os.path.exists(PROFILE):
            cascade_p = cv2.CascadeClassifier(PROFILE)
            if not cascade_p.empty():
                f2 = cascade_p.detectMultiScale(gray, scaleFactor=1.15, minNeighbors=5,
                                                minSize=(50, 50), maxSize=(int(w*0.9), int(h*0.9)))
                faces.extend(f2)

        if len(faces) > 0:
            max_face = max(faces, key=lambda f: f[2] * f[3])
            face_ratio = (max_face[2] * max_face[3]) / total_area
            if face_ratio > 0.15:
                return "close_up_face"
            elif face_ratio > 0.05:
                return "medium_face"
            else:
                return "wide_face"

        # --- 2. Skin detection (mani/braccia) ---
        hsv = cv2.cvtColor(fr, cv2.COLOR_BGR2HSV)
        skin1 = cv2.inRange(hsv, np.array([0, 20, 70]), np.array([20, 255, 255]))
        skin2 = cv2.inRange(hsv, np.array([160, 20, 70]), np.array([180, 255, 255]))
        skin = cv2.bitwise_or(skin1, skin2)
        # Morfologia per eliminare rumore
        kernel = np.ones((5, 5), np.uint8)
        skin = cv2.morphologyEx(skin, cv2.MORPH_OPEN, kernel)
        skin_ratio = np.sum(skin > 0) / total_area
        if skin_ratio > 0.15:
            return "hands"

        # --- 3. Edge density (dettaglio cibo) ---
        edges = cv2.Canny(gray, 50, 150)
        edge_density = np.sum(edges > 0) / total_area
        if edge_density > 0.12:
            return "detail"

        # --- 4. Luminosità media (ambiance scuro) ---
        if gray.mean() < 60:
            return "ambiance"

        return "wide"
    except Exception:
        return "unknown"


        h, w = fr.shape[:2]
        # Face detection veloce (Haar cascade integrato in OpenCV)
        cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        if not os.path.exists(cascade_path):
            return "unknown"
        face_cascade = cv2.CascadeClassifier(cascade_path)
        gray = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY)
        faces = face_cascade.detectMultiScale(gray, scaleFactor=1.2, minNeighbors=4, minSize=(60, 60))

        if len(faces) > 0:
            # Area volto / area frame
            max_face = max(faces, key=lambda f: f[2]*f[3])
            face_ratio = (max_face[2] * max_face[3]) / (w * h)
            if face_ratio > 0.15:
                return "close_up_face"
            elif face_ratio > 0.05:
                return "medium_face"
            else:
                return "wide_face"

        # Skin detection per mani/braccia
        hsv = cv2.cvtColor(fr, cv2.COLOR_BGR2HSV)
        skin1 = cv2.inRange(hsv, np.array([0, 20, 70]), np.array([20, 255, 255]))
        skin2 = cv2.inRange(hsv, np.array([160, 20, 70]), np.array([180, 255, 255]))
        skin = cv2.bitwise_or(skin1, skin2)
        skin_ratio = np.sum(skin > 0) / (w * h)
        if skin_ratio > 0.10:
            return "hands"
        # Altrimenti dettaglio/wide
        # Usiamo anche edge density: molto dettaglio = closeup food
        edges = cv2.Canny(gray, 50, 150)
        edge_density = np.sum(edges > 0) / (w * h)
        if edge_density > 0.15:
            return "detail"
        return "wide"
    except Exception:
        return "unknown"


def analyze_closing_pattern(last_frames: list[str]) -> dict:
    """Analizza gli ultimi frame dei video di riferimento per capire il pattern di chiusura."""
    if not last_frames:
        return {"pattern": "sconosciuto", "description": "nessun frame analizzabile"}

    from anthropic import Anthropic
    import base64
    client = Anthropic(api_key=CFG["ANTHROPIC_API_KEY"])

    content = [{"type": "text", "text": (
        "Ti mostro gli ULTIMI frame di " + str(len(last_frames)) +
        " video TikTok di riferimento dello stesso creator.\n\n"
        "Analizza il PATTERN DI CHIUSURA comune e rispondi ESCLUSIVAMENTE con JSON:\n"
        "{\n"
        '  "pattern": "piatto_finale|reazione_finale|logo_locale|cta_visiva|dettaglio_soddisfacente|altro",\n'
        '  "description": "descrizione in 1-2 frasi di come chiudono tipicamente i video",\n'
        '  "last_shot_type": "primo piano|mezzo busto|dettaglio|totale|scritta",\n'
        '  "voiceover_ending": "come finisce tipicamente il testo/voce (es. CTA netta, ringraziamento, invito)"\n'
        "}\n"
    )}]

    for fp in last_frames[:8]:
        if os.path.exists(fp):
            with open(fp, "rb") as fh:
                b64 = base64.b64encode(fh.read()).decode()
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": "image/jpeg", "data": b64},
            })

    try:
        resp = client.messages.create(
            model=CFG["CLAUDE_MODEL_SMART"],
            max_tokens=600,
            messages=[{"role": "user", "content": content}],
        )
        raw = resp.content[0].text.strip()
        import re as _re
        if "```" in raw:
            raw = _re.sub(r"```json\s*", "", raw)
            raw = _re.sub(r"```\s*", "", raw)
        m = _re.search(r"\{.*\}", raw, _re.DOTALL)
        if m:
            return json.loads(m.group(0))
    except Exception as e:
        return {"error": str(e)}
    return {"error": "no JSON"}


def analyze_with_claude(transcripts: list[str], subtitle_style: dict,
                        avg_shot_duration: float) -> dict:
    from anthropic import Anthropic

    client = Anthropic(api_key=CFG["ANTHROPIC_API_KEY"])

    joined = "\n\n--- VIDEO ---\n".join(transcripts[:10]) or "[nessun parlato rilevato]"

    prompt = f"""Analizza i seguenti trascritti di video TikTok/Reels di riferimento dello stesso creator.

Dati tecnici rilevati automaticamente:
- Durata media shot: {avg_shot_duration:.2f} secondi
- Sottotitoli rilevati in posizione {"bassa" if subtitle_style.get("alignment") == 2 else "media"}, dimensione {subtitle_style.get("font_size", "?")} (ASS), {subtitle_style.get("num_subtitle_samples", 0)} occorrenze

TRASCRITTI:
{joined}

Estrai i pattern comuni di storytelling. Rispondi ESCLUSIVAMENTE con JSON valido:
{{
  "hook_style": "pattern nei primi 2-3 secondi",
  "narrative_structure": "struttura ricorrente (es. Hook -> Problema -> Soluzione -> CTA)",
  "tone_of_voice": "registro (es. incalzante, informale, divulgativo)",
  "pacing_style": "descrizione del ritmo (allineata alla durata media shot rilevata)",
  "shot_sequence": "sequenza tipica delle inquadrature",
  "call_to_action_style": "modalità di chiusura",
  "language": "it"
}}"""

    resp = client.messages.create(
        model=CFG["CLAUDE_MODEL_SMART"],
        max_tokens=CFG["CLAUDE_MAX_TOKENS"],
        messages=[{"role": "user", "content": prompt}],
    )
    raw = resp.content[0].text.strip()

    # Parsing robusto
    import re
    if "```" in raw:
        raw = re.sub(r"```json\s*", "", raw)
        raw = re.sub(r"```\s*", "", raw)
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        return {"error": "Claude non ha restituito JSON valido.", "raw": raw[:500]}
    try:
        return json.loads(m.group(0))
    except Exception as e:
        return {"error": f"JSON parse: {e}", "raw": raw[:500]}


# ============================================================
# ORCHESTRATORE — run_style_learn
# ============================================================
def run_style_learn(job_id: int, profile_name: str, tiktok_account: str,
                    video_paths: list[str]):
    """Orchestratore del job di style learning."""
    total_videos = len(video_paths)
    if total_videos == 0:
        manager.fail(job_id, "Nessun video di riferimento.")
        return

    work_dir = os.path.join(CFG["FRAMES_DIR"], f"job_{job_id}")
    os.makedirs(work_dir, exist_ok=True)

    # ---- A + B: scene detection + frame extraction ----
    manager.update_progress(job_id, 2, "scene detection…")
    manager.log(job_id, f"Analisi {total_videos} video di riferimento.")

    all_scenes = []
    all_frames = []
    for i, vp in enumerate(video_paths, 1):
        try:
            scenes = detect_scenes(vp)
            all_scenes.append(scenes)
            n = len(scenes)
            manager.log(job_id, f"[{i}/{total_videos}] {os.path.basename(vp)} → {n} scene")

            vfdir = os.path.join(work_dir, f"v{i}")
            frames = extract_key_frames(vp, scenes, vfdir, max_frames=20)
            all_frames.extend(frames)
            manager.log(job_id, f"   estratti {len(frames)} frame chiave")
        except Exception as e:
            manager.log(job_id, f"[{i}/{total_videos}] errore scene detection: {e}", level="error")
            all_scenes.append([])

        manager.update_progress(job_id, 2 + int(20 * i / total_videos), f"scene {i}/{total_videos}")

    # ---- A2: Analisi tecnica per ogni scena ----
    manager.update_progress(job_id, 22, "analisi tecnica scene…")
    all_transitions = []
    all_motions = []
    all_compositions = []
    for i, vp in enumerate(video_paths, 1):
        scenes = all_scenes[i-1] if i-1 < len(all_scenes) else []
        for s_start, s_end in scenes:
            dur = s_end - s_start
            if dur < 0.3 or dur > 30:
                continue
            # Composition al centro scena
            comp = _classify_composition(vp, (s_start + s_end) / 2.0)
            all_compositions.append(comp)
            # Camera motion su tutta la scena
            if dur >= 0.5:
                motion = _classify_camera_motion(vp, s_start, s_end)
                all_motions.append(motion)
            # Transition all'inizio scena (non per la prima)
            if s_start > 0.4:
                tr = _classify_transition(vp, s_start)
                all_transitions.append(tr)
        manager.update_progress(job_id, 22 + int(3 * i / total_videos), f"analisi {i}/{total_videos}")

    # Distribuzioni
    def _dist(items):
        from collections import Counter
        c = Counter(items)
        total = sum(c.values()) or 1
        return {k: round(v / total, 3) for k, v in c.most_common()}

    transition_dist = _dist(all_transitions)
    motion_dist = _dist(all_motions)
    composition_dist = _dist(all_compositions)
    manager.log(job_id, f"Transizioni: {transition_dist}")
    manager.log(job_id, f"Movimento camera: {motion_dist}")
    manager.log(job_id, f"Composizione: {composition_dist}")

    # Durata media shot
    all_durs = []
    for scenes in all_scenes:
        for s, e in scenes:
            d = e - s
            if 0.15 < d < 30:
                all_durs.append(d)
    avg_shot = float(np.mean(all_durs)) if all_durs else 2.5
    manager.log(job_id, f"Durata media shot: {avg_shot:.2f}s (su {len(all_durs)} shot)")

    # Durata TOTALE: mediana con filtro outlier (scarta <10s e >120s)
    raw_durations = []
    for vp in video_paths:
        try:
            d = _probe_duration(vp)
            if d and d > 0:
                raw_durations.append(d)
        except Exception:
            pass

    filtered = [d for d in raw_durations if 10 <= d <= 120]
    if not filtered:
        filtered = raw_durations

    if filtered:
        median_raw = float(np.median(filtered))
        target_duration = max(20.0, min(median_raw, 60.0))
    else:
        median_raw = 0.0
        target_duration = 35.0

    manager.log(job_id, f"Durata video riferimento — raw: {len(raw_durations)} · "
                        f"filtrati 10-120s: {len(filtered)} · "
                        f"mediana grezza: {median_raw:.1f}s · "
                        f"target finale: {target_duration:.1f}s")

    # ---- B2: Ultimo frame di ogni video (per analisi chiusura) ----
    manager.update_progress(job_id, 22, "analisi chiusure…")
    closing_dir = os.path.join(work_dir, "closing")
    os.makedirs(closing_dir, exist_ok=True)
    last_frames = []
    for i, vp in enumerate(video_paths, 1):
        out = os.path.join(closing_dir, f"last_{i}.jpg")
        if _extract_last_frame(vp, out):
            last_frames.append(out)
    manager.log(job_id, f"Estratti {len(last_frames)} frame di chiusura")

    # ---- C: Whisper ----
    manager.update_progress(job_id, 25, "trascrizione…")
    transcripts = []
    for i, vp in enumerate(video_paths, 1):
        try:
            txt = transcribe_video(vp)
            transcripts.append(txt)
            manager.log(job_id, f"[{i}/{total_videos}] trascritto ({len(txt)} char)")
        except Exception as e:
            manager.log(job_id, f"[{i}/{total_videos}] errore Whisper: {e}", level="error")
            transcripts.append("")
        manager.update_progress(job_id, 25 + int(25 * i / total_videos), f"trascrizione {i}/{total_videos}")

    # ---- D: OCR + subtitle style ----
    manager.update_progress(job_id, 52, "OCR sottotitoli…")
    try:
        sub_style = extract_subtitle_style(all_frames)
        manager.log(job_id,
            f"OCR: {sub_style.get('num_subtitle_samples', 0)} campioni, "
            f"pos={sub_style.get('alignment')}, "
            f"size={sub_style.get('font_size')}, "
            f"primary={sub_style.get('primary_color_rgb')}, "
            f"outline={sub_style.get('outline_color_rgb')}")
    except Exception as e:
        manager.log(job_id, f"Errore OCR: {e}", level="error")
        sub_style = {}

    # ---- E: Font matching ----
    manager.update_progress(job_id, 65, "font matching…")
    font_match = {}
    try:
        crops = sub_style.pop("_crops", [])
        manager.log(job_id, f"Font matching su {len(crops)} crop, testo: {[c['text'] for c in crops[:5]]}")
        font_match = match_font(crops)
        if font_match.get("best"):
            b = font_match["best"]
            manager.log(job_id,
                f"Font migliore: {b['name']}  score={b['score']:.3f}  "
                f"(ssim={b['ssim']:.3f}, ar={b['ar']:.2f}, ink={b['ink']:.2f})")
            manager.log(job_id,
                "Top 3: " + " | ".join(
                    f"{x['name']}({x['score']:.3f})" for x in font_match.get("top3", [])))
            manager.log(job_id,
                f"Valutati {font_match.get('evaluated', 0)} font su {font_match.get('samples_used', 0)} campioni")
        else:
            manager.log(job_id, f"Font matching: {font_match.get('error', 'nessun candidato valido.')}", level="error")
    except Exception as e:
        manager.log(job_id, f"Errore font matching: {e}", level="error")
    except Exception as e:
        manager.log(job_id, f"Errore font matching: {e}", level="error")

    # ---- E2: Analisi pattern di chiusura ----
    manager.update_progress(job_id, 68, "analisi pattern chiusura…")
    try:
        closing = analyze_closing_pattern(last_frames)
        manager.log(job_id, f"Pattern chiusura: {closing.get('pattern', '?')} — "
                            f"{closing.get('description', '')[:120]}")
    except Exception as e:
        manager.log(job_id, f"Errore analisi chiusura: {e}", level="error")
        closing = {}

    # ---- F: Claude analysis ----
    manager.update_progress(job_id, 75, "analisi narrativa (Claude)…")
    try:
        narrative = analyze_with_claude(transcripts, sub_style, avg_shot)
    except Exception as e:
        manager.log(job_id, f"Errore Claude: {e}", level="error")
        narrative = {"error": str(e)}

    # ---- G: Composizione profilo ----
    manager.update_progress(job_id, 92, "salvataggio profilo…")

    best_font = (font_match.get("best") or {}).get("name", "Impact")

    profile = {
        "schema_version": 1,
        "profile_name": profile_name,
        "tiktok_account": tiktok_account,
        "created_at": __import__("datetime").datetime.utcnow().isoformat(timespec="seconds"),
        "reference_videos": [os.path.basename(p) for p in video_paths],
        "metrics": {
            "avg_shot_duration_sec": round(avg_shot, 2),
            "total_shots": len(all_durs),
            "total_videos": total_videos,
            "target_duration_sec": round(target_duration, 1),
        },
        "technical_style": {
            "transitions": transition_dist,
            "camera_motion": motion_dist,
            "composition": composition_dist,
            "dominant_transition": max(transition_dist, key=transition_dist.get) if transition_dist else "cut",
            "dominant_motion": max(motion_dist, key=motion_dist.get) if motion_dist else "static",
            "dominant_composition": max(composition_dist, key=composition_dist.get) if composition_dist else "unknown",
        },
        "narrative": narrative,
        "reference_transcripts": transcripts,  # <-- esempi reali
        "closing_pattern": closing,
        "subtitle_style": {
            "font_name": best_font,
            "font_top3": font_match.get("top3", []),
            "font_top20": font_match.get("top20", []),
            "alignment": sub_style.get("alignment", 2),
            "font_size": sub_style.get("font_size", 22),
            "margin_v": sub_style.get("margin_v", 60),
            "primary_color_ass": sub_style.get("primary_color_ass", "&H00FFFFFF&"),
            "outline_color_ass": sub_style.get("outline_color_ass", "&H00000000&"),
            "outline_width": 3,
            "bold": 1,
            "primary_color_rgb": sub_style.get("primary_color_rgb", [255, 255, 255]),
            "outline_color_rgb": sub_style.get("outline_color_rgb", [0, 0, 0]),
            "sample_texts": sub_style.get("sample_texts", []),
        },
    }

    # Salva file
    styles_dir = CFG["STYLES_DIR"]
    os.makedirs(styles_dir, exist_ok=True)
    # Inserisci nel DB per ottenere l'id, poi salva file <id>.json
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO profiles (name, tiktok_account, style_json) VALUES (?, ?, ?)",
            (profile_name, tiktok_account, json.dumps(profile, ensure_ascii=False)),
        )
        profile_id = cur.lastrowid

    profile["id"] = profile_id
    out_path = os.path.join(styles_dir, f"{profile_id}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(profile, f, indent=2, ensure_ascii=False)

    manager.log(job_id, f"Profilo salvato: {out_path}")
    manager.update_progress(job_id, 100, "completato")
    manager.finish(job_id, {"profile_id": profile_id, "path": out_path, "name": profile_name})

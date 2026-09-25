"""Analisi tecnica per scena: motion camera, stabilità, luminosità, contrasto."""
import cv2
import numpy as np


def analyze_scene_motion(video_path, start, end, n_samples=5):
    """
    Analizza il movimento camera in una scena.
    Ritorna dict con:
      - motion_type: static | pan_slow | pan_fast | tilt_slow | tilt_fast | zoom |
                     handheld_stable | handheld_shaky | drift_slow
      - motion_quality: good | acceptable | bad
      - stability_score: 0-100 (100 = perfettamente stabile)
      - avg_magnitude, variance, direction_dx, direction_dy
    """
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return {}
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    dur = end - start
    if dur <= 0.2:
        cap.release()
        return {}

    frames = []
    for i in range(n_samples):
        t = start + dur * i / max(1, n_samples - 1)
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(t * fps))
        ok, fr = cap.read()
        if ok and fr is not None:
            gray = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY)
            gray = cv2.resize(gray, (160, 90))
            frames.append(gray)
    cap.release()

    if len(frames) < 3:
        return {}

    flows = []
    for i in range(len(frames) - 1):
        try:
            flow = cv2.calcOpticalFlowFarneback(
                frames[i], frames[i + 1], None,
                0.5, 3, 15, 3, 5, 1.2, 0
            )
            flows.append(flow)
        except Exception:
            continue

    if len(flows) < 2:
        return {}

    mags = [float(np.mean(np.sqrt(f[..., 0]**2 + f[..., 1]**2))) for f in flows]
    avg_mag = float(np.mean(mags))
    mag_var = float(np.var(mags))

    dx = float(np.mean([np.median(f[..., 0]) for f in flows]))
    dy = float(np.mean([np.median(f[..., 1]) for f in flows]))

    # Classificazione tipo movimento
    if avg_mag < 0.5:
        motion_type = "static"
    elif avg_mag < 2.0 and mag_var < 1.5:
        if abs(dx) > abs(dy) * 1.5:
            motion_type = "pan_slow"
        elif abs(dy) > abs(dx) * 1.5:
            motion_type = "tilt_slow"
        else:
            motion_type = "drift_slow"
    elif avg_mag > 5.0 and mag_var > 4.0:
        motion_type = "handheld_shaky"
    elif avg_mag > 3.0:
        if abs(dx) > abs(dy) * 1.5:
            motion_type = "pan_fast"
        elif abs(dy) > abs(dx) * 1.5:
            motion_type = "tilt_fast"
        else:
            motion_type = "handheld"
    else:
        motion_type = "handheld_stable"

    # Stabilità 0-100 (100 = stabile)
    stability = max(0.0, min(100.0, 100.0 - mag_var * 15))

    # Qualità movimento
    if stability > 60 and avg_mag < 8:
        motion_quality = "good"
    elif stability > 35 and avg_mag < 15:
        motion_quality = "acceptable"
    else:
        motion_quality = "bad"

    return {
        "motion_type": motion_type,
        "motion_quality": motion_quality,
        "avg_magnitude": round(avg_mag, 2),
        "variance": round(mag_var, 2),
        "stability_score": round(stability, 1),
        "direction_dx": round(dx, 2),
        "direction_dy": round(dy, 2),
    }


def analyze_scene_brightness(video_path, start, end):
    """Luminosità media, contrasto, e blur (Laplacian variance) al centro scena."""
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return {}
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    mid = (start + end) / 2.0
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(mid * fps))
    ok, fr = cap.read()
    cap.release()
    if not ok or fr is None:
        return {}

    gray = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY)
    blur = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    brightness = float(gray.mean())
    contrast = float(gray.std())

    # Quality flag (soglie sever)
    issues = []
    if brightness < 30:
        issues.append("troppo scuro")
    elif brightness > 240:
        issues.append("sovraesposto")
    if blur < 45:
        issues.append(f"fuori fuoco (blur={blur:.0f})")
    if contrast < 20:
        issues.append("contrasto piatto")

    return {
        "brightness": round(brightness, 1),
        "contrast": round(contrast, 1),
        "blur_score": round(blur, 1),
        "quality_issues": issues,
        "is_poor_quality": len(issues) > 0,
    }


def analyze_scene_composition(video_path, start, end):
    """Composizione: close_up / medium / wide basato su edge density + volto."""
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return {}
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    mid = (start + end) / 2.0
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(mid * fps))
    ok, fr = cap.read()
    cap.release()
    if not ok or fr is None:
        return {}

    h, w = fr.shape[:2]
    gray = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 50, 150)
    edge_density = float(np.sum(edges > 0)) / (w * h)

    # Prova face detection
    cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    import os
    face_ratio = 0.0
    if os.path.exists(cascade_path):
        cascade = cv2.CascadeClassifier(cascade_path)
        if not cascade.empty():
            faces = cascade.detectMultiScale(gray, 1.2, 4, minSize=(50, 50))
            if len(faces) > 0:
                max_face = max(faces, key=lambda f: f[2] * f[3])
                face_ratio = (max_face[2] * max_face[3]) / (w * h)

    if face_ratio > 0.15:
        composition = "close_up_face"
    elif face_ratio > 0.05:
        composition = "medium_face"
    elif edge_density > 0.15:
        composition = "detail"
    elif edge_density > 0.08:
        composition = "medium_wide"
    else:
        composition = "wide"

    return {
        "composition": composition,
        "edge_density": round(edge_density, 4),
        "face_ratio": round(face_ratio, 4),
    }


def analyze_scene_full(video_path, start, end):
    """Esegue tutte le analisi e ritorna un dict unico."""
    out = {}
    out.update(analyze_scene_motion(video_path, start, end))
    out.update(analyze_scene_brightness(video_path, start, end))
    out.update(analyze_scene_composition(video_path, start, end))
    return out

"""Curation scene: classifica keep/skip (backstage, outtake, dead-air, doppioni)."""
import os
import json
import base64
import subprocess
import re
from datetime import datetime
from pathlib import Path
from collections import Counter

import cv2
import imagehash
from app.workers.scene_analyzer import analyze_scene_full
from PIL import Image

from app.config import CFG
from app.jobs import manager


# ==== helpers ====
def _extract_frame(video_path, t, out_path, max_width=384):
    cmd = [
        "ffmpeg", "-y", "-ss", f"{max(t,0):.3f}", "-i", video_path,
        "-frames:v", "1", "-q:v", "4",
        "-vf", f"scale={max_width}:-1",
        out_path,
    ]
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=25)
    except Exception:
        return False
    return os.path.exists(out_path) and os.path.getsize(out_path) > 0


def _phash(path):
    try:
        return imagehash.phash(Image.open(path))
    except Exception:
        return None


def _b64(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()


def _load_manifest():
    d = os.path.join(CFG["MEDIA_DIR"], "analysis")
    files = sorted(
        [os.path.join(d, f) for f in os.listdir(d)
         if f.startswith("job_") and f.endswith("_manifest.json")],
        key=os.path.getmtime, reverse=True,
    )
    if not files:
        return None
    with open(files[0]) as f:
        return json.load(f)


def _load_profile(profile_id):
    if not profile_id:
        return {}
    from app.db import get_conn
    with get_conn() as conn:
        row = conn.execute("SELECT style_json FROM profiles WHERE id=?", (profile_id,)).fetchone()
    return json.loads(row["style_json"]) if row else {}


# ==== estrazione scene con 3 keyframe ====
def _scene_quality_metrics(keyframes, video_path=None, start=None, end=None):
    """Analisi completa: motion, stability, brightness, composition.
    Usa scene_analyzer se video_path/start/end disponibili, altrimenti fallback su keyframes.
    """
    import cv2
    import numpy as np

    if not keyframes:
        return {"is_low_quality": True, "reason": "nessun keyframe",
                "blur": 0, "lum": 0, "motion": 0, "stability": 0,
                "motion_type": "unknown", "motion_quality": "bad",
                "composition": "unknown"}

    # Metrica veloce dai keyframe (sempre disponibile)
    blurs, lums = [], []
    for kf in keyframes[:3]:
        try:
            img = cv2.imread(kf)
            if img is None:
                continue
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            blurs.append(float(cv2.Laplacian(gray, cv2.CV_64F).var()))
            lums.append(float(gray.mean()))
        except Exception:
            pass

    if not blurs:
        return {"is_low_quality": True, "reason": "keyframe non leggibili",
                "blur": 0, "lum": 0, "motion": 0, "stability": 0,
                "motion_type": "unknown", "motion_quality": "bad",
                "composition": "unknown"}

    avg_blur = float(np.mean(blurs))
    avg_lum = float(np.mean(lums))

    # Se ho video_path, uso analyzer completo
    if video_path and start is not None and end is not None:
        try:
            analysis = analyze_scene_full(video_path, start, end)
        except Exception:
            analysis = {}
    else:
        analysis = {}

    motion_type = analysis.get("motion_type", "unknown")
    motion_quality = analysis.get("motion_quality", "unknown")
    stability = analysis.get("stability_score", 0)
    composition = analysis.get("composition", "unknown")
    brightness = analysis.get("brightness", avg_lum)
    blur = analysis.get("blur_score", avg_blur)
    contrast = analysis.get("contrast", 0)
    edge_density = analysis.get("edge_density", 0)

    # === SCARTO AUTOMATICO ===
    reasons = []
    if brightness < 30:
        reasons.append(f"scena buia ({brightness:.0f})")
    elif brightness > 240:
        reasons.append(f"sovraesposta ({brightness:.0f})")
    if blur < 45:
        reasons.append(f"fuori fuoco ({blur:.0f})")
    if motion_quality == "bad":
        reasons.append(f"camera shake ({stability:.0f}/100)")
    if contrast < 15:
        reasons.append(f"contrasto piatto ({contrast:.0f})")

    is_low = len(reasons) > 0

    return {
        "is_low_quality": is_low,
        "reason": " · ".join(reasons),
        "blur": round(blur, 1),
        "lum": round(brightness, 1),
        "contrast": round(contrast, 1),
        "stability": round(stability, 1),
        "motion_type": motion_type,
        "motion_quality": motion_quality,
        "composition": composition,
        "edge_density": round(edge_density, 4),
    }


def _split_scene(start, end, video_seed=0):
    """
    Divide una scena lunga in sub-clip con DURATE VARIABILI (pattern ritmico).
    Ritorna lista di (start, end) con durate diverse per dare varietà al montaggio.
    """
    dur = end - start
    if dur <= 1.8:
        return [(start, end)]

    # Pattern ritmico di durate target (in secondi) — varietà umana
    # Alterna shot brevi, medi, lunghi per creare ritmo
    duration_pattern = [2.0, 3.4, 2.5, 4.0, 2.2, 2.8, 3.8, 2.4, 1.8, 3.2, 4.2, 2.6, 3.0, 2.1, 3.6]

    # Offset diverso per ogni video (varietà tra clip diverse)
    offset = (video_seed * 7) % len(duration_pattern)

    parts = []
    t = start
    i = 0
    while t < end - 0.5:  # lascia almeno 0.5s di margine
        remaining = end - t
        if remaining < 1.8:
            # Troppo corto: allunga l'ultimo pezzo invece di crearne uno piccolo
            if parts:
                s, _ = parts[-1]
                parts[-1] = (s, end)
            else:
                parts.append((t, end))
            break

        # Prendi la durata dal pattern
        target_dur = duration_pattern[(offset + i) % len(duration_pattern)]

        # Adatta se è più lunga del rimanente
        if target_dur > remaining:
            target_dur = remaining

        # Adatta se lascerebbe un residuo troppo piccolo
        if remaining - target_dur < 1.8 and remaining - target_dur > 0:
            target_dur = remaining

        sub_end = t + target_dur
        parts.append((round(t, 3), round(sub_end, 3)))
        t = sub_end
        i += 1

    # Fallback se non si è creato nulla
    if not parts:
        return [(start, end)]
    return parts



def _build_scenes(manifest, frames_root):
    scenes = []
    sid = 0
    for v in manifest.get("videos", []):
        name = v.get("name", "?")
        path = v.get("path")
        base = Path(name).stem
        for si, sc in enumerate(v.get("scenes", [])):
            full_start = float(sc.get("start", 0))
            full_end = float(sc.get("end", 0))

            # Splitta la scena in sub-clip con DURATE VARIABILI (pattern ritmico)
            # Seed deterministico per ogni video (stessa clip = stesso split)
            vseed = abs(hash(name)) % 1000
            subclips = _split_scene(full_start, full_end, video_seed=vseed)

            for sub_i, (start, end) in enumerate(subclips):
                dur = end - start
                if dur < 0.8:
                    continue

                # Keyframes: 25% e 75% del sub-clip
                ts = [start + dur * f for f in (0.25, 0.75)]
                d = os.path.join(frames_root, f"{base}_s{si}_sub{sub_i}")
                os.makedirs(d, exist_ok=True)
                kfs = []
                for i, t in enumerate(ts):
                    p = os.path.join(d, f"f{i}.jpg")
                    if _extract_frame(path, t, p):
                        kfs.append(p)
                if len(kfs) < 2:
                    continue

                q = _scene_quality_metrics(kfs, path, start, end)

                scenes.append({
                    "id": sid,
                    "clip_name": name,
                    "clip_path": path,
                    "scene_index": f"{si}.{sub_i}",
                    "start": round(start, 2),
                    "end": round(end, 2),
                    "duration": round(dur, 2),
                    "keyframes": kfs,
                    "quality": q,
                    "is_low_quality": q["is_low_quality"],
                    "quality_reason": q["reason"],
                    "blur_score": q["blur"],
                    "lum_score": q["lum"],
                    "stability_score": q.get("stability", 0),
                    "motion_type": q.get("motion_type", "unknown"),
                    "motion_quality": q.get("motion_quality", "unknown"),
                    "composition": q.get("composition", "unknown"),
                })
                sid += 1
    return scenes


# ==== dedup pHash ====
def _dedup(scenes, threshold=6):
    hashes = []
    for s in scenes:
        mid = s["keyframes"][len(s["keyframes"]) // 2]
        hashes.append(_phash(mid))
    for i, s in enumerate(scenes):
        s["duplicate_of"] = None
        if hashes[i] is None:
            continue
        for j in range(i):
            if hashes[j] is None:
                continue
            try:
                if (hashes[i] - hashes[j]) <= threshold:
                    s["duplicate_of"] = scenes[j]["id"]
                    break
            except Exception:
                continue
    return scenes


# ==== prompt Claude ====
def _build_prompt(scenes, profile):
    nar = profile.get("narrative", {}) if profile else {}
    return f"""Sei un Video Editor esperto specializzato in contenuti food/beverage per TikTok, Reels e Shorts.

Ti mostro i keyframe di {len(scenes)} scene estratte dai video raw di una giornata di riprese.
Il montaggio finale deve raccontare l'esperienza in modo COMPLETO e BILANCIATO, mostrando:
- il LOCALE (esterno, ingresso, sala, atmosfera, arredi)
- il MENU (menù fisico, lavagna, totem)
- il CIBO (piatti, dettagli, preparazione)
- le BEVANDE (bibite, alcolici, versare, brindisi, sorso)
- le PERSONE (staff, cliente che mangia, reazioni, gesti)

## CONTESTO STILE
- Hook: {nar.get('hook_style','')[:120]}
- Struttura: {nar.get('narrative_structure','')[:150]}
- Tono: {nar.get('tone_of_voice','')[:120]}

## COMPITO
Per OGNI scena decidi: **keep** (buona per il video finale) o **skip** (scartare).

### CATEGORIZZAZIONE (campo `role`)
Assegna UNA di queste categorie ESATTE:

**LOCALE (PRIORITÀ ALTA):**
- `venue_exterior` — insegna, facciata, strada, esterno
- `venue_entrance` — porta, ingresso, soglia
- `venue_interior_wide` — sala vista d'insieme, tavoli, atmosfera
- `venue_interior_detail` — dettagli arredo, luci, pareti, decori
- `kitchen` — cucina, chef, preparazione
- `staff` — camerieri, cassieri, personale in azione
- `menu_board` — menù, lavagna, totem, schermo ordini

**REGOLA IMPORTANTE — MACCHINE E SCHERMI**:
Se la scena mostra una MACCHINA, TOTEM, MONITOR, WALLSCREEN, DISPENSER, TV o qualsiasi elemento tecnologico del locale (anche con azione drink in corso), classificala come `venue_interior_detail` e NON come `drink_pour`/`drink_detail`.
Motivo: il montaggio deve mostrare il LOCALE, non solo l'azione.

Esempi corretti:
- "mano che versa da Coca-Cola Freestyle" → `venue_interior_detail` (NON drink_pour)
- "cliente usa totem touchscreen" → `venue_interior_detail` (NON menu_board)
- "wall screen con video promozionale" → `venue_interior_detail`
- "bicchiere Coca-Cola al tavolo" → `drink_detail` (nessuna macchina visibile)

**BEVANDE:**
- `drink_pour` — versare, spillare, miscelare
- `drink_detail` — bicchiere, bottiglia, cocktail primo piano
- `drink_cheers` — brindisi, sorso, drink in mano

**CIBO:**
- `food_prep` — preparazione piatto in corso
- `food_plated` — piatto finito, presentazione
- `food_closeup` — dettaglio texture, vapore, condimento

**PERSONE:**
- `person_eating` — assaggio, morso, reazione genuina
- `person_talking` — volto che parla in camera
- `hands_gesture` — mani che indicano, mostrano, gesti

**SCARTI:**
- `backstage` — setup camera, prove inquadratura, persone di spalle senza azione
- `dead-air` — vuoto, camera a vuoto, tempi morti
- `outtake` — errori, risate fuori luogo, ripartenze
- `duplicate` — stessa inquadratura di un'altra scena (scegli il migliore)
- `blurry` — fuori fuoco (ti segnalo `blur_score` nella riga scena)
- `shaky` — camera shake eccessivo (ti segnalo `stability_score`)
- `non-pertinente` — soggetti estranei al locale/cibo

### SCARTA (skip) se:
- persona sistema camera/telefono, setup luci, preparazione prima dell'azione
- inquadrature di pavimento/soffitto/pareti vuote
- camera shake estremo o fuori fuoco (guarda i valori blur/stability)
- duplicati (tieni il migliore)
- persone di spalle senza azione
- soggetti non pertinenti al locale

### TIENI (keep) SOLO se la scena è CHIARAMENTE utilizzabile:
- mostra qualcosa di riconoscibile del locale/cibo/persone
- inquadratura stabile e leggibile
- contenuto pertinente e utile alla narrazione

### REGOLA D'ORO
- **Nel dubbio, SCARTA** scene con problemi tecnici (blur, shake, scuro)
- **Non scartare** scene di ambiente solo perché "statiche": le panoramiche statiche del locale sono FONDAMENTALI per il montaggio finale. Scartale SOLO se sono vuote/dead-air.
- **Bilancia le categorie**: se possibile, tieni almeno 1-2 scene per ogni categoria disponibile

## OUTPUT
Rispondi ESCLUSIVAMENTE con JSON:
{{
  "scenes": [
    {{
      "id": 0,
      "decision": "keep" | "skip",
      "role": "venue_exterior|venue_entrance|venue_interior_wide|venue_interior_detail|kitchen|staff|menu_board|drink_pour|drink_detail|drink_cheers|food_prep|food_plated|food_closeup|person_eating|person_talking|hands_gesture|backstage|dead-air|outtake|duplicate|blurry|shaky|non-pertinente",
      "description": "descrizione concreta di 8-12 parole: COSA vedi esattamente nella scena (soggetto + azione + contesto). Es: 'insegna luminosa del locale di notte vista da fuori', 'mani che versano coca nel bicchiere al tavolo', 'primo piano ali glassate su piatto bianco'",
      "subject": "soggetto principale in 2-3 parole: es. 'insegna', 'ali fritte', 'bicchiere coca', 'cliente che mangia', 'chef', 'tavolo', 'sala interna', 'pizza', 'salsa', 'mani', 'volto maschile'",
      "key_props": ["lista", "di", "oggetti", "riconoscibili", "max 3"],
      "duplicate_of": null | id_della_scena_migliore,
      "reason": "max 10 parole — motivo keep/skip"
    }}
  ]
}}

REGOLE CRITICHE SUI CAMPI NUOVI:
- `description`: DESCRIVI LETTERALMENTE cosa vedi, non inventare. Se la scena mostra "mano con telefono" scrivi così. NON scrivere "scena interessante".
- `subject`: il soggetto principale per il matching (2-3 parole max).
- `key_props`: max 3 oggetti riconoscibili (es. "coca-cola", "menu", "logo", "cassa").

ESEMPI CORRETTI:
- "description": "insegna WING STOP del locale di notte con luci al neon"
- "description": "mani che versano coca-cola in bicchiere di vetro al tavolo"
- "description": "primo piano ali fritte dorate su piatto con salsa"
- "description": "cliente seduto al tavolo che addenta ala fritta sorridendo"

ESEMPI SBAGLIATI (non fare):
- "description": "scena di cibo" — troppo generico
- "description": "qualcosa di bello" — non concreto
- "subject": "roba" — inutile
"""


# ==== chiamata Claude (a batch di 30 scene) ====
_BATCH_SIZE = 30  # 30 scene x 3 keyframe = 90 immagini, sotto il limite di 100


def _classify_batch(client, scenes_batch, profile, batch_label, total_batches):
    """Una singola chiamata Claude per un batch di scene."""
    content = [{"type": "text", "text": _build_prompt(scenes_batch, profile)}]
    content.append({
        "type": "text",
        "text": "\n\nNOTA: questo e il batch " + str(batch_label) + " di " + str(total_batches) +
                ". Rispondi SOLO per le scene contenute in questo batch."
    })
    for s in scenes_batch:
        header = "\n--- SCENA id=" + str(s["id"]) + " - " + s["clip_name"] + " - " + str(s["duration"]) + "s"
        if s.get("duplicate_of") is not None:
            header += " - possibile duplicato di id=" + str(s["duplicate_of"])
        header += " ---"
        content.append({"type": "text", "text": header})
        for kf in s["keyframes"][:3]:
            try:
                content.append({
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/jpeg", "data": _b64(kf)},
                })
            except Exception:
                pass

    resp = client.messages.create(
        model=CFG["CLAUDE_MODEL_FAST"],
        max_tokens=4000,
        messages=[{"role": "user", "content": content}],
    )
    return resp.content[0].text.strip()


def _claude_classify(scenes, profile):
    """Suddivide le scene in batch e chiama Claude ripetutamente."""
    from anthropic import Anthropic
    client = Anthropic(api_key=CFG["ANTHROPIC_API_KEY"])

    batches = [scenes[i:i + _BATCH_SIZE] for i in range(0, len(scenes), _BATCH_SIZE)]
    all_results = []

    for bi, batch in enumerate(batches, 1):
        raw = _classify_batch(client, batch, profile, bi, len(batches))
        if "```" in raw:
            raw = raw.replace("```json", "").replace("```", "")
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            for s in batch:
                all_results.append({
                    "id": s["id"], "decision": "keep", "role": "detail",
                    "duplicate_of": None, "reason": "batch non classificato (fallback)",
                })
            continue
        try:
            data = json.loads(m.group(0))
            for d in data.get("scenes", []):
                all_results.append(d)
        except Exception as e:
            for s in batch:
                all_results.append({
                    "id": s["id"], "decision": "keep", "role": "detail",
                    "duplicate_of": None, "reason": "parse errore",
                })

    return json.dumps({"scenes": all_results}, ensure_ascii=False)


# ==== orchestratore ====
def curate_clips(job_id, profile_id=None):
    profile = _load_profile(profile_id)

    manager.log(job_id, "Carico manifest raw…")
    manifest = _load_manifest()
    if not manifest:
        manager.fail(job_id, "Nessun manifest raw. Esegui prima 'Analizza i raw'.")
        return

    n_vids = len(manifest.get("videos", []))
    manager.log(job_id, f"Manifest: {n_vids} video")

    frames_root = os.path.join(CFG["FRAMES_DIR"], f"curate_job_{job_id}")
    os.makedirs(frames_root, exist_ok=True)

    manager.update_progress(job_id, 5, "estrazione keyframe…")
    scenes = _build_scenes(manifest, frames_root)
    manager.log(job_id, f"Scene totali: {len(scenes)}")
    if not scenes:
        manager.fail(job_id, "Nessuna scena valida.")
        return

    manager.update_progress(job_id, 25, "dedup pHash…")
    _dedup(scenes, threshold=6)  # soglia standard
    n_dup = sum(1 for s in scenes if s.get("duplicate_of") is not None)
    manager.log(job_id, f"Duplicati rilevati (pHash, soglia 8): {n_dup}")

    # Pre-filtro: separa scene buone da scartate per qualità
    good_scenes = []
    dropped = []
    for s in scenes:
        if s.get("is_low_quality"):
            dropped.append(s)
        else:
            good_scenes.append(s)

    if dropped:
        from collections import Counter
        reasons = Counter()
        for s in dropped:
            r = s.get("quality_reason", "?")
            # Semplifica il motivo per il contatore
            if "buia" in r:
                reasons["buio"] += 1
            elif "fuori fuoco" in r:
                reasons["fuori fuoco"] += 1
            elif "shake" in r or "movimento" in r:
                reasons["camera shake"] += 1
            elif "bianco" in r or "sovra" in r:
                reasons["sovraesposto"] += 1
            else:
                reasons["altro"] += 1
        manager.log(job_id, f"✂ Pre-filtro: scartate {len(dropped)} scene automaticamente")
        manager.log(job_id, "  Motivi: " + ", ".join(f"{k}={v}" for k, v in reasons.most_common()))

    scenes = good_scenes

    if not scenes:
        manager.fail(job_id, "Tutte le scene sono state scartate dal pre-filtro qualità.")
        return

    manager.update_progress(job_id, 40, "classificazione Claude…")
    n_batches = (len(scenes) + 29) // 30
    manager.log(job_id, f"Invio {len(scenes)} scene a Claude in {n_batches} batch (max 30 scene/batch)…")
    raw = _claude_classify(scenes, profile)

    manager.update_progress(job_id, 85, "parsing…")
    if "```" in raw:
        raw = re.sub(r"```json\s*", "", raw)
        raw = re.sub(r"```\s*", "", raw)
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        manager.fail(job_id, f"Claude non ha restituito JSON: {raw[:300]}")
        return
    try:
        result = json.loads(m.group(0))
    except Exception as e:
        manager.fail(job_id, f"JSON parse: {e}")
        return

    by_id = {s["id"]: s for s in scenes}
    curated = []
    n_keep = n_skip = 0
    for d in result.get("scenes", []):
        sid = d.get("id")
        if sid not in by_id:
            continue
        s = by_id[sid]
        d.update({
            "clip_name": s["clip_name"],
            "clip_path": s["clip_path"],
            "scene_index": s["scene_index"],
            "start": s["start"],
            "end": s["end"],
            "duration": s["duration"],
            "keyframes": s["keyframes"],
            # Campi semantici (per il matcher)
            "description": d.get("description", ""),
            "subject": d.get("subject", ""),
            "key_props": d.get("key_props", []),
        })
        if d.get("decision") == "keep":
            n_keep += 1
        else:
            n_skip += 1
        curated.append(d)

    out_dir = os.path.join(CFG["MEDIA_DIR"], "curated")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"job_{job_id}_curated.json")
    payload = {
        "generated_at": datetime.utcnow().isoformat(timespec="seconds"),
        "profile_id": profile_id,
        "total": len(curated),
        "keep": n_keep,
        "skip": n_skip,
        "scenes": curated,
    }
    with open(out_path, "w") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)

    manager.log(job_id, f"Curation salvata: {out_path}")
    manager.log(job_id, f"Bilancio: {n_keep} keep / {n_skip} skip su {len(curated)} scene")
    roles = Counter(d.get("role","?") for d in curated if d.get("decision") == "keep")
    if roles:
        manager.log(job_id, "Ruoli keep: " + ", ".join(f"{k}={v}" for k, v in roles.most_common()))
    skip_reasons = Counter(d.get("role","?") for d in curated if d.get("decision") != "keep")
    if skip_reasons:
        manager.log(job_id, "Motivi skip: " + ", ".join(f"{k}={v}" for k, v in skip_reasons.most_common()))

    manager.update_progress(job_id, 100, "completato")
    manager.finish(job_id, {
        "curated_path": out_path,
        "keep": n_keep,
        "skip": n_skip,
        "total": len(curated),
    })

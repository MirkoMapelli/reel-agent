"""Genera EDL partendo dalle scene 'keep' della curation (o fallback raw)."""
import os
import json
import base64
import re
from datetime import datetime

from app.config import CFG
from app.jobs import manager
from app.workers.story_writer import write_story_and_match
from app.db import get_conn


def _b64(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()


def _load_profile(profile_id):
    with get_conn() as conn:
        row = conn.execute("SELECT style_json FROM profiles WHERE id=?", (profile_id,)).fetchone()
    if not row:
        raise RuntimeError(f"Profilo #{profile_id} non trovato.")
    return json.loads(row["style_json"])


def _load_latest_curated():
    d = os.path.join(CFG["MEDIA_DIR"], "curated")
    if not os.path.isdir(d):
        return None
    files = sorted(
        [os.path.join(d, f) for f in os.listdir(d)
         if f.startswith("job_") and f.endswith("_curated.json")],
        key=os.path.getmtime, reverse=True,
    )
    if not files:
        return None
    with open(files[0]) as f:
        return json.load(f)


def _scenes_from_curated(curated):
    out = []
    for s in curated.get("scenes", []):
        if s.get("decision") != "keep":
            continue
        out.append({
            "scene_id": s.get("id"),
            "clip_name": s.get("clip_name"),
            "clip_path": s.get("clip_path"),
            "scene_index": s.get("scene_index"),
            "start": s.get("start"),
            "end": s.get("end"),
            "duration": s.get("duration"),
            "role": s.get("role", "detail"),
            "keyframe": (s.get("keyframes") or [None])[len(s.get("keyframes") or [None]) // 2],
        })
    return out


def _build_prompt(profile, scenes, venue_name=""):
    sub = profile.get("subtitle_style", {})
    nar = profile.get("narrative", {})
    met = profile.get("metrics", {})
    tech = profile.get("technical_style", {})
    closing = profile.get("closing_pattern", {})
    closing_desc = closing.get("description", "") if isinstance(closing, dict) else ""
    closing_type = closing.get("pattern", "") if isinstance(closing, dict) else ""
    closing_vo = closing.get("voiceover_ending", "") if isinstance(closing, dict) else ""

    avg_shot = met.get("avg_shot_duration_sec", 2.3)
    target_dur_shot = avg_shot  # alias per il prompt
    target_dur = met.get("target_duration_sec", 35.0)
    target_dur_min = int(target_dur * 0.90)   # 54s se target=60
    target_dur_max = int(target_dur * 1.15)   # 69s se target=60
    # Numero minimo di scene per raggiungere il target con shot da 3s
    n_scene_min = max(17, int(target_dur / 3.0))

    lines = []
    for s in scenes:
        lines.append(
            f"scene_id={s['scene_id']:>3} · {s['clip_name']:<18} "
            f"· durata={s['duration']:.2f}s · ruolo={s['role']}"
        )
    scenes_block = "\n".join(lines)
    n_available = len(scenes)

    # Clausinga per nome locale (separata per evitare nested f-string)
    if venue_name:
        venue_clause = f'NOME LOCALE: "{venue_name}" — menzionalo ALMENO UNA VOLTA nel voiceover in modo naturale.'
    else:
        venue_clause = ''

    prompt = f"""Sei un Senior Video Editor TikTok/Reels food. Hai già analizzato i raw e selezionato SOLO le scene buone.

## PROFILO DI STILE appreso
- Hook (primi 2-3s): {nar.get('hook_style','')}
- Struttura: {nar.get('narrative_structure','')}
- Tono: {nar.get('tone_of_voice','')}
- Ritmo: {nar.get('pacing_style','')} (shot medio: {avg_shot:.2f}s)
- Sequenza inquadrature: {nar.get('shot_sequence','')}
- CTA: {nar.get('call_to_action_style','')}
- Pattern di chiusura osservato: {closing_type} — {closing_desc}
- Come chiudono il voiceover i riferimenti: {closing_vo}

## STILE TECNICO
- Transizione dominante: {tech.get('dominant_transition', 'cut')}
- Movimento camera dominante: {tech.get('dominant_motion', 'static')}
- Composizione dominante: {tech.get('dominant_composition', 'hands')}

{venue_clause}

## SCENE BUONE DISPONIBILI ({n_available})
{scenes_block}

## COMPITO
Costruisci l'ORDINE NARRATIVO del video finale. **NON decidere tu le durate né il numero di scene** — il software le calcolerà automaticamente.

Rispondi ESCLUSIVAMENTE con JSON valido:

{{
  "title": "Titolo breve (max 40 char)",
  "voiceover_script": "Testo continuo 90-140 parole da leggere ad alta voce. Segui hook -> sviluppo -> CTA del profilo. Deve essere lungo abbastanza da coprire ~60 secondi di parlato.",
  "timeline": [
    {{
      "scene_id": 5,
      "subtitle_text": "Porzione esatta del voiceover letta durante questa scena"
    }}
  ]
}}

## REGOLA D'ORO — NUMERO DI SCENE
Includi nella timeline **ALMENO 20 scene** (idealmente 22-25). 
- Hai {n_available} scene buone disponibili, usane il maggior numero possibile.
- Se includi meno di 20 scene, il montaggio sarà RIFIUTATO automaticamente.
- **NON scrivere `in_sec` o `out_sec`**: il software li calcolerà per centrare la durata target di {target_dur:.0f}s.

## REGOLE NARRATIVE
A) CHIUSURA: l'ultima scena deve essere conclusiva (piatto completo, reazione finale).
B) CHIUSURA VOICEOVER: termina con CTA netta ({closing_vo}).
C) PRIMA SCENA: apri con inquadratura FORTE (piatto pronto, protagonista in azione).
D) COMPOSIZIONE: privilegia "{tech.get('dominant_composition', 'unknown')}".

## REGOLA SUL VOICEOVER
Il voiceover deve essere **90-140 parole** (circa 55-75 secondi di parlato). Deve coprire TUTTE le 20+ scene.
La concatenazione dei `subtitle_text` deve ricostruire ESATTAMENTE il voiceover_script.
"""
    return prompt


# ============================================================
# BILANCIAMENTO CATEGORIE
# ============================================================

# Quote target per categoria (min%, max%)
_QUOTAS = {
    "ambiance": {"min": 0.18, "max": 0.40, "roles": [
        "venue_exterior", "venue_entrance", "venue_interior_wide",
        "venue_interior_detail", "kitchen", "staff", "menu_board"
    ]},
    "drinks": {"min": 0.08, "max": 0.28, "roles": [
        "drink_pour", "drink_detail", "drink_cheers"
    ]},
    "food": {"min": 0.22, "max": 0.45, "roles": [
        "food_prep", "food_plated", "food_closeup"
    ]},
    "people": {"min": 0.15, "max": 0.40, "roles": [
        "person_eating", "person_talking", "hands_gesture"
    ]},
}

def _role_category(role):
    for cat, info in _QUOTAS.items():
        if role in info["roles"]:
            return cat
    return "other"


def _rebalance_by_category(all_scenes, target_n):
    """
    Costruisce una timeline bilanciata di ~target_n scene:
      - Rispetta quote minime per ambiance, drinks, food, people
      - Ordine narrativo: apertura locale → menu/bevande → cibo/persone → chiusura
    """
    import random as _random
    _random.seed(42)

    # Raggruppa per categoria
    by_cat = {cat: [] for cat in list(_QUOTAS.keys()) + ["other"]}
    for s in all_scenes:
        cat = _role_category(s.get("role", ""))
        by_cat[cat].append(s)

    # Ordina ogni gruppo per qualità (blur alto, stability alta)
    def _quality_key(s):
        q = s.get("quality", {})
        blur = q.get("blur", 0)
        stab = q.get("stability", 0)
        return blur * 0.5 + stab * 0.5

    for cat in by_cat:
        by_cat[cat].sort(key=_quality_key, reverse=True)

    # Piano: quante scene per categoria
    plan = {}
    remaining = target_n
    # Minimi garantiti
    for cat, info in _QUOTAS.items():
        avail = len(by_cat[cat])
        want_min = max(1, int(round(target_n * info["min"])))
        want_max = int(round(target_n * info["max"]))
        take = min(avail, max(want_min, min(want_max, int(round(target_n * (info["min"] + info["max"]) / 2)))))
        plan[cat] = take
        remaining -= take

    # Riempi con altre categorie
    if remaining > 0:
        for cat in ["food", "people", "ambiance", "drinks", "other"]:
            if remaining <= 0:
                break
            extra = min(remaining, len(by_cat[cat]) - plan.get(cat, 0))
            if extra > 0:
                plan[cat] = plan.get(cat, 0) + extra
                remaining -= extra

    # Se ancora restano slot, aggiungi qualsiasi cosa
    if remaining > 0:
        for cat in by_cat:
            if remaining <= 0:
                break
            extra = min(remaining, len(by_cat[cat]) - plan.get(cat, 0))
            if extra > 0:
                plan[cat] = plan.get(cat, 0) + extra
                remaining -= extra

    # Seleziona le scene
    picked = {}
    for cat, n in plan.items():
        picked[cat] = by_cat[cat][:n]

    # === ORDINE NARRATIVO ===
    # Fasi: apertura (venue_exterior/entrance) → contesto (ambiance+menu+drinks) →
    #        corpo (food+people) → chiusura (person_eating/drink_cheers)
    ordered = []

    # 1. Apertura: venue_exterior/entrance (max 2)
    opening = [s for s in picked.get("ambiance", []) if s.get("role") in ("venue_exterior", "venue_entrance")]
    for s in opening[:2]:
        ordered.append(s)
        picked["ambiance"].remove(s)

    # 2. Contesto: resto ambiance + menu_board + drinks
    context = []
    for s in picked.get("ambiance", []):
        context.append(s)
    for s in picked.get("drinks", [])[:max(1, len(picked.get("drinks", [])) // 2)]:
        context.append(s)
    _random.shuffle(context)
    ordered.extend(context)
    used_drinks = set(id(s) for s in context if s.get("role", "").startswith("drink_"))

    # 3. Corpo: food + people intercalati
    body_food = [s for s in picked.get("food", [])]
    body_people = [s for s in picked.get("people", [])]
    _random.shuffle(body_food)
    _random.shuffle(body_people)

    body = []
    fi, pi = 0, 0
    while fi < len(body_food) or pi < len(body_people):
        # Aggiungi 2 food
        for _ in range(2):
            if fi < len(body_food):
                body.append(body_food[fi]); fi += 1
        # Aggiungi 1 people (alternanza)
        if pi < len(body_people):
            body.append(body_people[pi]); pi += 1
    ordered.extend(body)

    # 4. Chiusura: rimanenti drinks + ultima person_eating
    remaining_drinks = [s for s in picked.get("drinks", []) if id(s) not in used_drinks]
    for s in remaining_drinks:
        ordered.append(s)

    # Deduplica mantenendo ordine
    seen = set()
    final = []
    for s in ordered:
        sid = s["scene_id"]
        if sid in seen:
            continue
        seen.add(sid)
        final.append(s)

    # Trim o espandi al target
    final = final[:target_n]
    return final


def generate_edl(job_id, profile_id, venue_name=""):
    """Genera EDL scrivendo una sceneggiatura per beat e matchando sulle scene reali."""
    manager.log(job_id, f"Carico profilo #{profile_id}...")
    profile = _load_profile(profile_id)

    # Carica curation più recente
    curated = _load_latest_curated()
    if not curated:
        manager.fail(job_id, "Nessuna curation trovata. Esegui 'Seleziona clip buone' prima.")
        return

    keep = [s for s in curated.get("scenes", []) if s.get("decision") == "keep"]
    if len(keep) < 5:
        manager.fail(job_id, f"Solo {len(keep)} scene keep. Servono almeno 5.")
        return

    # Target durata: range custom o profilo
    target_dur = float(profile.get("metrics", {}).get("target_duration_sec", 60.0))
    try:
        cfg_path = os.path.join(CFG["MEDIA_DIR"], "analysis_config.json")
        if os.path.exists(cfg_path):
            with open(cfg_path) as f:
                _cfg = json.load(f)
            dr = _cfg.get("duration_range", {})
            _dmax = float(dr.get("max", 0) or 0)
            _dmin = float(dr.get("min", 0) or 0)
            if _dmax > 0:
                target_dur = _dmax
                manager.log(job_id,
                    f"Range custom [{_dmin:.0f}-{_dmax:.0f}s] -> target_dur={target_dur:.1f}s")
    except Exception as e:
        manager.log(job_id, f"WARN lettura range: {e}", level="error")

    # Chiama lo story writer
    try:
        edl, story = write_story_and_match(
            job_id=job_id,
            profile=profile,
            curated_data=curated,
            target_dur=target_dur,
            venue_name=venue_name,
        )
    except Exception as e:
        manager.fail(job_id, f"Errore story writer: {e}")
        return

    # Salva EDL
    from datetime import datetime
    edl["generated_at"] = datetime.utcnow().isoformat(timespec="seconds")
    edl_dir = os.path.join(CFG["MEDIA_DIR"], "edl")
    os.makedirs(edl_dir, exist_ok=True)
    edl_path = os.path.join(edl_dir, f"job_{job_id}_edl.json")
    with open(edl_path, "w", encoding="utf-8") as f:
        json.dump(edl, f, indent=2, ensure_ascii=False)

    # Salva anche la sceneggiatura per la UI regia
    story["edl_job_id"] = job_id
    story_dir = os.path.join(CFG["MEDIA_DIR"], "story")
    os.makedirs(story_dir, exist_ok=True)
    story_path = os.path.join(story_dir, f"job_{job_id}_story.json")
    with open(story_path, "w", encoding="utf-8") as f:
        json.dump(story, f, indent=2, ensure_ascii=False)

    manager.log(job_id, f"EDL salvato: {edl_path}")
    manager.log(job_id, f"Sceneggiatura salvata: {story_path}")
    manager.log(job_id, f"Titolo: {edl.get('title')}")
    manager.log(job_id, f"Scene in timeline: {edl['n_clips']}")
    manager.log(job_id, f"Durata totale: {edl['total_duration']}s")
    manager.log(job_id, f"Voiceover: {len(edl.get('voiceover_script',''))} char")

    manager.update_progress(job_id, 100, "completato")
    manager.finish(job_id, {
        "edl_path": edl_path,
        "story_path": story_path,
        "n_clips": edl["n_clips"],
        "total_duration": edl["total_duration"],
        "title": edl.get("title",""),
    })

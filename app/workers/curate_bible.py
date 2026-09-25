"""
Step 6.2 — Curation
Input : manifest.json prodotto da analyze_raw (uno per raw)
Output: curated.json con archetype + score + prompt per ogni subclip

Tassonomia: hook, context, product, reaction, establishing, detail, cta
"""
from __future__ import annotations
import json, math, argparse, os
from typing import Any

ARCHETYPES = ("hook", "context", "product", "reaction", "establishing", "detail", "cta")

# mapping shot_type (bible) -> motion_type (analyze_raw) ammessi
SHOT_TYPE_TO_MOTIONS = {
    "statica_tripod": {"static"},
    "pov": {"handheld", "static"},
    "handheld": {"handheld", "pan_slow", "pan_fast"},
    "dal_basso": {"static", "tilt_up"},
    "dall_alto_90": {"static", "tilt_down"},
}

FACE_ARCHETYPES = {"hook", "reaction", "cta"}
FOOD_ARCHETYPES = {"product", "detail"}


def load_bible(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def build_archetype_profiles(bible: dict) -> dict:
    recipes_by_type: dict[str, list[dict]] = {a: [] for a in ARCHETYPES}
    for r in bible.get("clone_recipes", []):
        t = r.get("type")
        if t in recipes_by_type:
            recipes_by_type[t].append(r)

    profiles = {}
    for a in ARCHETYPES:
        meta = bible.get("archetypes", {}).get(a, {})
        dr = meta.get("duration_range_sec") or [1.5, 3.0]
        rs = recipes_by_type[a]
        durations = [r.get("duration_sec") for r in rs
                     if isinstance(r.get("duration_sec"), (int, float))]
        dmean = sum(durations) / len(durations) if durations else (dr[0] + dr[1]) / 2.0

        def dist(key):
            d = {}
            for r in rs:
                v = r.get(key)
                if v is None:
                    continue
                d[v] = d.get(v, 0) + 1
            return d

        fv = [r.get("food_visible") for r in rs if r.get("food_visible") is not None]
        food_ratio = (sum(1 for x in fv if x) / len(fv)) if fv else 0.5

        profiles[a] = {
            "duration_range": tuple(dr),
            "duration_mean": dmean,
            "shot_type_dist": dist("shot_type"),
            "motion_dist": dist("movement_speed"),
            "angle_dist": dist("camera_angle"),
            "food_visible_ratio": food_ratio,
            "narrative_note": meta.get("note", ""),
            "variants": meta.get("variants", []),
        }
    return profiles


def _subclip_metrics(sc: dict) -> dict:
    m = sc.get("metrics") or {}
    return {**sc, **m}


def _subclip_duration(sc: dict) -> float:
    if isinstance(sc.get("duration"), (int, float)):
        return float(sc["duration"])
    if "start" in sc and "end" in sc:
        return float(sc["end"]) - float(sc["start"])
    return 0.0


def score_subclip(subclip: dict, archetype: str, profiles: dict, bible: dict) -> dict:
    m = _subclip_metrics(subclip)
    dur = _subclip_duration(subclip)
    prof = profiles[archetype]
    dr = prof["duration_range"]

    if m.get("is_poor_quality"):
        return {"score": 0.0, "breakdown": {"poor_quality": True}, "hard_fail": True}
    stab = float(m.get("stability_score") or 0.0)
    blur = float(m.get("blur_score") or 0.0)
    if stab < 40:
        return {"score": 0.0, "breakdown": {"low_stability": stab}, "hard_fail": True}
    if 0 < blur < 30:
        return {"score": 0.0, "breakdown": {"low_blur": blur}, "hard_fail": True}

    sigma = max((dr[1] - dr[0]) / 2.0, 0.4)
    d_score = math.exp(-((dur - prof["duration_mean"]) ** 2) / (2 * sigma ** 2))

    motion = (m.get("motion_type") or "").lower()
    allowed = set()
    for st in prof["shot_type_dist"]:
        allowed |= SHOT_TYPE_TO_MOTIONS.get(st, set())
    if not allowed:
        allowed = {"static", "handheld", "pan_slow"}
    motion_score = 1.0 if motion in allowed else 0.3

    face = float(m.get("face_ratio") or 0.0)
    face_score = min(face * 2.0, 1.0) if archetype in FACE_ARCHETYPES else 1.0 - min(face, 1.0)

    dom_comp = bible.get("visual", {}).get("dominant_composition")
    comp = (m.get("composition") or "").lower()
    comp_score = 1.0 if comp == dom_comp else 0.7

    stab_n = min(stab / 100.0, 1.0)
    blur_n = min(blur / 500.0, 1.0)

    total = (
        0.30 * d_score
        + 0.25 * motion_score
        + 0.20 * face_score
        + 0.10 * comp_score
        + 0.10 * stab_n
        + 0.05 * blur_n
    )
    return {
        "score": round(total, 4),
        "breakdown": {
            "duration": round(d_score, 3),
            "motion": motion_score,
            "face": round(face_score, 3),
            "composition": comp_score,
            "stability_n": round(stab_n, 3),
            "blur_n": round(blur_n, 3),
        },
        "hard_fail": False,
    }


def assign_archetype(subclip: dict, profiles: dict, bible: dict) -> dict:
    ranking = []
    for a in ARCHETYPES:
        r = score_subclip(subclip, a, profiles, bible)
        if r["hard_fail"]:
            return {"archetype": None, "score": 0.0, "ranking": [], "reason": "hard_fail"}
        ranking.append((a, r["score"]))
    ranking.sort(key=lambda x: -x[1])
    return {"archetype": ranking[0][0], "score": ranking[0][1], "ranking": ranking}


def build_prompt(subclip: dict, archetype: str, profiles: dict, bible: dict) -> str:
    m = _subclip_metrics(subclip)
    prof = profiles[archetype]
    dr = prof["duration_range"]
    dur = _subclip_duration(subclip)

    gergo = []
    for v in bible.get("archetypes", {}).get("cta", {}).get("variants", []):
        if "CLA-MO-RO-SO" in v or "sillab" in v.lower():
            gergo.append(v)
    vocab = bible.get("voice", {}).get("vocabulary_combined", [])
    if isinstance(vocab, list):
        gergo.extend([w for w in vocab if isinstance(w, str) and "-" in w][:5])

    subtitles = bible.get("subtitles", {})
    voice = bible.get("voice", {})
    mix = bible.get("audio_mix", {})

    lines = [
        f"ARCHETYPE: {archetype}",
        f"DURATION: {dur:.2f}s (target {dr[0]}-{dr[1]}s, mean {prof['duration_mean']:.2f}s)",
        f"NARRATIVE_ROLE: {prof['narrative_note']}",
        f"SHOT_TYPE_TOP: {sorted(prof['shot_type_dist'].items(), key=lambda x: -x[1])[:3]}",
        f"MOTION_TOP: {sorted(prof['motion_dist'].items(), key=lambda x: -x[1])[:2]}",
        f"ANALYZED_MOTION: {m.get('motion_type')}",
        f"ANALYZED_COMPOSITION: {m.get('composition')}",
        f"FACE_RATIO: {m.get('face_ratio')}",
        f"FOOD_VISIBLE_EXPECTED: {prof['food_visible_ratio'] >= 0.5}",
        f"SUBTITLES: {subtitles.get('style','word-by-word')}, uppercase, thick black outline",
        f"VOICE: {voice.get('wpm','243')} WPM, avg segment {voice.get('avg_segment_duration_sec','~3')}s",
        f"AUDIO_MIX: voice {mix.get('voice_pct')}% + ambient {mix.get('ambient_pct')}% + asmr {mix.get('asmr_pct')}% (NO music)",
        f"TRANSITION: hard cut only",
    ]
    if gergo:
        lines.append("GERGO_VERBATIM (do not translate): " + " | ".join(gergo))
    if prof["variants"]:
        lines.append("VARIANTS_REFERENCE: " + " | ".join(prof["variants"][:3]))
    return "\n".join(lines)


def _normalize_subclips(manifest: Any) -> list[dict]:
    if isinstance(manifest, list):
        return manifest
    for key in ("subclips", "clips", "shots", "items"):
        if isinstance(manifest.get(key), list):
            return manifest[key]
    return []


def _dist(items):
    d = {}
    for x in items:
        d[x] = d.get(x, 0) + 1
    return d


def curate(manifest_path: str, bible_path: str, out_path: str) -> dict:
    bible = load_bible(bible_path)
    profiles = build_archetype_profiles(bible)
    with open(manifest_path) as f:
        manifest = json.load(f)

    subclips = _normalize_subclips(manifest)
    curated = []
    for sc in subclips:
        a = assign_archetype(sc, profiles, bible)
        if a["archetype"] is None:
            continue
        curated.append({
            "id": sc.get("id") or f"{sc.get('video','')}_{sc.get('start',0)}",
            "video": sc.get("video") or manifest.get("video"),
            "start": sc.get("start"),
            "end": sc.get("end"),
            "duration": _subclip_duration(sc),
            "archetype": a["archetype"],
            "score": a["score"],
            "ranking": a["ranking"][:3],
            "prompt": build_prompt(sc, a["archetype"], profiles, bible),
            "sheet_path": sc.get("sheet_path"),
            "audio_path": sc.get("audio_path"),
        })

    curated.sort(key=lambda x: -x["score"])

    out = {
        "schema_version": 1,
        "source_manifest": os.path.basename(manifest_path),
        "bible_version": bible.get("schema_version"),
        "n_subclips_in": len(subclips),
        "n_subclips_out": len(curated),
        "archetype_distribution": _dist([c["archetype"] for c in curated]),
        "items": curated,
    }
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("manifest")
    ap.add_argument("--bible", default="/opt/reel-agent/media/ground_truth/style_bible.json")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    out = args.out or args.manifest.replace(".json", ".curated.json")
    r = curate(args.manifest, args.bible, out)
    print(json.dumps({
        "out": out,
        "n_in": r["n_subclips_in"],
        "n_out": r["n_subclips_out"],
        "archetype_distribution": r["archetype_distribution"],
    }, indent=2))


if __name__ == "__main__":
    main()

# Alias retro-compatibile con server.py che importa 'curate_clips'
curate_clips = curate

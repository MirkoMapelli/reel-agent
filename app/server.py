"""reel-agent — server Flask."""
import os
import time
import re
import shutil
import subprocess
import glob
from app.workers.style_learn import run_style_learn
from app.workers.font_preview import render_preview
from app.workers.analyze_raw import run_analyze_raw
import json
from flask import Response, request, send_file
from app.jobs import manager
from app.workers.download_refs import download_references
from flask import Flask, render_template, jsonify, request
from flask import send_from_directory
from app.workers.tiktok_fetch import list_tiktok_videos
from app.workers.curate_clips import curate_clips
from app.workers.generate_edl import generate_edl
from app.workers.render_final import render_final
from app.workers.assemble_voice import assemble_voiceover
from app.workers.reburn_subtitles import reburn_subtitles
from flask import Flask, render_template, jsonify
from app.config import CFG, require_api_key
from app.db import init_db, get_conn

app = Flask(
    __name__,
    template_folder="templates",
    static_folder="static",
)

@app.route("/")
def index():
    return render_template("index.html", version=CFG["APP_VERSION"])

@app.route("/api/health")
def health():
    api_ok = bool(CFG["ANTHROPIC_API_KEY"]) and not CFG["ANTHROPIC_API_KEY"].startswith("incolla")
    db_ok = False
    try:
        with get_conn() as conn:
            conn.execute("SELECT 1").fetchone()
        db_ok = True
    except Exception:
        pass
    return jsonify({
        "status": "ok",
        "version": CFG["APP_VERSION"],
        "anthropic_key": api_ok,
        "db": db_ok,
    })


@app.route("/tiktok_cache/<path:filename>")
def serve_tiktok_cache(filename):
    cache_dir = os.path.join(CFG["MEDIA_DIR"], "tiktok_cache")
    return send_from_directory(cache_dir, filename)

@app.route("/api/tiktok/list", methods=["POST"])
def tiktok_list():
    data = request.json or {}
    username = (data.get("username") or "").strip()
    max_videos = int(data.get("max_videos", 20))
    if not username:
        return jsonify({"error": "Username TikTok richiesto."}), 400
    try:
        videos = list_tiktok_videos(username, max_videos)
        return jsonify({"videos": videos, "count": len(videos)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# === Job API ===

@app.route("/api/jobs/tiktok/import", methods=["POST"])
def api_tiktok_import():
    """Avvia job di download dei riferimenti selezionati."""
    data = request.json or {}
    videos = data.get("videos", [])
    if not videos:
        return jsonify({"error": "Nessun video selezionato."}), 400

    job_id = manager.start_job(
        job_type="download_refs",
        payload={"count": len(videos)},
        fn=download_references,
        videos=videos,
    )
    return jsonify({"job_id": job_id})


@app.route("/api/jobs/<int:job_id>")
def api_job_get(job_id):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            return jsonify({"error": "job inesistente"}), 404
        logs = conn.execute(
            "SELECT level, message, created_at FROM job_logs WHERE job_id=? ORDER BY id DESC LIMIT 100",
            (job_id,),
        ).fetchall()
    return jsonify({
        "id": row["id"],
        "type": row["type"],
        "status": row["status"],
        "progress": row["progress"],
        "label": row["step_label"],
        "error": row["error"],
        "result": json.loads(row["result_json"]) if row["result_json"] else None,
        "logs": [dict(l) for l in logs],
    })


@app.route("/api/jobs/<int:job_id>/stream")
def api_job_stream(job_id):
    return Response(
        manager.sse_stream(job_id),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# === Style learning ===

@app.route("/api/styles/learn", methods=["POST"])
def api_styles_learn():
    data = request.json or {}
    profile_name = (data.get("profile_name") or "").strip()
    tiktok_account = (data.get("tiktok_account") or "").strip().lstrip("@")

    if not profile_name:
        return jsonify({"error": "Nome profilo richiesto."}), 400

    # Prende tutti i video in media/style_ref/
    video_paths = sorted(glob.glob(os.path.join(CFG["STYLE_REF_DIR"], "*.mp4")))
    if not video_paths:
        return jsonify({"error": "Nessun video di riferimento in media/style_ref/."}), 400

    job_id = manager.start_job(
        job_type="style_learn",
        payload={"profile_name": profile_name, "videos": len(video_paths)},
        fn=run_style_learn,
        profile_name=profile_name,
        tiktok_account=tiktok_account,
        video_paths=video_paths,
    )
    return jsonify({"job_id": job_id, "videos": len(video_paths)})


@app.route("/api/styles", methods=["GET"])
def api_styles_list():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, name, tiktok_account, created_at FROM profiles ORDER BY id DESC"
        ).fetchall()
    return jsonify({"profiles": [dict(r) for r in rows]})


@app.route("/api/styles/<int:profile_id>", methods=["GET"])
def api_styles_get(profile_id):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM profiles WHERE id=?", (profile_id,)).fetchone()
    if not row:
        return jsonify({"error": "profilo inesistente"}), 404
    return jsonify({
        "id": row["id"],
        "name": row["name"],
        "tiktok_account": row["tiktok_account"],
        "created_at": row["created_at"],
        "style": json.loads(row["style_json"]),
    })



@app.route("/api/styles/<int:profile_id>/font-previews")
def api_font_previews(profile_id):
    with get_conn() as conn:
        row = conn.execute("SELECT style_json FROM profiles WHERE id=?", (profile_id,)).fetchone()
    if not row:
        return jsonify({"error": "profilo inesistente"}), 404

    style = json.loads(row["style_json"])
    sub = style.get("subtitle_style", {})
    top = sub.get("font_top20", []) or sub.get("font_top3", [])
    samples = sub.get("sample_texts", ["ANTEPRIMA FONT"])[:3]
    text = samples[0] if samples else "ANTEPRIMA FONT"

    primary = sub.get("primary_color_ass", "&H00FEFEFE&")
    outline = sub.get("outline_color_ass", "&H00020203&")

    def ass_to_hex(ass: str) -> str:
        s = ass.strip("&").lstrip("H").lstrip("h")
        if len(s) < 8:
            return "#FFFFFF"
        bb, gg, rr = s[2:4], s[4:6], s[6:8]
        return f"#{rr}{gg}{bb}"

    color_hex = ass_to_hex(primary)
    outline_hex = ass_to_hex(outline)

    out = []
    for i, f in enumerate(top):
        preview = render_preview(text, f["path"], color_hex=color_hex, outline_hex=outline_hex)
        out.append({
            "rank": i + 1,
            "name": f["name"],
            "path": f["path"],
            "score": f.get("score"),
            "ssim": f.get("ssim"),
            "ar": f.get("ar"),
            "ink": f.get("ink"),
            "preview": preview,
            "is_best": i == 0,
        })
    return jsonify({"text": text, "fonts": out})


@app.route("/api/styles/<int:profile_id>/set-font", methods=["POST"])
def api_set_font(profile_id):
    data = request.json or {}
    new_name = (data.get("font_name") or "").strip()
    if not new_name:
        return jsonify({"error": "font_name richiesto"}), 400

    with get_conn() as conn:
        row = conn.execute("SELECT style_json FROM profiles WHERE id=?", (profile_id,)).fetchone()
        if not row:
            return jsonify({"error": "profilo inesistente"}), 404
        style = json.loads(row["style_json"])
        style.setdefault("subtitle_style", {})["font_name"] = new_name
        style["subtitle_style"]["font_overridden"] = True
        conn.execute(
            "UPDATE profiles SET style_json=?, updated_at=datetime('now') WHERE id=?",
            (json.dumps(style, ensure_ascii=False), profile_id),
        )
    return jsonify({"status": "ok", "font_name": new_name})


@app.route("/api/styles/<int:profile_id>", methods=["DELETE"])
def api_styles_delete(profile_id):
    with get_conn() as conn:
        row = conn.execute("SELECT id FROM profiles WHERE id=?", (profile_id,)).fetchone()
        if not row:
            return jsonify({"error": "profilo inesistente"}), 404
        conn.execute("DELETE FROM profiles WHERE id=?", (profile_id,))
    fp = os.path.join(CFG["STYLES_DIR"], f"{profile_id}.json")
    if os.path.exists(fp):
        try:
            os.remove(fp)
        except Exception:
            pass
    return jsonify({"status": "ok", "deleted_id": profile_id})


@app.route("/api/style_ref/clear", methods=["POST"])
def api_style_ref_clear():
    import glob as _glob
    d = CFG["STYLE_REF_DIR"]
    files = _glob.glob(os.path.join(d, "*"))
    count = 0
    for f in files:
        if os.path.isfile(f):
            try:
                os.remove(f)
                count += 1
            except Exception:
                pass
    return jsonify({"status": "ok", "message": f"Eliminati {count} video di riferimento."})


@app.route("/api/raw/upload", methods=["POST"])
def api_raw_upload():
    if "files" not in request.files:
        return jsonify({"error": "Nessun file inviato."}), 400
    files = request.files.getlist("files")
    saved = []
    for file in files:
        if file and file.filename:
            safe = os.path.basename(file.filename)
            path = os.path.join(CFG["RAW_DIR"], safe)
            file.save(path)
            saved.append(safe)
    if not saved:
        return jsonify({"error": "Nessun file valido."}), 400
    return jsonify({"status": "ok", "files": saved, "count": len(saved)})


@app.route("/api/raw/list", methods=["GET"])
def api_raw_list():
    import glob as _glob
    files = sorted(_glob.glob(os.path.join(CFG["RAW_DIR"], "*")))
    out = []
    for f in files:
        if not os.path.isfile(f):
            continue
        try:
            size_mb = round(os.path.getsize(f) / (1024 * 1024), 1)
        except Exception:
            size_mb = 0
        out.append({
            "name": os.path.basename(f),
            "size_mb": size_mb,
        })
    return jsonify({"files": out, "count": len(out)})


@app.route("/api/raw/delete", methods=["POST"])
def api_raw_delete():
    data = request.json or {}
    name = os.path.basename((data.get("name") or "").strip())
    if not name:
        return jsonify({"error": "Nome file richiesto."}), 400
    path = os.path.join(CFG["RAW_DIR"], name)
    if not os.path.exists(path):
        return jsonify({"error": "File inesistente."}), 404
    try:
        os.remove(path)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"status": "ok", "deleted": name})


@app.route("/api/raw/clear", methods=["POST"])
def api_raw_clear():
    import glob as _glob
    count = 0
    for f in _glob.glob(os.path.join(CFG["RAW_DIR"], "*")):
        if os.path.isfile(f):
            try:
                os.remove(f)
                count += 1
            except Exception:
                pass
    return jsonify({"status": "ok", "message": f"Eliminati {count} file raw."})


@app.route("/api/raw/analyze", methods=["POST"])
def api_raw_analyze():
    import glob as _glob
    vids = sorted(_glob.glob(os.path.join(CFG["RAW_DIR"], "*")))
    vids = [v for v in vids if os.path.isfile(v) and v.lower().endswith((".mp4", ".mov", ".mkv", ".webm", ".avi"))]
    if not vids:
        return jsonify({"error": "Nessun video raw in media/raw/."}), 400

    job_id = manager.start_job(
        job_type="analyze_raw",
        payload={"videos": len(vids)},
        fn=run_analyze_raw,
        video_paths=vids,
    )
    return jsonify({"job_id": job_id, "videos": len(vids)})


@app.route("/api/analysis/list", methods=["GET"])
def api_analysis_list():
    import glob as _glob
    d = os.path.join(CFG["MEDIA_DIR"], "analysis")
    if not os.path.isdir(d):
        return jsonify({"manifests": []})
    files = sorted(_glob.glob(os.path.join(d, "job_*_manifest.json")), reverse=True)
    out = []
    for f in files[:20]:
        try:
            with open(f) as fh:
                m = json.load(fh)
            out.append({
                "file": os.path.basename(f),
                "total": m.get("total"),
                "videos": len(m.get("videos", [])),
            })
        except Exception:
            pass
    return jsonify({"manifests": out})


@app.route("/api/curate/run", methods=["POST"])
def api_curate_run():
    data = request.json or {}
    profile_id = data.get("profile_id")
    if profile_id:
        try:
            profile_id = int(profile_id)
        except Exception:
            return jsonify({"error": "profile_id non valido"}), 400
    else:
        profile_id = None

    job_id = manager.start_job(
        job_type="curate_clips",
        payload={"profile_id": profile_id},
        fn=curate_clips,
        profile_id=profile_id,
    )
    return jsonify({"job_id": job_id})


@app.route("/api/curated/latest", methods=["GET"])
def api_curated_latest():
    d = os.path.join(CFG["MEDIA_DIR"], "curated")
    if not os.path.isdir(d):
        return jsonify({"exists": False})
    files = sorted(
        [os.path.join(d, f) for f in os.listdir(d)
         if f.startswith("job_") and f.endswith("_curated.json")],
        key=os.path.getmtime, reverse=True,
    )
    if not files:
        return jsonify({"exists": False})
    with open(files[0]) as f:
        data = json.load(f)
    return jsonify({
        "exists": True,
        "file": os.path.basename(files[0]),
        "total": data.get("total"),
        "keep": data.get("keep"),
        "skip": data.get("skip"),
        "generated_at": data.get("generated_at"),
    })


@app.route("/api/render/run", methods=["POST"])
def api_render_run():
    data = request.json or {}
    pid = data.get("profile_id")
    if not pid:
        return jsonify({"error": "profile_id richiesto"}), 400
    try:
        pid = int(pid)
    except Exception:
        return jsonify({"error": "profile_id non valido"}), 400

    job_id = manager.start_job(
        job_type="render_final",
        payload={"profile_id": pid},
        fn=render_final,
        profile_id=pid,
    )
    return jsonify({"job_id": job_id, "profile_id": pid})


@app.route("/api/render/latest")
def api_render_latest():
    import glob as _glob
    files = sorted(_glob.glob(os.path.join(CFG["RENDERS_DIR"], "render_*.mp4")),
                   key=os.path.getmtime, reverse=True)
    if not files:
        return jsonify({"exists": False})
    f = files[0]
    base = os.path.basename(f)
    job_id = base.replace("render_", "").replace(".mp4", "")
    srt = os.path.join(CFG["RENDERS_DIR"], f"render_{job_id}.srt")
    vo = os.path.join(CFG["RENDERS_DIR"], f"render_{job_id}_voiceover.txt")
    size_mb = round(os.path.getsize(f) / (1024 * 1024), 1)
    vo_text = ""
    if os.path.exists(vo):
        with open(vo) as fh:
            vo_text = fh.read()
    return jsonify({
        "exists": True,
        "job_id": job_id,
        "file": base,
        "url": f"/renders/{base}",
        "srt_url": f"/renders/render_{job_id}.srt" if os.path.exists(srt) else None,
        "size_mb": size_mb,
        "voiceover_script": vo_text,
    })


@app.route("/blind_test")
@app.route("/blind_test/")
def blind_test_index():
    blind_dir = os.path.join(CFG["BASE_DIR"], "blind_test")
    idx = os.path.join(blind_dir, "index.html")
    if not os.path.exists(idx):
        return jsonify({"error": "blind_test non ancora generato"}), 404
    return send_file(idx)


@app.route("/blind_test/videos/<path:filename>")
def blind_test_video(filename):
    blind_videos = os.path.join(CFG["BASE_DIR"], "blind_test", "videos")
    return send_from_directory(blind_videos, filename, conditional=True)


@app.route("/brain")
def brain_page():
    p = os.path.join(CFG["APP_DIR"], "templates", "brain.html")
    if not os.path.exists(p):
        return "brain.html non trovato", 404
    return send_file(p)


@app.route("/api/brain/settings", methods=["GET"])
def api_brain_settings_get():
    sp = os.path.join(CFG["BASE_DIR"], "brain", "settings.json")
    if not os.path.exists(sp):
        return jsonify({"user_notes": "", "subtitle_style": {}})
    try:
        with open(sp) as f:
            return jsonify(json.load(f))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/brain/settings", methods=["POST"])
def api_brain_settings_post():
    sp = os.path.join(CFG["BASE_DIR"], "brain", "settings.json")
    data = request.json or {}
    try:
        with open(sp) as f:
            current = json.load(f)
    except Exception:
        current = {"user_notes": "", "subtitle_style": {}}

    if "user_notes" in data:
        current["user_notes"] = (data["user_notes"] or "")[:8000]
    if "subtitle_style" in data and isinstance(data["subtitle_style"], dict):
        current.setdefault("subtitle_style", {}).update(data["subtitle_style"])

    from datetime import datetime
    current["updated_at"] = datetime.now().isoformat(timespec="seconds")

    with open(sp, "w") as f:
        json.dump(current, f, indent=2, ensure_ascii=False)
    return jsonify({"ok": True, "settings": current})


@app.route("/api/brain/state", methods=["GET"])
def api_brain_state():
    """Stato completo: versione attiva, corpus, versioni disponibili."""
    brain_dir = os.path.join(CFG["BASE_DIR"], "brain")
    versions_dir = os.path.join(brain_dir, "versions")
    archive_dir = os.path.join(versions_dir, "_archive")
    corpus_dir = os.path.join(brain_dir, "corpus")

    active_ver = None
    p = os.path.join(brain_dir, "active_version.txt")
    if os.path.exists(p):
        active_ver = open(p).read().strip()

    # Versione attiva dettagli
    active_info = {}
    ab = os.path.join(brain_dir, "active_brain.json")
    if os.path.exists(ab):
        try:
            b = json.load(open(ab))
            active_info = {
                "schema_version": b.get("schema_version"),
                "generated_at": b.get("generated_at"),
                "primary_creator": b.get("primary_creator"),
                "corpus": b.get("corpus"),
            }
        except Exception:
            pass

    # Lista versioni visibili
    versions = []
    if os.path.isdir(versions_dir):
        for f in sorted(os.listdir(versions_dir)):
            if f.startswith("creator_brain_v") and f.endswith(".json"):
                fp = os.path.join(versions_dir, f)
                m = re.search(r"v0\.(\d+)\.json$", f)
                ver = f"v0.{m.group(1)}" if m else f
                versions.append({
                    "version": ver,
                    "file": f,
                    "size_bytes": os.path.getsize(fp),
                    "mtime": os.path.getmtime(fp),
                    "is_active": ver == active_ver,
                })
    versions.sort(key=lambda x: x["mtime"], reverse=True)

    n_archive = 0
    archived = []
    if os.path.isdir(archive_dir):
        for f in sorted(os.listdir(archive_dir)):
            if not f.endswith(".json"):
                continue
            if f.startswith("legacy_pre_corpus"):
                # legacy: mostra come "legacy-v0.1"
                fp = os.path.join(archive_dir, f)
                archived.append({
                    "version": "legacy-v0.1",
                    "file": f,
                    "size_bytes": os.path.getsize(fp),
                    "mtime": os.path.getmtime(fp),
                    "is_active": False,
                    "is_archived": True,
                    "is_legacy": True,
                })
                continue
            if not f.startswith("creator_brain_v"):
                continue
            fp = os.path.join(archive_dir, f)
            m = re.search(r"v0\.(\d+)\.json$", f)
            ver = f"v0.{m.group(1)}" if m else f
            archived.append({
                "version": ver,
                "file": f,
                "size_bytes": os.path.getsize(fp),
                "mtime": os.path.getmtime(fp),
                "is_active": ver == active_ver,
                "is_archived": True,
            })
        archived.sort(key=lambda x: x["mtime"], reverse=True)
        n_archive = len(archived)

    # Corpus
    creators = []
    if os.path.isdir(corpus_dir):
        for c in sorted(os.listdir(corpus_dir)):
            cdir = os.path.join(corpus_dir, c)
            vdir = os.path.join(cdir, "videos")
            if not os.path.isdir(vdir):
                continue
            n_v = 0
            n_a = 0
            n_vfiles = 0
            for vid in os.listdir(vdir):
                vd = os.path.join(vdir, vid)
                if os.path.isdir(vd):
                    n_v += 1
                    if os.path.exists(os.path.join(vd, "analysis.json")):
                        n_a += 1
                    if os.path.exists(os.path.join(vd, "video.mp4")):
                        n_vfiles += 1
            creators.append({
                "creator": c,
                "n_videos": n_v,
                "n_video_files": n_vfiles,
                "n_analyzed": n_a,
                "is_primary": c == "jay.emme",
            })

    return jsonify({
        "active_version": active_ver,
        "active_info": active_info,
        "versions": versions,
        "archived_versions": archived,
        "n_archive": n_archive,
        "creators": creators,
    })


@app.route("/api/brain/catalog_state", methods=["GET"])
def api_brain_catalog_state():
    """Ritorna stato cataloghi per ogni creator."""
    import json as _json
    corpus_dir = os.path.join(CFG["BASE_DIR"], "brain", "corpus")
    out = []
    if os.path.isdir(corpus_dir):
        for c in sorted(os.listdir(corpus_dir)):
            cpath = os.path.join(corpus_dir, c, '_catalog.json')
            vpath = os.path.join(corpus_dir, c, 'videos')
            # Conta video.mp4 e analysis.json effettivamente su disco
            n_video_files = 0
            n_analysis = 0
            if os.path.isdir(vpath):
                for vd in os.listdir(vpath):
                    vdir = os.path.join(vpath, vd)
                    if not os.path.isdir(vdir):
                        continue
                    if os.path.exists(os.path.join(vdir, 'video.mp4')):
                        n_video_files += 1
                    if os.path.exists(os.path.join(vdir, 'analysis.json')):
                        n_analysis += 1

            entry = {
                'creator': c,
                'n_video_files': n_video_files,
                'n_analysis': n_analysis,
            }
            if os.path.exists(cpath):
                try:
                    d = _json.load(open(cpath))
                    entry['n_total'] = d.get('n_total', 0)
                    entry['updated_at'] = d.get('updated_at')
                    entry['last_full_scan'] = d.get('last_full_scan')
                except Exception:
                    entry['n_total'] = 0
            else:
                entry['n_total'] = 0
                entry['updated_at'] = None
            out.append(entry)
    return jsonify({"catalogs": out})


@app.route("/api/brain/delete_videos", methods=["POST"])
def api_brain_delete_videos():
    data = request.json or {}
    handle = (data.get("handle") or "").strip()
    if not handle:
        return jsonify({"error": "handle richiesto"}), 400
    if handle == "jay.emme":
        return jsonify({"error": "NON puoi cancellare video di jay.emme"}), 400
    try:
        from app.workers.brain_ops import run_delete_videos
    except Exception as e:
        return jsonify({"error": f"import: {e}"}), 500
    job_id = manager.start_job(
        job_type="brain_delete_videos",
        payload={"handle": handle},
        fn=run_delete_videos,
        handle=handle,
    )
    return jsonify({"job_id": job_id, "handle": handle})


@app.route("/api/subs/fonts", methods=["GET"])
def api_subs_fonts():
    """Ritorna lista font disponibili (Obelix + altri)."""
    import subprocess
    fonts = []
    try:
        r = subprocess.run(['fc-list', '--format', '%{family}\n'], capture_output=True, text=True, timeout=5)
        seen = set()
        for line in (r.stdout or '').split('\n'):
            fam = line.strip()
            if not fam or ',' in fam:
                continue
            if fam in seen:
                continue
            seen.add(fam)
            # Priorità: Obelix primo
            fonts.append({'family': fam, 'label': fam})
    except Exception:
        pass

    # Ordina: Obelix per primo, poi Noto, poi resto
    def sort_key(f):
        fam = f['family']
        if 'obelix' in fam.lower():
            return (0, fam)
        if 'noto' in fam.lower():
            return (1, fam)
        return (2, fam)
    fonts.sort(key=sort_key)
    return jsonify({"fonts": fonts[:60]})


@app.route("/api/subs/font-file/<path:family>")
def api_subs_font_file(family):
    """Serve il file .ttf del font richiesto (per @font-face nel browser)."""
    import subprocess
    try:
        r = subprocess.run(['fc-match', '-f', '%{file}', family],
                           capture_output=True, text=True, timeout=5)
        path = (r.stdout or '').strip()
        if path and os.path.exists(path):
            return send_file(path, mimetype='font/ttf')
    except Exception:
        pass
    return jsonify({"error": "font non trovato"}), 404


@app.route("/api/subs/upload", methods=["POST"])
def api_subs_upload():
    if "file" not in request.files:
        return jsonify({"error": "nessun file"}), 400
    f = request.files["file"]
    if not f.filename:
        return jsonify({"error": "nome vuoto"}), 400

    upload_dir = os.path.join(CFG["MEDIA_DIR"], "simple_subs", "_uploads")
    os.makedirs(upload_dir, exist_ok=True)
    safe = re.sub(r'[^A-Za-z0-9._-]', '_', f.filename)[:80]
    tmp_path = os.path.join(upload_dir, f"{int(time.time())}_{safe}")
    f.save(tmp_path)

    # Options (font, colors, size, ecc.)
    opts = {}
    raw_opts = request.form.get("options")
    if raw_opts:
        try:
            opts = json.loads(raw_opts)
        except Exception:
            pass

    try:
        from app.workers.simple_subs import run_simple_subtitles
    except Exception as e:
        return jsonify({"error": f"import: {e}"}), 500

    job_id = manager.start_job(
        job_type="simple_subs",
        payload={"filename": f.filename, "options": opts},
        fn=run_simple_subtitles,
        video_path=tmp_path,
        options=opts,
    )
    return jsonify({"job_id": job_id, "filename": f.filename})


@app.route("/api/subs/result/<int:job_id>", methods=["GET"])
def api_subs_result(job_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT status, result_json, error FROM jobs WHERE id=?", (job_id,)
        ).fetchone()
    if not row:
        return jsonify({"error": "job non trovato"}), 404
    res = {}
    if row["result_json"]:
        try: res = json.loads(row["result_json"])
        except Exception: pass
    return jsonify({
        "status": row["status"],
        "error": row["error"],
        "result": res,
        "video_url": f"/api/subs/video/{job_id}" if row["status"] == "done" else None,
    })


@app.route("/api/subs/video/<int:job_id>")
def api_subs_video(job_id):
    p = os.path.join(CFG["MEDIA_DIR"], "simple_subs", f"job_{job_id}", "output.mp4")
    if not os.path.exists(p):
        return jsonify({"error": "non disponibile"}), 404
    return send_file(p, mimetype="video/mp4", conditional=True)


@app.route("/api/brain/generate_from_master", methods=["POST"])
def api_brain_from_master():
    """Approccio B: concat raw -> Gemini editor -> EDL."""
    data = request.json or {}
    profile_id = data.get("profile_id") or 16
    venue_name = (data.get("venue_name") or "Wing Stop Milano").strip()[:80]
    # Lista raw (opzionale): se assente, usa i primi 10 raw ordinati
    raw_list = data.get("raws")

    raw_dir = CFG["RAW_DIR"]
    if not raw_list:
        files = sorted([
            f for f in os.listdir(raw_dir)
            if f.lower().endswith((".mp4", ".mov", ".mkv"))
        ])
        raw_list = files[:10]
    else:
        raw_list = [Path(f).name for f in raw_list]

    raw_paths = [os.path.join(raw_dir, f) for f in raw_list if os.path.exists(os.path.join(raw_dir, f))]
    if len(raw_paths) < 3:
        return jsonify({"error": f"servono almeno 3 raw validi, trovati {len(raw_paths)}"}), 400

    try:
        from app.workers.brain_edit_from_master import run_brain_edit_from_master
    except Exception as e:
        return jsonify({"error": f"import: {e}"}), 500

    job_id = manager.start_job(
        job_type="brain_edit_from_master",
        payload={"n_raws": len(raw_paths), "profile_id": profile_id, "raws": raw_list},
        fn=run_brain_edit_from_master,
        raw_paths=raw_paths,
        profile_id=profile_id,
        venue_name=venue_name,
    )
    return jsonify({"job_id": job_id, "n_raws": len(raw_paths), "engine": "brain_edit_from_master"})


@app.route("/api/studio/keyframe")
def api_studio_keyframe():
    """Serve un keyframe dato il path assoluto (whitelist: solo sotto MEDIA_DIR)."""
    path = request.args.get("path", "")
    if not path:
        return jsonify({"error": "path mancante"}), 400
    media_root = os.path.realpath(CFG["MEDIA_DIR"])
    abs_path = os.path.realpath(path)
    if not abs_path.startswith(media_root + os.sep):
        return jsonify({"error": "path non consentito"}), 403
    if not os.path.exists(abs_path):
        return jsonify({"error": "non trovato"}), 404
    return send_file(abs_path, mimetype="image/jpeg")


_raw_dur_cache = {}

@app.route("/api/studio/raw_durations", methods=["POST"])
def api_studio_raw_durations():
    """Ritorna durate raw in secondi. Input: {names: [...]}."""
    data = request.json or {}
    names = data.get("names", [])
    out = {}
    raw_dir = CFG["RAW_DIR"]
    for n in names:
        if n in _raw_dur_cache:
            out[n] = _raw_dur_cache[n]
            continue
        path = os.path.join(raw_dir, n)
        if not os.path.exists(path):
            out[n] = 0
            continue
        try:
            r = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries",
                 "format=duration", "-of",
                 "default=noprint_wrappers=1:nokey=1", path],
                capture_output=True, text=True, timeout=10)
            dur = float(r.stdout.strip())
        except Exception:
            dur = 0
        _raw_dur_cache[n] = dur
        out[n] = dur
    return jsonify(out)


@app.route("/api/studio/frame")
def api_studio_frame():
    """Estrae il frame del raw a un timestamp specifico (per handle in/out)."""
    clip_name = request.args.get("clip_name", "")
    t = request.args.get("t", "")
    if not clip_name or t == "":
        return jsonify({"error": "clip_name e t richiesti"}), 400
    try:
        t = float(t)
    except Exception:
        return jsonify({"error": "t non valido"}), 400
    try:
        from app.workers.studio import ensure_frame_at
    except Exception as e:
        return jsonify({"error": f"import: {e}"}), 500
    path = ensure_frame_at(clip_name, t)
    if not path:
        return jsonify({"error": "frame non disponibile"}), 404
    return send_file(path, mimetype="image/jpeg")


@app.route("/api/studio/filmstrip", methods=["POST"])
def api_studio_filmstrip():
    """Ritorna filmstrip per un clip. Input: {clip_name, n?}."""
    data = request.json or {}
    clip_name = data.get("clip_name", "")
    n = int(data.get("n", 10))
    if not clip_name:
        return jsonify({"error": "clip_name mancante"}), 400
    try:
        from app.workers.studio import ensure_filmstrip
    except Exception as e:
        return jsonify({"error": f"import: {e}"}), 500
    res = ensure_filmstrip(clip_name, n=n)
    return jsonify(res)


@app.route("/api/studio/edls", methods=["GET"])
def api_studio_edls_list():
    """Lista di tutti gli EDL disponibili (filename, mtime, n_clips)."""
    edl_dir = os.path.join(CFG["MEDIA_DIR"], "edl")
    out = []
    if os.path.isdir(edl_dir):
        for f in os.listdir(edl_dir):
            if not (f.startswith("job_") and f.endswith("_edl.json")):
                continue
            fp = os.path.join(edl_dir, f)
            try:
                import json as _json
                d = _json.load(open(fp))
                out.append({
                    "filename": f,
                    "n_clips": d.get("n_clips"),
                    "duration": d.get("total_duration"),
                    "title": (d.get("title") or "")[:60],
                    "mtime": os.path.getmtime(fp),
                })
            except Exception:
                pass
    out.sort(key=lambda x: x["mtime"], reverse=True)
    return jsonify({"edls": out[:30]})


@app.route("/api/studio/edl/<filename>", methods=["GET"])
def api_studio_edl_by_name(filename):
    """Carica un EDL specifico per filename."""
    if not filename.startswith("job_") or not filename.endswith("_edl.json"):
        return jsonify({"error": "filename non valido"}), 400
    fp = os.path.join(CFG["MEDIA_DIR"], "edl", filename)
    if not os.path.exists(fp):
        return jsonify({"error": "non trovato"}), 404
    try:
        import json as _json
        return jsonify({"edl": _json.load(open(fp)), "filename": filename})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/studio/edl", methods=["GET"])
def api_studio_get_edl():
    """Carica l'EDL piu' recente (per editing)."""
    try:
        from app.workers.studio import load_latest_edl
    except Exception as e:
        return jsonify({"error": f"import: {e}"}), 500
    edl, name = load_latest_edl()
    if not edl:
        return jsonify({"error": "nessun EDL trovato"}), 404
    return jsonify({"edl": edl, "filename": name})


@app.route("/api/studio/edl", methods=["POST"])
def api_studio_save_edl():
    """Salva EDL modificato (sovrascrive job_999)."""
    data = request.json or {}
    edl = data.get("edl")
    if not isinstance(edl, dict):
        return jsonify({"error": "edl mancante"}), 400
    try:
        from app.workers.studio import save_edl
    except Exception as e:
        return jsonify({"error": f"import: {e}"}), 500
    path = save_edl(edl)
    return jsonify({"ok": True, "path": path})


@app.route("/api/studio/apply_mix", methods=["POST"])
def api_studio_apply_mix():
    """Applica mix finale (parlato + musica) su render esistente."""
    data = request.json or {}
    edl_job_id = data.get("edl_job_id")
    music = data.get("music") or None
    voice_vol = float(data.get("voice_vol", 1.0))
    music_vol = float(data.get("music_vol", 0.3))
    if not edl_job_id:
        return jsonify({"error": "edl_job_id richiesto"}), 400

    renders_dir = CFG["RENDERS_DIR"]
    src_path = os.path.join(renders_dir, f"render_{edl_job_id}.mp4")
    if not os.path.exists(src_path):
        return jsonify({"error": f"render_{edl_job_id}.mp4 non trovato"}), 404

    music_path = None
    if music:
        music_path = os.path.join(CFG["MEDIA_DIR"], "studio", "music", music)
        if not os.path.exists(music_path):
            return jsonify({"error": f"musica non trovata: {music}"}), 404

    out_path = os.path.join(renders_dir, f"render_{edl_job_id}_mixed.mp4")
    try:
        from app.workers.studio import apply_mix
    except Exception as e:
        return jsonify({"error": f"import: {e}"}), 500

    ok, err = apply_mix(src_path, out_path, music_path, voice_vol, music_vol)
    if not ok:
        return jsonify({"error": f"mix fallito: {err}"}), 500

    return jsonify({
        "ok": True,
        "output": out_path,
        "url": f"/renders/render_{edl_job_id}_mixed.mp4",
        "voice_vol": voice_vol,
        "music_vol": music_vol,
        "music": music,
    })


@app.route("/api/studio/music", methods=["GET"])
def api_studio_music_list():
    try:
        from app.workers.studio import list_music
    except Exception as e:
        return jsonify({"error": f"import: {e}"}), 500
    return jsonify({"tracks": list_music()})


@app.route("/api/studio/music/upload", methods=["POST"])
def api_studio_music_upload():
    if "file" not in request.files:
        return jsonify({"error": "nessun file"}), 400
    f = request.files["file"]
    if not f.filename:
        return jsonify({"error": "nome vuoto"}), 400
    try:
        from app.workers.studio import save_music
    except Exception as e:
        return jsonify({"error": f"import: {e}"}), 500
    return jsonify({"ok": True, "track": save_music(f)})


@app.route("/api/studio/music/<path:name>", methods=["DELETE"])
def api_studio_music_delete(name):
    try:
        from app.workers.studio import delete_music
    except Exception as e:
        return jsonify({"error": f"import: {e}"}), 500
    ok = delete_music(name)
    return jsonify({"ok": ok})


@app.route("/api/brain/panic", methods=["POST"])
def api_brain_panic():
    """🛑 Ferma tutto, pulisce zombie e file temporanei."""
    try:
        from app.workers.brain_ops import run_panic_cleanup
    except Exception as e:
        return jsonify({"error": f"import: {e}"}), 500
    job_id = manager.start_job(
        job_type="brain_panic",
        payload={},
        fn=run_panic_cleanup,
    )
    return jsonify({"job_id": job_id, "action": "panic_cleanup"})


@app.route("/api/brain/full_scan", methods=["POST"])
def api_brain_full_scan():
    data = request.json or {}
    handle = (data.get("handle") or "").strip()
    if not handle:
        return jsonify({"error": "handle richiesto"}), 400
    try:
        from app.workers.brain_ops import run_full_scan
    except Exception as e:
        return jsonify({"error": f"import: {e}"}), 500
    job_id = manager.start_job(
        job_type="brain_full_scan",
        payload={"handle": handle},
        fn=run_full_scan,
        handle=handle,
    )
    return jsonify({"job_id": job_id, "handle": handle})


@app.route("/api/brain/download_creator", methods=["POST"])
def api_brain_download():
    data = request.json or {}
    handle = (data.get("handle") or "").strip()
    limit = int(data.get("limit", 15))
    date_from = (data.get("date_from") or "").strip() or None
    date_to = (data.get("date_to") or "").strip() or None
    if not handle:
        return jsonify({"error": "handle richiesto"}), 400
    try:
        from app.workers.brain_ops import run_download_creator
    except Exception as e:
        return jsonify({"error": f"import: {e}"}), 500
    job_id = manager.start_job(
        job_type="brain_download",
        payload={"handle": handle, "limit": limit, "date_from": date_from, "date_to": date_to},
        fn=run_download_creator,
        handle=handle, limit=limit, date_from=date_from, date_to=date_to,
    )
    return jsonify({"job_id": job_id, "handle": handle})


@app.route("/api/brain/analyze_creator", methods=["POST"])
def api_brain_analyze():
    data = request.json or {}
    handle = (data.get("handle") or "").strip()
    if not handle:
        return jsonify({"error": "handle richiesto"}), 400
    try:
        from app.workers.brain_ops import run_analyze_creator
    except Exception as e:
        return jsonify({"error": f"import: {e}"}), 500
    job_id = manager.start_job(
        job_type="brain_analyze",
        payload={"handle": handle},
        fn=run_analyze_creator,
        handle=handle,
    )
    return jsonify({"job_id": job_id, "handle": handle})


@app.route("/api/brain/add_and_compare", methods=["POST"])
def api_brain_add_compare():
    data = request.json or {}
    handle = (data.get("handle") or "").strip()
    limit = int(data.get("limit", 15))
    date_from = (data.get("date_from") or "").strip() or None
    date_to = (data.get("date_to") or "").strip() or None
    auto_delete = bool(data.get("auto_delete", True))
    if not handle:
        return jsonify({"error": "handle richiesto"}), 400
    try:
        from app.workers.brain_ops import run_add_and_compare
    except Exception as e:
        return jsonify({"error": f"import: {e}"}), 500
    job_id = manager.start_job(
        job_type="brain_add_compare",
        payload={"handle": handle, "limit": limit, "date_from": date_from, "date_to": date_to, "auto_delete": auto_delete},
        fn=run_add_and_compare,
        handle=handle, limit=limit, date_from=date_from, date_to=date_to, auto_delete=auto_delete,
    )
    return jsonify({"job_id": job_id, "handle": handle, "limit": limit})


@app.route("/api/brain/build", methods=["POST"])
def api_brain_build():
    try:
        from app.workers.brain_ops import run_build_brain
    except Exception as e:
        return jsonify({"error": f"import: {e}"}), 500
    job_id = manager.start_job(
        job_type="brain_build",
        payload={},
        fn=run_build_brain,
    )
    return jsonify({"job_id": job_id})


@app.route("/api/brain/rollback", methods=["POST"])
def api_brain_rollback():
    data = request.json or {}
    version = (data.get("version") or "").strip()
    if not version:
        return jsonify({"error": "version richiesta"}), 400
    try:
        from app.workers.brain_ops import run_rollback
    except Exception as e:
        return jsonify({"error": f"import: {e}"}), 500
    job_id = manager.start_job(
        job_type="brain_rollback",
        payload={"version": version},
        fn=run_rollback,
        version=version,
    )
    return jsonify({"job_id": job_id, "version": version})


@app.route("/renders/<path:filename>")
def serve_render(filename):
    return send_from_directory(CFG["RENDERS_DIR"], filename)


@app.route("/api/brain/generate", methods=["POST"])
def api_brain_generate():
    """Genera EDL usando il Creator Brain. Il profile_id e' OPZIONALE
    (serve solo a valle per il render, non per la generazione)."""
    data = request.json or {}
    profile_id = data.get("profile_id") or 16
    venue_name = (data.get("venue_name") or "Wing Stop Milano").strip()[:80]
    try:
        profile_id = int(profile_id)
    except Exception:
        profile_id = 16

    try:
        from app.workers.brain_generate import run_brain_e
    except Exception as e:
        return jsonify({"error": f"brain_generate import fallito: {e}"}), 500

    job_id = manager.start_job(
        job_type="brain_generate",
        payload={"profile_id": profile_id, "venue_name": venue_name, "engine": "brain"},
        fn=run_brain_e,
        profile_id=profile_id,
        venue_name=venue_name,
    )
    return jsonify({"job_id": job_id, "profile_id": profile_id, "engine": "brain"})


@app.route("/api/apply/generate", methods=["POST"])
def api_apply_generate():
    data = request.json or {}
    profile_id = data.get("profile_id")
    venue_name = (data.get("venue_name") or "").strip()[:80]
    if not profile_id:
        return jsonify({"error": "profile_id richiesto."}), 400
    try:
        profile_id = int(profile_id)
    except Exception:
        return jsonify({"error": "profile_id non valido."}), 400

    with get_conn() as conn:
        row = conn.execute("SELECT id FROM profiles WHERE id=?", (profile_id,)).fetchone()
    if not row:
        return jsonify({"error": f"Profilo #{profile_id} non trovato."}), 404

    job_id = manager.start_job(
        job_type="generate_edl",
        payload={"profile_id": profile_id, "venue_name": venue_name},
        fn=generate_edl,
        profile_id=profile_id,
        venue_name=venue_name,
    )
    return jsonify({"job_id": job_id, "profile_id": profile_id, "venue_name": venue_name})


@app.route("/api/edl/list", methods=["GET"])
def api_edl_list():
    import glob as _glob
    d = os.path.join(CFG["MEDIA_DIR"], "edl")
    if not os.path.isdir(d):
        return jsonify({"edls": []})
    files = sorted(_glob.glob(os.path.join(d, "job_*_edl.json")), reverse=True)
    out = []
    for f in files[:10]:
        try:
            with open(f) as fh:
                e = json.load(fh)
            out.append({
                "file": os.path.basename(f),
                "title": e.get("title", ""),
                "n_clips": e.get("n_clips", 0),
                "total_duration": e.get("total_duration", 0),
            })
        except Exception:
            pass
    return jsonify({"edls": out})


@app.route("/api/edl/latest", methods=["GET"])
def api_edl_latest():
    import glob as _glob
    files = sorted(_glob.glob(os.path.join(CFG["MEDIA_DIR"], "edl", "job_*_edl.json")),
                   key=os.path.getmtime, reverse=True)
    if not files:
        return jsonify({"exists": False})
    with open(files[0]) as f:
        edl = json.load(f)
    job_id = int(os.path.basename(files[0]).replace("job_", "").replace("_edl.json", ""))
    edl["edl_job_id"] = job_id
    return jsonify({"exists": True, "edl": edl})


@app.route("/api/edl/<int:edl_job_id>/update-subtitle", methods=["POST"])
def api_edl_update_subtitle(edl_job_id):
    data = request.json or {}
    sid = data.get("scene_id")
    new_text = data.get("subtitle_text", "")
    if sid is None:
        return jsonify({"error": "scene_id mancante"}), 400
    p = os.path.join(CFG["MEDIA_DIR"], "edl", f"job_{edl_job_id}_edl.json")
    if not os.path.exists(p):
        return jsonify({"error": "EDL non trovato"}), 404
    with open(p) as f:
        edl = json.load(f)
    found = False
    for seg in edl.get("timeline", []):
        if seg.get("scene_id") == sid:
            seg["subtitle_text"] = new_text
            found = True
            break
    if not found:
        return jsonify({"error": "scene_id non trovato"}), 404
    with open(p, "w") as f:
        json.dump(edl, f, indent=2, ensure_ascii=False)
    return jsonify({"status": "ok"})


@app.route("/api/voice/status/<int:edl_job_id>", methods=["GET"])
def api_voice_status(edl_job_id):
    vdir = os.path.join(CFG["VOICEOVER_DIR"], f"edl_{edl_job_id}")
    if not os.path.isdir(vdir):
        return jsonify({"recordings": {}})
    out = {}
    import glob as _glob
    for ext in ("webm", "m4a", "mp4", "wav"):
        for f in _glob.glob(os.path.join(vdir, f"scene_*.{ext}")):
            try:
                sid = int(os.path.basename(f).replace("scene_", "").rsplit(".", 1)[0])
                if str(sid) in out:
                    continue  # priorità al primo formato trovato
                out[str(sid)] = {
                    "size_kb": round(os.path.getsize(f) / 1024, 1),
                    "mtime": os.path.getmtime(f),
                    "ext": ext,
                }
            except Exception:
                pass
    return jsonify({"recordings": out})


@app.route("/api/voice/upload", methods=["POST"])
def api_voice_upload():
    if "audio" not in request.files:
        return jsonify({"error": "file audio mancante"}), 400
    edl_job_id = request.form.get("edl_job_id")
    scene_id = request.form.get("scene_id")
    if not edl_job_id or scene_id is None:
        return jsonify({"error": "edl_job_id e scene_id richiesti"}), 400
    try:
        edl_job_id = int(edl_job_id)
        scene_id = int(scene_id)
    except Exception:
        return jsonify({"error": "parametri non validi"}), 400

    vdir = os.path.join(CFG["VOICEOVER_DIR"], f"edl_{edl_job_id}")
    os.makedirs(vdir, exist_ok=True)
    # Determina estensione dal filename originale o dal content-type
    orig_name = (request.files["audio"].filename or "audio.webm").lower()
    if orig_name.endswith(".m4a") or "mp4" in (request.files["audio"].content_type or ""):
        ext = "m4a"
    elif orig_name.endswith(".wav"):
        ext = "wav"
    else:
        ext = "webm"
    # Rimuovi eventuali file precedenti con altre estensioni per questa scena
    import glob as _glob
    for old_f in _glob.glob(os.path.join(vdir, f"scene_{scene_id}.*")):
        try:
            os.remove(old_f)
        except Exception:
            pass
    dst = os.path.join(vdir, f"scene_{scene_id}.{ext}")
    request.files["audio"].save(dst)
    size_kb = round(os.path.getsize(dst) / 1024, 1)
    return jsonify({"status": "ok", "scene_id": scene_id, "size_kb": size_kb, "ext": ext})


@app.route("/api/voice/assemble/<int:edl_job_id>", methods=["POST"])
def api_voice_assemble(edl_job_id):
    job_id = manager.start_job(
        job_type="assemble_voice",
        payload={"edl_job_id": edl_job_id},
        fn=assemble_voiceover,
        edl_job_id=edl_job_id,
    )
    return jsonify({"job_id": job_id, "edl_job_id": edl_job_id})


@app.route("/voiceovers/<path:filename>")
def serve_voiceover(filename):
    return send_from_directory(CFG["VOICEOVER_DIR"], filename)


@app.route("/api/style_ref/count", methods=["GET"])
def api_style_ref_count():
    import glob as _glob
    files = [f for f in _glob.glob(os.path.join(CFG["STYLE_REF_DIR"], "*"))
             if os.path.isfile(f)]
    total_mb = round(sum(os.path.getsize(f) for f in files) / (1024 * 1024), 1)
    return jsonify({"count": len(files), "size_mb": total_mb})


@app.route("/api/scene/<int:edl_job_id>/<int:scene_id>/clip.mp4")
def api_scene_clip(edl_job_id, scene_id):
    import glob as _glob
    edl_path = os.path.join(CFG["MEDIA_DIR"], "edl", f"job_{edl_job_id}_edl.json")
    if not os.path.exists(edl_path):
        return jsonify({"error": "EDL non trovato"}), 404
    with open(edl_path) as f:
        edl = json.load(f)
    seg = next((s for s in edl.get("timeline", []) if s.get("scene_id") == scene_id), None)
    if not seg:
        return jsonify({"error": "scena non trovata"}), 404

    cache_dir = os.path.join(CFG["MEDIA_DIR"], "previews", f"edl_{edl_job_id}")
    os.makedirs(cache_dir, exist_ok=True)
    cache = os.path.join(cache_dir, f"scene_{scene_id}.mp4")

    if not os.path.exists(cache) or os.path.getsize(cache) == 0:
        src_vid = seg["clip_path"]
        in_sec = float(seg.get("in_sec", 0))
        dur = max(0.3, float(seg.get("duration", 1.0)))
        # Preview piccola e veloce: 320px, 20fps, QSV
        cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-init_hw_device", "qsv=hw:/dev/dri/renderD128",
            "-filter_hw_device", "hw",
            "-ss", f"{in_sec:.3f}",
            "-i", src_vid,
            "-t", f"{dur:.3f}",
            "-vf", "scale=320:-2,fps=20,format=nv12,hwupload=extra_hw_frames=64",
            "-an",
            "-c:v", "h264_qsv", "-preset", "veryfast", "-global_quality", "26",
            cache,
        ]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if r.returncode != 0 or not os.path.exists(cache):
                return jsonify({"error": f"ffmpeg: {(r.stderr or '')[-300:]}"}), 500
        except subprocess.TimeoutExpired:
            return jsonify({"error": "timeout generazione preview"}), 500
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    return send_file(cache, mimetype="video/mp4", conditional=True)


@app.route("/api/voice/delete", methods=["POST"])
def api_voice_delete():
    import glob as _glob
    data = request.json or {}
    edl_job_id = data.get("edl_job_id")
    scene_id = data.get("scene_id")
    if not edl_job_id or scene_id is None:
        return jsonify({"error": "edl_job_id e scene_id richiesti"}), 400
    try:
        edl_job_id = int(edl_job_id)
        scene_id = int(scene_id)
    except Exception:
        return jsonify({"error": "parametri non validi"}), 400

    vdir = os.path.join(CFG["VOICEOVER_DIR"], f"edl_{edl_job_id}")
    if not os.path.isdir(vdir):
        return jsonify({"status": "ok", "deleted": 0})

    deleted = 0
    for f in _glob.glob(os.path.join(vdir, f"scene_{scene_id}.*")):
        try:
            os.remove(f)
            deleted += 1
        except Exception:
            pass
    return jsonify({"status": "ok", "deleted": deleted, "scene_id": scene_id})


@app.route("/api/render/<int:edl_job_id>/reburn", methods=["POST"])
def api_render_reburn(edl_job_id):
    job_id = manager.start_job(
        job_type="reburn_subtitles",
        payload={"edl_job_id": edl_job_id},
        fn=reburn_subtitles,
        edl_job_id=edl_job_id,
    )
    return jsonify({"job_id": job_id, "edl_job_id": edl_job_id})


# ============================================================
# ==== STORAGE MANAGEMENT ====
# ============================================================

def _dir_size(path):
    """Ritorna (bytes, file_count) per una cartella ricorsivamente."""
    total = 0
    count = 0
    if not os.path.isdir(path):
        return 0, 0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
                count += 1
            except Exception:
                pass
    return total, count


@app.route("/api/storage/summary", methods=["GET"])
def api_storage_summary():
    # Disco
    du = shutil.disk_usage("/")
    disk = {
        "total_gb": round(du.total / (1024**3), 1),
        "used_gb": round(du.used / (1024**3), 1),
        "free_gb": round(du.free / (1024**3), 1),
        "used_pct": round(du.used / du.total * 100, 1),
    }

    # Categorie
    cats = [
        ("renders",     CFG["RENDERS_DIR"],     "Render finali",        False),
        ("renders_merged", os.path.join(CFG["RENDERS_DIR"], "_merged"), "Merge persistente (reburn)", False),
        ("raw",         CFG["RAW_DIR"],         "Video raw",            False),
        ("style_ref",   CFG["STYLE_REF_DIR"],   "Video riferimento",    False),
        ("voiceover",   CFG["VOICEOVER_DIR"],   "Registrazioni voce",   False),
        ("frames",      CFG["FRAMES_DIR"],      "Frame estratti",       True),
        ("trimmed",     CFG["TRIMMED_DIR"],     "File temporanei",      True),
        ("previews",    os.path.join(CFG["MEDIA_DIR"], "previews"), "Preview scene", True),
        ("analysis",    os.path.join(CFG["MEDIA_DIR"], "analysis"), "Analisi", False),
        ("curated",     os.path.join(CFG["MEDIA_DIR"], "curated"), "Curation", False),
        ("edl",         os.path.join(CFG["MEDIA_DIR"], "edl"), "EDL", False),
        ("tiktok_cache", os.path.join(CFG["MEDIA_DIR"], "tiktok_cache"), "Cache TikTok", True),
    ]

    out = []
    for key, path, label, cleanable in cats:
        size, count = _dir_size(path)
        out.append({
            "key": key,
            "label": label,
            "path": path,
            "size_mb": round(size / (1024*1024), 1),
            "count": count,
            "cleanable": cleanable,
        })

    return jsonify({"disk": disk, "categories": out})


@app.route("/api/storage/cleanup", methods=["POST"])
def api_storage_cleanup():
    """Cleanup rapido: cancella il contenuto di una categoria 'cleanable'."""
    data = request.json or {}
    key = data.get("key", "").strip()

    # Whitelist di sicurezza — SOLO queste cartelle possono essere pulite
    ALLOWED = {
        "frames":    CFG["FRAMES_DIR"],
        "trimmed":   CFG["TRIMMED_DIR"],
        "previews":  os.path.join(CFG["MEDIA_DIR"], "previews"),
        "tiktok_cache": os.path.join(CFG["MEDIA_DIR"], "tiktok_cache"),
    }

    if key not in ALLOWED:
        return jsonify({"error": f"Categoria non cancellabile: {key}"}), 400

    target = ALLOWED[key]
    if not os.path.isdir(target):
        return jsonify({"status": "ok", "deleted": 0, "freed_mb": 0})

    # Calcola dimensione prima
    freed_before, _ = _dir_size(target)
    deleted = 0

    for item in os.listdir(target):
        full = os.path.join(target, item)
        try:
            if os.path.isfile(full):
                os.remove(full)
                deleted += 1
            elif os.path.isdir(full):
                shutil.rmtree(full)
                deleted += 1
        except Exception:
            pass

    freed_mb = round(freed_before / (1024*1024), 1)
    return jsonify({"status": "ok", "deleted": deleted, "freed_mb": freed_mb, "key": key})


@app.route("/api/storage/renders", methods=["GET"])
def api_storage_renders():
    """Lista dei render (con metadati)."""
    rdir = CFG["RENDERS_DIR"]
    if not os.path.isdir(rdir):
        return jsonify({"renders": []})

    out = []
    for f in os.listdir(rdir):
        if not f.startswith("render_") or not f.endswith(".mp4"):
            continue
        if f.startswith("render_voice_"):
            continue  # gestiamo i render base, non le varianti con voce
        full = os.path.join(rdir, f)
        try:
            stat = os.stat(full)
        except Exception:
            continue
        # Estrai edl_job_id
        m = re.search(r"render_(\d+)\.mp4", f)
        edl_job_id = int(m.group(1)) if m else None
        # Verifica varianti
        voice_path = os.path.join(rdir, f"render_voice_{edl_job_id}.mp4") if edl_job_id else None
        merged_path = os.path.join(rdir, "_merged", f"merged_{edl_job_id}.mp4") if edl_job_id else None
        srt_path = os.path.join(rdir, f"render_{edl_job_id}.srt") if edl_job_id else None
        out.append({
            "file": f,
            "edl_job_id": edl_job_id,
            "size_mb": round(stat.st_size / (1024*1024), 1),
            "mtime": stat.st_mtime,
            "date": __import__("datetime").datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
            "has_voice": bool(voice_path and os.path.exists(voice_path)),
            "has_merged": bool(merged_path and os.path.exists(merged_path)),
            "has_srt": bool(srt_path and os.path.exists(srt_path)),
        })

    out.sort(key=lambda x: x["mtime"], reverse=True)
    return jsonify({"renders": out})


@app.route("/api/storage/delete-renders", methods=["POST"])
def api_storage_delete_renders():
    """Cancella render specificati (con eventuali file collegati)."""
    data = request.json or {}
    ids = data.get("edl_job_ids", [])
    delete_voice = bool(data.get("delete_voice", True))
    delete_merged = bool(data.get("delete_merged", False))
    delete_srt = bool(data.get("delete_srt", True))

    if not isinstance(ids, list) or not ids:
        return jsonify({"error": "Nessun id da cancellare"}), 400

    rdir = CFG["RENDERS_DIR"]
    deleted = []
    freed_bytes = 0

    for edl_job_id in ids:
        try:
            edl_job_id = int(edl_job_id)
        except Exception:
            continue

        candidates = [os.path.join(rdir, f"render_{edl_job_id}.mp4")]
        if delete_voice:
            candidates.append(os.path.join(rdir, f"render_voice_{edl_job_id}.mp4"))
        if delete_srt:
            candidates.append(os.path.join(rdir, f"render_{edl_job_id}.srt"))
            candidates.append(os.path.join(rdir, f"render_{edl_job_id}_voiceover.txt"))
        if delete_merged:
            candidates.append(os.path.join(rdir, "_merged", f"merged_{edl_job_id}.mp4"))

        for f in candidates:
            if os.path.isfile(f):
                try:
                    size = os.path.getsize(f)
                    os.remove(f)
                    freed_bytes += size
                    deleted.append(os.path.basename(f))
                except Exception:
                    pass

    return jsonify({
        "status": "ok",
        "deleted_count": len(deleted),
        "deleted_files": deleted,
        "freed_mb": round(freed_bytes / (1024*1024), 1),
    })


# ============================================================
# ==== ANCHORS (primo/ultimo frame) ====
# ============================================================

def _anchors_dir():
    d = os.path.join(CFG["MEDIA_DIR"], "anchors")
    os.makedirs(d, exist_ok=True)
    return d



def _anchors_config_path():
    return os.path.join(_anchors_dir(), "config.json")


def _read_anchors_config():
    """Legge config.json degli anchor. Ritorna dict con 'first' e 'last'."""
    default = {
        "first": {"file": None, "duration": 1.0, "text": ""},
        "last":  {"file": None, "duration": 1.0, "text": ""},
    }
    p = _anchors_config_path()
    if not os.path.exists(p):
        return default
    try:
        with open(p) as f:
            cfg = json.load(f)
        # Merge con default
        for k in ("first", "last"):
            if k not in cfg:
                cfg[k] = default[k]
            for kk, vv in default[k].items():
                cfg[k].setdefault(kk, vv)
        return cfg
    except Exception:
        return default


def _write_anchors_config(cfg):
    with open(_anchors_config_path(), "w") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


@app.route("/api/anchors/upload", methods=["POST"])
def api_anchors_upload():
    if "file" not in request.files:
        return jsonify({"error": "Nessun file"}), 400
    kind = (request.form.get("kind") or "").strip()  # "first" o "last"
    if kind not in ("first", "last"):
        return jsonify({"error": "kind deve essere 'first' o 'last'"}), 400

    f = request.files["file"]
    if not f or not f.filename:
        return jsonify({"error": "File vuoto"}), 400

    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in (".jpg", ".jpeg", ".png", ".webp"):
        return jsonify({"error": f"Formato non supportato: {ext}"}), 400

    d = _anchors_dir()
    # Rimuovi eventuali file precedenti con lo stesso kind
    import glob as _glob
    for old in _glob.glob(os.path.join(d, f"{kind}_frame.*")):
        try:
            os.remove(old)
        except Exception:
            pass

    dst = os.path.join(d, f"{kind}_frame{ext}")
    f.save(dst)

    # Salva anche il riferimento nel config (mantieni text/duration esistenti)
    cfg = _read_anchors_config()
    cfg[kind]["file"] = os.path.basename(dst)
    _write_anchors_config(cfg)

    size_kb = round(os.path.getsize(dst) / 1024, 1)
    return jsonify({
        "status": "ok",
        "kind": kind,
        "file": os.path.basename(dst),
        "size_kb": size_kb,
        "url": f"/anchors/{os.path.basename(dst)}",
    })


@app.route("/api/anchors/list", methods=["GET"])
def api_anchors_list():
    import glob as _glob
    d = _anchors_dir()
    cfg = _read_anchors_config()
    out = {"first": None, "last": None}
    for kind in ("first", "last"):
        files = _glob.glob(os.path.join(d, f"{kind}_frame.*"))
        if files:
            f = files[0]
            out[kind] = {
                "file": os.path.basename(f),
                "size_kb": round(os.path.getsize(f) / 1024, 1),
                "url": f"/anchors/{os.path.basename(f)}",
                "mtime": os.path.getmtime(f),
                "duration": cfg[kind].get("duration", 1.0),
                "text": cfg[kind].get("text", ""),
            }
    return jsonify(out)


@app.route("/api/anchors/config", methods=["POST"])
def api_anchors_config():
    """Aggiorna duration + text per un anchor."""
    data = request.json or {}
    kind = (data.get("kind") or "").strip()
    if kind not in ("first", "last"):
        return jsonify({"error": "kind deve essere 'first' o 'last'"}), 400

    cfg = _read_anchors_config()
    if "duration" in data:
        try:
            dur = float(data["duration"])
            dur = max(0.3, min(dur, 10.0))  # 0.3-10s
            cfg[kind]["duration"] = round(dur, 2)
        except Exception:
            return jsonify({"error": "duration non valida"}), 400
    if "text" in data:
        cfg[kind]["text"] = (data["text"] or "").strip()[:500]

    _write_anchors_config(cfg)
    return jsonify({"status": "ok", "config": cfg})


@app.route("/api/anchors/delete", methods=["POST"])
def api_anchors_delete():
    import glob as _glob
    data = request.json or {}
    kind = data.get("kind", "").strip()
    if kind not in ("first", "last"):
        return jsonify({"error": "kind deve essere 'first' o 'last'"}), 400
    d = _anchors_dir()
    deleted = 0
    for f in _glob.glob(os.path.join(d, f"{kind}_frame.*")):
        try:
            os.remove(f)
            deleted += 1
        except Exception:
            pass

    # Pulisci config
    cfg = _read_anchors_config()
    cfg[kind]["file"] = None
    cfg[kind]["text"] = ""
    cfg[kind]["duration"] = 1.0
    _write_anchors_config(cfg)

    return jsonify({"status": "ok", "deleted": deleted, "kind": kind})


@app.route("/anchors/<path:filename>")
def serve_anchor(filename):
    return send_from_directory(_anchors_dir(), filename)


@app.route("/api/apply/duration-range", methods=["POST"])
def api_apply_duration_range():
    """Salva range durata custom (min/max) che sovrascrive il profilo."""
    data = request.json or {}
    try:
        dmin = float(data.get("min", 0) or 0)
        dmax = float(data.get("max", 0) or 0)
    except Exception:
        return jsonify({"error": "valori non numerici"}), 400

    # 0 = non impostato (usa profilo)
    if dmin < 0 or dmax < 0:
        return jsonify({"error": "valori negativi non ammessi"}), 400
    if dmax > 0 and dmin > 0 and dmin > dmax:
        return jsonify({"error": "min > max"}), 400

    # Salva in un file di config globale
    cfg_path = os.path.join(CFG["MEDIA_DIR"], "analysis_config.json")
    cfg = {}
    if os.path.exists(cfg_path):
        try:
            with open(cfg_path) as f:
                cfg = json.load(f)
        except Exception:
            cfg = {}
    cfg["duration_range"] = {"min": dmin, "max": dmax}
    with open(cfg_path, "w") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)

    return jsonify({"status": "ok", "duration_range": cfg["duration_range"]})


@app.route("/api/apply/duration-range", methods=["GET"])
def api_apply_duration_range_get():
    cfg_path = os.path.join(CFG["MEDIA_DIR"], "analysis_config.json")
    if not os.path.exists(cfg_path):
        return jsonify({"min": 0, "max": 0})
    try:
        with open(cfg_path) as f:
            cfg = json.load(f)
        return jsonify(cfg.get("duration_range", {"min": 0, "max": 0}))
    except Exception:
        return jsonify({"min": 0, "max": 0})


def main():
    init_db()
    require_api_key()   # fallisce subito se la key non è configurata
    print(f"[reel-agent] v{CFG['APP_VERSION']} — http://{CFG['HOST']}:{CFG['PORT']}")
    # HTTPS se i certificati esistono, altrimenti HTTP
    import os as _os
    cert = _os.path.join(CFG["BASE_DIR"], "certs", "cert.pem")
    key = _os.path.join(CFG["BASE_DIR"], "certs", "key.pem")
    ssl_ctx = None
    if _os.path.exists(cert) and _os.path.exists(key):
        ssl_ctx = (cert, key)
        print(f"[reel-agent] HTTPS attivo su https://{CFG['HOST']}:{CFG['PORT']}")
    else:
        print(f"[reel-agent] HTTP (no cert) — http://{CFG['HOST']}:{CFG['PORT']}")
    app.run(host=CFG["HOST"], port=CFG["PORT"], debug=False, threaded=True,
            ssl_context=ssl_ctx)

if __name__ == "__main__":
    main()

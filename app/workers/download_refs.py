"""Scarica i video TikTok selezionati in media/style_ref/."""
import os
import sys
import subprocess
from app.config import CFG
from app.jobs import manager


def _ytdlp_cmd():
    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--impersonate", "chrome",
        "--no-warnings",
        "-f", "b[ext=mp4]/b",
        "--user-agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "--referer", "https://www.tiktok.com/",
    ]
    cookies = os.path.join(CFG["BASE_DIR"], "cookies.txt")
    if os.path.exists(cookies):
        cmd.extend(["--cookies", cookies])
    return cmd


def download_references(job_id: int, videos: list[dict]):
    """
    videos: lista di dict {"id": str, "url": str, "title": str}
    Salva ogni video in media/style_ref/ref_<n>_<id>.mp4
    """
    out_dir = CFG["STYLE_REF_DIR"]
    os.makedirs(out_dir, exist_ok=True)

    total = len(videos)
    if total == 0:
        manager.fail(job_id, "Nessun video selezionato.")
        return

    manager.log(job_id, f"Inizio download di {total} video di riferimento.")
    manager.update_progress(job_id, 0, f"0/{total}")

    saved = []
    for idx, v in enumerate(videos, 1):
        vid = v.get("id") or f"v{idx}"
        url = v.get("url")
        title = (v.get("title") or "")[:60]

        manager.log(job_id, f"[{idx}/{total}] {title}")
        manager.update_progress(job_id, int((idx - 1) / total * 100), f"{idx}/{total}")

        out_template = os.path.join(out_dir, f"ref_{idx:02d}_{vid}.%(ext)s")
        cmd = _ytdlp_cmd() + ["-o", out_template, url]

        res = subprocess.run(cmd, capture_output=True, text=True)
        stderr = (res.stderr or "").strip()

        # Trova file salvato
        found = None
        for ext in ("mp4", "mkv", "webm"):
            p = os.path.join(out_dir, f"ref_{idx:02d}_{vid}.{ext}")
            if os.path.exists(p) and os.path.getsize(p) > 0:
                found = p
                break

        if found:
            size_mb = os.path.getsize(found) / (1024 * 1024)
            manager.log(job_id, f"   ✓ salvato: {os.path.basename(found)} ({size_mb:.1f} MB)")
            saved.append({
                "index": idx,
                "id": vid,
                "path": found,
                "size_mb": round(size_mb, 1),
                "title": title,
            })
        else:
            manager.log(job_id, f"   ✗ errore download: {stderr[:300]}", level="error")

        manager.update_progress(job_id, int(idx / total * 100), f"{idx}/{total}")

    if not saved:
        manager.fail(job_id, "Nessun video scaricato correttamente.")
        return

    manager.log(job_id, f"Completato: {len(saved)}/{total} video in {out_dir}")
    manager.finish(job_id, {"saved": saved, "total": total, "dir": out_dir})

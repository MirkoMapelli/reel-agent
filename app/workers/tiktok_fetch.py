"""Lista metadati TikTok via yt-dlp (venv) con impersonificazione Chrome + cookies."""
import os
import sys
import json
import subprocess
import urllib.request
from app.config import CFG

def _ytdlp_base_cmd():
    return [
        sys.executable, "-m", "yt_dlp",
        "--impersonate", "chrome",
        "--no-warnings",
        "--ignore-errors",
        "--user-agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "--referer", "https://www.tiktok.com/",
    ]

def list_tiktok_videos(username: str, max_videos: int = 20) -> list[dict]:
    username = username.strip().lstrip("@")
    target = f"https://www.tiktok.com/@{username}"

    cache_dir = os.path.join(CFG["MEDIA_DIR"], "tiktok_cache")
    os.makedirs(cache_dir, exist_ok=True)

    cmd = _ytdlp_base_cmd() + [
        "--flat-playlist",
        "--dump-single-json",
        "--playlist-end", str(max_videos),
    ]
    cookies = os.path.join(CFG["BASE_DIR"], "cookies.txt")
    if os.path.exists(cookies):
        cmd.extend(["--cookies", cookies])
    cmd.append(target)

    res = subprocess.run(cmd, capture_output=True, text=True)
    stderr = (res.stderr or "").strip()
    stdout = (res.stdout or "").strip()

    if res.returncode != 0 and not stdout:
        raise RuntimeError(stderr or "yt-dlp non ha restituito dati.")

    try:
        info = json.loads(stdout) if stdout else {}
    except json.JSONDecodeError:
        raise RuntimeError(f"Output yt-dlp non valido:\n{stdout[:600]}\n--- stderr ---\n{stderr[:600]}")

    if not info:
        raise RuntimeError(f"yt-dlp ha restituito vuoto.\nstderr:\n{stderr[:800]}")

    entries = info.get("entries", []) or []
    if not entries:
        raise RuntimeError(
            f"Nessun video restituito per @{username}.\n"
            f"Dettagli yt-dlp:\n{stderr[:800] or '(nessun errore)'}"
        )

    videos = []
    for entry in entries:
        if not entry:
            continue
        vid = entry.get("id")
        if not vid:
            continue

        # URL canonica del video, costruita dall'ID (più affidabile del campo 'url' in flat-playlist)
        video_url = f"https://www.tiktok.com/@{username}/video/{vid}"

        title = entry.get("title") or entry.get("description") or f"Video {vid}"
        upload_date = entry.get("upload_date", "") or ""
        ts = entry.get("timestamp")
        if not upload_date and ts:
            import datetime as _dt
            upload_date = _dt.datetime.utcfromtimestamp(ts).strftime("%Y%m%d")

        thumb_url = entry.get("thumbnail") or (
            entry["thumbnails"][-1]["url"] if entry.get("thumbnails") else None
        )

        thumb_name = f"thumb_{vid}.jpg"
        thumb_path = os.path.join(cache_dir, thumb_name)
        if thumb_url and not os.path.exists(thumb_path):
            try:
                req = urllib.request.Request(thumb_url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=10) as r, open(thumb_path, "wb") as f:
                    f.write(r.read())
            except Exception:
                pass

        date_fmt = (
            f"{upload_date[6:8]}/{upload_date[4:6]}/{upload_date[:4]}"
            if len(upload_date) == 8 else "N/D"
        )

        videos.append({
            "id": vid,
            "url": video_url,
            "title": title,
            "date": date_fmt,
            "duration": entry.get("duration"),
            "views": entry.get("view_count"),
            "thumbnail": f"/tiktok_cache/{thumb_name}" if os.path.exists(thumb_path) else None,
        })

    return videos

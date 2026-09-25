"""Worker semplice: video+audio → video con sottotitoli karaoke Obelix.

Nessuna AI. Solo Whisper + ASS karaoke + ffmpeg burn.
"""
import os, sys, json, time, subprocess
from pathlib import Path

BASE = Path('/opt/reel-agent')
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / 'app'))

from app.jobs import manager
from app.config import CFG
from app.workers.render_final import _write_ass, _effective_subtitle_style

MEDIA = BASE / 'media'
SUBS_DIR = MEDIA / 'simple_subs'
SUBS_DIR.mkdir(parents=True, exist_ok=True)


def _whisper_words(video_path):
    from faster_whisper import WhisperModel
    model = WhisperModel('small', device='cpu', compute_type='int8')
    segments, _ = model.transcribe(str(video_path), language='it',
                                    beam_size=1, word_timestamps=True)
    words = []
    for s in segments:
        for w in (s.words or []):
            words.append({
                'start': float(w.start),
                'end': float(w.end),
                'word': w.word.strip(),
            })
    return words


def _group_words_to_segments(words, max_words=6, max_gap=0.35, max_dur=3.5):
    """Raggruppa parole in 'frasi' karaoke."""
    if not words:
        return []
    segments = []
    cur = [words[0]]
    for w in words[1:]:
        gap = w['start'] - cur[-1]['end']
        dur = w['end'] - cur[0]['start']
        if gap > max_gap or len(cur) >= max_words or dur > max_dur:
            segments.append({
                'start': round(cur[0]['start'], 3),
                'end': round(cur[-1]['end'], 3),
                'dur': round(cur[-1]['end'] - cur[0]['start'], 3),
                'text': ' '.join(x['word'] for x in cur),
            })
            cur = [w]
        else:
            cur.append(w)
    if cur:
        segments.append({
            'start': round(cur[0]['start'], 3),
            'end': round(cur[-1]['end'], 3),
            'dur': round(cur[-1]['end'] - cur[0]['start'], 3),
            'text': ' '.join(x['word'] for x in cur),
        })
    return segments


def _burn_keep_audio(video_in, ass_path, style, video_out):
    """Burn sottotitoli ASS + mantiene traccia audio."""
    font_name = style.get("font_name", "ObelixPro")
    font_clean = (font_name.replace("-Regular", "")
                  .replace("-BoldSim", "").replace("-Bold", ""))
    raw_size = int(style.get("font_size", 60))
    font_size = max(54, raw_size) if raw_size < 40 else raw_size
    primary = style.get("primary_color_ass", "&H00FDFDFD&")
    outline = style.get("outline_color_ass", "&H00010101&")
    outline_w = int(style.get("outline_width", 4))
    margin_v = int(style.get("margin_v", 155))
    alignment = int(style.get("alignment", 2))
    bold = int(style.get("bold", 1))

    force_style = (
        f"FontName={font_clean},FontSize={font_size},"
        f"PrimaryColour={primary},OutlineColour={outline},"
        f"BorderStyle=1,Outline={outline_w},Shadow=0,"
        f"Alignment={alignment},MarginV={margin_v},Bold={bold},"
        f"PlayResX=1080,PlayResY=1920"
    )

    ass_esc = str(ass_path).replace(":", "\\:")
    if str(ass_path).lower().endswith(".ass"):
        vf = f"subtitles='{ass_esc}',format=nv12,hwupload=extra_hw_frames=64"
    else:
        vf = f"subtitles='{ass_esc}':force_style='{force_style}',format=nv12,hwupload=extra_hw_frames=64"

    cmd = [
        "ffmpeg", "-y",
        "-init_hw_device", "qsv=hw:/dev/dri/renderD128",
        "-filter_hw_device", "hw",
        "-i", str(video_in),
        "-vf", vf,
        "-c:v", "h264_qsv", "-preset", "veryfast", "-global_quality", "22",
        "-c:a", "aac", "-b:a", "128k",
        "-map", "0:v:0", "-map", "0:a:0?",
        str(video_out),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    except subprocess.TimeoutExpired:
        return False, 'ffmpeg timeout'
    return r.returncode == 0, (r.stderr or '')[-1500:]


def run_simple_subtitles(job_id, video_path, options=None):
    """Pipeline: video+audio → Whisper → ASS karaoke → burn."""
    options = options or {}
    video_path = Path(video_path)
    job_dir = SUBS_DIR / f'job_{job_id}'
    job_dir.mkdir(parents=True, exist_ok=True)
    out_path = job_dir / 'output.mp4'

    manager.log(job_id, f'[1/4] input: {video_path.name}')
    manager.update_progress(job_id, 5, 'lettura audio')

    if not video_path.exists():
        manager.fail(job_id, f'video non trovato')
        return

    # 1. Whisper
    manager.log(job_id, '[2/4] Whisper word-timestamps…')
    try:
        words = _whisper_words(video_path)
    except Exception as e:
        manager.fail(job_id, f'whisper error: {e}')
        return
    manager.log(job_id, f'  {len(words)} parole')
    if not words:
        manager.fail(job_id, 'nessuna parola rilevata')
        return

    # 2. Group
    manager.update_progress(job_id, 40, 'raggruppo in frasi')
    segments = _group_words_to_segments(words)
    manager.log(job_id, f'[3/4] {len(segments)} frasi')

    # 3. ASS
    manager.update_progress(job_id, 60, 'genero ASS karaoke')
    style = _effective_subtitle_style({})

    # Override da options (UI)
    if options.get('font_name'):
        style['font_name'] = options['font_name']
    if options.get('font_size'):
        style['font_size'] = int(options['font_size'])
    if options.get('outline_width') is not None:
        style['outline_width'] = int(options['outline_width'])
    if options.get('margin_v') is not None:
        style['margin_v'] = int(options['margin_v'])
    if options.get('primary_color_hex'):
        h = options['primary_color_hex'].replace('#','').upper()
        if len(h) == 6:
            r_, g_, b_ = h[0:2], h[2:4], h[4:6]
            style['primary_color_ass'] = f'&H00{b_}{g_}{r_}&'
    if options.get('highlight_color_hex'):
        h = options['highlight_color_hex'].replace('#','').upper()
        if len(h) == 6:
            r_, g_, b_ = h[0:2], h[2:4], h[4:6]
            style['highlight_color_ass'] = f'&H00{b_}{g_}{r_}&'

    # Modalità karaoke
    km = (options.get('karaoke_mode') or 'phrase').lower()
    style['karaoke_mode'] = km
    # "static" = nessun karaoke: disattiva evidenziazione, mostra frase fissa
    if km == 'static':
        style['karaoke'] = False
    else:
        style['karaoke'] = True

    manager.log(job_id, f"  STYLE RICEVUTO: font_name={style.get('font_name')!r} size={style.get('font_size')} primary={style.get('primary_color_ass')} highlight={style.get('highlight_color_ass')}")
    manager.log(job_id, f"  OPTIONS RICEVUTE DA UI: {options}")
    ass_path = job_dir / 'output.ass'
    ass_segments = [{'dur': s['dur'], 'text': s['text']} for s in segments]
    try:
        _write_ass(ass_segments, str(ass_path), style)
    except Exception as e:
        manager.fail(job_id, f'ASS error: {e}')
        return
    manager.log(job_id, f'  ASS: {ass_path.name}')

    # 4. Burn
    manager.update_progress(job_id, 75, 'burn-in sottotitoli (con audio)')
    ok, err = _burn_keep_audio(str(video_path), str(ass_path), style, str(out_path))
    if not ok:
        manager.fail(job_id, f'burn-in fallito: {err[:300]}')
        return

    size_mb = out_path.stat().st_size / 1024 / 1024
    manager.log(job_id, f'[4/4] output: {out_path.name} ({size_mb:.1f} MB)')
    manager.update_progress(job_id, 100, 'completato')
    manager.finish(job_id, {
        'ok': True,
        'output': str(out_path),
        'n_words': len(words),
        'n_segments': len(segments),
        'size_mb': round(size_mb, 1),
    })

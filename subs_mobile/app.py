#!/usr/bin/env python3
"""Mobile subtitles. Porta 5001, HTTP.
Features: preview live karaoke, editor trascritto, drag sottotitoli, storico.
"""
import os, sys, json, re, time, uuid, threading, subprocess, traceback
from pathlib import Path
from flask import Flask, request, jsonify, send_file, render_template

BASE = Path('/opt/reel-agent')
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / 'app'))
from dotenv import load_dotenv
load_dotenv(BASE / '.env')

from app.workers.simple_subs import _whisper_words, _group_words_to_segments, _burn_keep_audio
from app.workers.render_final import _write_ass, _effective_subtitle_style

JOBS = BASE / 'subs_mobile' / 'jobs'
JOBS.mkdir(parents=True, exist_ok=True)

app = Flask(__name__, template_folder=str(BASE / 'subs_mobile' / 'templates'))
app.config['MAX_CONTENT_LENGTH'] = 2 * 1024 * 1024 * 1024


def _sd(job_id):
    d = JOBS / job_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _load(job_id):
    p = _sd(job_id) / 'state.json'
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def _save(job_id, state):
    (_sd(job_id) / 'state.json').write_text(json.dumps(state, indent=2, ensure_ascii=False))


def _set_status(job_id, status, progress=0, message='', **extra):
    state = _load(job_id) or {}
    state.update({'status': status, 'progress': progress, 'message': message,
                  'ts': time.time()})
    state.update(extra)
    _save(job_id, state)


def _transcribe_worker(job_id):
    try:
        st = _load(job_id)
        video = _sd(job_id) / st['video_file']
        _set_status(job_id, 'transcribing', 5, 'lettura audio…')
        words = _whisper_words(str(video))
        if not words:
            _set_status(job_id, 'error', 0, 'nessuna parola rilevata')
            return
        _set_status(job_id, 'transcribing', 60, f'{len(words)} parole')
        segments = _group_words_to_segments(words)
        # Salva segmenti editabili
        st = _load(job_id)
        st['words'] = words
        st['segments'] = segments
        st['status'] = 'ready_edit'
        st['progress'] = 100
        st['message'] = f'{len(segments)} frasi pronte — modificabili'
        st['video_duration'] = words[-1]['end'] if words else 0
        _save(job_id, st)
    except Exception as e:
        traceback.print_exc()
        _set_status(job_id, 'error', 0, f'{type(e).__name__}: {str(e)[:200]}')


def _render_worker(job_id, opts):
    try:
        st = _load(job_id)
        video = _sd(job_id) / st['video_file']
        segments = st.get('segments', [])
        if not segments:
            _set_status(job_id, 'error', 0, 'nessun segmento')
            return
        _set_status(job_id, 'rendering', 30, 'genero sottotitoli…')

        style = _effective_subtitle_style({})
        # Applica opzioni
        if opts.get('font_name'): style['font_name'] = opts['font_name']
        if opts.get('font_size'): style['font_size'] = int(opts['font_size'])
        if opts.get('outline_width') is not None: style['outline_width'] = int(opts['outline_width'])
        if opts.get('margin_v') is not None: style['margin_v'] = int(opts['margin_v'])
        if opts.get('margin_h') is not None: style['margin_h'] = int(opts['margin_h'])
        for k, target in [('primary_color_hex','primary_color_ass'),
                          ('highlight_color_hex','highlight_color_ass')]:
            h = (opts.get(k) or '').replace('#','').upper()
            if len(h) == 6:
                r_, g_, b_ = h[0:2], h[2:4], h[4:6]
                style[target] = f'&H00{b_}{g_}{r_}&'

        km = (opts.get('karaoke_mode') or 'phrase').lower()
        style['karaoke_mode'] = km
        style['karaoke'] = (km != 'static')

        # Applica override testo modificato
        edited = opts.get('segments')
        if edited and isinstance(edited, list):
            segments = edited

        ass_path = _sd(job_id) / 'output.ass'
        _write_ass([{'dur': s['dur'], 'text': s['text']} for s in segments],
                   str(ass_path), style)
        _set_status(job_id, 'rendering', 70, 'burn-in sottotitoli…')

        out_path = _sd(job_id) / 'output.mp4'
        ok, err = _burn_keep_audio(str(video), str(ass_path), style, str(out_path))
        if not ok:
            _set_status(job_id, 'error', 0, f'burn-in: {err[:200]}')
            return

        size_mb = out_path.stat().st_size / 1024 / 1024
        _set_status(job_id, 'done', 100, 'completato',
                    size_mb=round(size_mb, 1),
                    n_segments=len(segments),
                    video_url=f'/api/video/{job_id}',
                    download_url=f'/api/video/{job_id}?dl=1')
    except Exception as e:
        traceback.print_exc()
        _set_status(job_id, 'error', 0, f'{type(e).__name__}: {str(e)[:200]}')


# ============================================================
# ROUTES
# ============================================================

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/fonts')
def api_fonts():
    fonts = []
    try:
        r = subprocess.run(['fc-list', '--format', '%{family}\n'],
                           capture_output=True, text=True, timeout=5)
        seen = set()
        for line in (r.stdout or '').split('\n'):
            fam = line.strip()
            if not fam or ',' in fam or fam in seen: continue
            seen.add(fam)
            fonts.append(fam)
    except Exception:
        pass
    def key(f):
        fl = f.lower()
        if 'obelix' in fl: return (0, f)
        if 'noto' in fl: return (1, f)
        return (2, f)
    fonts.sort(key=key)
    return jsonify({'fonts': fonts[:80]})


@app.route('/api/font-file/<path:family>')
def api_font_file(family):
    try:
        r = subprocess.run(['fc-match', '-f', '%{file}', family],
                           capture_output=True, text=True, timeout=5)
        path = (r.stdout or '').strip()
        if path and os.path.exists(path):
            return send_file(path, mimetype='font/ttf')
    except Exception:
        pass
    return jsonify({'error': 'not found'}), 404


@app.route('/api/upload', methods=['POST'])
def api_upload():
    if 'file' not in request.files:
        return jsonify({'error': 'no file'}), 400
    f = request.files['file']
    if not f.filename:
        return jsonify({'error': 'empty name'}), 400

    job_id = uuid.uuid4().hex[:12]
    job_dir = _sd(job_id)
    safe = re.sub(r'[^A-Za-z0-9._-]', '_', f.filename)[:80]
    up_name = f'input_{safe}'
    f.save(str(job_dir / up_name))

    _save(job_id, {
        'job_id': job_id,
        'filename': f.filename,
        'video_file': up_name,
        'status': 'queued',
        'progress': 0,
        'message': 'in coda',
        'created_at': time.time(),
    })
    threading.Thread(target=_transcribe_worker, args=(job_id,), daemon=True).start()
    return jsonify({'job_id': job_id, 'filename': f.filename})


@app.route('/api/status/<job_id>')
def api_status(job_id):
    st = _load(job_id)
    if not st:
        return jsonify({'error': 'not found'}), 404
    return jsonify(st)


@app.route('/api/segments/<job_id>', methods=['GET', 'POST'])
def api_segments(job_id):
    st = _load(job_id)
    if not st:
        return jsonify({'error': 'not found'}), 404
    if request.method == 'POST':
        data = request.json or {}
        segs = data.get('segments')
        if isinstance(segs, list):
            st['segments'] = segs
            _save(job_id, st)
        return jsonify({'ok': True, 'n': len(st.get('segments', []))})
    return jsonify({'segments': st.get('segments', [])})


@app.route('/api/render/<job_id>', methods=['POST'])
def api_render(job_id):
    st = _load(job_id)
    if not st:
        return jsonify({'error': 'not found'}), 404
    opts = request.json or {}
    threading.Thread(target=_render_worker, args=(job_id, opts), daemon=True).start()
    return jsonify({'ok': True, 'job_id': job_id})


@app.route('/api/video/<job_id>')
def api_video(job_id):
    p = _sd(job_id) / 'output.mp4'
    if not p.exists():
        return jsonify({'error': 'not ready'}), 404
    dl = request.args.get('dl') == '1'
    return send_file(str(p), mimetype='video/mp4',
                     as_attachment=dl, download_name=f'karaoke_{job_id}.mp4',
                     conditional=True)


@app.route('/api/raw/<job_id>')
def api_raw(job_id):
    """Serve il video originale (per anteprima live)."""
    st = _load(job_id)
    if not st:
        return jsonify({'error': 'not found'}), 404
    p = _sd(job_id) / st['video_file']
    if not p.exists():
        return jsonify({'error': 'no file'}), 404
    return send_file(str(p), mimetype='video/mp4', conditional=True)


@app.route('/api/jobs')
def api_jobs():
    """Storico job, ordinati per data."""
    out = []
    for d in sorted(JOBS.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if not d.is_dir(): continue
        st = _load(d.name)
        if not st: continue
        out.append({
            'job_id': d.name,
            'filename': st.get('filename', '?'),
            'status': st.get('status', '?'),
            'created_at': st.get('created_at', 0),
            'size_mb': st.get('size_mb'),
            'n_segments': st.get('n_segments'),
            'has_output': (d / 'output.mp4').exists(),
        })
    return jsonify({'jobs': out[:50]})


@app.route('/api/jobs/<job_id>/delete', methods=['POST'])
def api_job_delete(job_id):
    import shutil
    d = JOBS / job_id
    if not d.exists():
        return jsonify({'error': 'not found'}), 404
    shutil.rmtree(d, ignore_errors=True)
    return jsonify({'ok': True})


if __name__ == '__main__':
    print('[subs-mobile] http://0.0.0.0:5001')
    app.run(host='0.0.0.0', port=5001, debug=False, threaded=True)

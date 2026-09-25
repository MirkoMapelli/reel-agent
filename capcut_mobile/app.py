#!/usr/bin/env python3
"""Mobile video editor (CapCut-like). Porta 5002, HTTP."""
import os, sys, json, uuid, subprocess, threading, time
from pathlib import Path
from flask import Flask, request, jsonify, send_file, render_template

BASE = Path('/opt/reel-agent')
SESS_DIR = BASE / 'capcut_mobile' / 'sessions'
SESS_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(__name__, template_folder=str(BASE / 'capcut_mobile' / 'templates'))
app.config['MAX_CONTENT_LENGTH'] = 4 * 1024 * 1024 * 1024


def _sd(sid):
    d = SESS_DIR / sid
    d.mkdir(parents=True, exist_ok=True)
    return d


def _load(sid):
    p = _sd(sid) / 'state.json'
    if not p.exists():
        return {'clips': []}
    return json.loads(p.read_text())


def _save(sid, state):
    (_sd(sid) / 'state.json').write_text(json.dumps(state, indent=2, ensure_ascii=False))


def _probe(path):
    r = subprocess.run(
        ['ffprobe', '-v', 'error', '-show_entries',
         'format=duration:stream=width,height,r_frame_rate',
         '-of', 'json', str(path)],
        capture_output=True, text=True, timeout=30)
    try:
        d = json.loads(r.stdout)
        dur = float(d.get('format', {}).get('duration') or 0)
        return {'duration': round(dur, 3)}
    except Exception:
        return {'duration': 0}


def _thumb(video, out):
    info = _probe(video)
    ts = max(0.1, info['duration'] / 2)
    cmd = ['ffmpeg', '-y', '-v', 'error', '-ss', f'{ts:.3f}', '-i', str(video),
           '-frames:v', '1', '-vf', 'scale=240:-2', '-q:v', '5', str(out)]
    r = subprocess.run(cmd, capture_output=True, timeout=15)
    return r.returncode == 0 and out.exists()


def _export_status_path(sid):
    return _sd(sid) / 'export_status.json'


def _set_export(sid, status, progress, message, **extra):
    d = {'status': status, 'progress': progress, 'message': message,
         'ts': time.time(), **extra}
    _export_status_path(sid).write_text(json.dumps(d))


def _do_export(sid):
    try:
        state = _load(sid)
        clips = state['clips']
        if not clips:
            _set_export(sid, 'error', 0, 'nessun clip')
            return
        work = _sd(sid) / 'export_work'
        work.mkdir(exist_ok=True)
        for f in work.glob('part_*.mp4'):
            f.unlink()
        parts = []
        n = len(clips)
        for i, c in enumerate(clips):
            src = _sd(sid) / c['file']
            dst = work / f'part_{i:03d}.mp4'
            start = float(c.get('start') or 0)
            end = float(c.get('end') or c.get('duration') or 0)
            dur = max(0.1, end - start)
            cmd = ['ffmpeg', '-y', '-v', 'error',
                   '-ss', f'{start:.3f}', '-i', str(src), '-t', f'{dur:.3f}',
                   '-vf', 'scale=1080:1920:force_original_aspect_ratio=decrease,'
                          'pad=1080:1920:(ow-iw)/2:(oh-ih)/2',
                   '-r', '30',
                   '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '23',
                   '-c:a', 'aac', '-b:a', '128k', '-ar', '44100',
                   '-pix_fmt', 'yuv420p',
                   '-movflags', '+faststart',
                   str(dst)]
            r = subprocess.run(cmd, capture_output=True, timeout=600)
            if r.returncode != 0:
                _set_export(sid, 'error', 0, f'clip {i+1} fallito: {r.stderr[-200:]}')
                return
            parts.append(dst)
            _set_export(sid, 'running', int((i+1) / n * 90), f'clip {i+1}/{n}')

        concat_file = work / 'concat.txt'
        concat_file.write_text('\n'.join(f"file '{p.name}'" for p in parts) + '\n')
        out = _sd(sid) / 'export.mp4'
        r = subprocess.run(
            ['ffmpeg', '-y', '-v', 'error', '-f', 'concat', '-safe', '0',
             '-i', str(concat_file), '-c', 'copy', '-movflags', '+faststart', str(out)],
            capture_output=True, timeout=300, cwd=str(work))
        if r.returncode != 0:
            _set_export(sid, 'error', 0, f'concat fallito: {r.stderr[-200:]}')
            return
        size_mb = out.stat().st_size / 1024 / 1024
        _set_export(sid, 'done', 100, 'completato', size_mb=round(size_mb, 1),
                    file='export.mp4')
    except Exception as e:
        _set_export(sid, 'error', 0, f'{type(e).__name__}: {e}')


# ============================================================
# ROUTES
# ============================================================

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/session', methods=['POST'])
def api_new_session():
    sid = uuid.uuid4().hex[:12]
    _save(sid, {'clips': []})
    return jsonify({'sid': sid})


@app.route('/api/session/<sid>/upload', methods=['POST'])
def api_upload(sid):
    if 'files' not in request.files:
        return jsonify({'error': 'nessun file'}), 400
    files = request.files.getlist('files')
    state = _load(sid)
    added = 0
    for f in files:
        if not f.filename:
            continue
        # nome file salvato
        ext = Path(f.filename).suffix or '.mp4'
        idx = len(state['clips'])
        fname = f'clip_{idx:03d}{ext}'
        dst = _sd(sid) / fname
        f.save(str(dst))
        info = _probe(dst)
        dur = info.get('duration') or 0
        if dur <= 0:
            dst.unlink()
            continue
        # thumbnail
        thumb_path = _sd(sid) / f'thumb_{idx:03d}.jpg'
        _thumb(dst, thumb_path)
        state['clips'].append({
            'file': fname,
            'orig_name': f.filename,
            'duration': round(dur, 3),
            'start': 0.0,
            'end': round(dur, 3),
            'thumb': thumb_path.name if thumb_path.exists() else None,
        })
        added += 1
    _save(sid, state)
    return jsonify({'added': added, 'total': len(state['clips'])})


@app.route('/api/session/<sid>/state')
def api_state(sid):
    return jsonify(_load(sid))


@app.route('/api/session/<sid>/trim/<int:idx>', methods=['POST'])
def api_trim(sid, idx):
    data = request.json or {}
    state = _load(sid)
    if idx < 0 or idx >= len(state['clips']):
        return jsonify({'error': 'index out'}), 400
    c = state['clips'][idx]
    start = max(0.0, float(data.get('start', c['start'])))
    end = min(c['duration'], float(data.get('end', c['end'])))
    if end - start < 0.2:
        return jsonify({'error': 'troppo corto'}), 400
    c['start'] = round(start, 3)
    c['end'] = round(end, 3)
    _save(sid, state)
    return jsonify({'ok': True, 'clip': c})


@app.route('/api/session/<sid>/delete/<int:idx>', methods=['POST'])
def api_delete(sid, idx):
    state = _load(sid)
    if idx < 0 or idx >= len(state['clips']):
        return jsonify({'error': 'index out'}), 400
    state['clips'].pop(idx)
    _save(sid, state)
    return jsonify({'ok': True, 'total': len(state['clips'])})


@app.route('/api/session/<sid>/move/<int:idx>', methods=['POST'])
def api_move(sid, idx):
    data = request.json or {}
    delta = int(data.get('delta', 0))
    state = _load(sid)
    clips = state['clips']
    if idx < 0 or idx >= len(clips) or delta == 0:
        return jsonify({'error': 'bad args'}), 400
    new = max(0, min(len(clips) - 1, idx + delta))
    if new == idx:
        return jsonify({'ok': True, 'idx': idx})
    clip = clips.pop(idx)
    clips.insert(new, clip)
    _save(sid, state)
    return jsonify({'ok': True, 'idx': new})


@app.route('/api/session/<sid>/split/<int:idx>', methods=['POST'])
def api_split(sid, idx):
    data = request.json or {}
    at = float(data.get('at', 0))
    state = _load(sid)
    clips = state['clips']
    if idx < 0 or idx >= len(clips):
        return jsonify({'error': 'index out'}), 400
    c = clips[idx]
    if at <= c['start'] + 0.2 or at >= c['end'] - 0.2:
        return jsonify({'error': 'punto troppo vicino al bordo'}), 400
    c1 = dict(c); c1['end'] = round(at, 3)
    c2 = dict(c); c2['start'] = round(at, 3)
    clips[idx:idx+1] = [c1, c2]
    _save(sid, state)
    return jsonify({'ok': True, 'total': len(clips)})


@app.route('/api/session/<sid>/reorder', methods=['POST'])
def api_reorder(sid):
    data = request.json or {}
    src_i = int(data.get('from', -1))
    dst_i = int(data.get('to', -1))
    state = _load(sid)
    clips = state['clips']
    if src_i < 0 or src_i >= len(clips) or dst_i < 0 or dst_i >= len(clips):
        return jsonify({'error': 'index out'}), 400
    if src_i == dst_i:
        return jsonify({'ok': True, 'from': src_i, 'to': dst_i})
    clip = clips.pop(src_i)
    clips.insert(dst_i, clip)
    _save(sid, state)
    return jsonify({'ok': True, 'from': src_i, 'to': dst_i})


@app.route('/api/session/<sid>/duplicate/<int:idx>', methods=['POST'])
def api_dup(sid, idx):
    state = _load(sid)
    clips = state['clips']
    if idx < 0 or idx >= len(clips):
        return jsonify({'error': 'index out'}), 400
    dup = dict(clips[idx])
    clips.insert(idx + 1, dup)
    _save(sid, state)
    return jsonify({'ok': True, 'total': len(clips)})


@app.route('/api/session/<sid>/thumb/<int:idx>')
def api_thumb(sid, idx):
    state = _load(sid)
    if idx < 0 or idx >= len(state['clips']):
        return '', 404
    t = state['clips'][idx].get('thumb')
    if not t:
        return '', 404
    p = _sd(sid) / t
    if not p.exists():
        return '', 404
    return send_file(str(p), mimetype='image/jpeg')


@app.route('/api/session/<sid>/raw/<int:idx>')
def api_raw(sid, idx):
    state = _load(sid)
    if idx < 0 or idx >= len(state['clips']):
        return '', 404
    p = _sd(sid) / state['clips'][idx]['file']
    if not p.exists():
        return '', 404
    return send_file(str(p), mimetype='video/mp4', conditional=True)


@app.route('/api/session/<sid>/export', methods=['POST'])
def api_export(sid):
    _set_export(sid, 'queued', 0, 'in coda')
    threading.Thread(target=_do_export, args=(sid,), daemon=True).start()
    return jsonify({'ok': True})


@app.route('/api/session/<sid>/export/status')
def api_export_status(sid):
    p = _export_status_path(sid)
    if not p.exists():
        return jsonify({'status': 'none', 'progress': 0, 'message': ''})
    return jsonify(json.loads(p.read_text()))


@app.route('/api/session/<sid>/export/file')
def api_export_file(sid):
    p = _sd(sid) / 'export.mp4'
    if not p.exists():
        return jsonify({'error': 'not ready'}), 404
    dl = request.args.get('dl') == '1'
    return send_file(str(p), mimetype='video/mp4',
                     as_attachment=dl, download_name='edit.mp4', conditional=True)


if __name__ == '__main__':
    print('[capcut] http://0.0.0.0:5002')
    app.run(host='0.0.0.0', port=5002, debug=False, threaded=True)

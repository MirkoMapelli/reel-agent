"""Job manager asincrono: thread + SSE broadcaster + persistenza SQLite."""
import json
import queue
import threading
import traceback
from datetime import datetime
from typing import Callable, Optional

from app.db import get_conn

# Stato in-memory: {job_id: {"queue": Queue, "subscribers": [Queue, ...]}}
_JOBS = {}
_LOCK = threading.Lock()

TERMINAL = {"done", "error", "cancelled"}


# ---------- persistenza ----------

def _db_create(job_type: str, payload: dict) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO jobs (type, status, progress, step_label, payload_json) "
            "VALUES (?, 'queued', 0, ?, ?)",
            (job_type, "in attesa", json.dumps(payload, ensure_ascii=False)),
        )
        return cur.lastrowid


def _db_update(job_id: int, **fields):
    if not fields:
        return
    fields["updated_at"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    sets = ", ".join(f"{k}=?" for k in fields)
    values = list(fields.values()) + [job_id]
    with get_conn() as conn:
        conn.execute(f"UPDATE jobs SET {sets} WHERE id=?", values)


def _db_log(job_id: int, level: str, message: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO job_logs (job_id, level, message) VALUES (?, ?, ?)",
            (job_id, level, message),
        )


# ---------- API pubblica ----------

def create_job(job_type: str, payload: dict) -> int:
    job_id = _db_create(job_type, payload)
    with _LOCK:
        _JOBS[job_id] = {"subscribers": []}
    return job_id


def emit(job_id: int, event: dict):
    """Notifica tutti i subscriber SSE."""
    with _LOCK:
        subs = list(_JOBS.get(job_id, {}).get("subscribers", []))
    for q in subs:
        try:
            q.put_nowait(event)
        except queue.Full:
            pass


def update_progress(job_id: int, pct: int, label: str = None):
    pct = max(0, min(100, int(pct)))
    fields = {"progress": pct}
    if label is not None:
        fields["step_label"] = label
    _db_update(job_id, **fields)
    emit(job_id, {"type": "progress", "progress": pct, "label": label or ""})


def log(job_id: int, message: str, level: str = "info"):
    _db_log(job_id, level, message)
    emit(job_id, {"type": "log", "level": level, "message": message})


def finish(job_id: int, result: dict = None):
    _db_update(job_id, status="done", progress=100, step_label="completato",
               result_json=json.dumps(result or {}, ensure_ascii=False))
    emit(job_id, {"type": "done", "result": result or {}})


def fail(job_id: int, error: str):
    _db_update(job_id, status="error", step_label="errore", error=error)
    emit(job_id, {"type": "error", "error": error})


def start_job(job_type: str, payload: dict, fn: Callable, *args, **kwargs) -> int:
    """Crea il job e lancia `fn(job_id, *args, **kwargs)` in un thread."""
    job_id = create_job(job_type, payload)
    _db_update(job_id, status="running")

    def _runner():
        emit(job_id, {"type": "started"})
        try:
            fn(job_id, *args, **kwargs)
        except Exception as e:
            tb = traceback.format_exc()
            log(job_id, tb, level="error")
            fail(job_id, str(e))

    t = threading.Thread(target=_runner, daemon=True)
    t.start()
    return job_id


# ---------- SSE stream ----------

def sse_stream(job_id: int):
    """Generatore SSE: stato iniziale + aggiornamenti live + heartbeat."""
    # Stato iniziale
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        logs = conn.execute(
            "SELECT level, message, created_at FROM job_logs WHERE job_id=? ORDER BY id",
            (job_id,),
        ).fetchall()

    if not row:
        yield f"data: {json.dumps({'type': 'error', 'error': 'job inesistente'})}\n\n"
        return

    yield f"data: {json.dumps({
        'type': 'state',
        'status': row['status'],
        'progress': row['progress'],
        'label': row['step_label'] or '',
        'logs': [dict(l) for l in logs],
    }, ensure_ascii=False)}\n\n"

    if row["status"] in TERMINAL:
        yield f"data: {json.dumps({'type': row['status'] if row['status'] != 'done' else 'done'})}\n\n"
        return

    q: queue.Queue = queue.Queue(maxsize=200)
    with _LOCK:
        _JOBS.setdefault(job_id, {"subscribers": []})["subscribers"].append(q)

    try:
        while True:
            try:
                ev = q.get(timeout=15)
            except queue.Empty:
                yield ": heartbeat\n\n"
                # controlla stato DB
                with get_conn() as conn:
                    r = conn.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
                if r and r["status"] in TERMINAL:
                    yield f"data: {json.dumps({'type': r['status']})}\n\n"
                    break
                continue

            yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
            if ev.get("type") in ("done", "error", "cancelled"):
                break
    finally:
        with _LOCK:
            subs = _JOBS.get(job_id, {}).get("subscribers", [])
            if q in subs:
                subs.remove(q)

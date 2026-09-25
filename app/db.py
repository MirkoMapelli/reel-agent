"""SQLite: profili di stile, job, log, run."""
import sqlite3
from pathlib import Path
from app.config import CFG

SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS profiles (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    tiktok_account  TEXT,
    style_json      TEXT NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS jobs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    type            TEXT NOT NULL,          -- 'tiktok_fetch' | 'learn' | 'render' | 'voiceover_mix'
    status          TEXT NOT NULL DEFAULT 'queued',  -- queued|running|done|error|cancelled
    progress        INTEGER NOT NULL DEFAULT 0,
    step_label      TEXT,
    payload_json    TEXT,
    result_json     TEXT,
    error           TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS job_logs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id          INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    level           TEXT NOT NULL DEFAULT 'info',
    message         TEXT NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_job_logs_job ON job_logs(job_id, id);

CREATE TABLE IF NOT EXISTS runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id      INTEGER REFERENCES profiles(id) ON DELETE SET NULL,
    status          TEXT NOT NULL DEFAULT 'queued',
    output_path     TEXT,
    voiceover_path  TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(CFG["DB_PATH"])
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def init_db() -> None:
    Path(CFG["DB_PATH"]).parent.mkdir(parents=True, exist_ok=True)
    with get_conn() as conn:
        conn.executescript(SCHEMA)

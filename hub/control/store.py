"""The flow engine's durable state: runs, step checkpoints, flow approvals, run-locks.

One SQLite file (`%LOCALAPPDATA%\\Control\\control.db`, WAL mode) next to the journal. The
journal (journal.py) stays the human-readable audit trail of every action Control ran; this store
is the engine's own bookkeeping so a run can be resumed after a crash or a reboot without redoing
completed steps or reissuing an already-answered approval.
"""
import json
import sqlite3
import time
from pathlib import Path

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id            TEXT PRIMARY KEY,
    flow_name     TEXT NOT NULL,
    flow_hash     TEXT NOT NULL,
    flow_text     TEXT NOT NULL DEFAULT '',   -- pinned: resume uses this, never a later edit on disk
    status        TEXT NOT NULL,      -- running, waiting, needs_attention, done, failed, cancelled
    input         TEXT NOT NULL,      -- json
    context       TEXT NOT NULL,      -- json: {"steps": {id: output}, "vars": {...}}
    cursor        INTEGER NOT NULL DEFAULT 0,
    wait_for      TEXT,               -- json: what run.resume needs, while status = waiting
    error         TEXT,
    parent_run_id TEXT,
    dry_run       INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS runs_flow ON runs(flow_name, status);

CREATE TABLE IF NOT EXISTS steps (
    run_id      TEXT NOT NULL,
    idx         TEXT NOT NULL,       -- "0", "0.2" (nested inside if/for_each), etc.
    step_id     TEXT NOT NULL,
    attempt     INTEGER NOT NULL DEFAULT 1,
    status      TEXT NOT NULL,        -- started, done, failed, skipped
    input       TEXT,                 -- json: resolved args (secrets already masked)
    output      TEXT,                 -- json
    error       TEXT,
    started_at  TEXT,
    finished_at TEXT,
    PRIMARY KEY (run_id, idx, attempt)
);

CREATE TABLE IF NOT EXISTS approvals (
    flow_name   TEXT PRIMARY KEY,
    flow_hash   TEXT NOT NULL,
    approved_at TEXT NOT NULL,
    expires_at  TEXT
);

CREATE TABLE IF NOT EXISTS locks (
    flow_name   TEXT PRIMARY KEY,
    run_id      TEXT NOT NULL,
    acquired_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trigger_state (
    flow_name     TEXT NOT NULL,
    trigger_idx   INTEGER NOT NULL,
    last_fired_at TEXT,
    last_run_id   TEXT,
    PRIMARY KEY (flow_name, trigger_idx)
);

CREATE TABLE IF NOT EXISTS seen_files (
    flow_name   TEXT NOT NULL,
    trigger_idx INTEGER NOT NULL,
    path        TEXT NOT NULL,
    size        INTEGER,
    mtime       REAL,
    fired_at    TEXT NOT NULL,
    PRIMARY KEY (flow_name, trigger_idx, path)
);
"""


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), isolation_level=None, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)

    def close(self):
        self.conn.close()

    # ---- runs ---------------------------------------------------------

    def create_run(self, run_id, flow_name, flow_hash, input_, *, flow_text="", dry_run=False,
                    parent_run_id=None):
        now = _now()
        self.conn.execute(
            "INSERT INTO runs (id, flow_name, flow_hash, flow_text, status, input, context, cursor, "
            "parent_run_id, dry_run, created_at, updated_at) VALUES (?,?,?,?,?,?,?,0,?,?,?,?)",
            (run_id, flow_name, flow_hash, flow_text, "running", json.dumps(input_, ensure_ascii=False),
             json.dumps({"input": input_, "steps": {}, "vars": {}}, ensure_ascii=False), parent_run_id,
             int(dry_run), now, now))
        return self.get_run(run_id)

    def get_run(self, run_id):
        row = self.conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            return None
        cols = [d[0] for d in self.conn.execute("SELECT * FROM runs LIMIT 0").description]
        rec = dict(zip(cols, row))
        rec["input"] = json.loads(rec["input"])
        rec["context"] = json.loads(rec["context"])
        rec["wait_for"] = json.loads(rec["wait_for"]) if rec["wait_for"] else None
        return rec

    def list_runs(self, *, flow_name=None, status=None, limit=50):
        q, params = "SELECT id FROM runs WHERE 1=1", []
        if flow_name:
            q += " AND flow_name=?"
            params.append(flow_name)
        if status:
            q += " AND status=?"
            params.append(status)
        q += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        return [self.get_run(r[0]) for r in self.conn.execute(q, params).fetchall()]

    def update_run(self, run_id, **fields):
        if not fields:
            return
        cols, params = [], []
        for key, value in fields.items():
            if key in ("context", "wait_for") and value is not None:
                value = json.dumps(value, ensure_ascii=False)
            cols.append(f"{key}=?")
            params.append(value)
        cols.append("updated_at=?")
        params.append(_now())
        params.append(run_id)
        self.conn.execute(f"UPDATE runs SET {', '.join(cols)} WHERE id=?", params)

    # ---- steps ----------------------------------------------------------

    def start_step(self, run_id, idx, step_id, attempt, input_):
        self.conn.execute(
            "INSERT OR REPLACE INTO steps (run_id, idx, step_id, attempt, status, input, started_at) "
            "VALUES (?,?,?,?, 'started', ?, ?)",
            (run_id, idx, step_id, attempt, json.dumps(input_, ensure_ascii=False, default=str), _now()))

    def finish_step(self, run_id, idx, attempt, status, output=None, error=None):
        self.conn.execute(
            "UPDATE steps SET status=?, output=?, error=?, finished_at=? "
            "WHERE run_id=? AND idx=? AND attempt=?",
            (status, json.dumps(output, ensure_ascii=False, default=str) if output is not None else None,
             error, _now(), run_id, idx, attempt))

    def steps_for(self, run_id):
        rows = self.conn.execute(
            "SELECT idx, step_id, attempt, status, input, output, error, started_at, finished_at "
            "FROM steps WHERE run_id=? ORDER BY idx, attempt", (run_id,)).fetchall()
        out = []
        for idx, step_id, attempt, status, input_, output, error, started_at, finished_at in rows:
            out.append({"idx": idx, "step_id": step_id, "attempt": attempt, "status": status,
                        "input": json.loads(input_) if input_ else None,
                        "output": json.loads(output) if output else None,
                        "error": error, "started_at": started_at, "finished_at": finished_at})
        return out

    def last_attempt(self, run_id, idx):
        row = self.conn.execute(
            "SELECT attempt, status, output FROM steps WHERE run_id=? AND idx=? "
            "ORDER BY attempt DESC LIMIT 1", (run_id, idx)).fetchone()
        if row is None:
            return None
        attempt, status, output = row
        return {"attempt": attempt, "status": status, "output": json.loads(output) if output else None}

    # ---- flow approvals ---------------------------------------------------

    def set_approval(self, flow_name, flow_hash, ttl_seconds=None):
        # expires_at is a raw epoch float (not the human "%Y-%m-%dT%H:%M:%S" timestamps used
        # elsewhere): those only have second resolution, which made a short or negative ttl
        # flaky to test and, in principle, to honour.
        expires = repr(time.time() + ttl_seconds) if ttl_seconds is not None else None
        self.conn.execute(
            "INSERT INTO approvals (flow_name, flow_hash, approved_at, expires_at) VALUES (?,?,?,?) "
            "ON CONFLICT(flow_name) DO UPDATE SET flow_hash=excluded.flow_hash, "
            "approved_at=excluded.approved_at, expires_at=excluded.expires_at",
            (flow_name, flow_hash, _now(), expires))

    def revoke_approval(self, flow_name):
        self.conn.execute("DELETE FROM approvals WHERE flow_name=?", (flow_name,))

    def is_approved(self, flow_name, flow_hash):
        row = self.conn.execute("SELECT flow_hash, expires_at FROM approvals WHERE flow_name=?",
                                 (flow_name,)).fetchone()
        if row is None:
            return False
        stored_hash, expires_at = row
        if stored_hash != flow_hash:
            return False
        if expires_at and float(expires_at) < time.time():
            return False
        return True

    # ---- single-flight locks (one run of a given flow at a time) ----------

    def acquire_lock(self, flow_name, run_id):
        try:
            self.conn.execute("INSERT INTO locks (flow_name, run_id, acquired_at) VALUES (?,?,?)",
                               (flow_name, run_id, _now()))
            return True
        except sqlite3.IntegrityError:
            return False

    def acquire_or_confirm_lock(self, flow_name, run_id):
        """True if `run_id` now holds (or already held) the lock for `flow_name`; False if a
        *different* run holds it. Used when resuming a run: it should reclaim its own lock, not
        fight a fresh run_flow call for a brand-new one."""
        holder = self.lock_holder(flow_name)
        if holder is None:
            return self.acquire_lock(flow_name, run_id)
        return holder == run_id

    def release_lock(self, flow_name):
        self.conn.execute("DELETE FROM locks WHERE flow_name=?", (flow_name,))

    def lock_holder(self, flow_name):
        row = self.conn.execute("SELECT run_id FROM locks WHERE flow_name=?", (flow_name,)).fetchone()
        return row[0] if row else None

    # ---- trigger bookkeeping (piece 3: scheduler & event triggers) --------

    def get_trigger_state(self, flow_name, trigger_idx):
        row = self.conn.execute("SELECT last_fired_at, last_run_id FROM trigger_state "
                                 "WHERE flow_name=? AND trigger_idx=?", (flow_name, trigger_idx)).fetchone()
        if row is None:
            return {"last_fired_at": None, "last_run_id": None}
        return {"last_fired_at": row[0], "last_run_id": row[1]}

    def set_trigger_state(self, flow_name, trigger_idx, *, last_fired_at, last_run_id=None):
        self.conn.execute(
            "INSERT INTO trigger_state (flow_name, trigger_idx, last_fired_at, last_run_id) VALUES (?,?,?,?) "
            "ON CONFLICT(flow_name, trigger_idx) DO UPDATE SET "
            "last_fired_at=excluded.last_fired_at, last_run_id=excluded.last_run_id",
            (flow_name, trigger_idx, last_fired_at, last_run_id))

    def seen_file(self, flow_name, trigger_idx, path):
        row = self.conn.execute("SELECT size, mtime, fired_at FROM seen_files "
                                 "WHERE flow_name=? AND trigger_idx=? AND path=?",
                                 (flow_name, trigger_idx, path)).fetchone()
        return None if row is None else {"size": row[0], "mtime": row[1], "fired_at": row[2]}

    def mark_seen_file(self, flow_name, trigger_idx, path, size, mtime):
        self.conn.execute(
            "INSERT OR REPLACE INTO seen_files (flow_name, trigger_idx, path, size, mtime, fired_at) "
            "VALUES (?,?,?,?,?,?)", (flow_name, trigger_idx, path, size, mtime, _now()))


_default = None


def default():
    global _default
    home = config.home()
    if _default is None or _default.path != home / "control.db":
        _default = Store(home / "control.db")
    return _default


def reset_default():
    global _default
    if _default is not None:
        _default.close()
    _default = None

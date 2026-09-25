"""Run model: one folder per refresh, a manifest updated atomically after every stage, and promotion by a single
atomic pointer flip (`current.json`). A failed, interrupted or killed run can never replace the last valid dataset.

    data/runs/<run_id>/manifest.json ...      data/current.json = {"run_id": ...}      data/.lock
"""
from __future__ import annotations

import datetime as dt
import json
import os
import platform
import shutil
import time
import traceback

from .errors import Xl2aiError
from .fsutil import atomic_write_json, read_json
from .log import log
from .procs import memory_status, pid_alive

LOW_COMMIT_MB = 1024                                      # below this, Excel and Python start failing unpredictably

CONTRACT_VERSION = "1.0"


def now_iso():
    return dt.datetime.now().isoformat(timespec="seconds")


# ------------------------------------------------------------------------------------------------ lock
class Lock:
    """Exclusive refresh lock.

    A live owner is never displaced just because the lock is old. Age is advisory only; takeover requires the
    recorded owner process to be gone (or an unreadable half-created lock to remain abandoned long enough).
    """

    def __init__(self, cfg):
        self.cfg, self.path, self.held = cfg, cfg.lock_file, False

    def _read(self):
        try:
            return read_json(self.path)
        except (OSError, ValueError):
            return None

    def _stale(self, info):
        if not isinstance(info, dict):                    # unreadable/empty: a writer may be mid-create, give it time
            try:
                return time.time() - os.path.getmtime(self.path) > 60
            except OSError:
                return True
        if not pid_alive(info.get("pid")):
            return True
        return False

    def acquire(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        for _ in range(3):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                info = self._read()
                if self._stale(info):
                    log("WARN", f"Taking over a stale lock (owner pid {info.get('pid') if info else '?'} is gone)")
                    try:
                        os.remove(self.path)
                    except OSError:
                        pass
                    continue
                if not isinstance(info, dict):             # not stale yet, but unreadable: a writer is mid-create
                    raise Xl2aiError("E_LOCKED", "another refresh appears to be starting (lock file is being written)",
                                     "wait a moment and retry; never delete a lock while its owner process is alive") from None
                age = time.time() - float(info.get("epoch", time.time()))
                limit = self.cfg.lock_stale_hours * 3600
                old_note = ""
                if limit and age > limit:
                    old_note = f"; lock is older than {self.cfg.lock_stale_hours}h but owner pid is still alive"
                raise Xl2aiError("E_LOCKED", f"another refresh is running (pid {info.get('pid')}, "
                                             f"since {info.get('started')}{old_note})",
                                 "wait for it; never delete a lock while its owner process is alive") from None
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"pid": os.getpid(), "started": now_iso(), "epoch": time.time()}, f)
            self.held = True
            return self
        raise Xl2aiError("E_LOCKED", "could not acquire the refresh lock")

    def release(self):
        if self.held:
            info = self._read()
            if info is None or info.get("pid") == os.getpid():
                try:
                    os.remove(self.path)
                except OSError:
                    pass
            self.held = False

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *exc):
        self.release()
        return False


# ------------------------------------------------------------------------------------------------ queries
def list_runs(cfg):
    if not os.path.isdir(cfg.runs_dir):
        return []
    items = []
    for d in os.listdir(cfg.runs_dir):
        manifest = os.path.join(cfg.runs_dir, d, "manifest.json")
        if not os.path.isfile(manifest):
            continue
        try:
            m = read_json(manifest)
            order = float(m.get("started_epoch", os.path.getmtime(manifest)))
        except (OSError, ValueError, TypeError):
            order = os.path.getmtime(manifest)
        items.append((order, d))
    items.sort(reverse=True)
    return [d for _, d in items]


def load_manifest(cfg, run_id):
    return read_json(os.path.join(cfg.runs_dir, run_id, "manifest.json"))


def current_run_id(cfg):
    try:
        return read_json(cfg.current_file).get("run_id")
    except (OSError, ValueError):
        return None


# ------------------------------------------------------------------------------------------------ stages
class StageRec:
    """Context manager for one stage. Records status, timing, artifacts, warnings and error in the manifest."""

    def __init__(self, run, name):
        self.run, self.name, self.t0 = run, name, None
        self.rec = {"name": name, "status": "running", "started": now_iso(), "seconds": None,
                    "artifacts": [], "warnings": [], "details": {}, "error": None}

    def artifact(self, path):
        self.rec["artifacts"].append(self.run.rel(path))

    def warn(self, msg):
        self.rec["warnings"].append(msg)
        log("WARN", f"[{self.name}] {msg}")

    def detail(self, key, value):
        self.rec["details"][key] = value

    def fail(self, code, message, hint=""):
        self.rec["status"], self.rec["error"] = "failed", {"code": code, "message": message, "hint": hint}

    def partial(self, message):
        self.rec["status"] = "partial"
        self.warn(message)

    def __enter__(self):
        self.t0 = time.perf_counter()
        self.run.m["stages"].append(self.rec)
        self.run.save()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.rec["seconds"] = round(time.perf_counter() - self.t0, 2)
        if exc_type is not None:
            if issubclass(exc_type, KeyboardInterrupt):
                self.rec["status"] = "interrupted"
            else:
                self.rec["status"] = "failed"
                if isinstance(exc, Xl2aiError):
                    self.rec["error"] = exc.as_dict()
                else:                                     # unexpected: keep enough of the traceback to diagnose it later
                    hint = "system is out of memory (see manifest platform.memory)" if isinstance(exc, MemoryError) else ""
                    self.rec["error"] = {"code": "E_STAGE", "message": f"{exc_type.__name__}: {exc}", "hint": hint,
                                         "trace": "".join(traceback.format_exception(exc_type, exc, tb)[-4:]).strip()}
        elif self.rec["status"] == "running":
            self.rec["status"] = "passed"
        self.run.save()
        if exc_type is not None and not issubclass(exc_type, (KeyboardInterrupt, SystemExit)):
            log("ERROR", f"[{self.name}] {self.rec['error']['message']}")
            return True                                   # recorded; the orchestrator decides what happens next
        return False


# ------------------------------------------------------------------------------------------------ run
class Run:
    def __init__(self, cfg, run_id, manifest):
        self.cfg, self.id, self.m = cfg, run_id, manifest
        self.dir = os.path.join(cfg.runs_dir, run_id)

    @classmethod
    def create(cls, cfg):
        run_id = time.strftime("%Y%m%dT%H%M%S") + "-" + os.urandom(2).hex()
        mem = memory_status()
        manifest = {"contract_version": CONTRACT_VERSION, "run_id": run_id, "project": cfg.project,
                    "started": now_iso(), "started_epoch": time.time(), "finished": None, "status": "running", "promoted": False,
                    "config_fingerprint": cfg.fingerprint(), "extract_fingerprint": cfg.extract_fingerprint(),
                    "platform": {"python": platform.python_version(), "pid": os.getpid(), "memory": mem},
                    "inputs": [], "stages": []}
        if mem and mem["commit_free_mb"] < LOW_COMMIT_MB:
            manifest["warnings"] = [f"low memory at start: {mem['commit_free_mb']} MB commit free "
                                    f"({mem['phys_free_mb']} MB RAM free); Excel and Python may fail unpredictably"]
            log("WARN", manifest["warnings"][0])
        run = cls(cfg, run_id, manifest)
        os.makedirs(run.dir)
        run.save()
        return run

    def path(self, *parts):
        return os.path.join(self.dir, *parts)

    def rel(self, path):
        return os.path.relpath(path, self.dir).replace("\\", "/")

    def save(self):
        atomic_write_json(self.path("manifest.json"), self.m)

    def stage(self, name):
        return StageRec(self, name)

    def stage_status(self, name):
        for s in self.m["stages"]:
            if s["name"] == name:
                return s["status"]
        return None

    def finish(self):
        """Decide the run status and promote it if (and only if) it earned it. Returns True if promoted."""
        statuses = {s["status"] for s in self.m["stages"]}
        if not statuses:
            status = "failed"
            self.m.setdefault("notes", []).append("run had no stages and cannot be promoted")
        elif statuses <= {"passed"}:
            status = "passed"
        elif statuses & {"failed", "interrupted", "running"}:
            status = "failed"
        else:
            status = "partial"
        promote = status == "passed" or (status == "partial" and self.cfg.allow_partial)
        self.m["finished"], self.m["status"] = now_iso(), status
        if promote:
            previous = current_run_id(self.cfg)
            atomic_write_json(self.cfg.current_file, {"run_id": self.id, "promoted_at": now_iso(), "previous": previous})
            self.m["promoted"] = True                     # the pointer file is the authority; this is informational
        self.save()
        apply_retention(self.cfg, protect={self.id})
        return promote


# ------------------------------------------------------------------------------------------------ housekeeping
def reap_orphans(cfg, exclude=()):
    """Runs left as 'running' by a process that died without finishing: mark them aborted (never delete evidence).
    Must be called while holding the lock. The run `current.json` points at is never marked aborted."""
    cur = current_run_id(cfg)
    for run_id in list_runs(cfg):
        if run_id in exclude:
            continue
        try:
            m = load_manifest(cfg, run_id)
        except (OSError, ValueError):
            continue
        if m.get("status") != "running":
            continue
        if run_id == cur:                                 # flipped to current, then died before saving: it is complete
            m["status"], m["promoted"] = "passed", True
            m["finished"] = m.get("finished") or now_iso()
            note = "finalised by recovery"
        else:
            m["status"], m["finished"] = "aborted", now_iso()
            for s in m.get("stages", []):
                if s.get("status") == "running":
                    s["status"] = "interrupted"
            note = "the process ended without finishing this run"
        m.setdefault("notes", []).append(note)
        atomic_write_json(os.path.join(cfg.runs_dir, run_id, "manifest.json"), m)
        log("WARN", f"Run {run_id}: {note} -> status {m['status']}")


def apply_retention(cfg, protect=()):
    """Keep the newest `keep_runs` runs plus current plus anything in `protect`; delete the rest."""
    runs = list_runs(cfg)
    keep = set(runs[:cfg.keep_runs]) | set(protect)
    cur = current_run_id(cfg)
    if cur:
        keep.add(cur)
    for run_id in runs:
        if run_id not in keep:
            shutil.rmtree(os.path.join(cfg.runs_dir, run_id), ignore_errors=True)
            log("INFO", f"Retention: removed old run {run_id}")


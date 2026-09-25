"""`agent.*`: piece 3 of the master roadmap — what checks a flow's `[[triggers]]` and starts it.

`agent.tick` is the one thing that actually looks: it is called by `control agent`'s loop, but it
is also just an ordinary safe action, so an agent (or a test) can call it directly. It never
starts a run itself — every fire goes through `flow.run`, so the normal tier/approval/lock rules
apply identically to a triggered run and a manual one (an unapproved risky flow still fails closed
with `APPROVAL_UNAVAILABLE` if nobody is at the screen to answer the box).
"""
import glob as glob_mod
import os
import time as time_mod
from datetime import datetime, timedelta

from .. import registry, runner
from ..registry import READ, SAFE, Action
from ..flow import model
from ..store import default as default_store
from ..triggers import cron
from . import flow as flow_area

DT_FMT = "%Y-%m-%dT%H:%M:%S"
OBJ = {"type": "object", "properties": {}}


def _now_dt():
    return datetime.now()


def _parse_dt(s):
    return datetime.strptime(s, DT_FMT)


def _due_cron(flow_name, idx, trig, store, now):
    state = store.get_trigger_state(flow_name, idx)
    last = state["last_fired_at"]
    start = _parse_dt(last) if last else now.replace(second=0, microsecond=0) - timedelta(minutes=1)
    try:
        next_due = cron.next_after(trig["expr"], start)
    except cron.CronError:
        return False, {}
    return next_due <= now, {}


def _due_interval(flow_name, idx, trig, store, now):
    state = store.get_trigger_state(flow_name, idx)
    last = state["last_fired_at"]
    if last is None:
        return True, {}
    return (now - _parse_dt(last)).total_seconds() >= trig["seconds"], {}


def _due_file(flow_name, idx, trig, store, now):
    watch, pattern = trig["watch"], trig.get("pattern", "*")
    stable_for_s = trig.get("stable_for_s", 5)
    if not os.path.isdir(watch):
        return False, {}
    for path in sorted(glob_mod.glob(os.path.join(watch, pattern))):
        try:
            st = os.stat(path)
        except OSError:
            continue
        if time_mod.time() - st.st_mtime < stable_for_s:
            continue                                       # still being written
        if store.seen_file(flow_name, idx, path) is not None:
            continue                                       # already fired for this exact file
        store.mark_seen_file(flow_name, idx, path, st.st_size, st.st_mtime)
        return True, {"path": path, "size": st.st_size}
    return False, {}


_CHECKERS = {"cron": _due_cron, "interval": _due_interval, "file": _due_file}


def _tick(args):
    store = default_store()
    now = _now_dt()
    fired, errors = [], []
    d = flow_area._flows_dir()
    if not d.exists():
        return {"fired": fired}, "no flows to check"
    for path in sorted(d.glob("*.toml")):
        try:
            f = model.parse_file(path)
        except model.FlowError as e:
            errors.append(f"{path.stem}: {e}")
            continue
        for idx, trig in enumerate(f.triggers):
            checker = _CHECKERS.get(trig["type"])
            if checker is None:
                continue
            due, extra = checker(f.name, idx, trig, store, now)
            if not due:
                continue
            payload, _ = runner.execute("flow.run", {"name": f.name,
                                                       "input": {"trigger": {"type": trig["type"], **extra,
                                                                              "fired_at": now.strftime(DT_FMT)}}})
            store.set_trigger_state(f.name, idx, last_fired_at=now.strftime(DT_FMT), last_run_id=payload.get("id"))
            fired.append({"flow": f.name, "trigger": idx, "type": trig["type"],
                         "run_id": payload.get("id"), "status": payload.get("status"), "ok": payload.get("ok", True)})
    text = "\n".join(f"fired {r['flow']} (trigger {r['trigger']}, {r['type']}) -> run {r['run_id']}: {r['status']}"
                     for r in fired) or "nothing due"
    if errors:
        text += "\n" + "\n".join(f"BROKEN {e}" for e in errors)
    return {"fired": fired, "errors": errors}, text


def _status(args):
    store = default_store()
    now = _now_dt()
    d = flow_area._flows_dir()
    rows = []
    if d.exists():
        for path in sorted(d.glob("*.toml")):
            try:
                f = model.parse_file(path)
            except model.FlowError:
                continue
            for idx, trig in enumerate(f.triggers):
                state = store.get_trigger_state(f.name, idx)
                row = {"flow": f.name, "trigger": idx, "type": trig["type"], "last_fired_at": state["last_fired_at"]}
                if trig["type"] == "cron":
                    try:
                        start = _parse_dt(state["last_fired_at"]) if state["last_fired_at"] else now
                        row["next_due"] = cron.next_after(trig["expr"], start).strftime(DT_FMT)
                    except cron.CronError:
                        row["next_due"] = None
                elif trig["type"] == "interval":
                    row["every_s"] = trig["seconds"]
                elif trig["type"] == "file":
                    row["watching"] = f"{trig['watch']}/{trig.get('pattern', '*')}"
                rows.append(row)
    text = "\n".join(f"{r['flow']:20} [{r['type']}] " + (r.get("next_due") or r.get("watching") or
                     f"every {r.get('every_s')}s") + (f"  last: {r['last_fired_at']}" if r["last_fired_at"] else "  never fired")
                     for r in rows) or "no triggers defined in any flow"
    return {"triggers": rows}, text


def register_area(cfg):
    acts = [
        Action("agent.tick", "Check every flow's triggers once and start any that are due. Never bypasses "
               "approval: an unapproved risky flow still fails closed if nobody is there to answer it.",
               OBJ, _tick, SAFE),
        Action("agent.status", "Every flow's triggers, when each last fired, and (for cron) when it is next due.",
               OBJ, _status, READ),
    ]
    for a in acts:
        registry.register(a)
    registry.set_area("agent", True, count=len(acts))

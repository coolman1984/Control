"""`record.*`: turn a person's own hands into both an audit trail and a draft flow.

wad already has a mature recorder (`wad record`, low-level mouse/keyboard hooks + accessibility
lookups, turning clicks into durable-selector steps, password fields into a redacted placeholder)
- see win-agent-desktop/wadlib/record.py, which this deliberately does not touch or reimplement.
Two things it doesn't do on its own: it blocks the calling process until stopped, so Control runs
it as a background child exactly like `gmes.*` (`win.*`/`web.*` stay in-process because those
calls return in milliseconds); and it produces a replay script, not a plain-language record of
what happened or a Control flow. `record.*` adds both: `record.audit` is the human-readable "what
did this person do" report (compliance, handover, documenting a process); `record.to_flow` is
piece 9's "Record -> flow" made concrete, now that piece 2's flow engine exists to run what it
writes - a recorded password field becomes `{{ secret:name }}`, ready for the owner to fill in
with `control vault set` before the draft is approved and run for real.
"""
import json
import sys
import time
from pathlib import Path

from .. import config, proc, registry
from ..errors import ControlError
from ..registry import READ, SAFE, Action

_ACTION_FOR_CMD = {"click": "win.click", "right-click": "win.right-click",
                    "double-click": "win.double-click", "focus": "win.focus",
                    "type": "win.type", "select": "win.select", "press": "win.press"}
SECRET_PLACEHOLDER = "${ENV:WAD_SECRET}"


def _sessions_dir():
    d = config.home() / "recordings"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _state_path():
    return config.home() / "recording.json"


def _load_state():
    p = _state_path()
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _save_state(state):
    _state_path().parent.mkdir(parents=True, exist_ok=True)
    _state_path().write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def _clear_state():
    try:
        _state_path().unlink()
    except OSError:
        pass


def _wad_dir(cfg):
    d = cfg.get("wad_dir")
    return d if d and Path(d).exists() else None


def _session_path(session_id):
    return _sessions_dir() / f"{session_id}.session.json"


def _load_session(session_id):
    path = _session_path(session_id)
    if not path.exists():
        raise ControlError("SESSION_NOT_FOUND", f"no recorded session {session_id!r}",
                            "list them with record.list")
    return json.loads(path.read_text(encoding="utf-8"))


def _start(cfg):
    def handler(args):
        if _load_state() is not None:
            raise ControlError("RECORD_ALREADY_ACTIVE", "a recording is already running",
                                "record.stop it first, or check record.status")
        wad_dir = _wad_dir(cfg)
        if wad_dir is None:
            raise ControlError("RECORD_UNAVAILABLE", "win-agent-desktop is not set up on this PC",
                                "run its own setup; win.* would report the same problem")
        session_id = time.strftime("rec-%Y%m%d-%H%M%S")
        out = _sessions_dir() / f"{session_id}.raw.json"
        argv = [cfg.get("gmes_python") or sys.executable, "wad.py", "record", str(out)]
        if args.get("window"):
            argv += ["--window", args["window"]]
        if args.get("seconds"):
            argv += ["--seconds", str(args["seconds"])]
        child = proc.start_background(argv, cwd=wad_dir)
        _save_state({"session_id": session_id, "out": str(out), "pid": child.pid,
                     "task": args.get("task", ""), "window": args.get("window", ""),
                     "started_at": time.strftime("%Y-%m-%dT%H:%M:%S")})
        return ({"ok": True, "session_id": session_id},
                f"recording {session_id} started (task: {args.get('task') or 'untitled'}); "
                f"stop it with record.stop")
    return handler


def _stop(cfg):
    def handler(args):
        state = _load_state()
        if state is None:
            raise ControlError("RECORD_NOT_ACTIVE", "no recording is running")
        wad_dir = _wad_dir(cfg)
        proc.run([cfg.get("gmes_python") or sys.executable, "wad.py", "record-stop"],
                 cwd=wad_dir, timeout=15)
        out_path = Path(state["out"])
        deadline = time.time() + 20
        while not out_path.exists() and time.time() < deadline:
            time.sleep(0.2)
        _clear_state()
        if not out_path.exists():
            raise ControlError("RECORD_TIMEOUT", "the recorder did not write its file in time",
                               "check it wasn't killed; wad's own recordings/ folder may still have it")
        raw = json.loads(out_path.read_text(encoding="utf-8"))
        session = {"session_id": state["session_id"], "task": state.get("task", ""),
                   "window": state.get("window", ""), "started_at": state.get("started_at"),
                   "stopped_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                   "steps": raw.get("steps", []), "recorded": raw.get("recorded")}
        _session_path(state["session_id"]).write_text(
            json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8")
        return ({"ok": True, "session_id": session["session_id"], "steps": len(session["steps"])},
                f"stopped {session['session_id']}: {len(session['steps'])} step(s) recorded")
    return handler


def _status(args):
    state = _load_state()
    if state is None:
        return {"active": False}, "no recording running"
    return {"active": True, **state}, f"recording {state['session_id']} since {state['started_at']}"


def _list(args):
    rows = []
    for path in sorted(_sessions_dir().glob("*.session.json")):
        try:
            s = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        rows.append({"session_id": s["session_id"], "task": s.get("task", ""),
                    "steps": len(s.get("steps", [])), "started_at": s.get("started_at")})
    text = "\n".join(f"{r['session_id']}  {r['steps']:>3} step(s)  {r['task'] or '(untitled)'}"
                     for r in rows) or "no recordings yet"
    return {"sessions": rows}, text


def _describe_step(s):
    cmd = s.get("cmd")
    if cmd in ("click", "right-click", "double-click", "focus"):
        return f"{cmd} on {s.get('target')}"
    if cmd == "type":
        shown = "(password, not shown)" if s.get("text") == SECRET_PLACEHOLDER else repr(s.get("text", ""))
        return f"typed {shown} into {s.get('target')}"
    if cmd == "select":
        return f"picked {s.get('option')!r} in {s.get('target')}"
    if cmd == "press":
        return f"pressed {s.get('combo')}"
    return f"{cmd} {s}"


def _audit(args):
    session = _load_session(args["session_id"])
    lines = [f"# What happened: {session['session_id']}",
              f"Task: {session.get('task') or '(not given)'}",
              f"Started: {session.get('started_at')}    Stopped: {session.get('stopped_at')}",
              f"Steps: {len(session['steps'])}", ""]
    windows_seen = []
    for i, s in enumerate(session["steps"], 1):
        w = s.get("window")
        if w and (not windows_seen or windows_seen[-1] != w):
            lines.append(f"\n**{w}**")
            windows_seen.append(w)
        lines.append(f"{i}. {_describe_step(s)}")
    report = "\n".join(lines) + "\n"
    report_path = _sessions_dir() / f"{session['session_id']}.audit.md"
    report_path.write_text(report, encoding="utf-8")
    return {"ok": True, "session_id": session["session_id"], "path": str(report_path)}, report


def steps_to_flow_toml(name, description, steps, *, secret_name="recorded_password"):
    """The pure conversion at the heart of `record.to_flow`: no I/O, easy to test on its own."""
    lines = [f"name = {json.dumps(name)}"]
    if description:
        lines.append(f"description = {json.dumps(description)}")
    lines.append("")
    n_secrets = 0
    for i, s in enumerate(steps, 1):
        action = _ACTION_FOR_CMD.get(s.get("cmd"))
        if action is None:
            continue                                # an unrecognised step is skipped, not guessed at
        args = {}
        if s.get("target"):
            args["target"] = s["target"]
        if s.get("window"):
            args["window"] = s["window"]
        if s.get("option"):
            args["option"] = s["option"]
        if s.get("combo"):
            args["combo"] = s["combo"]
        if "text" in s:
            if s["text"] == SECRET_PLACEHOLDER:
                n_secrets += 1
                nm = secret_name if n_secrets == 1 else f"{secret_name}_{n_secrets}"
                args["text"] = "{{ secret:%s }}" % nm
            else:
                args["text"] = s["text"]
        lines += ["[[steps]]", f'id = "step{i}"', 'type = "action"', f"action = {json.dumps(action)}"]
        if args:
            lines.append("[steps.args]")
            lines += [f"{k} = {json.dumps(v)}" for k, v in args.items()]
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _to_flow(args):
    from ..flow import model
    from .flow import _flow_path

    session = _load_session(args["session_id"])
    name = args["name"]
    text = steps_to_flow_toml(name, args.get("description") or f"recorded: {session.get('task') or session['session_id']}",
                              session["steps"], secret_name=args.get("secret_name", "recorded_password"))
    try:
        flow = model.parse(text, name=name)
        issues = model.validate(flow)
    except model.FlowError as e:
        raise ControlError("FLOW_INVALID", f"the recording could not be converted: {e}") from e
    path = _flow_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    hard = [i for i in issues if not i.startswith("WARNING")]
    note = ("no issues" if not issues else "; ".join(issues))
    return ({"ok": not hard, "path": str(path), "issues": issues, "steps": len(flow.step_ids())},
            f"wrote {path} ({len(flow.step_ids())} step(s)) - {note}\n"
            f"next: flow.describe {{'name': {name!r}}}, then flow.approve once it looks right")


OBJ = {"type": "object", "properties": {}}


def register_area(cfg):
    acts = [
        Action("record.start", "Start recording a person's clicks/typing/keys as a background "
               "wad session - stop with record.stop. Only one recording at a time.",
               {"type": "object", "properties": {
                   "task": {"type": "string", "description": "what this recording is for, shown in the audit trail"},
                   "window": {"type": "string", "description": "only record in windows whose title contains this"},
                   "seconds": {"type": "number", "description": "stop by itself after this long"}}},
               _start(cfg), SAFE),
        Action("record.stop", "Stop the active recording and save it as a session.",
               OBJ, _stop(cfg), SAFE),
        Action("record.status", "Whether a recording is active right now.", OBJ, _status, READ),
        Action("record.list", "Every saved recording session.", OBJ, _list, READ),
        Action("record.audit", "A plain-language, step-by-step report of one recorded session "
               "(for compliance, handover, or documenting how a task is done). Never shows a "
               "recorded password.",
               {"type": "object", "required": ["session_id"], "properties": {"session_id": {"type": "string"}}},
               _audit, SAFE),
        Action("record.to_flow", "Turn a recorded session into a draft flow (control/flow) ready "
               "for flow.validate and flow.approve. A recorded password becomes {{ secret:name }}.",
               {"type": "object", "required": ["session_id", "name"], "properties": {
                   "session_id": {"type": "string"}, "name": {"type": "string", "description": "the new flow's name"},
                   "description": {"type": "string"},
                   "secret_name": {"type": "string", "description": "vault name for a recorded password (default recorded_password)"}}},
               _to_flow, SAFE),
    ]
    for a in acts:
        registry.register(a)
    registry.set_area("record", True, count=len(acts))

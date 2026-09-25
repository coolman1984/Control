"""The `flow.*` and `run.*` actions: piece 2 of the master roadmap, the durable flow engine.

Flows are TOML files under `<CONTROL_HOME>/flows/*.toml`, authored by an agent or the owner.
`flow.approve` pins one version (by its sha256) as pre-approved; `flow.run` then runs every step,
including risky ones, without asking again, until the file changes or the approval expires.
An unapproved flow can still be run — `flow.run` itself just becomes risky, one approval box to
start it, and any risky step inside still asks on its own.
"""
from .. import registry
from ..errors import ControlError
from ..registry import READ, RISKY, SAFE, Action
from ..flow import model, engine
from ..store import default as default_store

OBJ = {"type": "object", "properties": {}}


def _flows_dir():
    from .. import config
    return config.home() / "flows"


def _flow_path(name):
    if "/" in name or "\\" in name or ".." in name:
        raise ControlError("USAGE", f"bad flow name {name!r}", "use the plain name, no path")
    return _flows_dir() / f"{name}.toml"


def _load(name):
    path = _flow_path(name)
    if not path.exists():
        raise ControlError("FLOW_NOT_FOUND", f"no flow named {name!r}",
                            f"put it at {path}, or list what's there with flow.list")
    return model.parse_file(path)


def _resolve_flow(name):
    try:
        return _load(name)
    except ControlError:
        return None


def _known_action(name):
    if name in registry.ACTIONS:
        return True
    area = name.split(".", 1)[0]
    info = registry.AREAS.get(area)
    if info is None or not info.get("ok"):
        return None            # can't tell, that area is not loaded on this machine
    return False


def _list(args):
    d = _flows_dir()
    if not d.exists():
        return {"flows": []}, "no flows yet"
    store = default_store()
    rows = []
    for path in sorted(d.glob("*.toml")):
        try:
            flow = model.parse_file(path)
        except model.FlowError as e:
            rows.append({"name": path.stem, "ok": False, "error": str(e)})
            continue
        rows.append({"name": flow.name, "description": flow.description, "steps": len(flow.step_ids()),
                     "approved": store.is_approved(flow.name, flow.hash)})
    text = "\n".join(f"{r['name']:24} {'[approved]' if r.get('approved') else '[needs approval]' if r.get('ok', True) else 'BROKEN: ' + r['error']}"
                      f"  {r.get('description', '')}" for r in rows) or "no flows yet"
    return {"flows": rows}, text


def _describe(args):
    flow = _load(args["name"])
    issues = model.validate(flow, known_action=_known_action)
    store = default_store()
    approved = store.is_approved(flow.name, flow.hash)
    info = {"name": flow.name, "description": flow.description, "concurrency": flow.concurrency,
            "hash": flow.hash, "steps": flow.step_ids(), "issues": issues, "approved": approved}
    text = (f"{flow.name} [{'approved' if approved else 'not approved'}]\n{flow.description}\n"
            f"steps: {', '.join(info['steps'])}\n" + ("\n".join(issues) if issues else "no issues"))
    return info, text


def _validate(args):
    if args.get("text"):
        try:
            flow = model.parse(args["text"], name=args.get("name"))
        except model.FlowError as e:
            return {"ok": False, "issues": [str(e)]}, f"invalid: {e}"
    else:
        flow = _load(args["name"])
    issues = model.validate(flow, known_action=_known_action)
    hard = [i for i in issues if not i.startswith("WARNING")]
    return {"ok": not hard, "issues": issues}, ("\n".join(issues) if issues else "valid, no issues")


def _approve(args):
    flow = _load(args["name"])
    issues = [i for i in model.validate(flow, known_action=_known_action) if not i.startswith("WARNING")]
    if issues:
        raise ControlError("FLOW_INVALID", f"{flow.name} has unresolved issues", "; ".join(issues))
    default_store().set_approval(flow.name, flow.hash, ttl_seconds=args.get("ttl_seconds"))
    return {"ok": True, "name": flow.name, "hash": flow.hash}, f"approved {flow.name} ({flow.hash[:12]})"


def _revoke(args):
    default_store().revoke_approval(args["name"])
    return {"ok": True}, f"revoked approval for {args['name']}"


def _run_tier(args):
    flow = _resolve_flow(args.get("name", ""))
    if flow is None:
        return SAFE               # will fail with FLOW_NOT_FOUND in the handler, nothing to gate
    return SAFE if default_store().is_approved(flow.name, flow.hash) else RISKY


def _run(args):
    flow = _load(args["name"])
    store = default_store()
    pre_approved = store.is_approved(flow.name, flow.hash)
    run = engine.run_flow(flow, args.get("input") or {}, store=store, dry_run=False,
                           pre_approved=pre_approved, resolve_flow=_resolve_flow)
    return _run_summary(run)


def _dry_run(args):
    flow = _load(args["name"])
    run = engine.run_flow(flow, args.get("input") or {}, store=default_store(), dry_run=True,
                           resolve_flow=_resolve_flow)
    return _run_summary(run)


def _run_summary(run):
    payload = {"id": run["id"], "flow": run["flow_name"], "status": run["status"], "cursor": run["cursor"],
               "steps": run["context"]["steps"], "error": run.get("error"), "wait_for": run.get("wait_for")}
    text = f"run {run['id']} ({run['flow_name']}): {run['status']}"
    if run.get("error"):
        text += f"\n  {run['error']}"
    if run.get("wait_for"):
        text += f"\n  waiting: {run['wait_for'].get('prompt')}"
    return payload, text


def _run_list(args):
    rows = default_store().list_runs(flow_name=args.get("flow"), status=args.get("status"),
                                      limit=args.get("limit") or 50)
    text = "\n".join(f"{r['id']}  {r['flow_name']:20} {r['status']:16} step {r['cursor']}" for r in rows) or "no runs yet"
    return {"runs": [{"id": r["id"], "flow": r["flow_name"], "status": r["status"], "cursor": r["cursor"],
                      "created_at": r["created_at"]} for r in rows]}, text


def _run_get(args):
    run = default_store().get_run(args["id"])
    if run is None:
        raise ControlError("RUN_NOT_FOUND", f"no run {args['id']!r}")
    steps = default_store().steps_for(args["id"])
    payload, text = _run_summary(run)
    payload["step_log"] = steps
    return payload, text


def _run_cancel(args):
    run = engine.cancel_run(args["id"], store=default_store())
    return {"ok": True, "id": run["id"], "status": run["status"]}, f"cancelled {run['id']}"


def _run_resume_tier(args):
    run = default_store().get_run(args.get("id", ""))
    if run is None:
        return SAFE
    return SAFE if default_store().is_approved(run["flow_name"], run["flow_hash"]) else RISKY


def _run_resume(args):
    run = engine.resume_run(args["id"], store=default_store(), resolve_flow=_resolve_flow,
                             answer=args.get("answer"), force=bool(args.get("force")))
    return _run_summary(run)


def _run_retry_step(args):
    store = default_store()
    run = store.get_run(args["id"])
    if run is None:
        raise ControlError("RUN_NOT_FOUND", f"no run {args['id']!r}")
    if run["status"] not in ("failed", "needs_attention"):
        raise ControlError("RUN_NOT_RESUMABLE", f"run {run['id']} is {run['status']}")
    result = engine.resume_run(args["id"], store=store, resolve_flow=_resolve_flow, force=True)
    return _run_summary(result)


def register_area(cfg):
    acts = [
        Action("flow.list", "Every flow file found, whether it is currently approved to run unattended.",
               OBJ, _list, READ),
        Action("flow.describe", "One flow's steps, hash and any validation issues.",
               {"type": "object", "required": ["name"], "properties": {"name": {"type": "string"}}},
               _describe, READ),
        Action("flow.validate", "Check a flow (by name, or raw TOML text) for structural problems before approving it.",
               {"type": "object", "properties": {"name": {"type": "string"}, "text": {"type": "string",
                "description": "raw TOML to validate instead of a saved file"}}}, _validate, READ),
        Action("flow.approve", "Pin one flow version as pre-approved: its risky steps then run without asking again "
               "until the file changes. Any edit invalidates this.",
               {"type": "object", "required": ["name"], "properties": {
                   "name": {"type": "string"},
                   "ttl_seconds": {"type": "integer", "description": "expire the approval after this long (default: never)"}}},
               _approve, RISKY, why="this authorizes every risky step in the flow to run unattended from now on"),
        Action("flow.revoke", "Remove a flow's approval; its next run asks again.",
               {"type": "object", "required": ["name"], "properties": {"name": {"type": "string"}}}, _revoke, SAFE),
        Action("flow.run", "Run a flow to completion or to its first pause/failure. Approved flows run unattended; "
               "an unapproved flow asks once to start, then each risky step inside still asks on its own.",
               {"type": "object", "required": ["name"], "properties": {
                   "name": {"type": "string"}, "input": {"type": "object", "description": "flow input values"}}},
               _run, _run_tier, why="this flow is not pre-approved (flow.approve), so starting it needs a yes"),
        Action("flow.dry_run", "Preview what a flow would do without running any step for real.",
               {"type": "object", "required": ["name"], "properties": {
                   "name": {"type": "string"}, "input": {"type": "object"}}}, _dry_run, READ),
        Action("run.list", "Recent and active runs, newest first.",
               {"type": "object", "properties": {"flow": {"type": "string"}, "status": {"type": "string"},
                "limit": {"type": "integer"}}}, _run_list, READ),
        Action("run.get", "One run's status, resolved step outputs and full step log.",
               {"type": "object", "required": ["id"], "properties": {"id": {"type": "string"}}}, _run_get, READ),
        Action("run.cancel", "Stop a running or waiting run; already-completed steps are not undone.",
               {"type": "object", "required": ["id"], "properties": {"id": {"type": "string"}}}, _run_cancel, SAFE),
        Action("run.resume", "Continue a waiting run (answering its ask_human step) or retry after a failure.",
               {"type": "object", "required": ["id"], "properties": {
                   "id": {"type": "string"}, "answer": {"type": "object", "description": "answers for an ask_human step"},
                   "force": {"type": "boolean", "description": "re-run the interrupted step even if not idempotent"}}},
               _run_resume, _run_resume_tier, why="this flow is not pre-approved, so continuing it needs a yes"),
        Action("run.retry_step", "Deliberately retry the step a run stopped on (needs_attention or failed), even if "
               "it is not marked idempotent -- only for a person who has checked it did not double-run.",
               {"type": "object", "required": ["id"], "properties": {"id": {"type": "string"}}},
               _run_retry_step, RISKY, why="retrying a non-idempotent step by force can repeat its side effect"),
    ]
    for a in acts:
        registry.register(a)
    registry.set_area("flow", True, count=sum(1 for a in acts if a.area == "flow"))
    registry.set_area("run", True, count=sum(1 for a in acts if a.area == "run"))

"""Executes one flow, step by step, checkpointing into the store so a run can resume after a
crash without redoing completed steps or re-asking an already-answered approval.

Design choices (see docs/plans/2026-09-25-control-master-roadmap.md, piece 2):
- Only *top-level* steps are resume points. `if`/`for_each` run their nested steps synchronously,
  start to finish, as one unit; if the process dies partway through one, the whole compound step
  is treated like a non-idempotent step (see below) rather than resuming mid-loop. This keeps the
  resume logic honest instead of silently guessing where inside a loop it is safe to continue.
- A step interrupted by a crash (a "started" checkpoint with no "done"/"failed" following it) is
  only re-run automatically if it declared `idempotent = true`; otherwise the run stops at
  `needs_attention` for a person (or `run.retry_step`) to look at — never re-run blind.
- `wait_for` only polls actions whose tier resolves to "read" — a risky action polled every few
  seconds would either spam the approval box or repeat a side effect.
- `ask_human` and `wait_for` may not be nested inside `if`/`for_each`/`parallel` (enforced by
  `model.validate`): pausing only makes sense at a point the engine can actually resume from.
- Taint tracking (piece 4): a step whose action is `untrusted_output=True` (its result carries a
  web page, an e-mail, OCR text — content someone outside could have written) marks its own step
  id as tainted. A later *risky* action step whose raw args mention `steps.<tainted id>` is run
  with the flow's own pre-approval switched off for that one call, even inside an approved flow —
  on an unattended box nobody is there to answer the resulting approval box, so it fails closed
  (`APPROVAL_UNAVAILABLE`) instead of letting injected content silently reach a risky action. v1
  scope: direct `action`/`parallel` step args only; a value that passes through `set` into `vars`,
  or through `if`'s `that`/`for_each`'s `items`, is not tracked as tainted.
"""
import re
import secrets
import threading
import time

from .. import registry, runner, vault
from ..errors import ControlError
from . import expr
from .expr import SecretRef
from .model import parse as parse_flow

_UI_LOCK = threading.Lock()


class _StepFailed(Exception):
    def __init__(self, code, text):
        super().__init__(text)
        self.code, self.text = code, text


class _Waiting(Exception):
    def __init__(self, info):
        super().__init__("waiting for a person")
        self.info = info


def new_run_id():
    return time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(4)


def _ui_gate(action_name, tier):
    area = action_name.split(".", 1)[0]
    if area in ("win", "web") and tier != registry.READ:
        return _UI_LOCK
    return None


def _secret_key_names(value, names=None):
    """Every top-level dict key, anywhere in this structure, whose value is (or contains) a
    SecretRef - matches journal.mask()'s own key-name-at-any-depth semantics, so force_secret_keys
    reliably hides it there even if the key itself doesn't look secret-shaped."""
    names = set() if names is None else names
    if isinstance(value, dict):
        for k, v in value.items():
            if isinstance(v, SecretRef) or (isinstance(v, (dict, list)) and _contains_secret(v)):
                names.add(k)
            _secret_key_names(v, names)
    elif isinstance(value, list):
        for v in value:
            _secret_key_names(v, names)
    return names


def _contains_secret(value):
    if isinstance(value, SecretRef):
        return True
    if isinstance(value, dict):
        return any(_contains_secret(v) for v in value.values())
    if isinstance(value, list):
        return any(_contains_secret(v) for v in value)
    return False


def _fill_secrets(value, *, preview):
    """Replace every SecretRef with either a display placeholder (`preview=True`, used by
    dry_run) or the real value from the vault (a real run) - the last thing that happens to a
    step's arguments before they reach `execute()`."""
    if isinstance(value, SecretRef):
        return f"<secret:{value.name}>" if preview else vault.get_secret(value.name)
    if isinstance(value, dict):
        return {k: _fill_secrets(v, preview=preview) for k, v in value.items()}
    if isinstance(value, list):
        return [_fill_secrets(v, preview=preview) for v in value]
    return value


_STEP_REF = re.compile(r"steps\.([A-Za-z_][A-Za-z0-9_]*)")


def _tainted_sources(raw_value, tainted_ids):
    """Which of `tainted_ids` this *unresolved* args structure's templates mention — scanned
    before resolution, since resolving replaces `{{ steps.x... }}` with a plain value that no
    longer says where it came from."""
    if not tainted_ids:
        return set()
    found = set()

    def walk(v):
        if isinstance(v, str):
            found.update(m.group(1) for m in _STEP_REF.finditer(v) if m.group(1) in tainted_ids)
        elif isinstance(v, dict):
            for vv in v.values():
                walk(vv)
        elif isinstance(v, list):
            for vv in v:
                walk(vv)

    walk(raw_value)
    return found


def _mark_if_untrusted(ctx, step_id, action_name):
    try:
        if registry.get(action_name).untrusted_output:
            ctx.setdefault("tainted", []).append(step_id)
    except ControlError:
        pass


def _call(action_name, args, execute, pre_approved, force_secret_keys=()):
    try:
        action = registry.get(action_name)
        tier = action.tier_for(args)
    except ControlError:
        tier = None
    lock = _ui_gate(action_name, tier) if tier else None
    if lock:
        with lock:
            return execute(action_name, args, pre_approved=pre_approved, force_secret_keys=force_secret_keys)
    return execute(action_name, args, pre_approved=pre_approved, force_secret_keys=force_secret_keys)


def _dispatch(step, ctx, store, run_id, idx, execute, dry_run, pre_approved, resolve_flow, answer):
    t = step.type
    if t == "action":
        raw_args = step.get("args") or {}
        resolved = expr.resolve(raw_args, ctx)
        if dry_run:
            return {"ok": True, "_dry_run": True, "would_run": step.get("action"),
                    "args": _fill_secrets(resolved, preview=True)}
        secret_keys = _secret_key_names(resolved)
        args = _fill_secrets(resolved, preview=False)
        tainted = _tainted_sources(raw_args, set(ctx.get("tainted", [])))
        payload, text = _call(step.get("action"), args, execute, pre_approved and not tainted,
                               force_secret_keys=secret_keys)
        if not payload.get("ok", True):
            raise _StepFailed(payload.get("code", "STEP_FAILED"), text or payload.get("message", "step failed"))
        _mark_if_untrusted(ctx, step.id, step.get("action"))
        return payload

    if t == "set":
        values = expr.resolve(step.get("vars") or {}, ctx)
        ctx["vars"].update(values)
        return {"ok": True, "set": sorted(values)}

    if t == "assert":
        value = expr.resolve_template(step.get("that"), ctx)
        has_equals = "equals" in step.raw
        ok = (value == step.get("equals")) if has_equals else expr.truthy(value)
        if not ok:
            want = repr(step.get("equals")) if has_equals else "a truthy value"
            raise _StepFailed("ASSERT_FAILED", f"{step.id}: expected {want}, got {value!r}")
        return {"ok": True, "value": value}

    if t == "if":
        value = expr.resolve_template(step.get("that"), ctx)
        has_equals = "equals" in step.raw
        matched = (value == step.get("equals")) if has_equals else expr.truthy(value)
        if not matched:
            return {"ok": True, "matched": False}
        _run_nested(step.steps, ctx, store, run_id, idx, execute, dry_run, pre_approved, resolve_flow)
        return {"ok": True, "matched": True}

    if t == "for_each":
        items = expr.resolve_template(step.get("items"), ctx)
        if not isinstance(items, list):
            raise _StepFailed("FOR_EACH_NOT_A_LIST", f"{step.id}: items resolved to {type(items).__name__}, not a list")
        as_name = step.get("as", "item")
        stop_on_error = bool(step.get("stop_on_error", False))
        results, failures = [], []
        for i, item in enumerate(items):
            loop_ctx = {"input": ctx["input"], "steps": dict(ctx["steps"]),
                        "vars": {**ctx["vars"], as_name: item, f"{as_name}_index": i},
                        "tainted": list(ctx.get("tainted", []))}
            try:
                _run_nested(step.steps, loop_ctx, store, run_id, f"{idx}.{i}", execute, dry_run, pre_approved, resolve_flow)
                results.append({"index": i, "ok": True})
                ctx["steps"].update(loop_ctx["steps"])
                for t_id in loop_ctx.get("tainted", []):
                    if t_id not in ctx.get("tainted", []):
                        ctx.setdefault("tainted", []).append(t_id)
            except _StepFailed as e:
                failures.append({"index": i, "code": e.code, "error": e.text})
                if stop_on_error:
                    break
        output = {"ok": not (stop_on_error and failures), "count": len(items),
                  "succeeded": len(results), "failed": len(failures), "failures": failures}
        if stop_on_error and failures:
            raise _StepFailed("FOR_EACH_STOPPED", f"{step.id}: item {failures[-1]['index']} failed: {failures[-1]['error']}")
        return output

    if t == "parallel":
        from concurrent.futures import ThreadPoolExecutor
        actions = step.get("actions") or []
        max_workers = step.get("max_workers") or len(actions) or 1
        resolved = [(a["id"], a["action"], expr.resolve(a.get("args") or {}, ctx)) for a in actions]
        if dry_run:
            return {aid: {"ok": True, "_dry_run": True, "would_run": aname, "args": _fill_secrets(aargs, preview=True)}
                    for aid, aname, aargs in resolved}
        tainted_set = set(ctx.get("tainted", []))
        out, errs, any_untrusted = {}, [], False
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {}
            for a, (aid, aname, aargs) in zip(actions, resolved):
                own_tainted = _tainted_sources(a.get("args") or {}, tainted_set)
                fut = pool.submit(_call, aname, _fill_secrets(aargs, preview=False), execute,
                                   pre_approved and not own_tainted, force_secret_keys=_secret_key_names(aargs))
                futures[fut] = aid, aname
            for fut in futures:
                aid, aname = futures[fut]
                payload, text = fut.result()
                out[aid] = payload
                if not payload.get("ok", True):
                    errs.append(f"{aid}: {text}")
                elif not any_untrusted:
                    try:
                        any_untrusted = registry.get(aname).untrusted_output
                    except ControlError:
                        pass
        if errs:
            raise _StepFailed("PARALLEL_FAILED", "; ".join(errs))
        if any_untrusted:
            ctx.setdefault("tainted", []).append(step.id)
        return out

    if t == "wait_for":
        args = expr.resolve(step.get("args") or {}, ctx)
        interval_s, timeout_s = step.get("interval_s", 5), step.get("timeout_s", 300)
        action_name = step.get("action")
        try:
            tier = registry.get(action_name).tier_for(args)
        except ControlError as e:
            raise _StepFailed(e.code, e.message) from None
        if tier != registry.READ:
            raise _StepFailed("WAIT_FOR_NOT_READ", f"{step.id}: wait_for only polls read actions, {action_name} is {tier}")
        if dry_run:
            return {"ok": True, "_dry_run": True, "would_poll": action_name, "args": args}
        deadline = time.monotonic() + timeout_s
        while True:
            payload, _ = execute(action_name, args)
            value = expr.resolve_template(step.get("that"), {**ctx, "result": payload})
            matched = (value == step.get("equals")) if "equals" in step.raw else expr.truthy(value)
            if matched:
                return payload
            if time.monotonic() >= deadline:
                raise _StepFailed("WAIT_FOR_TIMEOUT", f"{step.id}: condition not met within {timeout_s}s")
            time.sleep(interval_s)

    if t == "ask_human":
        if answer is None:
            raise _Waiting({"prompt": step.get("prompt"), "fields": step.get("fields") or [], "step_id": step.id})
        for f in step.get("fields") or []:
            if f.get("required") and answer.get(f["name"]) in (None, ""):
                raise _StepFailed("ANSWER_INCOMPLETE", f"{step.id}: {f['name']} is required")
        return {"ok": True, "answer": answer}

    if t == "call_flow":
        sub_name = step.get("flow")
        if resolve_flow is None:
            raise _StepFailed("FLOW_NOT_FOUND", f"{step.id}: no flow loader given to run {sub_name!r}")
        sub_flow = resolve_flow(sub_name)
        if sub_flow is None:
            raise _StepFailed("FLOW_NOT_FOUND", f"{step.id}: no flow named {sub_name!r}")
        sub_input = expr.resolve(step.get("input") or {}, ctx)
        sub_pre_approved = dry_run or store.is_approved(sub_flow.name, sub_flow.hash)
        sub = run_flow(sub_flow, sub_input, store=store, execute=execute, dry_run=dry_run,
                        parent_run_id=run_id, pre_approved=sub_pre_approved, resolve_flow=resolve_flow)
        if sub["status"] == "waiting":
            raise _StepFailed("CALL_FLOW_WAITING", f"{step.id}: sub-flow {sub_name!r} needs a person (ask_human); "
                                                    f"call_flow does not support pausing sub-flows yet")
        if sub["status"] != "done":
            raise _StepFailed("CALL_FLOW_FAILED", f"{step.id}: sub-flow {sub_name!r} ended {sub['status']}: {sub.get('error')}")
        return {"run_id": sub["id"], "status": sub["status"], "steps": sub["context"]["steps"]}

    raise _StepFailed("UNKNOWN_STEP_TYPE", f"{step.id}: {t}")


def _run_nested(steps, ctx, store, run_id, parent_idx, execute, dry_run, pre_approved, resolve_flow):
    for j, s in enumerate(steps):
        output = _execute_step(s, ctx, store, run_id, f"{parent_idx}.{j}", execute, dry_run,
                                pre_approved, resolve_flow, answer=None)
        ctx["steps"][s.id] = output


def _execute_step(step, ctx, store, run_id, idx, execute, dry_run, pre_approved, resolve_flow, answer):
    retry = step.get("retry") or {}
    attempts = max(1, retry.get("attempts", 1))
    backoff = retry.get("backoff_s", 0)
    retry_on = retry.get("on", "*")
    last = None
    for attempt in range(1, attempts + 1):
        store.start_step(run_id, str(idx), step.id, attempt, {"type": step.type})
        try:
            try:
                output = _dispatch(step, ctx, store, run_id, idx, execute, dry_run, pre_approved, resolve_flow, answer)
            except ControlError as e:              # a bad template etc. is a step failure, not a crash
                raise _StepFailed(e.code, e.message) from e
            store.finish_step(run_id, str(idx), attempt, "done", output=output)
            return output
        except _Waiting:
            store.finish_step(run_id, str(idx), attempt, "waiting")
            raise
        except _StepFailed as e:
            store.finish_step(run_id, str(idx), attempt, "failed", error=e.text)
            if dry_run:
                return {"ok": True, "_dry_run": True, "note": f"cannot preview further: {e.text}"}
            retryable = retry_on == "*" or e.code in (retry_on or [])
            last = e
            if attempt < attempts and retryable:
                if backoff:
                    time.sleep(backoff)
                continue
            raise
    raise last


def _advance(flow, run_id, store, execute, dry_run, resolve_flow, pre_approved=False, force_first=False, answer=None):
    run = store.get_run(run_id)
    ctx = run["context"]
    start_idx = run["cursor"]
    idx = start_idx
    steps = flow.steps

    if not force_first and idx < len(steps):
        last = store.last_attempt(run_id, str(idx))
        if last and last["status"] == "started" and not steps[idx].get("idempotent", False):
            store.update_run(run_id, status="needs_attention",
                              error=f"step {steps[idx].id!r} was interrupted mid-run and is not marked idempotent; "
                                    f"use run.retry_step to retry it deliberately")
            return store.get_run(run_id)

    while idx < len(steps):
        step = steps[idx]
        step_answer = answer if idx == start_idx else None
        try:
            output = _execute_step(step, ctx, store, run_id, idx, execute, dry_run, pre_approved,
                                    resolve_flow, step_answer)
        except _Waiting as w:
            store.update_run(run_id, status="waiting", cursor=idx, context=ctx, wait_for=w.info)
            return store.get_run(run_id)
        except _StepFailed as e:
            store.update_run(run_id, status="failed", cursor=idx, context=ctx, error=f"{e.code}: {e.text}")
            return store.get_run(run_id)
        ctx["steps"][step.id] = output
        idx += 1
        store.update_run(run_id, cursor=idx, context=ctx)

    store.update_run(run_id, status="done", cursor=idx, context=ctx)
    return store.get_run(run_id)


def run_flow(flow, input_, *, store, execute=None, dry_run=False, run_id=None, parent_run_id=None,
             pre_approved=False, resolve_flow=None):
    execute = execute or runner.execute
    run_id = run_id or new_run_id()
    if flow.concurrency == "single" and not dry_run:
        if not store.acquire_lock(flow.name, run_id):
            holder = store.lock_holder(flow.name)
            store.create_run(run_id, flow.name, flow.hash, input_, flow_text=flow.text,
                              dry_run=dry_run, parent_run_id=parent_run_id)
            store.update_run(run_id, status="failed", error=f"another run of {flow.name} is already active: {holder}")
            return store.get_run(run_id)
    store.create_run(run_id, flow.name, flow.hash, input_, flow_text=flow.text, dry_run=dry_run,
                      parent_run_id=parent_run_id)
    result = _advance(flow, run_id, store, execute, dry_run, resolve_flow, pre_approved=pre_approved)
    if flow.concurrency == "single" and not dry_run and result["status"] not in ("running", "waiting"):
        store.release_lock(flow.name)
    return result


def resume_run(run_id, *, store, execute=None, resolve_flow=None, answer=None, force=False):
    execute = execute or runner.execute
    run = store.get_run(run_id)
    if run is None:
        raise ControlError("RUN_NOT_FOUND", f"no run {run_id!r}")
    if run["status"] not in ("waiting", "needs_attention", "failed", "running"):
        raise ControlError("RUN_NOT_RESUMABLE", f"run {run_id} is {run['status']}, not waiting/failed/needs_attention")
    # a run still marked "running" from a fresh process is always a crash: this engine never
    # returns control to a caller while status is "running" (see _advance/run_flow), so nothing
    # legitimate leaves a run in that state for another process to find
    flow = parse_flow(run["flow_text"], name=run["flow_name"])
    if flow.concurrency == "single" and not run["dry_run"]:
        if not store.acquire_or_confirm_lock(flow.name, run_id):
            raise ControlError("FLOW_LOCKED", f"another run of {flow.name} is active: {store.lock_holder(flow.name)}")
    pre_approved = bool(run["dry_run"]) or store.is_approved(flow.name, flow.hash)
    store.update_run(run_id, status="running", error=None)
    result = _advance(flow, run_id, store, execute, bool(run["dry_run"]), resolve_flow,
                       pre_approved=pre_approved, force_first=force, answer=answer)
    if flow.concurrency == "single" and not run["dry_run"] and result["status"] not in ("running", "waiting"):
        store.release_lock(flow.name)
    return result


def cancel_run(run_id, *, store):
    run = store.get_run(run_id)
    if run is None:
        raise ControlError("RUN_NOT_FOUND", f"no run {run_id!r}")
    if run["status"] not in ("running", "waiting", "needs_attention"):
        raise ControlError("RUN_NOT_CANCELLABLE", f"run {run_id} already {run['status']}")
    store.update_run(run_id, status="cancelled")
    store.release_lock(run["flow_name"])
    return store.get_run(run_id)

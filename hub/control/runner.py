"""The one path every action takes: validate -> approve if risky -> run -> journal."""
import time

from . import journal as journal_mod
from . import registry, safety
from .errors import ControlError
from .registry import READ, RISKY

UNDO_ACTIONS = {"sys.undo"}       # the only actions an undo step may run automatically

_REFUSALS = {
    safety.NO: ("APPROVAL_DENIED", "the person said no",
                "do not retry the same action; ask the person what to do instead"),
    safety.TIMEOUT: ("APPROVAL_TIMEOUT", "no answer to the approval box in time",
                     "ask the person in chat first, then retry once"),
    safety.UNAVAILABLE: ("APPROVAL_UNAVAILABLE", "there is no desktop to show the approval box on",
                         "start Control from the person's signed-in desktop session"),
}


def execute(name, args=None, *, journal=None, approver=None, pre_approved=False, force_secret_keys=()):
    journal = journal or journal_mod.default()
    approver = approver or safety.approve
    started = time.monotonic()
    tier, approval, undo = None, None, None
    args = dict(args or {})
    try:
        action = registry.get(name)
        args = registry.validate(action, args)
        if safety.targets_approval_box(name, args):
            raise ControlError("REFUSED", "actions may not target Control's approval box",
                               "only the person answers that box")
        tier = action.tier_for(args)
        if action.area in ("win", "web") and tier != READ and safety.approval_box_open():
            raise ControlError("REFUSED", "an approval box is open; UI actions wait until the person answers it",
                               "wait, then retry")
        if tier == RISKY and not pre_approved:
            approve_args = journal_mod.mask(args, name, force_keys=force_secret_keys) if force_secret_keys else args
            if name == "control.undo":
                try:
                    entry = journal.get(args.get("id"))
                    approve_args = {"id": args.get("id"), "steps": entry.get("undo") or []}
                except ControlError:
                    pass                                # let the normal NOT_FOUND surface below
            approval = approver(name, approve_args, action.why or "this action is marked risky")
            if approval not in (safety.YES, safety.OFF):
                raise ControlError(*_REFUSALS.get(approval, _REFUSALS[safety.NO]))
        payload, text = action.handler(args)
        payload = dict(payload)
        undo = payload.pop("_undo", None)
        payload.setdefault("ok", True)
    except ControlError as e:
        payload, text = e.payload(), e.text()
    except Exception as e:                   # a misbehaving tool must never kill the server
        payload = {"ok": False, "code": "INTERNAL", "message": f"{type(e).__name__}: {e}",
                   "hint": "this is a Control or tool bug; check control.journal and retry once"}
        text = f"ERROR INTERNAL: {payload['message']}\n  hint: {payload['hint']}"
    if tier is not None and tier != READ:
        payload["journal_id"] = journal.append(
            action=name, args=args, tier=tier, approval=approval, ok=payload.get("ok", True),
            code=payload.get("code"), ms=int((time.monotonic() - started) * 1000), undo=undo,
            force_secret_keys=force_secret_keys)
    return payload, text


def undo(eid, *, journal=None):
    journal = journal or journal_mod.default()
    entry = journal.get(eid)
    if entry.get("undone"):
        raise ControlError("ALREADY_UNDONE", f"{eid} was already undone")
    steps = entry.get("undo") or []
    if not steps:
        raise ControlError("NOT_UNDOABLE", f"{entry['action']} cannot be undone automatically",
                           "reverse it by hand, or ask the person")
    for step in steps:
        if step.get("action") not in UNDO_ACTIONS:
            raise ControlError("UNDO_REFUSED", f"undo step {step.get('action')!r} is not an allowed undo action",
                               f"only {', '.join(sorted(UNDO_ACTIONS))} may run automatically as undo")
    texts = []
    for step in steps:
        payload, text = execute(step["action"], step["args"], journal=journal, pre_approved=True)
        texts.append(text)
        if not payload.get("ok", True):
            raise ControlError("UNDO_FAILED", text, "nothing further was undone; see control.journal")
    journal.mark_undone(eid)
    return {"ok": True, "undone": eid, "steps": len(steps)}, "\n".join(texts)

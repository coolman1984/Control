"""The control.* actions: what is loaded, find, describe, journal, undo, doctor."""
import sys

from .. import __version__, journal, registry
from ..registry import READ, RISKY, Action

OBJ = {"type": "object", "properties": {}}


def _areas(args):
    rows = dict(sorted(registry.AREAS.items()))
    lines = [f"{k:8} {'ok' if v['ok'] else 'OFF'}  {v['count'] if v['ok'] else v['reason']}"
             + (f"\n         hint: {v['hint']}" if not v["ok"] and v["hint"] else "")
             for k, v in rows.items()]
    return {"areas": rows}, "\n".join(lines)


def _tier_label(action):
    return action.tier if isinstance(action.tier, str) else "depends on arguments"


def _find(args):
    words = [w.lower() for w in args["query"].split()]
    scored = []
    for a in registry.ACTIONS.values():
        if args.get("area") and a.area != args["area"]:
            continue
        hay = (a.name + " " + a.help).lower()
        score = sum(3 if w in a.name.lower() else 1 for w in words if w in hay)
        if score:
            scored.append((-score, a.name, a))
    found = [{"name": a.name, "tier": _tier_label(a), "help": a.help.split("\n")[0]}
             for _, _, a in sorted(scored)[:15]]
    text = "\n".join(f"{f['name']}  [{f['tier']}]  {f['help']}" for f in found) or "nothing matched"
    return {"actions": found}, text


def _describe(args):
    a = registry.get(args["action"])
    info = {"name": a.name, "tier": _tier_label(a), "help": a.help, "schema": a.schema}
    return info, f"{a.name} [{info['tier']}]\n{a.help}\nargs: {', '.join(a.schema.get('properties', {})) or '(none)'}"


def _journal(args):
    entries = journal.default().last(args.get("last") or 20)
    text = "\n".join(f"{e['id']}  {e['action']:28} {e['tier']:5} {'ok' if e.get('ok') else 'FAILED ' + str(e.get('code'))}"
                     f"{'  (undone)' if e['undone'] else ''}{'  [undoable]' if e.get('undo') and not e['undone'] else ''}"
                     for e in entries) or "journal is empty"
    return {"entries": entries}, text


def _undo(args):
    from .. import runner
    return runner.undo(args["id"])


def _doctor(args):
    payload, text = _areas(args)
    payload.update(python=sys.version.split()[0], version=__version__)
    return payload, f"Control {__version__} on Python {payload['python']}\n{text}"


def register_area(cfg):
    acts = [
        Action("control.areas", "Which areas loaded, how many actions each, and why an area is off.", OBJ, _areas, READ),
        Action("control.find_action", "Search every action by words; returns names, tiers and one-line help.",
               {"type": "object", "required": ["query"], "properties": {
                   "query": {"type": "string", "description": "words to look for"},
                   "area": {"type": "string", "description": "limit to one area: win, web, sys, data, gmes"}}},
               _find, READ),
        Action("control.describe", "Full help, tier and arguments of one action.",
               {"type": "object", "required": ["action"], "properties": {
                   "action": {"type": "string", "description": "action name, e.g. sys.junk"}}}, _describe, READ),
        Action("control.journal", "The last changes Control made, with ids for undo.",
               {"type": "object", "properties": {"last": {"type": "integer", "description": "how many (default 20)"}}},
               _journal, READ),
        Action("control.undo", "Reverse a journaled change by its id, when it can be reversed.",
               {"type": "object", "required": ["id"], "properties": {"id": {"type": "string", "description": "journal id"}}},
               _undo, RISKY, why="undo changes the PC or an app again"),
        Action("control.doctor", "Health of Control itself: Python, version, every area.", OBJ, _doctor, READ),
    ]
    for a in acts:
        registry.register(a)
    registry.set_area("control", True, count=len(acts))

"""One declaration per action. CLI, MCP, the guide and validation are all built from it."""
from dataclasses import dataclass
from typing import Any, Callable

from .errors import ControlError

READ, SAFE, RISKY = "read", "safe", "risky"
TIERS = (READ, SAFE, RISKY)

ACTIONS: dict = {}
AREAS: dict = {}

_TYPES = {"string": str, "integer": int, "number": (int, float), "boolean": bool,
          "array": list, "object": dict}


@dataclass
class Action:
    name: str
    help: str
    schema: dict
    handler: Callable[[dict], tuple]
    tier: Any                       # a tier string, or a function(args) -> tier
    image: bool = False
    why: str = ""
    untrusted_output: bool = False  # its result carries outside content (a web page, an e-mail,
                                     # OCR text): a flow step downstream may not treat it as a
                                     # command (see control/flow/engine.py's taint tracking)

    @property
    def area(self):
        return self.name.split(".", 1)[0]

    def tier_for(self, args):
        tier = self.tier(args) if callable(self.tier) else self.tier
        if tier not in TIERS:
            raise ValueError(f"{self.name}: tier {tier!r} is not one of {TIERS}")
        return tier


def register(action):
    if "." not in action.name:
        raise ValueError(f"action name {action.name!r} must be area.verb")
    if action.name in ACTIONS:
        raise ValueError(f"duplicate action {action.name}")
    ACTIONS[action.name] = action


def set_area(name, ok, count=0, reason="", hint=""):
    AREAS[name] = {"ok": ok, "count": count, "reason": reason, "hint": hint}


def surface_hash():
    """A stable digest of the whole tool surface (name, help, schema, static tier) - piece 4's
    tool-description integrity check (see control/tools_lock.py) hashes this across a restart to
    notice a description or schema that changed without the owner reviewing it. A dynamic tier
    (a function) can't be hashed meaningfully, so it contributes a constant instead of its code."""
    import hashlib
    import json as _json
    rows = sorted((a.name, a.help, _json.dumps(a.schema, sort_keys=True, default=str),
                  a.tier if isinstance(a.tier, str) else "dynamic") for a in ACTIONS.values())
    return hashlib.sha256(_json.dumps(rows, ensure_ascii=False).encode("utf-8")).hexdigest()


def get(name):
    try:
        return ACTIONS[name]
    except KeyError:
        raise ControlError("UNKNOWN_ACTION", f"no action named {name!r}",
                           "call control.find_action to search, or control.areas to see what loaded") from None


def _coerce(name, key, value, spec):
    kind = spec.get("type")
    if value is None or kind not in _TYPES:
        return value
    if kind == "array" and isinstance(value, str):
        value = [value]
    if kind == "integer" and isinstance(value, str):
        try:
            value = int(value)
        except ValueError:
            pass
    if kind == "number" and isinstance(value, str):
        try:
            value = float(value)
        except ValueError:
            pass
    numeric = kind in ("integer", "number")
    if not isinstance(value, _TYPES[kind]) or (numeric and isinstance(value, bool)):
        raise ControlError("USAGE", f"{name}: {key} must be {kind}", spec.get("description", ""))
    if "enum" in spec and value not in spec["enum"]:
        raise ControlError("USAGE", f"{name}: {key} must be one of {spec['enum']}")
    return value


def validate(action, args):
    args = dict(args or {})
    props = action.schema.get("properties", {})
    for key in action.schema.get("required", []):
        if args.get(key) is None:
            raise ControlError("USAGE", f"{action.name}: {key} is required",
                               props.get(key, {}).get("description", ""))
    out = {}
    for key, value in args.items():
        if key not in props:
            raise ControlError("USAGE", f"{action.name}: unknown argument {key!r}",
                               "arguments: " + (", ".join(props) or "(none)"))
        out[key] = _coerce(action.name, key, value, props[key])
    return out

"""Flow definitions: one TOML file per flow, parsed into `Flow`/`Step` and checked by `validate`.

TOML (not YAML) on purpose: the rest of `control/` is stdlib-only (see the piece-1 build plan's
Global Constraints) and Python ships a TOML reader (`tomllib`) but no YAML one. TOML's array-of-
tables syntax also nests cleanly for `if`/`for_each`/`parallel` (confirmed against `tomllib`
before this was written).
"""
import hashlib
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

STEP_TYPES = {"action", "set", "if", "for_each", "parallel", "call_flow", "wait_for", "ask_human", "assert"}
REQUIRED_BY_TYPE = {
    "action": ["action"], "set": ["vars"], "if": ["that", "steps"], "for_each": ["items", "steps"],
    "parallel": ["actions"], "call_flow": ["flow"], "wait_for": ["action", "that"],
    "ask_human": ["prompt"], "assert": ["that"],
}


class FlowError(Exception):
    """A flow file could not be parsed or fails validation."""


@dataclass
class Step:
    id: str
    type: str
    raw: dict
    steps: list = field(default_factory=list)   # nested Steps, for if / for_each

    def get(self, key, default=None):
        return self.raw.get(key, default)


@dataclass
class Flow:
    name: str
    text: str
    description: str = ""
    concurrency: str = "single"          # "single" | "parallel"
    input_schema: dict = field(default_factory=dict)
    steps: list = field(default_factory=list)

    @property
    def hash(self):
        return compute_hash(self.text)

    def step_ids(self):
        out = []

        def walk(steps):
            for s in steps:
                out.append(s.id)
                walk(s.steps)

        walk(self.steps)
        return out


def compute_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _build_steps(raw_steps, path="steps"):
    out = []
    for i, raw in enumerate(raw_steps or []):
        where = f"{path}[{i}]"
        sid = raw.get("id")
        if not sid:
            raise FlowError(f"{where}: every step needs an id")
        stype = raw.get("type")
        if stype not in STEP_TYPES:
            raise FlowError(f"{where} ({sid}): type must be one of {sorted(STEP_TYPES)}, got {stype!r}")
        for key in REQUIRED_BY_TYPE[stype]:
            if raw.get(key) in (None, ""):
                raise FlowError(f"{where} ({sid}): {stype} needs {key!r}")
        nested = _build_steps(raw.get("steps"), f"{where}.steps") if stype in ("if", "for_each") else []
        out.append(Step(id=sid, type=stype, raw=raw, steps=nested))
    return out


def parse(text, name=None):
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise FlowError(f"invalid TOML: {e}") from e
    flow_name = data.get("name") or name
    if not flow_name:
        raise FlowError("flow needs a name (top-level `name = \"...\"`)")
    concurrency = data.get("concurrency", "single")
    if concurrency not in ("single", "parallel"):
        raise FlowError(f"concurrency must be 'single' or 'parallel', got {concurrency!r}")
    steps = _build_steps(data.get("steps"))
    if not steps:
        raise FlowError("flow has no steps")
    return Flow(name=flow_name, text=text, description=data.get("description", ""),
                concurrency=concurrency, input_schema=data.get("input", {}), steps=steps)


def parse_file(path):
    path = Path(path)
    return parse(path.read_text(encoding="utf-8"), name=path.stem)


def validate(flow, *, known_action=None):
    """Returns a list of human-readable problems; empty means the flow is fine to approve/run.
    `known_action(name) -> bool | None` — None means "can't tell, that area isn't loaded here";
    such actions are reported as warnings, not hard errors, so validation works on a machine
    that has not loaded every area (control doctor already tolerates that for the areas below)."""
    issues = []
    seen_ids = set()

    def check_ids(steps):
        for s in steps:
            if s.id in seen_ids:
                issues.append(f"duplicate step id {s.id!r}")
            seen_ids.add(s.id)
            check_ids(s.steps)

    check_ids(flow.steps)

    def check_retry(s):
        retry = s.get("retry")
        if retry:
            if not isinstance(retry.get("attempts", 1), int) or retry.get("attempts", 1) < 1:
                issues.append(f"{s.id}: retry.attempts must be a positive integer")
            on = retry.get("on")
            if on is not None and not (on == "*" or isinstance(on, list)):
                issues.append(f"{s.id}: retry.on must be a list of error codes or \"*\"")

    def check_action_name(s, key="action"):
        name = s.get(key)
        if name is None:
            return
        if "." not in name:
            issues.append(f"{s.id}: {key} {name!r} must be area.verb")
            return
        if known_action is None:
            return
        ok = known_action(name)
        if ok is False:
            issues.append(f"{s.id}: unknown action {name!r}")
        elif ok is None:
            issues.append(f"WARNING {s.id}: {name!r} could not be checked here (its area is not loaded)")

    def walk(steps, nested):
        for s in steps:
            check_retry(s)
            if s.type in ("ask_human", "wait_for") and nested:
                issues.append(f"{s.id}: {s.type} may only appear as a top-level step, not inside "
                               f"if/for_each/parallel (a pause has to be somewhere the engine can resume)")
            if s.type == "action":
                check_action_name(s)
            elif s.type == "wait_for":
                check_action_name(s)
            elif s.type == "call_flow":
                pass  # checked by the caller (needs the flow directory)
            elif s.type == "parallel":
                for a in s.get("actions") or []:
                    if "." not in (a.get("action") or ""):
                        issues.append(f"{s.id}: parallel action {a.get('id')!r} needs area.verb")
            walk(s.steps, nested=True)

    walk(flow.steps, nested=False)
    return issues

# Control — piece 1 (front door) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One app, `control`, that exposes wad, xl2ai, WinSight and the G-MES bot as one set of tiered, journaled actions through one CLI, one MCP server and one agent guide.

**Architecture:** A small registry (`control/registry.py`) holds every action once (name, JSON schema, handler, tier). Area modules fill it: wad and xl2ai are imported in-process; WinSight runs as one child MCP process; G-MES runs as subprocesses. `runner.execute` is the single path every call takes: validate → approval gate for risky → run → journal. CLI, MCP and the guide are all generated from the registry.

**Tech Stack:** Python 3.11 (own venv), stdlib only in `control/` (ctypes for the message box, tomllib, subprocess, threading), pytest. Go 1.26 to build WinSight.

**Spec:** `docs/specs/2026-09-25-control-hub-design.md`

## Global Constraints

- Python `>=3.11`; venv at `I:\Control\hub\.venv` created with `py -3.11`. Always run tests with `.venv\Scripts\python -m pytest`.
- Do not modify any file under `I:\Control\win-agent-desktop`, `I:\Control\Office-Automation`, `I:\Control\opening-nerp-tcode`, `I:\Control\Performance`. WinSight is built into `I:\Control\hub\bin\winsight.exe` (git-ignored).
- Tiers are exactly `"read"`, `"safe"`, `"risky"`.
- Action names are `area.verb` (dotted). MCP tool names are the action name with `.` replaced by `_`.
- Always reach registry state as `registry.ACTIONS` / `registry.AREAS` (module attribute), never `from .registry import ACTIONS` — tests swap these dicts.
- Every failure is `ControlError(code, message, hint)`; payloads always carry `ok`.
- State dir: `%LOCALAPPDATA%\Control` unless `CONTROL_HOME` is set. Journal: `<state>\journal.jsonl`. Config: `<state>\config.toml`.
- Approval box title is exactly `Control — approve?`; default button No; timeout 120 s → treated as No.
- All child processes get `NO_PROXY`/`no_proxy` including `127.0.0.1,localhost`, and `PYTHONIOENCODING=utf-8`.
- Commit after each task; message ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. Arabic / non-ASCII text in arguments and tool output (Arabic sheet names, G-MES Korean labels) must survive CLI, MCP and journal — covered in Task 3 (journal) and Task 11 (MCP) tests.
2. A risky action attempted while no desktop is available must fail closed (`APPROVAL_UNAVAILABLE`), never run — covered in Task 4 and Task 5.
3. A child tool that hangs (WinSight, G-MES) must time out and be killed, and the next call must work again — covered in Task 6.
4. Secrets in arguments (`password`, `token`) must never reach the journal or the approval text in clear — covered in Task 3 and Task 4.
5. One area failing to load (WinSight not built, G-MES folder missing) must leave the others working and be reported with a hint — covered in Task 7 and Task 12.

---

### Task 1: Scaffold, venv, errors, config

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `setup.ps1`, `control/__init__.py`, `control/areas/__init__.py` (empty for now), `control/errors.py`, `control/config.py`
- Test: `tests/conftest.py`, `tests/test_config.py`

**Interfaces:**
- Produces: `ControlError(code, message, hint="")` with `.payload() -> dict`, `.text() -> str`; `config.HUB: Path`, `config.ROOT: Path`, `config.home() -> Path`, `config.load() -> dict`.

- [ ] **Step 1: Create project files**

`pyproject.toml`:
```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "control-hub"
version = "0.1.0"
description = "One front door for wad, xl2ai, WinSight and the G-MES bot"
requires-python = ">=3.11"
dependencies = []

[project.optional-dependencies]
dev = ["pytest>=8"]

[project.scripts]
control = "control.cli:main"

[tool.setuptools]
packages = ["control", "control.areas"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

`.gitignore`:
```
.venv/
bin/
__pycache__/
*.egg-info/
.pytest_cache/
```

`setup.ps1`:
```powershell
# One-time setup of Control on this PC. Safe to re-run.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if (-not (Test-Path .venv)) { py -3.11 -m venv .venv }
.venv\Scripts\python -m pip install -U pip
.venv\Scripts\python -m pip install -e ..\win-agent-desktop -e "..\Office-Automation[all]" -e ".[dev]"
New-Item -ItemType Directory -Force bin | Out-Null
Push-Location ..\Performance
go build -o ..\hub\bin\winsight.exe .\cmd\winsight
Pop-Location
Write-Host "Control is ready: .venv\Scripts\control doctor"
```

`control/__init__.py`:
```python
"""Control - one front door for wad, xl2ai, WinSight and the G-MES bot."""
__version__ = "0.1.0"
```

`control/errors.py`:
```python
"""The one failure shape: a stable code, what happened, what to do next."""


class ControlError(Exception):
    def __init__(self, code, message, hint=""):
        super().__init__(message)
        self.code, self.message, self.hint = code, message, hint

    def payload(self):
        return {"ok": False, "code": self.code, "message": self.message, "hint": self.hint}

    def text(self):
        return f"ERROR {self.code}: {self.message}" + (f"\n  hint: {self.hint}" if self.hint else "")
```

- [ ] **Step 2: Write the failing test**

`tests/conftest.py`:
```python
import pytest

from control import registry


@pytest.fixture(autouse=True)
def control_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("CONTROL_HOME", str(home))
    monkeypatch.delenv("CONTROL_APPROVAL", raising=False)
    return home


@pytest.fixture
def clean_registry(monkeypatch):
    monkeypatch.setattr(registry, "ACTIONS", {})
    monkeypatch.setattr(registry, "AREAS", {})
```
(`control.registry` arrives in Task 2; until then create `control/registry.py` with the single line `ACTIONS, AREAS = {}, {}` so conftest imports.)

`tests/test_config.py`:
```python
from control import config
from control.errors import ControlError


def test_home_follows_env(control_home):
    assert config.home() == control_home


def test_defaults_point_at_sibling_folders():
    cfg = config.load()
    assert cfg["gmes_dir"].endswith("opening-nerp-tcode")
    assert cfg["winsight_exe"].endswith("winsight.exe")
    assert cfg["timeouts"]["gmes"] == 1800
    assert cfg["approval_timeout"] == 120


def test_user_file_overrides_and_merges(control_home):
    control_home.mkdir(parents=True)
    (control_home / "config.toml").write_text('gmes_dir = "D:/g"\n[timeouts]\nsys = 5\n', encoding="utf-8")
    cfg = config.load()
    assert cfg["gmes_dir"] == "D:/g"
    assert cfg["timeouts"]["sys"] == 5 and cfg["timeouts"]["gmes"] == 1800


def test_error_shapes():
    e = ControlError("X", "went wrong", "try y")
    assert e.payload() == {"ok": False, "code": "X", "message": "went wrong", "hint": "try y"}
    assert e.text() == "ERROR X: went wrong\n  hint: try y"
```

- [ ] **Step 3: Create venv and run test to verify it fails**

Run: `powershell -File setup.ps1` (WinSight build may be skipped by commenting nothing — it must succeed; if `go build` fails, stop and report). Then `.venv\Scripts\python -m pytest tests/test_config.py -v`
Expected: FAIL — `control.config` has no attribute `home`.

- [ ] **Step 4: Implement `control/config.py`**

```python
"""Where Control keeps its state, and the few settings that differ per machine."""
import copy
import os
import sys
import tomllib
from pathlib import Path

HUB = Path(__file__).resolve().parents[1]
ROOT = HUB.parent

DEFAULTS = {
    "gmes_dir": str(ROOT / "opening-nerp-tcode"),
    "gmes_python": sys.executable,
    "winsight_exe": str(HUB / "bin" / "winsight.exe"),
    "data_workspace": None,
    "timeouts": {"gmes": 1800, "sys": 300, "sys_fix": 900},
    "approval_timeout": 120,
}


def home():
    env = os.environ.get("CONTROL_HOME")
    if env:
        return Path(env)
    return Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "Control"


def load():
    cfg = copy.deepcopy(DEFAULTS)
    path = home() / "config.toml"
    if path.exists():
        with open(path, "rb") as fh:
            user = tomllib.load(fh)
        for key, value in user.items():
            if isinstance(value, dict) and isinstance(cfg.get(key), dict):
                cfg[key].update(value)
            else:
                cfg[key] = value
    return cfg
```

- [ ] **Step 5: Run tests — expect PASS**, then commit

```bash
git add -A && git commit -m "feat: scaffold Control package, venv setup, errors and config"
```

---

### Task 2: Registry and argument validation

**Files:**
- Create/replace: `control/registry.py`
- Test: `tests/test_registry.py`

**Interfaces:**
- Consumes: `ControlError`.
- Produces: `READ, SAFE, RISKY, TIERS`; `Action(name, help, schema, handler, tier, image=False, why="")` with `.area`, `.tier_for(args) -> str`; `ACTIONS: dict[str, Action]`; `AREAS: dict[str, dict]` (`{"ok": bool, "count": int, "reason": str, "hint": str}`); `register(action)`; `get(name) -> Action`; `validate(action, args) -> dict`; `set_area(name, ok, count=0, reason="", hint="")`. Handler signature: `handler(args: dict) -> tuple[dict, str]`. A handler may put `"_undo": [{"action": str, "args": dict}, ...]` in its payload.

- [ ] **Step 1: Write the failing test** `tests/test_registry.py`:
```python
import pytest

from control import registry
from control.errors import ControlError
from control.registry import Action, READ, RISKY, SAFE

SCHEMA = {"type": "object", "required": ["path"],
          "properties": {"path": {"type": "string", "description": "file"},
                         "count": {"type": "integer"}, "fast": {"type": "boolean"},
                         "mode": {"type": "string", "enum": ["a", "b"]},
                         "ids": {"type": "array", "items": {"type": "string"}}}}


def act(name="t.do", tier=SAFE):
    return Action(name, "does it", SCHEMA, lambda a: ({"ok": True}, "done"), tier)


def test_register_and_get(clean_registry):
    registry.register(act())
    assert registry.get("t.do").area == "t"


def test_unknown_action(clean_registry):
    with pytest.raises(ControlError) as e:
        registry.get("nope.x")
    assert e.value.code == "UNKNOWN_ACTION"


def test_duplicate_and_bad_names_rejected(clean_registry):
    registry.register(act())
    with pytest.raises(ValueError):
        registry.register(act())
    with pytest.raises(ValueError):
        registry.register(act(name="nodot"))


def test_validate_required_unknown_types(clean_registry):
    a = act()
    assert registry.validate(a, {"path": "x", "count": "3"}) == {"path": "x", "count": 3}
    for bad, code_part in [({}, "required"), ({"path": "x", "zzz": 1}, "unknown"),
                           ({"path": "x", "count": "many"}, "integer"),
                           ({"path": "x", "fast": 1}, "boolean"),
                           ({"path": "x", "mode": "c"}, "one of")]:
        with pytest.raises(ControlError) as e:
            registry.validate(a, bad)
        assert e.value.code == "USAGE" and code_part in e.value.message


def test_validate_accepts_arabic_and_single_string_for_array(clean_registry):
    a = act()
    assert registry.validate(a, {"path": "مبيعات.xlsx", "ids": "junk.*"})["ids"] == ["junk.*"]


def test_dynamic_tier(clean_registry):
    a = Action("t.fix", "", {"type": "object", "properties": {"apply": {"type": "boolean"}}},
               lambda a: ({}, ""), lambda args: RISKY if args.get("apply") else READ)
    assert a.tier_for({}) == READ and a.tier_for({"apply": True}) == RISKY
    bad = Action("t.bad", "", {}, lambda a: ({}, ""), "maybe")
    with pytest.raises(ValueError):
        bad.tier_for({})
```

- [ ] **Step 2: Run** `.venv\Scripts\python -m pytest tests/test_registry.py -v` — Expected: FAIL (ImportError `Action`).

- [ ] **Step 3: Implement** `control/registry.py`:
```python
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
```

- [ ] **Step 4: Run tests — expect PASS.** Commit: `feat: action registry with schema validation and tiers`.

---

### Task 3: Journal

**Files:**
- Create: `control/journal.py`
- Test: `tests/test_journal.py`

**Interfaces:**
- Produces: `mask(args) -> dict`; `Journal(path)` with `.append(*, action, args, tier, approval, ok, code=None, ms=0, undo=None) -> str` (entry id), `.entries() -> list[dict]` (oldest first, each with `"undone": bool`), `.last(n) -> list[dict]`, `.get(eid) -> dict` (raises `ControlError("NOT_FOUND")`), `.mark_undone(eid)`; `default() -> Journal` at `config.home()/"journal.jsonl"`.

- [ ] **Step 1: Failing test** `tests/test_journal.py`:
```python
import json

import pytest

from control import journal
from control.errors import ControlError


def test_append_read_and_mask(tmp_path):
    j = journal.Journal(tmp_path / "j.jsonl")
    eid = j.append(action="gmes.run", args={"password": "p@ss", "screens": ["P1"], "ملف": "قيمة"},
                   tier="safe", approval=None, ok=True, ms=5)
    raw = (tmp_path / "j.jsonl").read_text(encoding="utf-8")
    assert "p@ss" not in raw and "قيمة" in raw
    e = j.get(eid)
    assert e["args"]["password"] == "***" and e["undone"] is False


def test_undo_marking_and_last(tmp_path):
    j = journal.Journal(tmp_path / "j.jsonl")
    ids = [j.append(action=f"a.{i}", args={}, tier="safe", approval=None, ok=True) for i in range(3)]
    j.mark_undone(ids[0])
    assert j.get(ids[0])["undone"] is True
    assert [e["action"] for e in j.last(2)] == ["a.1", "a.2"]
    assert len(j.entries()) == 3              # undo markers are not entries


def test_missing_and_corrupt_lines(tmp_path):
    p = tmp_path / "j.jsonl"
    p.write_text('not json\n{"id": "x1", "action": "a.b"}\n', encoding="utf-8")
    j = journal.Journal(p)
    assert j.get("x1")["action"] == "a.b"
    with pytest.raises(ControlError) as e:
        j.get("nope")
    assert e.value.code == "NOT_FOUND"


def test_default_lives_in_home(control_home):
    assert journal.default().path == control_home / "journal.jsonl"
```

- [ ] **Step 2: Run — expect FAIL.**

- [ ] **Step 3: Implement** `control/journal.py`:
```python
"""One append-only record of every change Control made, with how to reverse it."""
import json
import os
import re
import secrets
import time
from pathlib import Path

from . import config
from .errors import ControlError

SECRET = re.compile(r"pass|pwd|secret|token|key|credential", re.I)


def mask(args):
    return {k: ("***" if SECRET.search(str(k)) else v) for k, v in (args or {}).items()}


class Journal:
    def __init__(self, path):
        self.path = Path(path)

    def _write(self, record):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

    def append(self, *, action, args, tier, approval, ok, code=None, ms=0, undo=None):
        eid = time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)
        self._write({"id": eid, "time": time.strftime("%Y-%m-%dT%H:%M:%S"), "action": action,
                     "args": mask(args), "tier": tier, "approval": approval, "ok": ok,
                     "code": code, "ms": ms, "undo": undo,
                     "approval_off": os.environ.get("CONTROL_APPROVAL") == "off"})
        return eid

    def mark_undone(self, eid):
        self._write({"undoes": eid, "time": time.strftime("%Y-%m-%dT%H:%M:%S")})

    def entries(self):
        if not self.path.exists():
            return []
        out, undone = [], set()
        with open(self.path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if "undoes" in rec:
                    undone.add(rec["undoes"])
                elif "id" in rec:
                    out.append(rec)
        for rec in out:
            rec["undone"] = rec["id"] in undone
        return out

    def last(self, n=20):
        return self.entries()[-n:]

    def get(self, eid):
        for rec in self.entries():
            if rec["id"] == eid:
                return rec
        raise ControlError("NOT_FOUND", f"no journal entry {eid!r}", "list them with control.journal")


def default():
    return Journal(config.home() / "journal.jsonl")
```

- [ ] **Step 4: Run — PASS.** Commit: `feat: append-only journal with secret masking and undo marks`.

---

### Task 4: Safety — approval gate

**Files:**
- Create: `control/safety.py`
- Test: `tests/test_safety.py`

**Interfaces:**
- Produces: `APPROVAL_TITLE`; answers `YES, NO, TIMEOUT, UNAVAILABLE, OFF`; `message_box(text, title, timeout_s) -> str`; `describe(action_name, args, reason) -> str`; `approve(action_name, args, reason, *, box=message_box, timeout_s=None) -> str`; `targets_approval_box(action_name, args) -> bool`.

- [ ] **Step 1: Failing test** `tests/test_safety.py`:
```python
from control import safety


def test_off_switch(monkeypatch):
    monkeypatch.setenv("CONTROL_APPROVAL", "off")
    assert safety.approve("win.shell", {}, "r", box=lambda *a: safety.NO) == safety.OFF


def test_box_answers_pass_through():
    for answer in (safety.YES, safety.NO, safety.TIMEOUT, safety.UNAVAILABLE):
        assert safety.approve("win.shell", {"command": "dir"}, "r", box=lambda *a, x=answer: x) == answer


def test_describe_masks_secrets_and_keeps_arabic():
    text = safety.describe("web.type", {"password": "hunter2", "text": "مرحبا"}, "types into a page")
    assert "hunter2" not in text and "مرحبا" in text and "web.type" in text


def test_box_gets_title_and_timeout():
    seen = {}

    def box(text, title, timeout_s):
        seen.update(title=title, timeout=timeout_s)
        return safety.NO

    safety.approve("win.shell", {}, "r", box=box, timeout_s=7)
    assert seen == {"title": "Control — approve?", "timeout": 7}


def test_self_targeting_detected():
    assert safety.targets_approval_box("win.click", {"window": "Control — approve?"})
    assert not safety.targets_approval_box("win.click", {"window": "Notepad"})
    assert not safety.targets_approval_box("sys.health", {"x": "Control — approve?"})
```

- [ ] **Step 2: Run — FAIL.**

- [ ] **Step 3: Implement** `control/safety.py`:
```python
"""Risky actions need the person's yes, asked on their own desktop by Control itself."""
import json
import os
import sys

from . import config
from .journal import mask

APPROVAL_TITLE = "Control — approve?"
YES, NO, TIMEOUT, UNAVAILABLE, OFF = "yes", "no", "timeout", "unavailable", "off"

_MB_YESNO, _MB_ICONQUESTION, _MB_DEFBUTTON2 = 0x4, 0x20, 0x100
_MB_SYSTEMMODAL, _MB_SETFOREGROUND, _MB_TOPMOST = 0x1000, 0x10000, 0x40000


def message_box(text, title, timeout_s):
    if sys.platform != "win32":
        return UNAVAILABLE
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    fn = user32.MessageBoxTimeoutW          # exported by user32 since XP; not in the headers
    fn.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT,
                   wintypes.WORD, wintypes.DWORD]
    fn.restype = ctypes.c_int
    flags = (_MB_YESNO | _MB_ICONQUESTION | _MB_DEFBUTTON2 | _MB_SYSTEMMODAL
             | _MB_SETFOREGROUND | _MB_TOPMOST)
    result = fn(None, text, title, flags, 0, int(timeout_s * 1000))
    if result == 0:
        return UNAVAILABLE                  # no desktop to show it on
    return {6: YES, 7: NO, 32000: TIMEOUT}.get(result, NO)


def describe(action_name, args, reason):
    shown = json.dumps(mask(args), ensure_ascii=False, indent=1, default=str)
    if len(shown) > 1500:
        shown = shown[:1500] + " ..."
    return (f"An AI agent wants to run:\n\n{action_name}\n\n{shown}\n\n"
            f"Why this needs you: {reason}\n\nAllow it?")


def approve(action_name, args, reason, *, box=message_box, timeout_s=None):
    if os.environ.get("CONTROL_APPROVAL") == "off":
        return OFF
    if timeout_s is None:
        timeout_s = config.load()["approval_timeout"]
    return box(describe(action_name, args, reason), APPROVAL_TITLE, timeout_s)


def targets_approval_box(action_name, args):
    if not action_name.startswith("win."):
        return False
    return any(isinstance(v, str) and "approve?" in v and "Control" in v for v in args.values())
```

- [ ] **Step 4: Run — PASS.** Commit: `feat: approval gate with desktop message box`.

---

### Task 5: Runner (the one execution path) and undo

**Files:**
- Create: `control/runner.py`
- Test: `tests/test_runner.py`

**Interfaces:**
- Consumes: registry, journal, safety.
- Produces: `execute(name, args=None, *, journal=None, approver=None, pre_approved=False) -> tuple[dict, str]` (payload always has `ok`; non-read calls add `journal_id`); `undo(eid, *, journal=None) -> tuple[dict, str]`.

- [ ] **Step 1: Failing test** `tests/test_runner.py`:
```python
import pytest

from control import registry, runner, safety
from control.errors import ControlError
from control.journal import Journal
from control.registry import Action, READ, RISKY, SAFE

OBJ = {"type": "object", "properties": {"x": {"type": "string"}, "window": {"type": "string"}}}


@pytest.fixture
def j(tmp_path):
    return Journal(tmp_path / "j.jsonl")


def add(name, tier, handler):
    registry.register(Action(name, "h", OBJ, handler, tier, why="because"))


def test_read_runs_and_is_not_journaled(clean_registry, j):
    add("t.look", READ, lambda a: ({"seen": a.get("x")}, "saw"))
    payload, text = runner.execute("t.look", {"x": "1"}, journal=j)
    assert payload == {"seen": "1", "ok": True} and text == "saw" and j.entries() == []


def test_safe_runs_and_is_journaled_with_undo(clean_registry, j):
    add("t.do", SAFE, lambda a: ({"_undo": [{"action": "t.back", "args": {}}]}, "did"))
    payload, _ = runner.execute("t.do", {}, journal=j)
    e = j.get(payload["journal_id"])
    assert e["tier"] == SAFE and e["undo"] == [{"action": "t.back", "args": {}}] and "_undo" not in payload


@pytest.mark.parametrize("answer,code", [(safety.NO, "APPROVAL_DENIED"),
                                         (safety.TIMEOUT, "APPROVAL_TIMEOUT"),
                                         (safety.UNAVAILABLE, "APPROVAL_UNAVAILABLE")])
def test_risky_refused_without_yes(clean_registry, j, answer, code):
    ran = []
    add("t.boom", RISKY, lambda a: (ran.append(1) or {}, "boom"))
    payload, _ = runner.execute("t.boom", {}, journal=j, approver=lambda *a: answer)
    assert payload["code"] == code and ran == []
    assert j.get(payload["journal_id"])["approval"] == answer


def test_risky_runs_on_yes_and_off(clean_registry, j):
    add("t.boom", RISKY, lambda a: ({}, "boom"))
    for answer in (safety.YES, safety.OFF):
        payload, _ = runner.execute("t.boom", {}, journal=j, approver=lambda *a, x=answer: x)
        assert payload["ok"] is True


def test_errors_become_payloads(clean_registry, j):
    def bad(a):
        raise ControlError("NOPE", "no", "h")
    add("t.bad", SAFE, bad)
    add("t.crash", SAFE, lambda a: 1 / 0)
    assert runner.execute("t.bad", {}, journal=j)[0]["code"] == "NOPE"
    p, text = runner.execute("t.crash", {}, journal=j)
    assert p["code"] == "INTERNAL" and "ZeroDivisionError" in p["message"]
    assert runner.execute("t.missing", {}, journal=j)[0]["code"] == "UNKNOWN_ACTION"
    assert runner.execute("t.bad", {"zzz": 1}, journal=j)[0]["code"] == "USAGE"


def test_self_targeting_refused(clean_registry, j):
    add("win.click", SAFE, lambda a: ({}, "clicked"))
    assert runner.execute("win.click", {"window": "Control — approve?"}, journal=j)[0]["code"] == "REFUSED"


def test_undo_runs_steps_preapproved_and_marks(clean_registry, j):
    calls = []
    add("t.back", RISKY, lambda a: (calls.append(a) or {}, "reversed"))
    add("t.do", SAFE, lambda a: ({"_undo": [{"action": "t.back", "args": {"x": "1"}}]}, "did"))
    eid = runner.execute("t.do", {}, journal=j)[0]["journal_id"]
    payload, _ = runner.undo(eid, journal=j)
    assert payload["ok"] and calls == [{"x": "1"}] and j.get(eid)["undone"]
    with pytest.raises(ControlError) as e:
        runner.undo(eid, journal=j)
    assert e.value.code == "ALREADY_UNDONE"


def test_not_undoable(clean_registry, j):
    add("t.do", SAFE, lambda a: ({}, "did"))
    eid = runner.execute("t.do", {}, journal=j)[0]["journal_id"]
    with pytest.raises(ControlError) as e:
        runner.undo(eid, journal=j)
    assert e.value.code == "NOT_UNDOABLE"
```

- [ ] **Step 2: Run — FAIL.**

- [ ] **Step 3: Implement** `control/runner.py`:
```python
"""The one path every action takes: validate -> approve if risky -> run -> journal."""
import time

from . import journal as journal_mod
from . import registry, safety
from .errors import ControlError
from .registry import READ, RISKY

_REFUSALS = {
    safety.NO: ("APPROVAL_DENIED", "the person said no",
                "do not retry the same action; ask the person what to do instead"),
    safety.TIMEOUT: ("APPROVAL_TIMEOUT", "no answer to the approval box in time",
                     "ask the person in chat first, then retry once"),
    safety.UNAVAILABLE: ("APPROVAL_UNAVAILABLE", "there is no desktop to show the approval box on",
                         "start Control from the person's signed-in desktop session"),
}


def execute(name, args=None, *, journal=None, approver=None, pre_approved=False):
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
        if tier == RISKY and not pre_approved:
            approval = approver(name, args, action.why or "this action is marked risky")
            if approval in _REFUSALS:
                raise ControlError(*_REFUSALS[approval])
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
            code=payload.get("code"), ms=int((time.monotonic() - started) * 1000), undo=undo)
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
    texts = []
    for step in steps:
        payload, text = execute(step["action"], step["args"], journal=journal, pre_approved=True)
        texts.append(text)
        if not payload.get("ok", True):
            raise ControlError("UNDO_FAILED", text, "nothing further was undone; see control.journal")
    journal.mark_undone(eid)
    return {"ok": True, "undone": eid, "steps": len(steps)}, "\n".join(texts)
```

- [ ] **Step 4: Run — PASS.** Commit: `feat: runner with approval gate, journaling and undo`.

---

### Task 6: Child processes — one-shot runs and a persistent MCP child

**Files:**
- Create: `control/proc.py`, `tests/fakes/fake_mcp.py`
- Test: `tests/test_proc.py`

**Interfaces:**
- Produces: `child_env() -> dict`; `run(cmd, *, cwd=None, timeout) -> tuple[int, str, str]` (raises `ControlError("TIMEOUT")`); `McpChild(cmd, *, name, timeout=300)` with `.request(method, params=None, timeout=None) -> dict` (the JSON-RPC `result`), `.call_tool(name, args, timeout=None) -> dict` (the `tools/call` result), `.close()`. Raises `TIMEOUT`, `CHILD_EXITED`, `CHILD_ERROR`.

- [ ] **Step 1: Create the fake server** `tests/fakes/fake_mcp.py`:
```python
"""A tiny MCP server for tests: echo, sleep, die."""
import json
import sys
import time

for line in sys.stdin:
    msg = json.loads(line)
    if "id" not in msg:
        continue
    method, params = msg["method"], msg.get("params") or {}
    if method == "initialize":
        result = {"protocolVersion": "2025-06-18", "capabilities": {}, "serverInfo": {"name": "fake"}}
    elif method == "tools/list":
        result = {"tools": [{"name": "echo", "inputSchema": {"type": "object"}}]}
    elif method == "tools/call":
        name, args = params["name"], params.get("arguments") or {}
        if name == "sleep":
            time.sleep(float(args.get("s", 10)))
        if name == "die":
            sys.exit(1)
        result = {"content": [{"type": "text", "text": json.dumps(args, ensure_ascii=False)}],
                  "structuredContent": args, "isError": False}
    else:
        print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": "nope"}}), flush=True)
        continue
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}, ensure_ascii=False) + "\n")
    sys.stdout.flush()
```

- [ ] **Step 2: Failing test** `tests/test_proc.py`:
```python
import sys
from pathlib import Path

import pytest

from control import proc
from control.errors import ControlError

FAKE = str(Path(__file__).parent / "fakes" / "fake_mcp.py")


def test_env_bypasses_proxy():
    env = proc.child_env()
    assert "127.0.0.1" in env["NO_PROXY"] and "localhost" in env["no_proxy"]
    assert env["PYTHONIOENCODING"] == "utf-8"


def test_run_ok_and_timeout():
    code, out, _ = proc.run([sys.executable, "-c", "print('مرحبا')"], timeout=30)
    assert code == 0 and "مرحبا" in out
    with pytest.raises(ControlError) as e:
        proc.run([sys.executable, "-c", "import time; time.sleep(30)"], timeout=1)
    assert e.value.code == "TIMEOUT"


def test_mcp_child_roundtrip_and_recovery():
    child = proc.McpChild([sys.executable, FAKE], name="fake", timeout=10)
    try:
        assert child.request("tools/list")["tools"][0]["name"] == "echo"
        assert child.call_tool("echo", {"a": "ب"})["structuredContent"] == {"a": "ب"}
        with pytest.raises(ControlError) as e:
            child.call_tool("sleep", {"s": 5}, timeout=1)
        assert e.value.code == "TIMEOUT"
        assert child.call_tool("echo", {"b": 1})["structuredContent"] == {"b": 1}   # restarted
        with pytest.raises(ControlError) as e:
            child.call_tool("die", {})
        assert e.value.code == "CHILD_EXITED"
        assert child.call_tool("echo", {"c": 1})["structuredContent"] == {"c": 1}
        with pytest.raises(ControlError) as e:
            child.request("bogus/method")
        assert e.value.code == "CHILD_ERROR"
    finally:
        child.close()
```

- [ ] **Step 3: Run — FAIL.**

- [ ] **Step 4: Implement** `control/proc.py`:
```python
"""Running other programs safely: time limits, whole-tree kills, no proxy for local traffic."""
import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import __version__
from .errors import ControlError

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def child_env():
    env = dict(os.environ)
    local = "127.0.0.1,localhost"
    current = env.get("NO_PROXY") or env.get("no_proxy") or ""
    env["NO_PROXY"] = env["no_proxy"] = f"{current},{local}" if current else local
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


def kill_tree(pid):
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True,
                       creationflags=_NO_WINDOW)
    else:
        try:
            os.kill(pid, 9)
        except OSError:
            pass


def run(cmd, *, cwd=None, timeout):
    p = subprocess.Popen(cmd, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, env=child_env(), text=True, encoding="utf-8",
                         errors="replace", creationflags=_NO_WINDOW)
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        kill_tree(p.pid)
        p.communicate()
        shown = " ".join(Path(c).name for c in cmd[:3])
        raise ControlError("TIMEOUT", f"{shown} did not finish within {timeout}s",
                           "raise the timeout in config.toml, or check that tool on its own") from None
    return p.returncode, out, err


class McpChild:
    """One long-lived MCP server child, restarted on demand after a crash or timeout."""

    def __init__(self, cmd, *, name, timeout=300):
        self.cmd, self.name, self.timeout = list(cmd), name, timeout
        self.p, self.q, self._id = None, None, 0
        self.lock = threading.Lock()

    @staticmethod
    def _pump(stream, q):
        for line in stream:
            q.put(line)
        q.put(None)

    def _send(self, msg):
        self.p.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
        self.p.stdin.flush()

    def _ensure(self):
        if self.p is not None and self.p.poll() is None:
            return
        self.p = subprocess.Popen(self.cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, env=child_env(), text=True,
                                  encoding="utf-8", errors="replace", creationflags=_NO_WINDOW)
        self.q = queue.Queue()
        threading.Thread(target=self._pump, args=(self.p.stdout, self.q), daemon=True).start()
        self._rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                 "clientInfo": {"name": "control", "version": __version__}}, 30)
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _rpc(self, method, params, timeout):
        self._id += 1
        mid = self._id
        try:
            self._send({"jsonrpc": "2.0", "id": mid, "method": method, "params": params})
        except OSError:
            self.p = None
            raise ControlError("CHILD_EXITED", f"{self.name} stopped unexpectedly",
                               "run control doctor") from None
        deadline = time.monotonic() + timeout
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                self.close()
                raise ControlError("TIMEOUT", f"{self.name} gave no answer within {timeout}s",
                                   "it was stopped; the next call starts it again")
            try:
                line = self.q.get(timeout=left)
            except queue.Empty:
                continue
            if line is None:
                self.p = None
                raise ControlError("CHILD_EXITED", f"{self.name} stopped unexpectedly",
                                   "the next call starts it again; if it repeats, run control doctor")
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if msg.get("id") != mid:
                continue
            if "error" in msg:
                raise ControlError("CHILD_ERROR", f"{self.name}: {msg['error'].get('message', '')}")
            return msg.get("result") or {}

    def request(self, method, params=None, timeout=None):
        with self.lock:
            self._ensure()
            return self._rpc(method, params or {}, timeout or self.timeout)

    def call_tool(self, name, args, timeout=None):
        return self.request("tools/call", {"name": name, "arguments": args or {}}, timeout)

    def close(self):
        if self.p is not None:
            kill_tree(self.p.pid)
            self.p = None
```

- [ ] **Step 5: Run — PASS.** Commit: `feat: child process helpers with timeouts and an MCP client`.

---

### Task 7: Area loader, `control.*` area, and wad → `win.*` / `web.*`

**Files:**
- Create: `control/areas/__init__.py` (replace), `control/areas/core.py`, `control/areas/win.py`
- Test: `tests/test_areas_core.py`, `tests/test_area_win.py`

**Interfaces:**
- Consumes: registry, runner, journal.
- Produces: `areas.load_all(cfg=None)` (idempotent; a failing module becomes `AREAS[name] = {"ok": False, ...}`); each area module exposes `register_area(cfg)`; `win.wad_tier(cmd) -> str`, `win.action_name(cmd) -> str`. Control actions: `control.areas`, `control.find_action`, `control.describe`, `control.journal`, `control.undo`, `control.doctor`.

- [ ] **Step 1: Failing tests**

`tests/test_areas_core.py`:
```python
from control import areas, registry, runner
from control.registry import Action, SAFE


def test_failing_area_is_reported_not_fatal(clean_registry, monkeypatch):
    monkeypatch.setattr(areas, "AREA_MODULES", ["core", "does_not_exist"])
    areas.load_all()
    assert registry.AREAS["control"]["ok"] is True
    assert registry.AREAS["does_not_exist"]["ok"] is False
    assert registry.AREAS["does_not_exist"]["reason"]


def test_find_describe_journal_undo(clean_registry, monkeypatch):
    monkeypatch.setattr(areas, "AREA_MODULES", ["core"])
    areas.load_all()
    registry.register(Action("t.clean", "clean the temp folder", {"type": "object", "properties": {}},
                             lambda a: ({"_undo": [{"action": "t.restore", "args": {}}]}, "cleaned"), SAFE))
    registry.register(Action("t.restore", "restore", {"type": "object", "properties": {}},
                             lambda a: ({}, "restored"), SAFE))
    found = runner.execute("control.find_action", {"query": "temp"})[0]["actions"]
    assert found[0]["name"] == "t.clean"
    assert runner.execute("control.describe", {"action": "t.clean"})[0]["tier"] == SAFE
    eid = runner.execute("t.clean", {})[0]["journal_id"]
    assert runner.execute("control.journal", {"last": 5})[0]["entries"][-1]["id"] == eid
    payload, _ = runner.execute("control.undo", {"id": eid}, approver=lambda *a: "yes")
    assert payload["ok"] and payload["undone"] == eid
```

`tests/test_area_win.py`:
```python
import pytest

from control import registry
from control.registry import READ, RISKY, SAFE

pytest.importorskip("wadlib")


def test_every_wad_command_mapped_with_expected_tiers(clean_registry):
    from control.areas import win
    win.register_area({})
    names = registry.ACTIONS
    assert registry.AREAS["win"]["ok"] and registry.AREAS["web"]["ok"]
    expect = {"win.snapshot": READ, "win.windows": READ, "win.click": SAFE, "win.type": SAFE,
              "win.shell": RISKY, "win.file-write": RISKY, "win.process-kill": RISKY,
              "win.file-read": READ, "win.batch": RISKY, "win.excel-run": RISKY,
              "win.outlook-send": RISKY, "web.snapshot": READ, "web.click": SAFE,
              "web.eval": RISKY, "web.launch": SAFE}
    for name, tier in expect.items():
        assert names[name].tier_for({}) == tier, name
    assert "win.mcp" not in names and "win.record" not in names
    assert not any(n.startswith("win.browser-") for n in names)
    assert names["win.screenshot"].image is True
```

- [ ] **Step 2: Run — FAIL.**

- [ ] **Step 3: Implement**

`control/areas/__init__.py`:
```python
"""Loads every area. One area failing to load never stops the others."""
import importlib

from .. import config, registry

AREA_MODULES = ["core", "win", "data", "sys", "gmes"]


def load_all(cfg=None):
    if registry.AREAS:
        return
    cfg = cfg or config.load()
    for mod in AREA_MODULES:
        try:
            importlib.import_module(f"control.areas.{mod}").register_area(cfg)
        except Exception as e:                       # noqa: BLE001 - reported, not raised
            hint = getattr(e, "hint", "") or "run setup.ps1, then control doctor"
            registry.set_area(mod, False, reason=f"{type(e).__name__}: {getattr(e, 'message', e)}", hint=hint)
```

`control/areas/core.py`:
```python
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
```

`control/areas/win.py`:
```python
"""wad's commands as win.* (desktop apps) and web.* (the browser group)."""
from .. import registry
from ..registry import READ, RISKY, SAFE, Action

SKIP = {"mcp", "record"}
RISKY_WAD = {"excel-run", "outlook-send", "browser-eval", "batch"}
WHY = {"system": "runs commands, writes files or stops processes on this PC",
       "excel-run": "runs a macro inside Excel", "outlook-send": "sends an e-mail",
       "browser-eval": "runs any JavaScript inside a web page",
       "batch": "replays many steps at once without asking again"}


def wad_tier(cmd):
    if cmd.readonly:
        return READ
    if cmd.group == "system" or cmd.name in RISKY_WAD:
        return RISKY
    return SAFE


def action_name(cmd):
    if cmd.group == "browser":
        return "web." + cmd.name.removeprefix("browser-")
    return "win." + cmd.name


def _handler(cmd):
    def handler(args):
        import uiautomation as auto
        from wadlib.cli import execute_guarded
        ns = cmd.namespace(args)
        with auto.UIAutomationInitializerInThread():
            return execute_guarded(cmd.name, ns)
    return handler


def register_area(cfg):
    import wadlib.cli  # noqa: F401 - importing it loads every command module
    from wadlib.registry import COMMANDS, input_schema
    acts = [Action(action_name(c), c.help, input_schema(c), _handler(c), wad_tier(c), image=c.image,
                   why=WHY.get(c.name, WHY.get(c.group, "")))
            for c in COMMANDS.values() if c.mcp and c.name not in SKIP]
    for a in acts:
        registry.register(a)
    registry.set_area("win", True, count=sum(a.area == "win" for a in acts))
    registry.set_area("web", True, count=sum(a.area == "web" for a in acts))
```

- [ ] **Step 4: Run** `.venv\Scripts\python -m pytest tests/test_areas_core.py tests/test_area_win.py -v` — Expected: PASS. If a wad tier in the expectation table differs because wad's flags differ from the table, fix `RISKY_WAD`/`wad_tier`, never the expectation (the table is the spec).

- [ ] **Step 5: Commit** `feat: area loader, control.* actions, wad as win/web areas`.

---

### Task 8: xl2ai → `data.*`

**Files:**
- Create: `control/areas/data.py`
- Test: `tests/test_area_data.py`

**Interfaces:**
- Produces: `data.<tool>` for every entry in `xl2ai.mcp_server.TOOLS`; `prepare`, `save_records` are safe, the rest read. Errors become `DATA_<code>` (xl2ai's `E_` prefix removed).

- [ ] **Step 1: Failing test** `tests/test_area_data.py`:
```python
import pytest

from control import registry, runner
from control.registry import READ, SAFE

pytest.importorskip("xl2ai")


def test_tools_and_tiers(clean_registry):
    from control.areas import data
    data.register_area({"data_workspace": None})
    assert registry.AREAS["data"]["ok"]
    assert registry.ACTIONS["data.start"].tier_for({}) == READ
    assert registry.ACTIONS["data.query"].tier_for({}) == READ
    assert registry.ACTIONS["data.prepare"].tier_for({}) == SAFE
    assert registry.ACTIONS["data.save_records"].tier_for({}) == SAFE


def test_empty_folder_gives_clean_result_not_crash(clean_registry, tmp_path):
    from control.areas import data
    data.register_area({"data_workspace": None})
    payload, text = runner.execute("data.start", {"workspace": str(tmp_path)})
    assert payload.get("code") != "INTERNAL"
    assert payload["ok"] or payload["code"].startswith("DATA_")
```

- [ ] **Step 2: Run — FAIL.**

- [ ] **Step 3: Implement** `control/areas/data.py`:
```python
"""xl2ai's agent tools as data.*: Excel and document folders, prepared and queryable."""
from .. import registry
from ..errors import ControlError
from ..registry import READ, SAFE, Action

SAFE_TOOLS = {"prepare", "save_records"}


def _handler(server, name):
    def handler(args):
        res = server.call_tool(name, args)
        text = "".join(c.get("text", "") for c in res.get("content", []) if c.get("type") == "text")
        data = res.get("structuredContent") or {}
        if res.get("isError"):
            code = str(data.get("error", "E_INTERNAL")).removeprefix("E_")
            raise ControlError("DATA_" + code, data.get("message", text), data.get("hint", ""))
        return {"ok": True, "result": data}, text
    return handler


def register_area(cfg):
    from xl2ai import mcp_server
    server = mcp_server.Server(workspace=cfg.get("data_workspace"))
    acts = [Action("data." + t["name"], t["description"], t["inputSchema"], _handler(server, t["name"]),
                   SAFE if t["name"] in SAFE_TOOLS else READ)
            for t in mcp_server.TOOLS]
    for a in acts:
        registry.register(a)
    registry.set_area("data", True, count=len(acts))
```

- [ ] **Step 4: Run — PASS.** Commit: `feat: xl2ai as the data area`.

---

### Task 9: WinSight → `sys.*`

**Files:**
- Create: `control/areas/sys.py`, `tests/fakes/fake_winsight.py`
- Test: `tests/test_area_sys.py`

**Interfaces:**
- Consumes: `proc.McpChild`.
- Produces: `sys.<tool>` for every WinSight MCP tool. Tiers: tools read; `brief` safe; `clean` read unless `apply` → safe; `undo` risky; `fix` read unless `apply`, then safe only if every matched fix is WinSight-`safe`, else risky (unknown → risky). `fix`/`clean` payloads carry `_undo` steps `{"action": "sys.undo", "args": {"id": <journal_id>}}`. Config key `winsight_cmd` (list) overrides `[winsight_exe, "mcp"]` (tests use it).

- [ ] **Step 1: Fake WinSight** `tests/fakes/fake_winsight.py`:
```python
"""Speaks like `winsight mcp` for tests: two tools, fix/clean/undo/journal."""
import json
import sys

FIXES = [{"id": "junk.temp.clean", "risk": "safe"}, {"id": "junk.old.delete", "risk": "risky"},
         {"id": "tweaks.x.apply", "risk": "moderate"}]
TOOLS = [{"name": "health", "description": "disk health", "inputSchema": {"type": "object", "properties": {}}},
         {"name": "junk", "description": "junk files", "inputSchema": {"type": "object", "properties": {}}},
         {"name": "tweaks", "description": "tweaks", "inputSchema": {"type": "object", "properties": {}}},
         {"name": "brief", "description": "write brief", "inputSchema": {"type": "object", "properties": {}}},
         {"name": "fix", "description": "fix", "inputSchema": {"type": "object", "required": ["ids"], "properties": {
             "ids": {"type": "array", "items": {"type": "string"}}, "apply": {"type": "boolean"},
             "confirm_risky": {"type": "boolean"}}}},
         {"name": "clean", "description": "clean", "inputSchema": {"type": "object", "properties": {"apply": {"type": "boolean"}}}},
         {"name": "undo", "description": "undo", "inputSchema": {"type": "object", "required": ["id"], "properties": {"id": {"type": "string"}}}},
         {"name": "journal", "description": "journal", "inputSchema": {"type": "object", "properties": {}}}]


def reply(mid, result):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": mid, "result": result}) + "\n")
    sys.stdout.flush()


def text(s, data=None, err=False):
    return {"content": [{"type": "text", "text": s}], "structuredContent": data, "isError": err}


for line in sys.stdin:
    msg = json.loads(line)
    if "id" not in msg:
        continue
    m, p = msg["method"], msg.get("params") or {}
    if m == "initialize":
        reply(msg["id"], {"protocolVersion": "2025-06-18", "capabilities": {}})
    elif m == "tools/list":
        reply(msg["id"], {"tools": TOOLS})
    elif m == "tools/call":
        n, a = p["name"], p.get("arguments") or {}
        tool = n
        if n in ("junk", "tweaks"):
            fx = [f for f in FIXES if f["id"].startswith(n + ".")]
            reply(msg["id"], text("found", {"tool": n, "findings": [{"id": n + ".f", "fixes": fx}]}))
        elif n == "fix":
            outs = [{"fix_id": i, "dry_run": not a.get("apply"), "ok": True,
                     "journal_id": "J-" + i if a.get("apply") else "",
                     "confirm_risky": a.get("confirm_risky", False)} for i in a["ids"]]
            reply(msg["id"], text("fixed", {"outcomes": outs}))
        elif n == "clean":
            reply(msg["id"], text("cleaned", {"outcomes": [{"fix_id": "junk.temp.clean", "ok": True,
                                                            "dry_run": not a.get("apply"),
                                                            "journal_id": "J-c" if a.get("apply") else ""}]}))
        elif n == "undo":
            reply(msg["id"], text("undone " + a["id"], {"ok": True}))
        else:
            reply(msg["id"], text(n + " ok", {"tool": n}))
```

- [ ] **Step 2: Failing test** `tests/test_area_sys.py`:
```python
import sys
from pathlib import Path

import pytest

from control import registry, runner
from control.registry import READ, RISKY, SAFE

FAKE = [sys.executable, str(Path(__file__).parent / "fakes" / "fake_winsight.py")]
CFG = {"winsight_cmd": FAKE, "winsight_exe": "unused", "timeouts": {"sys": 20, "sys_fix": 20}}


@pytest.fixture
def area(clean_registry):
    from control.areas import sys as sys_area
    sys_area.register_area(CFG)
    yield
    sys_area.shutdown()


def tier(name, args):
    return registry.ACTIONS[name].tier_for(args)


def test_static_tiers(area):
    assert tier("sys.health", {}) == READ and tier("sys.journal", {}) == READ
    assert tier("sys.brief", {}) == SAFE and tier("sys.undo", {"id": "x"}) == RISKY
    assert tier("sys.clean", {}) == READ and tier("sys.clean", {"apply": True}) == SAFE


def test_fix_tier_follows_winsight_risk(area):
    assert tier("sys.fix", {"ids": ["junk.old.delete"]}) == READ               # preview
    assert tier("sys.fix", {"ids": ["junk.temp.clean"], "apply": True}) == SAFE
    assert tier("sys.fix", {"ids": ["junk.old.delete"], "apply": True}) == RISKY
    assert tier("sys.fix", {"ids": ["tweaks.x.apply"], "apply": True}) == RISKY  # moderate
    assert tier("sys.fix", {"ids": ["junk.*"], "apply": True}) == RISKY         # includes risky
    assert tier("sys.fix", {"ids": ["nothing.here"], "apply": True}) == RISKY   # unknown fails closed
    assert tier("sys.fix", {"ids": ["*"], "apply": True}) == RISKY


def test_fix_apply_journals_undo_and_confirms(area):
    payload, _ = runner.execute("sys.fix", {"ids": ["junk.temp.clean"], "apply": True})
    assert payload["ok"] and payload["result"]["outcomes"][0]["confirm_risky"] is True
    from control import journal
    e = journal.default().get(payload["journal_id"])
    assert e["undo"] == [{"action": "sys.undo", "args": {"id": "J-junk.temp.clean"}}]
    undo_payload, text = runner.undo(payload["journal_id"])
    assert undo_payload["ok"] and "undone J-junk.temp.clean" in text


def test_missing_exe_is_reported(clean_registry, tmp_path):
    from control.areas import sys as sys_area
    from control.errors import ControlError
    with pytest.raises(ControlError) as e:
        sys_area.register_area({"winsight_exe": str(tmp_path / "none.exe"), "timeouts": {"sys": 5, "sys_fix": 5}})
    assert e.value.code == "SYS_MISSING" and "go build" in e.value.hint
```

- [ ] **Step 3: Run — FAIL.**

- [ ] **Step 4: Implement** `control/areas/sys.py`:
```python
"""WinSight (Go) as sys.*: PC health, space, speed, repairs - through one `winsight mcp` child."""
import fnmatch
from pathlib import Path

from .. import registry
from ..errors import ControlError
from ..proc import McpChild
from ..registry import READ, RISKY, SAFE, Action

_child = None
WRITE_TOOLS = {"fix", "clean", "undo"}


def shutdown():
    global _child
    if _child is not None:
        _child.close()
        _child = None


def _known_risks(child, patterns):
    """{fix_id: risk} for every fix the patterns' tools report; None when a tool can't be told."""
    risks = {}
    for tool in {p.split(".", 1)[0] for p in patterns}:
        if any(ch in tool for ch in "*?["):
            return None
        res = child.call_tool(tool, {})
        for finding in (res.get("structuredContent") or {}).get("findings") or []:
            for fx in finding.get("fixes") or []:
                risks[fx["id"]] = fx.get("risk", "risky")
    return risks


def _fix_tier(child):
    def tier(args):
        if not args.get("apply"):
            return READ
        patterns = args.get("ids") or []
        try:
            risks = _known_risks(child, patterns)
        except ControlError:
            return RISKY
        if not risks or not patterns:
            return RISKY
        for p in patterns:
            matched = [r for fid, r in risks.items() if fnmatch.fnmatchcase(fid, p)]
            if not matched or any(r != "safe" for r in matched):
                return RISKY
        return SAFE
    return tier


def _handler(child, name, cfg):
    timeout = cfg["timeouts"]["sys_fix" if name in WRITE_TOOLS else "sys"]

    def handler(args):
        call_args = dict(args)
        if name == "fix" and call_args.get("apply"):
            call_args["confirm_risky"] = True      # Control's own gate already decided
        res = child.call_tool(name, call_args, timeout=timeout)
        text = "".join(c.get("text", "") for c in res.get("content", []) if c.get("type") == "text")
        data = res.get("structuredContent")
        if res.get("isError"):
            raise ControlError("SYS_ERROR", text[:800] or f"winsight {name} failed",
                               "see sys.journal, or run the same WinSight tool on its own")
        payload = {"ok": True, "result": data}
        if name in ("fix", "clean") and isinstance(data, dict):
            outs = data.get("outcomes") or []
            payload["ok"] = not any(o.get("error") for o in outs)
            steps = [{"action": "sys.undo", "args": {"id": o["journal_id"]}}
                     for o in outs if o.get("journal_id") and not o.get("dry_run")]
            if steps:
                payload["_undo"] = steps
        return payload, text
    return handler


def register_area(cfg):
    global _child
    cmd = cfg.get("winsight_cmd")
    if not cmd:
        exe = Path(cfg["winsight_exe"])
        if not exe.exists():
            raise ControlError("SYS_MISSING", f"WinSight not found at {exe}",
                               r"build it: cd I:\Control\Performance; go build -o ..\hub\bin\winsight.exe .\cmd\winsight")
        cmd = [str(exe), "mcp"]
    shutdown()
    _child = McpChild(cmd, name="winsight", timeout=cfg["timeouts"]["sys"])
    tools = _child.request("tools/list").get("tools") or []
    acts = []
    for t in tools:
        n = t["name"]
        if n == "fix":
            tier, why = _fix_tier(_child), "changes Windows settings or deletes files"
        elif n == "clean":
            tier, why = (lambda a: SAFE if a.get("apply") else READ), ""
        elif n == "undo":
            tier, why = RISKY, "reverses an earlier change to Windows"
        elif n == "brief":
            tier, why = SAFE, ""
        else:
            tier, why = READ, ""
        acts.append(Action("sys." + n, t.get("description", ""),
                           t.get("inputSchema") or {"type": "object", "properties": {}},
                           _handler(_child, n, cfg), tier, why=why))
    for a in acts:
        registry.register(a)
    registry.set_area("sys", True, count=len(acts))
```

- [ ] **Step 5: Run — PASS.** Commit: `feat: WinSight as the sys area with risk-aware fix tiers`.

---

### Task 10: G-MES → `gmes.*`

**Files:**
- Create: `control/areas/gmes.py`, `tests/fakes/gmes/gmes_report.py`, `tests/fakes/gmes/gmes_batch.py`
- Test: `tests/test_area_gmes.py`

**Interfaces:**
- Produces: `gmes.find` (read), `gmes.describe` (read), `gmes.run` (safe), `gmes.batch_list` (read), `gmes.batch_plan` (read), `gmes.batch_run` (safe). Exit 3 → `GMES_BUSY`; exit 2 → `GMES_USAGE`; other non-zero on run → payload `ok: False, code: GMES_FAILED`. `gmes.run` returns the parsed `--manifest` JSON under `manifest`.

- [ ] **Step 1: Fakes**

`tests/fakes/gmes/gmes_report.py`:
```python
import argparse
import json
import sys
import time

p = argparse.ArgumentParser()
p.add_argument("command")
p.add_argument("screens", nargs="+")
p.add_argument("--division"); p.add_argument("--from", dest="date_from"); p.add_argument("--to", dest="date_to")
p.add_argument("--date"); p.add_argument("--export"); p.add_argument("--verify"); p.add_argument("--output-dir")
p.add_argument("--set", action="append", default=[]); p.add_argument("--manifest"); p.add_argument("--dry-run", action="store_true")
a = p.parse_args()
if "BUSY" in a.screens:
    sys.exit(3)
if "SLOW" in a.screens:
    time.sleep(30)
if "BAD" in a.screens:
    print("screen BAD not found"); sys.exit(1)
print(f"{a.command} {' '.join(a.screens)} division={a.division} set={a.set} مرحبا")
if a.manifest:
    json.dump({"from": a.date_from, "to": a.date_to, "division": a.division,
               "results": [{"screen": s, "rows": 42} for s in a.screens]}, open(a.manifest, "w"))
```

`tests/fakes/gmes/gmes_batch.py`:
```python
import sys
print("batch " + " ".join(sys.argv[1:]))
```

- [ ] **Step 2: Failing test** `tests/test_area_gmes.py`:
```python
import sys
from pathlib import Path

import pytest

from control import registry, runner
from control.registry import READ, SAFE

FAKE_DIR = str(Path(__file__).parent / "fakes" / "gmes")
CFG = {"gmes_dir": FAKE_DIR, "gmes_python": sys.executable, "timeouts": {"gmes": 3}}


@pytest.fixture
def area(clean_registry):
    from control.areas import gmes
    gmes.register_area(CFG)


def test_tiers(area):
    t = {n: a.tier_for({}) for n, a in registry.ACTIONS.items()}
    assert t == {"gmes.find": READ, "gmes.describe": READ, "gmes.run": SAFE,
                 "gmes.batch_list": READ, "gmes.batch_plan": READ, "gmes.batch_run": SAFE}


def test_run_passes_args_and_reads_manifest(area):
    payload, text = runner.execute("gmes.run", {"screens": ["P3151WM00"], "division": "VD",
                                                "date": "20260924", "set": ["A=1"]})
    assert payload["ok"] and payload["manifest"]["results"][0]["rows"] == 42
    assert "division=VD" in text and "['A=1']" in text and "مرحبا" in text


@pytest.mark.parametrize("screen,code", [("BUSY", "GMES_BUSY"), ("BAD", "GMES_FAILED"), ("SLOW", "TIMEOUT")])
def test_failures(area, screen, code):
    payload, _ = runner.execute("gmes.run", {"screens": [screen]})
    assert payload["ok"] is False and payload["code"] == code


def test_find_and_batch(area):
    assert "find stock" in runner.execute("gmes.find", {"text": "stock"})[1]
    assert "batch plan all --date yesterday" in runner.execute(
        "gmes.batch_plan", {"selection": ["all"], "date": "yesterday"})[1]


def test_missing_folder(clean_registry, tmp_path):
    from control.areas import gmes
    from control.errors import ControlError
    with pytest.raises(ControlError) as e:
        gmes.register_area({"gmes_dir": str(tmp_path), "gmes_python": sys.executable, "timeouts": {"gmes": 3}})
    assert e.value.code == "GMES_MISSING"
```

- [ ] **Step 3: Run — FAIL.**

- [ ] **Step 4: Implement** `control/areas/gmes.py`:
```python
"""The G-MES report bot as gmes.*, run as its own process so its browser lock and state stay its own."""
import json
import tempfile
from pathlib import Path

from .. import proc, registry
from ..errors import ControlError
from ..registry import READ, SAFE, Action

S = {"type": "string"}
DATE = {"type": "string", "description": "YYYYMMDD"}


def _script(cfg, script, argv):
    code, out, err = proc.run([cfg["gmes_python"], "-u", script, *argv], cwd=cfg["gmes_dir"],
                              timeout=cfg["timeouts"]["gmes"])
    tail = "\n".join((out + ("\n" + err if err.strip() else "")).strip().splitlines()[-80:])
    if code == 3:
        raise ControlError("GMES_BUSY", "another G-MES run is active (run lock held)",
                           "wait for it to finish, then retry")
    if code == 2:
        raise ControlError("GMES_USAGE", tail[-800:], "check the arguments with gmes.describe")
    return code, tail


def _result(code, tail, **extra):
    payload = {"ok": code == 0, "exit_code": code, **extra}
    if code != 0:
        payload.update(code="GMES_FAILED", message=tail.splitlines()[-1] if tail else "G-MES failed",
                       hint="read the output above; G-MES saves a screenshot of the failure")
    return payload, tail


def _report(cfg, command):
    def handler(args):
        argv = ["find", args["text"]] if command == "find" else ["describe", *args["screens"]]
        return _result(*_script(cfg, "gmes_report.py", argv))
    return handler


def _run(cfg):
    def handler(args):
        argv = ["run", *args["screens"]]
        for key, flag in (("division", "--division"), ("date_from", "--from"), ("date_to", "--to"),
                          ("date", "--date"), ("export", "--export"), ("verify", "--verify"),
                          ("output_dir", "--output-dir")):
            if args.get(key):
                argv += [flag, str(args[key])]
        for pair in args.get("set") or []:
            argv += ["--set", pair]
        if args.get("dry_run"):
            argv.append("--dry-run")
        manifest = Path(tempfile.mkdtemp(prefix="control-gmes-")) / "manifest.json"
        argv += ["--manifest", str(manifest)]
        code, tail = _script(cfg, "gmes_report.py", argv)
        data = json.loads(manifest.read_text(encoding="utf-8")) if manifest.exists() else None
        return _result(code, tail, manifest=data)
    return handler


def _batch(cfg, sub):
    def handler(args):
        argv = [sub, *(args.get("selection") or [])]
        for key, flag in (("date", "--date"), ("batch", "--batch"), ("export", "--export"),
                          ("output_dir", "--output-dir")):
            if args.get(key):
                argv += [flag, str(args[key])]
        return _result(*_script(cfg, "gmes_batch.py", argv))
    return handler


def register_area(cfg):
    d = Path(cfg["gmes_dir"])
    if not (d / "gmes_report.py").exists():
        raise ControlError("GMES_MISSING", f"G-MES bot not found in {d}", "set gmes_dir in config.toml")
    sel = {"selection": {"type": "array", "items": S, "description": "all | 1,3,5-7 | screen codes | !X to exclude"},
           "date": {"type": "string", "description": "date policy, e.g. yesterday"},
           "batch": {"type": "string", "description": "start from a saved batch"},
           "export": {"type": "string", "enum": ["xlsx", "csv", "both"]}}
    acts = [
        Action("gmes.find", "Search the 809 G-MES screens by code or words.",
               {"type": "object", "required": ["text"], "properties": {"text": S}}, _report(cfg, "find"), READ),
        Action("gmes.describe", "What a G-MES screen needs (filters, dates, grids) before running it.",
               {"type": "object", "required": ["screens"], "properties": {"screens": {"type": "array", "items": S}}},
               _report(cfg, "describe"), READ),
        Action("gmes.run", "Sign in, open the screens, set filters, run, verify the rows match, save Excel.",
               {"type": "object", "required": ["screens"], "properties": {
                   "screens": {"type": "array", "items": S, "description": "screen codes, e.g. P3151WM00"},
                   "division": S, "date_from": DATE, "date_to": DATE, "date": DATE,
                   "export": {"type": "string", "enum": ["xlsx", "csv", "both", "none"]},
                   "verify": {"type": "string", "description": "COLUMN[=VALUE] every row must match"},
                   "set": {"type": "array", "items": S, "description": "NAME=VALUE filters"},
                   "output_dir": S, "dry_run": {"type": "boolean"}}},
               _run(cfg), SAFE),
        Action("gmes.batch_list", "The recorded (taught) screens, numbered.",
               {"type": "object", "properties": {}}, _batch(cfg, "list"), READ),
        Action("gmes.batch_plan", "Show what a batch would run and what it would skip - touches nothing.",
               {"type": "object", "properties": sel}, _batch(cfg, "plan"), READ),
        Action("gmes.batch_run", "Run several recorded screens now; one failing screen does not stop the rest.",
               {"type": "object", "properties": dict(sel, output_dir=S)}, _batch(cfg, "run"), SAFE),
    ]
    for a in acts:
        registry.register(a)
    registry.set_area("gmes", True, count=len(acts))
```
- [ ] **Step 5: Run — PASS.** Commit: `feat: G-MES bot as the gmes area`.

---

### Task 11: CLI and MCP server

**Files:**
- Create: `control/cli.py`, `control/mcp.py`
- Test: `tests/test_cli.py`, `tests/test_mcp.py`

**Interfaces:**
- Consumes: `areas.load_all`, `runner.execute`, `guide.render` (Task 12; import lazily).
- Produces: `cli.main(argv=None) -> int`; `cli.parse_action_args(action, argv) -> tuple[dict, bool]`; `mcp.mcp_name(action_name) -> str`; `mcp.Server(preload=(), execute=runner.execute, out=None)` with `.handle(msg) -> dict|None`, `.tools() -> list`; `mcp.serve(preload=(), stdin=None, stdout=None) -> int`.

- [ ] **Step 1: Failing tests**

`tests/test_cli.py`:
```python
import json

from control import cli, registry
from control.registry import Action, SAFE

SCHEMA = {"type": "object", "required": ["path"], "properties": {
    "path": {"type": "string"}, "count": {"type": "integer"}, "fast": {"type": "boolean"},
    "ids": {"type": "array", "items": {"type": "string"}}}}


def test_parse_action_args(clean_registry):
    a = Action("t.do", "help", SCHEMA, lambda x: ({}, ""), SAFE)
    args, as_json = cli.parse_action_args(a, ["--path", "ملف.xlsx", "--count", "2", "--fast", "--ids", "a", "b", "--json"])
    assert args == {"path": "ملف.xlsx", "count": 2, "fast": True, "ids": ["a", "b"]} and as_json


def test_main_runs_action_and_exit_codes(clean_registry, monkeypatch, capsys):
    monkeypatch.setattr("control.areas.load_all", lambda cfg=None: None)
    registry.register(Action("t.do", "help", SCHEMA, lambda x: ({"got": x["path"]}, "did it"), SAFE))
    assert cli.main(["t.do", "--path", "x"]) == 0
    assert "did it" in capsys.readouterr().out
    assert cli.main(["t.do", "--path", "x", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["got"] == "x"
    assert cli.main(["t.nope"]) == 1
```

`tests/test_mcp.py`:
```python
import io
import json

from control import mcp, registry
from control.registry import Action, READ, RISKY, SAFE

OBJ = {"type": "object", "properties": {"x": {"type": "string"}}}


def setup(clean):
    registry.set_area("control", True, 1); registry.set_area("win", True, 2); registry.set_area("sys", True, 1)
    registry.register(Action("control.areas", "areas", OBJ, lambda a: ({}, "areas"), READ))
    registry.register(Action("win.snapshot", "snap", OBJ, lambda a: ({"ok": True}, "tree"), READ))
    registry.register(Action("win.shell", "shell", OBJ, lambda a: ({}, "ran"), RISKY))
    registry.register(Action("sys.health", "health", OBJ, lambda a: ({"v": "مرحبا"}, "ok مرحبا"), READ))


def rpc(server, method, params=None, mid=1):
    return server.handle({"jsonrpc": "2.0", "id": mid, "method": method, "params": params or {}})


def names(server):
    return {t["name"] for t in rpc(server, "tools/list")["result"]["tools"]}


def test_initialize_and_core_set(clean_registry):
    setup(clean_registry)
    s = mcp.Server(out=io.StringIO())
    init = rpc(s, "initialize", {"protocolVersion": "2025-06-18"})["result"]
    assert init["capabilities"]["tools"]["listChanged"] is True and "control" in init["instructions"]
    n = names(s)
    assert {"control_areas", "win_snapshot", "control_load_area", "control_call"} <= n
    assert "win_shell" not in n and "sys_health" not in n
    assert all("." not in x for x in n)


def test_load_area_notifies_and_annotations(clean_registry):
    setup(clean_registry)
    out = io.StringIO()
    s = mcp.Server(out=out)
    rpc(s, "tools/call", {"name": "control_load_area", "arguments": {"area": "win"}})
    assert "notifications/tools/list_changed" in out.getvalue()
    tools = {t["name"]: t for t in rpc(s, "tools/list")["result"]["tools"]}
    assert tools["win_shell"]["annotations"]["destructiveHint"] is True
    assert tools["win_snapshot"]["annotations"]["readOnlyHint"] is True
    bad = rpc(s, "tools/call", {"name": "control_load_area", "arguments": {"area": "nope"}})["result"]
    assert bad["isError"] is True


def test_call_paths_and_json(clean_registry):
    setup(clean_registry)
    s = mcp.Server(out=io.StringIO(), execute=lambda n, a: registry.get(n).handler(a))
    r = rpc(s, "tools/call", {"name": "control_call", "arguments": {"action": "sys.health", "args": {}}})["result"]
    assert r["content"][0]["text"] == "ok مرحبا" and r["isError"] is False
    r = rpc(s, "tools/call", {"name": "sys_health", "arguments": {"json": True}})["result"]
    assert json.loads(r["content"][0]["text"])["v"] == "مرحبا"


def test_serve_loop_utf8(clean_registry):
    setup(clean_registry)
    inp = io.StringIO(json.dumps({"jsonrpc": "2.0", "id": 7, "method": "ping"}) + "\nnot json\n")
    out = io.StringIO()
    mcp.serve(stdin=inp, stdout=out, load=False)
    lines = [json.loads(l) for l in out.getvalue().splitlines()]
    assert lines[0] == {"jsonrpc": "2.0", "id": 7, "result": {}} and lines[1]["error"]["code"] == -32700
```

- [ ] **Step 2: Run — FAIL.**

- [ ] **Step 3: Implement** `control/cli.py`:
```python
"""`control <area.verb> --arg value ...` - every action, same arguments as over MCP."""
import argparse
import json
import sys

from . import areas, registry, runner

USAGE = """control - one front door for Windows apps, websites, G-MES, PC repair and Excel analysis

  control areas                    what loaded
  control doctor                   health of Control itself
  control guide [--write]          the agent guide (--write refreshes AGENT_GUIDE.md)
  control journal [N]              the last changes
  control undo <id>                reverse a change
  control mcp [--areas win,sys]    serve every action to an AI agent over MCP
  control <area.verb> --help       one action, e.g. control sys.health
"""


def parse_action_args(action, argv):
    p = argparse.ArgumentParser(prog=f"control {action.name}", description=action.help)
    for key, spec in action.schema.get("properties", {}).items():
        flag, kind, help_ = "--" + key.replace("_", "-"), spec.get("type"), spec.get("description", "")
        if kind == "boolean":
            p.add_argument(flag, dest=key, action="store_true", default=None, help=help_)
        elif kind == "array":
            p.add_argument(flag, dest=key, nargs="+", help=help_)
        elif kind == "integer":
            p.add_argument(flag, dest=key, type=int, help=help_)
        elif kind == "number":
            p.add_argument(flag, dest=key, type=float, help=help_)
        elif kind == "object":
            p.add_argument(flag, dest=key, type=json.loads, help=help_ + " (JSON)")
        else:
            p.add_argument(flag, dest=key, help=help_)
    p.add_argument("--json", dest="_json", action="store_true", help="print the full JSON payload")
    ns = vars(p.parse_args(argv))
    as_json = ns.pop("_json")
    return {k: v for k, v in ns.items() if v is not None}, as_json


def _print(payload, text, as_json):
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str) if as_json else text)
    return 0 if payload.get("ok", True) else 1


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(USAGE)
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd == "mcp":
        from . import mcp
        preload = []
        if "--areas" in rest and rest.index("--areas") + 1 < len(rest):
            preload = rest[rest.index("--areas") + 1].split(",")
        return mcp.serve(preload=preload)
    areas.load_all()
    if cmd == "guide":
        from . import guide
        if "--write" in rest:
            print(f"wrote {guide.write()}")
        else:
            print(guide.render())
        return 0
    shortcuts = {"areas": ("control.areas", {}), "doctor": ("control.doctor", {}),
                 "journal": ("control.journal", {"last": int(rest[0])} if rest else {}),
                 "undo": ("control.undo", {"id": rest[0]} if rest else {})}
    if cmd in shortcuts:
        name, args = shortcuts[cmd]
        return _print(*runner.execute(name, args), "--json" in rest)
    try:
        action = registry.get(cmd)
    except Exception as e:
        print(e.text() if hasattr(e, "text") else e)
        return 1
    args, as_json = parse_action_args(action, rest)
    return _print(*runner.execute(cmd, args), as_json)
```

- [ ] **Step 4: Implement** `control/mcp.py`:
```python
"""Control as one MCP server (stdio, JSON-RPC 2.0). Tools come from the registry."""
import base64
import copy
import json
import sys
import traceback

from . import __version__, areas, registry, runner
from .registry import READ, RISKY

PROTOCOLS = ["2025-06-18", "2025-03-26", "2024-11-05"]
CORE_EXTRAS = ["win.windows", "win.snapshot", "web.snapshot", "sys.doctor", "data.start", "gmes.find"]
TEXT_CAP = 30000
INSTRUCTIONS = """control is one front door to this Windows PC: desktop apps (win_*), websites (web_*),
Samsung G-MES reports (gmes_*), PC health and repair (sys_*), Excel/document analysis (data_*).
Start: control_areas. Find the right action: control_find_action. Load a whole area's tools:
control_load_area. Any action by name: control_call {action, args}.
Loop: look (read tools) -> act -> check the result -> look again. Never repeat an action whose
effect you did not check. Actions are read / safe / risky; risky ones show the person a yes/no
box - if they say no, do not retry, ask them. Every change is in control_journal and many can be
reversed with control_undo. When unsure about one action, call control_describe with its name."""

META = [
    {"name": "control_load_area", "description": "Add every tool of one area (win, web, sys, data, gmes) to your tool list.",
     "inputSchema": {"type": "object", "required": ["area"], "properties": {"area": {"type": "string"}}},
     "annotations": {"readOnlyHint": True}},
    {"name": "control_call", "description": "Run any action by its name (e.g. sys.junk) with its arguments.",
     "inputSchema": {"type": "object", "required": ["action"], "properties": {
         "action": {"type": "string"}, "args": {"type": "object"}, "json": {"type": "boolean"}}}},
]


def mcp_name(action_name):
    return action_name.replace(".", "_")


class Server:
    def __init__(self, preload=(), execute=None, out=None):
        self.loaded = set(preload)
        self.execute = execute or runner.execute
        self.out = out if out is not None else sys.stdout

    def _visible(self):
        return [a for n, a in registry.ACTIONS.items()
                if a.area == "control" or n in CORE_EXTRAS or a.area in self.loaded]

    @staticmethod
    def _tool(a):
        schema = copy.deepcopy(a.schema) or {"type": "object", "properties": {}}
        schema.setdefault("properties", {})["json"] = {"type": "boolean", "description": "return the full JSON payload"}
        static = a.tier if isinstance(a.tier, str) else None
        return {"name": mcp_name(a.name), "description": f"[{static or 'tier depends on arguments'}] {a.help}",
                "inputSchema": schema,
                "annotations": {"readOnlyHint": static == READ, "destructiveHint": static == RISKY,
                                "openWorldHint": a.area in ("web", "gmes")}}

    def tools(self):
        return [self._tool(a) for a in self._visible()] + META

    def _write(self, msg):
        self.out.write(json.dumps(msg, ensure_ascii=False, default=str) + "\n")
        self.out.flush()

    @staticmethod
    def _text(body, is_error):
        if len(body) > TEXT_CAP:
            body = body[:TEXT_CAP] + f"\n... cut at {TEXT_CAP} characters; ask for less or use json paging"
        return {"content": [{"type": "text", "text": body}], "isError": is_error}

    def call(self, tool, args):
        args = dict(args or {})
        if tool == "control_load_area":
            area = args.get("area")
            info = registry.AREAS.get(area)
            if not info or not info["ok"]:
                why = info["reason"] if info else "no such area"
                return self._text(f"ERROR AREA_UNAVAILABLE: {area}: {why}", True)
            self.loaded.add(area)
            self._write({"jsonrpc": "2.0", "method": "notifications/tools/list_changed"})
            names = sorted(mcp_name(n) for n, a in registry.ACTIONS.items() if a.area == area)
            return self._text(f"loaded {area}: " + ", ".join(names), False)
        if tool == "control_call":
            name, want_json, args = args.get("action", ""), bool(args.get("json")), dict(args.get("args") or {})
        else:
            by_mcp = {mcp_name(n): n for n in registry.ACTIONS}
            name = by_mcp.get(tool, tool)
            want_json = bool(args.pop("json", False))
        payload, text = self.execute(name, args)
        body = json.dumps(payload, ensure_ascii=False, default=str) if want_json else text
        result = self._text(body, not payload.get("ok", True))
        action = registry.ACTIONS.get(name)
        if action and action.image and payload.get("ok", True) and payload.get("path"):
            try:
                with open(payload["path"], "rb") as fh:
                    result["content"].append({"type": "image", "mimeType": "image/png",
                                              "data": base64.b64encode(fh.read()).decode("ascii")})
            except OSError:
                pass
        return result

    def handle(self, msg):
        method, mid = msg.get("method"), msg.get("id")
        if mid is None:
            return None
        try:
            if method == "initialize":
                asked = (msg.get("params") or {}).get("protocolVersion")
                result = {"protocolVersion": asked if asked in PROTOCOLS else PROTOCOLS[0],
                          "capabilities": {"tools": {"listChanged": True}},
                          "serverInfo": {"name": "control", "version": __version__},
                          "instructions": INSTRUCTIONS}
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": self.tools()}
            elif method == "tools/call":
                p = msg.get("params") or {}
                result = self.call(p.get("name"), p.get("arguments"))
            elif method in ("resources/list", "prompts/list"):
                result = {method.split("/")[0]: []}
            else:
                return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"method not found: {method}"}}
        except Exception as e:
            traceback.print_exc(file=sys.stderr)
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": str(e)}}
        return {"jsonrpc": "2.0", "id": mid, "result": result}


def serve(preload=(), stdin=None, stdout=None, load=True):
    stdin = stdin or sys.stdin
    out = stdout or sys.stdout
    real_stdout, sys.stdout = sys.stdout, sys.stderr      # stray prints must not corrupt the protocol
    try:
        if load:
            areas.load_all()
        server = Server(preload=preload, out=out)
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                server._write({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}})
                continue
            resp = ([r for r in (server.handle(m) for m in msg) if r] if isinstance(msg, list) else server.handle(msg))
            if resp:
                server._write(resp)
    finally:
        sys.stdout = real_stdout
    return 0
```
- [ ] **Step 5: Run** `.venv\Scripts\python -m pytest tests/test_cli.py tests/test_mcp.py -v` — PASS. Commit: `feat: CLI and MCP server generated from the registry`.

---

### Task 12: The one guide, playbook, doctor check, README

**Files:**
- Create: `control/guide.py`, `docs/PLAYBOOK.md`, `README.md`, generated `AGENT_GUIDE.md`
- Test: `tests/test_guide.py`

**Interfaces:**
- Produces: `guide.render() -> str`; `guide.write() -> Path` (writes `HUB/AGENT_GUIDE.md`).

- [ ] **Step 1: Write `docs/PLAYBOOK.md`** (hand-written part, English, for agents):
```markdown
# Control — agent guide

Control is one front door to this Windows PC. Connect once (`control mcp`) and use:

| Area | For | Examples |
|---|---|---|
| `win` | any desktop app, through its accessibility tree | `win.windows`, `win.snapshot`, `win.click`, `win.type`, `win.excel-read` |
| `web` | any website, inside Control's own browser profile | `web.launch`, `web.snapshot`, `web.click`, `web.type`, `web.text` |
| `gmes` | Samsung G-MES reports: sign in, filter, verify, save Excel | `gmes.find`, `gmes.describe`, `gmes.run`, `gmes.batch_plan` |
| `sys` | PC health, space, speed, broken or missing parts, and their fixes | `sys.doctor`, `sys.junk`, `sys.health`, `sys.fix` |
| `data` | folders of Excel, Word, PDF, e-mail made queryable | `data.start`, `data.find`, `data.query`, `data.trace` |

## The loop
1. Look first (read actions). 2. Act once. 3. Check what changed (every wad action reports
`changed:` lines; `web.*` reads values back; `sys.fix` reports freed bytes and journal ids).
4. Look again before the next step. Never repeat an action whose effect you did not check.

## Tiers
- **read** — changes nothing; use freely.
- **safe** — small or undoable change; runs and is written to the journal.
- **risky** — a yes/no box appears on the person's screen. `APPROVAL_DENIED`: do not retry, ask
  them. `APPROVAL_TIMEOUT`: ask in chat, then retry once. You cannot answer that box yourself.
- `sys.fix` with `apply` is safe only when every matched fix is WinSight-safe; otherwise risky.
  Always preview first (`apply` false).

## Journal and undo
Every safe/risky call returns a `journal_id`. `control.journal` lists them; `control.undo <id>`
reverses the ones marked `[undoable]`.

## Recipes across areas
- **Report → analysis:** `gmes.run` (note `output_dir` or the manifest's files) → `data.prepare`
  with `workspace` = that folder → `data.start` → `data.query`.
- **Slow PC:** `sys.doctor` → read the top findings → `sys.fix` preview → apply the safe ones →
  `control.journal`.
- **App with no accessibility tree:** `win.screenshot` → `win.ocr` → `win.click-text`.

## Traps learned the hard way
- Corporate proxies swallow local traffic; Control sets `NO_PROXY` for its children — do not unset it.
- Windows 11 Notepad restores old tabs: create and verify a blank tab before typing.
- Excel's ValuePattern lies: write cells with `win.excel-write` (reads back), not `win.type`.
- An app running as administrator is invisible to a non-elevated Control.
- G-MES allows one run at a time: `GMES_BUSY` means wait, not retry in a loop.
- Arabic keyboard layout breaks ribbon key tips: `win.input-lang en` for that window.

## Details per tool
The four tools keep their own docs: `I:\Control\win-agent-desktop\docs\COMMANDS.md`,
`I:\Control\Office-Automation\AI_USAGE.md`, `I:\Control\opening-nerp-tcode\GMES_SKILL.md`,
`I:\Control\Performance\AGENTS.md`.
```

- [ ] **Step 2: Failing test** `tests/test_guide.py`:
```python
import os

import pytest

from control import areas, config, guide, registry
from control.registry import Action, READ


def test_render_lists_actions_and_unavailable_areas(clean_registry):
    registry.set_area("t", True, 1)
    registry.set_area("sys", False, reason="SYS_MISSING: not built", hint="go build")
    registry.register(Action("t.look", "look | at things\nmore", {"type": "object", "properties": {}},
                             lambda a: ({}, ""), READ))
    text = guide.render()
    assert text.startswith("# Control — agent guide")
    assert "| `t.look` | read | look \\| at things |" in text
    assert "Unavailable on this PC: SYS_MISSING: not built" in text


@pytest.mark.skipif(os.environ.get("CONTROL_LIVE") != "1", reason="needs every area installed")
def test_committed_guide_is_fresh():
    areas.load_all()
    assert all(v["ok"] for v in registry.AREAS.values()), registry.AREAS
    assert (config.HUB / "AGENT_GUIDE.md").read_text(encoding="utf-8") == guide.render()
```

- [ ] **Step 3: Run — FAIL.**

- [ ] **Step 4: Implement** `control/guide.py`:
```python
"""AGENT_GUIDE.md = the hand-written playbook + a generated reference of every action."""
from . import config, registry


def _tier(a):
    return a.tier if isinstance(a.tier, str) else "depends"


def render():
    playbook = (config.HUB / "docs" / "PLAYBOOK.md").read_text(encoding="utf-8").rstrip()
    lines = [playbook, "", "## Action reference (generated by `control guide --write` — do not edit)", "",
             "MCP tool name = action name with `.` replaced by `_`.", ""]
    for area in sorted(registry.AREAS):
        info = registry.AREAS[area]
        lines += [f"### {area}", ""]
        if not info["ok"]:
            lines += [f"_Unavailable on this PC: {info['reason']}_", ""]
            continue
        lines += ["| Action | Tier | What it does |", "|---|---|---|"]
        for name in sorted(n for n, a in registry.ACTIONS.items() if a.area == area):
            a = registry.ACTIONS[name]
            first = a.help.strip().split("\n")[0].replace("|", "\\|")
            lines.append(f"| `{name}` | {_tier(a)} | {first} |")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write():
    path = config.HUB / "AGENT_GUIDE.md"
    path.write_text(render(), encoding="utf-8")
    return path
```

- [ ] **Step 5: Write `README.md`** — short: what Control is (3 lines), `setup.ps1`, `control doctor`, `claude mcp add control -- I:\Control\hub\.venv\Scripts\control.exe mcp`, the tiers table, `CONTROL_APPROVAL=off` warning, link to `AGENT_GUIDE.md` and the spec. Include an Arabic summary paragraph at top (same content as the spec's ملخص).

- [ ] **Step 6: Run full suite** `.venv\Scripts\python -m pytest -v` — all PASS. Generate the guide: `.venv\Scripts\control guide --write`, then `set CONTROL_LIVE=1` and run `tests/test_guide.py` — PASS. Commit: `feat: one agent guide, playbook and README`.

---

### Task 13: Live check on the owner's PC and registration

**Files:**
- Create: `tools/smoke.py`

- [ ] **Step 1: Write `tools/smoke.py`**:
```python
"""Live check on the real PC. Run from the signed-in desktop: .venv\\Scripts\\python tools\\smoke.py"""
import sys
import tempfile

from control import areas, registry, runner

areas.load_all()
bad = {k: v for k, v in registry.AREAS.items() if not v["ok"]}
print("areas:", {k: v["count"] if v["ok"] else "OFF" for k, v in registry.AREAS.items()})
checks = [("win.windows", {}), ("win.desktop-check", {}), ("sys.health", {}),
          ("data.start", {"workspace": tempfile.mkdtemp()}), ("gmes.batch_list", {})]
failed = []
for name, args in checks:
    payload, text = runner.execute(name, args)
    ok = payload.get("ok", True) or payload.get("code", "").startswith("DATA_")
    print(f"{'PASS' if ok else 'FAIL'}  {name}: {text.splitlines()[0] if text else ''}")
    if not ok:
        failed.append(name)
print("\nNow a risky action: a yes/no box must appear. Click NO.")
payload, _ = runner.execute("win.process-kill", {"name": "control-smoke-does-not-exist.exe"})
print("PASS" if payload.get("code") == "APPROVAL_DENIED" else f"FAIL  risky gate: {payload}")
sys.exit(1 if failed or bad or payload.get("code") != "APPROVAL_DENIED" else 0)
```
(Before running, confirm `win.process-kill`'s argument name with `.venv\Scripts\control control.describe --action win.process-kill` and adjust the dict key to match.)

- [ ] **Step 2: Run it with the owner watching** — expected: all PASS, the box appears, owner clicks No → `APPROVAL_DENIED`.

- [ ] **Step 3: Safe fix + undo** — `control sys.junk` → pick one `safe` fix id → `control sys.fix --ids <id> --apply` → `control journal` shows it `[undoable]` → `control undo <journal-id>` (box appears; owner clicks Yes) → journal shows `(undone)`.

- [ ] **Step 4: Register with Claude Code** (owner confirms first):
```bash
claude mcp add control -- I:\Control\hub\.venv\Scripts\control.exe mcp
```
In a new Claude Code session: `control_areas` works; `control_load_area {"area":"sys"}` adds sys tools.

- [ ] **Step 5: Confirm the four original repos are unchanged**: in each, `git status --short` is empty (except build caches ignored by their own `.gitignore`), and run each repo's own test command once.

- [ ] **Step 6: Commit** `test: live smoke check`.

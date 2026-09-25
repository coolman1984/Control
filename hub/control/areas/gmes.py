"""The G-MES report bot as gmes.*, run as its own process so its browser lock and state stay its own."""
import json
import shutil
import tempfile
from pathlib import Path

from .. import proc, registry
from ..errors import ControlError
from ..registry import READ, RISKY, SAFE, Action

S = {"type": "string"}
DATE = {"type": "string", "description": "YYYYMMDD"}
PINS_SETTINGS = "changes the saved export settings G-MES reuses for this screen on every later run"


def _run_tier(args):
    return RISKY if args.get("export") or args.get("output_dir") else SAFE


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
        manifest_dir = Path(tempfile.mkdtemp(prefix="control-gmes-"))
        manifest = manifest_dir / "manifest.json"
        argv += ["--manifest", str(manifest)]
        try:
            code, tail = _script(cfg, "gmes_report.py", argv)
            data = json.loads(manifest.read_text(encoding="utf-8")) if manifest.exists() else None
            return _result(code, tail, manifest=data)
        finally:
            shutil.rmtree(manifest_dir, ignore_errors=True)
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
        Action("gmes.run", "Sign in, open the screens, set filters, run, verify the rows match, save Excel.\n"
               f"export or output_dir: {PINS_SETTINGS}.",
               {"type": "object", "required": ["screens"], "properties": {
                   "screens": {"type": "array", "items": S, "description": "screen codes, e.g. P3151WM00"},
                   "division": S, "date_from": DATE, "date_to": DATE, "date": DATE,
                   "export": {"type": "string", "enum": ["xlsx", "csv", "both", "none"]},
                   "verify": {"type": "string", "description": "COLUMN[=VALUE] every row must match"},
                   "set": {"type": "array", "items": S, "description": "NAME=VALUE filters"},
                   "output_dir": S, "dry_run": {"type": "boolean"}}},
               _run(cfg), _run_tier, why=PINS_SETTINGS),
        Action("gmes.batch_list", "The recorded (taught) screens, numbered.",
               {"type": "object", "properties": {}}, _batch(cfg, "list"), READ),
        Action("gmes.batch_plan", "Show what a batch would run and what it would skip - touches nothing.",
               {"type": "object", "properties": sel}, _batch(cfg, "plan"), READ),
        Action("gmes.batch_run", "Run several recorded screens now; one failing screen does not stop the rest.\n"
               f"export or output_dir: {PINS_SETTINGS}.",
               {"type": "object", "properties": dict(sel, output_dir=S)}, _batch(cfg, "run"), _run_tier,
               why=PINS_SETTINGS),
    ]
    for a in acts:
        registry.register(a)
    registry.set_area("gmes", True, count=len(acts))

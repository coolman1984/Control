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
  control agent [--interval N]     run forever, checking every flow's triggers (piece 3)
  control vault set <name>         store a secret for {{ secret:name }} (typed here, never over MCP)
  control vault list               names of the stored secrets (never their values)
  control vault delete <name>      remove a stored secret
  control dashboard [--port N]     serve a read-only local view of runs/triggers/journal
  control tools verify             check the tool surface against the accepted baseline
  control tools accept             accept the current tool surface as the new baseline
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
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(USAGE)
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd == "vault":
        from . import vault as vault_cli
        return vault_cli.main(rest)
    if cmd == "mcp":
        from . import mcp
        preload = []
        if "--areas" in rest and rest.index("--areas") + 1 < len(rest):
            preload = rest[rest.index("--areas") + 1].split(",")
        return mcp.serve(preload=preload)
    areas.load_all()
    if cmd == "agent":
        from . import agent
        interval = None
        if "--interval" in rest and rest.index("--interval") + 1 < len(rest):
            interval = float(rest[rest.index("--interval") + 1])
        return agent.run_forever(interval_s=interval)
    if cmd == "dashboard":
        from . import dashboard
        port = 8765
        if "--port" in rest and rest.index("--port") + 1 < len(rest):
            port = int(rest[rest.index("--port") + 1])
        token = None
        if "--token" in rest and rest.index("--token") + 1 < len(rest):
            token = rest[rest.index("--token") + 1]
        return dashboard.run_forever(port=port, token=token)
    if cmd == "tools":
        from . import tools_lock
        return tools_lock.main(rest)
    if cmd == "guide":
        from . import guide
        if "--write" in rest:
            print(f"wrote {guide.write()}")
        else:
            print(guide.render())
        return 0
    shortcuts = {
        "areas": lambda: ("control.areas", {}),
        "doctor": lambda: ("control.doctor", {}),
        "journal": lambda: ("control.journal", {"last": int(rest[0])} if rest else {}),
        "undo": lambda: ("control.undo", {"id": rest[0]} if rest else {})
    }
    if cmd in shortcuts:
        name, args = shortcuts[cmd]()
        return _print(*runner.execute(name, args), "--json" in rest)
    try:
        action = registry.get(cmd)
    except Exception as e:
        print(e.text() if hasattr(e, "text") else e)
        return 1
    args, as_json = parse_action_args(action, rest)
    return _print(*runner.execute(cmd, args), as_json)

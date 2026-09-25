"""WinSight (Go) as sys.*: PC health, space, speed, repairs - through one `winsight mcp` child."""
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


def _fix_tier(child):
    """SAFE only when WinSight's own preview resolves every pattern to fixes it reports as safe
    and not needing admin; RISKY (fail closed) on any error, unknown pattern, or exception."""
    def tier(args):
        if not args.get("apply"):
            return READ
        patterns = args.get("ids") or []
        if not patterns:
            return RISKY
        try:
            preview = child.call_tool("fix", {"ids": patterns, "apply": False})
            outcomes = (preview.get("structuredContent") or {}).get("outcomes") or []
            if not outcomes or any(o.get("error") for o in outcomes):
                return RISKY
            resolved_ids = {o["fix_id"] for o in outcomes}
            risks = {}
            for tool in {fid.split(".", 1)[0] for fid in resolved_ids}:
                res = child.call_tool(tool, {})
                for finding in (res.get("structuredContent") or {}).get("findings") or []:
                    for fx in finding.get("fixes") or []:
                        risks[fx["id"].lower()] = (fx.get("risk", "risky"), fx.get("needs_admin", False))
            for fid in resolved_ids:
                risk, needs_admin = risks.get(fid.lower(), ("risky", True))
                if risk != "safe" or needs_admin:
                    return RISKY
            return SAFE
        except Exception:
            return RISKY
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
    startup_timeout = cfg["timeouts"].get("startup", 15)
    try:
        tools = _child.request("tools/list", timeout=startup_timeout).get("tools") or []
    except ControlError as e:
        if e.code == "TIMEOUT":
            raise ControlError("TIMEOUT", e.message,
                               f"WinSight did not start within {startup_timeout} s; "
                               r"run bin\winsight.exe doctor") from None
        raise
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

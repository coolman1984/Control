"""Control as one MCP server (stdio, JSON-RPC 2.0). Tools come from the registry."""
import base64
import copy
import json
import os
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
                if a.area in ("control", "flow", "run", "agent") or n in CORE_EXTRAS or a.area in self.loaded]

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
        if want_json and len(body) > TEXT_CAP:
            body = json.dumps({"ok": False, "code": "TOO_LARGE",
                               "message": f"the JSON result is {len(body)} characters, over the {TEXT_CAP} cap",
                               "hint": "ask for less (limits, a smaller range) or use text output"},
                              ensure_ascii=False)
            result = self._text(body, True)
        else:
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
    if os.environ.get("CONTROL_APPROVAL") == "off":
        print("WARNING: CONTROL_APPROVAL=off — risky actions will run without asking anyone; "
              "only use this for the owner's own scripts, never for an AI agent session",
              file=sys.stderr)
    if stdin is None:
        stdin = sys.stdin
        try:
            stdin.reconfigure(encoding="utf-8")           # Windows pipes default to the console's
        except Exception:                                 # ANSI codepage, which scrambles non-ASCII
            pass
    out = stdout
    if out is None:
        out = sys.stdout
        try:
            out.reconfigure(encoding="utf-8")
        except Exception:
            pass
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

"""Speaks like `winsight mcp` for tests: two tools, fix/clean/undo/journal."""
import fnmatch
import json
import sys
import time

SLOW_START = "--slow-start" in sys.argv[1:]

FIXES = [{"id": "junk.temp.clean", "risk": "safe", "needs_admin": False, "reversible": True},
         {"id": "junk.old.delete", "risk": "risky", "needs_admin": False, "reversible": False},
         {"id": "junk.admin.clean", "risk": "safe", "needs_admin": True, "reversible": False},
         {"id": "junk.cache.clean", "risk": "safe", "needs_admin": False, "reversible": False},
         {"id": "tweaks.x.apply", "risk": "moderate", "needs_admin": False, "reversible": True}]
FIX_IDS = [f["id"] for f in FIXES]
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
        if SLOW_START:
            time.sleep(3)
        reply(msg["id"], {"tools": TOOLS})
    elif m == "tools/call":
        n, a = p["name"], p.get("arguments") or {}
        tool = n
        if n in ("junk", "tweaks"):
            fx = [f for f in FIXES if f["id"].startswith(n + ".")]
            reply(msg["id"], text("found", {"tool": n, "findings": [{"id": n + ".f", "fixes": fx}]}))
        elif n == "fix":
            apply = a.get("apply")
            outs = []
            for pattern in a["ids"]:
                matched = [i for i in FIX_IDS if fnmatch.fnmatchcase(i.lower(), pattern.lower())]
                if not matched:
                    outs.append({"fix_id": pattern, "ok": False, "dry_run": not apply,
                                "error": f"no fix matches {pattern!r}"})
                    continue
                for fid in matched:
                    reversible = next(f["reversible"] for f in FIXES if f["id"] == fid)
                    outs.append({"fix_id": fid, "dry_run": not apply, "ok": True,
                                "journal_id": "J-" + fid if apply else "",
                                "reversible": reversible,
                                "confirm_risky": a.get("confirm_risky", False)})
            reply(msg["id"], text("fixed", {"outcomes": outs}))
        elif n == "clean":
            reply(msg["id"], text("cleaned", {"outcomes": [{"fix_id": "junk.temp.clean", "ok": True,
                                                            "dry_run": not a.get("apply"),
                                                            "journal_id": "J-c" if a.get("apply") else ""}]}))
        elif n == "undo":
            reply(msg["id"], text("undone " + a["id"], {"ok": True}))
        else:
            reply(msg["id"], text(n + " ok", {"tool": n}))

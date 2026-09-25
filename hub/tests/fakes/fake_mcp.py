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

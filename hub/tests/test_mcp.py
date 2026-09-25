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


def test_call_over_json_cap_returns_structured_error(clean_registry):
    setup(clean_registry)
    big = {"ok": True, "blob": "x" * 40000}
    s = mcp.Server(out=io.StringIO(), execute=lambda n, a: (big, "text"))
    r = rpc(s, "tools/call", {"name": "sys_health", "arguments": {"json": True}})["result"]
    assert r["isError"] is True
    body = json.loads(r["content"][0]["text"])
    assert body == {"ok": False, "code": "TOO_LARGE",
                    "message": body["message"], "hint": body["hint"]}
    assert "TOO_LARGE" in body["code"]


def test_approval_off_warns_on_stderr(clean_registry, capsys):
    setup(clean_registry)
    import os
    os.environ["CONTROL_APPROVAL"] = "off"
    try:
        inp = io.StringIO("")
        mcp.serve(stdin=inp, stdout=io.StringIO(), load=False)
    finally:
        del os.environ["CONTROL_APPROVAL"]
    captured = capsys.readouterr()
    assert "CONTROL_APPROVAL=off" in captured.err


def test_serve_loop_utf8(clean_registry):
    setup(clean_registry)
    inp = io.StringIO(json.dumps({"jsonrpc": "2.0", "id": 7, "method": "ping"}) + "\nnot json\n")
    out = io.StringIO()
    mcp.serve(stdin=inp, stdout=out, load=False)
    lines = [json.loads(l) for l in out.getvalue().splitlines()]
    assert lines[0] == {"jsonrpc": "2.0", "id": 7, "result": {}} and lines[1]["error"]["code"] == -32700

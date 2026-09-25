"""Helper child process for tests/test_mcp_process.py: registers t.echo, serves one request off
real stdin/stdout, then writes whatever args it received straight to a file (bypassing the pipe)
so the test can check the decoded value without a round trip through the same broken codec."""
import json
import sys
from pathlib import Path

from control import mcp, registry
from control.registry import READ, Action

captured = []


def _echo(args):
    captured.append(args)
    return {"ok": True}, "echoed"


registry.register(Action("t.echo", "echo back", {"type": "object", "properties": {"text": {"type": "string"}}},
                         _echo, READ))
registry.set_area("t", True, 1)

mcp.serve(load=False)

Path(sys.argv[1]).write_text(json.dumps(captured, ensure_ascii=False), encoding="utf-8")

"""serve() must reconfigure the real stdin/stdout to UTF-8 itself, since Windows pipes decode as
the console's ANSI codepage (this machine: cp1256) and scramble non-ASCII arguments such as
Arabic. A pure pass-through echo (decode wrong, then encode wrong again) happens to round-trip
byte-identical for this codepage, so that alone would not prove anything; instead this spawns a
child that decodes a real argument ONCE off stdin and writes what it saw straight to a file
(bypassing the pipe on the way out), which does show the corruption when the fix is absent."""
import json
import os
import subprocess
import sys
from pathlib import Path

HUB = Path(__file__).resolve().parent.parent
CHILD = Path(__file__).resolve().parent / "fakes" / "mcp_echo_child.py"


def test_serve_reconfigures_stdin_so_arabic_args_survive(tmp_path):
    env = dict(os.environ)
    env.pop("PYTHONIOENCODING", None)   # prove serve() itself fixes the encoding, not the env
    env.pop("PYTHONUTF8", None)
    out_path = tmp_path / "captured.json"
    p = subprocess.Popen([sys.executable, str(CHILD), str(out_path)], stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=str(HUB), env=env)
    try:
        req = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                          "params": {"name": "t_echo", "arguments": {"text": "مرحبا يا صديقي"}}},
                         ensure_ascii=False) + "\n"
        p.stdin.write(req.encode("utf-8"))
        p.stdin.flush()
        p.stdin.close()
        p.wait(timeout=10)
    except subprocess.TimeoutExpired:
        p.kill()
        p.wait()
        raise
    captured = json.loads(out_path.read_text(encoding="utf-8"))
    assert captured == [{"text": "مرحبا يا صديقي"}], p.stderr.read().decode("utf-8", errors="replace")

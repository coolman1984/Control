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

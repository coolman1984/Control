import io

import pytest

from control import mcp, registry, tools_lock
from control.registry import READ, Action

OBJ = {"type": "object", "properties": {}}


@pytest.fixture(autouse=True)
def one_action(clean_registry):
    registry.register(Action("t.a", "does a thing", OBJ, lambda a: ({}, ""), READ))


def test_no_lock_means_check_passes(control_home):
    assert tools_lock.read() is None
    assert tools_lock.check() is None


def test_write_then_check_matches(control_home):
    h = tools_lock.write()
    assert tools_lock.read() == h
    assert tools_lock.check() is None


def test_check_detects_a_changed_description(control_home):
    tools_lock.write()
    registry.ACTIONS["t.a"].help = "does something else now"
    drifted = tools_lock.check()
    assert drifted is not None and drifted != tools_lock.read()


def test_check_detects_a_new_action(control_home):
    tools_lock.write()
    registry.register(Action("t.b", "new one", OBJ, lambda a: ({}, ""), READ))
    assert tools_lock.check() is not None


def test_surface_hash_stable_and_order_independent(control_home):
    h1 = registry.surface_hash()
    registry.register(Action("t.b", "another", OBJ, lambda a: ({}, ""), READ))
    h2 = registry.surface_hash()
    assert h1 != h2
    assert registry.surface_hash() == h2                  # stable across repeated calls


def test_cli_main(control_home, capsys):
    assert tools_lock.main([]) == 1
    assert tools_lock.main(["verify"]) == 0                # no lock yet -> passes
    assert tools_lock.main(["accept"]) == 0
    assert "accepted" in capsys.readouterr().out
    assert tools_lock.main(["verify"]) == 0

    registry.ACTIONS["t.a"].help = "changed"
    assert tools_lock.main(["verify"]) == 1
    out = capsys.readouterr().out
    assert "WARNING" in out


def test_mcp_serve_warns_on_drift_but_still_starts(control_home, monkeypatch):
    tools_lock.write()
    registry.ACTIONS["t.a"].help = "changed after the lock was written"
    monkeypatch.setattr("control.areas.load_all", lambda: None)
    err = io.StringIO()
    monkeypatch.setattr("sys.stderr", err)
    code = mcp.serve(stdin=io.StringIO(""), stdout=io.StringIO())
    assert code == 0
    assert "tool surface changed" in err.getvalue()

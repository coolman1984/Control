import pytest

from control import registry
from control.registry import READ, RISKY, SAFE

pytest.importorskip("wadlib")


def test_every_wad_command_mapped_with_expected_tiers(clean_registry):
    from control.areas import win
    win.register_area({})
    names = registry.ACTIONS
    assert registry.AREAS["win"]["ok"] and registry.AREAS["web"]["ok"]
    expect = {"win.snapshot": READ, "win.windows": READ, "win.click": SAFE, "win.type": SAFE,
              "win.shell": RISKY, "win.file-write": RISKY, "win.process-kill": RISKY,
              "win.file-read": READ, "win.batch": RISKY, "win.excel-run": RISKY,
              "win.outlook-send": RISKY, "web.snapshot": READ, "web.click": SAFE,
              "web.eval": RISKY, "web.launch": SAFE,
              "win.excel-write": SAFE, "win.word-write": SAFE, "win.outlook-draft": SAFE,
              "win.ppt-add-slide": SAFE, "win.ppt-replace": SAFE, "win.ppt-save": RISKY}
    for name, tier in expect.items():
        assert names[name].tier_for({}) == tier, name
    assert "win.mcp" not in names and "win.record" not in names
    assert not any(n.startswith("win.browser-") for n in names)
    assert names["win.screenshot"].image is True


def test_launch_shell_tool_is_risky(clean_registry):
    from control.areas import win
    win.register_area({})
    a = registry.ACTIONS["win.launch"]
    assert a.tier_for({"app": "cmd"}) == RISKY
    assert a.tier_for({"app": r"C:\Windows\System32\cmd.exe"}) == RISKY
    assert a.tier_for({"app": "PowerShell.exe"}) == RISKY
    assert a.tier_for({"app": "regedit"}) == RISKY
    assert a.tier_for({"app": "notepad.exe"}) == SAFE
    assert a.tier_for({}) == SAFE

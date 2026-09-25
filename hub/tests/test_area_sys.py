import sys
from pathlib import Path

import pytest

from control import registry, runner
from control.registry import READ, RISKY, SAFE

FAKE = [sys.executable, str(Path(__file__).parent / "fakes" / "fake_winsight.py")]
CFG = {"winsight_cmd": FAKE, "winsight_exe": "unused", "timeouts": {"sys": 20, "sys_fix": 20}}


@pytest.fixture
def area(clean_registry):
    from control.areas import sys as sys_area
    sys_area.register_area(CFG)
    yield
    sys_area.shutdown()


def tier(name, args):
    return registry.ACTIONS[name].tier_for(args)


def test_static_tiers(area):
    assert tier("sys.health", {}) == READ and tier("sys.journal", {}) == READ
    assert tier("sys.brief", {}) == SAFE and tier("sys.undo", {"id": "x"}) == RISKY
    assert tier("sys.clean", {}) == READ and tier("sys.clean", {"apply": True}) == SAFE


def test_fix_tier_follows_winsight_risk(area):
    assert tier("sys.fix", {"ids": ["junk.old.delete"]}) == READ               # preview
    assert tier("sys.fix", {"ids": ["junk.temp.clean"], "apply": True}) == SAFE
    assert tier("sys.fix", {"ids": ["junk.old.delete"], "apply": True}) == RISKY
    assert tier("sys.fix", {"ids": ["tweaks.x.apply"], "apply": True}) == RISKY  # moderate
    assert tier("sys.fix", {"ids": ["junk.*"], "apply": True}) == RISKY         # includes risky
    assert tier("sys.fix", {"ids": ["nothing.here"], "apply": True}) == RISKY   # unknown fails closed
    assert tier("sys.fix", {"ids": ["*"], "apply": True}) == RISKY


def test_fix_tier_case_insensitive_resolution(area):
    assert tier("sys.fix", {"ids": ["JUNK.TEMP.CLEAN"], "apply": True}) == SAFE


def test_fix_tier_needs_admin_is_risky(area):
    assert tier("sys.fix", {"ids": ["junk.admin.clean"], "apply": True}) == RISKY


def test_fix_apply_journals_undo_and_confirms(area):
    payload, _ = runner.execute("sys.fix", {"ids": ["junk.temp.clean"], "apply": True})
    assert payload["ok"] and payload["result"]["outcomes"][0]["confirm_risky"] is True
    from control import journal
    e = journal.default().get(payload["journal_id"])
    assert e["undo"] == [{"action": "sys.undo", "args": {"id": "J-junk.temp.clean"}}]
    undo_payload, text = runner.undo(payload["journal_id"])
    assert undo_payload["ok"] and "undone J-junk.temp.clean" in text


def test_fix_apply_not_reversible_is_not_journaled_as_undoable(area):
    payload, _ = runner.execute("sys.fix", {"ids": ["junk.cache.clean"], "apply": True})
    assert payload["ok"]
    from control import journal
    e = journal.default().get(payload["journal_id"])
    assert e["undo"] is None or e["undo"] == []
    with pytest.raises(Exception):
        runner.undo(payload["journal_id"])


def test_slow_area_startup_times_out_and_kills_child(clean_registry):
    from control.areas import sys as sys_area
    from control.errors import ControlError
    slow = FAKE + ["--slow-start"]
    cfg = {"winsight_cmd": slow, "winsight_exe": "unused",
           "timeouts": {"sys": 20, "sys_fix": 20, "startup": 1}}
    try:
        with pytest.raises(ControlError) as e:
            sys_area.register_area(cfg)
        assert e.value.code == "TIMEOUT"
        assert "1 s" in e.value.hint and "winsight.exe doctor" in e.value.hint
        assert sys_area._child is None or sys_area._child.p is None
    finally:
        sys_area.shutdown()


def test_missing_exe_is_reported(clean_registry, tmp_path):
    from control.areas import sys as sys_area
    from control.errors import ControlError
    with pytest.raises(ControlError) as e:
        sys_area.register_area({"winsight_exe": str(tmp_path / "none.exe"), "timeouts": {"sys": 5, "sys_fix": 5}})
    assert e.value.code == "SYS_MISSING" and "go build" in e.value.hint

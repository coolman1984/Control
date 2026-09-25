import pytest

from control import registry, runner
from control.registry import READ, SAFE

pytest.importorskip("xl2ai")


def test_tools_and_tiers(clean_registry):
    from control.areas import data
    data.register_area({"data_workspace": None})
    assert registry.AREAS["data"]["ok"]
    assert registry.ACTIONS["data.start"].tier_for({}) == READ
    assert registry.ACTIONS["data.query"].tier_for({}) == READ
    assert registry.ACTIONS["data.prepare"].tier_for({}) == SAFE
    assert registry.ACTIONS["data.save_records"].tier_for({}) == SAFE


def test_empty_folder_gives_clean_result_not_crash(clean_registry, tmp_path):
    from control.areas import data
    data.register_area({"data_workspace": None})
    payload, text = runner.execute("data.start", {"workspace": str(tmp_path)})
    assert payload.get("code") != "INTERNAL"
    assert payload["ok"] or payload["code"].startswith("DATA_")

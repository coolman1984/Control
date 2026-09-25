import os

import pytest

from control import areas, config, guide, registry
from control.registry import Action, READ


def test_render_lists_actions_and_unavailable_areas(clean_registry):
    registry.set_area("t", True, 1)
    registry.set_area("sys", False, reason="SYS_MISSING: not built", hint="go build")
    registry.register(Action("t.look", "look | at things\nmore", {"type": "object", "properties": {}},
                             lambda a: ({}, ""), READ))
    text = guide.render()
    assert text.startswith("# Control — agent guide")
    assert r"| `t.look` | read | look \| at things |" in text
    assert "Unavailable on this PC: SYS_MISSING: not built" in text


@pytest.mark.skipif(os.environ.get("CONTROL_LIVE") != "1", reason="needs every area installed")
def test_committed_guide_is_fresh():
    areas.load_all()
    assert all(v["ok"] for v in registry.AREAS.values()), registry.AREAS
    assert (config.HUB / "AGENT_GUIDE.md").read_text(encoding="utf-8") == guide.render()

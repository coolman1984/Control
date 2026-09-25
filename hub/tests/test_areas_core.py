from control import areas, registry, runner
from control.registry import Action, SAFE


def test_failing_area_is_reported_not_fatal(clean_registry, monkeypatch):
    monkeypatch.setattr(areas, "AREA_MODULES", ["core", "does_not_exist"])
    areas.load_all()
    assert registry.AREAS["control"]["ok"] is True
    assert registry.AREAS["does_not_exist"]["ok"] is False
    assert registry.AREAS["does_not_exist"]["reason"]


def test_find_describe_journal_undo(clean_registry, monkeypatch):
    monkeypatch.setattr(areas, "AREA_MODULES", ["core"])
    areas.load_all()
    registry.register(Action("t.clean", "clean the temp folder", {"type": "object", "properties": {}},
                             lambda a: ({"_undo": [{"action": "sys.undo", "args": {}}]}, "cleaned"), SAFE))
    registry.register(Action("sys.undo", "restore", {"type": "object", "properties": {}},
                             lambda a: ({}, "restored"), SAFE))
    found = runner.execute("control.find_action", {"query": "temp"})[0]["actions"]
    assert found[0]["name"] == "t.clean"
    assert runner.execute("control.describe", {"action": "t.clean"})[0]["tier"] == SAFE
    eid = runner.execute("t.clean", {})[0]["journal_id"]
    assert runner.execute("control.journal", {"last": 5})[0]["entries"][-1]["id"] == eid
    payload, _ = runner.execute("control.undo", {"id": eid}, approver=lambda *a: "yes")
    assert payload["ok"] and payload["undone"] == eid

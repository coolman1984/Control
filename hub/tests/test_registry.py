import pytest

from control import registry
from control.errors import ControlError
from control.registry import Action, READ, RISKY, SAFE

SCHEMA = {"type": "object", "required": ["path"],
          "properties": {"path": {"type": "string", "description": "file"},
                         "count": {"type": "integer"}, "fast": {"type": "boolean"},
                         "mode": {"type": "string", "enum": ["a", "b"]},
                         "ids": {"type": "array", "items": {"type": "string"}}}}


def act(name="t.do", tier=SAFE):
    return Action(name, "does it", SCHEMA, lambda a: ({"ok": True}, "done"), tier)


def test_register_and_get(clean_registry):
    registry.register(act())
    assert registry.get("t.do").area == "t"


def test_unknown_action(clean_registry):
    with pytest.raises(ControlError) as e:
        registry.get("nope.x")
    assert e.value.code == "UNKNOWN_ACTION"


def test_duplicate_and_bad_names_rejected(clean_registry):
    registry.register(act())
    with pytest.raises(ValueError):
        registry.register(act())
    with pytest.raises(ValueError):
        registry.register(act(name="nodot"))


def test_validate_required_unknown_types(clean_registry):
    a = act()
    assert registry.validate(a, {"path": "x", "count": "3"}) == {"path": "x", "count": 3}
    for bad, code_part in [({}, "required"), ({"path": "x", "zzz": 1}, "unknown"),
                           ({"path": "x", "count": "many"}, "integer"),
                           ({"path": "x", "fast": 1}, "boolean"),
                           ({"path": "x", "mode": "c"}, "one of")]:
        with pytest.raises(ControlError) as e:
            registry.validate(a, bad)
        assert e.value.code == "USAGE" and code_part in e.value.message


def test_validate_accepts_arabic_and_single_string_for_array(clean_registry):
    a = act()
    assert registry.validate(a, {"path": "مبيعات.xlsx", "ids": "junk.*"})["ids"] == ["junk.*"]


def test_dynamic_tier(clean_registry):
    a = Action("t.fix", "", {"type": "object", "properties": {"apply": {"type": "boolean"}}},
               lambda a: ({}, ""), lambda args: RISKY if args.get("apply") else READ)
    assert a.tier_for({}) == READ and a.tier_for({"apply": True}) == RISKY
    bad = Action("t.bad", "", {}, lambda a: ({}, ""), "maybe")
    with pytest.raises(ValueError):
        bad.tier_for({})

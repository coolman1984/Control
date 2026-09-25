import json

from control import cli, registry
from control.registry import Action, SAFE

SCHEMA = {"type": "object", "required": ["path"], "properties": {
    "path": {"type": "string"}, "count": {"type": "integer"}, "fast": {"type": "boolean"},
    "ids": {"type": "array", "items": {"type": "string"}}}}


def test_parse_action_args(clean_registry):
    a = Action("t.do", "help", SCHEMA, lambda x: ({}, ""), SAFE)
    args, as_json = cli.parse_action_args(a, ["--path", "ملف.xlsx", "--count", "2", "--fast", "--ids", "a", "b", "--json"])
    assert args == {"path": "ملف.xlsx", "count": 2, "fast": True, "ids": ["a", "b"]} and as_json


def test_main_runs_action_and_exit_codes(clean_registry, monkeypatch, capsys):
    monkeypatch.setattr("control.areas.load_all", lambda cfg=None: None)
    registry.register(Action("t.do", "help", SCHEMA, lambda x: ({"got": x["path"]}, "did it"), SAFE))
    assert cli.main(["t.do", "--path", "x"]) == 0
    assert "did it" in capsys.readouterr().out
    assert cli.main(["t.do", "--path", "x", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["got"] == "x"
    assert cli.main(["t.nope"]) == 1

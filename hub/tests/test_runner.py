import pytest

from control import registry, runner, safety
from control.errors import ControlError
from control.journal import Journal
from control.registry import Action, READ, RISKY, SAFE

OBJ = {"type": "object", "properties": {"x": {"type": "string"}, "window": {"type": "string"}, "id": {"type": "string"}}}


@pytest.fixture
def j(tmp_path):
    return Journal(tmp_path / "j.jsonl")


def add(name, tier, handler):
    registry.register(Action(name, "h", OBJ, handler, tier, why="because"))


def test_read_runs_and_is_not_journaled(clean_registry, j):
    add("t.look", READ, lambda a: ({"seen": a.get("x")}, "saw"))
    payload, text = runner.execute("t.look", {"x": "1"}, journal=j)
    assert payload == {"seen": "1", "ok": True} and text == "saw" and j.entries() == []


def test_safe_runs_and_is_journaled_with_undo(clean_registry, j):
    add("t.do", SAFE, lambda a: ({"_undo": [{"action": "t.back", "args": {}}]}, "did"))
    payload, _ = runner.execute("t.do", {}, journal=j)
    e = j.get(payload["journal_id"])
    assert e["tier"] == SAFE and e["undo"] == [{"action": "t.back", "args": {}}] and "_undo" not in payload


@pytest.mark.parametrize("answer,code", [(safety.NO, "APPROVAL_DENIED"),
                                         (safety.TIMEOUT, "APPROVAL_TIMEOUT"),
                                         (safety.UNAVAILABLE, "APPROVAL_UNAVAILABLE")])
def test_risky_refused_without_yes(clean_registry, j, answer, code):
    ran = []
    add("t.boom", RISKY, lambda a: (ran.append(1) or {}, "boom"))
    payload, _ = runner.execute("t.boom", {}, journal=j, approver=lambda *a: answer)
    assert payload["code"] == code and ran == []
    assert j.get(payload["journal_id"])["approval"] == answer


def test_risky_runs_on_yes_and_off(clean_registry, j):
    add("t.boom", RISKY, lambda a: ({}, "boom"))
    for answer in (safety.YES, safety.OFF):
        payload, _ = runner.execute("t.boom", {}, journal=j, approver=lambda *a, x=answer: x)
        assert payload["ok"] is True


def test_errors_become_payloads(clean_registry, j):
    def bad(a):
        raise ControlError("NOPE", "no", "h")
    add("t.bad", SAFE, bad)
    add("t.crash", SAFE, lambda a: 1 / 0)
    assert runner.execute("t.bad", {}, journal=j)[0]["code"] == "NOPE"
    p, text = runner.execute("t.crash", {}, journal=j)
    assert p["code"] == "INTERNAL" and "ZeroDivisionError" in p["message"]
    assert runner.execute("t.missing", {}, journal=j)[0]["code"] == "UNKNOWN_ACTION"
    assert runner.execute("t.bad", {"zzz": 1}, journal=j)[0]["code"] == "USAGE"


def test_self_targeting_refused(clean_registry, j):
    add("win.click", SAFE, lambda a: ({}, "clicked"))
    assert runner.execute("win.click", {"window": "Control — approve?"}, journal=j)[0]["code"] == "REFUSED"


def test_ui_action_refused_while_approval_box_open(clean_registry, j, monkeypatch):
    ran = []
    add("win.click", SAFE, lambda a: (ran.append(1) or {}, "clicked"))
    monkeypatch.setattr(safety, "approval_box_open", lambda: True)
    payload, _ = runner.execute("win.click", {}, journal=j)
    assert payload["code"] == "REFUSED" and ran == []


def test_read_ui_action_runs_while_approval_box_open(clean_registry, j, monkeypatch):
    add("win.snapshot", READ, lambda a: ({"ok": True}, "tree"))
    monkeypatch.setattr(safety, "approval_box_open", lambda: True)
    payload, _ = runner.execute("win.snapshot", {}, journal=j)
    assert payload.get("ok") is True


def test_ui_action_runs_while_approval_box_closed(clean_registry, j, monkeypatch):
    ran = []
    add("win.click", SAFE, lambda a: (ran.append(1) or {}, "clicked"))
    monkeypatch.setattr(safety, "approval_box_open", lambda: False)
    payload, _ = runner.execute("win.click", {}, journal=j)
    assert ran == [1] and payload.get("ok") is True


def test_undo_runs_steps_preapproved_and_marks(clean_registry, j):
    calls = []
    add("sys.undo", RISKY, lambda a: (calls.append(a) or {}, "reversed"))
    add("t.do", SAFE, lambda a: ({"_undo": [{"action": "sys.undo", "args": {"x": "1"}}]}, "did"))
    eid = runner.execute("t.do", {}, journal=j)[0]["journal_id"]
    payload, _ = runner.undo(eid, journal=j)
    assert payload["ok"] and calls == [{"x": "1"}] and j.get(eid)["undone"]
    with pytest.raises(ControlError) as e:
        runner.undo(eid, journal=j)
    assert e.value.code == "ALREADY_UNDONE"


def test_undo_refuses_steps_not_in_allowlist(clean_registry, j):
    add("t.back", SAFE, lambda a: ({}, "reversed"))
    add("t.do", SAFE, lambda a: ({"_undo": [{"action": "t.back", "args": {}}]}, "did"))
    eid = runner.execute("t.do", {}, journal=j)[0]["journal_id"]
    with pytest.raises(ControlError) as e:
        runner.undo(eid, journal=j)
    assert e.value.code == "UNDO_REFUSED"


def test_control_undo_approval_shows_resolved_steps(clean_registry, j):
    add("sys.undo", RISKY, lambda a: ({}, "reversed"))

    def undo_handler(a):
        return runner.undo(a["id"], journal=j)
    add("control.undo", RISKY, undo_handler)
    eid = j.append(action="sys.fix", args={}, tier=SAFE, approval=None, ok=True,
                   undo=[{"action": "sys.undo", "args": {"id": "J-1"}}])
    seen = {}

    def approver(name, args, reason):
        seen.update(name=name, args=args)
        return safety.YES
    payload, _ = runner.execute("control.undo", {"id": eid}, journal=j, approver=approver)
    assert payload["ok"]
    assert seen == {"name": "control.undo",
                    "args": {"id": eid, "steps": [{"action": "sys.undo", "args": {"id": "J-1"}}]}}


def test_control_undo_approval_falls_through_when_id_not_found(clean_registry, j):
    add("control.undo", RISKY, lambda a: runner.undo(a["id"], journal=j))
    seen = {}

    def approver(name, args, reason):
        seen.update(name=name, args=args)
        return safety.YES
    payload, _ = runner.execute("control.undo", {"id": "nope"}, journal=j, approver=approver)
    assert seen == {"name": "control.undo", "args": {"id": "nope"}}
    assert payload["code"] == "NOT_FOUND"


def test_not_undoable(clean_registry, j):
    add("t.do", SAFE, lambda a: ({}, "did"))
    eid = runner.execute("t.do", {}, journal=j)[0]["journal_id"]
    with pytest.raises(ControlError) as e:
        runner.undo(eid, journal=j)
    assert e.value.code == "NOT_UNDOABLE"


def test_unknown_answer_fails_closed(clean_registry, j):
    ran = []
    add("t.risky", RISKY, lambda a: (ran.append(1) or {}, "ran"))
    
    # Test with None
    payload, _ = runner.execute("t.risky", {}, journal=j, approver=lambda *a: None)
    assert payload["code"] == "APPROVAL_DENIED" and ran == []
    assert j.get(payload["journal_id"])["approval"] is None
    
    # Test with unexpected string
    payload, _ = runner.execute("t.risky", {}, journal=j, approver=lambda *a: "maybe")
    assert payload["code"] == "APPROVAL_DENIED" and ran == []
    assert j.get(payload["journal_id"])["approval"] == "maybe"

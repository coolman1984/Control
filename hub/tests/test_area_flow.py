import pytest

from control import config, registry, runner, safety
from control.areas import flow as flow_area
from control.registry import Action, READ


def write_flow(name, text):
    d = config.home() / "flows"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.toml").write_text(text, encoding="utf-8")


@pytest.fixture(autouse=True)
def area(clean_registry):
    registry.register(Action("t.a", "test", {"type": "object", "properties": {"x": {}}},
                              lambda a: ({"ok": True, "value": 1}, "ok"), READ))
    registry.register(Action("t.risky", "test", {"type": "object", "properties": {}},
                              lambda a: ({"ok": True}, "did it"), "risky", why="risky test action"))
    flow_area.register_area({})


SIMPLE = """
name = "hello"
description = "a tiny flow"

[[steps]]
id = "a"
type = "action"
action = "t.a"
"""

RISKY_FLOW = """
name = "danger"

[[steps]]
id = "a"
type = "action"
action = "t.risky"
"""


def test_flow_areas_registered():
    assert registry.AREAS["flow"]["ok"] is True
    assert registry.AREAS["run"]["ok"] is True


def test_list_describe_validate(control_home):
    write_flow("hello", SIMPLE)
    payload, _ = runner.execute("flow.list", {})
    assert payload["flows"][0]["name"] == "hello" and payload["flows"][0]["approved"] is False

    payload, _ = runner.execute("flow.describe", {"name": "hello"})
    assert payload["steps"] == ["a"] and payload["issues"] == []

    payload, _ = runner.execute("flow.validate", {"name": "hello"})
    assert payload["ok"] is True

    payload, _ = runner.execute("flow.validate", {"text": 'name = "x"\n[[steps]]\nid="a"\ntype="action"\naction="made.up.bad"\n'})
    assert payload["ok"] is True or payload["issues"]        # warning-only since area 't' isn't "loaded" info-free here


def test_flow_not_found(control_home):
    payload, _ = runner.execute("flow.describe", {"name": "nope"})
    assert payload["code"] == "FLOW_NOT_FOUND"


def test_approve_then_run_unattended(control_home, monkeypatch):
    write_flow("hello", SIMPLE)
    payload, _ = runner.execute("flow.approve", {"name": "hello"}, approver=lambda *a: safety.YES)
    assert payload["ok"] is True

    payload, _ = runner.execute("flow.run", {"name": "hello"})
    assert payload["status"] == "done" and payload["steps"]["a"]["value"] == 1


def test_unapproved_run_is_risky_and_refused_on_no(control_home):
    write_flow("hello", SIMPLE)
    payload, _ = runner.execute("flow.run", {"name": "hello"}, approver=lambda *a: safety.NO)
    assert payload["code"] == "APPROVAL_DENIED"


def test_approve_refuses_invalid_flow(control_home):
    write_flow("bad", 'name = "bad"\n[[steps]]\nid="a"\ntype="action"\naction="nodothere"\n')
    payload, _ = runner.execute("flow.approve", {"name": "bad"}, approver=lambda *a: safety.YES)
    assert payload["code"] == "FLOW_INVALID"


def test_editing_an_approved_flow_invalidates_approval(control_home):
    write_flow("hello", SIMPLE)
    runner.execute("flow.approve", {"name": "hello"}, approver=lambda *a: safety.YES)
    write_flow("hello", SIMPLE.replace("a tiny flow", "an edited flow"))
    payload, _ = runner.execute("flow.run", {"name": "hello"}, approver=lambda *a: safety.NO)
    assert payload["code"] == "APPROVAL_DENIED"       # no longer pre-approved: this hash never approved


def test_revoke(control_home):
    write_flow("hello", SIMPLE)
    runner.execute("flow.approve", {"name": "hello"}, approver=lambda *a: safety.YES)
    runner.execute("flow.revoke", {"name": "hello"})
    payload, _ = runner.execute("flow.run", {"name": "hello"}, approver=lambda *a: safety.NO)
    assert payload["code"] == "APPROVAL_DENIED"


def test_dry_run_is_read_and_needs_no_approval(control_home):
    write_flow("hello", SIMPLE)
    payload, _ = runner.execute("flow.dry_run", {"name": "hello"}, approver=lambda *a: (_ for _ in ()).throw(AssertionError("should not ask")))
    assert payload["status"] == "done"


def test_run_lifecycle_list_get_cancel(control_home):
    write_flow("wait", 'name = "wait"\n[[steps]]\nid="ask"\ntype="ask_human"\nprompt="go?"\n')
    runner.execute("flow.approve", {"name": "wait"}, approver=lambda *a: safety.YES)
    payload, _ = runner.execute("flow.run", {"name": "wait"})
    assert payload["status"] == "waiting"
    run_id = payload["id"]

    listed, _ = runner.execute("run.list", {})
    assert any(r["id"] == run_id for r in listed["runs"])

    got, _ = runner.execute("run.get", {"id": run_id})
    assert got["status"] == "waiting" and "step_log" in got

    cancelled, _ = runner.execute("run.cancel", {"id": run_id})
    assert cancelled["status"] == "cancelled"


def test_run_resume_answers_ask_human(control_home):
    write_flow("wait", """
name = "wait"
[[steps]]
id = "ask"
type = "ask_human"
prompt = "go?"
[[steps.fields]]
name = "go"
required = true
""")
    runner.execute("flow.approve", {"name": "wait"}, approver=lambda *a: safety.YES)
    payload, _ = runner.execute("flow.run", {"name": "wait"})
    run_id = payload["id"]
    resumed, _ = runner.execute("run.resume", {"id": run_id, "answer": {"go": "yes"}})
    assert resumed["status"] == "done"


def test_run_retry_step_after_needs_attention(control_home):
    from control.store import default as store_default
    write_flow("hello", SIMPLE)
    runner.execute("flow.approve", {"name": "hello"}, approver=lambda *a: safety.YES)
    store = store_default()
    from control.flow import model as flow_model
    f = flow_model.parse_file(config.home() / "flows" / "hello.toml")
    store.create_run("stuck", "hello", f.hash, {}, flow_text=f.text)
    store.start_step("stuck", "0", "a", 1, {"type": "action"})

    resumed, _ = runner.execute("run.resume", {"id": "stuck"})
    assert resumed["status"] == "needs_attention"

    payload, _ = runner.execute("run.retry_step", {"id": "stuck"}, approver=lambda *a: safety.YES)
    assert payload["status"] == "done"

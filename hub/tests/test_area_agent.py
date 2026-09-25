import time

from control import config, registry, runner, safety
from control.areas import agent as agent_area
from control.areas import flow as flow_area
from control.registry import Action, READ


def write_flow(name, text):
    d = config.home() / "flows"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.toml").write_text(text, encoding="utf-8")


def setup(clean_registry):
    registry.register(Action("t.a", "test", {"type": "object", "properties": {}},
                              lambda a: ({"ok": True}, "ok"), READ))
    flow_area.register_area({})
    agent_area.register_area({})


def test_areas_registered(clean_registry):
    setup(clean_registry)
    assert registry.AREAS["agent"]["ok"] is True


def test_interval_trigger_fires_once_then_waits(clean_registry, control_home):
    setup(clean_registry)
    write_flow("ping", """
name = "ping"
[[steps]]
id = "a"
type = "action"
action = "t.a"

[[triggers]]
type = "interval"
seconds = 3600
""")
    runner.execute("flow.approve", {"name": "ping"}, approver=lambda *a: safety.YES)

    payload, _ = runner.execute("agent.tick", {})
    assert len(payload["fired"]) == 1 and payload["fired"][0]["flow"] == "ping"

    payload2, _ = runner.execute("agent.tick", {})
    assert payload2["fired"] == []                  # not due again for another hour


def test_cron_trigger_fires_on_matching_minute(clean_registry, control_home, monkeypatch):
    setup(clean_registry)
    write_flow("daily", """
name = "daily"
[[steps]]
id = "a"
type = "action"
action = "t.a"

[[triggers]]
type = "cron"
expr = "* * * * *"
""")
    runner.execute("flow.approve", {"name": "daily"}, approver=lambda *a: safety.YES)
    payload, _ = runner.execute("agent.tick", {})
    assert len(payload["fired"]) == 1


def test_unapproved_risky_flow_trigger_fails_closed(clean_registry, control_home):
    registry.register(Action("t.risky", "test", {"type": "object", "properties": {}},
                              lambda a: ({"ok": True}, "did it"), "risky", why="test"))
    flow_area.register_area({})
    agent_area.register_area({})
    write_flow("danger", """
name = "danger"
[[steps]]
id = "a"
type = "action"
action = "t.risky"

[[triggers]]
type = "interval"
seconds = 1
""")
    # never approved -> flow.run is risky -> no one is at the screen -> APPROVAL_UNAVAILABLE
    payload, _ = runner.execute("agent.tick", {})
    assert payload["fired"][0]["ok"] is False        # flow.run itself was refused before it ran a step
    assert payload["fired"][0]["status"] is None


def test_file_trigger_fires_once_per_stable_file(clean_registry, control_home, tmp_path):
    setup(clean_registry)
    watch = tmp_path / "inbox"
    watch.mkdir()
    write_flow("onfile", f"""
name = "onfile"
[[steps]]
id = "a"
type = "action"
action = "t.a"

[[triggers]]
type = "file"
watch = {str(watch)!r}
pattern = "*.txt"
stable_for_s = 0
""")
    runner.execute("flow.approve", {"name": "onfile"}, approver=lambda *a: safety.YES)

    payload, _ = runner.execute("agent.tick", {})
    assert payload["fired"] == []                    # nothing dropped yet

    (watch / "a.txt").write_text("hi")
    payload, _ = runner.execute("agent.tick", {})
    assert len(payload["fired"]) == 1

    payload, _ = runner.execute("agent.tick", {})
    assert payload["fired"] == []                    # same file: already fired, not again


def test_stop_all_cancels_every_active_run(clean_registry, control_home):
    setup(clean_registry)
    write_flow("wait1", 'name = "wait1"\n[[steps]]\nid="ask"\ntype="ask_human"\nprompt="?"\n')
    write_flow("wait2", 'name = "wait2"\n[[steps]]\nid="ask"\ntype="ask_human"\nprompt="?"\n')
    runner.execute("flow.approve", {"name": "wait1"}, approver=lambda *a: safety.YES)
    runner.execute("flow.approve", {"name": "wait2"}, approver=lambda *a: safety.YES)
    r1, _ = runner.execute("flow.run", {"name": "wait1"})
    r2, _ = runner.execute("flow.run", {"name": "wait2"})
    assert r1["status"] == "waiting" and r2["status"] == "waiting"

    payload, _ = runner.execute("agent.stop_all", {})
    assert sorted(payload["stopped"]) == sorted([r1["id"], r2["id"]])

    got1, _ = runner.execute("run.get", {"id": r1["id"]})
    assert got1["status"] == "cancelled"


def test_status_lists_triggers(clean_registry, control_home):
    setup(clean_registry)
    write_flow("ping", """
name = "ping"
[[steps]]
id = "a"
type = "action"
action = "t.a"

[[triggers]]
type = "cron"
expr = "0 8 * * *"
""")
    payload, _ = runner.execute("agent.status", {})
    assert payload["triggers"][0]["flow"] == "ping" and payload["triggers"][0]["type"] == "cron"


def test_broken_flow_file_is_reported_not_fatal(clean_registry, control_home):
    setup(clean_registry)
    write_flow("broken", "not valid toml [[[")
    write_flow("ok", """
name = "ok"
[[steps]]
id = "a"
type = "action"
action = "t.a"
[[triggers]]
type = "interval"
seconds = 1
""")
    runner.execute("flow.approve", {"name": "ok"}, approver=lambda *a: safety.YES)
    payload, _ = runner.execute("agent.tick", {})
    assert payload["errors"] and any("broken" in e for e in payload["errors"])
    assert len(payload["fired"]) == 1

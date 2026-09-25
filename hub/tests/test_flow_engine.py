import pytest

from control import registry, runner, safety
from control.errors import ControlError
from control.flow import engine, model
from control.registry import Action, READ, RISKY, SAFE
from control.store import Store


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "control.db")


SCHEMA = {"type": "object", "properties": {"x": {}, "n": {}, "name": {}}}


def add(name, tier, handler, why=""):
    registry.register(Action(name, "test action", SCHEMA, handler, tier, why=why))


def flow(text):
    return model.parse(text)


def test_sequential_steps_pass_context(clean_registry, store):
    add("t.a", READ, lambda a: ({"value": 41}, "a"))
    add("t.b", READ, lambda a: ({"value": a["x"] + 1}, "b"))
    run = engine.run_flow(flow("""
name = "seq"
[[steps]]
id = "a"
type = "action"
action = "t.a"
[[steps]]
id = "b"
type = "action"
action = "t.b"
[steps.args]
x = "{{ steps.a.value }}"
"""), {}, store=store)
    assert run["status"] == "done"
    assert run["context"]["steps"]["b"]["value"] == 42


def test_failed_step_stops_the_run(clean_registry, store):
    calls = []
    add("t.a", READ, lambda a: (calls.append(1) or {"ok": False, "code": "NOPE"}, "no"))
    add("t.b", READ, lambda a: (calls.append(1) or {}, "never"))
    run = engine.run_flow(flow("""
name = "f"
[[steps]]
id = "a"
type = "action"
action = "t.a"
[[steps]]
id = "b"
type = "action"
action = "t.b"
"""), {}, store=store)
    assert run["status"] == "failed" and run["cursor"] == 0
    assert calls == [1]


def test_retry_succeeds_on_second_attempt(clean_registry, store):
    attempts = {"n": 0}

    def flaky(a):
        attempts["n"] += 1
        if attempts["n"] < 2:
            return {"ok": False, "code": "BUSY"}, "busy"
        return {"ok": True, "done": True}, "ok"

    add("t.flaky", READ, flaky)
    run = engine.run_flow(flow("""
name = "f"
[[steps]]
id = "a"
type = "action"
action = "t.flaky"
[steps.retry]
attempts = 3
backoff_s = 0
on = ["BUSY"]
"""), {}, store=store)
    assert run["status"] == "done" and attempts["n"] == 2


def test_retry_does_not_apply_to_unlisted_codes(clean_registry, store):
    attempts = {"n": 0}

    def always_fail(a):
        attempts["n"] += 1
        return {"ok": False, "code": "OTHER"}, "no"

    add("t.a", READ, always_fail)
    run = engine.run_flow(flow("""
name = "f"
[[steps]]
id = "a"
type = "action"
action = "t.a"
[steps.retry]
attempts = 3
on = ["BUSY"]
"""), {}, store=store)
    assert run["status"] == "failed" and attempts["n"] == 1


def test_if_runs_nested_steps_when_truthy(clean_registry, store):
    ran = []
    add("t.cond", READ, lambda a: ({"go": True}, "c"))
    add("t.inner", READ, lambda a: (ran.append(1) or {}, "i"))
    run = engine.run_flow(flow("""
name = "f"
[[steps]]
id = "cond"
type = "action"
action = "t.cond"
[[steps]]
id = "gate"
type = "if"
that = "{{ steps.cond.go }}"
[[steps.steps]]
id = "inner"
type = "action"
action = "t.inner"
"""), {}, store=store)
    assert run["status"] == "done" and ran == [1]
    assert run["context"]["steps"]["gate"]["matched"] is True
    assert run["context"]["steps"]["inner"]["ok"] is True


def test_if_skips_when_falsy(clean_registry, store):
    ran = []
    add("t.cond", READ, lambda a: ({"go": False}, "c"))
    add("t.inner", READ, lambda a: (ran.append(1) or {}, "i"))
    run = engine.run_flow(flow("""
name = "f"
[[steps]]
id = "cond"
type = "action"
action = "t.cond"
[[steps]]
id = "gate"
type = "if"
that = "{{ steps.cond.go }}"
[[steps.steps]]
id = "inner"
type = "action"
action = "t.inner"
"""), {}, store=store)
    assert run["status"] == "done" and ran == []
    assert run["context"]["steps"]["gate"]["matched"] is False


def test_for_each_processes_every_item_and_reports_failures(clean_registry, store):
    seen = []

    def maybe_fail(a):
        seen.append(a["n"])
        if a["n"] == 2:
            return {"ok": False, "code": "BAD"}, "bad"
        return {"ok": True}, "ok"

    add("t.item", READ, maybe_fail)
    run = engine.run_flow(flow("""
name = "f"
[[steps]]
id = "loop"
type = "for_each"
items = "{{ input.nums }}"
as = "n"
[[steps.steps]]
id = "process"
type = "action"
action = "t.item"
[steps.steps.args]
n = "{{ vars.n }}"
"""), {"nums": [1, 2, 3]}, store=store)
    assert run["status"] == "done"
    assert seen == [1, 2, 3]
    out = run["context"]["steps"]["loop"]
    assert out["count"] == 3 and out["succeeded"] == 2 and out["failed"] == 1


def test_for_each_stop_on_error_halts_the_loop(clean_registry, store):
    seen = []

    def maybe_fail(a):
        seen.append(a["n"])
        return ({"ok": False, "code": "BAD"}, "bad") if a["n"] == 2 else ({"ok": True}, "ok")

    add("t.item", READ, maybe_fail)
    run = engine.run_flow(flow("""
name = "f"
[[steps]]
id = "loop"
type = "for_each"
items = "{{ input.nums }}"
as = "n"
stop_on_error = true
[[steps.steps]]
id = "process"
type = "action"
action = "t.item"
[steps.steps.args]
n = "{{ vars.n }}"
"""), {"nums": [1, 2, 3]}, store=store)
    assert run["status"] == "failed"
    assert seen == [1, 2]


def test_parallel_runs_all_and_merges_output(clean_registry, store):
    add("t.x", READ, lambda a: ({"who": "x"}, "x"))
    add("t.y", READ, lambda a: ({"who": "y"}, "y"))
    run = engine.run_flow(flow("""
name = "f"
[[steps]]
id = "both"
type = "parallel"
[[steps.actions]]
id = "x"
action = "t.x"
[[steps.actions]]
id = "y"
action = "t.y"
"""), {}, store=store)
    assert run["status"] == "done"
    out = run["context"]["steps"]["both"]
    assert out["x"]["who"] == "x" and out["y"]["who"] == "y"


def test_parallel_fails_if_any_branch_fails(clean_registry, store):
    add("t.x", READ, lambda a: ({"ok": True}, "x"))
    add("t.y", READ, lambda a: ({"ok": False, "code": "BOOM"}, "y"))
    run = engine.run_flow(flow("""
name = "f"
[[steps]]
id = "both"
type = "parallel"
[[steps.actions]]
id = "x"
action = "t.x"
[[steps.actions]]
id = "y"
action = "t.y"
"""), {}, store=store)
    assert run["status"] == "failed"


def test_wait_for_polls_until_condition_true(clean_registry, store):
    calls = {"n": 0}

    def poll(a):
        calls["n"] += 1
        return {"ready": calls["n"] >= 3}, "poll"

    add("t.poll", READ, poll)
    run = engine.run_flow(flow("""
name = "f"
[[steps]]
id = "wait"
type = "wait_for"
action = "t.poll"
that = "{{ result.ready }}"
interval_s = 0
timeout_s = 10
"""), {}, store=store)
    assert run["status"] == "done" and calls["n"] == 3


def test_wait_for_times_out(clean_registry, store):
    add("t.poll", READ, lambda a: ({"ready": False}, "poll"))
    run = engine.run_flow(flow("""
name = "f"
[[steps]]
id = "wait"
type = "wait_for"
action = "t.poll"
that = "{{ result.ready }}"
interval_s = 0
timeout_s = 0
"""), {}, store=store)
    assert run["status"] == "failed" and "WAIT_FOR_TIMEOUT" in run["error"]


def test_wait_for_refuses_non_read_action(clean_registry, store):
    add("t.act", SAFE, lambda a: ({"ready": True}, "poll"))
    run = engine.run_flow(flow("""
name = "f"
[[steps]]
id = "wait"
type = "wait_for"
action = "t.act"
that = "{{ result.ready }}"
"""), {}, store=store)
    assert run["status"] == "failed" and "WAIT_FOR_NOT_READ" in run["error"]


def test_ask_human_pauses_then_resumes_with_answer(clean_registry, store):
    add("t.after", READ, lambda a: ({"got": a["name"]}, "ok"))
    f = flow("""
name = "f"
[[steps]]
id = "ask"
type = "ask_human"
prompt = "what is your name?"
[[steps.fields]]
name = "name"
required = true
[[steps]]
id = "after"
type = "action"
action = "t.after"
[steps.args]
name = "{{ steps.ask.answer.name }}"
""")
    run = engine.run_flow(f, {}, store=store)
    assert run["status"] == "waiting"
    assert run["wait_for"]["prompt"] == "what is your name?"

    incomplete = engine.resume_run(run["id"], store=store, answer={})
    assert incomplete["status"] == "failed" and "ANSWER_INCOMPLETE" in incomplete["error"]

    resumed = engine.resume_run(run["id"], store=store, answer={"name": "sam"})
    assert resumed["status"] == "done"
    assert resumed["context"]["steps"]["after"]["got"] == "sam"


def test_resume_requires_a_waiting_or_failed_run(clean_registry, store):
    add("t.a", READ, lambda a: ({}, "ok"))
    run = engine.run_flow(flow('name = "f"\n[[steps]]\nid="a"\ntype="action"\naction="t.a"\n'), {}, store=store)
    assert run["status"] == "done"
    with pytest.raises(ControlError) as e:
        engine.resume_run(run["id"], store=store)
    assert e.value.code == "RUN_NOT_RESUMABLE"
    with pytest.raises(ControlError) as e:
        engine.resume_run("missing", store=store)
    assert e.value.code == "RUN_NOT_FOUND"


def test_crash_mid_step_needs_attention_unless_idempotent(clean_registry, store):
    add("t.a", READ, lambda a: ({"ok": True}, "a"))
    f = flow('name = "f"\n[[steps]]\nid="a"\ntype="action"\naction="t.a"\n')
    run = engine.run_flow(f, {}, store=store)
    assert run["status"] == "done"

    # simulate a process that died between start_step and finish_step
    store.create_run("crashed", "f", f.hash, {}, flow_text=f.text)
    store.start_step("crashed", "0", "a", 1, {"type": "action"})
    resumed = engine.resume_run("crashed", store=store)
    assert resumed["status"] == "needs_attention"

    # force=True (run.retry_step) re-runs it anyway
    forced = engine.resume_run("crashed", store=store, force=True)
    assert forced["status"] == "done"


def test_crash_mid_step_auto_resumes_when_idempotent(clean_registry, store):
    calls = []
    add("t.a", READ, lambda a: (calls.append(1) or {"ok": True}, "a"))
    f = flow('name = "f"\n[[steps]]\nid="a"\ntype="action"\naction="t.a"\nidempotent = true\n')
    store.create_run("crashed", "f", f.hash, {}, flow_text=f.text)
    store.start_step("crashed", "0", "a", 1, {"type": "action"})
    resumed = engine.resume_run("crashed", store=store)
    assert resumed["status"] == "done" and calls == [1]


def test_single_flight_lock_blocks_concurrent_run(clean_registry, store):
    add("t.a", READ, lambda a: ({}, "a"))
    f = flow('name = "f"\n[[steps]]\nid="a"\ntype="action"\naction="t.a"\n')
    store.acquire_lock("f", "someone-else")
    run = engine.run_flow(f, {}, store=store)
    assert run["status"] == "failed" and "already active" in run["error"]


def test_parallel_flow_does_not_take_a_lock(clean_registry, store):
    add("t.a", READ, lambda a: ({}, "a"))
    f = flow('name = "f"\nconcurrency = "parallel"\n[[steps]]\nid="a"\ntype="action"\naction="t.a"\n')
    store.acquire_lock("f", "someone-else")
    run = engine.run_flow(f, {}, store=store)
    assert run["status"] == "done"


def test_cancel_stops_a_waiting_run_and_releases_lock(clean_registry, store):
    f = flow('name = "f"\n[[steps]]\nid="ask"\ntype="ask_human"\nprompt = "?"\n')
    run = engine.run_flow(f, {}, store=store)
    assert run["status"] == "waiting"
    cancelled = engine.cancel_run(run["id"], store=store)
    assert cancelled["status"] == "cancelled"
    assert store.lock_holder("f") is None
    with pytest.raises(ControlError):
        engine.cancel_run(run["id"], store=store)


def test_risky_step_runs_unattended_when_flow_is_pre_approved(clean_registry, store):
    ran = []
    add("t.risky", RISKY, lambda a: (ran.append(1) or {"ok": True}, "did it"), why="test")
    f = flow('name = "f"\n[[steps]]\nid="a"\ntype="action"\naction="t.risky"\n')
    run = engine.run_flow(f, {}, store=store, pre_approved=True)
    assert run["status"] == "done" and ran == [1]


def test_risky_step_is_refused_without_pre_approval_on_this_headless_box(clean_registry, store):
    add("t.risky", RISKY, lambda a: ({"ok": True}, "did it"), why="test")
    f = flow('name = "f"\n[[steps]]\nid="a"\ntype="action"\naction="t.risky"\n')
    run = engine.run_flow(f, {}, store=store, pre_approved=False)
    # no desktop here -> APPROVAL_UNAVAILABLE -> the step (and the run) fails, exactly like a real
    # risky step nobody could approve
    assert run["status"] == "failed"


def test_call_flow_runs_a_sub_flow_and_exposes_its_output(clean_registry, store):
    add("t.a", READ, lambda a: ({"value": 1}, "a"))
    sub = flow('name = "sub"\n[[steps]]\nid="a"\ntype="action"\naction="t.a"\n')
    main = flow("""
name = "main"
[[steps]]
id = "call"
type = "call_flow"
flow = "sub"
""")
    run = engine.run_flow(main, {}, store=store, resolve_flow=lambda n: sub if n == "sub" else None)
    assert run["status"] == "done"
    assert run["context"]["steps"]["call"]["steps"]["a"]["value"] == 1


def test_call_flow_missing_flow_fails(clean_registry, store):
    main = flow('name = "main"\n[[steps]]\nid="call"\ntype="call_flow"\nflow = "nope"\n')
    run = engine.run_flow(main, {}, store=store, resolve_flow=lambda n: None)
    assert run["status"] == "failed" and "FLOW_NOT_FOUND" in run["error"]


def test_call_flow_rejects_a_pausing_sub_flow(clean_registry, store):
    sub = flow('name = "sub"\n[[steps]]\nid="ask"\ntype="ask_human"\nprompt="?"\n')
    main = flow('name = "main"\n[[steps]]\nid="call"\ntype="call_flow"\nflow = "sub"\n')
    run = engine.run_flow(main, {}, store=store, resolve_flow=lambda n: sub if n == "sub" else None)
    assert run["status"] == "failed" and "CALL_FLOW_WAITING" in run["error"]


def test_set_and_assert(clean_registry, store):
    run = engine.run_flow(flow("""
name = "f"
[[steps]]
id = "s"
type = "set"
[steps.vars]
greeting = "hi"
[[steps]]
id = "check"
type = "assert"
that = "{{ vars.greeting }}"
equals = "hi"
"""), {}, store=store)
    assert run["status"] == "done"


def test_assert_failure_fails_the_run(clean_registry, store):
    run = engine.run_flow(flow("""
name = "f"
[[steps]]
id = "check"
type = "assert"
that = "{{ input.x }}"
equals = "hi"
"""), {"x": "bye"}, store=store)
    assert run["status"] == "failed" and "ASSERT_FAILED" in run["error"]


def test_dry_run_never_calls_the_real_action(clean_registry, store):
    called = []
    add("t.a", RISKY, lambda a: (called.append(1) or {"ok": True}, "a"))
    run = engine.run_flow(flow("""
name = "f"
[[steps]]
id = "a"
type = "action"
action = "t.a"
[[steps]]
id = "b"
type = "action"
action = "t.a"
[steps.args]
x = "{{ steps.a.manifest.folder }}"
"""), {}, store=store, dry_run=True)
    assert called == []
    assert run["status"] == "done"
    assert run["context"]["steps"]["a"]["_dry_run"] is True
    assert "cannot preview further" in run["context"]["steps"]["b"]["note"]

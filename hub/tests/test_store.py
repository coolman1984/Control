import pytest

from control.store import Store


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "control.db")


def test_create_and_get_run(store):
    run = store.create_run("r1", "myflow", "hash1", {"date": "2026-09-25"})
    assert run["status"] == "running" and run["cursor"] == 0
    assert run["input"] == {"date": "2026-09-25"}
    assert store.get_run("does-not-exist") is None


def test_update_run_context_and_cursor(store):
    store.create_run("r1", "myflow", "hash1", {})
    store.update_run("r1", cursor=2, context={"input": {}, "steps": {"a": {"ok": True}}, "vars": {}}, status="done")
    run = store.get_run("r1")
    assert run["cursor"] == 2 and run["status"] == "done" and run["context"]["steps"]["a"]["ok"] is True


def test_step_checkpoints_and_last_attempt(store):
    store.create_run("r1", "myflow", "hash1", {})
    store.start_step("r1", "0", "a", 1, {"type": "action"})
    assert store.last_attempt("r1", "0") == {"attempt": 1, "status": "started", "output": None}
    store.finish_step("r1", "0", 1, "done", output={"ok": True})
    assert store.last_attempt("r1", "0") == {"attempt": 1, "status": "done", "output": {"ok": True}}
    steps = store.steps_for("r1")
    assert len(steps) == 1 and steps[0]["step_id"] == "a" and steps[0]["status"] == "done"


def test_list_runs_filters(store):
    store.create_run("r1", "flowA", "h", {})
    store.create_run("r2", "flowB", "h", {})
    store.update_run("r2", status="done")
    assert [r["id"] for r in store.list_runs(flow_name="flowA")] == ["r1"]
    assert [r["id"] for r in store.list_runs(status="done")] == ["r2"]


def test_approvals_hash_and_expiry(store, monkeypatch):
    assert store.is_approved("f", "h1") is False
    store.set_approval("f", "h1")
    assert store.is_approved("f", "h1") is True
    assert store.is_approved("f", "h2") is False        # edited file -> different hash -> not approved
    store.revoke_approval("f")
    assert store.is_approved("f", "h1") is False


def test_approval_expiry(store):
    store.set_approval("f", "h1", ttl_seconds=-1)        # already expired
    assert store.is_approved("f", "h1") is False


def test_single_flight_lock(store):
    assert store.acquire_lock("f", "r1") is True
    assert store.acquire_lock("f", "r2") is False
    assert store.lock_holder("f") == "r1"
    assert store.acquire_or_confirm_lock("f", "r1") is True
    assert store.acquire_or_confirm_lock("f", "r2") is False
    store.release_lock("f")
    assert store.lock_holder("f") is None
    assert store.acquire_lock("f", "r2") is True

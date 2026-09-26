import json
import threading
import urllib.request

from control import dashboard, journal as journal_mod
from control.store import Store


def test_api_runs_and_run_detail(tmp_path):
    store = Store(tmp_path / "c.db")
    store.create_run("r1", "flowA", "h", {"x": 1})
    store.update_run("r1", status="done", context={"input": {}, "steps": {"a": {"ok": True}}, "vars": {}})
    store.start_step("r1", "0", "a", 1, {"type": "action"})
    store.finish_step("r1", "0", 1, "done", output={"ok": True})

    rows = dashboard.api_runs(store, {})
    assert rows[0]["id"] == "r1" and rows[0]["status"] == "done"

    row = dashboard.api_run(store, "r1")
    assert row["steps"]["a"]["ok"] is True
    assert row["step_log"][0]["step_id"] == "a"
    assert dashboard.api_run(store, "missing") is None


def test_api_runs_filters(tmp_path):
    store = Store(tmp_path / "c.db")
    store.create_run("r1", "flowA", "h", {})
    store.create_run("r2", "flowB", "h", {})
    store.update_run("r2", status="done")
    assert [r["id"] for r in dashboard.api_runs(store, {"flow": ["flowA"]})] == ["r1"]
    assert [r["id"] for r in dashboard.api_runs(store, {"status": ["done"]})] == ["r2"]


def test_api_journal(tmp_path):
    j = journal_mod.Journal(tmp_path / "j.jsonl")
    j.append(action="t.a", args={"x": 1}, tier="safe", approval=None, ok=True)
    rows = dashboard.api_journal(j, {})
    assert rows[0]["action"] == "t.a"


def test_api_triggers_lists_and_reports_broken_flows(tmp_path):
    store = Store(tmp_path / "c.db")
    d = tmp_path / "flows"
    d.mkdir()
    (d / "ok.toml").write_text('name = "ok"\n[[steps]]\nid="a"\ntype="action"\naction="t.a"\n'
                               '[[triggers]]\ntype = "interval"\nseconds = 60\n', encoding="utf-8")
    (d / "broken.toml").write_text("not valid [[[", encoding="utf-8")
    rows = dashboard.api_triggers(store, d)
    by_flow = {r.get("flow"): r for r in rows}
    assert by_flow["ok"]["type"] == "interval" and by_flow["ok"]["approved"] is False
    assert "error" in by_flow["broken"]


def test_http_server_end_to_end_with_token(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTROL_HOME", str(tmp_path / "home"))
    from control import config
    (config.home() / "flows").mkdir(parents=True, exist_ok=True)

    out = []

    class FakeOut:
        def write(self, s):
            out.append(s)

    server_ref = {}
    original_server_class = dashboard.ThreadingHTTPServer

    def capturing_server(*a, **kw):
        s = original_server_class(*a, **kw)
        server_ref["server"] = s
        return s

    monkeypatch.setattr(dashboard, "ThreadingHTTPServer", capturing_server)

    t = threading.Thread(target=dashboard.run_forever, kwargs={"port": 0, "token": "secret", "out": FakeOut()})
    t.start()
    import time
    for _ in range(50):
        if "server" in server_ref:
            break
        time.sleep(0.02)
    port = server_ref["server"].server_port

    try:
        resp = urllib.request.urlopen(f"http://127.0.0.1:{port}/api/runs?token=secret", timeout=2)
        assert resp.status == 200
        assert json.loads(resp.read()) == []

        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/runs", timeout=2)
            assert False, "should have been refused without a token"
        except urllib.error.HTTPError as e:
            assert e.code == 403

        page = urllib.request.urlopen(f"http://127.0.0.1:{port}/?token=secret", timeout=2)
        assert b"Control" in page.read()
    finally:
        server_ref["server"].shutdown()
        t.join(timeout=2)

import io

from control import agent


def test_run_forever_ticks_and_stops_after_n_sleeps(monkeypatch):
    calls = []

    def fake_execute(name, args=None):
        calls.append(name)
        if len(calls) >= 3:
            raise KeyboardInterrupt
        return {"fired": [], "errors": []}, "nothing due"

    monkeypatch.setattr("control.runner.execute", fake_execute)
    out = io.StringIO()
    code = agent.run_forever(interval_s=0, out=out, sleep=lambda s: None)
    assert code == 0
    assert calls == ["agent.tick"] * 3
    assert "stopped" in out.getvalue()


def test_run_forever_once_returns_after_a_single_tick(monkeypatch):
    calls = []
    monkeypatch.setattr("control.runner.execute", lambda name, args=None: (calls.append(1) or ({"fired": []}, "ok")))
    code = agent.run_forever(interval_s=0, out=io.StringIO(), sleep=lambda s: (_ for _ in ()).throw(AssertionError("should not sleep")), once=True)
    assert code == 0 and calls == [1]

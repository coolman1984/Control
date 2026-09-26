import sys
from pathlib import Path

import pytest

from control import registry, runner
from control.areas import flow as flow_area
from control.areas import record as record_area
from control.registry import READ, SAFE

FAKE_DIR = str(Path(__file__).parent / "fakes" / "wad")
CFG = {"gmes_python": sys.executable, "wad_dir": FAKE_DIR}


@pytest.fixture
def area(clean_registry):
    flow_area.register_area({})
    record_area.register_area(CFG)


def test_tiers(area):
    tiers = {n: a.tier for n, a in registry.ACTIONS.items() if n.startswith("record.")}
    assert tiers == {"record.start": SAFE, "record.stop": SAFE, "record.status": READ,
                     "record.list": READ, "record.audit": SAFE, "record.to_flow": SAFE}


def test_status_when_nothing_running(area):
    payload, text = runner.execute("record.status", {})
    assert payload["active"] is False and "no recording" in text


def test_start_stop_cycle_produces_a_session(area, control_home):
    payload, _ = runner.execute("record.start", {"task": "close month-end report", "window": "Excel"})
    assert payload["ok"] is True
    session_id = payload["session_id"]

    again, _ = runner.execute("record.start", {})
    assert again["code"] == "RECORD_ALREADY_ACTIVE"

    stopped, _ = runner.execute("record.stop", {})
    assert stopped["ok"] is True and stopped["session_id"] == session_id and stopped["steps"] == 6

    status, _ = runner.execute("record.status", {})
    assert status["active"] is False

    listed, _ = runner.execute("record.list", {})
    assert listed["sessions"][0]["session_id"] == session_id and listed["sessions"][0]["steps"] == 6


def test_stop_without_a_running_recording(area):
    payload, _ = runner.execute("record.stop", {})
    assert payload["code"] == "RECORD_NOT_ACTIVE"


def test_unavailable_when_wad_dir_missing(clean_registry, control_home):
    record_area.register_area({"gmes_python": sys.executable, "wad_dir": "/does/not/exist"})
    payload, _ = runner.execute("record.start", {})
    assert payload["code"] == "RECORD_UNAVAILABLE"


def test_audit_report_redacts_password_and_groups_by_window(area, control_home):
    runner.execute("record.start", {"task": "reset a password"})
    stopped, _ = runner.execute("record.stop", {})
    payload, text = runner.execute("record.audit", {"session_id": stopped["session_id"]})
    assert payload["ok"] is True
    assert "not shown" in text
    assert "hunter2" not in text and "${ENV:WAD_SECRET}" not in text
    assert Path(payload["path"]).exists()


def test_audit_missing_session(area):
    payload, _ = runner.execute("record.audit", {"session_id": "nope"})
    assert payload["code"] == "SESSION_NOT_FOUND"


def test_to_flow_writes_a_draft_and_maps_secret(area, control_home):
    from control import config

    runner.execute("record.start", {"task": "close month-end report"})
    stopped, _ = runner.execute("record.stop", {})
    payload, text = runner.execute("record.to_flow", {"session_id": stopped["session_id"], "name": "close-month"})
    assert payload["ok"] is True and payload["steps"] == 6

    flow_path = config.home() / "flows" / "close-month.toml"
    assert flow_path.exists()
    written = flow_path.read_text(encoding="utf-8")
    assert "secret:recorded_password" in written
    assert "hunter2" not in written

    validated, _ = runner.execute("flow.validate", {"name": "close-month"})


def test_steps_to_flow_toml_is_pure_and_maps_secrets():
    from control.flow import model

    steps = [
        {"cmd": "click", "target": "role=Button name=Save", "window": "Notepad"},
        {"cmd": "type", "target": "role=Edit", "window": "Notepad", "text": "hi"},
        {"cmd": "type", "target": "role=Edit name=Password", "window": "Notepad", "text": "${ENV:WAD_SECRET}"},
        {"cmd": "select", "target": "role=ComboBox", "window": "Notepad", "option": "Yes"},
        {"cmd": "press", "combo": "ctrl+s", "window": "Notepad"},
        {"cmd": "unknown-thing", "target": "x"},
    ]
    text = record_area.steps_to_flow_toml("demo", "a demo", steps)
    flow = model.parse(text)
    assert flow.name == "demo" and len(flow.steps) == 5      # the unknown step type is skipped
    assert flow.steps[2].raw["args"]["text"] == "{{ secret:recorded_password }}"
    assert model.validate(flow) == []

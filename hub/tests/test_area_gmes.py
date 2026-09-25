import sys
from pathlib import Path

import pytest

from control import registry, runner
from control.registry import READ, RISKY, SAFE

FAKE_DIR = str(Path(__file__).parent / "fakes" / "gmes")
CFG = {"gmes_dir": FAKE_DIR, "gmes_python": sys.executable, "timeouts": {"gmes": 3}}


@pytest.fixture
def area(clean_registry):
    from control.areas import gmes
    gmes.register_area(CFG)


def test_tiers(area):
    t = {n: a.tier_for({}) for n, a in registry.ACTIONS.items()}
    assert t == {"gmes.find": READ, "gmes.describe": READ, "gmes.run": SAFE,
                 "gmes.batch_list": READ, "gmes.batch_plan": READ, "gmes.batch_run": SAFE}


def test_run_passes_args_and_reads_manifest(area):
    payload, text = runner.execute("gmes.run", {"screens": ["P3151WM00"], "division": "VD",
                                                "date": "20260924", "set": ["A=1"]})
    assert payload["ok"] and payload["manifest"]["results"][0]["rows"] == 42
    assert "division=VD" in text and "['A=1']" in text and "مرحبا" in text


@pytest.mark.parametrize("screen,code", [("BUSY", "GMES_BUSY"), ("BAD", "GMES_FAILED"), ("SLOW", "TIMEOUT")])
def test_failures(area, screen, code):
    payload, _ = runner.execute("gmes.run", {"screens": [screen]})
    assert payload["ok"] is False and payload["code"] == code


def test_find_and_batch(area):
    assert "find stock" in runner.execute("gmes.find", {"text": "stock"})[1]
    assert "batch plan all --date yesterday" in runner.execute(
        "gmes.batch_plan", {"selection": ["all"], "date": "yesterday"})[1]


def test_run_tier_risky_when_export_or_output_dir_given(area):
    a = registry.ACTIONS["gmes.run"]
    assert a.tier_for({}) == SAFE
    assert a.tier_for({"export": "xlsx"}) == RISKY
    assert a.tier_for({"output_dir": r"C:\out"}) == RISKY
    assert "saved export settings" in a.help


def test_batch_run_tier_risky_when_export_or_output_dir_given(area):
    a = registry.ACTIONS["gmes.batch_run"]
    assert a.tier_for({}) == SAFE
    assert a.tier_for({"export": "xlsx"}) == RISKY
    assert a.tier_for({"output_dir": r"C:\out"}) == RISKY


def test_run_deletes_temp_manifest_folder(area, monkeypatch):
    from control.areas import gmes as gmes_mod
    seen = {}
    real_mkdtemp = gmes_mod.tempfile.mkdtemp

    def spy(*a, **kw):
        d = real_mkdtemp(*a, **kw)
        seen["dir"] = d
        return d
    monkeypatch.setattr(gmes_mod.tempfile, "mkdtemp", spy)
    payload, _ = runner.execute("gmes.run", {"screens": ["P3151WM00"]})
    assert payload["ok"]
    assert "dir" in seen and not Path(seen["dir"]).exists()


def test_missing_folder(clean_registry, tmp_path):
    from control.areas import gmes
    from control.errors import ControlError
    with pytest.raises(ControlError) as e:
        gmes.register_area({"gmes_dir": str(tmp_path), "gmes_python": sys.executable, "timeouts": {"gmes": 3}})
    assert e.value.code == "GMES_MISSING"

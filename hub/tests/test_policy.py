import pytest

from control import policy
from control.errors import ControlError


def test_no_file_means_no_restriction(tmp_path):
    pol = policy.load(tmp_path)
    assert pol == {}
    policy.check(pol, "any-flow", "win.shell", {})    # never raises


def test_deny_actions_blocks(tmp_path):
    (tmp_path / "policy.toml").write_text(
        '[defaults]\ndeny_actions = ["win.shell"]\n', encoding="utf-8")
    pol = policy.load(tmp_path)
    with pytest.raises(ControlError) as e:
        policy.check_action(pol, "any-flow", "win.shell")
    assert e.value.code == "POLICY_DENIED"
    policy.check_action(pol, "any-flow", "win.click")     # not denied


def test_deny_globs():
    pol = {"defaults": {"deny_actions": ["sys.*"]}}
    with pytest.raises(ControlError):
        policy.check_action(pol, "f", "sys.fix")
    policy.check_action(pol, "f", "win.click")


def test_flow_specific_deny_adds_to_defaults():
    pol = {"defaults": {"deny_actions": ["win.shell"]},
           "flows": {"nightly": {"deny_actions": ["gmes.run"]}}}
    with pytest.raises(ControlError):
        policy.check_action(pol, "nightly", "win.shell")   # default floor still applies
    with pytest.raises(ControlError):
        policy.check_action(pol, "nightly", "gmes.run")
    policy.check_action(pol, "other-flow", "gmes.run")     # only nightly's own deny applies to it


def test_allow_actions_restricts_to_only_those():
    pol = {"flows": {"nightly": {"allow_actions": ["gmes.*", "data.*"]}}}
    policy.check_action(pol, "nightly", "gmes.run")
    with pytest.raises(ControlError) as e:
        policy.check_action(pol, "nightly", "win.click")
    assert e.value.code == "POLICY_NOT_ALLOWED"
    policy.check_action(pol, "other-flow", "win.click")    # no allowlist for this flow


def test_rate_limit():
    pol = {"flows": {"f": {"max_calls": {"win.click": 2}}}}
    counts = {}
    policy.check(pol, "f", "win.click", counts)
    policy.check(pol, "f", "win.click", counts)
    assert counts["win.click"] == 2
    with pytest.raises(ControlError) as e:
        policy.check(pol, "f", "win.click", counts)
    assert e.value.code == "POLICY_RATE_LIMIT"


def test_rate_limit_glob_and_per_action_counting():
    pol = {"flows": {"f": {"max_calls": {"win.*": 3}}}}
    counts = {}
    policy.check(pol, "f", "win.click", counts)
    policy.check(pol, "f", "win.type", counts)
    policy.check(pol, "f", "win.click", counts)
    # win.click has run twice, win.type once - both under the shared glob limit of 3 each
    assert counts == {"win.click": 2, "win.type": 1}

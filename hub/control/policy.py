"""An optional `<CONTROL_HOME>/policy.toml`: per-flow action allow/deny lists and per-run rate
limits. Piece 4 of the master roadmap. Absent file = no extra restriction beyond the tier/approval
model already enforced everywhere (this is a second, independent fence, not a replacement).

```toml
[defaults]
deny_actions = ["win.shell", "sys.fix"]      # a safety floor: no flow's own policy can remove these

[flows.nightly-report]
allow_actions = ["gmes.*", "data.*"]         # if given, only these globs may run (defaults'
                                              # deny_actions still applies on top)
[flows.nightly-report.max_calls]
"win.click" = 200                            # this run may call win.click at most 200 times
```
"""
import fnmatch
import tomllib
from pathlib import Path

from . import config
from .errors import ControlError


def load(home=None):
    path = (home or config.home()) / "policy.toml"
    if not path.exists():
        return {}
    with open(path, "rb") as fh:
        return tomllib.load(fh)


def for_flow(policy, flow_name):
    defaults = policy.get("defaults", {})
    own = policy.get("flows", {}).get(flow_name, {})
    return {"deny_actions": list(defaults.get("deny_actions", [])) + list(own.get("deny_actions", [])),
            "allow_actions": own.get("allow_actions"),         # None = no allowlist restriction
            "max_calls": own.get("max_calls", {})}


def _matches_any(name, globs):
    return any(fnmatch.fnmatchcase(name, g) for g in globs)


def check_action(policy, flow_name, action_name):
    """Raises ControlError if `action_name` is not allowed for this flow by policy.toml."""
    rules = for_flow(policy, flow_name)
    if _matches_any(action_name, rules["deny_actions"]):
        raise ControlError("POLICY_DENIED", f"{action_name} is denied for {flow_name} by policy.toml",
                           "edit policy.toml's deny_actions/allow_actions if this flow should be allowed to")
    if rules["allow_actions"] is not None and not _matches_any(action_name, rules["allow_actions"]):
        raise ControlError("POLICY_NOT_ALLOWED", f"{action_name} is not in {flow_name}'s allow_actions",
                           "add a matching glob to policy.toml's [flows.<name>] allow_actions")


def check_rate(policy, flow_name, action_name, counts):
    """`counts` is the run's own {action_name: n} so far; raises once a configured limit for a
    matching glob would be exceeded by this call. The limit is per exact action name, not pooled
    across every action a glob matches - `"win.*" = 3` allows `win.click` up to 3 times *and*
    `win.type` up to 3 times, not 3 `win.*` calls combined."""
    for glob, limit in for_flow(policy, flow_name)["max_calls"].items():
        if fnmatch.fnmatchcase(action_name, glob) and counts.get(action_name, 0) >= limit:
            raise ControlError("POLICY_RATE_LIMIT", f"{action_name} has already run {limit} time(s) "
                               f"this run, the limit policy.toml sets for {glob!r}",
                               "raise the limit in policy.toml if this run legitimately needs more")


def check(policy, flow_name, action_name, counts):
    check_action(policy, flow_name, action_name)
    check_rate(policy, flow_name, action_name, counts)
    counts[action_name] = counts.get(action_name, 0) + 1

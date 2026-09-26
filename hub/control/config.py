"""Where Control keeps its state, and the few settings that differ per machine."""
import copy
import os
import sys
import tomllib
from pathlib import Path

HUB = Path(__file__).resolve().parents[1]
ROOT = HUB.parent

DEFAULTS = {
    "gmes_dir": str(ROOT / "opening-nerp-tcode"),
    "gmes_python": sys.executable,
    "wad_dir": str(ROOT / "win-agent-desktop"),
    "winsight_exe": str(HUB / "bin" / "winsight.exe"),
    "data_workspace": None,
    "timeouts": {"gmes": 1800, "sys": 300, "sys_fix": 900, "startup": 15},
    "approval_timeout": 120,
    "agent_poll_s": 30,
}


def home():
    env = os.environ.get("CONTROL_HOME")
    if env:
        return Path(env)
    return Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "Control"


def load():
    cfg = copy.deepcopy(DEFAULTS)
    path = home() / "config.toml"
    if path.exists():
        with open(path, "rb") as fh:
            user = tomllib.load(fh)
        for key, value in user.items():
            if isinstance(value, dict) and isinstance(cfg.get(key), dict):
                cfg[key].update(value)
            else:
                cfg[key] = value
    return cfg

from control import config
from control.errors import ControlError


def test_home_follows_env(control_home):
    assert config.home() == control_home


def test_defaults_point_at_sibling_folders():
    cfg = config.load()
    assert cfg["gmes_dir"].endswith("opening-nerp-tcode")
    assert cfg["winsight_exe"].endswith("winsight.exe")
    assert cfg["timeouts"]["gmes"] == 1800
    assert cfg["approval_timeout"] == 120


def test_user_file_overrides_and_merges(control_home):
    control_home.mkdir(parents=True)
    (control_home / "config.toml").write_text('gmes_dir = "D:/g"\n[timeouts]\nsys = 5\n', encoding="utf-8")
    cfg = config.load()
    assert cfg["gmes_dir"] == "D:/g"
    assert cfg["timeouts"]["sys"] == 5 and cfg["timeouts"]["gmes"] == 1800


def test_error_shapes():
    e = ControlError("X", "went wrong", "try y")
    assert e.payload() == {"ok": False, "code": "X", "message": "went wrong", "hint": "try y"}
    assert e.text() == "ERROR X: went wrong\n  hint: try y"

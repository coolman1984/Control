import pytest

from control.flow import model


def test_parse_cron_trigger():
    f = model.parse("""
name = "f"
[[steps]]
id = "a"
type = "action"
action = "sys.health"

[[triggers]]
type = "cron"
expr = "0 8 * * 1-5"
""")
    assert f.triggers == [{"type": "cron", "expr": "0 8 * * 1-5"}]


def test_parse_interval_and_file_triggers():
    f = model.parse("""
name = "f"
[[steps]]
id = "a"
type = "action"
action = "sys.health"

[[triggers]]
type = "interval"
seconds = 300

[[triggers]]
type = "file"
watch = "/inbox"
pattern = "*.xlsx"
stable_for_s = 10
""")
    assert f.triggers[0] == {"type": "interval", "seconds": 300}
    assert f.triggers[1]["watch"] == "/inbox" and f.triggers[1]["stable_for_s"] == 10


@pytest.mark.parametrize("toml_trigger,msg", [
    ('[[triggers]]\ntype = "bogus"\n', "type must be one of"),
    ('[[triggers]]\ntype = "cron"\n', "needs 'expr'"),
    ('[[triggers]]\ntype = "interval"\n', "needs 'seconds'"),
    ('[[triggers]]\ntype = "file"\n', "needs 'watch'"),
])
def test_bad_trigger_shapes_rejected(toml_trigger, msg):
    text = 'name = "f"\n[[steps]]\nid="a"\ntype="action"\naction="sys.health"\n' + toml_trigger
    with pytest.raises(model.FlowError) as e:
        model.parse(text)
    assert msg in str(e.value)


def test_validate_catches_bad_cron_expr():
    f = model.parse("""
name = "f"
[[steps]]
id = "a"
type = "action"
action = "sys.health"

[[triggers]]
type = "cron"
expr = "not a cron"
""")
    issues = model.validate(f)
    assert any("triggers[0]" in i for i in issues)


def test_validate_ok_for_good_triggers():
    f = model.parse("""
name = "f"
[[steps]]
id = "a"
type = "action"
action = "sys.health"

[[triggers]]
type = "cron"
expr = "*/5 * * * *"

[[triggers]]
type = "interval"
seconds = 60
""")
    assert model.validate(f, known_action=lambda n: True) == []

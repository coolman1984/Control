import pytest

from control.flow import model


def test_parse_minimal():
    flow = model.parse("""
name = "demo"
description = "a demo flow"

[[steps]]
id = "a"
type = "action"
action = "sys.health"
""")
    assert flow.name == "demo" and flow.description == "a demo flow"
    assert flow.step_ids() == ["a"]
    assert flow.concurrency == "single"


def test_parse_nested_if_and_for_each():
    flow = model.parse("""
name = "demo"

[[steps]]
id = "a"
type = "action"
action = "sys.health"

[[steps]]
id = "b"
type = "if"
that = "{{ steps.a.ok }}"

[[steps.steps]]
id = "b1"
type = "action"
action = "sys.junk"

[[steps]]
id = "c"
type = "for_each"
items = "{{ input.files }}"

[[steps.steps]]
id = "c1"
type = "action"
action = "data.prepare"
""")
    assert flow.step_ids() == ["a", "b", "b1", "c", "c1"]


@pytest.mark.parametrize("text,msg", [
    ("", "name"),
    ('name = "x"\n', "no steps"),
    ('name = "x"\n[[steps]]\ntype = "action"\naction = "sys.health"\n', "id"),
    ('name = "x"\n[[steps]]\nid = "a"\naction = "sys.health"\n', "type"),
    ('name = "x"\n[[steps]]\nid = "a"\ntype = "action"\n', "action"),
    ("not valid toml [[[", "invalid TOML"),
])
def test_parse_rejects_bad_shapes(text, msg):
    with pytest.raises(model.FlowError) as e:
        model.parse(text)
    assert msg.split()[0].lower() in str(e.value).lower()


def test_hash_is_stable_and_content_sensitive():
    a = model.parse('name = "x"\n[[steps]]\nid="a"\ntype="action"\naction="sys.health"\n')
    b = model.parse(a.text)
    c = model.parse(a.text.replace("sys.health", "sys.junk"))
    assert a.hash == b.hash
    assert a.hash != c.hash


def test_validate_duplicate_ids():
    flow = model.parse("""
name = "x"
[[steps]]
id = "a"
type = "action"
action = "sys.health"
[[steps]]
id = "a"
type = "action"
action = "sys.junk"
""")
    issues = model.validate(flow)
    assert any("duplicate step id" in i for i in issues)


def test_validate_known_action_hook():
    flow = model.parse('name = "x"\n[[steps]]\nid="a"\ntype="action"\naction="made.up"\n')
    assert any("unknown action" in i for i in model.validate(flow, known_action=lambda n: False))
    assert model.validate(flow, known_action=lambda n: True) == []
    assert any("WARNING" in i for i in model.validate(flow, known_action=lambda n: None))


def test_validate_rejects_nested_pause_steps():
    flow = model.parse("""
name = "x"
[[steps]]
id = "a"
type = "if"
that = "{{ input.go }}"
[[steps.steps]]
id = "b"
type = "ask_human"
prompt = "ok?"
""")
    issues = model.validate(flow)
    assert any("top-level" in i for i in issues)

import pytest

from control.errors import ControlError
from control.flow import expr

CTX = {"input": {"date": "2026-09-25", "files": ["a.xlsx", "b.xlsx"]},
       "steps": {"report": {"ok": True, "manifest": {"folder": "/tmp/out", "rows": [{"name": "x"}]}}},
       "vars": {"n": 3}}


def test_whole_string_template_returns_raw_value():
    assert expr.resolve_template("{{ steps.report.ok }}", CTX) is True
    assert expr.resolve_template("{{ steps.report.manifest }}", CTX) == {"folder": "/tmp/out", "rows": [{"name": "x"}]}
    assert expr.resolve_template("{{ input.files }}", CTX) == ["a.xlsx", "b.xlsx"]


def test_indexed_and_nested_path():
    assert expr.resolve_template("{{ steps.report.manifest.rows[0].name }}", CTX) == "x"
    assert expr.resolve_template("{{ input.files[1] }}", CTX) == "b.xlsx"
    assert expr.resolve_template("{{ vars.n }}", CTX) == 3


def test_embedded_template_stringifies():
    assert expr.resolve_template("folder: {{ steps.report.manifest.folder }}!", CTX) == "folder: /tmp/out!"


def test_no_template_passthrough():
    assert expr.resolve_template("plain text", CTX) == "plain text"


def test_unresolved_path_raises():
    with pytest.raises(ControlError) as e:
        expr.resolve_template("{{ steps.missing.x }}", CTX)
    assert e.value.code == "EXPR_UNRESOLVED"


def test_invalid_path_syntax_raises():
    with pytest.raises(ControlError) as e:
        expr.resolve_template("{{ steps.report; drop }}", CTX)
    assert e.value.code == "EXPR_INVALID"


def test_resolve_walks_nested_structures():
    args = {"a": "{{ input.date }}", "b": [1, "{{ vars.n }}"], "c": {"d": "{{ steps.report.ok }}"}}
    assert expr.resolve(args, CTX) == {"a": "2026-09-25", "b": [1, 3], "c": {"d": True}}


@pytest.mark.parametrize("value,expected", [
    (True, True), (False, False), (None, False), (0, False), ("", False),
    ([], False), ({}, False), ([1], True), ({"a": 1}, True), ("x", True), (1, True),
])
def test_truthy(value, expected):
    assert expr.truthy(value) is expected

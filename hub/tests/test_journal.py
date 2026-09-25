import json

import pytest

from control import journal
from control.errors import ControlError


def test_append_read_and_mask(tmp_path):
    j = journal.Journal(tmp_path / "j.jsonl")
    eid = j.append(action="gmes.run", args={"password": "p@ss", "screens": ["P1"], "ملف": "قيمة"},
                   tier="safe", approval=None, ok=True, ms=5)
    raw = (tmp_path / "j.jsonl").read_text(encoding="utf-8")
    assert "p@ss" not in raw and "قيمة" in raw
    e = j.get(eid)
    assert e["args"]["password"] == "***" and e["undone"] is False


def test_undo_marking_and_last(tmp_path):
    j = journal.Journal(tmp_path / "j.jsonl")
    ids = [j.append(action=f"a.{i}", args={}, tier="safe", approval=None, ok=True) for i in range(3)]
    j.mark_undone(ids[0])
    assert j.get(ids[0])["undone"] is True
    assert [e["action"] for e in j.last(2)] == ["a.1", "a.2"]
    assert len(j.entries()) == 3              # undo markers are not entries


def test_missing_and_corrupt_lines(tmp_path):
    p = tmp_path / "j.jsonl"
    p.write_text('not json\n{"id": "x1", "action": "a.b"}\n', encoding="utf-8")
    j = journal.Journal(p)
    assert j.get("x1")["action"] == "a.b"
    with pytest.raises(ControlError) as e:
        j.get("nope")
    assert e.value.code == "NOT_FOUND"


def test_default_lives_in_home(control_home):
    assert journal.default().path == control_home / "journal.jsonl"


def test_mask_is_recursive_over_nested_structures():
    out = journal.mask({"outer": {"password": "p1", "items": [{"token": "t1"}, {"ok": "x"}]}})
    assert out["outer"]["password"] == "***"
    assert out["outer"]["items"][0]["token"] == "***"
    assert out["outer"]["items"][1]["ok"] == "x"


def test_mask_redacts_free_text_for_type_actions():
    out = journal.mask({"text": "hunter2"}, action="web.type")
    assert out["text"] != "hunter2"
    assert "hunter2" not in json.dumps(out)
    assert out["text"].startswith("<7 chars, sha256:")


def test_mask_redacts_free_text_for_clipboard_and_press():
    assert journal.mask({"value": "secretstuff"}, action="win.clipboard")["value"].startswith("<11 chars")
    # "keys" also matches the secret-key regex (contains "key"), so it is masked outright - still
    # never stored as free text either way.
    assert journal.mask({"keys": "ctrl+a"}, action="win.press")["keys"] == "***"


def test_mask_leaves_free_text_alone_for_other_actions():
    assert journal.mask({"text": "hello"}, action="gmes.run")["text"] == "hello"
    assert journal.mask({"text": "hello"})["text"] == "hello"


def test_mask_preserves_arabic_non_secret_values():
    assert journal.mask({"note": "مرحبا"}, action="gmes.run")["note"] == "مرحبا"


def test_journal_append_masks_free_text_via_action(tmp_path):
    j = journal.Journal(tmp_path / "j.jsonl")
    j.append(action="web.type", args={"text": "hunter2"}, tier="safe", approval=None, ok=True)
    raw = (tmp_path / "j.jsonl").read_text(encoding="utf-8")
    assert "hunter2" not in raw

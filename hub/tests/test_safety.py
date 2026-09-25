from control import safety


def test_off_switch(monkeypatch):
    monkeypatch.setenv("CONTROL_APPROVAL", "off")
    assert safety.approve("win.shell", {}, "r", box=lambda *a: safety.NO) == safety.OFF


def test_box_answers_pass_through():
    for answer in (safety.YES, safety.NO, safety.TIMEOUT, safety.UNAVAILABLE):
        assert safety.approve("win.shell", {"command": "dir"}, "r", box=lambda *a, x=answer: x) == answer


def test_describe_masks_secrets_and_keeps_arabic():
    text = safety.describe("web.type", {"password": "hunter2", "text": "مرحبا"}, "types into a page")
    assert "hunter2" not in text and "مرحبا" in text and "web.type" in text


def test_box_gets_title_and_timeout():
    seen = {}

    def box(text, title, timeout_s):
        seen.update(title=title, timeout=timeout_s)
        return safety.NO

    safety.approve("win.shell", {}, "r", box=box, timeout_s=7)
    assert seen == {"title": "Control — approve?", "timeout": 7}


def test_self_targeting_detected():
    assert safety.targets_approval_box("win.click", {"window": "Control — approve?"})
    assert not safety.targets_approval_box("win.click", {"window": "Notepad"})
    assert not safety.targets_approval_box("sys.health", {"x": "Control — approve?"})


def test_approval_box_open_uses_injected_finder():
    seen = {}

    def finder(cls, title):
        seen.update(cls=cls, title=title)
        return 12345

    assert safety.approval_box_open(finder=finder) is True
    assert seen == {"cls": "#32770", "title": safety.APPROVAL_TITLE}
    assert safety.approval_box_open(finder=lambda cls, title: 0) is False


def test_approval_box_open_false_on_non_windows(monkeypatch):
    monkeypatch.setattr(safety.sys, "platform", "linux")
    assert safety.approval_box_open() is False


def test_describe_truncates_very_long_args_showing_head_and_tail():
    text = safety.describe("win.type", {"text": "x" * 3000}, "r")
    assert len(text) < 2200
    assert text.count("x") == 1386             # 1000 head + 400 tail, mid replaced
    assert "characters hidden" in text
    assert "WARNING: the arguments are very long" in text
    assert text.rstrip().endswith("Allow it?")


def test_describe_short_args_are_not_truncated():
    text = safety.describe("win.type", {"text": "short"}, "r")
    assert "characters hidden" not in text and "WARNING" not in text


def test_describe_escapes_unicode_format_characters():
    text = safety.describe("win.type", {"text": "a‮b"}, "r")
    assert "‮" not in text
    assert "\\u202e" in text

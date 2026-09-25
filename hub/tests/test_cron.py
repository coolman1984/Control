from datetime import datetime

import pytest

from control.triggers import cron


def dt(s):
    return datetime.strptime(s, "%Y-%m-%d %H:%M")


def test_every_minute():
    spec = cron.parse("* * * * *")
    assert spec.matches(dt("2026-09-25 08:00"))
    assert spec.matches(dt("2026-09-25 23:59"))


def test_specific_time():
    spec = cron.parse("30 8 * * *")
    assert spec.matches(dt("2026-09-25 08:30"))
    assert not spec.matches(dt("2026-09-25 08:31"))
    assert not spec.matches(dt("2026-09-25 09:30"))


def test_step_and_range():
    spec = cron.parse("*/15 9-17 * * *")
    assert spec.matches(dt("2026-09-25 09:00"))
    assert spec.matches(dt("2026-09-25 09:15"))
    assert not spec.matches(dt("2026-09-25 09:10"))
    assert not spec.matches(dt("2026-09-25 18:00"))


def test_weekdays_only():
    spec = cron.parse("0 8 * * 1-5")
    assert spec.matches(dt("2026-09-25 08:00"))    # Friday
    assert not spec.matches(dt("2026-09-26 08:00"))  # Saturday
    assert not spec.matches(dt("2026-09-27 08:00"))  # Sunday


def test_dow_zero_is_sunday():
    spec = cron.parse("0 8 * * 0")
    assert spec.matches(dt("2026-09-27 08:00"))    # Sunday
    assert not spec.matches(dt("2026-09-28 08:00"))  # Monday


def test_dom_and_dow_both_restricted_is_or():
    spec = cron.parse("0 0 1 * 1")     # first of the month, OR any Monday
    assert spec.matches(dt("2026-09-01 00:00"))    # 1st (Tuesday)
    assert spec.matches(dt("2026-09-28 00:00"))    # a Monday
    assert not spec.matches(dt("2026-09-15 00:00"))  # neither


def test_next_after():
    spec = cron.parse("0 8 * * *")
    nxt = spec.next_after(dt("2026-09-25 08:00"))
    assert nxt == dt("2026-09-26 08:00")           # already past today's 08:00 exactly -> tomorrow
    nxt2 = spec.next_after(dt("2026-09-25 07:00"))
    assert nxt2 == dt("2026-09-25 08:00")


@pytest.mark.parametrize("bad", ["* * *", "60 * * * *", "*/0 * * * *", "x * * * *"])
def test_bad_expr_raises(bad):
    with pytest.raises(cron.CronError):
        cron.parse(bad)

"""A compact, stdlib-only 5-field cron matcher: minute hour day-of-month month day-of-week.

Deliberately not the full cron grammar: numbers, `*`, `a-b`, `a/n`, `a-b/n` and comma lists only —
no month/weekday names, no `?`, `L`, `W` or `#`. That covers every schedule a piece-3 flow trigger
plausibly needs ("weekdays at 8am", "every 15 minutes") without pulling in a dependency, which
would break the stdlib-only constraint the rest of `control/` holds to.
"""
from datetime import datetime, timedelta

FIELDS = ("minute", "hour", "dom", "month", "dow")
RANGES = {"minute": (0, 59), "hour": (0, 23), "dom": (1, 31), "month": (1, 12), "dow": (0, 6)}
MAX_STEPS = 4 * 366 * 24 * 60          # about 4 years of minutes: a generous, finite search bound


class CronError(ValueError):
    pass


def _parse_field(spec, lo, hi, name):
    allowed = set()
    for part in spec.split(","):
        base, _, step_s = part.partition("/")
        step = 1
        if step_s:
            try:
                step = int(step_s)
            except ValueError:
                raise CronError(f"{name}: bad step in {part!r}") from None
            if step < 1:
                raise CronError(f"{name}: step must be positive in {part!r}")
        if base == "*":
            start, end = lo, hi
        elif "-" in base:
            a, b = base.split("-", 1)
            try:
                start, end = int(a), int(b)
            except ValueError:
                raise CronError(f"{name}: bad range {part!r}") from None
        else:
            try:
                start = end = int(base)
            except ValueError:
                raise CronError(f"{name}: bad value {part!r}") from None
        if not (lo <= start <= hi and lo <= end <= hi and start <= end):
            raise CronError(f"{name}: {part!r} is outside {lo}-{hi}")
        allowed.update(range(start, end + 1, step))
    return allowed


class CronSpec:
    def __init__(self, expr):
        parts = expr.split()
        if len(parts) != 5:
            raise CronError(f"expected 5 fields (minute hour dom month dow), got {len(parts)}: {expr!r}")
        self.expr = expr
        self.raw = dict(zip(FIELDS, parts))
        self.fields = {name: _parse_field(spec, *RANGES[name], name) for name, spec in self.raw.items()}

    def matches(self, dt):
        if dt.minute not in self.fields["minute"]:
            return False
        if dt.hour not in self.fields["hour"]:
            return False
        if dt.month not in self.fields["month"]:
            return False
        dom_wild, dow_wild = self.raw["dom"] == "*", self.raw["dow"] == "*"
        cron_dow = (dt.weekday() + 1) % 7          # cron: Sunday=0..Saturday=6; Python: Monday=0
        dom_ok, dow_ok = dt.day in self.fields["dom"], cron_dow in self.fields["dow"]
        if dom_wild and dow_wild:
            return True
        if dom_wild:
            return dow_ok
        if dow_wild:
            return dom_ok
        return dom_ok or dow_ok                    # cron's well-known quirk: both restricted -> OR

    def next_after(self, start):
        candidate = (start + timedelta(minutes=1)).replace(second=0, microsecond=0)
        for _ in range(MAX_STEPS):
            if self.matches(candidate):
                return candidate
            candidate += timedelta(minutes=1)
        raise CronError(f"{self.expr!r} does not match any time in the next {MAX_STEPS} minutes")


def parse(expr):
    return CronSpec(expr)


def next_after(expr, start=None):
    return CronSpec(expr).next_after(start or datetime.now())

"""The animated frame for work in progress, plus human-readable durations."""
from __future__ import annotations

import time


class Spinner:
    """Frame selection is derived from the clock, not from a counter, so the animation stays smooth no matter how
    often (or unevenly) the caller happens to redraw."""

    def __init__(self, frames, interval=0.08):
        self.frames, self.interval = frames, interval
        self.started = time.monotonic()

    @classmethod
    def for_console(cls, console, interval=0.08):
        return cls(console.glyph.spinner, interval)

    def frame(self, now=None):
        now = time.monotonic() if now is None else now
        step = int((now - self.started) / self.interval)
        return self.frames[step % len(self.frames)]

    @property
    def elapsed(self):
        return time.monotonic() - self.started


def duration(seconds):
    """Compact, aligned-friendly duration: `0.42s`, `12.3s`, `4m08s`, `1h02m`."""
    if seconds is None:
        return "-"
    seconds = float(seconds)
    if seconds < 10:
        return f"{seconds:.2f}s"
    if seconds < 60:
        return f"{seconds:.1f}s"
    if seconds < 3600:
        return f"{int(seconds // 60)}m{int(seconds % 60):02d}s"
    return f"{int(seconds // 3600)}h{int((seconds % 3600) // 60):02d}m"


def size_bytes(count):
    """Byte counts a person can read at a glance."""
    if count is None:
        return "-"
    value = float(count)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:,.0f} {unit}" if unit == "B" else f"{value:,.1f} {unit}"
        value /= 1024
    return f"{value:,.1f} GB"

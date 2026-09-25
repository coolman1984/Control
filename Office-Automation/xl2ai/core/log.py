"""Console logger shared by every stage.

When a live view is driving the screen it owns the terminal, so the legacy line logger steps aside rather than
interleaving with it. Nothing is lost: the structured events the views render are also what the journal records.
"""
from __future__ import annotations

import time

_silent = False


def silence(value=True):
    """Stop (or resume) direct log printing. Returns the previous setting so callers can restore it."""
    global _silent
    previous, _silent = _silent, bool(value)
    return previous


def is_silent():
    return _silent


def log(level, msg):
    if _silent:
        return
    print(f"[{time.strftime('%H:%M:%S')}] {level:<5} {msg}", flush=True)

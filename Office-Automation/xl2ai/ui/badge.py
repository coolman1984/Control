"""Status badges: a fixed-width, colour-coded token so a column of statuses scans vertically in one glance."""
from __future__ import annotations

from . import style as st
from .measure import pad

# meaning -> (glyph name, semantic colour, short label)
KINDS = {
    "passed": ("ok", "ok", "passed"),
    "ok": ("ok", "ok", "ok"),
    "extracted": ("ok", "ok", "extracted"),
    "pass": ("ok", "ok", "pass"),
    "running": ("running", "running", "running"),
    "pending": ("pending", "faint", "pending"),
    "queued": ("pending", "faint", "queued"),
    "partial": ("warn", "warn", "partial"),
    "warn": ("warn", "warn", "warn"),
    "skipped": ("skip", "skip", "skipped"),
    "reused": ("diamond", "reused", "reused"),
    "failed": ("fail", "error", "failed"),
    "error": ("fail", "error", "error"),
    "fail": ("fail", "error", "fail"),
    "aborted": ("fail", "error", "aborted"),
    "interrupted": ("warn", "warn", "interrupted"),
    "info": ("info", "info", "info"),
}


def resolve(kind):
    return KINDS.get(str(kind).lower(), ("bullet", "muted", str(kind)))


def badge(console, kind, label=None, size=None):
    """`<glyph> <label>`, coloured by meaning and optionally padded to a fixed width for table columns."""
    glyph_name, colour, default_label = resolve(kind)
    text = f"{console.glyph(glyph_name)} {label if label is not None else default_label}"
    if size:
        text = pad(text, size)
    return console.paint(text, colour)


def mark(console, kind):
    """Just the coloured glyph, for dense lists where the word would be noise."""
    glyph_name, colour, _ = resolve(kind)
    return console.paint(console.glyph(glyph_name), colour)


def count_badge(console, count, kind, singular, plural=None):
    """`3 warnings`, coloured by severity and silent at zero (returns an empty string)."""
    if not count:
        return ""
    word = singular if count == 1 else (plural or singular + "s")
    _, colour, _ = resolve(kind)
    return console.paint(f"{count:,} {word}", colour)


def pill(console, text, kind="info"):
    """A reverse-video token for the one thing on screen that must be noticed first."""
    _, colour, _ = resolve(kind)
    return st.of(colour, bold=True, reverse=True)(f" {text} ", console.caps)

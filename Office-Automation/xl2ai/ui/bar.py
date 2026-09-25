"""Progress bars and proportion meters."""
from __future__ import annotations

from . import style as st
from .measure import width


def bar(console, done, total, size=24, filled_style=st.ACCENT, empty_style=st.FAINT):
    """A determinate progress bar. `total` of 0 renders as empty rather than dividing by zero."""
    ratio = 0.0 if not total else max(0.0, min(1.0, done / total))
    filled = int(round(ratio * size))
    g = console.glyph
    head = g("bar_full") * filled
    tail = g("bar_empty") * (size - filled)
    return console.styled(head, filled_style) + console.styled(tail, empty_style)


def percent(done, total):
    return "  0%" if not total else f"{int(round(100 * max(0.0, min(1.0, done / total)))):>3}%"


def segmented(console, parts, size=32):
    """A single bar split by category: `parts` is [(count, semantic_name), ...].

    Used to show at a glance how a run's work actually divided up (extracted / reused / skipped / failed) without
    making the reader compare numbers in their head.
    """
    total = sum(max(0, count) for count, _ in parts)
    if not total:
        return console.styled(console.glyph("bar_empty") * size, st.FAINT)
    out, used = [], 0
    for index, (count, name) in enumerate(parts):
        if count <= 0:
            continue
        span = size - used if index == len(parts) - 1 else max(1, int(round(size * count / total)))
        span = max(0, min(span, size - used))
        out.append(console.paint(console.glyph("bar_full") * span, name))
        used += span
    if used < size:
        out.append(console.styled(console.glyph("bar_empty") * (size - used), st.FAINT))
    return "".join(out)


def legend(console, parts, separator="   "):
    """The key for a segmented bar: a coloured dot, the label and the count."""
    items = []
    for count, name in parts:
        if count <= 0:
            continue
        items.append(console.paint(console.glyph("bullet"), name) + " "
                     + console.styled(f"{name} {count:,}", st.MUTED))
    return separator.join(items)


def sparkline(console, values, size=None):
    """A tiny inline trend. Falls back to a flat rule when the terminal has no block characters."""
    values = [float(v) for v in values if v is not None]
    if not values:
        return ""
    if not console.caps.unicode:
        return console.styled(console.glyph("bar_empty") * min(len(values), size or len(values)), st.FAINT)
    blocks = "▁▂▃▄▅▆▇█"
    if size and len(values) > size:                       # keep the most recent points when space is short
        values = values[-size:]
    low, high = min(values), max(values)
    span = (high - low) or 1.0
    return console.styled(
        "".join(blocks[min(len(blocks) - 1, int((v - low) / span * (len(blocks) - 1)))] for v in values),
        st.ACCENT)

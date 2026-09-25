"""How wide is this text, really.

Column alignment is the difference between a UI that looks built and one that looks broken, and `len()` is wrong
for combining marks, CJK, emoji and any string that already carries colour escapes. Everything that pads,
truncates or wraps goes through here.
"""
from __future__ import annotations

import re
import unicodedata

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")

# Ranges a terminal renders double-width. Deliberately narrow: East Asian "ambiguous" characters (which include
# the arrows and check marks used by the glyph set) stay one column, which is how terminals actually draw them.
_WIDE_RANGES = (
    (0x1100, 0x115F), (0x2E80, 0x303E), (0x3041, 0x33FF), (0x3400, 0x4DBF), (0x4E00, 0x9FFF),
    (0xA000, 0xA4CF), (0xAC00, 0xD7A3), (0xF900, 0xFAFF), (0xFE30, 0xFE6F), (0xFF00, 0xFF60),
    (0xFFE0, 0xFFE6), (0x1F300, 0x1F64F), (0x1F680, 0x1F6FF), (0x1F900, 0x1F9FF), (0x1FA70, 0x1FAFF),
    (0x20000, 0x2FFFD), (0x30000, 0x3FFFD),
)


def strip_ansi(text):
    return ANSI_RE.sub("", str(text))


def char_width(ch):
    code = ord(ch)
    if code == 0 or unicodedata.combining(ch):
        return 0
    if code < 32 or 0x7F <= code < 0xA0:                  # control characters occupy nothing on screen
        return 0
    if code == 0xFE0F:                                    # emoji variation selector: zero width on its own
        return 0
    for low, high in _WIDE_RANGES:
        if low <= code <= high:
            return 2
    return 1


def width(text):
    """Display columns of `text`, ignoring any colour escapes it already contains."""
    return sum(char_width(ch) for ch in strip_ansi(text))


def truncate(text, limit, marker="…"):
    """Cut to `limit` columns, ending with `marker` when something was removed. Never returns more than `limit`."""
    text = str(text)
    if limit <= 0:
        return ""
    if width(text) <= limit:
        return text
    marker_width = width(marker)
    if limit <= marker_width:
        return marker[:limit] if marker_width <= limit else ""
    budget, out = limit - marker_width, []
    used = 0
    for ch in text:
        w = char_width(ch)
        if used + w > budget:
            break
        out.append(ch)
        used += w
    return "".join(out) + marker


def pad(text, target, align="left", fill=" "):
    """Pad to exactly `target` columns (truncating first if the text is already too wide)."""
    text = truncate(text, target)
    missing = max(0, target - width(text))
    if align == "right":
        return fill * missing + text
    if align == "center":
        left = missing // 2
        return fill * left + text + fill * (missing - left)
    return text + fill * missing


def wrap(text, limit):
    """Word-wrap into lines of at most `limit` columns. A word longer than a line is hard-split, never dropped."""
    if limit <= 0:
        return [""]
    lines, current, used = [], [], 0
    for word in str(text).split():
        w = width(word)
        if w > limit:                                     # a path or hash with no spaces: break it across lines
            if current:
                lines.append(" ".join(current))
                current, used = [], 0
            remaining = word
            while width(remaining) > limit:
                head = truncate(remaining, limit, marker="")
                lines.append(head)
                remaining = remaining[len(head):]
            if remaining:
                current, used = [remaining], width(remaining)
            continue
        if current and used + 1 + w > limit:
            lines.append(" ".join(current))
            current, used = [word], w
        else:
            current.append(word)
            used = used + 1 + w if used else w
    if current:
        lines.append(" ".join(current))
    return lines or [""]

"""Symbols, in a unicode set and a plain ASCII set.

Every glyph here is deliberately one column wide (no emoji, no variation selectors), so box drawing and column
alignment stay exact in every terminal and font. Views ask for a glyph by meaning and get whichever set fits.
"""
from __future__ import annotations

UNICODE = {
    # status
    "ok": "✔",              # heavy check
    "fail": "✘",            # heavy ballot x
    "warn": "⚠",            # warning sign
    "info": "ℹ",            # information
    "skip": "─",            # dash
    "pending": "○",         # hollow circle
    "running": "●",         # filled circle
    "bullet": "•",
    "arrow": "→",
    "chevron": "›",
    "pointer": "▸",
    "diamond": "◆",
    "dot": "·",
    # box drawing (rounded, thin)
    "tl": "╭", "tr": "╮", "bl": "╰", "br": "╯",
    "h": "─", "v": "│",
    "tee_down": "┬", "tee_up": "┴", "tee_right": "├", "tee_left": "┤", "cross": "┼",
    # tree
    "branch": "├─", "last": "╰─", "trunk": "│ ", "gap": "  ",
    # meters
    "bar_full": "━", "bar_empty": "─", "bar_cap": "╸",
    "block": "█", "half": "▄", "shade": "░",
}

ASCII = {
    "ok": "+", "fail": "x", "warn": "!", "info": "i", "skip": "-",
    "pending": ".", "running": "*", "bullet": "*", "arrow": "->", "chevron": ">",
    "pointer": ">", "diamond": "*", "dot": ".",
    "tl": "+", "tr": "+", "bl": "+", "br": "+",
    "h": "-", "v": "|",
    "tee_down": "+", "tee_up": "+", "tee_right": "+", "tee_left": "+", "cross": "+",
    "branch": "|-", "last": "`-", "trunk": "| ", "gap": "  ",
    "bar_full": "=", "bar_empty": "-", "bar_cap": "=",
    "block": "#", "half": "=", "shade": ".",
}

# Braille spinner: smooth, one column wide, and readable even at low refresh rates.
SPINNER_UNICODE = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
SPINNER_ASCII = "|/-\\"


class Glyphs:
    """A resolved glyph set. `g("ok")` is the only call sites need."""

    __slots__ = ("_table", "spinner", "unicode")

    def __init__(self, unicode_ok):
        self.unicode = bool(unicode_ok)
        self._table = UNICODE if unicode_ok else ASCII
        self.spinner = SPINNER_UNICODE if unicode_ok else SPINNER_ASCII

    def __call__(self, name):
        return self._table.get(name, "")

    @classmethod
    def for_caps(cls, caps):
        return cls(caps.unicode)

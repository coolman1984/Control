"""The colour system: one definition per colour, rendered at whatever depth the terminal supports.

Colours are defined once as RGB and carry their own 256-colour and 16-colour fallbacks, so a colour never has to
be chosen twice. Names are semantic (`DANGER`), not literal (`RED`): the meaning is what callers depend on.
"""
from __future__ import annotations

from .caps import COLOR16, COLOR256, TRUECOLOR


class Color:
    """One colour with an explicit fallback at every supported depth."""

    __slots__ = ("rgb", "c256", "c16", "name")

    def __init__(self, rgb, c256, c16, name=""):
        self.rgb, self.c256, self.c16, self.name = rgb, c256, c16, name

    def sgr(self, depth, background=False):
        """The SGR parameter list for this colour at `depth`, or None when it cannot be shown."""
        if depth >= TRUECOLOR:
            r, g, b = self.rgb
            return f"{48 if background else 38};2;{r};{g};{b}"
        if depth >= COLOR256:
            return f"{48 if background else 38};5;{self.c256}"
        if depth >= COLOR16:
            base = 40 if background else 30
            code, bright = self.c16 % 8, self.c16 >= 8
            return str(base + code + (60 if bright else 0))
        return None

    def __repr__(self):
        return f"Color({self.name or self.rgb})"


# ---- the base ramp -------------------------------------------------------------------------------------------
# Tuned for readability on both dark and light terminals: nothing relies on a specific background colour.
TEXT = Color((205, 214, 229), 252, 7, "text")
MUTED = Color((126, 138, 158), 245, 8, "muted")
FAINT = Color((92, 102, 120), 240, 8, "faint")
ACCENT = Color((122, 162, 247), 111, 12, "accent")
ACCENT_DIM = Color((86, 116, 180), 67, 4, "accent-dim")
SUCCESS = Color((74, 222, 128), 78, 10, "success")
WARNING = Color((251, 191, 36), 214, 11, "warning")
DANGER = Color((248, 113, 113), 203, 9, "danger")
INFO = Color((129, 140, 248), 105, 12, "info")
SPECIAL = Color((192, 132, 252), 141, 13, "special")
CYAN = Color((34, 211, 238), 80, 14, "cyan")

# ---- semantic aliases, so views never name a colour by look -------------------------------------------------
SEMANTIC = {
    "text": TEXT,
    "muted": MUTED,
    "faint": FAINT,
    "accent": ACCENT,
    "accent-dim": ACCENT_DIM,
    "ok": SUCCESS,
    "pass": SUCCESS,
    "warn": WARNING,
    "error": DANGER,
    "fail": DANGER,
    "info": INFO,
    "special": SPECIAL,
    "running": CYAN,
    "skip": FAINT,
    "reused": SPECIAL,
}


def semantic(name):
    """Look a colour up by meaning; unknown names fall back to ordinary text rather than raising."""
    return SEMANTIC.get(name, TEXT)

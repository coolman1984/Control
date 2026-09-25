"""Horizontal rules and section headings: the quiet structure that makes a long run readable."""
from __future__ import annotations

from . import style as st
from .measure import width


def rule(console, char=None, style=st.BORDER, indent=0):
    """A full-width divider line."""
    char = char or console.glyph("h")
    span = max(0, console.width - indent)
    return " " * indent + console.styled(char * span, style)


def heading(console, text, subtitle="", style=st.HEADING, indent=0):
    """A titled divider: the title, then a rule filling the rest of the line, then an optional right-hand note.

        -- Extract ------------------------------------------------- 3 sources
    """
    glyph_h = console.glyph("h")
    title = console.styled(text, style)
    right = console.styled(subtitle, st.FAINT) if subtitle else ""
    # The line is: indent + "--" + " " + text + " " + fill + [" " + subtitle]
    fixed = indent + 2 + 1 + width(text) + 1 + ((1 + width(subtitle)) if subtitle else 0)
    fill = max(1, console.width - fixed)
    pieces = [" " * indent, console.styled(glyph_h * 2 + " ", st.BORDER), title, " ",
              console.styled(glyph_h * fill, st.BORDER)]
    if right:
        pieces += [" ", right]
    return "".join(pieces)


def kv(console, label, value, label_width=18, label_style=st.LABEL, value_style=st.VALUE, indent=2):
    """One aligned `label   value` line, the workhorse of every summary block."""
    from .measure import pad
    text = pad(str(label), label_width)
    return " " * indent + console.styled(text, label_style) + " " + console.styled(str(value), value_style)

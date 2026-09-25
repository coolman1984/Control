"""Bordered panels: a rounded box with an inline title, used to frame anything that must not be skimmed past."""
from __future__ import annotations

from . import style as st
from .measure import pad, width, wrap


def panel(console, body, title="", title_style=st.TITLE, border_style=st.BORDER, indent=0, pad_x=1, inner=None):
    """Render `body` (a string or list of pre-styled lines) inside a box. Returns a list of lines.

    `inner` caps the content width; by default the panel fills the console. Lines are expected to be already
    styled, so their display width is measured with escapes stripped.
    """
    g = console.glyph
    outer = max(20, min(console.width - indent, console.width))
    inner_width = (inner if inner is not None else outer - 2 - pad_x * 2)
    lines = body.splitlines() if isinstance(body, str) else list(body)

    top_fill = inner_width + pad_x * 2
    if title:
        label = f" {title} "
        fill = max(0, top_fill - width(label))
        top = (console.styled(g("tl") + g("h"), border_style) + console.styled(label, title_style)
               + console.styled(g("h") * max(0, fill - 1) + g("tr"), border_style))
    else:
        top = console.styled(g("tl") + g("h") * top_fill + g("tr"), border_style)
    out = [" " * indent + top]

    bar = console.styled(g("v"), border_style)
    for line in lines:
        out.append(" " * indent + bar + " " * pad_x + pad(line, inner_width) + " " * pad_x + bar)
    out.append(" " * indent + console.styled(g("bl") + g("h") * top_fill + g("br"), border_style))
    return out


def callout(console, text, kind="info", title=None, indent=0):
    """A short, wrapped, colour-coded panel for a warning or an error that deserves to stop the eye."""
    glyph = {"info": "info", "warn": "warn", "error": "fail", "ok": "ok"}.get(kind, "info")
    tone = {"info": st.INFO, "warn": st.WARN, "error": st.ERROR, "ok": st.OK}.get(kind, st.INFO)
    heading = f"{console.glyph(glyph)} {title}" if title else console.glyph(glyph)
    body_width = max(20, console.width - indent - 4)
    body = [console.styled(line, st.VALUE) for line in wrap(text, body_width)]
    return panel(console, body, title=heading, title_style=tone, border_style=tone, indent=indent)

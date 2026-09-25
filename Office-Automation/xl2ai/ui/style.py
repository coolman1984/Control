"""Styled text: a colour plus attributes, applied only when the terminal can show it.

`Style` is immutable and composable, so a view can describe intent once ("dim, accent") and reuse it. Applying a
style on a no-colour terminal returns the text unchanged, which keeps every caller free of `if colour:` branches.
"""
from __future__ import annotations

from . import palette

RESET = "\x1b[0m"


class Style:
    __slots__ = ("fg", "bg", "bold", "dim", "italic", "underline", "reverse")

    def __init__(self, fg=None, bg=None, bold=False, dim=False, italic=False, underline=False, reverse=False):
        self.fg, self.bg = fg, bg
        self.bold, self.dim, self.italic = bold, dim, italic
        self.underline, self.reverse = underline, reverse

    def merged(self, **changes):
        current = {name: getattr(self, name) for name in self.__slots__}
        current.update(changes)
        return Style(**current)

    def codes(self, depth):
        parts = []
        if self.bold:
            parts.append("1")
        if self.dim:
            parts.append("2")
        if self.italic:
            parts.append("3")
        if self.underline:
            parts.append("4")
        if self.reverse:
            parts.append("7")
        if self.fg is not None:
            code = self.fg.sgr(depth)
            if code:
                parts.append(code)
        if self.bg is not None:
            code = self.bg.sgr(depth, background=True)
            if code:
                parts.append(code)
        return parts

    def __call__(self, text, caps):
        """Render `text` in this style for `caps`. Plain text out when colour is unavailable."""
        text = str(text)
        if not caps.colored or not text:
            return text
        parts = self.codes(caps.color)
        if not parts:
            return text
        return f"\x1b[{';'.join(parts)}m{text}{RESET}"


def of(name, **attrs):
    """Build a style from a semantic colour name: `of('warn', bold=True)`."""
    return Style(fg=palette.semantic(name), **attrs)


# Ready-made styles for the shapes every view needs. Views should prefer these over building their own.
PLAIN = Style()
TITLE = Style(fg=palette.TEXT, bold=True)
HEADING = Style(fg=palette.ACCENT, bold=True)
LABEL = Style(fg=palette.MUTED)
VALUE = Style(fg=palette.TEXT)
MUTED = Style(fg=palette.MUTED)
FAINT = Style(fg=palette.FAINT)
BORDER = Style(fg=palette.FAINT)
OK = Style(fg=palette.SUCCESS)
WARN = Style(fg=palette.WARNING)
ERROR = Style(fg=palette.DANGER)
INFO = Style(fg=palette.INFO)
SPECIAL = Style(fg=palette.SPECIAL)
RUNNING = Style(fg=palette.CYAN)
ACCENT = Style(fg=palette.ACCENT)

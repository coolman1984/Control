"""What this terminal can actually do.

Every other module in `xl2ai.ui` asks this first and degrades honestly: full colour on a modern terminal, 256
colours on an older one, plain ASCII in a pipe, a log file or CI. Nothing here ever raises; a wrong guess must
downgrade the output, never break the program.
"""
from __future__ import annotations

import os
import shutil
import sys

TRUECOLOR, COLOR256, COLOR16, NOCOLOR = 24, 8, 4, 0

# Terminals that answer honestly about truecolour support through TERM_PROGRAM rather than COLORTERM.
_TRUECOLOR_PROGRAMS = {"iterm.app", "vscode", "wezterm", "ghostty", "hyper", "warpterminal", "tabby"}


def _env_flag(name):
    """None when unset, else True/False. '0', 'false', 'no' and '' all mean off."""
    raw = os.environ.get(name)
    if raw is None:
        return None
    return raw.strip().lower() not in ("", "0", "false", "no", "off")


def _detect_color(stream, env):
    if _env_flag("NO_COLOR") is not None:                 # the NO_COLOR convention: presence alone disables
        return NOCOLOR
    forced = env.get("FORCE_COLOR")
    if forced is not None:
        mapping = {"0": NOCOLOR, "1": COLOR16, "2": COLOR256, "3": TRUECOLOR}
        return mapping.get(forced.strip(), TRUECOLOR)
    term = env.get("TERM", "").lower()
    if term == "dumb":
        return NOCOLOR
    if not _is_tty(stream):
        return NOCOLOR
    if "truecolor" in env.get("COLORTERM", "").lower() or "24bit" in env.get("COLORTERM", "").lower():
        return TRUECOLOR
    if env.get("TERM_PROGRAM", "").lower().replace(" ", "") in _TRUECOLOR_PROGRAMS:
        return TRUECOLOR
    if env.get("WT_SESSION"):                             # Windows Terminal
        return TRUECOLOR
    if "256" in term:
        return COLOR256
    if term:
        return COLOR16
    return NOCOLOR


def _is_tty(stream):
    try:
        return bool(stream.isatty())
    except Exception:                                     # a wrapped/closed stream must not crash rendering
        return False


def _detect_unicode(stream, env):
    if _env_flag("XL2AI_ASCII"):
        return False
    encoding = (getattr(stream, "encoding", "") or "").lower().replace("-", "")
    if encoding.startswith("utf"):
        return True
    return "utf" in env.get("LANG", "").lower().replace("-", "")


def _detect_size(fallback=(100, 30)):
    try:                                                  # get_terminal_size already honours COLUMNS/LINES
        size = shutil.get_terminal_size(fallback)
        return max(40, size.columns), max(10, size.lines)
    except Exception:
        return fallback


class Caps:
    """An immutable snapshot of terminal capability. Build one per command, pass it down."""

    __slots__ = ("tty", "color", "unicode", "width", "height", "interactive")

    def __init__(self, tty, color, unicode_ok, width, height, interactive):
        self.tty, self.color, self.unicode = tty, color, unicode_ok
        self.width, self.height, self.interactive = width, height, interactive

    @classmethod
    def detect(cls, stream=None, env=None):
        stream = stream if stream is not None else sys.stdout
        env = env if env is not None else os.environ
        tty = _is_tty(stream)
        width, height = _detect_size()
        # CI is a TTY often enough, but a redrawing live region there produces unreadable logs.
        in_ci = _env_flag("CI") is True or bool(env.get("GITHUB_ACTIONS"))
        return cls(tty=tty, color=_detect_color(stream, env), unicode_ok=_detect_unicode(stream, env),
                   width=width, height=height, interactive=tty and not in_ci)

    @classmethod
    def plain(cls, width=100):
        """No colour, no unicode, no redraw: the shape used for files, pipes and tests."""
        return cls(tty=False, color=NOCOLOR, unicode_ok=False, width=width, height=30, interactive=False)

    @property
    def colored(self):
        return self.color > NOCOLOR

    def narrowed(self, width):
        return Caps(self.tty, self.color, self.unicode, max(20, width), self.height, self.interactive)

    def __repr__(self):
        return (f"Caps(tty={self.tty}, color={self.color}, unicode={self.unicode}, "
                f"width={self.width}, interactive={self.interactive})")

"""Risky actions need the person's yes, asked on their own desktop by Control itself."""
import json
import os
import sys
import unicodedata

from . import config
from .journal import mask

APPROVAL_TITLE = "Control — approve?"
YES, NO, TIMEOUT, UNAVAILABLE, OFF = "yes", "no", "timeout", "unavailable", "off"

_MB_YESNO, _MB_ICONQUESTION, _MB_DEFBUTTON2 = 0x4, 0x20, 0x100
_MB_SYSTEMMODAL, _MB_SETFOREGROUND, _MB_TOPMOST = 0x1000, 0x10000, 0x40000


def message_box(text, title, timeout_s):
    if sys.platform != "win32":
        return UNAVAILABLE
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    fn = user32.MessageBoxTimeoutW          # exported by user32 since XP; not in the headers
    fn.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT,
                   wintypes.WORD, wintypes.DWORD]
    fn.restype = ctypes.c_int
    flags = (_MB_YESNO | _MB_ICONQUESTION | _MB_DEFBUTTON2 | _MB_SYSTEMMODAL
             | _MB_SETFOREGROUND | _MB_TOPMOST)
    result = fn(None, text, title, flags, 0, int(timeout_s * 1000))
    if result == 0:
        return UNAVAILABLE                  # no desktop to show it on
    return {6: YES, 7: NO, 32000: TIMEOUT}.get(result, NO)


def _escape_format_chars(s):
    """Unicode format characters (category Cf, e.g. U+202E right-to-left override, U+200F right-
    to-left mark) are invisible but can make shown text read backwards or hide part of it; show
    their escape instead of the character itself."""
    return "".join(f"\\u{ord(c):04x}" if unicodedata.category(c) == "Cf" else c for c in s)


def describe(action_name, args, reason):
    shown = json.dumps(mask(args), ensure_ascii=False, indent=1, default=str)
    warning = ""
    if len(shown) > 1500:
        hidden = len(shown) - 1400
        shown = shown[:1000] + f"\n… [{hidden} characters hidden] …\n" + shown[-400:]
        warning = "\nWARNING: the arguments are very long — check the end carefully.\n"
    shown = _escape_format_chars(shown)
    return (f"An AI agent wants to run:\n\n{action_name}\n\n{shown}\n{warning}\n"
            f"Why this needs you: {reason}\n\nAllow it?")


def approve(action_name, args, reason, *, box=message_box, timeout_s=None):
    if os.environ.get("CONTROL_APPROVAL") == "off":
        return OFF
    if timeout_s is None:
        timeout_s = config.load()["approval_timeout"]
    return box(describe(action_name, args, reason), APPROVAL_TITLE, timeout_s)


def targets_approval_box(action_name, args):
    if not action_name.startswith("win."):
        return False
    return any(isinstance(v, str) and "approve?" in v and "Control" in v for v in args.values())


def approval_box_open(finder=None):
    """True if Control's own approval box is currently on screen, from any session."""
    if finder is None:
        if sys.platform != "win32":
            return False
        import ctypes
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        finder = user32.FindWindowW
    return bool(finder("#32770", APPROVAL_TITLE))

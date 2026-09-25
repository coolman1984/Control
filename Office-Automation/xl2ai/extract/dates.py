"""Excel date/time handling: NumberFormat classification and serial -> ISO conversion."""
from __future__ import annotations

import datetime as dt
import re

from .names import fmt_num

def classify_format(fmt):
    """'date' | 'datetime' | 'time' | None from an Excel NumberFormat string."""
    if not isinstance(fmt, str) or not fmt:
        return None
    s = fmt.split(";")[0]
    s = re.sub(r'"[^"]*"|\\.|_.|\*.|\[(?![hms]+\])[^\]]*\]', "", s).lower()
    if "general" in s or "e+" in s or "e-" in s:
        return None
    has_date = re.search(r"[yd]|m{3,}", s) is not None
    has_time = re.search(r"[hs]|am/pm", s) is not None
    if has_date:
        return "datetime" if has_time else "date"
    return "time" if has_time else None


def serial_to_iso(v, kind, is1904):
    if kind == "time":
        total = int(round(v * 86400))
        return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"
    if not (0 <= v < 2958466):
        return fmt_num(v)
    if not is1904 and v < 61:                          # before Excel's fictitious 1900-02-29 the epoch shifts a day
        if v < 1:
            return serial_to_iso(v, "time", is1904) if kind == "datetime" else "1900-01-00"
        if v >= 60:
            return "1900-02-29"                        # displayed by Excel, never a real date
        epoch = dt.datetime(1899, 12, 31)
    else:
        epoch = dt.datetime(1904, 1, 1) if is1904 else dt.datetime(1899, 12, 30)
    days = int(v)
    secs = int(round((v - days) * 86400))
    d = epoch + dt.timedelta(days=days, seconds=secs if kind == "datetime" else 0)
    return d.strftime("%Y-%m-%d %H:%M:%S") if kind == "datetime" else d.strftime("%Y-%m-%d")


def datetime_to_iso(d):
    if d.year <= 1899 and d.month == 12 and d.day == 30:          # time-only cell returned by .Value
        return d.strftime("%H:%M:%S")
    return d.strftime("%Y-%m-%d %H:%M:%S") if (d.hour or d.minute or d.second) else d.strftime("%Y-%m-%d")

"""Identifier handling: SQL quoting, column letters, table/column name sanitising."""
from __future__ import annotations

import re

from .common import ERROR_TEXT, ERR_HI, ERR_LO

def q(ident):
    return '"' + ident.replace('"', '""') + '"'


def col_letter(n):
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def sanitize_table(name, used):
    t = re.sub(r"\W+", "_", str(name).strip(), flags=re.UNICODE).strip("_") or "sheet"
    if t[0].isdigit() or t.lower().startswith("sqlite_"):
        t = "t_" + t
    base, k = t, 2
    while t.lower() in used:
        t = f"{base}_{k}"
        k += 1
    used.add(t.lower())
    return t


def fmt_num(v):
    if v.is_integer() and abs(v) < 1e15:
        return str(int(v))
    return repr(v)


def clean_header(v):
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    if isinstance(v, int) and ERR_LO <= v <= ERR_HI:
        v = ERROR_TEXT.get(v, "error")
    return re.sub(r"\W+", "_", str(v).strip(), flags=re.UNICODE).strip("_")


def build_columns(headers):
    """(names, generated_count, renamed_duplicate_count). Names are unique case-insensitively."""
    names, seen, gen, dup = [], {"_xl_row"}, 0, 0
    for i, h in enumerate(headers, 1):
        n = clean_header(h)
        if not n:
            n, gen = f"col_{i}", gen + 1
        base, k = n, 2
        if n.lower() in seen:
            dup += 1
        while n.lower() in seen:
            n = f"{base}_{k}"
            k += 1
        seen.add(n.lower())
        names.append(n)
    return names, gen, dup

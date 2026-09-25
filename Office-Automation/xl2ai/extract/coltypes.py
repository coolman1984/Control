"""Column typing: whole-column statistics, type decision and per-block value conversion."""
from __future__ import annotations

from .common import ERROR_TEXT, ERR_HI, ERR_LO, MAX_SAFE_INT, NoneType
from .dates import datetime_to_iso, serial_to_iso
from .names import fmt_num

class ColStat:
    __slots__ = ("has_float", "has_str", "has_bool", "all_int", "nerr")

    def __init__(self):
        self.has_float = self.has_str = self.has_bool = False
        self.all_int = True
        self.nerr = 0

    def update(self, col):
        ts = set(map(type, col))
        ts.discard(NoneType)
        if not ts:
            return
        if int in ts:                       # Value2 returns numbers as float, so an int is an Excel error code
            ints = [v for v in col if v.__class__ is int]
            nerr = sum(1 for v in ints if ERR_LO <= v <= ERR_HI)
            self.nerr += nerr
            if nerr == len(ints):
                ts.discard(int)
        if str in ts:
            self.has_str = True
        if bool in ts:
            self.has_bool = True
        if float in ts or int in ts:
            self.has_float = True
            if self.all_int:
                nums = [v for v in col if v.__class__ is float]
                self.all_int = all(map(float.is_integer, nums)) and (not nums or max(map(abs, nums)) < MAX_SAFE_INT)

    @property
    def has_data(self):
        return self.has_float or self.has_str or self.has_bool

    def kind(self):
        n, s, b = self.has_float, self.has_str, self.has_bool
        if n and not s and not b:
            return "int" if self.all_int else "real"
        if s and not n and not b:
            return "text"
        if b and not n and not s:
            return "bool"
        return "mixed" if (n or s or b) else "text"


class ColPlan:
    __slots__ = ("name", "xl_col", "header", "kind", "sql_type", "date_kind", "date_mask", "stat")

    def __init__(self, name, xl_col, header, stat, strict):
        self.name, self.xl_col, self.header, self.stat = name, xl_col, header, stat
        self.kind, self.date_kind, self.date_mask = stat.kind(), None, None
        self.sql_type = ""
        self.finish(strict)

    def make_date(self, kind, mask, strict):
        self.date_kind, self.date_mask, self.kind = kind, mask, "date"
        self.finish(strict)

    def finish(self, strict):
        self.sql_type = {"int": "INTEGER", "real": "REAL", "text": "TEXT", "bool": "INTEGER", "date": "TEXT",
                         "mixed": "ANY" if strict else "BLOB"}[self.kind]


def convert_column(vals, plan, xl_rows, is1904):
    """One column of one block -> (values, [(index, error_text)]). Fast paths stay in C-level builtins."""
    ts = set(map(type, vals))
    ts.discard(NoneType)
    kind = plan.kind
    if not ts:
        return list(vals), None
    if ts <= {float}:
        if kind in ("real", "mixed"):
            return list(vals), None
        if kind == "int":
            return [None if v is None else int(v) for v in vals], None
    elif ts <= {str} and kind in ("text", "mixed", "date"):
        return list(vals), None
    elif ts <= {bool} and kind == "bool":
        return [None if v is None else int(v) for v in vals], None
    out, errs = [None] * len(vals), []
    mask = plan.date_mask
    for i, v in enumerate(vals):
        if v is None:
            continue
        t = type(v)
        if t is float:
            if kind == "date":
                d = mask.get(xl_rows[i]) if mask is not None else None
                if d is not None:
                    out[i] = datetime_to_iso(d)
                elif mask is None:
                    out[i] = serial_to_iso(v, plan.date_kind, is1904)
                else:
                    out[i] = fmt_num(v)
            elif kind == "int":
                out[i] = int(v)
            else:
                out[i] = v
        elif t is str:
            out[i] = v
        elif t is bool:
            out[i] = int(v) if kind == "bool" else ("TRUE" if v else "FALSE")
        elif t is int:
            if ERR_LO <= v <= ERR_HI:
                errs.append((i, ERROR_TEXT.get(v, "#ERROR")))
            else:
                out[i] = v
        else:
            out[i] = str(v)
    return out, errs


def clean_surrogates(vals):
    return [v.encode("utf-16", "surrogatepass").decode("utf-16", "replace") if isinstance(v, str) else v
            for v in vals]

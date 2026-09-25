"""Formula lineage: which sheet (or other workbook) a computed column pulls its numbers from.

A report sheet whose cells are formulas like `='Raw Data'!D2*1.14` or `=SUMIFS([Sales.xlsx]Jan!C:C, ...)` is
not independent data -- it is a view over another table. Knowing that saves an agent from treating the report and
its source as two unrelated facts, and tells it where to look when a report number needs explaining.

Pure parsing of formula text already captured during extraction (`_formulas` samples, plus `_formula_refs` when
the extraction engine recorded every formula), so it costs no Excel time. Always `inferred`: a column's lineage
comes from the formulas that were captured, which for the Excel engine is one sample per column.
"""
from __future__ import annotations

import hashlib
import os
import re

_STRING = re.compile(r'"(?:[^"]|"")*"')
_QUOTED = re.compile(r"'((?:[^']|'')+)'!")
_BARE = re.compile(r"(?<![\w.\]'])((?:\[[^\]]+\])?[^\s!'(),+\-*/^&=<>;:{}\"\[\]]+(?::[^\s!'(),+\-*/^&=<>;:{}\"\[\]]+)?)!")
_BOOK = re.compile(r"^(?:(.*)\[([^\]]+)\])?(.*)$")


def formula_refs(formula):
    """[(workbook or None, sheet)] referenced by one formula, in first-seen order, duplicates removed.

    Handles `Sheet!A1`, `'My Sheet'!R1C1`, `[Book.xlsx]Sheet!A1`, `'C:\\dir\\[Book.xlsx]Sheet'!A1`, and the
    `[1]Sheet!A1` index form stored inside .xlsx files. Text inside string literals is ignored.
    """
    if not formula:
        return []
    text = _STRING.sub('""', str(formula))
    found = []
    for m in _QUOTED.finditer(text):
        found.append((m.start(), m.group(1).replace("''", "'")))
    stripped = _QUOTED.sub(lambda m: " " * len(m.group(0)), text)
    for m in _BARE.finditer(stripped):
        found.append((m.start(), m.group(1)))
    out, seen = [], set()
    for _, name in sorted(found):
        _, book, sheet = _BOOK.match(name).groups()
        key = ((book or "").strip() or None, sheet.strip())
        if key[1] and key not in seen:
            seen.add(key)
            out.append(key)
    return out


def _norm(s):
    return (s or "").strip().casefold()


def build_lineage(con):
    """Fill `_lineage` in an open catalog connection from `_formulas` (+ `_formula_refs`). Returns row count."""
    tables = con.execute("SELECT table_id, source_id, sheet_name FROM _tables").fetchall()
    by_sheet = {(sid, _norm(sheet)): tid for tid, sid, sheet in tables}
    sources = con.execute("SELECT source_id, path FROM _sources").fetchall()
    by_book = {}
    for sid, path in sources:
        base = os.path.basename(path or "")
        for key in (_norm(base), _norm(os.path.splitext(base)[0]), _norm(sid)):
            if key:
                by_book.setdefault(key, sid)
    src_of = {tid: sid for tid, sid, _ in tables}

    refs, complete = {}, set()
    if con.execute("SELECT 1 FROM sqlite_master WHERE name='_formula_refs'").fetchone():
        for cid, tid, book, sheet, cells, sample in con.execute(
                "SELECT column_id, table_id, ref_workbook, ref_sheet, cells, sample FROM _formula_refs"):
            refs[(cid, tid, book, sheet)] = {"cells": cells, "sample": sample, "method": "all_formulas"}
            complete.add(cid)
    for cid, tid, sample in con.execute("SELECT column_id, table_id, sample_r1c1 FROM _formulas WHERE has_formula=1"):
        if cid in complete:                  # every formula of this column was already read; a sample adds nothing
            continue
        for book, sheet in formula_refs(sample):
            refs.setdefault((cid, tid, book, sheet), {"cells": None, "sample": sample, "method": "formula_sample"})

    rows = []
    for (cid, tid, book, sheet), info in refs.items():
        own = src_of.get(tid)
        if book:
            target_src = by_book.get(_norm(book)) or by_book.get(_norm(os.path.splitext(book)[0]))
            kind = "workbook"
        else:
            target_src, kind = own, "sheet"
        target = by_sheet.get((target_src, _norm(sheet))) if target_src else None
        if target == tid:
            continue                                  # a reference to its own sheet is arithmetic, not lineage
        status = "resolved" if target else ("external" if kind == "workbook" and not target_src else "unresolved")
        lid = hashlib.sha1(f"{cid}|{book}|{sheet}".encode()).hexdigest()[:20]
        rows.append((lid, tid, cid, kind, book, sheet, target_src, target, status, info["cells"],
                     str(info["sample"] or "")[:200], info["method"]))
    con.executemany("INSERT OR REPLACE INTO _lineage VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    return len(rows)


def table_lineage(con, table_id=None):
    """[{table_id, feeds_from: [{table_id|workbook, sheet, status, columns}]}] -- one summary per computed table."""
    sql = ("SELECT l.table_id, l.ref_kind, l.ref_workbook, l.ref_sheet, l.target_table_id, l.status, c.sql_name "
           "FROM _lineage l LEFT JOIN _columns c ON c.column_id = l.column_id")
    args = ()
    if table_id:
        sql += " WHERE l.table_id = ?"
        args = (table_id,)
    out = {}
    for tid, kind, book, sheet, target, status, col in con.execute(sql + " ORDER BY l.table_id, c.position", args):
        key = (kind, book, sheet, target, status)
        t = out.setdefault(tid, {})
        t.setdefault(key, []).append(col)
    return [{"table_id": tid,
             "feeds_from": [{"kind": k[0], "workbook": k[1], "sheet": k[2], "table_id": k[3], "status": k[4],
                             "columns": cols} for k, cols in feeds.items()]}
            for tid, feeds in out.items()]

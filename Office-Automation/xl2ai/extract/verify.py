"""Independent verification: ask Excel (COUNTA/AGGREGATE) and compare with SQLite."""
from __future__ import annotations

import math

from .com import ExcelDied, com_msg
from .names import col_letter, q

def quote_sheet(name):
    return "'" + name.replace("'", "''") + "'"


def verify_table(sess, con, res):
    """Ask Excel itself (COUNTA / AGGREGATE) and compare with SQLite. Returns (#checks, #mismatches)."""
    rows = []
    checks = bad = 0
    stored_cells = res.header_cells + res.preamble_cells
    sheet = quote_sheet(res.sheet_name)
    for p in res.plans:
        L = col_letter(p.xl_col)
        addr = f"{sheet}!${L}${res.data_first}:${L}${res.last_row}"
        tcol = f"{q(res.table_name)}"
        n_sql = con.execute(f"SELECT COUNT({q(p.name)}) FROM {tcol}").fetchone()[0] + p.stat.nerr
        try:
            n_xl = sess.robust(lambda: sess.app.Evaluate(f"COUNTA({addr})"))
        except ExcelDied:
            raise
        except Exception as e:
            n_xl = None
            rows.append((res.table_name, p.name, "counta", None, n_sql, None, f"Excel Evaluate failed: {com_msg(e)}"))
        stored_cells += n_sql
        if n_xl is not None:
            ok = int(isinstance(n_xl, float) and n_xl == n_sql)
            rows.append((res.table_name, p.name, "counta", n_xl, n_sql, ok, ""))
            checks += 1
            bad += 1 - ok
        if p.kind in ("int", "real", "mixed"):
            s_sql, a_sql = con.execute(
                f"SELECT TOTAL(CASE WHEN typeof({q(p.name)}) IN ('integer','real') THEN {q(p.name)} END), "
                f"TOTAL(CASE WHEN typeof({q(p.name)}) IN ('integer','real') THEN abs({q(p.name)}) END) "
                f"FROM {tcol}").fetchone()
            try:
                s_xl = sess.robust(lambda: sess.app.Evaluate(f"AGGREGATE(9,6,{addr})"))
            except ExcelDied:
                raise
            except Exception:
                s_xl = None
            if isinstance(s_xl, float):
                ok = int(math.isclose(s_xl, s_sql, rel_tol=1e-9, abs_tol=1e-9 * max(1.0, a_sql)))
                rows.append((res.table_name, p.name, "sum", s_xl, s_sql, ok, ""))
                checks += 1
                bad += 1 - ok
            else:
                rows.append((res.table_name, p.name, "sum", None, s_sql, None, f"Excel returned {s_xl!r}"))
    # Independent completeness check: does every non-empty cell of the WHOLE sheet (not just the extent we chose,
    # which is what the per-column checks measure) exist in the database? Catches truncated extents.
    try:
        nrows = sess.robust(lambda: sess.sheet(res.sheet_index).Rows.Count)
        total = sess.robust(lambda: sess.app.Evaluate(f"COUNTA({sheet}!$1:${nrows})"))
        ok = int(isinstance(total, float) and total == stored_cells)
        rows.append((res.table_name, "(whole sheet)", "cells_total", total if isinstance(total, float) else None,
                     stored_cells, ok, "" if ok else "sheet has cells that are not in the database"))
        checks += 1
        bad += 1 - ok
    except ExcelDied:
        raise
    except Exception as e:
        rows.append((res.table_name, "(whole sheet)", "cells_total", None, stored_cells, None, com_msg(e)))
    con.executemany("INSERT INTO _verification VALUES (?,?,?,?,?,?,?)", rows)
    return checks, bad

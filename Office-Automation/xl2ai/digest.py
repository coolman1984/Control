"""Key numbers: the first summaries any analyst (or agent) computes on a new table, computed once, in advance.

For every plain data table: totals of its amount columns, the biggest groups by its category columns, and its
month-by-month trend. An agent that would otherwise spend its first ten tool calls writing exactly these GROUP BY
queries reads them from the brief instead -- and every number carries the SQL that produced it, so it can be
re-run and checked.

Rules that keep the numbers honest:
  * only `data` tables with a single region are summarised; reports, dashboards and multi-table sheets are listed
    as skipped with the reason (their rows are not one kind of thing, so a total would be wrong);
  * totals/subtotal rows flagged by `analyze` are excluded, and the count excluded is recorded;
  * measures are columns whose inferred role is money or quantity -- never identifiers or codes, whatever their
    type; a column is summed on its own, never added to another (units/currencies are never mixed);
  * everything is deterministic SQL on the extracted data. No model is involved.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys

from .core.config import load_config
from .core.errors import Xl2aiError
from .core.runs import current_run_id
from .core.sqliteutil import ro_connection

MAX_MEASURES = 4
MAX_DIMS = 3
TOP_GROUPS = 10
MAX_MONTHS = 60
DIM_ROLES = {"category": 0, "geo": 1, "code": 2, "boolean": 3}
ISO_DATE = "[0-9][0-9][0-9][0-9]-[0-9][0-9]*"
# a per-unit value (a price, a rate, an average) is not additive: its total means nothing, only its average does
NON_ADDITIVE_WORDS = ("price", "unit", "rate", "avg", "average", "mean", "per", "سعر", "متوسط", "معدل")


def q(name):
    return '"' + str(name).replace('"', '""') + '"'


def _pick_columns(con, table_id):
    """(measures, dims, date_col) by inferred role, best first."""
    rows = con.execute("""SELECT c.name, c.sql_type, c.kind, r.role, r.confidence, r.currency, r.unit,
                                 COALESCE(p.distinct_count, 0), COALESCE(p.n, 0) - COALESCE(p.nulls, 0)
                          FROM _columns c
                          LEFT JOIN _column_roles r ON r.column_id = c.column_id
                          LEFT JOIN _profile_columns p ON p.column_id = c.column_id
                          WHERE c.table_id = ? ORDER BY c.position""", (table_id,)).fetchall()
    measures, dims, dates = [], [], []
    for name, sql_type, kind, role, conf, currency, unit, distinct, filled in rows:
        numeric = str(sql_type).upper() in ("INTEGER", "REAL")
        if numeric and role in ("money", "quantity"):
            rank = (0 if role == "money" else 1 if (conf or 0) >= 0.6 else 2)
            if not _additive(name):
                rank += 3                       # still summarised (average/range), never the headline total
            measures.append((rank, name, currency or unit))
        elif role in DIM_ROLES and 2 <= distinct <= 50 and filled:
            dims.append((DIM_ROLES[role], -filled, name))
        elif role == "date" and kind == "date":
            dates.append((-filled, name))
    measures.sort(key=lambda m: m[0])
    dims.sort()
    dates.sort()
    return ([(n, u) for _, n, u in measures[:MAX_MEASURES]], [n for _, _, n in dims[:MAX_DIMS]],
            dates[0][1] if dates else None)


def _additive(name):
    tokens = set(re.sub(r"[_\W]+", " ", str(name).lower()).split())
    return not any(w in tokens for w in NON_ADDITIVE_WORDS)


def _row_id(*parts):
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:20]


def _digest_table(con, src, table_id, table, measures, dims, date_col, excluded):
    """Write `_digest` rows for one table. Returns the number of rows written."""
    where = f"WHERE _xl_row NOT IN ({','.join(str(int(x)) for x in excluded)})" if excluded else ""
    rows_out = []
    primary = next((name for name, _ in measures if _additive(name)), None)

    sql = f"SELECT COUNT(*) FROM {q(table)} {where}"
    total_rows = src.execute(sql).fetchone()[0]
    rows_out.append(("total", "*", None, "rows", float(total_rows), total_rows, None, None, sql))
    totals = {}
    for name, unit in measures:
        sql = (f"SELECT TOTAL({q(name)}), AVG({q(name)}), MIN({q(name)}), MAX({q(name)}), COUNT({q(name)}) "
               f"FROM {q(table)} {where}")
        s, a, lo, hi, n = src.execute(sql).fetchone()
        totals[name] = s
        stats = (("sum", s), ("avg", a), ("min", lo), ("max", hi)) if _additive(name) else \
            (("avg", a), ("min", lo), ("max", hi))
        for key, value in stats:
            rows_out.append(("total", name, None, key, value, n, None, None, sql))

    for dim in dims:
        agg = f"TOTAL({q(primary)})" if primary else "COUNT(*)"
        sql = (f"SELECT {q(dim)}, {agg} AS v, COUNT(*) FROM {q(table)} {where} "
               f"GROUP BY {q(dim)} ORDER BY v DESC, {q(dim)} LIMIT {TOP_GROUPS + 1}")
        groups = src.execute(sql).fetchall()
        base = totals.get(primary) if primary else total_rows
        shown = groups[:TOP_GROUPS]
        for rank, (key, value, n) in enumerate(shown, 1):
            share = (value / base) if base else None
            rows_out.append(("by_group", primary or "*", dim, "(blank)" if key is None else str(key), value, n,
                             share, rank, sql))
        if len(groups) > TOP_GROUPS:
            rest_sql = (f"SELECT COUNT(DISTINCT {q(dim)}) FROM {q(table)} {where}")
            n_groups = src.execute(rest_sql).fetchone()[0]
            covered = sum(v or 0 for _, v, _ in shown)
            rest = (base or 0) - covered
            rows_out.append(("by_group", primary or "*", dim, f"(other {n_groups - TOP_GROUPS} groups)", rest,
                             None, (rest / base) if base else None, TOP_GROUPS + 1, sql))

    if date_col:
        agg = f"TOTAL({q(primary)})" if primary else "COUNT(*)"
        cond = f"{q(date_col)} GLOB '{ISO_DATE}'"
        month_where = f"{where} AND {cond}" if where else f"WHERE {cond}"
        sql = (f"SELECT substr({q(date_col)}, 1, 7) AS month, {agg}, COUNT(*) FROM {q(table)} {month_where} "
               f"GROUP BY month ORDER BY month DESC LIMIT {MAX_MONTHS}")
        for rank, (month, value, n) in enumerate(reversed(src.execute(sql).fetchall()), 1):
            rows_out.append(("by_month", primary or "*", date_col, month, value, n, None, rank, sql))

    con.executemany("INSERT OR REPLACE INTO _digest VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    [(_row_id(table_id, sec, m, d, k), table_id, sec, m, d, k, v, n, sh, rk, sql)
                     for sec, m, d, k, v, n, sh, rk, sql in rows_out])
    return len(rows_out)


def _sum_rows(con, src, table_id, table, measures, excluded):
    """Rows whose amount equals the sum of every other row: a grand-total row, whatever its label says.

    Checked only on the largest value of each additive measure, and only when that row leaves at least half of its
    cells empty (a totals row carries a label and numbers, not a full record) -- so a genuine big sale cannot match.
    Recorded as `totals_candidate` flags with the reason, like label-based ones.
    """
    found = []
    ncols = src.execute(f"SELECT COUNT(*) FROM pragma_table_info('{table.replace(chr(39), chr(39) * 2)}')").fetchone()[0]
    for name, _ in measures:
        if not _additive(name):
            continue
        skip = f"AND _xl_row NOT IN ({','.join(str(int(x)) for x in excluded + found)})" if excluded + found else ""
        row = src.execute(f"SELECT _xl_row, {q(name)} FROM {q(table)} WHERE typeof({q(name)}) IN ('integer','real') "
                          f"{skip} ORDER BY {q(name)} DESC LIMIT 1").fetchone()
        if not row or not row[1] or row[1] <= 0:
            continue
        xl_row, top = row
        rest = src.execute(f"SELECT TOTAL({q(name)}), COUNT({q(name)}) FROM {q(table)} WHERE _xl_row <> ? {skip}",
                           (xl_row,)).fetchone()
        if rest[1] < 2 or abs(top - rest[0]) > 0.005 * abs(top):
            continue
        filled = src.execute(f"SELECT * FROM {q(table)} WHERE _xl_row=?", (xl_row,)).fetchone()
        empties = sum(v is None for v in filled[1:])
        if empties * 2 >= max(1, ncols - 1):
            found.append(xl_row)
            con.execute("INSERT OR REPLACE INTO _row_flags VALUES (?,?,?,?)",
                        (table_id, int(xl_row), "totals_candidate", f"{name} equals the sum of all other rows"))
    return found


def build_digest(cfg, run_id, catalog_path=None):
    run_dir = os.path.join(cfg.runs_dir, run_id)
    path = catalog_path or os.path.join(run_dir, "catalog.db")
    if not os.path.isfile(path):
        raise Xl2aiError("E_STAGE_INPUT", f"catalog not found: {path}", "run semantics first")
    con = sqlite3.connect(path)
    try:
        con.executescript(DDL)
        con.execute("DELETE FROM _digest")
        con.execute("DELETE FROM _digest_tables")
        present = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        kinds = dict(con.execute("SELECT table_id, kind FROM _table_kind")) if "_table_kind" in present else {}
        regions = dict(con.execute("SELECT table_id, COUNT(*) FROM _regions WHERE kind='table' GROUP BY table_id")) \
            if "_regions" in present else {}
        for table_id, table, db_rel, row_count in con.execute(
                "SELECT table_id, table_name, db_rel, row_count FROM _tables ORDER BY table_id").fetchall():
            kind = kinds.get(table_id)
            measures, dims, date_col = _pick_columns(con, table_id)
            reason = None
            if not row_count:
                reason = "no data rows"
            elif kind not in (None, "data"):
                reason = f"sheet looks like a {kind}, not one table of records; totals would be misleading"
            elif regions.get(table_id, 0) > 1:
                reason = "sheet holds several tables; summarise each region instead (query region)"
            elif not (measures or dims or date_col):
                reason = "no amount, category or date column recognised"
            excluded = []
            if "_row_flags" in present:
                excluded = [r[0] for r in con.execute(
                    "SELECT DISTINCT xl_row FROM _row_flags WHERE table_id=? AND flag='totals_candidate'",
                    (table_id,))]
            meta = (json.dumps([m for m, _ in measures], ensure_ascii=False),
                    json.dumps(dict(measures), ensure_ascii=False), json.dumps(dims, ensure_ascii=False), date_col)
            if reason:
                con.execute("INSERT INTO _digest_tables VALUES (?,?,?,?,?,?,?,?)",
                            (table_id, "skipped", reason, len(excluded)) + meta)
                continue
            with ro_connection(os.path.join(run_dir, db_rel.replace("/", os.sep))) as src:
                try:
                    extra = _sum_rows(con, src, table_id, table, measures, excluded)
                    excluded = excluded + extra
                    _digest_table(con, src, table_id, table, measures, dims, date_col, excluded)
                    status, reason = "computed", None
                except sqlite3.DatabaseError as e:
                    status, reason = "error", str(e)
            con.execute("INSERT INTO _digest_tables VALUES (?,?,?,?,?,?,?,?)",
                        (table_id, status, reason, len(excluded)) + meta)
        con.commit()
    finally:
        con.close()
    return path


DDL = """
CREATE TABLE IF NOT EXISTS _digest (
  id TEXT PRIMARY KEY, table_id TEXT NOT NULL, section TEXT NOT NULL, measure TEXT, dim TEXT, key TEXT,
  value REAL, rows INTEGER, share REAL, rank INTEGER, sql TEXT
);
CREATE INDEX IF NOT EXISTS idx_digest_table ON _digest(table_id, section, dim, rank);
CREATE TABLE IF NOT EXISTS _digest_tables (
  table_id TEXT PRIMARY KEY, status TEXT, reason TEXT, excluded_rows INTEGER, measures_json TEXT,
  units_json TEXT, dims_json TEXT, date_column TEXT
);
"""


def _fmt(v):
    if v is None:
        return "-"
    if isinstance(v, float) and v.is_integer() and abs(v) < 1e15:
        v = int(v)
    if isinstance(v, int):
        return f"{v:,}"
    return f"{v:,.2f}"


def digest_lines(con, table_id, top=3, months=3):
    """Short plain-language lines for one table (used by the agent brief). [] when nothing was computed."""
    if not con.execute("SELECT 1 FROM sqlite_master WHERE name='_digest_tables'").fetchone():
        return []
    row = con.execute("SELECT status, reason, excluded_rows, units_json FROM _digest_tables WHERE table_id=?",
                      (table_id,)).fetchone()
    if not row:
        return []
    status, reason, excluded, units_json = row
    if status != "computed":
        return [f"Key numbers: not computed -- {reason}"]
    units = json.loads(units_json or "{}")
    lines = []
    n = con.execute("SELECT value FROM _digest WHERE table_id=? AND section='total' AND key='rows'",
                    (table_id,)).fetchone()
    head = f"Key numbers ({_fmt(n[0] if n else None)} rows"
    head += f", {excluded} totals row(s) excluded)" if excluded else ")"
    lines.append(head + ":")
    for measure, s, a, lo, hi in con.execute(
            """SELECT measure,
                      MAX(CASE key WHEN 'sum' THEN value END), MAX(CASE key WHEN 'avg' THEN value END),
                      MAX(CASE key WHEN 'min' THEN value END), MAX(CASE key WHEN 'max' THEN value END)
               FROM _digest WHERE table_id=? AND section='total' AND measure<>'*'
               GROUP BY measure ORDER BY MIN(rowid)""", (table_id,)):
        unit = f" {units[measure]}" if units.get(measure) else ""
        total = f"total {_fmt(s)}{unit}, " if s is not None else ""
        lines.append(f"    - {measure}: {total}average {_fmt(a)}{unit if s is None else ''}, "
                     f"range {_fmt(lo)} to {_fmt(hi)}" + ("" if s is not None else " (a per-unit value: not added up)"))
    for measure, dim in con.execute("""SELECT DISTINCT measure, dim FROM _digest
                                       WHERE table_id=? AND section='by_group' ORDER BY dim""", (table_id,)).fetchall():
        parts = [f"{k} {share:.0%}" if share is not None else f"{k} {_fmt(v)}"
                 for k, v, share in con.execute(
                     """SELECT key, value, share FROM _digest WHERE table_id=? AND section='by_group' AND dim=?
                        ORDER BY rank LIMIT ?""", (table_id, dim, top))]
        what = "rows" if measure == "*" else measure
        lines.append(f"    - biggest {dim} by {what}: " + ", ".join(parts))
    trend = con.execute("""SELECT measure, dim, key, value FROM _digest WHERE table_id=? AND section='by_month'
                           ORDER BY rank""", (table_id,)).fetchall()
    if trend:
        measure, dim = trend[0][0], trend[0][1]
        what = "rows" if measure == "*" else measure
        tail = ", ".join(f"{k} {_fmt(v)}" for _, _, k, v in trend[-months:])
        lines.append(f"    - {what} by month of {dim} ({trend[0][2]} to {trend[-1][2]}, {len(trend)} months): "
                     f"latest {tail}")
    return lines


def main(argv=None):
    ap = argparse.ArgumentParser(prog="xl2ai digest",
                                 description="Pre-compute key numbers: totals, biggest groups and monthly trends.")
    ap.add_argument("--config")
    ap.add_argument("--run")
    args = ap.parse_args(argv)
    try:
        cfg = load_config(args.config)
        rid = args.run or current_run_id(cfg)
        if not rid:
            raise Xl2aiError("E_STAGE_INPUT", "no current run")
        path = build_digest(cfg, rid)
    except Xl2aiError as e:
        print(str(e), file=sys.stderr)
        return 1
    print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())

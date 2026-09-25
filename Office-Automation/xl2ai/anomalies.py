"""Things that look unusual: the checks an analyst runs before trusting a number, run once, in advance.

Four deterministic detectors, each bounded, each explaining itself with examples an agent can trace to an Excel row:
  * outlier    -- values far outside the column's own spread (beyond Q1 - 3*IQR or Q3 + 3*IQR, "extreme" fences);
  * negative   -- negative values in an amount/quantity column that is otherwise almost always positive;
  * trend_jump / missing_period -- a month far off the table's usual monthly level, or months with no rows at all
    inside the covered range (from the pre-computed monthly key numbers);
  * date_out_of_range -- dates before 1990 or more than a year in the future.

Every finding is `inferred`: unusual is not wrong. The point is that an agent sees "3 extreme values in amount
(e.g. 999,999 at Excel row 62)" before it averages that column, instead of discovering it after.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sqlite3
import statistics
import sys

from .core.config import load_config
from .core.errors import Xl2aiError
from .core.runs import current_run_id
from .core.sqliteutil import ro_connection

MIN_ROWS = 20                 # fewer values than this: no spread worth measuring
FENCE = 3.0                   # IQR multiplier: 3 = "extreme" (Tukey), deliberately conservative
NEGATIVE_MAX_SHARE = 0.02     # "negative" is only notable when negatives are rare
TREND_Z = 3.5                 # robust z-score (median/MAD) for a month to count as a jump
EXAMPLES = 5

DDL = """
CREATE TABLE IF NOT EXISTS _anomalies (
  id TEXT PRIMARY KEY, table_id TEXT NOT NULL, column_name TEXT, kind TEXT NOT NULL, severity TEXT,
  count INTEGER, detail TEXT, examples TEXT, sql TEXT
);
"""


def q(name):
    return '"' + str(name).replace('"', '""') + '"'


def _id(*parts):
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:20]


def _quantile(src, table, col, where, n, frac):
    offset = max(0, min(n - 1, int(round(frac * (n - 1)))))
    row = src.execute(f"SELECT {q(col)} FROM {q(table)} {where} ORDER BY {q(col)} LIMIT 1 OFFSET ?",
                      (offset,)).fetchone()
    return row[0] if row else None


def _numeric_where(col, excluded):
    parts = [f"typeof({q(col)}) IN ('integer','real')"]
    if excluded:
        parts.append(f"_xl_row NOT IN ({','.join(str(int(x)) for x in excluded)})")
    return "WHERE " + " AND ".join(parts)


def _column_checks(src, table, col, excluded):
    """[(kind, severity, count, detail, examples, sql)] for one numeric measure column."""
    out = []
    where = _numeric_where(col, excluded)
    n = src.execute(f"SELECT COUNT(*) FROM {q(table)} {where}").fetchone()[0]
    if n < MIN_ROWS:
        return out
    q1 = _quantile(src, table, col, where, n, 0.25)
    q3 = _quantile(src, table, col, where, n, 0.75)
    med = _quantile(src, table, col, where, n, 0.5)
    if q1 is not None and q3 is not None and q3 > q1:
        iqr = q3 - q1
        lo, hi = q1 - FENCE * iqr, q3 + FENCE * iqr
        sql = (f"SELECT _xl_row, {q(col)} FROM {q(table)} {where} AND ({q(col)} < {lo!r} OR {q(col)} > {hi!r}) "
               f"ORDER BY abs({q(col)} - {med!r}) DESC")
        cnt = src.execute(f"SELECT COUNT(*) FROM ({sql})").fetchone()[0]
        if cnt:
            ex = [[r, v] for r, v in src.execute(sql + f" LIMIT {EXAMPLES}")]
            share = cnt / n
            out.append(("outlier", "warn" if share < 0.01 else "info", cnt,
                        f"{cnt:,} value(s) outside the extreme range {lo:,.4g} .. {hi:,.4g} "
                        f"(median {med:,.4g}, middle half {q1:,.4g} .. {q3:,.4g}); averages and totals of this "
                        f"column are sensitive to them", ex, sql))
    neg_sql = f"SELECT _xl_row, {q(col)} FROM {q(table)} {where} AND {q(col)} < 0 ORDER BY {q(col)}"
    neg = src.execute(f"SELECT COUNT(*) FROM ({neg_sql})").fetchone()[0]
    if neg and neg / n <= NEGATIVE_MAX_SHARE:
        ex = [[r, v] for r, v in src.execute(neg_sql + f" LIMIT {EXAMPLES}")]
        out.append(("negative", "info", neg,
                    f"{neg:,} negative value(s) in a column that is otherwise positive ({neg / n:.2%} of rows) -- "
                    f"returns, corrections or entry errors; decide before summing", ex, neg_sql))
    return out


def _trend_checks(con, table_id):
    """Month jumps and missing months from the digest's monthly series (no extra scan of the data)."""
    months = con.execute("""SELECT key, value, dim, measure FROM _digest WHERE table_id=? AND section='by_month'
                            ORDER BY key""", (table_id,)).fetchall()
    out = []
    if len(months) < 2:
        return out
    dim, measure = months[0][2], months[0][3]
    what = "row count" if measure == "*" else measure
    have = {m for m, _, _, _ in months}
    y, mo = map(int, months[0][0].split("-"))
    ye, me = map(int, months[-1][0].split("-"))
    missing = []
    while (y, mo) < (ye, me):
        mo += 1
        if mo > 12:
            y, mo = y + 1, 1
        key = f"{y:04d}-{mo:02d}"
        if key not in have:
            missing.append(key)
    if missing:
        out.append((dim, "missing_period", "warn", len(missing),
                    f"no rows at all for {len(missing)} month(s) inside {months[0][0]} .. {months[-1][0]} "
                    f"(by {dim}); a gap in the data, or a period that really had no activity", missing[:12], None))
    values = [v or 0.0 for _, v, _, _ in months]
    if len(values) >= 6:
        med = statistics.median(values)
        mad = statistics.median(abs(v - med) for v in values)
        if mad > 0:
            jumps = [(m, v, 0.6745 * (v - med) / mad) for (m, _, _, _), v in zip(months, values)]
            jumps = [(m, v, z) for m, v, z in jumps if abs(z) >= TREND_Z]
            if jumps:
                out.append((dim, "trend_jump", "info", len(jumps),
                            f"{len(jumps)} month(s) where {what} is far from its usual monthly level "
                            f"(median {med:,.4g}): " + ", ".join(f"{m} {'up' if z > 0 else 'down'}"
                                                                 for m, _, z in jumps[:6]),
                            [[m, v] for m, v, _ in jumps[:EXAMPLES]], None))
    return out


def _date_checks(src, table, col, excluded):
    now = dt.date.today()
    lo, hi = "1990-01-01", f"{now.year + 1:04d}-{now.month:02d}-{now.day:02d}"
    extra = f" AND _xl_row NOT IN ({','.join(str(int(x)) for x in excluded)})" if excluded else ""
    sql = (f"SELECT _xl_row, {q(col)} FROM {q(table)} WHERE {q(col)} GLOB '[0-9][0-9][0-9][0-9]-*' "
           f"AND ({q(col)} < '{lo}' OR {q(col)} > '{hi}'){extra} ORDER BY {q(col)}")
    cnt = src.execute(f"SELECT COUNT(*) FROM ({sql})").fetchone()[0]
    if not cnt:
        return []
    ex = [[r, v] for r, v in src.execute(sql + f" LIMIT {EXAMPLES}")]
    return [("date_out_of_range", "warn", cnt,
             f"{cnt:,} date(s) before {lo} or after {hi} -- typing errors or placeholder dates", ex, sql)]


def build_anomalies(cfg, run_id, catalog_path=None):
    run_dir = os.path.join(cfg.runs_dir, run_id)
    path = catalog_path or os.path.join(run_dir, "catalog.db")
    if not os.path.isfile(path):
        raise Xl2aiError("E_STAGE_INPUT", f"catalog not found: {path}", "run digest first")
    con = sqlite3.connect(path)
    try:
        con.executescript(DDL)
        con.execute("DELETE FROM _anomalies")
        computed = con.execute("""SELECT d.table_id, t.table_name, t.db_rel, d.measures_json, d.date_column
                                  FROM _digest_tables d JOIN _tables t ON t.table_id = d.table_id
                                  WHERE d.status = 'computed'""").fetchall()
        rows = []
        for table_id, table, db_rel, measures_json, date_col in computed:
            excluded = [r[0] for r in con.execute(
                "SELECT DISTINCT xl_row FROM _row_flags WHERE table_id=? AND flag='totals_candidate'", (table_id,))]
            with ro_connection(os.path.join(run_dir, db_rel.replace("/", os.sep))) as src:
                for col in json.loads(measures_json or "[]"):
                    for kind, sev, cnt, detail, ex, sql in _column_checks(src, table, col, excluded):
                        rows.append((_id(table_id, col, kind), table_id, col, kind, sev, cnt, detail,
                                     json.dumps(ex, ensure_ascii=False, default=str), sql))
                if date_col:
                    for kind, sev, cnt, detail, ex, sql in _date_checks(src, table, date_col, excluded):
                        rows.append((_id(table_id, date_col, kind), table_id, date_col, kind, sev, cnt, detail,
                                     json.dumps(ex, ensure_ascii=False, default=str), sql))
            for col, kind, sev, cnt, detail, ex, sql in _trend_checks(con, table_id):
                rows.append((_id(table_id, col, kind), table_id, col, kind, sev, cnt, detail,
                             json.dumps(ex, ensure_ascii=False, default=str), sql))
        con.executemany("INSERT OR REPLACE INTO _anomalies VALUES (?,?,?,?,?,?,?,?,?)", rows)
        con.commit()
    finally:
        con.close()
    return path


def anomaly_lines(con, table_id, limit=5):
    if not con.execute("SELECT 1 FROM sqlite_master WHERE name='_anomalies'").fetchone():
        return []
    rows = con.execute("""SELECT column_name, kind, severity, detail, examples FROM _anomalies WHERE table_id=?
                          ORDER BY CASE severity WHEN 'warn' THEN 0 ELSE 1 END, kind LIMIT ?""",
                       (table_id, limit)).fetchall()
    if not rows:
        return []
    lines = ["Looks unusual (check before relying on totals):"]
    for col, kind, sev, detail, examples in rows:
        ex = json.loads(examples or "[]")
        shown = ""
        if ex and isinstance(ex[0], list) and len(ex[0]) == 2 and isinstance(ex[0][0], int):
            shown = " e.g. " + ", ".join(f"{v} at Excel row {r}" for r, v in ex[:3])
        lines.append(f"    - [{sev}] {col} {kind}: {detail}{shown}")
    return lines


def main(argv=None):
    ap = argparse.ArgumentParser(prog="xl2ai anomalies", description="Flag outliers, odd months and odd dates.")
    ap.add_argument("--config")
    ap.add_argument("--run")
    args = ap.parse_args(argv)
    try:
        cfg = load_config(args.config)
        rid = args.run or current_run_id(cfg)
        if not rid:
            raise Xl2aiError("E_STAGE_INPUT", "no current run")
        path = build_anomalies(cfg, rid)
    except Xl2aiError as e:
        print(str(e), file=sys.stderr)
        return 1
    print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Do the reports agree with the raw data? Checked by matching, not by trusting the report's formulas.

A report table ("Cairo 1,250 / Giza 980 / Total 2,230") is compared with every raw data table it could summarise:
when the report's row labels are the values of one of the raw table's category columns, each report number is
checked against the raw table's SUM (or COUNT) of each amount column grouped by that category. A report column
whose numbers mostly match one of those aggregates is *explained* by it; the rows that do not match are listed
with both numbers. A "Total" row is checked against the grand total.

This finds the classic failure an agent would otherwise repeat unknowingly: a report that silently drifted from
its data (a pasted value, a filter left on, a stale refresh). It needs no formulas -- values alone -- so it works
for pasted-value reports and for the DRM files the direct engine cannot read, and it never re-reads Excel.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sqlite3
import sys

from .core.config import load_config
from .core.errors import Xl2aiError
from .core.runs import current_run_id
from .core.sqliteutil import ro_connection
from .query import fold

MAX_REPORT_ROWS = 1000
MAX_DIM_DISTINCT = 5000
MIN_LABEL_OVERLAP = 0.6
MIN_MATCH_SHARE = 0.5
TOTAL_WORDS = {fold(w) for w in ("total", "totals", "grand total", "الإجمالي", "إجمالي", "اجمالي", "الاجمالي",
                                  "المجموع", "مجموع", "sum")}

DDL = """
CREATE TABLE IF NOT EXISTS _reconciliation (
  id TEXT PRIMARY KEY, report_table_id TEXT NOT NULL, report_column TEXT, label_column TEXT,
  source_table_id TEXT, source_column TEXT, dim_column TEXT, agg TEXT, compared INTEGER, matched INTEGER,
  status TEXT, mismatches TEXT, sql TEXT
);
"""


def q(name):
    return '"' + str(name).replace('"', '""') + '"'


def _num(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)) and math.isfinite(v):
        return float(v)
    return None


def _close(a, b):
    return math.isclose(a, b, rel_tol=1e-6, abs_tol=0.005)


def _source_db(run_dir, db_rel):
    return ro_connection(os.path.join(run_dir, db_rel.replace("/", os.sep)))


def _report_candidates(con):
    """Small tables with at least one text-ish and one number-ish column: anything that could be a summary."""
    return con.execute("""SELECT table_id, table_name, db_rel, row_count, source_id FROM _tables
                          WHERE row_count BETWEEN 2 AND ? ORDER BY table_id""", (MAX_REPORT_ROWS,)).fetchall()


def _source_candidates(con):
    return con.execute("""SELECT t.table_id, t.table_name, t.db_rel, t.row_count FROM _tables t
                          JOIN _digest_tables d ON d.table_id = t.table_id WHERE d.status = 'computed'""").fetchall()


def _measures(con, table_id):
    """Numeric columns worth aggregating: amounts and quantities, never ids/codes."""
    return [r[0] for r in con.execute(
        """SELECT c.name FROM _columns c JOIN _column_roles r ON r.column_id = c.column_id
           WHERE c.table_id = ? AND upper(c.sql_type) IN ('INTEGER','REAL') AND r.role IN ('money','quantity')
           ORDER BY c.position""", (table_id,))]


def _dims(con, table_id):
    return [r[0] for r in con.execute(
        """SELECT c.name FROM _columns c LEFT JOIN _profile_columns p ON p.column_id = c.column_id
           LEFT JOIN _column_roles r ON r.column_id = c.column_id
           WHERE c.table_id = ? AND COALESCE(p.distinct_count, 0) BETWEEN 2 AND ?
             AND COALESCE(r.role, '') NOT IN ('money','quantity','percentage','date','free_text')
           ORDER BY c.position""", (table_id, MAX_DIM_DISTINCT))]


def _reconcile_pair(rep_rows, rep_cols, src, s_table, s_dims, s_measures, excluded):
    """Best explanation per report numeric column against one source table: [(report_col, label_col, dim,
    measure, agg, compared, matched, mismatches, sql)]."""
    found = []
    label_cols = [c for c in rep_cols if sum(bool(isinstance(r[c], str) and r[c].strip()) for r in rep_rows) >= 2]
    num_cols = [c for c in rep_cols if sum(_num(r[c]) is not None for r in rep_rows) >= 2]
    if not label_cols or not num_cols:
        return found
    where = f"WHERE _xl_row NOT IN ({','.join(str(int(x)) for x in excluded)})" if excluded else ""
    for dim in s_dims:
        values = {fold(v) for v, in src.execute(
            f"SELECT DISTINCT CAST({q(dim)} AS TEXT) FROM {q(s_table)} {where} LIMIT {MAX_DIM_DISTINCT + 1}")}
        for lab in label_cols:
            labels = {fold(r[lab]) for r in rep_rows if isinstance(r[lab], str) and r[lab].strip()}
            labels -= TOTAL_WORDS
            hit = labels & values
            if len(hit) < 2 or len(hit) / max(1, len(labels)) < MIN_LABEL_OVERLAP:
                continue
            aggs = [("SUM", m) for m in s_measures] + [("COUNT", "*")]
            series = {}
            for agg, m in aggs:
                expr = f"TOTAL({q(m)})" if agg == "SUM" else "COUNT(*)"
                sql = (f"SELECT CAST({q(dim)} AS TEXT), {expr} FROM {q(s_table)} {where} GROUP BY {q(dim)}")
                by = {}
                for k, v in src.execute(sql):
                    by[fold(k)] = by.get(fold(k), 0.0) + (v or 0.0)
                by["__all__"] = sum(by.values())
                series[(agg, m)] = (by, sql)
            for col in num_cols:
                best = None
                for (agg, m), (by, sql) in series.items():
                    compared = matched = 0
                    bad = []
                    for r in rep_rows:
                        label, value = r[lab], _num(r[col])
                        if value is None or not isinstance(label, str):
                            continue
                        key = fold(label)
                        expected = by.get("__all__") if key in TOTAL_WORDS else by.get(key)
                        if expected is None:
                            continue
                        compared += 1
                        if _close(value, expected):
                            matched += 1
                        else:
                            bad.append({"label": label, "report": value, "data": round(expected, 6),
                                        "xl_row": r["_xl_row"]})
                    if compared >= 2 and matched / compared >= MIN_MATCH_SHARE and (
                            best is None or matched > best[6]):
                        best = (col, lab, dim, m, agg, compared, matched, bad, sql)
                if best:
                    found.append(best)
    return found


def build_reconciliation(cfg, run_id, catalog_path=None):
    run_dir = os.path.join(cfg.runs_dir, run_id)
    path = catalog_path or os.path.join(run_dir, "catalog.db")
    if not os.path.isfile(path):
        raise Xl2aiError("E_STAGE_INPUT", f"catalog not found: {path}", "run digest first")
    con = sqlite3.connect(path)
    try:
        con.executescript(DDL)
        con.execute("DELETE FROM _reconciliation")
        lineage = {}
        if con.execute("SELECT 1 FROM sqlite_master WHERE name='_lineage'").fetchone():
            for tid, target in con.execute("SELECT DISTINCT table_id, target_table_id FROM _lineage "
                                           "WHERE target_table_id IS NOT NULL"):
                lineage.setdefault(tid, set()).add(target)
        sources = _source_candidates(con)
        out = []
        for r_id, r_table, r_db, r_rows, _ in _report_candidates(con):
            cands = [s for s in sources if s[0] != r_id and s[3] > r_rows]
            if r_id in lineage:
                linked = [s for s in cands if s[0] in lineage[r_id]]
                cands = linked or cands
            if not cands:
                continue
            with _source_db(run_dir, r_db) as rsrc:
                cur = rsrc.execute(f"SELECT * FROM {q(r_table)}")
                cols = [d[0] for d in cur.description]
                rep_rows = [dict(zip(cols, row)) for row in cur.fetchall()]
            rep_cols = [c for c in cols if c != "_xl_row"]
            # a sheet holding several tables is checked one table at a time: mixing a by-branch table with a
            # by-category table would dilute every label match below the threshold
            groups = [(None, rep_rows)]
            regions = con.execute("""SELECT region_no, first_row, last_row, header_row FROM _regions
                                     WHERE table_id=? AND kind='table' ORDER BY region_no""", (r_id,)).fetchall() \
                if con.execute("SELECT 1 FROM sqlite_master WHERE name='_regions'").fetchone() else []
            if len(regions) > 1:
                groups = [(no, [r for r in rep_rows if lo <= r["_xl_row"] <= hi and r["_xl_row"] != hdr])
                          for no, lo, hi, hdr in regions]
            best_per_col = {}
            for s_id, s_table, s_db, _ in cands:
                s_measures, s_dims = _measures(con, s_id), _dims(con, s_id)
                if not s_dims:
                    continue
                excluded = [x for x, in con.execute(
                    "SELECT DISTINCT xl_row FROM _row_flags WHERE table_id=? AND flag='totals_candidate'", (s_id,))]
                with _source_db(run_dir, s_db) as src:
                    for region_no, rows in groups:
                        for col, lab, dim, m, agg, compared, matched, bad, sql in _reconcile_pair(
                                rows, rep_cols, src, s_table, s_dims, s_measures, excluded):
                            prev = best_per_col.get((region_no, col))
                            if prev is None or matched > prev[6]:
                                best_per_col[(region_no, col)] = (s_id, lab, dim, m, agg, compared, matched, bad, sql)
            for (region_no, col), (s_id, lab, dim, m, agg, compared, matched, bad, sql) in best_per_col.items():
                status = "reconciled" if matched == compared else "partial"
                rid = hashlib.sha1(f"{r_id}|{region_no}|{col}".encode()).hexdigest()[:20]
                if region_no is not None:
                    lab = f"{lab} (region {region_no})"
                out.append((rid, r_id, col, lab, s_id, m, dim, agg, compared, matched, status,
                            json.dumps(bad[:10], ensure_ascii=False, default=str), sql))
        con.executemany("INSERT OR REPLACE INTO _reconciliation VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", out)
        con.commit()
    finally:
        con.close()
    return path


def reconciliation_lines(con, table_id):
    """Agent-brief lines for one report table: which columns are explained by which raw data, and disagreements."""
    if not con.execute("SELECT 1 FROM sqlite_master WHERE name='_reconciliation'").fetchone():
        return []
    rows = con.execute("""SELECT r.report_column, r.label_column, t.table_name, r.source_column, r.dim_column,
                                 r.agg, r.compared, r.matched, r.status, r.mismatches
                          FROM _reconciliation r JOIN _tables t ON t.table_id = r.source_table_id
                          WHERE r.report_table_id = ? ORDER BY r.status DESC, r.report_column""",
                       (table_id,)).fetchall()
    if not rows:
        return []
    lines = ["Checked against raw data:"]
    for col, lab, src, m, dim, agg, compared, matched, status, bad_json in rows:
        how = f"{agg}({src}.{m})" if agg == "SUM" else f"row count of {src}"
        if status == "reconciled":
            lines.append(f"    - {col} = {how} by {dim}: all {compared} rows agree")
        else:
            bad = json.loads(bad_json or "[]")
            ex = "; ".join(f"{b['label']}: report {b['report']:,} vs data {b['data']:,}" for b in bad[:3])
            lines.append(f"    - {col} ~ {how} by {dim}: **{compared - matched} of {compared} rows disagree** ({ex})")
    return lines


def main(argv=None):
    ap = argparse.ArgumentParser(prog="xl2ai reconcile", description="Check report tables against raw data.")
    ap.add_argument("--config")
    ap.add_argument("--run")
    args = ap.parse_args(argv)
    try:
        cfg = load_config(args.config)
        rid = args.run or current_run_id(cfg)
        if not rid:
            raise Xl2aiError("E_STAGE_INPUT", "no current run")
        path = build_reconciliation(cfg, rid)
    except Xl2aiError as e:
        print(str(e), file=sys.stderr)
        return 1
    print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())

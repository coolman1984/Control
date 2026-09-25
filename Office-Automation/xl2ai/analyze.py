"""Profile verified tables, surface generic data-quality findings, infer candidate keys, and fingerprint rows."""
from __future__ import annotations

import argparse
from collections import Counter
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


NULL_TOKENS = ("n/a", "na", "null", "none", "-", "--", "nil", "(blank)", "blank")

TOTALS_LABELS = ("total", "totals", "grand total", "subtotal", "sub-total", "sum", "net total",
                 "إجمالي", "الإجمالي", "المجموع", "مجموع", "الاجمالي", "اجمالي")


TOTALS_PREFIXES = ("total", "subtotal", "sub-total", "grand total", "إجمالي", "الإجمالي", "اجمالي", "الاجمالي",
                   "مجموع", "المجموع")


def q(name):
    return '"' + str(name).replace('"', '""') + '"'


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _finding_id(code, subject):
    return hashlib.sha1(f"{code}|{subject}".encode()).hexdigest()[:20]


def _value(v):
    if v is None:
        return None
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return str(v)
    return v


def _row_fingerprint(src, table_name, row_count, max_rows):
    """Content fingerprint plus a row-hash multiset, ignoring Excel row position.

    Reordering identical business rows should not look like a data change. The multiset also lets change detection say
    how many rows were added/removed even when the table has no trusted key.
    """
    if max_rows <= 0 or row_count > max_rows:
        return None, "omitted", None
    counts = Counter()
    cur = src.execute(f"SELECT * FROM {q(table_name)}")
    # The Excel row tracker is always named "_xl_row" and always first (see extract/sheet.py); detect it by
    # name, never by the value's type -- an ordinary integer business column in that position looks the same.
    skip_first = bool(cur.description) and cur.description[0][0] == "_xl_row"
    for row in cur:
        business = row[1:] if skip_first else row
        rh = hashlib.sha256(_json([_value(v) for v in business]).encode("utf-8", "surrogatepass")).hexdigest()
        counts[rh] += 1
    h = hashlib.sha256()
    for rh, n in sorted(counts.items()):
        h.update(rh.encode())
        h.update(b":")
        h.update(str(n).encode())
        h.update(b"\n")
    return h.hexdigest(), "multiset_sha256", counts


def _column_profile(src, table_name, name, sql_type, kind, row_count, top_k, sample_values):
    col = q(name)
    tbl = q(table_name)
    # COUNT/COUNT DISTINCT/MIN/MAX/AVG in one pass instead of up to three separate full-column scans; SQL
    # aggregates already ignore NULLs, so no WHERE clause is needed here.
    want_mean = str(sql_type).upper() in ("INTEGER", "REAL") and kind not in ("date", "datetime", "time")
    mean_expr = f"AVG({col})" if want_mean else "NULL"
    n, distinct_count, mn, mx, mean = src.execute(
        f"SELECT COUNT({col}), COUNT(DISTINCT {col}), MIN({col}), MAX({col}), {mean_expr} FROM {tbl}"
    ).fetchone()
    nulls = max(0, int(row_count) - int(n or 0))
    top = src.execute(
        f"SELECT {col}, COUNT(*) c FROM {tbl} WHERE {col} IS NOT NULL "
        f"GROUP BY {col} ORDER BY c DESC, {col} LIMIT ?", (top_k,)
    ).fetchall()
    sample = src.execute(
        f"SELECT DISTINCT {col} FROM {tbl} WHERE {col} IS NOT NULL LIMIT ?", (sample_values,)
    ).fetchall()
    return {
        "n": int(n or 0), "nulls": nulls, "distinct": int(distinct_count or 0),
        "min": _value(mn), "max": _value(mx), "mean": _value(mean),
        "top": [[_value(v), int(c)] for v, c in top], "sample": [_value(x[0]) for x in sample],
    }


def _detect_totals_rows(src, table_name, text_columns, max_rows):
    """[(xl_row, label)] for rows whose first_row-order matching text column reads as a totals/subtotal label.

    Label-based only (not value-matching a column sum): a coincidental partial sum is a common false positive,
    while "total"/"grand total"/"إجمالي" in a text cell is a strong, low-noise signal a human would also use.
    Bounded by max_rows for the same reason row-hashing is: a full scan on a huge table is not worth its cost here.
    """
    if not text_columns:
        return []
    tbl = q(table_name)
    found = {}
    marks = ",".join("?" * len(TOTALS_LABELS))
    # a bare label ("Total"), or a short label led/ended by one ("Cairo Total", "Total Q1", "إجمالي القاهرة");
    # length-capped so a sentence that merely mentions a total is not taken for a totals row
    affix = " OR ".join(["v LIKE ?"] * (2 * len(TOTALS_PREFIXES)))
    affix_args = [p for w in TOTALS_PREFIXES for p in (w + " %", "% " + w)]
    for col in text_columns:
        rows = src.execute(
            f"SELECT _xl_row, raw FROM (SELECT _xl_row, {q(col)} AS raw, LOWER(TRIM(CAST({q(col)} AS TEXT))) AS v "
            f"FROM {tbl} "
            f"WHERE typeof({q(col)}) = 'text') WHERE v IN ({marks}) OR (length(v) <= 40 AND ({affix})) LIMIT ?",
            (*TOTALS_LABELS, *affix_args, max_rows)
        ).fetchall()
        for xl_row, val in rows:
            found.setdefault(xl_row, str(val))
    return sorted(found.items())


def _classify_table_kind(row_count, column_count, header_row, formula_ratio, pivot_tables, has_totals_rows):
    """(kind, confidence, method, [reason,...]) -- always `inferred`, never presented as confirmed.

    A deliberately small, explicit rule set over signals the platform already computed, not a model call: an
    agent can see exactly why a table was classified a given way and override it via config if wrong.
    """
    reasons = []
    if row_count == 0:
        return "empty", 1.0, "heuristic", ["no data rows"]
    if header_row is None and row_count <= 20 and column_count <= 4:
        reasons.append(f"no header detected and small ({row_count} rows x {column_count} cols)")
        return "notes", 0.6, "heuristic", reasons
    if pivot_tables:
        reasons.append(f"{pivot_tables} pivot table(s) present on this sheet")
        return "dashboard", 0.6, "heuristic", reasons
    if formula_ratio is not None and formula_ratio >= 0.3:
        reasons.append(f"{formula_ratio:.0%} of cells carry formulas")
        return "report", 0.5, "heuristic", reasons
    if has_totals_rows and row_count <= 50:
        reasons.append("small table containing a totals/subtotal row and little else")
        return "report", 0.4, "heuristic", reasons
    reasons.append(f"{row_count} data row(s), header detected, low formula density")
    return "data", 0.7 if header_row is not None else 0.4, "heuristic", reasons


def _add_finding(con, code, severity, table_id, column_id, count, examples, message):
    subject = column_id or table_id
    con.execute("INSERT OR REPLACE INTO _dq_findings VALUES (?,?,?,?,?,?,?,?)",
                (_finding_id(code, subject), code, severity, table_id, column_id, int(count or 0),
                 _json(examples or []), message))


def analyze_catalog(cfg, run_id, catalog_path=None):
    run_dir = os.path.join(cfg.runs_dir, run_id)
    path = catalog_path or os.path.join(run_dir, "catalog.db")
    if not os.path.isfile(path):
        raise Xl2aiError("E_STAGE_INPUT", f"catalog not found: {path}", "run catalog first")

    con = sqlite3.connect(path)
    try:
        con.execute("DELETE FROM _profile_columns")
        con.execute("DELETE FROM _table_profiles")
        con.execute("DELETE FROM _row_hashes")
        con.execute("DELETE FROM _dq_findings")
        con.execute("DELETE FROM _keys")
        con.execute("DELETE FROM _table_kind")
        con.execute("DELETE FROM _row_flags")
        tables = con.execute("""SELECT table_id,table_name,db_rel,row_count,header_row,column_count
                                FROM _tables ORDER BY table_id""").fetchall()
        for table_id, table_name, db_rel, row_count, header_row, column_count in tables:
            src_path = os.path.join(run_dir, db_rel.replace("/", os.sep))
            with ro_connection(src_path) as src:
                if header_row is None:
                    _add_finding(con, "DQ_HEADER_UNDETECTED", "info", table_id, None, 1, [],
                                 "no header row was detected; generated column names may need confirmation")
                if int(column_count or 0) >= 500:
                    _add_finding(con, "DQ_VERY_WIDE", "warn", table_id, None, int(column_count), [],
                                 "very wide table; inspect whether several logical regions were flattened together")
                log_cols = {r[1] for r in src.execute("PRAGMA table_info(_extraction_log)").fetchall()}
                wanted = {"formula_cells", "pivot_tables", "merged_in_data"}
                formulas = pivots = merged = 0
                if wanted.issubset(log_cols):
                    x = src.execute("""SELECT formula_cells,pivot_tables,merged_in_data
                                       FROM _extraction_log WHERE table_name=? LIMIT 1""", (table_name,)).fetchone()
                    if x:
                        formulas, pivots, merged = (int(v or 0) for v in x)
                        if formulas:
                            _add_finding(con, "DQ_FORMULAS_VALUE_ONLY", "warn", table_id, None, formulas, [],
                                         "formula results were extracted as values; which sheets they pull from is in query meta lineage")
                        if pivots:
                            _add_finding(con, "DQ_PIVOT_OUTPUT_ONLY", "warn", table_id, None, pivots, [],
                                         "pivot output was extracted; pivot definition/source logic is not yet represented")
                        if merged:
                            _add_finding(con, "DQ_MERGED_IN_DATA", "warn", table_id, None, 1, [],
                                         "merged cells exist inside the data region; only top-left cells carry values")
                total_cells = int(row_count or 0) * int(column_count or 0)
                formula_ratio = (formulas / total_cells) if total_cells else None
                row_fp, row_mode, row_hashes = _row_fingerprint(
                    src, table_name, int(row_count), cfg.analysis["row_hash_max_rows"])
                con.execute("INSERT INTO _table_profiles VALUES (?,?,?,?)",
                            (table_id, int(row_count), row_fp, row_mode))
                if row_hashes:
                    con.executemany("INSERT INTO _row_hashes VALUES (?,?,?)",
                                    ((table_id, rh, n) for rh, n in row_hashes.items()))
                cols = con.execute("""SELECT column_id,name,sql_type,kind,non_null,error_cells,original_header
                                      FROM _columns WHERE table_id=? ORDER BY position""", (table_id,)).fetchall()
                candidate_cols = []
                # every column that can hold text: a "Total" label typed into a numeric id column makes it mixed,
                # and one typed into a date column sits among dates stored as text -- both were missed before
                text_columns = [c[1] for c in cols if c[3] in ("text", "mixed", "date")]
                generated_headers = sum(1 for c in cols if c[6] is None)
                if cols and generated_headers / len(cols) >= 0.5:
                    _add_finding(con, "DQ_HEADER_LOW_CONFIDENCE", "warn", table_id, None, generated_headers, [],
                                 "at least half the column names were generated; confirm the table/header structure")
                for column_id, name, sql_type, kind, non_null, error_cells, original_header in cols:
                    p = _column_profile(src, table_name, name, sql_type, kind, int(row_count),
                                        cfg.analysis["top_k"], cfg.analysis["sample_values"])
                    con.execute("INSERT INTO _profile_columns VALUES (?,?,?,?,?,?,?,?,?)",
                                (column_id, p["n"], p["nulls"], p["distinct"],
                                 None if p["min"] is None else str(p["min"]),
                                 None if p["max"] is None else str(p["max"]),
                                 p["mean"], _json(p["top"]), _json(p["sample"])))

                    if row_count and p["nulls"] / row_count >= 0.5:
                        sev = "error" if p["nulls"] / row_count >= 0.9 else "warn"
                        _add_finding(con, "DQ_HIGH_NULL", sev, table_id, column_id, p["nulls"], p["sample"],
                                     f"{p['nulls']} of {row_count} rows are null")
                    if p["n"] > 1 and p["distinct"] == 1:
                        _add_finding(con, "DQ_CONSTANT", "info", table_id, column_id, p["n"], p["sample"],
                                     "column has one non-null value")
                    if int(error_cells or 0):
                        _add_finding(con, "DQ_EXCEL_ERRORS", "warn", table_id, column_id, int(error_cells), [],
                                     "Excel error cells were stored as NULL; inspect source _cell_errors")
                    if kind == "mixed":
                        _add_finding(con, "DQ_MIXED_TYPE", "warn", table_id, column_id, p["n"], p["sample"],
                                     "column contains mixed value types")
                    if original_header is None:
                        _add_finding(con, "DQ_GENERATED_HEADER", "info", table_id, column_id, 1, [name],
                                     "column name was generated because no header text was available")
                    if str(sql_type).upper() in ("TEXT", "BLOB") and p["n"]:
                        marks = ",".join("?" * len(NULL_TOKENS))
                        cnt = src.execute(
                            f"SELECT COUNT(*) FROM {q(table_name)} WHERE LOWER(TRIM(CAST({q(name)} AS TEXT))) "
                            f"IN ({marks})", NULL_TOKENS).fetchone()[0]
                        if cnt:
                            ex = src.execute(
                                f"SELECT DISTINCT {q(name)} FROM {q(table_name)} WHERE "
                                f"LOWER(TRIM(CAST({q(name)} AS TEXT))) IN ({marks}) LIMIT 3", NULL_TOKENS
                            ).fetchall()
                            _add_finding(con, "DQ_NULL_TOKEN", "warn", table_id, column_id, cnt,
                                         [x[0] for x in ex], "text values look like null markers; no coercion was applied")

                    if row_count > 1 and p["n"]:
                        uniq = p["distinct"] / max(1, row_count)
                        null_rate = p["nulls"] / max(1, row_count)
                        if uniq >= 0.98 and null_rate <= 0.02:
                            kid = hashlib.sha1(f"{table_id}|{column_id}".encode()).hexdigest()[:20]
                            con.execute("INSERT INTO _keys VALUES (?,?,?,?,?,?,?,?)",
                                        (kid, table_id, _json([column_id]), uniq, null_rate,
                                         "inferred", "single_column_uniqueness", min(1.0, uniq)))
                        if uniq >= 0.10 and null_rate <= 0.10:
                            candidate_cols.append((column_id, name, uniq, null_rate))

                # Try a small number of composite candidates only when there is no near-perfect single key.
                have_single = con.execute(
                    "SELECT 1 FROM _keys WHERE table_id=? AND method='single_column_uniqueness' LIMIT 1",
                    (table_id,)).fetchone()
                if not have_single and 1 < len(candidate_cols) <= 12 and row_count <= max(200_000, cfg.analysis["row_hash_max_rows"]):
                    for i in range(min(6, len(candidate_cols))):
                        for j in range(i + 1, min(6, len(candidate_cols))):
                            c1, n1, _, _ = candidate_cols[i]
                            c2, n2, _, _ = candidate_cols[j]
                            distinct_pair = src.execute(
                                f"SELECT COUNT(*) FROM (SELECT {q(n1)},{q(n2)} FROM {q(table_name)} "
                                f"WHERE {q(n1)} IS NOT NULL AND {q(n2)} IS NOT NULL GROUP BY {q(n1)},{q(n2)})"
                            ).fetchone()[0]
                            uniq = distinct_pair / max(1, row_count)
                            if uniq >= 0.98:
                                null_pair = src.execute(
                                    f"SELECT COUNT(*) FROM {q(table_name)} WHERE {q(n1)} IS NULL OR {q(n2)} IS NULL"
                                ).fetchone()[0] / max(1, row_count)
                                kid = hashlib.sha1(f"{table_id}|{c1}|{c2}".encode()).hexdigest()[:20]
                                con.execute("INSERT INTO _keys VALUES (?,?,?,?,?,?,?,?)",
                                            (kid, table_id, _json([c1, c2]), uniq, null_pair,
                                             "inferred", "composite_uniqueness", min(1.0, uniq)))
                                break
                        else:
                            continue
                        break

                totals_rows = _detect_totals_rows(src, table_name, text_columns, cfg.analysis["row_hash_max_rows"])
                for xl_row, label in totals_rows:
                    con.execute("INSERT OR REPLACE INTO _row_flags VALUES (?,?,?,?)",
                                (table_id, int(xl_row), "totals_candidate", label))
                if totals_rows:
                    _add_finding(con, "DQ_TOTALS_ROW_IN_DATA", "warn", table_id, None, len(totals_rows),
                                 [label for _, label in totals_rows[:3]],
                                 "row(s) labelled as a total/subtotal were found inside the data; a default "
                                 "aggregate over this table will double-count them unless excluded by _xl_row")

                kind, kind_conf, kind_method, kind_reasons = _classify_table_kind(
                    int(row_count), int(column_count or 0), header_row, formula_ratio, pivots, bool(totals_rows))
                con.execute("INSERT INTO _table_kind VALUES (?,?,?,?,?)",
                            (table_id, kind, kind_conf, kind_method, _json(kind_reasons)))
        con.commit()
    finally:
        con.close()
    return path


def main(argv=None):
    ap = argparse.ArgumentParser(prog="xl2ai analyze", description="Profile columns, detect generic quality issues, infer keys.")
    ap.add_argument("--config")
    ap.add_argument("--run")
    args = ap.parse_args(argv)
    try:
        cfg = load_config(args.config)
        rid = args.run or current_run_id(cfg)
        if not rid:
            raise Xl2aiError("E_STAGE_INPUT", "no current run")
        analyze_catalog(cfg, rid)
    except Xl2aiError as e:
        print(str(e), file=sys.stderr)
        return 1
    print(os.path.join(cfg.runs_dir, rid, "catalog.db"))
    return 0


if __name__ == "__main__":
    sys.exit(main())

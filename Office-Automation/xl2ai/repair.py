"""Opt-in, reversible repair suggestions: null-token normalization, whitespace/case category consolidation, and
text-as-number coercion.

Nothing here ever touches the extracted database or any table an agent queries directly. Every suggestion is
written to `_repairs` (original_value, repaired_value, rule) in the catalog and nowhere else -- the extracted
truth that was verified against Excel is never edited, so "repairs on" can never make output diverge from
"repairs off" for any tool except the ones that explicitly ask to see repaired values
(`xl2ai query repaired <table>`). This is a stronger safety property than "reversible via an undo log": there is
nothing to undo, because nothing was changed in place.

Detection (the underlying DQ_NULL_TOKEN etc. findings in `analyze.py`) always runs, regardless of this config.
Only turning a detected issue into a concrete before/after suggestion here is gated by `[repair]` in
`xl2ai.toml`, and even then only ever adds rows to `_repairs` -- it never writes to a data table.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys

from .analyze import NULL_TOKENS
from .core.config import load_config
from .core.errors import Xl2aiError
from .core.runs import current_run_id
from .core.sqliteutil import ro_connection

NUMERIC_TEXT_RE = re.compile(r"^-?[\d,]+\.?\d*$")


def q(name):
    return '"' + str(name).replace('"', '""') + '"'


def _repair_id(table_id, column_id, xl_row, rule):
    return hashlib.sha1(f"{table_id}|{column_id}|{xl_row}|{rule}".encode()).hexdigest()[:20]


def _suggest_null_tokens(src, table_name, column_id, name, max_rows):
    marks = ",".join("?" * len(NULL_TOKENS))
    rows = src.execute(
        f"SELECT _xl_row, {q(name)} FROM {q(table_name)} "
        f"WHERE LOWER(TRIM(CAST({q(name)} AS TEXT))) IN ({marks}) LIMIT ?",
        (*NULL_TOKENS, max_rows)).fetchall()
    return [(column_id, xl_row, str(val), None, "null_token") for xl_row, val in rows]


def _norm_category(value):
    return re.sub(r"\s+", " ", str(value).strip()).casefold()


def _suggest_category_consolidation(src, table_name, column_id, name, max_rows):
    """Map whitespace/case variants of the same value onto their most frequent spelling.

    A canonical form is chosen by frequency, not alphabetically: the goal is "what most of the workbook already
    calls this", not an arbitrary pick. Only variant groups with more than one distinct raw spelling matter --
    a single spelling used consistently needs no suggestion.
    """
    rows = src.execute(
        f"SELECT {q(name)}, COUNT(*) c FROM {q(table_name)} WHERE {q(name)} IS NOT NULL "
        f"GROUP BY {q(name)} ORDER BY c DESC LIMIT ?", (max_rows,)).fetchall()
    groups = {}
    for value, count in rows:
        groups.setdefault(_norm_category(value), []).append((value, count))
    out = []
    for norm, variants in groups.items():
        if len(variants) < 2:
            continue
        variants.sort(key=lambda vc: vc[1], reverse=True)
        canonical = variants[0][0]
        for value, _ in variants[1:]:
            if value == canonical:
                continue
            xl_rows = src.execute(f"SELECT _xl_row FROM {q(table_name)} WHERE {q(name)}=?", (value,)).fetchall()
            for (xl_row,) in xl_rows:
                out.append((column_id, xl_row, str(value), str(canonical), "category_consolidation"))
    return out


def _suggest_text_coercion(src, table_name, column_id, name, max_rows):
    rows = src.execute(
        f"SELECT _xl_row, {q(name)} FROM {q(table_name)} WHERE {q(name)} IS NOT NULL LIMIT ?",
        (max_rows,)).fetchall()
    out = []
    for xl_row, value in rows:
        if not isinstance(value, str):
            continue
        stripped = value.strip()
        if not stripped or not NUMERIC_TEXT_RE.match(stripped):
            continue
        candidate = stripped.replace(",", "")
        try:
            parsed = float(candidate)
        except ValueError:
            continue
        if parsed.is_integer() and "." not in candidate:
            parsed = int(parsed)
        repaired_text = str(parsed)
        if repaired_text == stripped:
            continue                                      # already a clean number as text; nothing to repair
        out.append((column_id, xl_row, value, repaired_text, "text_as_number"))
    return out


def build_repairs(cfg, run_id, catalog_path=None):
    import os
    run_dir = os.path.join(cfg.runs_dir, run_id)
    path = catalog_path or os.path.join(run_dir, "catalog.db")
    if not os.path.isfile(path):
        raise Xl2aiError("E_STAGE_INPUT", f"catalog not found: {path}", "run analyze first")

    con = sqlite3.connect(path)
    try:
        con.execute("DELETE FROM _repairs")
        if not cfg.repair["enabled"]:
            con.commit()
            return path

        max_rows = cfg.analysis["row_hash_max_rows"]
        tables = con.execute("SELECT table_id, table_name, db_rel FROM _tables").fetchall()
        for table_id, table_name, db_rel in tables:
            src_path = os.path.join(run_dir, db_rel.replace("/", os.sep))
            cols = con.execute(
                "SELECT column_id, name, sql_type FROM _columns WHERE table_id=?", (table_id,)).fetchall()
            with ro_connection(src_path) as src:
                for column_id, name, sql_type in cols:
                    if str(sql_type).upper() not in ("TEXT", "BLOB"):
                        continue
                    suggestions = []
                    if cfg.repair["null_tokens"]:
                        suggestions += _suggest_null_tokens(src, table_name, column_id, name, max_rows)
                    if cfg.repair["category_consolidation"]:
                        suggestions += _suggest_category_consolidation(src, table_name, column_id, name, max_rows)
                    if cfg.repair["text_coercion"]:
                        suggestions += _suggest_text_coercion(src, table_name, column_id, name, max_rows)
                    for col_id, xl_row, original, repaired, rule in suggestions:
                        rid = _repair_id(table_id, col_id, xl_row, rule)
                        con.execute("INSERT OR REPLACE INTO _repairs VALUES (?,?,?,?,?,?,?)",
                                    (rid, table_id, col_id, xl_row, original, repaired, rule))
        con.commit()
    finally:
        con.close()
    return path


def main(argv=None):
    ap = argparse.ArgumentParser(prog="xl2ai repair",
                                 description="Suggest opt-in, reversible repairs (null tokens, category "
                                             "spelling, text-as-number) without touching any data table.")
    ap.add_argument("--config")
    ap.add_argument("--run")
    args = ap.parse_args(argv)
    try:
        cfg = load_config(args.config)
        rid = args.run or current_run_id(cfg)
        if not rid:
            raise Xl2aiError("E_STAGE_INPUT", "no current run")
        path = build_repairs(cfg, rid)
    except Xl2aiError as e:
        print(str(e), file=sys.stderr)
        return 1
    if not cfg.repair["enabled"]:
        print(f"{path}\n[repair].enabled is false: no suggestions were generated (detection in `analyze` still runs)")
        return 0
    con = sqlite3.connect(path)
    n = con.execute("SELECT COUNT(*) FROM _repairs").fetchone()[0]
    con.close()
    print(f"{path}\n{n} repair suggestion(s); see `xl2ai query meta repairs` or `xl2ai query repaired <table>`")
    return 0


if __name__ == "__main__":
    sys.exit(main())

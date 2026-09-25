"""The semantic layer: column roles, units/currency, table grain, time coverage, auto-drafted definitions, and
cross-file duplicate/version hints.

Everything here is derived from catalog metadata `analyze`/`relations` already computed -- no Excel is re-read,
no AI model is called. Every label is a deterministic rule with a method and a confidence, and stays `inferred`
(never `confirmed`) until a human declares it in a pack (see BUSINESS_RULES.md). This is deliberate: a wrong
`inferred` grain or role is a visible, checkable claim; a wrong `confirmed` one would be silently trusted by
every agent downstream.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys

from .core.config import load_config
from .core.errors import Xl2aiError
from .core.runs import current_run_id

CURRENCY_TOKENS = {
    "egp": "EGP", "e.g.p": "EGP", "جنيه": "EGP", "ج.م": "EGP", "ج.م.": "EGP",
    "usd": "USD", "$": "USD", "دولار": "USD",
    "eur": "EUR", "€": "EUR", "يورو": "EUR",
    "sar": "SAR", "ريال": "SAR",
}

IDENTIFIER_WORDS = ("id", "code", "no", "number", "ref", "reference", "key", "رقم", "كود")
MONEY_WORDS = ("price", "amount", "cost", "salary", "revenue", "fee", "charge", "balance", "total", "value",
              "سعر", "مبلغ", "تكلفة", "راتب", "إيراد", "قيمة", "رصيد")
PERCENT_WORDS = ("percent", "pct", "rate", "ratio", "نسبة", "معدل")
QUANTITY_WORDS = ("quantity", "count", "units", "stock", "عدد", "كمية")
GEO_WORDS = ("nation", "city", "region", "governorate", "address", "location", "محافظة", "مدينة", "عنوان", "دولة")
CONTACT_WORDS = ("email", "phone", "mobile", "fax", "تليفون", "بريد", "هاتف", "جوال")
BOOL_TOKENS = {"true", "false", "yes", "no", "y", "n", "0", "1"}


def _norm(name):
    # [_\W]+ splits on underscore too (snake_case headers like "order_id" must tokenize to "order", "id"),
    # while keeping Unicode letters -- \W (not \w) matches non-word characters but is still Unicode-aware on
    # str patterns, so Arabic column names survive this instead of being blanked out by an ASCII-only class.
    return re.sub(r"[_\W]+", " ", str(name).lower()).strip()


def _any_word(text, words):
    """Whole-token match, not substring: "notes" must not match the token "no", "code" must not match "co"."""
    tokens = set(text.split())
    return any(w in tokens for w in words)


def detect_unit_currency(column_name):
    """(unit, currency) guessed from the column NAME only -- never from values, which is far less reliable
    without also parsing number formats this platform does not yet capture (see EDGE_CASES.md)."""
    low = _norm(column_name)
    currency = None
    for token, code in CURRENCY_TOKENS.items():
        if token in low or token in str(column_name).lower():
            currency = code
            break
    unit = "currency" if currency else None
    if not unit and _any_word(low, PERCENT_WORDS):
        unit = "percent"
    return unit, currency


def classify_column_role(name, sql_type, kind, n, nulls, distinct, sample):
    """(role, confidence, method, [reason,...]).

    A small explicit rule set, checked in priority order, over signals `analyze` already computed. Never a model
    call: the same inputs always give the same role, and every role carries the reason it was chosen so an agent
    (or a human writing a pack) can see exactly why and override it if wrong.
    """
    low = _norm(name)
    sql_type = str(sql_type).upper()
    non_null = max(1, n)
    uniq = distinct / non_null if non_null else 0.0

    if kind in ("date", "datetime", "time"):
        return "date", 0.95, "heuristic", [f"extractor-detected {kind} column"]

    if kind == "bool" or (distinct and distinct <= 2 and sample and
                          all(str(v).strip().lower() in BOOL_TOKENS for v in sample if v is not None)):
        return "boolean", 0.8, "heuristic", ["at most two distinct values, all boolean-like"]

    # A name match to "id"/"code"/... wins outright: it is a stronger, more specific signal than uniqueness
    # alone, and must be checked before any type-specific branch below (an "order_id" is an identifier even
    # if it happens to also be numeric and would otherwise read as a quantity).
    if uniq >= 0.9 and non_null > 1 and _any_word(low, IDENTIFIER_WORDS):
        return "identifier", 0.9, "heuristic", [f"{uniq:.0%} unique", "name suggests an identifier"]

    if sql_type in ("INTEGER", "REAL"):
        if _any_word(low, IDENTIFIER_WORDS):
            # a repeating number named like an id ("customer_id" in an orders table) is a reference to something,
            # never an amount -- summing it would be meaningless
            return "code", 0.6, "heuristic", ["numeric column whose name suggests a code/reference, not an amount"]
        if _any_word(low, MONEY_WORDS):
            return "money", 0.75, "heuristic", ["numeric column whose name suggests a monetary value"]
        if _any_word(low, PERCENT_WORDS):
            return "percentage", 0.7, "heuristic", ["numeric column whose name suggests a rate/percentage"]
        if _any_word(low, QUANTITY_WORDS):
            return "quantity", 0.65, "heuristic", ["numeric column whose name suggests a count/quantity"]
        if uniq >= 0.98 and non_null > 1:
            return "identifier", 0.5, "heuristic", [f"{uniq:.0%} unique, no more specific name match"]
        return "quantity", 0.3, "heuristic", ["numeric column with no more specific name match"]

    if sql_type in ("TEXT", "BLOB"):
        # A specific name match (this is a place, this is contact info) beats a generic repetition pattern:
        # a "city" column with few distinct values is still a place, not a bucket of arbitrary labels.
        if _any_word(low, CONTACT_WORDS):
            return "contact", 0.7, "heuristic", ["text column whose name suggests contact information"]
        if _any_word(low, GEO_WORDS):
            return "geo", 0.7, "heuristic", ["text column whose name suggests a place"]
        if _any_word(low, IDENTIFIER_WORDS):
            return "code", 0.55, "heuristic", ["text column whose name suggests a code/reference"]
        if non_null and uniq <= 0.5 and distinct <= 50:
            return "category", 0.6, "heuristic", [f"{distinct} distinct value(s) over {non_null} row(s)"]
        if uniq >= 0.98 and non_null > 1:
            return "identifier", 0.4, "heuristic", [f"{uniq:.0%} unique text, no more specific name match"]
        if non_null and uniq >= 0.7:
            return "free_text", 0.5, "heuristic", [f"{uniq:.0%} unique text, no category/identifier pattern matched"]

    return "unknown", 0.0, "heuristic", ["no rule matched confidently"]


def _table_grain(con, table_id, table_name):
    """(columns[], description, status, confidence, method). `status` is `inferred` or `unconfirmed`.

    Grain is the single fact that makes or breaks any aggregation an agent runs, so an unclear grain is stated
    as unclear rather than guessed: silence here would be worse than no grain detection at all.
    """
    key = con.execute(
        """SELECT columns_json,uniqueness,status,method FROM _keys WHERE table_id=?
           ORDER BY CASE status WHEN 'confirmed' THEN 0 ELSE 1 END, score DESC LIMIT 1""",
        (table_id,)).fetchone()
    if not key or float(key[1] or 0) < 0.9:
        return [], (f"grain not confirmed for '{table_name}': no column or column combination was found that "
                    "uniquely identifies a row with high confidence; aggregating this table may double- or "
                    "under-count depending on what one row actually represents"), "unknown", 0.0, "none"
    columns_json, uniqueness, status, method = key
    col_ids = json.loads(columns_json)
    names = [r[0] for cid in col_ids for r in con.execute(
        "SELECT name FROM _columns WHERE column_id=?", (cid,)).fetchall()]
    label = " + ".join(names) if names else ", ".join(col_ids)
    conf_word = "confirmed" if status == "confirmed" else "inferred"
    description = f"one row per {table_name}, identified by {label} ({conf_word}, {uniqueness:.0%} unique)"
    return col_ids, description, ("confirmed" if status == "confirmed" else "inferred"), float(uniqueness), method


def _time_coverage(con, table_id):
    """[(column_id, min, max)] for every date/datetime column already profiled by analyze."""
    return con.execute(
        """SELECT p.column_id, p.min_value, p.max_value
           FROM _profile_columns p JOIN _columns c ON c.column_id=p.column_id
           WHERE c.table_id=? AND c.kind IN ('date','datetime','time') AND p.min_value IS NOT NULL""",
        (table_id,)).fetchall()


def _draft_table_definition(con, table_id, table_name, source_id, row_count, grain_desc, coverage):
    parts = [f"'{table_name}' ({source_id}): {row_count} row(s)", grain_desc]
    if coverage:
        spans = [f"{c[1]} to {c[2]}" for c in coverage]
        parts.append("time coverage: " + "; ".join(spans))
    return ". ".join(parts) + "."


def _detect_duplicates(con):
    """[(table_id_a, table_id_b, method, score, evidence)] for tables that look like copies of each other.

    Same schema fingerprint is a strong signal on its own; row-hash overlap (already computed by analyze's
    row-multiset fingerprinting) adds a content-based check that survives a renamed sheet or source file.
    """
    found = []
    rows = con.execute("SELECT table_id, schema_fingerprint, source_id FROM _tables").fetchall()
    by_fp = {}
    for tid, fp, sid in rows:
        by_fp.setdefault(fp, []).append((tid, sid))
    for fp, group in by_fp.items():
        if len(group) < 2:
            continue
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                (ta, sa), (tb, sb) = group[i], group[j]
                if sa == sb:
                    continue                              # same workbook, same schema: not a cross-file duplicate
                overlap = _row_hash_overlap(con, ta, tb)
                evidence = {"schema_fingerprint": fp, "row_hash_overlap": overlap}
                found.append((ta, tb, "schema_fingerprint", 0.5 + 0.5 * overlap, json.dumps(evidence)))
    return found


def _row_hash_overlap(con, table_a, table_b):
    a = {r[0] for r in con.execute("SELECT row_hash FROM _row_hashes WHERE table_id=?", (table_a,)).fetchall()}
    b = {r[0] for r in con.execute("SELECT row_hash FROM _row_hashes WHERE table_id=?", (table_b,)).fetchall()}
    if not a or not b:
        return 0.0
    return len(a & b) / max(1, min(len(a), len(b)))


def build_semantics(cfg, run_id, catalog_path=None):
    import os
    run_dir = os.path.join(cfg.runs_dir, run_id)
    path = catalog_path or os.path.join(run_dir, "catalog.db")
    if not os.path.isfile(path):
        raise Xl2aiError("E_STAGE_INPUT", f"catalog not found: {path}", "run analyze first")

    con = sqlite3.connect(path)
    try:
        con.execute("DELETE FROM _column_roles")
        con.execute("DELETE FROM _table_grain")
        con.execute("DELETE FROM _time_coverage")
        con.execute("DELETE FROM _duplicate_candidates")
        con.execute("DELETE FROM _dictionary WHERE origin='auto'")

        tables = con.execute("""SELECT table_id,table_name,source_id,row_count FROM _tables""").fetchall()
        for table_id, table_name, source_id, row_count in tables:
            cols = con.execute("""SELECT column_id,name,sql_type,kind FROM _columns
                                  WHERE table_id=? ORDER BY position""", (table_id,)).fetchall()
            for column_id, name, sql_type, kind in cols:
                prof = con.execute(
                    "SELECT n,nulls,distinct_count,sample FROM _profile_columns WHERE column_id=?",
                    (column_id,)).fetchone()
                n, nulls, distinct, sample_json = prof if prof else (0, 0, 0, "[]")
                sample = json.loads(sample_json or "[]")
                role, conf, method, reasons = classify_column_role(name, sql_type, kind, n, nulls, distinct, sample)
                unit, currency = detect_unit_currency(name)
                con.execute("INSERT INTO _column_roles VALUES (?,?,?,?,?,?,?,?)",
                            (column_id, table_id, role, conf, method, json.dumps(reasons, ensure_ascii=False),
                             unit, currency))
                if role in ("money", "quantity", "percentage") and (unit or currency):
                    # PRIMARY KEY is (term, pack): pack is namespaced per column so two columns that happen to
                    # share a name (e.g. "amount" in two different tables) never overwrite each other's entry.
                    con.execute("INSERT OR REPLACE INTO _dictionary VALUES (?,?,?,?,?,?,?,?,?)",
                                (name, f"{role} column" + (f" in {currency}" if currency else ""), "[]",
                                 currency or unit, column_id, "inferred", "auto", f"auto/{column_id}", None))

            grain_cols, grain_desc, grain_status, grain_conf, grain_method = _table_grain(con, table_id, table_name)
            con.execute("INSERT INTO _table_grain VALUES (?,?,?,?,?,?)",
                        (table_id, json.dumps(grain_cols), grain_desc, grain_status, grain_conf, grain_method))

            coverage = _time_coverage(con, table_id)
            for column_id, min_v, max_v in coverage:
                con.execute("INSERT OR REPLACE INTO _time_coverage VALUES (?,?,?,?)",
                            (table_id, column_id, min_v, max_v))

            definition = _draft_table_definition(con, table_id, table_name, source_id, row_count, grain_desc, coverage)
            con.execute("INSERT OR REPLACE INTO _dictionary VALUES (?,?,?,?,?,?,?,?,?)",
                        (table_name, definition, "[]", None, table_id, "inferred", "auto", f"auto/{table_id}", None))

        for ta, tb, method, score, evidence in _detect_duplicates(con):
            did = hashlib.sha1(f"{ta}|{tb}|{method}".encode()).hexdigest()[:20]
            con.execute("INSERT OR REPLACE INTO _duplicate_candidates VALUES (?,?,?,?,?,?)",
                        (did, ta, tb, method, score, evidence))
        con.commit()
    finally:
        con.close()
    return path


def main(argv=None):
    ap = argparse.ArgumentParser(prog="xl2ai semantics",
                                 description="Infer column roles, units, table grain, time coverage and draft definitions.")
    ap.add_argument("--config")
    ap.add_argument("--run")
    args = ap.parse_args(argv)
    try:
        cfg = load_config(args.config)
        rid = args.run or current_run_id(cfg)
        if not rid:
            raise Xl2aiError("E_STAGE_INPUT", "no current run")
        build_semantics(cfg, rid)
    except Xl2aiError as e:
        print(str(e), file=sys.stderr)
        return 1
    import os
    print(os.path.join(cfg.runs_dir, rid, "catalog.db"))
    return 0


if __name__ == "__main__":
    sys.exit(main())

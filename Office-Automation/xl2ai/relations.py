"""Infer conservative table relationships from candidate keys, names/types, and sampled value containment."""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import re
import sqlite3
import sys

from .core.config import load_config
from .core.errors import Xl2aiError
from .core.runs import current_run_id


def q(name):
    return '"' + str(name).replace('"', '""') + '"'


def _norm(name):
    return re.sub(r"[^a-z0-9]+", "", str(name).lower())


def _family(sql_type, kind):
    if kind in ("date", "datetime", "time"):
        return "date"
    if str(sql_type).upper() in ("INTEGER", "REAL"):
        return "number"
    return "text"


def _sample_values(src, table, column, limit):
    return [r[0] for r in src.execute(
        f"SELECT DISTINCT {q(column)} FROM {q(table)} WHERE {q(column)} IS NOT NULL LIMIT ?", (limit,)
    ).fetchall()]


def _matched(parent_src, table, column, values):
    if not values:
        return 0
    matched = set()
    for i in range(0, len(values), 400):
        chunk = values[i:i+400]
        marks = ",".join("?" * len(chunk))
        rows = parent_src.execute(
            f"SELECT DISTINCT {q(column)} FROM {q(table)} WHERE {q(column)} IN ({marks})", chunk
        ).fetchall()
        matched.update(r[0] for r in rows)
    return len(matched)


def infer_relations(cfg, run_id, catalog_path=None):
    run_dir = os.path.join(cfg.runs_dir, run_id)
    path = catalog_path or os.path.join(run_dir, "catalog.db")
    if not os.path.isfile(path):
        raise Xl2aiError("E_STAGE_INPUT", "catalog not found", "run catalog/analyze first")
    con = sqlite3.connect(path)
    src_cache = {}

    def source(db_rel):
        p = os.path.join(run_dir, db_rel.replace("/", os.sep))
        if p not in src_cache:
            src_cache[p] = sqlite3.connect(f"file:{os.path.abspath(p)}?mode=ro", uri=True)
        return src_cache[p]

    try:
        # Only clear previously INFERRED relationships. Pack-confirmed relationships (written by the rules stage,
        # which now runs before this one) are human knowledge and must survive a re-run of inference.
        con.execute("DELETE FROM _relationships WHERE status='inferred'")
        cols = con.execute("""SELECT c.column_id,c.table_id,c.name,c.sql_type,c.kind,t.table_name,t.db_rel,
                                     COALESCE(p.distinct_count,0),t.row_count
                              FROM _columns c JOIN _tables t ON t.table_id=c.table_id
                              LEFT JOIN _profile_columns p ON p.column_id=c.column_id
                              ORDER BY c.column_id""").fetchall()
        by_id = {r[0]: r for r in cols}
        # A human-confirmed key is fully trusted regardless of its measured uniqueness (which may be NULL: packs
        # apply before any profiling of that exact column pair existed). A generic inferred key still needs a
        # near-perfect uniqueness score before it is trusted as a relationship target.
        key_rows = con.execute(
            "SELECT table_id,columns_json,uniqueness,status FROM _keys "
            "WHERE status='confirmed' OR (status='inferred' AND uniqueness>=0.98)"
        ).fetchall()
        parents = []
        for table_id, columns_json, uniqueness, status in key_rows:
            ids = json.loads(columns_json)
            if len(ids) == 1 and ids[0] in by_id:
                trust = 1.0 if status == "confirmed" else float(uniqueness)
                parents.append((by_id[ids[0]], trust))
        for child in cols:
            child_id, child_table_id, child_name, child_type, child_kind, child_table, child_db, child_distinct, child_rows = child
            min_domain = 3 if int(child_rows or 0) < 50 else 5
            if int(child_distinct or 0) < min_domain:
                continue
            nf = _norm(child_name)
            for parent, uniqueness in parents:
                parent_id, parent_table_id, parent_name, parent_type, parent_kind, parent_table, parent_db, _, _ = parent
                if child_table_id == parent_table_id:
                    continue
                if _family(child_type, child_kind) != _family(parent_type, parent_kind):
                    continue
                np = _norm(parent_name)
                name_score = 1.0 if nf == np and nf else difflib.SequenceMatcher(None, nf, np).ratio()
                if name_score < 0.82:
                    continue
                cs, ps = source(child_db), source(parent_db)
                vals = _sample_values(cs, child_table, child_name, cfg.analysis["relation_sample"])
                if len(vals) < min_domain:
                    continue
                match = _matched(ps, parent_table, parent_name, vals)
                containment = match / len(vals)
                if containment < 0.90:
                    continue
                score = min(1.0, 0.65 * containment + 0.25 * name_score + 0.10 * uniqueness)
                rid = hashlib.sha1(f"{child_id}|{parent_id}".encode()).hexdigest()[:20]
                evidence = json.dumps({"sampled": len(vals), "matched": match, "name_score": round(name_score,4)},
                                      separators=(",", ":"))
                # OR IGNORE: a pack may already have confirmed this exact column pair (same id); the confirmed
                # row always wins and is never overwritten by a freshly inferred guess.
                con.execute("INSERT OR IGNORE INTO _relationships VALUES (?,?,?,?,?,?,?,?,?)",
                            (rid, child_id, parent_id, "inclusion", containment, "inferred",
                             "key_name_containment", score, evidence))
        con.commit()
    finally:
        for c in src_cache.values():
            c.close()
        con.close()
    return path


def main(argv=None):
    ap = argparse.ArgumentParser(prog="xl2ai relations", description="Infer conservative relationships between tables.")
    ap.add_argument("--config")
    ap.add_argument("--run")
    args = ap.parse_args(argv)
    try:
        cfg = load_config(args.config)
        rid = args.run or current_run_id(cfg)
        if not rid:
            raise Xl2aiError("E_STAGE_INPUT", "no current run")
        infer_relations(cfg, rid)
    except Xl2aiError as e:
        print(str(e), file=sys.stderr)
        return 1
    print(os.path.join(cfg.runs_dir, rid, "catalog.db"))
    return 0


if __name__ == "__main__":
    sys.exit(main())

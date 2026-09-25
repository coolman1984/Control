"""Compare a run with the previous trusted run: sources, schema, volume, content fingerprints, categories and KPIs."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys

from .core.config import load_config
from .core.errors import Xl2aiError
from .core.fsutil import read_json
from .core.runs import current_run_id


def _id(kind, subject):
    return hashlib.sha1(f"{kind}|{subject}".encode()).hexdigest()[:20]


def _previous(cfg, run_id):
    cur = current_run_id(cfg)
    if cur and cur != run_id:
        return cur
    try:
        ptr = read_json(cfg.current_file)
        return ptr.get("previous") if ptr.get("run_id") == run_id else cur
    except (OSError, ValueError):
        return cur


def _insert(con, kind, severity, subject, before, after, evidence=None):
    con.execute("INSERT OR REPLACE INTO _changes VALUES (?,?,?,?,?,?,?)",
                (_id(kind, subject), kind, severity, subject,
                 json.dumps(before, ensure_ascii=False, separators=(",", ":"), default=str),
                 json.dumps(after, ensure_ascii=False, separators=(",", ":"), default=str),
                 json.dumps(evidence or {}, ensure_ascii=False, separators=(",", ":"), default=str)))


def detect_changes(cfg, run_id, previous_run_id=None, catalog_path=None):
    run_dir = os.path.join(cfg.runs_dir, run_id)
    path = catalog_path or os.path.join(run_dir, "catalog.db")
    if not os.path.isfile(path):
        raise Xl2aiError("E_STAGE_INPUT", "current catalog not found")
    prev_id = previous_run_id if previous_run_id is not None else _previous(cfg, run_id)
    con = sqlite3.connect(path)
    try:
        con.execute("DELETE FROM _changes")
        if not prev_id:
            _insert(con, "baseline", "info", "run", None, run_id)
            con.commit()
            return path
        prev_path = os.path.join(cfg.runs_dir, prev_id, "catalog.db")
        if not os.path.isfile(prev_path):
            _insert(con, "baseline", "info", "run", prev_id, run_id, {"reason": "previous catalog unavailable"})
            con.commit()
            return path
        prev = sqlite3.connect(f"file:{os.path.abspath(prev_path)}?mode=ro", uri=True)
        try:
            old_sources = {r[0]: r[1] for r in prev.execute("SELECT source_id,sha256 FROM _sources")}
            new_sources = {r[0]: r[1] for r in con.execute("SELECT source_id,sha256 FROM _sources")}
            for sid in sorted(set(old_sources) | set(new_sources)):
                if sid not in old_sources:
                    _insert(con, "source", "info", sid, None, new_sources[sid])
                elif sid not in new_sources:
                    _insert(con, "source", "warn", sid, old_sources[sid], None)
                elif old_sources[sid] != new_sources[sid]:
                    _insert(con, "source", "info", sid, old_sources[sid], new_sources[sid])

            def table_rows(db):
                return db.execute("""SELECT table_id,source_id,sheet_name,row_count,schema_fingerprint
                                     FROM _tables ORDER BY source_id,sheet_name""").fetchall()

            old_rows, new_rows = table_rows(prev), table_rows(con)
            old_exact = {(r[1], r[2]): r for r in old_rows}
            new_exact = {(r[1], r[2]): r for r in new_rows}
            pairs, used_old, used_new = [], set(), set()

            for key in sorted(set(old_exact) & set(new_exact)):
                pairs.append((f"{key[0]}/{key[1]}", old_exact[key], new_exact[key]))
                used_old.add(old_exact[key][0])
                used_new.add(new_exact[key][0])

            # Rename detection: unmatched table with same source and identical schema fingerprint.
            for n in new_rows:
                if n[0] in used_new:
                    continue
                candidates = [o for o in old_rows if o[0] not in used_old and o[1] == n[1] and o[4] == n[4]]
                if len(candidates) == 1:
                    o = candidates[0]
                    subject = f"{n[1]}/{o[2]}->{n[2]}"
                    _insert(con, "schema", "info", subject, {"sheet": o[2]}, {"sheet": n[2]},
                            {"rename_detected": True, "schema_fingerprint": n[4]})
                    pairs.append((subject, o, n))
                    used_old.add(o[0])
                    used_new.add(n[0])

            for o in old_rows:
                if o[0] not in used_old:
                    _insert(con, "schema", "warn", f"{o[1]}/{o[2]}", {"table_id": o[0]}, None)
            for n in new_rows:
                if n[0] not in used_new:
                    _insert(con, "schema", "info", f"{n[1]}/{n[2]}", None, {"table_id": n[0]})

            for subject, o, n in pairs:
                if o[4] != n[4]:
                    _insert(con, "schema", "warn", subject, o[4], n[4])
                if int(o[3]) != int(n[3]):
                    base = max(1, int(o[3]))
                    ratio = abs(int(n[3]) - int(o[3])) / base
                    _insert(con, "volume", "warn" if ratio >= 0.2 else "info", subject, int(o[3]), int(n[3]),
                            {"change_ratio": round(ratio, 6)})
                old_fp = prev.execute("SELECT row_fingerprint,row_hash_mode FROM _table_profiles WHERE table_id=?", (o[0],)).fetchone()
                new_fp = con.execute("SELECT row_fingerprint,row_hash_mode FROM _table_profiles WHERE table_id=?", (n[0],)).fetchone()
                if old_fp and new_fp and old_fp[0] and new_fp[0] and old_fp[0] != new_fp[0]:
                    _insert(con, "value", "info", subject, old_fp[0], new_fp[0],
                            {"mode_before": old_fp[1], "mode_after": new_fp[1]})

                old_hashes = dict(prev.execute("SELECT row_hash,n FROM _row_hashes WHERE table_id=?", (o[0],)).fetchall())
                new_hashes = dict(con.execute("SELECT row_hash,n FROM _row_hashes WHERE table_id=?", (n[0],)).fetchall())
                if old_hashes or new_hashes:
                    added = sum(max(0, new_hashes.get(h, 0) - old_hashes.get(h, 0))
                                for h in set(old_hashes) | set(new_hashes))
                    removed = sum(max(0, old_hashes.get(h, 0) - new_hashes.get(h, 0))
                                  for h in set(old_hashes) | set(new_hashes))
                    if added or removed:
                        _insert(con, "row", "info", subject, {"removed": removed}, {"added": added},
                                {"method": "row_hash_multiset", "content_not_stored": True})

                old_cols = {r[0]: (r[1], int(r[2] or 0)) for r in prev.execute(
                    """SELECT c.name,p.top_k,p.distinct_count
                       FROM _columns c JOIN _profile_columns p ON p.column_id=c.column_id
                       WHERE c.table_id=?""", (o[0],))}
                new_cols = {r[0]: (r[1], int(r[2] or 0)) for r in con.execute(
                    """SELECT c.name,p.top_k,p.distinct_count
                       FROM _columns c JOIN _profile_columns p ON p.column_id=c.column_id
                       WHERE c.table_id=?""", (n[0],))}
                for col in sorted(set(old_cols) & set(new_cols)):
                    old_top_raw, old_distinct = old_cols[col]
                    new_top_raw, new_distinct = new_cols[col]
                    if old_top_raw == new_top_raw:
                        continue
                    old_top = json.loads(old_top_raw or "[]")
                    new_top = json.loads(new_top_raw or "[]")
                    old_values = [x[0] for x in old_top]
                    new_values = [x[0] for x in new_top]
                    old_complete = old_distinct <= len(old_top)
                    new_complete = new_distinct <= len(new_top)
                    item_subject = f"{subject}.{col}"
                    if old_complete and new_complete and set(map(str, old_values)) != set(map(str, new_values)):
                        _insert(con, "category", "info", item_subject, old_values, new_values,
                                {"method": "complete_domain", "old_distinct": old_distinct,
                                 "new_distinct": new_distinct})
                    else:
                        _insert(con, "distribution", "info", item_subject, old_top, new_top,
                                {"method": "top_k_frequency",
                                 "complete_domain_before": old_complete,
                                 "complete_domain_after": new_complete})

            old_k = {(r[0],r[1]): r[2] for r in prev.execute("SELECT kpi_id,pack,value FROM _kpi_results")}
            new_k = {(r[0],r[1]): r[2] for r in con.execute("SELECT kpi_id,pack,value FROM _kpi_results")}
            for key in sorted(set(old_k) | set(new_k)):
                subject = f"{key[1]}/{key[0]}"
                if old_k.get(key) != new_k.get(key):
                    _insert(con, "kpi", "info", subject, old_k.get(key), new_k.get(key))
        finally:
            prev.close()
        con.commit()
    finally:
        con.close()
    return path


def main(argv=None):
    ap = argparse.ArgumentParser(prog="xl2ai changes", description="Compare a run with its previous trusted run.")
    ap.add_argument("--config")
    ap.add_argument("--run")
    ap.add_argument("--previous")
    args = ap.parse_args(argv)
    try:
        cfg = load_config(args.config)
        rid = args.run or current_run_id(cfg)
        if not rid:
            raise Xl2aiError("E_STAGE_INPUT", "no current run")
        detect_changes(cfg, rid, args.previous)
    except Xl2aiError as e:
        print(str(e), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

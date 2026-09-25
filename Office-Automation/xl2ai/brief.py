"""`xl2ai brief`: the one call a cold agent makes first.

Answers, in a single bounded call: which run is current, is it fresh, is each table safe to use, what the
platform could not fully resolve, and what to run next. Everything here is derived from artifacts other stages
already produced (manifest, catalog, quality findings, rule results) -- brief computes no new facts of its own.

Exit codes: 0 ready | 1 needs review (usable, but read the gaps first) | 2 not ready (stale, unpromoted or failed).
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys

from .core.config import load_config
from .core.errors import Xl2aiError
from .core.runs import current_run_id, load_manifest
from .status import compute_status


def _table_columns(con, table):
    try:
        return {r[1] for r in con.execute(f"PRAGMA table_info({table})").fetchall()}
    except sqlite3.DatabaseError:
        return set()


def build_brief(cfg, run_id=None, in_progress=False):
    """`in_progress`: the run is still being built (the refresh's own agent-brief stage). Its sources were
    fingerprinted moments ago and it is promoted only if every stage passes, so "stale"/"not promoted" would
    describe the build, not the data -- those two checks are skipped instead of reported as false gaps."""
    status, status_code = compute_status(cfg, deep=False)
    if in_progress:
        status = dict(status, sources_fresh=True)
    rid = run_id or current_run_id(cfg)
    out = {"project": cfg.project, "run_id": rid, "fresh": status.get("sources_fresh"),
           "status_code": status_code, "tables": [], "gaps": [], "next_commands": []}
    if not rid:
        out["gaps"].append({"kind": "no_run", "message": "no run has ever been promoted"})
        out["next_commands"].append("xl2ai refresh")
        return out, 2
    manifest = load_manifest(cfg, rid)
    run_dir = os.path.join(cfg.runs_dir, rid)
    catalog_path = os.path.join(run_dir, "catalog.db")
    if not os.path.isfile(catalog_path):
        out["gaps"].append({"kind": "no_catalog", "message": f"run {rid} has no catalog.db"})
        out["next_commands"].append("xl2ai refresh")
        return out, 2

    extract_stage = next((s for s in manifest.get("stages", []) if s["name"] == "extract"), None)
    verify_mismatches = {r["source_id"]: int(r.get("verify_mismatches", 0) or 0)
                          for r in (extract_stage or {}).get("details", {}).get("sources", [])}

    con = sqlite3.connect(f"file:{os.path.abspath(catalog_path)}?mode=ro", uri=True)
    try:
        existing = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        has_unsupported = "_unsupported" in existing
        has_row_flags = "_row_flags" in existing
        has_table_kind = "_table_kind" in existing
        has_grain = "_table_grain" in existing
        has_regions = "_regions" in existing
        has_lineage = "_lineage" in existing
        has_anomalies = "_anomalies" in existing
        has_recon = "_reconciliation" in existing
        for tid, sid, sheet, table_name, rows in con.execute(
            "SELECT table_id,source_id,sheet_name,table_name,row_count FROM _tables ORDER BY table_id"):
            errors = con.execute(
                "SELECT COUNT(*) FROM _dq_findings WHERE table_id=? AND severity='error'", (tid,)).fetchone()[0]
            warnings = con.execute(
                "SELECT COUNT(*) FROM _dq_findings WHERE table_id=? AND severity='warn'", (tid,)).fetchone()[0]
            blind_spots = 0
            if has_unsupported:
                blind_spots = con.execute(
                    "SELECT COUNT(*) FROM _unsupported WHERE table_id=?", (tid,)).fetchone()[0]
            totals_rows = 0
            if has_row_flags:
                totals_rows = con.execute(
                    "SELECT COUNT(*) FROM _row_flags WHERE table_id=? AND flag='totals_candidate'",
                    (tid,)).fetchone()[0]
            kind = None
            if has_table_kind:
                row = con.execute("SELECT kind FROM _table_kind WHERE table_id=?", (tid,)).fetchone()
                kind = row[0] if row else None
            grain_status = None
            if has_grain:
                row = con.execute("SELECT status FROM _table_grain WHERE table_id=?", (tid,)).fetchone()
                grain_status = row[0] if row else None
            regions = 0
            if has_regions:
                regions = con.execute("SELECT COUNT(*) FROM _regions WHERE table_id=? AND kind='table'",
                                      (tid,)).fetchone()[0]
            feeds_from, external_refs = [], 0
            if has_lineage:
                for kind_, book, ref_sheet, target, status_ in con.execute(
                        "SELECT DISTINCT ref_kind, ref_workbook, ref_sheet, target_table_id, status FROM _lineage "
                        "WHERE table_id=? ORDER BY ref_workbook, ref_sheet", (tid,)):
                    feeds_from.append({"table_id": target, "workbook": book, "sheet": ref_sheet, "status": status_})
                    external_refs += status_ != "resolved"
            anomalies = 0
            if has_anomalies:
                anomalies = con.execute("SELECT COUNT(*) FROM _anomalies WHERE table_id=? AND severity='warn'",
                                        (tid,)).fetchone()[0]
            disagreements = []
            if has_recon:
                disagreements = [dict(zip(("column", "source_table_id", "compared", "matched", "mismatches"), r))
                                 for r in con.execute(
                                     """SELECT report_column, source_table_id, compared, matched, mismatches
                                        FROM _reconciliation WHERE report_table_id=? AND status='partial'""", (tid,))]
            mismatches = verify_mismatches.get(sid, 0)
            if mismatches:
                readiness = "not_ready"
            elif errors or blind_spots or totals_rows or regions > 1 or disagreements:
                readiness = "needs_review"
            else:
                readiness = "ready"
            out["tables"].append({"table_id": tid, "source_id": sid, "sheet": sheet, "table_name": table_name,
                                   "rows": int(rows), "kind": kind, "grain_status": grain_status,
                                   "readiness": readiness, "quality_errors": errors,
                                   "quality_warnings": warnings, "blind_spots": blind_spots,
                                   "totals_rows": totals_rows, "verify_mismatches": mismatches,
                                   "regions": regions, "feeds_from": feeds_from,
                                   "unresolved_sources": external_refs, "anomalies": anomalies,
                                   "report_disagreements": disagreements})

        rule_errors = con.execute("SELECT COUNT(*) FROM _rule_results WHERE status='error'").fetchone()[0]
        rule_failures = con.execute(
            "SELECT COUNT(*) FROM _rule_results WHERE status='fail' AND severity='error'").fetchone()[0]
        changes = con.execute("SELECT COUNT(*) FROM _changes").fetchone()[0]
    finally:
        con.close()

    if not status.get("sources_fresh"):
        out["gaps"].append({"kind": "stale", "message": "one or more sources changed since this run",
                            "detail": status.get("changes")})
    unpromoted = not in_progress and (manifest.get("status") != "passed" or not manifest.get("promoted"))
    if unpromoted:
        out["gaps"].append({"kind": "not_promoted", "message": f"run status is '{manifest.get('status')}'"})
    if rule_errors:
        out["gaps"].append({"kind": "rule_errors", "message": f"{rule_errors} rule(s) could not execute"})
    if rule_failures:
        out["gaps"].append({"kind": "rule_failures", "message": f"{rule_failures} blocking business rule(s) failed"})
    not_ready = [t for t in out["tables"] if t["readiness"] == "not_ready"]
    needs_review = [t for t in out["tables"] if t["readiness"] == "needs_review"]
    for t in not_ready:
        out["gaps"].append({"kind": "verification_mismatch", "table_id": t["table_id"],
                            "message": "stored data did not verify against Excel; do not trust this table"})
    for t in needs_review:
        if t["blind_spots"]:
            out["gaps"].append({"kind": "blind_spot", "table_id": t["table_id"],
                                "message": f"{t['blind_spots']} thing(s) in this sheet could not be fully read "
                                           "(see query meta quality)"})
        if t["quality_errors"]:
            out["gaps"].append({"kind": "quality_error", "table_id": t["table_id"],
                                "message": f"{t['quality_errors']} error-severity quality finding(s)"})
        if t["totals_rows"]:
            out["gaps"].append({"kind": "totals_row_in_data", "table_id": t["table_id"],
                                "message": f"{t['totals_rows']} totals/subtotal row(s) inside the data; exclude "
                                           "them explicitly before summing (see query meta row_flags)"})
        for d in t.get("report_disagreements", []):
            bad = json.loads(d["mismatches"] or "[]")
            ex = "; ".join(f"{b['label']}: report {b['report']} vs data {b['data']}" for b in bad[:2])
            out["gaps"].append({"kind": "report_disagrees_with_data", "table_id": t["table_id"],
                                "message": f"column {d['column']}: {d['compared'] - d['matched']} of {d['compared']} "
                                           f"rows do not match the raw data ({ex}); quote the raw data, and say the "
                                           "report differs (see query meta reconciliation)"})
        if t["regions"] > 1:
            out["gaps"].append({"kind": "several_tables_in_sheet", "table_id": t["table_id"],
                                "message": f"this sheet holds {t['regions']} separate tables that were stored as one "
                                           "wide table; read them one at a time (see query meta regions, "
                                           "query region)"})
    for t in out["tables"]:
        if t.get("unresolved_sources"):
            names = ", ".join(sorted({(f["workbook"] + "!" if f["workbook"] else "") + (f["sheet"] or "")
                                      for f in t["feeds_from"] if f["status"] != "resolved"}))
            out["gaps"].append({"kind": "depends_on_unextracted", "table_id": t["table_id"],
                                "message": f"formulas here pull from data that is not part of this project ({names}); "
                                           "those numbers cannot be traced further (see query meta lineage)"})
        if t.get("grain_status") == "unknown":
            out["gaps"].append({"kind": "grain_unknown", "table_id": t["table_id"],
                                "message": "no column or combination uniquely identifies a row with high "
                                           "confidence; do not assume what one row represents (see query meta grain)"})

    out["changes_since_previous"] = changes
    out["next_commands"] = ["xl2ai query schema"]
    if not status.get("sources_fresh"):
        out["next_commands"] = ["xl2ai refresh"] + out["next_commands"]
    if changes:
        out["next_commands"].append("xl2ai query compare")
    if needs_review or not_ready:
        out["next_commands"].append("xl2ai query meta quality")

    if not_ready or not status.get("sources_fresh") or unpromoted:
        code = 2
    elif needs_review or rule_errors or rule_failures:
        code = 1
    else:
        code = 0
    return out, code


def _render(out):
    lines = [f"project: {out['project']}", f"run: {out['run_id']}", f"fresh: {out['fresh']}"]
    for t in out["tables"]:
        flags = []
        if t["quality_errors"]:
            flags.append(f"{t['quality_errors']} error(s)")
        if t["blind_spots"]:
            flags.append(f"{t['blind_spots']} blind spot(s)")
        if t["totals_rows"]:
            flags.append(f"{t['totals_rows']} totals row(s)")
        if t.get("regions", 0) > 1:
            flags.append(f"{t['regions']} tables in one sheet")
        if t.get("anomalies"):
            flags.append(f"{t['anomalies']} unusual pattern(s)")
        if t.get("report_disagreements"):
            flags.append("report disagrees with raw data")
        if t.get("feeds_from"):
            flags.append("computed from " + ", ".join(sorted({(f["workbook"] + "!" if f["workbook"] else "")
                                                              + (f["sheet"] or "") for f in t["feeds_from"]})))
        flag_text = f" [{', '.join(flags)}]" if flags else ""
        kind_text = f" ({t['kind']})" if t.get("kind") else ""
        lines.append(f"  {t['readiness']:<12} {t['table_id']:<40}{kind_text} {t['rows']:>10,} rows{flag_text}")
    if out["gaps"]:
        lines.append("gaps:")
        for g in out["gaps"]:
            lines.append(f"  - {g['kind']}: {g['message']}")
    else:
        lines.append("gaps: none")
    lines.append("next: " + " | ".join(out["next_commands"]))
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="xl2ai brief",
                                 description="Orient a cold agent in one call: readiness, gaps, next commands.")
    ap.add_argument("--config")
    ap.add_argument("--run")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    try:
        cfg = load_config(args.config)
        out, code = build_brief(cfg, args.run)
    except Xl2aiError as e:
        if args.json:
            print(json.dumps({"ok": False, "error": e.as_dict()}, ensure_ascii=False))
        else:
            print(str(e), file=sys.stderr)
        return 5
    if args.json:
        print(json.dumps(out, indent=1, ensure_ascii=False))
    else:
        print(_render(out), end="")
    return code


if __name__ == "__main__":
    sys.exit(main())

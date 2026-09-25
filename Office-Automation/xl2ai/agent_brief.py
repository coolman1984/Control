"""`ai/agent_brief.md`: one file, written in plain names rather than the compact pack's short handles (`t3.c7`),
that a cold agent can read start to finish and know everything `xl2ai brief` plus `query meta *` plus the change
log would otherwise take five separate calls to assemble.

This is deliberately not a replacement for `ai/context_pack.md`: the context pack is token-budgeted and uses
handles because it may be read on every turn of a long session; this file is meant to be read once, at the start,
and is allowed to be longer because clarity matters more than size for a first read. Nothing here is computed --
every fact is read from tables `analyze`/`semantics`/`repair`/`rules`/`relations`/`changes` already wrote.
"""
from __future__ import annotations

import json
import os
import sqlite3

import argparse
import sys

from .brief import build_brief
from .anomalies import anomaly_lines
from .digest import digest_lines
from .reconcile import reconciliation_lines
from .core.config import load_config
from .core.errors import Xl2aiError
from .core.fsutil import atomic_write_text
from .core.runs import current_run_id

CHANGE_EXPLANATIONS = {
    "source": "a source file was added, removed, or its content changed",
    "schema": "a table's columns or identity changed shape",
    "volume": "a table's row count changed materially",
    "value": "row content changed even though the row count did not",
    "row": "rows were added and/or removed",
    "category": "the set of distinct values in a column changed completely",
    "distribution": "the most common values in a column shifted",
    "kpi": "a business KPI's value changed",
    "baseline": "this is the first run, or the previous run could not be compared",
}


def _fmt_value(raw):
    try:
        v = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        return str(raw)
    if isinstance(v, (list, dict)):
        text = json.dumps(v, ensure_ascii=False)
        return text if len(text) <= 80 else text[:77] + "..."
    return str(v)


def _render_changes(con):
    rows = con.execute(
        """SELECT kind,severity,subject,before_value,after_value FROM _changes
           WHERE kind != 'baseline' ORDER BY CASE severity WHEN 'error' THEN 0 WHEN 'warn' THEN 1 ELSE 2 END, kind, subject"""
    ).fetchall()
    if not rows:
        baseline = con.execute("SELECT 1 FROM _changes WHERE kind='baseline' LIMIT 1").fetchone()
        return (["This is the first run (or the previous run is unavailable): nothing to compare against yet."]
                if baseline else ["No changes since the previous trusted run."])
    lines = []
    for kind, severity, subject, before, after in rows:
        why = CHANGE_EXPLANATIONS.get(kind, "")
        lines.append(f"- **[{severity}] {subject}** ({kind}): {_fmt_value(before)} -> {_fmt_value(after)}"
                     + (f" -- {why}" if why else ""))
    return lines


def _a1(row, col):
    letters = ""
    while col:
        col, rem = divmod(col - 1, 26)
        letters = chr(65 + rem) + letters
    return f"{letters}{row}"


def _join_lines(con, limit=15):
    """One line per relationship: what links to what, how sure, and the JOIN to use (cross-file aware)."""
    from .query import q, source_alias
    rows = con.execute("""SELECT r.status, r.containment, r.score, fc.name, ft.table_name, ft.source_id,
                                 tc.name, tt.table_name, tt.source_id
                          FROM _relationships r
                          JOIN _columns fc ON fc.column_id = r.from_column JOIN _tables ft ON ft.table_id = fc.table_id
                          JOIN _columns tc ON tc.column_id = r.to_column JOIN _tables tt ON tt.table_id = tc.table_id
                          ORDER BY r.status DESC, r.score DESC LIMIT ?""", (limit,)).fetchall()
    lines = []
    for status, containment, score, fcol, ftab, fsrc, tcol, ttab, tsrc in rows:
        same = fsrc == tsrc
        a = q(ftab) if same else f"{source_alias(fsrc)}.{q(ftab)}"
        b = q(ttab) if same else f"{source_alias(tsrc)}.{q(ttab)}"
        how = "xl2ai query sql " + (fsrc if same else '"*"')
        pct = f"{containment:.0%} of values found" if containment is not None else "declared"
        lines.append(f"- `{ftab}.{fcol}` -> `{ttab}.{tcol}` ({status}, {pct}): "
                     f"`FROM {a} a JOIN {b} b ON a.{q(fcol)} = b.{q(tcol)}` via `{how}`")
    return lines


def _column_lines(con, table_id):
    has_roles = con.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='_column_roles'").fetchone()[0]
    cols = con.execute("SELECT column_id,name,sql_type FROM _columns WHERE table_id=? ORDER BY position",
                       (table_id,)).fetchall()
    groups = {}
    if con.execute("SELECT 1 FROM sqlite_master WHERE name='_header_groups'").fetchone():
        groups = {r[0]: json.loads(r[1] or "[]") for r in con.execute(
            "SELECT column_id,path FROM _header_groups WHERE table_id=?", (table_id,)).fetchall()}
    roles = {}
    if has_roles:
        roles = {r[0]: (r[1], r[2], r[3]) for r in con.execute(
            "SELECT column_id,role,unit,currency FROM _column_roles WHERE table_id=?", (table_id,)).fetchall()}
    lines = []
    for cid, name, sql_type in cols:
        role, unit, currency = roles.get(cid, (None, None, None))
        tag = f" [{role}" + (f", {currency}" if currency else f", {unit}" if unit else "") + "]" if role else ""
        under = f" -- under {' > '.join(groups[cid])}" if groups.get(cid) else ""
        lines.append(f"    - {name} ({sql_type}){tag}{under}")
    return lines


def build_agent_brief(cfg, run_id, catalog_path=None, in_progress=False):
    brief_out, _ = build_brief(cfg, run_id, in_progress=in_progress)
    run_dir = os.path.join(cfg.runs_dir, run_id)
    path = catalog_path or os.path.join(run_dir, "catalog.db")
    if not os.path.isfile(path):
        raise Xl2aiError("E_STAGE_INPUT", "catalog not found")

    con = sqlite3.connect(f"file:{os.path.abspath(path)}?mode=ro", uri=True)
    try:
        existing = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        lines = [f"# Agent brief -- {brief_out['project']} (run {brief_out['run_id']})", "",
                 f"Fresh: {brief_out['fresh']} | Overall readiness: "
                 f"{'READY' if not brief_out['gaps'] else 'REVIEW GAPS BELOW'}", ""]

        if brief_out["gaps"]:
            lines.append("## Read this first: gaps")
            for g in brief_out["gaps"]:
                lines.append(f"- **{g['kind']}**" + (f" ({g['table_id']})" if g.get("table_id") else "")
                            + f": {g['message']}")
            lines.append("")

        lines.append("## What changed since the previous trusted run, and why it matters")
        lines.extend(_render_changes(con))
        lines.append("")

        from .query import source_alias
        name_counts = {}
        for t in brief_out["tables"]:
            name_counts[t["table_name"].lower()] = name_counts.get(t["table_name"].lower(), 0) + 1
        lines.append("## Tables")
        for t in brief_out["tables"]:
            tid = t["table_id"]
            grain_desc = None
            if "_table_grain" in existing:
                row = con.execute("SELECT description FROM _table_grain WHERE table_id=?", (tid,)).fetchone()
                grain_desc = row[0] if row else None
            lines.append(f"### {t['table_name']} (`{tid}`) -- {t['sheet']} -- {t['rows']:,} rows"
                        + (f" -- kind: {t['kind']}" if t.get("kind") else ""))
            lines.append("In SQL (query across all files): `" + (t["table_name"] if name_counts[t["table_name"].lower()] == 1
                         else f"{source_alias(t['source_id'])}.{t['table_name']}") + "`")
            if grain_desc:
                lines.append(f"Grain: {grain_desc}")
            if t["readiness"] != "ready":
                lines.append(f"Readiness: **{t['readiness']}**")
            if t.get("feeds_from"):
                names = {x[0]: x[1] for x in con.execute("SELECT table_id, table_name FROM _tables")}
                src = sorted({(names.get(f["table_id"]) and f"`{names[f['table_id']]}`") or
                              ((f["workbook"] + "!") if f["workbook"] else "") + (f["sheet"] or "") +
                              (" (not in this project)" if f["status"] != "resolved" else "")
                              for f in t["feeds_from"]})
                lines.append("Computed from: " + ", ".join(src) + " (formulas; see `xl2ai query meta lineage`)")
            if t.get("regions", 0) > 1 and "_regions" in existing:
                lines.append(f"Holds {t['regions']} separate tables -- read one at a time with "
                             f"`xl2ai query region {tid} <n>`:")
                for no, r0, c0, r1, c1, hdr in con.execute(
                        "SELECT region_no, first_row, first_col, last_row, last_col, header_row FROM _regions "
                        "WHERE table_id=? AND kind='table' ORDER BY region_no", (tid,)):
                    lines.append(f"    - region {no}: {_a1(r0, c0)}:{_a1(r1, c1)}"
                                 + (f", header row {hdr}" if hdr else ", no header row"))
            lines.extend(digest_lines(con, tid))
            lines.extend(anomaly_lines(con, tid))
            lines.extend(reconciliation_lines(con, tid))
            lines.append("Columns:")
            lines.extend(_column_lines(con, tid))
            lines.append("")

        from .documents.links import document_lines
        lines.extend(document_lines(con))
        joins = _join_lines(con)
        if joins:
            lines.append("## How the tables connect (ready-to-use joins)")
            lines.extend(joins)
            lines.append("")

        definitions = con.execute(
            "SELECT term,meaning,status,origin FROM _dictionary ORDER BY status DESC,term").fetchall()
        if definitions:
            lines.append("## Definitions")
            for term, meaning, status, origin in definitions:
                lines.append(f"- **{term}** ({status}/{origin}): {meaning}")
            lines.append("")

        kpis = con.execute("SELECT kpi_id,pack,value,unit FROM _kpi_results ORDER BY pack,kpi_id").fetchall()
        if kpis:
            lines.append("## KPIs")
            for kid, pack, value, unit in kpis:
                lines.append(f"- {pack}/{kid} = {value} {unit or ''}".rstrip())
            lines.append("")

        lines.append("## Next commands")
        lines.extend(f"- `{c}`" for c in brief_out["next_commands"])
    finally:
        con.close()

    ai_dir = os.path.join(run_dir, "ai")
    os.makedirs(ai_dir, exist_ok=True)
    out_path = os.path.join(ai_dir, "agent_brief.md")
    atomic_write_text(out_path, "\n".join(lines).rstrip() + "\n")
    return out_path


def main(argv=None):
    ap = argparse.ArgumentParser(prog="xl2ai agent-brief",
                                 description="Write ai/agent_brief.md: one plain-language file a cold agent "
                                             "can read instead of five separate calls.")
    ap.add_argument("--config")
    ap.add_argument("--run")
    args = ap.parse_args(argv)
    try:
        cfg = load_config(args.config)
        rid = args.run or current_run_id(cfg)
        if not rid:
            raise Xl2aiError("E_STAGE_INPUT", "no current run")
        path = build_agent_brief(cfg, rid)
    except Xl2aiError as e:
        print(str(e), file=sys.stderr)
        return 1
    print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())

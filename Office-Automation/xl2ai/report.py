"""One-screen human report for the current trusted dataset. Metadata only; business rows are never dumped."""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys

from .core.config import load_config
from .core.errors import Xl2aiError
from .core.fsutil import atomic_write_text
from .core.runs import current_run_id


def _counts(con, sql):
    return {str(k): int(v) for k, v in con.execute(sql).fetchall()}


def build_report(cfg, run_id=None):
    rid = run_id or current_run_id(cfg)
    if not rid:
        raise Xl2aiError("E_STAGE_INPUT", "no current run")
    run_dir = os.path.join(cfg.runs_dir, rid)
    path = os.path.join(run_dir, "catalog.db")
    if not os.path.isfile(path):
        raise Xl2aiError("E_STAGE_INPUT", "catalog not found")
    con = sqlite3.connect(f"file:{os.path.abspath(path)}?mode=ro", uri=True)
    try:
        summary = {
            "run_id": rid,
            "sources": con.execute("SELECT COUNT(*) FROM _sources").fetchone()[0],
            "tables": con.execute("SELECT COUNT(*) FROM _tables").fetchone()[0],
            "rows": con.execute("SELECT COALESCE(SUM(row_count),0) FROM _tables").fetchone()[0],
            "quality": _counts(con, "SELECT severity,COUNT(*) FROM _dq_findings GROUP BY severity"),
            "keys": _counts(con, "SELECT status,COUNT(*) FROM _keys GROUP BY status"),
            "relationships": _counts(con, "SELECT status,COUNT(*) FROM _relationships GROUP BY status"),
            "rules": _counts(con, "SELECT status,COUNT(*) FROM _rule_results GROUP BY status"),
            "changes": _counts(con, "SELECT kind,COUNT(*) FROM _changes GROUP BY kind"),
            "kpis": [dict(id=r[0], pack=r[1], value=r[2], unit=r[3])
                     for r in con.execute("SELECT kpi_id,pack,value,unit FROM _kpi_results ORDER BY pack,kpi_id")],
            "context_pack": os.path.join(run_dir, "ai", "context_pack.md"),
        }
        return summary
    finally:
        con.close()


def render_report(r):
    def counts(d):
        return ", ".join(f"{k}={v}" for k,v in sorted(d.items())) or "none"
    lines = [
        "xl2ai project report",
        f"run: {r['run_id']}",
        f"sources: {r['sources']} | tables: {r['tables']} | rows: {r['rows']:,}",
        f"quality: {counts(r['quality'])}",
        f"keys: {counts(r['keys'])}",
        f"relationships: {counts(r['relationships'])}",
        f"rules: {counts(r['rules'])}",
        f"changes: {counts(r['changes'])}",
    ]
    if r["kpis"]:
        lines.append("KPIs:")
        lines.extend(f"  {x['pack']}/{x['id']} = {x['value']} {x['unit']}".rstrip() for x in r["kpis"])
    lines.append(f"context: {r['context_pack']}")
    return "\n".join(lines) + "\n"


def write_report(cfg, run_id):
    text = render_report(build_report(cfg, run_id))
    path = os.path.join(cfg.runs_dir, run_id, "report.txt")
    atomic_write_text(path, text)
    return path, text


def main(argv=None):
    ap = argparse.ArgumentParser(prog="xl2ai report", description="Show the health and change summary of a trusted run.")
    ap.add_argument("--config")
    ap.add_argument("--run")
    args = ap.parse_args(argv)
    try:
        cfg = load_config(args.config)
        report = build_report(cfg, args.run)
    except Xl2aiError as e:
        print(str(e), file=sys.stderr)
        return 1
    print(render_report(report), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())

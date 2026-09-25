"""Build a deterministic, token-budgeted AI context pack from catalog metadata only."""
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
from .core.fsutil import atomic_write_json, atomic_write_text
from .core.runs import current_run_id


def _compact_json(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _est_tokens(obj):
    """Conservative model-agnostic token estimate.

    ASCII text is usually several characters per token. Arabic, CJK and emoji can be much denser, so every
    non-ASCII code point is counted as one token to avoid silently overflowing multilingual context budgets.
    """
    text = _compact_json(obj)
    ascii_chars = sum(ord(ch) < 128 for ch in text)
    non_ascii = len(text) - ascii_chars
    return int(math.ceil(ascii_chars / 3.5 + non_ascii))


def _item_chars(item):
    """(ascii_chars, non_ascii_chars) of this item's own compact JSON.

    json.dumps encodes a nested value the same way whether it is serialized alone or inside a larger structure
    (same separators/sort_keys/ensure_ascii settings recurse), so this is exactly the substring the item
    contributes to the full payload -- not an approximation.
    """
    text = _compact_json(item)
    ascii_chars = sum(ord(ch) < 128 for ch in text)
    return ascii_chars, len(text) - ascii_chars


def _add_budgeted(payload, section, item, budget_tokens, omitted, label, state):
    """Track running (ascii, non_ascii) totals instead of re-serializing the whole payload for every item, which
    made building a pack with N items O(N^2)."""
    lst = payload.setdefault(section, [])
    item_ascii, item_non_ascii = _item_chars(item)
    delta_ascii = item_ascii + (1 if lst else 0)          # the JSON list separator "," when not the first element
    ascii_total = state["ascii"] + delta_ascii
    non_ascii_total = state["non_ascii"] + item_non_ascii
    tokens = int(math.ceil(ascii_total / 3.5 + non_ascii_total))
    if tokens > budget_tokens:
        omitted[label] = omitted.get(label, 0) + 1
        return False
    lst.append(item)
    state["ascii"], state["non_ascii"] = ascii_total, non_ascii_total
    return True


def _render_markdown(p):
    lines = ["# xl2ai context pack", "",
             f"run: {p['run_id']} | hash: {p['hash']} | est tokens: {p['est_tokens']}", "", "## Sources"]
    for s in p["sources"]:
        lines.append(f"- {s['source_id']} | {s['format']} | {s['size']} bytes | {s['mtime']} | hash={s['hash_mode']}" +
                     (" | reused" if s.get("reused") else ""))
    lines += ["", "## Tables"]
    for t in p["tables"]:
        cols = ", ".join(f"{c['h']}={c['name']}:{c['type']}" for c in t.get("columns", []))
        kind_tag = f" | kind={t['kind']}" if t.get("kind") else ""
        grain_tag = f" | grain: {t['grain']}" if t.get("grain") else ""
        lines.append(f"- {t['h']} {t['table_id']} | {t['source_id']} / {t['sheet']} | {t['rows']} rows{kind_tag}{grain_tag}" +
                     (f" | {cols}" if cols else ""))
    if p.get("relationships"):
        lines += ["", "## Relationships"]
        for r in p["relationships"]:
            containment = "n/a" if r.get("containment") is None else f"{r['containment']:.3f}"
            score = "n/a" if r.get("score") is None else f"{r['score']:.3f}"
            lines.append(f"- {r['from']} -> {r['to']} | {r.get('status','')} | containment {containment} | score {score}")
    if p.get("definitions"):
        lines += ["", "## Definitions"]
        for d in p["definitions"]:
            lines.append(f"- {d['term']}: {d['meaning']}" + (f" [{d['unit']}]" if d.get("unit") else ""))
    if p.get("kpis"):
        lines += ["", "## KPIs"]
        for k in p["kpis"]:
            lines.append(f"- {k['pack']}/{k['id']} = {k['value']} {k.get('unit','')}".rstrip())
    if p.get("rules"):
        lines += ["", "## Rules"]
        for r in p["rules"]:
            lines.append(f"- {r['pack']}/{r['id']}: {r['status']}" + (f" | {r['message']}" if r.get("message") else ""))
    if p.get("changes"):
        lines += ["", "## Changes since previous trusted run"]
        for c in p["changes"]:
            lines.append(f"- {c['kind']} | {c['severity']} | {c['subject']}")
    if p.get("quality"):
        lines += ["", "## Data-quality warnings"]
        for d in p["quality"]:
            lines.append(f"- {d['severity']} {d['code']} | {d['subject']} | {d['message']}")
    if p.get("digest"):
        lines += ["", "## Key numbers (pre-computed; totals rows excluded)"]
        for x in p["digest"]:
            tot = "; ".join(f"{m} {k} {v:,}" for m, d in x["totals"].items() for k, v in d.items())
            top = "; ".join(f"{dim}: " + ", ".join(f"{k} {s:.0%}" if s is not None else str(k) for k, s in g)
                            for dim, g in x["top"].items())
            lines.append(f"- {x['table']} | {x['rows']:,} rows" + (f" | {tot}" if tot else "") + (f" | {top}" if top else ""))
    if p.get("reconciliation"):
        lines += ["", "## Reports checked against raw data"]
        for x in p["reconciliation"]:
            lines.append(f"- {x['report']}.{x['column']} = {x['explained_by']} | rows agreeing {x['agree']}")
    if p.get("anomalies"):
        lines += ["", "## Looks unusual"]
        for x in p["anomalies"]:
            lines.append(f"- {x['severity']} {x['table']}.{x['column']} {x['kind']} ({x['count']})")
    if p.get("lineage"):
        lines += ["", "## Computed from (formula lineage)"]
        for x in p["lineage"]:
            lines.append(f"- {x['table']} <- {x['from']}" + ("" if x["status"] == "resolved" else f" ({x['status']})"))
    if p.get("regions"):
        lines += ["", "## Sheets holding several tables (read with query region)"]
        for x in p["regions"]:
            lines.append(f"- {x['table']}: {x['tables_in_sheet']} tables")
    if p.get("blind_spots"):
        lines += ["", "## Blind spots (could not be fully read)"]
        for b in p["blind_spots"]:
            lines.append(f"- [{b['scope']}] {b['subject']} | {b['kind']} ({b['count']}) | {b['detail']}")
    if p.get("omitted"):
        lines += ["", "## Omitted to stay inside budget"]
        for o in p["omitted"]:
            lines.append(f"- {o['what']}: {o['count']} | use {o['use_tool']}")
    return "\n".join(lines).rstrip() + "\n"


def build_context_pack(cfg, run_id, catalog_path=None):
    run_dir = os.path.join(cfg.runs_dir, run_id)
    path = catalog_path or os.path.join(run_dir, "catalog.db")
    if not os.path.isfile(path):
        raise Xl2aiError("E_STAGE_INPUT", "catalog not found")
    con = sqlite3.connect(f"file:{os.path.abspath(path)}?mode=ro", uri=True)
    try:
        budget_tokens = int(cfg.ai["context_tokens"])
        payload = {"contract_version": "1.0", "run_id": run_id, "sources": [], "tables": [],
                   "relationships": [], "definitions": [], "kpis": [], "rules": [], "changes": [],
                   "quality": [], "blind_spots": [], "warnings": [], "omitted": []}
        omitted = {}
        base_text = _compact_json(payload)
        base_ascii = sum(ord(ch) < 128 for ch in base_text)
        state = {"ascii": base_ascii, "non_ascii": len(base_text) - base_ascii}

        source_rows = con.execute("""SELECT s.source_id,s.path,s.size,s.mtime,s.hash_mode,s.reused,
                                            COALESCE((SELECT COUNT(*) FROM _tables t WHERE t.source_id=s.source_id),0)
                                     FROM _sources s ORDER BY s.source_id""").fetchall()
        for sid, source_path, size, mtime, hash_mode, reused, table_count in source_rows:
            source_format = os.path.splitext(source_path or "")[1].lower().lstrip(".") or "unknown"
            _add_budgeted(payload, "sources",
                          {"source_id": sid, "format": source_format, "hash_mode": hash_mode,
                           "size": int(size or 0), "mtime": mtime,
                           "reused": bool(reused), "tables": int(table_count)}, budget_tokens, omitted, "sources", state)

        has_table_kind = con.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='_table_kind'").fetchone()[0]
        kinds = {}
        if has_table_kind:
            kinds = {r[0]: r[1] for r in con.execute("SELECT table_id,kind FROM _table_kind").fetchall()}
        has_grain = con.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='_table_grain'").fetchone()[0]
        grains = {}
        if has_grain:
            grains = {r[0]: (r[1], r[2]) for r in con.execute(
                "SELECT table_id,status,description FROM _table_grain").fetchall()}

        table_rows = con.execute("""SELECT table_id,source_id,sheet_name,row_count,column_count
                                    FROM _tables ORDER BY table_id""").fetchall()
        handles = {}
        for i, (tid, sid, sheet, rows, ncols) in enumerate(table_rows, 1):
            h = f"t{i}"
            handles[tid] = h
            cols = con.execute("""SELECT position,name,sql_type FROM _columns
                                  WHERE table_id=? ORDER BY position""", (tid,)).fetchall()
            grain_status, grain_desc = grains.get(tid, (None, None))
            item = {"h": h, "table_id": tid, "source_id": sid, "sheet": sheet,
                    "rows": int(rows), "cols": int(ncols), "kind": kinds.get(tid),
                    "grain_status": grain_status, "grain": grain_desc,
                    "columns": [{"h": f"{h}.c{p}", "name": n, "type": typ} for p,n,typ in cols]}
            if not _add_budgeted(payload, "tables", item, budget_tokens, omitted, "tables", state):
                skinny = dict(item)
                skinny["columns"] = []
                if _add_budgeted(payload, "tables", skinny, budget_tokens, omitted, "tables_without_columns", state):
                    omitted["columns"] = omitted.get("columns", 0) + len(cols)

        col_handles = {}
        for tid, h in handles.items():
            for cid, pos in con.execute("SELECT column_id,position FROM _columns WHERE table_id=?", (tid,)):
                col_handles[cid] = f"{h}.c{pos}"

        for f,t,containment,status,method,score in con.execute(
            """SELECT from_column,to_column,containment,status,method,score
               FROM _relationships ORDER BY score DESC,id"""):
            _add_budgeted(payload, "relationships",
                          {"from": col_handles.get(f,f), "to": col_handles.get(t,t),
                           "containment": None if containment is None else round(float(containment),4),
                           "status": status, "method": method,
                           "score": None if score is None else round(float(score),4)},
                          budget_tokens, omitted, "relationships", state)

        for term,meaning,aliases,unit,applies,status in con.execute(
            "SELECT term,meaning,aliases,unit,applies_to,status FROM _dictionary ORDER BY term"):
            _add_budgeted(payload, "definitions",
                          {"term": term, "meaning": meaning, "aliases": json.loads(aliases or "[]"),
                           "unit": unit, "applies_to": applies, "status": status},
                          budget_tokens, omitted, "definitions", state)

        for kid,pack,value,unit,dims in con.execute(
            "SELECT kpi_id,pack,value,unit,dims FROM _kpi_results ORDER BY pack,kpi_id"):
            _add_budgeted(payload, "kpis",
                          {"id": kid, "pack": pack, "value": value, "unit": unit, "dims": json.loads(dims or "{}")},
                          budget_tokens, omitted, "kpis", state)
        for rid,pack,status,severity,message in con.execute(
            "SELECT rule_id,pack,status,severity,message FROM _rule_results ORDER BY pack,rule_id"):
            _add_budgeted(payload, "rules",
                          {"id": rid, "pack": pack, "status": status, "severity": severity, "message": message},
                          budget_tokens, omitted, "rules", state)
        for kind,severity,subject in con.execute(
            "SELECT kind,severity,subject FROM _changes ORDER BY kind,subject"):
            _add_budgeted(payload, "changes",
                          {"kind": kind, "severity": severity, "subject": subject},
                          budget_tokens, omitted, "changes", state)
        for code,severity,table_id,column_id,message in con.execute(
            """SELECT code,severity,table_id,column_id,message FROM _dq_findings
               WHERE severity IN ('warn','error') ORDER BY CASE severity WHEN 'error' THEN 0 ELSE 1 END,code,id"""):
            subject = col_handles.get(column_id) or handles.get(table_id) or column_id or table_id
            _add_budgeted(payload, "quality",
                          {"code": code, "severity": severity, "subject": subject, "message": message},
                          budget_tokens, omitted, "quality", state)

        present = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "_lineage" in present:
            for tid, book, ref_sheet, target, status in con.execute(
                    """SELECT DISTINCT table_id, ref_workbook, ref_sheet, target_table_id, status FROM _lineage
                       ORDER BY table_id, ref_workbook, ref_sheet"""):
                _add_budgeted(payload, "lineage",
                              {"table": handles.get(tid, tid),
                               "from": handles.get(target) or ((book + "!") if book else "") + (ref_sheet or ""),
                               "status": status},
                              budget_tokens, omitted, "lineage", state)
        if "_digest" in present:
            for tid, in con.execute("SELECT table_id FROM _digest_tables WHERE status='computed' ORDER BY table_id"):
                item = {"table": handles.get(tid, tid), "rows": None, "totals": {}, "top": {}}
                for measure, key, value in con.execute(
                        "SELECT measure, key, value FROM _digest WHERE table_id=? AND section='total'", (tid,)):
                    if measure == "*":
                        item["rows"] = int(value)
                    elif key in ("sum", "avg") and value is not None and (key == "sum" or measure not in item["totals"]):
                        item["totals"][measure] = {"sum" if key == "sum" else "avg": round(value, 2)}
                for dim, k, share in con.execute(
                        """SELECT dim, key, share FROM _digest WHERE table_id=? AND section='by_group' AND rank<=3
                           ORDER BY dim, rank""", (tid,)):
                    item["top"].setdefault(dim, []).append([k, None if share is None else round(share, 3)])
                _add_budgeted(payload, "digest", item, budget_tokens, omitted, "digest", state)
        if "_reconciliation" in present:
            for tid, col, src, m, agg, compared, matched, status in con.execute(
                    """SELECT report_table_id, report_column, source_table_id, source_column, agg, compared, matched,
                              status FROM _reconciliation ORDER BY status DESC, report_table_id"""):
                _add_budgeted(payload, "reconciliation",
                              {"report": handles.get(tid, tid), "column": col,
                               "explained_by": f"{agg}({handles.get(src, src)}.{m})", "agree": f"{matched}/{compared}"},
                              budget_tokens, omitted, "reconciliation", state)
        if "_anomalies" in present:
            for tid, col, kind, sev, cnt in con.execute(
                    """SELECT table_id, column_name, kind, severity, count FROM _anomalies
                       ORDER BY CASE severity WHEN 'warn' THEN 0 ELSE 1 END, table_id, kind"""):
                _add_budgeted(payload, "anomalies", {"table": handles.get(tid, tid), "column": col, "kind": kind,
                                                     "severity": sev, "count": cnt},
                              budget_tokens, omitted, "anomalies", state)
        if "_regions" in present:
            for tid, n in con.execute("""SELECT table_id, COUNT(*) FROM _regions WHERE kind='table'
                                         GROUP BY table_id HAVING COUNT(*) > 1 ORDER BY table_id"""):
                _add_budgeted(payload, "regions", {"table": handles.get(tid, tid), "tables_in_sheet": int(n)},
                              budget_tokens, omitted, "regions", state)
        has_unsupported = con.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='_unsupported'").fetchone()[0]
        if has_unsupported:
            for scope,source_id,table_id,kind,count,detail in con.execute(
                "SELECT scope,source_id,table_id,kind,count,detail FROM _unsupported ORDER BY scope,kind"):
                subject = handles.get(table_id) if table_id else source_id
                _add_budgeted(payload, "blind_spots",
                              {"scope": scope, "subject": subject, "kind": kind, "count": int(count or 0),
                               "detail": detail},
                              budget_tokens, omitted, "blind_spots", state)

        use = {"sources":"query schema","tables":"query schema","tables_without_columns":"query describe",
               "columns":"query describe","relationships":"query meta relationships",
               "definitions":"query meta definitions","kpis":"query meta kpis","rules":"query meta rules",
               "changes":"query compare","quality":"query meta quality","blind_spots":"xl2ai brief",
               "lineage":"query meta lineage","regions":"query meta regions","digest":"query digest",
               "reconciliation":"query meta reconciliation","anomalies":"query meta anomalies"}
        payload["omitted"] = [{"what": k, "count": v, "use_tool": use.get(k, "query schema")}
                              for k,v in sorted(omitted.items()) if v]
        base = dict(payload)
        payload["hash"] = hashlib.sha256(_compact_json(base).encode()).hexdigest()[:16]
        payload["est_tokens"] = _est_tokens(payload)
        if payload["est_tokens"] > cfg.ai["context_tokens"]:
            payload["warnings"].append("context core exceeds configured token budget; increase ai.context_tokens")
        ai_dir = os.path.join(run_dir, "ai")
        os.makedirs(ai_dir, exist_ok=True)
        jp = os.path.join(ai_dir, "context_pack.json")
        mp = os.path.join(ai_dir, "context_pack.md")
        atomic_write_json(jp, payload)
        atomic_write_text(mp, _render_markdown(payload))
        return jp, mp, payload
    finally:
        con.close()


def main(argv=None):
    ap=argparse.ArgumentParser(prog="xl2ai pack",description="Build the compact AI context pack for a run.")
    ap.add_argument("--config")
    ap.add_argument("--run")
    args=ap.parse_args(argv)
    try:
        cfg=load_config(args.config)
        rid=args.run or current_run_id(cfg)
        if not rid:
            raise Xl2aiError("E_STAGE_INPUT","no current run")
        jp,mp,p=build_context_pack(cfg,rid)
    except Xl2aiError as e:
        print(str(e),file=sys.stderr)
        return 1
    print(f"{mp}\n{jp}\nestimated tokens: {p['est_tokens']}")
    return 0


if __name__=="__main__":
    sys.exit(main())

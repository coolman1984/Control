"""`xl2ai refresh`: run every stage into a new run folder, and promote it only if it earned it.

Exit codes: 0 promoted | 1 run failed | 2 partial (not promoted) | 4 another refresh holds the lock | 5 config/source error.
Stages today: sources, extract. Later stages register in STAGES (each is `fn(run, stage_rec, cfg, ctx)`).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time

from .core.config import load_config
from .core.errors import Xl2aiError
from .core.fsutil import atomic_write_json
from .core.log import is_silent, log
from .core.runs import CONTRACT_VERSION, Lock, Run, current_run_id, load_manifest, now_iso, reap_orphans
from .core.sqliteutil import ro_connection
from .observe import events as E
from .observe.bus import BUS
from .observe.journal import Journal, path_for_run as journal_path_for_run
from .sources.inventory import build_inventory


def stage_sources(run, st, cfg, ctx):
    sources, warnings = build_inventory(cfg)
    for s in sources:
        BUS.emit(E.SOURCE_FOUND, s["source_id"], source_id=s["source_id"], path=s["path"], size=s["size"])
        BUS.emit(E.SOURCE_HASHED, s["source_id"], source_id=s["source_id"], size=s["size"],
                 sha256=s["sha256"], hash_mode=s["hash_mode"])
    for w in warnings:
        st.warn(w)
    path = run.path("sources.json")
    atomic_write_json(path, {"contract_version": CONTRACT_VERSION, "generated": now_iso(),
                             "sources": sources, "warnings": warnings})
    st.artifact(path)
    run.m["inputs"] = [{k: s[k] for k in ("source_id", "path", "sha256", "size", "mtime", "hash_mode")} for s in sources]
    st.detail("count", len(sources))
    ctx["sources"] = sources


def _same_source_path(a, b):
    try:
        return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))
    except (TypeError, OSError):
        return False


def find_reusable_extraction(cfg, source):
    """Return a trusted prior extraction for an exactly unchanged source, otherwise None.

    Reuse is intentionally conservative: the previous run must be the promoted, fully-passed run; extraction-affecting
    settings must match; and path, hash, size and mtime must all be identical. Old manifests that predate this feature
    have no extract_fingerprint and therefore fall back to a normal extraction once.
    """
    prev_id = current_run_id(cfg)
    if not prev_id:
        return None
    try:
        prev = load_manifest(cfg, prev_id)
    except (OSError, ValueError):
        return None
    if prev.get("status") != "passed" or not prev.get("promoted"):
        return None
    if prev.get("extract_fingerprint") != cfg.extract_fingerprint():
        return None

    old = next((x for x in prev.get("inputs", []) if x.get("source_id") == source.get("source_id")), None)
    if not old or not _same_source_path(old.get("path"), source.get("path")):
        return None
    if any(old.get(k) != source.get(k) for k in ("sha256", "size", "mtime")):
        return None

    stage = next((x for x in prev.get("stages", []) if x.get("name") == "extract"), None)
    if not stage or stage.get("status") != "passed":
        return None
    rec = next((x for x in stage.get("details", {}).get("sources", [])
                if x.get("source_id") == source.get("source_id")), None)
    if not rec or rec.get("exit_code") != 0 or int(rec.get("verify_mismatches", 0) or 0):
        return None
    rel = rec.get("db")
    if not rel:
        return None

    root = os.path.abspath(os.path.join(cfg.runs_dir, prev_id))
    candidate = os.path.abspath(os.path.join(root, rel.replace("/", os.sep)))
    try:
        if os.path.commonpath([root, candidate]) != root:
            return None
    except ValueError:
        return None
    if not os.path.isfile(candidate):
        return None
    return {"run_id": prev_id, "db": candidate, "record": rec}


def materialize_reuse(src, dst):
    """Make the new run self-contained. Prefer an instant NTFS hard link; fall back to a normal copy."""
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    try:
        os.link(src, dst)
        return "hardlink"
    except OSError:
        shutil.copy2(src, dst)
        return "copy"


def stage_audit(run, st, cfg, ctx):
    from .audit import audit_run
    result=audit_run(cfg,run.id,deep=False,catalog_path=ctx.get("catalog"))
    st.detail("checks",result["checks"])
    st.detail("issues",len(result["issues"]))
    if not result["ok"]:
        first=result["issues"][0]
        st.fail("E_AUDIT",f"{len(result['issues'])} integrity issue(s); first: {first['code']}",
                "run 'xl2ai audit --run <run_id> --deep' for a full check")


def stage_report(run, st, cfg, ctx):
    from .report import write_report
    path, _ = write_report(cfg, run.id)
    st.artifact(path)


def stage_contextpack(run, st, cfg, ctx):
    from .contextpack import build_context_pack
    jp, mp, payload = build_context_pack(cfg, run.id, ctx.get("catalog"))
    st.artifact(jp)
    st.artifact(mp)
    st.detail("est_tokens", payload["est_tokens"])
    st.detail("hash", payload["hash"])


def stage_agent_brief(run, st, cfg, ctx):
    from .agent_brief import build_agent_brief
    path = build_agent_brief(cfg, run.id, ctx.get("catalog"), in_progress=True)
    st.artifact(path)


def stage_changes(run, st, cfg, ctx):
    from .changes import detect_changes
    path = detect_changes(cfg, run.id, catalog_path=ctx.get("catalog"))
    with ro_connection(path) as con:
        st.detail("changes", con.execute("SELECT COUNT(*) FROM _changes").fetchone()[0])


def stage_repair(run, st, cfg, ctx):
    from .repair import build_repairs
    path = build_repairs(cfg, run.id, ctx.get("catalog"))
    with ro_connection(path) as con:
        st.detail("repair_suggestions", con.execute("SELECT COUNT(*) FROM _repairs").fetchone()[0])


def stage_rules(run, st, cfg, ctx):
    from .rules import run_packs
    path = run_packs(cfg, run.id, ctx.get("catalog"))
    with ro_connection(path) as con:
        failed = con.execute("SELECT COUNT(*) FROM _rule_results WHERE status='fail'").fetchone()[0]
        errors = con.execute("SELECT COUNT(*) FROM _rule_results WHERE status='error'").fetchone()[0]
        st.detail("failed_rules", failed)
        st.detail("rule_errors", errors)
        if errors:
            st.fail("E_RULE", f"{errors} rule(s) could not execute")
        elif cfg.block_on_rule_error:
            blocking = con.execute(
                "SELECT COUNT(*) FROM _rule_results WHERE status='fail' AND severity='error'").fetchone()[0]
            if blocking:
                st.fail("E_RULE", f"{blocking} blocking business rule(s) failed")


def stage_relations(run, st, cfg, ctx):
    from .relations import infer_relations
    path = infer_relations(cfg, run.id, ctx.get("catalog"))
    with ro_connection(path) as con:
        n = con.execute("SELECT COUNT(*) FROM _relationships").fetchone()[0]
    st.detail("relationships", n)


def stage_digest(run, st, cfg, ctx):
    from .digest import build_digest
    path = build_digest(cfg, run.id, ctx.get("catalog"))
    with ro_connection(path) as con:
        st.detail("tables_summarised",
                  con.execute("SELECT COUNT(*) FROM _digest_tables WHERE status='computed'").fetchone()[0])
        errors = con.execute("SELECT table_id, reason FROM _digest_tables WHERE status='error'").fetchall()
    for tid, reason in errors:
        st.warn(f"key numbers for {tid} could not be computed: {reason}")


def stage_anomalies(run, st, cfg, ctx):
    from .anomalies import build_anomalies
    path = build_anomalies(cfg, run.id, ctx.get("catalog"))
    with ro_connection(path) as con:
        st.detail("anomalies", con.execute("SELECT COUNT(*) FROM _anomalies").fetchone()[0])


def stage_reconcile(run, st, cfg, ctx):
    from .reconcile import build_reconciliation
    path = build_reconciliation(cfg, run.id, ctx.get("catalog"))
    with ro_connection(path) as con:
        st.detail("report_columns_checked", con.execute("SELECT COUNT(*) FROM _reconciliation").fetchone()[0])
        st.detail("report_columns_disagreeing",
                  con.execute("SELECT COUNT(*) FROM _reconciliation WHERE status='partial'").fetchone()[0])


def stage_links(run, st, cfg, ctx):
    from .documents.links import build_mentions
    path = build_mentions(cfg, run.id, ctx.get("catalog"))
    with ro_connection(path) as con:
        if con.execute("SELECT 1 FROM sqlite_master WHERE name='_documents'").fetchone():
            st.detail("documents", con.execute("SELECT COUNT(*) FROM _documents").fetchone()[0])
            st.detail("document_links", con.execute("SELECT COUNT(*) FROM _mentions").fetchone()[0])


def stage_analyze(run, st, cfg, ctx):
    from .analyze import analyze_catalog
    path = analyze_catalog(cfg, run.id, ctx.get("catalog"))
    with ro_connection(path) as con:
        st.detail("quality_findings", con.execute("SELECT COUNT(*) FROM _dq_findings").fetchone()[0])
        st.detail("candidate_keys", con.execute("SELECT COUNT(*) FROM _keys").fetchone()[0])


def stage_semantics(run, st, cfg, ctx):
    from .semantics import build_semantics
    path = build_semantics(cfg, run.id, ctx.get("catalog"))
    with ro_connection(path) as con:
        st.detail("roles_classified", con.execute("SELECT COUNT(*) FROM _column_roles").fetchone()[0])
        unconfirmed = con.execute("SELECT COUNT(*) FROM _table_grain WHERE status='unknown'").fetchone()[0]
        st.detail("tables_with_unknown_grain", unconfirmed)
        st.detail("duplicate_candidates", con.execute("SELECT COUNT(*) FROM _duplicate_candidates").fetchone()[0])


def stage_catalog(run, st, cfg, ctx):
    from .catalog import build_catalog
    path = build_catalog(cfg, run.id, run.m)
    st.artifact(path)
    with ro_connection(path) as con:
        st.detail("tables", con.execute("SELECT COUNT(*) FROM _tables").fetchone()[0])
    ctx["catalog"] = path


def _extract_worker(path, db, opts):
    """Runs in a separate process (direct engine only): one workbook, output silenced, results sent back."""
    from .core.log import silence
    from .extract.pipeline import process_file
    silence(True)
    return process_file(path, db, opts)


def _worker_count(cfg, opts, pending):
    """How many workbooks to extract at once. The Excel engine is always one at a time (one private Excel)."""
    from .extract.pipeline import resolve_engine
    if resolve_engine(opts) != "direct" or len(pending) < 2:
        return 1
    wanted = cfg.extract.get("workers", 0) or min(4, os.cpu_count() or 1)
    return max(1, min(wanted, len(pending)))


def stage_extract(run, st, cfg, ctx):
    opts = cfg.extract_options()
    per, codes = {}, set()
    pending = []
    for s in ctx["sources"]:
        db = run.path("extract", s["source_id"] + ".db")
        reusable = None if ctx.get("force_extract") else find_reusable_extraction(cfg, s)
        if reusable:
            mode = materialize_reuse(reusable["db"], db)
            old = reusable["record"]
            info = {"source_id": s["source_id"], "exit_code": 0, "db": run.rel(db),
                    "message": "unchanged source; reused previous trusted extraction",
                    "sheets": dict(old.get("sheets", {})),
                    "verify_checks": int(old.get("verify_checks", 0) or 0),
                    "verify_mismatches": 0, "reused": True,
                    "reused_from_run": reusable["run_id"], "reuse_mode": mode}
            st.artifact(db)
            per[s["source_id"]] = info
            codes.add(0)
            BUS.emit(E.SOURCE_REUSED, "unchanged source; reused previous trusted extraction",
                     source_id=s["source_id"], from_run=reusable["run_id"], mode=mode)
            log("INFO", f"Reused unchanged source '{s['source_id']}' from run {reusable['run_id']} ({mode}); Excel not opened")
            continue
        pending.append((s, db))

    def record(s, db, code, results, message, seconds):
        for r in results:
            BUS.emit(E.SHEET_END, r.message or "", level="error" if r.status == "error" else "info",
                     name=r.sheet_name, status=r.status, rows=r.data_rows, columns=r.columns,
                     seconds=round(r.total_sec, 3))
        BUS.emit(E.SOURCE_EXTRACT_END, message or "",
                 level="error" if code in (1, 3) else "info",
                 source_id=s["source_id"], status="error" if code in (1, 3) else "extracted",
                 exit_code=code, seconds=round(seconds, 3))
        codes.add(code)
        info = {"source_id": s["source_id"], "exit_code": code, "db": None, "message": message, "reused": False,
                "sheets": {k: sum(r.status == k for r in results) for k in ("extracted", "skipped", "error")}}
        if os.path.isfile(db):
            info["db"] = run.rel(db)
            st.artifact(db)
            with ro_connection(db) as con:
                meta = dict(con.execute("SELECT key, value FROM _meta").fetchall())
            info["verify_checks"] = int(meta.get("verify_checks", 0))
            info["verify_mismatches"] = int(meta.get("verify_mismatches", 0))
        per[s["source_id"]] = info

    workers = _worker_count(cfg, opts, pending)
    st.detail("workers", workers)
    if workers > 1:
        from concurrent.futures import ProcessPoolExecutor
        log("INFO", f"Extracting {len(pending)} workbooks, {workers} at a time (direct engine)")
        started = {}
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = {}
            for s, db in pending:
                with BUS.scope(source_id=s["source_id"]):
                    BUS.emit(E.SOURCE_EXTRACT_START, s["source_id"], source_id=s["source_id"], path=s["path"])
                started[s["source_id"]] = time.perf_counter()
                futures[s["source_id"]] = (s, db, pool.submit(_extract_worker, s["path"], db, opts))
            for sid, (s, db, fut) in futures.items():
                with BUS.scope(source_id=sid):
                    try:
                        code, results, message = fut.result()
                    except Exception as e:                    # a worker that died is that source's failure only
                        code, results, message = 1, [], f"extraction worker failed: {type(e).__name__}: {e}"
                    record(s, db, code, results, message, time.perf_counter() - started[sid])
                if not is_silent():
                    ok = sum(r.status == "extracted" for r in results)
                    print(f"{s['path']}: {ok} sheet(s) extracted" + (f" | {message}" if message else ""), flush=True)
    else:
        process_file = None                          # COM is imported only when at least one workbook really changed
        for s, db in pending:
            if process_file is None:
                from .extract.pipeline import process_file as _process_file
                process_file = _process_file
            if not is_silent():
                print(f"\n{'=' * 100}\n{s['path']}\n{'=' * 100}")
            with BUS.scope(source_id=s["source_id"]):
                BUS.emit(E.SOURCE_EXTRACT_START, s["source_id"], source_id=s["source_id"], path=s["path"])
                t0 = time.perf_counter()
                code, results, message = process_file(s["path"], db, opts)
                record(s, db, code, results, message, time.perf_counter() - t0)
    per = [per[s["source_id"]] for s in ctx["sources"] if s["source_id"] in per]
    st.detail("sources", per)
    if 3 in codes:
        bad = [p["source_id"] for p in per if p["exit_code"] == 3]
        st.fail("E_VERIFY_MISMATCH", "stored data did not verify against the workbook for: " + ", ".join(bad),
                "see the _verification table in that database")
    elif 1 in codes:
        bad = [f"{p['source_id']} ({p['message']})" for p in per if p["exit_code"] == 1]
        st.fail("E_EXTRACT", "could not extract: " + "; ".join(bad))
    elif 2 in codes:
        st.partial("some sheets failed to extract; the run is not promoted unless allow_partial is set")


STAGES = (("sources", stage_sources), ("extract", stage_extract), ("catalog", stage_catalog),
          ("analyze", stage_analyze), ("semantics", stage_semantics), ("repair", stage_repair),
          ("rules", stage_rules), ("relations", stage_relations), ("digest", stage_digest),
          ("anomalies", stage_anomalies), ("reconcile", stage_reconcile), ("links", stage_links),
          ("changes", stage_changes),
          ("audit", stage_audit), ("contextpack", stage_contextpack), ("agent_brief", stage_agent_brief),
          ("report", stage_report))
# rules runs before relations: pack-confirmed keys must exist in `_keys` before relation inference can use them
# as trusted parent candidates (see relations.infer_relations), not just generic uniqueness-inferred ones.


def run_refresh(cfg, force=False):
    """Returns (exit_code, run). Raises Xl2aiError for lock/config problems before any run exists."""
    with Lock(cfg):
        reap_orphans(cfg)
        run = Run.create(cfg)
        log("INFO", f"Run {run.id} started (project '{cfg.project}')")
        ctx = {"force_extract": bool(force)}
        journal = Journal(journal_path_for_run(run.dir)).open()
        detach = journal.attach(BUS)
        BUS.emit(E.RUN_START, f"run {run.id} started", run_id=run.id, project=cfg.project)
        try:
            for name, fn in STAGES:
                with BUS.scope(stage=name):
                    BUS.emit(E.STAGE_START, name, name=name)
                    with run.stage(name) as st:
                        fn(run, st, cfg, ctx)
                    status = run.stage_status(name)
                    record = next((s for s in run.m["stages"] if s["name"] == name), {})
                    BUS.emit(E.STAGE_END, name, level="error" if status != "passed" else "info",
                             name=name, status=status, seconds=record.get("seconds"),
                             error=record.get("error"), details=record.get("details") or {})
                    for key, value in (record.get("details") or {}).items():
                        if isinstance(value, (int, float, str)) and not isinstance(value, bool):
                            BUS.emit(E.STAGE_DETAIL, "", **{key: value})
                    for warning in record.get("warnings") or []:
                        BUS.warn(E.WARNING, warning)
                    if status != "passed":
                        break                               # later stages need this stage's output
        finally:
            promoted = run.finish()
            if promoted:
                try:
                    from .workspace import publish_latest
                    publish_latest(cfg, run.id)
                except OSError as e:                   # a convenience copy must never undo a promotion
                    log("WARN", f"could not update data/latest: {e}")
            BUS.emit(E.RUN_END, f"run {run.m['status']}", run_id=run.id,
                     status=run.m["status"], promoted=bool(promoted))
            detach()
            journal.close()
        code = 0 if promoted else (2 if run.m["status"] == "partial" else 1)
        return code, run


def print_summary(run, promoted):
    m = run.m
    print(f"\nRun {m['run_id']}: {m['status'].upper()}  |  promoted: {'yes' if promoted else 'NO (current dataset unchanged)'}")
    for s in m["stages"]:
        err = f"  -> {s['error']['code']}: {s['error']['message']}" if s.get("error") else ""
        print(f"  {s['name']:<10} {s['status']:<11} {s['seconds'] if s['seconds'] is not None else '-':>7}s{err}")
        for w in s["warnings"]:
            print(f"      warn: {w}")
    print(f"Folder: {run.dir}")


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(prog="xl2ai refresh", description="Refresh everything into a new run; promote only if valid.")
    ap.add_argument("paths", nargs="*", help="override the configured sources")
    ap.add_argument("--config", help="path to xl2ai.toml (default: discovered upward from the current folder)")
    ap.add_argument("--json", action="store_true", help="print the run manifest as JSON at the end")
    ap.add_argument("--force", action="store_true", help="re-extract every source even when it is unchanged")
    args = ap.parse_args(argv)
    try:
        cfg = load_config(args.config, required=not args.paths)
        if args.paths:
            cfg = cfg.with_sources(args.paths)
        code, run = run_refresh(cfg, force=args.force)
    except Xl2aiError as e:
        print(f"{e}" + (f"\n  hint: {e.hint}" if e.hint else ""), file=sys.stderr)
        return 4 if e.code == "E_LOCKED" else 5
    print_summary(run, run.m["promoted"])
    if args.json:
        print(json.dumps(run.m, indent=1, ensure_ascii=False))
    return code


if __name__ == "__main__":
    sys.exit(main())

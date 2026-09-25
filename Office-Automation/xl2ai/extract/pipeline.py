"""Workbook-level run: open, loop sheets with crash recovery, verify, promote DB, CLI."""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
import time
import traceback

from ..core.config import wrapper_prefixes_or_empty
from ..core.procs import kill_pid
from ..sources.detect import sniff_file
from ..sources.inventory import expand_paths
from .com import ExcelDied, ExcelSession, com_msg
from ..core.log import is_silent
from .common import PYWIN32_AVAILABLE, SCHEMA_VERSION, VISIBILITY, log, pywintypes
from .names import sanitize_table
from .sheet import SheetResult, extract_sheet
from .store import open_db, resolve_db_path, write_log
from .unsupported import detect_sheet_unsupported, detect_stale_calculation, detect_workbook_unsupported
from .verify import verify_table


def resolve_engine(opts):
    """'excel' | 'direct' for `[extract] engine` (auto = Excel when this machine can drive it, else direct)."""
    engine = getattr(opts, "engine", "auto") or "auto"
    if engine == "auto":
        return "excel" if os.name == "nt" and PYWIN32_AVAILABLE else "direct"
    return engine


def process_file(src, db_path, opts):
    """Extract one source -- a workbook with the configured engine, or a document (Word, PowerPoint, PDF, e-mail,
    text) with the document readers. Returns (exit_code, [SheetResult-like], message)."""
    from ..documents.readers import DOC_KINDS, kind_of
    if kind_of(src) in DOC_KINDS:
        from ..documents.store import extract_document
        return extract_document(src, db_path, opts)
    if resolve_engine(opts) == "direct":
        from .direct import process_file_direct
        return process_file_direct(src, db_path, opts)
    return process_file_excel(src, db_path, opts)


def process_file_excel(src, db_path, opts):
    """Excel engine. COM is initialised for the calling thread: the agent connection runs a refresh in a
    background thread, and COM refuses every call from a thread that did not initialise it."""
    if os.name != "nt" or not PYWIN32_AVAILABLE:
        return _process_file_excel(src, db_path, opts)
    from .common import pythoncom
    pythoncom.CoInitialize()
    try:
        return _process_file_excel(src, db_path, opts)
    finally:
        import gc
        gc.collect()                                  # release every COM proxy before leaving the apartment
        pythoncom.CoUninitialize()


def _process_file_excel(src, db_path, opts):
    t_run = time.perf_counter()
    if os.name != "nt" or not PYWIN32_AVAILABLE:
        # Say so plainly. Without this the COM objects are all None and the first failure surfaces as an
        # unrelated AttributeError deep inside the error handling itself.
        msg = ("Excel extraction requires Windows, Microsoft Excel and pywin32. "
               "Metadata, query and reporting commands still work on this machine.")
        log("ERROR", msg)
        return 1, [], msg
    partial = db_path + ".partial"
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    kind = sniff_file(src, getattr(opts, "wrapper_prefixes", ()))
    if kind == "encrypted":
        msg = "The file is password protected (Office encryption). Remove the password and try again."
        log("ERROR", msg)
        return 1, [], msg
    if kind == "drm":
        log("INFO", "DRM-wrapped file: it can only be read through Excel's DRM agent (this tool does that)")
    sess = ExcelSession(src, visible=opts.visible, open_timeout=opts.open_timeout)
    con = None
    results, fatal = [], None
    checks = mismatches = 0
    open_s = 0.0
    try:
        con = open_db(partial)
        log("INFO", "Starting a private Excel instance...")
        sess.start()
        log("INFO", f"Opening (read-only): {src}")
        t = time.perf_counter()
        sess.open()
        open_s = time.perf_counter() - t
        total = sess.robust(lambda: sess.wb.Sheets.Count)    # always go through sess.wb: it changes on restart
        log("INFO", f"Opened in {open_s:.2f}s | {total} sheet(s) | Excel {sess.app.Version}")
        unsupported = [("workbook", None, kind, count, detail)
                       for kind, count, detail in detect_workbook_unsupported(sess, sess.wb)]
        if detect_stale_calculation(sess):
            unsupported.append(("workbook", None, "stale_calculation", 1,
                                "the workbook was saved with pending recalculation; formula-derived values may "
                                "not reflect the latest inputs"))
            log("WARN", "Workbook has pending recalculation; extracted formula results may be stale")
        for _, _, kind, count, detail in unsupported:
            log("WARN", f"Unsupported content: {kind} ({count}) - {detail}")
        used = set()
        kill_once = os.environ.get("XL2SQL_TEST_KILL_BEFORE")   # test hook: simulate an Excel crash once
        for i in range(1, total + 1):
            name = sess.robust(lambda: sess.wb.Sheets(i).Name)
            if opts.sheets and name.strip().lower() not in opts.sheets:
                continue
            vis = VISIBILITY.get(sess.robust(lambda: sess.wb.Sheets(i).Visible), "visible")
            res = SheetResult(i, name, sanitize_table(name, used), vis)
            results.append(res)
            t = time.perf_counter()
            budget = opts.block_cells
            if kill_once and kill_once == name.strip():
                kill_once = None
                log("WARN", f"TEST HOOK: killing Excel (pid {sess.pid}) before '{name.strip()}'")
                kill_pid(sess.pid)
            for attempt in range(3):
                res.read_sec = 0.0
                try:
                    extract_sheet(sess, i, con, res, opts, budget)
                    if res.status != "error":
                        for kind, count, detail in detect_sheet_unsupported(sess, sess.sheet(i)):
                            unsupported.append(("sheet", res.sheet_name, kind, count, detail))
                    break
                except Exception as e:
                    if sess.is_dead(e):                    # crash/hang: restart Excel, retry with smaller blocks
                        log("WARN", f"Excel died on '{name}': {com_msg(e)}")
                        budget = max(50_000, budget // 2)
                        if attempt == 2 or sess.restarts >= 6:
                            res.status, res.message = "error", f"Excel kept crashing: {com_msg(e)}"
                            break
                        sess.restart()
                        continue
                    res.status, res.message = "error", com_msg(e)
                    log("ERROR", f"Sheet '{name}' failed: {res.message}")
                    log("ERROR", traceback.format_exc(limit=4).strip().splitlines()[-2].strip())
                    break
            res.total_sec = time.perf_counter() - t
            tag = f" | {vis}" if vis != "visible" else ""
            if is_silent():                              # a live view owns the screen; it renders these itself
                pass
            elif res.status == "extracted":
                print(f"[{i}/{total}] {name.strip()}: {res.data_rows:,} rows x {res.columns} cols | header row "
                      f"{res.header_row or 'none'} | read {res.read_sec:.2f}s | write {res.write_sec:.2f}s{tag}"
                      + (f" | {res.message}" if res.message else ""), flush=True)
            else:
                print(f"[{i}/{total}] {name.strip()}: {res.status.upper()} - {res.message}{tag}", flush=True)
        if opts.verify:
            log("INFO", "Verifying against Excel (COUNTA / AGGREGATE)...")
            t = time.perf_counter()
            for res in results:
                if res.status == "extracted" and res.plans:
                    try:
                        c, b = verify_table(sess, con, res)
                    except ExcelDied as e:
                        log("WARN", f"Verification of '{res.sheet_name}' interrupted: {e}")
                        continue
                    checks, mismatches = checks + c, mismatches + b
                    if b:
                        log("ERROR", f"  MISMATCH in '{res.sheet_name}': {b} of {c} checks (see _verification)")
            log("INFO", f"Verification: {checks} checks, {mismatches} mismatch(es) in {time.perf_counter() - t:.1f}s")
    except Exception as e:
        fatal = com_msg(e) if pywintypes is not None and isinstance(e, pywintypes.com_error) else str(e)
        log("ERROR", fatal)
    finally:
        t = time.perf_counter()
        sess.close()
        close_s = time.perf_counter() - t
    if fatal:
        if con is not None:
            con.close()
        for p in (partial, partial + "-journal"):
            if os.path.exists(p):
                os.remove(p)
        return 1, results, fatal
    total_s = time.perf_counter() - t_run
    write_log(con, results)
    if unsupported:
        con.executemany("INSERT INTO _unsupported VALUES (?,?,?,?,?)", unsupported)
    meta = {"schema_version": SCHEMA_VERSION, "source_path": src, "source_size": os.path.getsize(src),
            "source_modified": dt.datetime.fromtimestamp(os.path.getmtime(src)).isoformat(timespec="seconds"),
            "extracted_at": dt.datetime.now().isoformat(timespec="seconds"), "tool": "excel_to_sqlite.py",
            "excel_restarts": sess.restarts, "verify_checks": checks, "verify_mismatches": mismatches,
            "total_seconds": round(total_s, 2)}
    con.executemany("INSERT INTO _meta VALUES (?,?)", [(k, str(v)) for k, v in meta.items()])
    con.execute("PRAGMA optimize")
    con.close()
    os.replace(partial, db_path)
    if not is_silent():
        print_summary(results, open_s, close_s, total_s, db_path, checks, mismatches)
    failed = any(r.status == "error" for r in results)
    return (3 if mismatches else 2 if failed else 0), results, ""


def print_summary(results, open_s, close_s, total_s, db_path, checks, mismatches):
    head = ("#", "Sheet", "Table", "Status", "Rows", "Cols", "Hdr", "Err", "Read s", "Write s")
    rows = [(str(r.sheet_index), r.sheet_name.strip()[:26], (r.table_name if r.status == "extracted" else "-")[:26],
             r.status, f"{r.data_rows:,}", str(r.columns), str(r.header_row or "-"), str(r.error_cells),
             f"{r.read_sec:.2f}", f"{r.write_sec:.2f}") for r in results]
    w = [max(len(h), *(len(x[i]) for x in rows)) if rows else len(h) for i, h in enumerate(head)]
    line = "  ".join("-" * n for n in w)
    print("\n" + line)
    print("  ".join(h.ljust(n) if i < 4 else h.rjust(n) for i, (h, n) in enumerate(zip(head, w))))
    print(line)
    for x in rows:
        print("  ".join(v.ljust(n) if i < 4 else v.rjust(n) for i, (v, n) in enumerate(zip(x, w))))
    print(line)
    ok = [r for r in results if r.status == "extracted"]
    print(f"Sheets: {len(ok)} extracted, {sum(r.status == 'skipped' for r in results)} skipped, "
          f"{sum(r.status == 'error' for r in results)} failed | rows: {sum(r.data_rows for r in ok):,} | "
          f"verification: {checks} checks, {mismatches} mismatches")
    for r in results:
        if r.status in ("skipped", "error"):
            print(f"  - {r.status.upper()}: {r.sheet_name.strip()} -> {r.message}")
    print(f"\nTiming: open {open_s:.2f}s | read {sum(r.read_sec for r in results):.2f}s | "
          f"write {sum(r.write_sec for r in results):.2f}s | close {close_s:.2f}s | TOTAL {total_s:.2f}s")
    print(f"Database: {db_path}")


def collect_files(paths):
    return expand_paths(paths)


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(prog="xl2ai extract", description="Extract Excel workbooks into SQLite (via Excel COM, or directly without Excel).")
    ap.add_argument("paths", nargs="+", help="Excel file(s), folder(s) or wildcard(s)")
    ap.add_argument("-o", "--output", help="output .db file (single input) or output folder")
    ap.add_argument("--no-verify", dest="verify", action="store_false", help="skip the Excel-vs-SQLite check")
    ap.add_argument("--strict", action="store_true", help="create STRICT tables (needs SQLite >= 3.37 to open)")
    ap.add_argument("--sheet", action="append", default=[], help="only this sheet (repeatable)")
    ap.add_argument("--block-cells", type=int, default=500_000, help="cells per Value2 call (default 500000)")
    ap.add_argument("--cache-cells", type=int, default=12_000_000, help="cache a sheet in RAM up to this many cells")
    ap.add_argument("--open-timeout", type=int, default=180, help="seconds before a hung open is killed")
    ap.add_argument("--visible", action="store_true", help="show the Excel window (debugging)")
    ap.add_argument("--engine", choices=("auto", "excel", "direct"), default="auto",
                    help="excel = drive Excel via COM (Windows; opens DRM files); direct = read the file itself "
                         "(any OS, faster); auto = excel when available, else direct")
    opts = ap.parse_args(argv)
    if resolve_engine(opts) == "excel" and (os.name != "nt" or not PYWIN32_AVAILABLE):
        log("ERROR", "The Excel engine requires Windows, Microsoft Excel and pywin32. Use --engine direct.")
        return 1
    opts.sheets = {s.strip().lower() for s in opts.sheet}
    opts.wrapper_prefixes = wrapper_prefixes_or_empty()       # from xl2ai.toml if one is found, else none

    files = collect_files(opts.paths)
    if not files:
        log("ERROR", "No Excel files found.")
        return 1
    if len(files) > 1 and opts.output and opts.output.lower().endswith((".db", ".sqlite", ".sqlite3")):
        log("ERROR", "-o must be a folder when several files are given.")
        return 1
    worst = 0
    for src in files:
        if not os.path.isfile(src):
            log("ERROR", f"File not found: {src}")
            worst = max(worst, 1)
            continue
        print(f"\n{'=' * 100}\n{src}\n{'=' * 100}")
        code, _, _ = process_file(src, resolve_db_path(src, opts.output), opts)
        worst = max(worst, code)
    return worst


if __name__ == "__main__":
    sys.exit(main())

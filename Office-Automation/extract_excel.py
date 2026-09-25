"""Extract every data sheet of an Excel file into a local SQLite database.

Uses real Excel through COM automation (win32com) for maximum fidelity.

Usage:
    python extract_excel.py <path_to_excel_file> [--output <db_file_or_folder>]
"""
import argparse
import datetime as dt
import gc
import os
import re
import sqlite3
import sys
import time
import traceback

try:
    import pythoncom  # noqa: F401  (ensures COM is initialised)
    import pywintypes
    import win32com.client as win32
except ImportError:
    print("ERROR: pywin32 is not installed. Run:  pip install pywin32")
    sys.exit(1)

XL_CALC_MANUAL = -4135
XL_WORKSHEET = -4167
VISIBILITY = {-1: "visible", 0: "hidden", 2: "very hidden"}
ERR_LO, ERR_HI = -2146826300, -2146826200      # Excel error codes (#N/A, #REF!, ...)
BLOCK_CELLS = 4_000_000                         # sheets bigger than this are read in row blocks
HEADER_SCAN_ROWS = 20
SAMPLE_SIZE = 2000


# ----------------------------------------------------------------------------- helpers
def log(level, msg):
    print(f"[{time.strftime('%H:%M:%S')}] {level:<5} {msg}", flush=True)


def now_iso():
    return dt.datetime.now().isoformat(timespec="seconds")


def com_msg(exc):
    """Readable text for a COM exception."""
    if isinstance(exc, pywintypes.com_error):
        info = exc.excepinfo
        if info and len(info) > 2 and info[2]:
            return str(info[2]).strip()
        return str(exc.strerror or exc).strip()
    return f"{type(exc).__name__}: {exc}"


def friendly_open_error(exc):
    text = com_msg(exc)
    low = text.lower()
    if any(w in low for w in ("locked", "in use", "sharing violation", "being used", "another process")):
        return f"The file is open or locked by another program. Close it and try again. ({text})"
    if "password" in low or "protected" in low:
        return f"The file is password protected and cannot be opened. ({text})"
    return (f"Excel could not open the file. It may be corrupted, in an unsupported "
            f"format, or not an Excel file. ({text})")


def is_err(v):
    return type(v) is int and ERR_LO <= v <= ERR_HI


def fmt_num(v):
    if v.is_integer() and abs(v) < 1e15:
        return str(int(v))
    return repr(v)


# ----------------------------------------------------------------------------- naming
def sanitize_table(name, used):
    t = re.sub(r"\W+", "_", str(name), flags=re.UNICODE).strip("_") or "sheet"
    if t[0].isdigit():
        t = "t_" + t
    if t.lower().startswith("_extraction"):
        t = "t_" + t
    base, k = t, 2
    while t.lower() in used:
        t = f"{base}_{k}"
        k += 1
    used.add(t.lower())
    return t


def clean_header(v):
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return re.sub(r"\W+", "_", str(v).strip(), flags=re.UNICODE).strip("_")


def build_columns(headers):
    """Return (names, generated_count, duplicate_count)."""
    names, seen, gen, dup = [], set(), 0, 0
    for i, h in enumerate(headers, 1):
        n = clean_header(h)
        if not n:
            n, gen = f"col_{i}", gen + 1
        base, k = n, 2
        if n.lower() in seen:
            dup += 1
        while n.lower() in seen:
            n = f"{base}_{k}"
            k += 1
        seen.add(n.lower())
        names.append(n)
    return names, gen, dup


def q(ident):
    return '"' + ident.replace('"', '""') + '"'


# ----------------------------------------------------------------------------- typing / dates
def classify_format(fmt):
    """Return 'date', 'datetime', 'time' or None from an Excel NumberFormat string."""
    if not isinstance(fmt, str) or not fmt:
        return None
    s = fmt.split(";")[0]
    s = re.sub(r'"[^"]*"|\\.|_.|\*.|\[(?![hms]+\])[^\]]*\]', "", s).lower()
    if "general" in s or "e+" in s or "e-" in s:
        return None
    has_date = re.search(r"[yd]", s) is not None
    has_time = re.search(r"[hs]|am/pm", s) is not None
    if has_date:
        return "datetime" if has_time else "date"
    if has_time:
        return "time"
    return None


def to_iso(v, kind, epoch, is1904):
    if kind == "time":
        total = int(round(v * 86400))
        return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"
    if not (0 <= v < 2958466):
        return fmt_num(v)
    base = epoch if (is1904 or v >= 61) else epoch - dt.timedelta(days=1)
    days = int(v)
    secs = int(round((v - days) * 86400))
    d = base + dt.timedelta(days=days, seconds=secs if kind == "datetime" else 0)
    if kind == "datetime" and secs:
        return d.strftime("%Y-%m-%d %H:%M:%S")
    return d.strftime("%Y-%m-%d")


def infer_type(sample):
    if not sample:
        return "TEXT"
    ok = True
    all_int = True
    for v in sample:
        t = type(v)
        if t is str or t not in (float, bool):
            return "TEXT"
        if t is float and not (v.is_integer() and abs(v) < 2 ** 62):
            all_int = False
    return "INTEGER" if all_int and ok else "REAL"


def convert_column(vals, sqltype, datekind, epoch, is1904):
    """Clean one column: errors -> NULL, dates -> ISO text, numbers typed. Returns (list, error_count)."""
    out = [None] * len(vals)
    errs = 0
    for i, v in enumerate(vals):
        if v is None:
            continue
        t = type(v)
        if t is float:
            if datekind:
                v = to_iso(v, datekind, epoch, is1904)
            elif sqltype == "INTEGER":
                if v.is_integer():
                    v = int(v)
            elif sqltype == "TEXT":
                v = fmt_num(v)
        elif t is int:
            if ERR_LO <= v <= ERR_HI:
                errs += 1
                continue
            if sqltype == "TEXT":
                v = str(v)
        elif t is bool:
            v = ("TRUE" if v else "FALSE") if sqltype == "TEXT" else int(v)
        elif t is not str:
            v = str(v)
        out[i] = v
    return out, errs


# ----------------------------------------------------------------------------- Excel reading
def read_rows(ws, ur, base_row, base_col, ncols, r0, r1, nrows):
    """Read rows [r0, r1) of the used range in one Value2 call (halves the block if Excel refuses)."""
    try:
        if r0 == 0 and r1 == nrows:
            v = ur.Value2
        else:
            v = ws.Range(ws.Cells(base_row + r0, base_col),
                         ws.Cells(base_row + r1 - 1, base_col + ncols - 1)).Value2
    except Exception:
        if r1 - r0 <= 1:
            raise
        mid = (r0 + r1) // 2
        return (read_rows(ws, ur, base_row, base_col, ncols, r0, mid, nrows)
                + read_rows(ws, ur, base_row, base_col, ncols, mid, r1, nrows))
    if not isinstance(v, tuple):
        return [[v]]
    return [list(r) for r in v]


def find_merges(app, ws, ur, base_row, base_col, nrows, ncols):
    """Locate merged areas without looping over every cell. Returns [(r0, c0, r1, c1, value)]."""
    merges, seen = [], set()
    try:
        app.FindFormat.Clear()
        app.FindFormat.MergeCells = True
        cell = ur.Find(What="", SearchFormat=True)
        first = cell.Address if cell is not None else None
        guard = 0
        while cell is not None and guard < 200_000:
            guard += 1
            area = cell.MergeArea
            r0, c0 = area.Row - base_row, area.Column - base_col
            if (r0, c0) not in seen:
                seen.add((r0, c0))
                nr, nc = area.Rows.Count, area.Columns.Count
                if nr * nc > 1:
                    val = area.Cells(1, 1).Value2
                    if val is not None:
                        merges.append((max(r0, 0), max(c0, 0), min(r0 + nr - 1, nrows - 1),
                                       min(c0 + nc - 1, ncols - 1), val))
            cell = ur.FindNext(cell)
            if cell is None or cell.Address == first:
                break
        if guard >= 200_000:
            log("WARN", "Too many merged cells; some may not be filled.")
    except Exception as e:
        log("WARN", f"Could not locate merged cells ({com_msg(e)}); merged values may be blank.")
    finally:
        try:
            app.FindFormat.Clear()
        except Exception:
            pass
    return merges


def apply_merges(block, b0, merges):
    b1 = b0 + len(block)
    for r0, c0, r1, c1, val in merges:
        for r in range(max(r0, b0), min(r1, b1 - 1) + 1):
            row = block[r - b0]
            for c in range(c0, c1 + 1):
                row[c] = val


def looks_like_header(row):
    vals = [v for v in row if v is not None and not (isinstance(v, str) and not v.strip())]
    if not vals or len(vals) < 0.5 * len(row):
        return False
    if sum(isinstance(v, str) for v in vals) < 0.6 * len(vals):
        return False
    return len({str(v).strip().lower() for v in vals}) >= 0.8 * len(vals)


def find_header(block):
    for i in range(min(HEADER_SCAN_ROWS, len(block) - 1)):
        if looks_like_header(block[i]):
            return i
    return None


def detect_date_kinds(ws, base_row, base_col, hdr, nrows, ncols, cols):
    """Per-column date kind from the cell NumberFormat (not from the raw value)."""
    top, bot = base_row + hdr + 1, base_row + nrows - 1
    kinds = [None] * ncols
    try:
        whole = ws.Range(ws.Cells(top, base_col), ws.Cells(bot, base_col + ncols - 1)).NumberFormat
        if isinstance(whole, str):
            return [classify_format(whole)] * ncols
    except Exception:
        pass
    for c in range(ncols):
        numeric_rows = [i for i, v in enumerate(cols[c]) if type(v) is float]
        if not numeric_rows:
            continue
        try:
            nf = ws.Range(ws.Cells(top, base_col + c), ws.Cells(bot, base_col + c)).NumberFormat
            if isinstance(nf, str):
                kinds[c] = classify_format(nf)
                continue
            step = max(1, len(numeric_rows) // 5)
            votes = {}
            for i in numeric_rows[::step][:5]:
                k = classify_format(ws.Cells(top + i, base_col + c).NumberFormat)
                votes[k] = votes.get(k, 0) + 1
            best = max(votes, key=votes.get)
            kinds[c] = best
        except Exception:
            pass
    return kinds


# ----------------------------------------------------------------------------- one sheet
def process_sheet(app, ws, con, used_tables, epoch, is1904, rec):
    ur = ws.UsedRange
    nrows, ncols = ur.Rows.Count, ur.Columns.Count
    base_row, base_col = ur.Row, ur.Column
    ext = wr = 0.0

    if nrows < 2:
        empty = nrows * ncols == 1 and ur.Value2 is None
        try:
            charts = ws.ChartObjects().Count
        except Exception:
            charts = 0
        if empty and charts:
            rec.update(status="skipped", message="chart-only sheet (no cell data)")
        elif empty:
            rec.update(status="skipped", message="empty sheet")
        else:
            rec.update(status="skipped", message=f"fewer than 2 rows ({nrows} row used)")
        return

    single = nrows * ncols <= BLOCK_CELLS
    rpb = nrows if single else max(1000, BLOCK_CELLS // ncols)

    # ---- extract first block (COM) -------------------------------------------------
    t = time.perf_counter()
    block = read_rows(ws, ur, base_row, base_col, ncols, 0, min(nrows, rpb), nrows)
    merges = []
    try:
        has_merge = ur.MergeCells
    except Exception:
        has_merge = None
    if has_merge is not False:
        merges = find_merges(app, ws, ur, base_row, base_col, nrows, ncols)
        apply_merges(block, 0, merges)
    hdr = find_header(block)
    if hdr is None:
        rec.update(status="skipped", message="no header-like row found (first rows are not mostly unique text)")
        return
    if hdr:
        log("INFO", f"  header found on row {hdr + 1} of the used range")
    headers = block[hdr]
    data = [r for r in block[hdr + 1:] if any(v is not None for v in r)]
    cols = list(zip(*data)) if data else [() for _ in range(ncols)]
    date_kinds = detect_date_kinds(ws, base_row, base_col, hdr, nrows, ncols,
                                   list(zip(*block[hdr + 1:])) or [() for _ in range(ncols)])
    ext += time.perf_counter() - t

    # ---- prepare schema (Python only) -----------------------------------------------
    t = time.perf_counter()
    keep = list(range(ncols))
    if single:
        keep = [c for c in keep if headers[c] is not None or any(v is not None for v in cols[c])]
    names, gen, dup = build_columns([headers[c] for c in keep])
    if gen:
        log("WARN", f"  {gen} missing/blank header(s) replaced with col_N names")
    if dup:
        log("WARN", f"  {dup} duplicate header(s) renamed with numeric suffix")
    sqltypes = []
    for c in keep:
        if date_kinds[c]:
            sqltypes.append("TEXT")
            continue
        col = cols[c]
        step = max(1, len(col) // SAMPLE_SIZE)
        sqltypes.append(infer_type([v for v in col[::step] if v is not None and not is_err(v)]))
    table = sanitize_table(ws.Name, used_tables)
    rec["table_name"] = table
    rec["column_count"] = len(names)
    wr += time.perf_counter() - t

    # ---- write: one transaction for the whole sheet ---------------------------------
    total_rows = total_errs = 0
    insert_sql = f"INSERT INTO {q(table)} VALUES ({','.join('?' * len(names))})"

    def write_block(rows):
        nonlocal total_rows, total_errs
        if not rows:
            return
        columns = list(zip(*rows))
        conv = []
        for j, c in enumerate(keep):
            vals, errs = convert_column(columns[c], sqltypes[j], date_kinds[c], epoch, is1904)
            total_errs += errs
            conv.append(vals)
        con.executemany(insert_sql, zip(*conv))
        total_rows += len(rows)

    con.execute("BEGIN")
    try:
        t = time.perf_counter()
        con.execute(f"CREATE TABLE {q(table)} (" +
                    ", ".join(f"{q(n)} {tp}" for n, tp in zip(names, sqltypes)) + ")")
        write_block(data)
        wr += time.perf_counter() - t
        pos = min(nrows, rpb)
        while pos < nrows:
            t = time.perf_counter()
            end = min(nrows, pos + rpb)
            block = read_rows(ws, ur, base_row, base_col, ncols, pos, end, nrows)
            apply_merges(block, pos, merges)
            ext += time.perf_counter() - t
            t = time.perf_counter()
            write_block([r for r in block if any(v is not None for v in r)])
            wr += time.perf_counter() - t
            pos = end
        if total_rows == 0:
            con.execute("ROLLBACK")
            rec.update(status="skipped", message="header found but no data rows", table_name="", column_count=0)
            return
        con.execute("COMMIT")
    except BaseException:
        try:
            con.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        rec.update(extract_sec=ext, write_sec=wr)
    rec.update(status="extracted", row_count=total_rows, error_cells=total_errs,
               message=f"{total_errs} Excel error value(s) stored as NULL" if total_errs else "")


# ----------------------------------------------------------------------------- summary output
def print_summary(records, open_s, close_s, total_s, db_path):
    rows = [r for r in records if r["row_type"] == "sheet"]
    head = ("Sheet", "Table", "Status", "Rows", "Cols", "Err->NULL", "Extract s", "Write s")
    data = []
    for r in rows:
        data.append((r["sheet_name"][:28], (r["table_name"] or "-")[:28], r["status"],
                     f"{r['row_count']:,}", str(r["column_count"]), str(r["error_cells"]),
                     f"{r['extract_sec']:.2f}", f"{r['write_sec']:.2f}"))
    widths = [max(len(head[i]), *(len(d[i]) for d in data)) if data else len(head[i]) for i in range(len(head))]
    line = "  ".join("-" * w for w in widths)
    print("\n" + "=" * len(line))
    print("SUMMARY")
    print("=" * len(line))
    print("  ".join(h.ljust(w) for h, w in zip(head, widths)))
    print(line)
    for d in data:
        print("  ".join(v.ljust(w) if i < 3 else v.rjust(w) for i, (v, w) in enumerate(zip(d, widths))))
    print(line)
    extracted = [r for r in rows if r["status"] == "extracted"]
    skipped = [r for r in rows if r["status"] == "skipped"]
    failed = [r for r in rows if r["status"] == "error"]
    print(f"Sheets: {len(extracted)} extracted, {len(skipped)} skipped, {len(failed)} failed | "
          f"total rows: {sum(r['row_count'] for r in extracted):,}")
    for r in skipped + failed:
        print(f"  - {r['status'].upper()}: {r['sheet_name']} -> {r['message']}")
    ext = sum(r["extract_sec"] for r in rows)
    wr = sum(r["write_sec"] for r in rows)
    print("\nTiming breakdown")
    print(f"  Opening the file        : {open_s:8.2f} s")
    print(f"  Extracting from Excel   : {ext:8.2f} s")
    print(f"  Writing to SQLite       : {wr:8.2f} s")
    print(f"  Closing Excel           : {close_s:8.2f} s")
    print(f"  TOTAL                   : {total_s:8.2f} s")
    print(f"\nDatabase: {db_path}")


def new_record(sheet_name, visibility=""):
    return dict(row_type="sheet", sheet_name=sheet_name, table_name="", visibility=visibility,
                status="", row_count=0, column_count=0, error_cells=0, message="",
                start_time=now_iso(), end_time="", duration_sec=0.0, extract_sec=0.0, write_sec=0.0)


def write_log_table(con, records):
    con.execute("DROP TABLE IF EXISTS _extraction_log")
    con.execute("""CREATE TABLE _extraction_log (
        id INTEGER PRIMARY KEY, row_type TEXT, sheet_name TEXT, table_name TEXT, visibility TEXT,
        status TEXT, row_count INTEGER, column_count INTEGER, excel_error_cells_as_null INTEGER,
        message TEXT, start_time TEXT, end_time TEXT, duration_sec REAL, extract_sec REAL, write_sec REAL)""")
    con.execute("BEGIN")
    con.executemany(
        "INSERT INTO _extraction_log (row_type, sheet_name, table_name, visibility, status, row_count, "
        "column_count, excel_error_cells_as_null, message, start_time, end_time, duration_sec, "
        "extract_sec, write_sec) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [(r["row_type"], r["sheet_name"], r["table_name"], r["visibility"], r["status"], r["row_count"],
          r["column_count"], r["error_cells"], r["message"], r["start_time"], r["end_time"],
          round(r["duration_sec"], 3), round(r["extract_sec"], 3), round(r["write_sec"], 3))
         for r in records])
    con.execute("COMMIT")


# ----------------------------------------------------------------------------- main run
def run(src, db_path):
    t_run = time.perf_counter()
    run_start = now_iso()
    app = wb = con = None
    records = []
    open_s = close_s = 0.0
    fatal = None

    try:
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        if os.path.exists(db_path):
            os.remove(db_path)
        con = sqlite3.connect(db_path, isolation_level=None)
        con.execute("PRAGMA journal_mode=OFF")
        con.execute("PRAGMA synchronous=OFF")

        log("INFO", "Starting a private Excel instance...")
        app = win32.DispatchEx("Excel.Application")
        app.Visible = False
        for prop, val in (("ScreenUpdating", False), ("DisplayAlerts", False), ("EnableEvents", False),
                          ("AskToUpdateLinks", False), ("AutomationSecurity", 3),
                          ("Calculation", XL_CALC_MANUAL)):
            try:
                setattr(app, prop, val)
            except Exception:
                pass  # Calculation cannot be set until a workbook is open; re-applied below

        log("INFO", f"Opening: {src}")
        t = time.perf_counter()
        try:
            # positional: Filename, UpdateLinks=0, ReadOnly=True (works with any pywin32 wrapper)
            wb = app.Workbooks.Open(src, 0, True)
        except Exception as e:
            raise RuntimeError(friendly_open_error(e)) from None
        open_s = time.perf_counter() - t
        try:
            app.Calculation = XL_CALC_MANUAL
        except Exception:
            pass
        log("INFO", f"Workbook opened in {open_s:.2f}s")

        is1904 = bool(wb.Date1904)
        epoch = dt.datetime(1904, 1, 1) if is1904 else dt.datetime(1899, 12, 30)
        used_tables = set()
        total = wb.Sheets.Count
        for i in range(1, total + 1):
            sheet = wb.Sheets(i)
            try:
                name = sheet.Name
            except Exception:
                name = f"sheet_{i}"
            try:
                vis = VISIBILITY.get(sheet.Visible, str(sheet.Visible))
            except Exception:
                vis = ""
            rec = new_record(name, vis)
            records.append(rec)
            t = time.perf_counter()
            if vis != "visible" and vis:
                log("INFO", f"Sheet '{name}' is {vis} - extracting anyway")
            try:
                if sheet.Type != XL_WORKSHEET:
                    rec.update(status="skipped", message="chart sheet (no cell data)")
                else:
                    process_sheet(app, sheet, con, used_tables, epoch, is1904, rec)
            except Exception as e:
                rec.update(status="error", message=com_msg(e))
                log("ERROR", f"Sheet '{name}' failed: {com_msg(e)}")
                log("ERROR", traceback.format_exc(limit=3).strip().splitlines()[-1])
            rec["duration_sec"] = time.perf_counter() - t
            rec["end_time"] = now_iso()
            if rec["status"] == "extracted":
                print(f"[{i}/{total}] {name}: {rec['row_count']:,} rows x {rec['column_count']} cols | "
                      f"extract {rec['extract_sec']:.2f}s | write {rec['write_sec']:.2f}s | "
                      f"total {rec['duration_sec']:.2f}s"
                      + (f" | {vis}" if vis != "visible" else ""), flush=True)
            else:
                print(f"[{i}/{total}] {name}: {rec['status'].upper()} - {rec['message']}", flush=True)
            del sheet
    except Exception as e:
        fatal = str(e)
        log("ERROR", fatal)
        rec = new_record("(workbook)")
        rec.update(status="error", message=fatal, end_time=now_iso())
        records.append(rec)
    finally:
        t = time.perf_counter()
        if wb is not None:
            try:
                wb.Close(SaveChanges=False)
            except Exception:
                pass
        if app is not None:
            try:
                app.Quit()
            except Exception:
                pass
        del wb, app
        gc.collect()
        close_s = time.perf_counter() - t
        total_s = time.perf_counter() - t_run
        if con is not None:
            try:
                summary = new_record("(total)")
                summary.update(row_type="summary", start_time=run_start, end_time=now_iso(),
                               duration_sec=total_s, status="error" if fatal else "done",
                               row_count=sum(r["row_count"] for r in records),
                               message=fatal or f"source: {src}",
                               extract_sec=0.0, write_sec=0.0)
                write_log_table(con, records + [summary])
            except Exception as e:
                log("ERROR", f"Could not write _extraction_log: {e}")
            con.close()

    print_summary(records, open_s, close_s, time.perf_counter() - t_run, db_path)
    return 1 if fatal else 0


def resolve_db_path(src, out):
    stem = os.path.splitext(os.path.basename(src))[0]
    if out and out.lower().endswith((".db", ".sqlite", ".sqlite3")):
        return os.path.abspath(out)
    folder = out if out else os.path.dirname(src)
    return os.path.abspath(os.path.join(folder, stem + ".db"))


def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="Extract an Excel file into a SQLite database via Excel COM.")
    ap.add_argument("excel_file", help="path to the Excel file (.xlsx, .xlsb, .xlsm, .xls)")
    ap.add_argument("--output", "-o", help="output .db file path, or an output folder")
    args = ap.parse_args()

    src = os.path.abspath(args.excel_file)
    if not os.path.isfile(src):
        log("ERROR", f"File not found: {src}")
        return 1
    if not src.lower().endswith((".xlsx", ".xlsb", ".xlsm", ".xls", ".xltx", ".xltm")):
        log("WARN", "File extension is not a known Excel type; trying anyway.")
    return run(src, resolve_db_path(src, args.output))


if __name__ == "__main__":
    sys.exit(main())

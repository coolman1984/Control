"""Per-sheet extraction: read blocks, statistics pass, write pass, preamble, errors."""
from __future__ import annotations

import datetime as dt
import sys
import time

from .coltypes import ColPlan, ColStat, clean_surrogates, convert_column
from .com import ExcelDied
from .common import ERROR_TEXT, ERR_HI, ERR_LO, HEADER_SCAN_ROWS, XL_FORMULAS, XL_WORKSHEET, log, pywintypes
from .dates import classify_format, serial_to_iso
from .layout import find_extent, find_header, find_merged_areas, header_confidence, show_filtered_rows
from .names import build_columns, clean_header, col_letter, q
from .regions import find_regions, header_groups, multiple_tables

class SheetResult:
    def __init__(self, idx, name, table, visibility):
        self.__dict__.update(sheet_index=idx, sheet_name=name, table_name=table, visibility=visibility,
                             status="", message="", header_row=None, first_row=None, last_row=None,
                             first_col=None, last_col=None, data_rows=0, columns=0, blank_rows_skipped=0,
                             error_cells=0, formula_cells=None, pivot_tables=None, filter_active=None,
                             merged_areas=0, merged_in_data=None, header_cells=0, preamble_cells=0, read_sec=0.0, write_sec=0.0, total_sec=0.0, plans=[], data_first=None,
                             header_confidence=None, header_reasons=None,
                             regions=[], regions_complete=1, header_groups={}, header_groups_method=None)


def record_structure(res, first_blk, fr, fc, lr, hdr, merged):
    """Separate table regions and grouped header rows, from values already read (no extra Excel calls)."""
    regions = find_regions(first_blk, fr, fc)
    if multiple_tables(regions):
        res.regions = regions
        res.regions_complete = int(len(first_blk) >= lr - fr + 1)
    res.header_groups, res.header_groups_method = header_groups(first_blk, hdr, fr, fc, merged)


def write_structure(con, res):
    import json
    for n, g in enumerate(res.regions, 1):
        con.execute("INSERT INTO _regions VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (res.table_name, n, g["first_row"], g["first_col"], g["last_row"], g["last_col"],
                     g["header_row"], g["kind"], g["cells"], res.regions_complete))
    by_col = {p.xl_col: p.name for p in res.plans}
    for xl_col, path in sorted(res.header_groups.items()):
        if xl_col in by_col:
            con.execute("INSERT INTO _header_groups VALUES (?,?,?,?,?)",
                        (res.table_name, xl_col, by_col[xl_col], json.dumps(path, ensure_ascii=False),
                         res.header_groups_method))


FORMULA_ROWS_PER_READ = 200_000
MAX_DISTINCT_FORMULAS = 20_000


def record_formula_refs(sess, ws, con, table, plan, data_first, last_row):
    """Every formula of one column, read as R1C1 text in large blocks (one COM call per block, like values).

    In R1C1 form a column filled down has one identical formula text on every row, so the distinct set is tiny
    and each is parsed once. Writes `_formula_refs` (sheets/workbooks referenced, with cell counts) -- the same
    table the direct engine fills, so lineage is complete for Excel-engine workbooks too, not a one-cell sample.
    """
    from ..lineage import formula_refs
    counts, first = {}, None
    for r0 in range(data_first, last_row + 1, FORMULA_ROWS_PER_READ):
        r1 = min(last_row, r0 + FORMULA_ROWS_PER_READ - 1)
        vals = sess.call(lambda: ws.Range(ws.Cells(r0, plan.xl_col), ws.Cells(r1, plan.xl_col)).FormulaR1C1)
        rows = vals if isinstance(vals, tuple) else ((vals,),)
        for row in rows:
            f = row[0] if isinstance(row, tuple) else row
            if isinstance(f, str) and f.startswith("="):
                counts[f] = counts.get(f, 0) + 1
                if len(counts) > MAX_DISTINCT_FORMULAS:
                    break
    refs = {}
    for f, n in counts.items():
        first = first or f
        for key in formula_refs(f):
            refs[key] = refs.get(key, 0) + n
    for (book, sheet), n in refs.items():
        con.execute("INSERT INTO _formula_refs VALUES (?,?,?,?,?,?)",
                    (table, plan.name, book, sheet, n, (first or "")[:200]))


def iter_data(blocks, data_first, counters):
    """Yield (xl_rows, columns) per block, skipping header/preamble rows and fully blank rows."""
    for r0, blk in blocks:
        rows, xl = [], []
        for k, row in enumerate(blk):
            absrow = r0 + k
            if absrow < data_first:
                continue
            if row.count(None) == len(row):
                counters["blank"] += 1
                continue
            rows.append(row)
            xl.append(absrow)
        if rows:
            yield xl, list(zip(*rows))


def detect_dates(sess, idx, plans, data_first, last_row, strict):
    """Mark date/time columns from NumberFormat; mixed-format columns fall back to reading .Value."""
    ws = sess.sheet(idx)
    for plan in plans:
        if not plan.stat.has_float:
            continue
        def rng():
            return ws.Range(ws.Cells(data_first, plan.xl_col), ws.Cells(last_row, plan.xl_col))
        nf = sess.call(lambda: rng().NumberFormat)
        if isinstance(nf, str):
            kind = classify_format(nf)
            if kind:
                plan.make_date(kind, None, strict)
            continue
        vals = sess.call(lambda: rng().Value)
        if not isinstance(vals, tuple):
            vals = ((vals,),)
        mask = {data_first + i: v[0] for i, v in enumerate(vals) if isinstance(v[0], dt.datetime)}
        if mask:
            plan.make_date("datetime", mask, strict)


def extract_sheet(sess, idx, con, res, opts, budget):
    t_start = time.perf_counter()
    ws = sess.sheet(idx)
    if sess.call(lambda: ws.Type) != XL_WORKSHEET:
        res.status, res.message = "skipped", "chart sheet (no cell data)"
        return
    res.filter_active = int(sess.call(lambda: show_filtered_rows(ws)))
    extent = sess.call(lambda: find_extent(ws))
    if extent is None:
        res.status, res.message = "skipped", "empty sheet"
        return
    fr, fc, lr, lc = extent
    ncols = lc - fc + 1
    res.first_row, res.last_row, res.first_col, res.last_col = fr, lr, fc, lc
    whole = sess.call(lambda: ws.Range(ws.Cells(fr, fc), ws.Cells(lr, lc)))

    def info(fn):
        try:
            return sess.call(fn)
        except ExcelDied:
            raise
        except Exception:
            return None
    res.pivot_tables = info(lambda: ws.PivotTables().Count)
    res.formula_cells = info(lambda: whole.SpecialCells(XL_FORMULAS).Count) or 0

    rows_per_block = max(1, budget // ncols)
    ranges = [(r, min(r + rows_per_block - 1, lr)) for r in range(fr, lr + 1, rows_per_block)]
    cache = (lr - fr + 1) * ncols <= opts.cache_cells       # small enough: read once, use for both passes

    def timed_read(r0, r1):
        t0 = time.perf_counter()
        v = sess.read_block(idx, r0, r1, fc, ncols)
        res.read_sec += time.perf_counter() - t0
        return r0, v
    blocks = [timed_read(r0, r1) for r0, r1 in ranges] if cache else None

    def iter_blocks():
        if cache:
            yield from blocks
        else:                                               # huge sheet: bounded memory, read twice
            for r0, r1 in ranges:
                yield timed_read(r0, r1)

    first_blk = next(iter_blocks())[1]
    width = sum(1 for c in range(ncols) if any(r[c] is not None for r in first_blk[:200]))
    hdr = find_header(first_blk, width)
    res.header_row = fr + hdr if hdr is not None else None
    res.data_first = data_first = (fr + hdr + 1) if hdr is not None else fr
    res.header_confidence, res.header_reasons = header_confidence(first_blk, width, hdr)

    # merged cells: values live only in the top-left cell (nothing is filled). List areas in the header region,
    # and just flag the data region (walking every merged area there is far too slow).
    merged = sess.call(lambda: find_merged_areas(ws, fr, min(lr, fr + HEADER_SCAN_ROWS), fc, lc))
    res.merged_areas = len(merged)
    record_structure(res, first_blk, fr, fc, lr, hdr, merged)
    if data_first <= lr:
        try:                                         # False = none merged, True/Null(mixed) = some merged
            mc = sess.call(lambda: ws.Range(ws.Cells(data_first, fc), ws.Cells(lr, lc)).MergeCells)
            res.merged_in_data = int(mc is not False)
        except pywintypes.com_error:
            if sess.is_dead(sys.exc_info()[1]):
                raise
            res.merged_in_data = None

    # ---- pass 1: statistics over the WHOLE column ------------------------------------------------
    stats = [ColStat() for _ in range(ncols)]
    counters = {"blank": 0}
    total_rows = 0
    for xl, cols in iter_data(iter_blocks(), data_first, counters):
        total_rows += len(xl)
        for c in range(ncols):
            stats[c].update(cols[c])
    headers =list(first_blk[hdr]) if hdr is not None else [None] * ncols
    keep = [c for c in range(ncols) if stats[c].has_data or stats[c].nerr or clean_header(headers[c])]
    if not keep:
        res.status, res.message = "skipped", "no usable columns"
        return
    names, gen, dup = build_columns([headers[c] for c in keep])
    if gen and hdr is not None:
        log("WARN", f"  {gen} blank header(s) -> col_N")
    if dup:
        log("WARN", f"  {dup} duplicate header(s) renamed with a numeric suffix")
    # Only headers of KEPT columns are actually represented anywhere (as column names). A header cell whose column
    # was dropped (e.g. whitespace/symbols-only text with no data) must not be credited as "stored", or the
    # whole-sheet completeness check below would silently pass over genuinely lost data.
    res.header_cells = sum(1 for c in keep if headers[c] is not None)
    plans = [ColPlan(n, fc + c, headers[c], stats[c], opts.strict) for n, c in zip(names, keep)]
    if total_rows:
        detect_dates(sess, idx, plans, data_first, lr, opts.strict)
    res.plans = plans
    res.columns, res.data_rows = len(plans), total_rows

    # ---- pass 2: write, one transaction per sheet ---------------------------------------------------
    is1904 = bool(sess.call(lambda: sess.wb.Date1904))
    table = res.table_name
    insert_sql = f"INSERT INTO {q(table)} VALUES ({','.join('?' * (len(plans) + 1))})"
    counters = {"blank": 0}
    con.execute("BEGIN")
    try:
        con.execute(f"CREATE TABLE {q(table)} (\"_xl_row\" INTEGER, " +
                    ", ".join(f"{q(p.name)} {p.sql_type}" for p in plans) + (") STRICT" if opts.strict else ")"))
        keep_idx = [p.xl_col - fc for p in plans]
        err_total = 0
        for xl, cols in iter_data(iter_blocks(), data_first, counters):
            out, err_rows = [xl], []
            for p, c in zip(plans, keep_idx):
                vals, errs = convert_column(cols[c], p, xl, is1904)
                out.append(vals)
                for i, text in errs or ():
                    err_rows.append((table, xl[i], p.xl_col, text))
            try:
                con.executemany(insert_sql, zip(*out))
            except UnicodeError:
                con.executemany(insert_sql, zip(*[out[0]] + [clean_surrogates(v) for v in out[1:]]))
            if err_rows:
                con.executemany("INSERT INTO _cell_errors VALUES (?,?,?,?)", err_rows)
                err_total += len(err_rows)
        res.blank_rows_skipped, res.error_cells = counters["blank"], err_total
        # rows above the header (titles, pivot page filters, group labels) are kept, never dropped
        if hdr:
            pre = []
            for k, row in enumerate(first_blk[:hdr]):
                for c, v in enumerate(row):
                    if v is not None:
                        if v.__class__ is int and ERR_LO <= v <= ERR_HI:
                            v = ERROR_TEXT.get(v, "#ERROR")
                        elif v.__class__ is float:            # a date shown in a title row should not become 46023.0
                            kind = classify_format(sess.call(lambda: ws.Cells(fr + k, fc + c).NumberFormat))
                            if kind:
                                v = serial_to_iso(v, kind, is1904)
                        pre.append((table, fr + k, fc + c, v))
            con.executemany("INSERT INTO _sheet_preamble VALUES (?,?,?,?)", pre)
            res.preamble_cells = len(pre)
        con.execute("COMMIT")
    except BaseException:
        try:
            con.execute("ROLLBACK")
        except Exception:
            pass
        raise
    for p in plans:
        n = con.execute(f"SELECT COUNT({q(p.name)}) FROM {q(table)}").fetchone()[0]
        con.execute("INSERT INTO _columns VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (table, p.xl_col - fc + 1, p.name, None if p.header is None else str(p.header), p.xl_col,
                     col_letter(p.xl_col), p.sql_type, p.kind, p.date_kind, n, p.stat.nerr))
    if res.formula_cells and total_rows:
        # The sheet has formulas somewhere; find out which columns so an agent can tell "computed" from "entered"
        # without guessing from the header text. One SpecialCells probe per column, not per cell.
        for p in plans:
            try:
                col_rng = ws.Range(ws.Cells(data_first, p.xl_col), ws.Cells(lr, p.xl_col))
                formula_cells = sess.call(lambda: col_rng.SpecialCells(XL_FORMULAS))
                sample = sess.call(lambda: formula_cells.Cells(1, 1).FormulaR1C1)
                con.execute("INSERT INTO _formulas VALUES (?,?,?,?)", (table, p.name, 1, str(sample)[:200]))
            except ExcelDied:
                raise
            except Exception:
                continue
            try:
                record_formula_refs(sess, ws, con, table, p, data_first, lr)
            except ExcelDied:
                raise
            except Exception as e:                  # lineage is a bonus; the sample above already stands
                log("WARN", f"  formula references of '{p.name}' not read: {e}")
    for area, val in merged:
        con.execute("INSERT INTO _merged_areas VALUES (?,?,?)", (res.sheet_name, area, val))
    write_structure(con, res)
    res.status = "extracted"
    if not total_rows:
        res.message = "header only (no data rows)"
    elif res.error_cells:
        res.message = f"{res.error_cells} Excel error cell(s) stored as NULL (see _cell_errors)"
    res.total_sec = time.perf_counter() - t_start
    res.write_sec = res.total_sec - res.read_sec            # everything that is not COM reading: typing + SQLite

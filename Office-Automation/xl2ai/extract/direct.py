"""Direct extraction engine: read workbooks without Excel, into exactly the same database contract.

The Excel engine (pipeline.py + sheet.py) drives a private Excel through COM. It is the only way to open
rights-managed (DRM) files, and Excel itself verifies every column -- but it needs Windows + Excel and is slow on
very large workbooks. This engine reads the file directly with python-calamine (a compiled xlsx/xlsm/xlsb/xls
reader), so it runs on any OS, in CI and in containers, and is typically many times faster.

Same output, same rules:
  * the same header detection, column typing, value conversion, preamble, structure detection and table naming
    code as the Excel engine (only the cell *reading* differs);
  * nothing is coerced silently; error cells are NULL in data and listed in `_cell_errors`;
  * every column is verified -- for .xlsx/.xlsm by a second, independent reader (a streaming scan of the sheet
    XML, which also yields every formula, error cell and cross-sheet reference); for .xlsb/.xls by the reader's own
    grid, stated as such in `_verification.note`. What this engine cannot see is recorded in `_unsupported`.

What it cannot do, by design: open DRM-wrapped files (only Excel's rights agent can), or recalculate formulas
(cached values saved in the file are what is extracted -- exactly what Excel shows on open with calculation off).
"""
from __future__ import annotations

import datetime as dt
import html
import math
import os
import posixpath
import re
import time
import zipfile
from collections import Counter, defaultdict
from xml.etree.ElementTree import iterparse

from ..core.log import is_silent, log
from ..sources.detect import sniff_file
from .coltypes import ColPlan, ColStat, clean_surrogates, convert_column
from .common import ERROR_TEXT, HEADER_SCAN_ROWS, SCHEMA_VERSION
from .dates import datetime_to_iso
from .layout import find_header, header_confidence
from .names import build_columns, clean_header, col_letter, fmt_num, q, sanitize_table
from .sheet import SheetResult, record_structure, write_structure
from .store import open_db, write_log

try:
    import python_calamine as _calamine
except ImportError:                                    # optional dependency: the Excel engine does not need it
    _calamine = None

ERROR_CODE = {text: code for code, text in ERROR_TEXT.items()}
NS_MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
NS_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
NS_PKG = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_ROW_OPEN = re.compile(rb"<((?:\w+:)?)row\b")
_ROW = re.compile(rb"<(?:\w+:)?row\b([^>]*?)(?:/>|>(.*?)</(?:\w+:)?row>)", re.S)
_C = re.compile(rb"<(?:\w+:)?c\b([^>]*?)(?:/>|>(.*?)</(?:\w+:)?c>)", re.S)
_V = re.compile(rb"<(?:\w+:)?v>(.*?)</(?:\w+:)?v>", re.S)
_F = re.compile(rb"<(?:\w+:)?f\b([^>]*?)(?:/>|>(.*?)</(?:\w+:)?f>)", re.S)
_TEXT = re.compile(rb"<(?:\w+:)?t\b[^>]*>(.*?)</(?:\w+:)?t>", re.S)
_ROW_NUM = re.compile(rb'<row r="(\d+)"')
_FAST_FILLED = re.compile(rb'<c r="([A-Z]+)\d+"[^>]*>(?:<f[^>]*?(?:/>|>[^<]*</f>))?<(?:v>[^<]|is>)')
_FAST_NUM = re.compile(rb'<c r="([A-Z]+)\d+"(?: s="\d+")?(?: t="n")?>(?:<f[^>]*?(?:/>|>[^<]*</f>))?<v>([^<]+)</v>')
_FAST_ERR = re.compile(rb'<c r="([A-Z]+)(\d+)"[^>]*? t="e"[^>]*>(?:<f[^>]*?(?:/>|>[^<]*</f>))?<v>([^<]*)</v>')
_FAST_F = re.compile(rb'<c r="([A-Z]+)(\d+)"[^>]*><f\b([^>]*?)(?:/>|>([^<]*)</f>)')
_R_ATTR = re.compile(rb'\br="(\d+)"')
_REF = re.compile(rb'\br="([A-Z]+)\d+"')
_T_ATTR = re.compile(rb'\bt="(\w+)"')
_SI_ATTR = re.compile(rb'\bsi="(\d+)"')
_EPOCH = dt.datetime(1899, 12, 30)


def available():
    return _calamine is not None


def _col_num(letters):
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch) - 64
    return n


# ---- value conversion: calamine cell -> the Value2-style value the shared extraction code expects --------------

def _is_date(v):
    return isinstance(v, (dt.date, dt.time))


def _as_datetime(v):
    if isinstance(v, dt.datetime):
        return v
    if isinstance(v, dt.date):
        return dt.datetime(v.year, v.month, v.day)
    return dt.datetime(1899, 12, 30, v.hour, v.minute, v.second)     # time-only, as Excel's .Value returns it


def _data_value(v):
    """Value2 semantics: numbers are float, empty is None, dates are serial numbers (typed later per column)."""
    t = v.__class__
    if t is str:
        return v if v else None
    if t is float or t is bool:
        return v
    if t is int:
        return float(v)
    if isinstance(v, dt.timedelta):
        return v.total_seconds() / 86400.0
    if _is_date(v):
        d = _as_datetime(v)
        return (d - _EPOCH).total_seconds() / 86400.0
    return None if v is None else str(v)


_DATE_TYPES = frozenset((dt.date, dt.datetime, dt.time))


def _row_values(raw):
    """_data_value over a row, with the two overwhelmingly common types handled inline."""
    return [v if (k := v.__class__) is float else (v or None) if k is str else _data_value(v) for v in raw]


def _display_value(v):
    """For header/preamble cells: a date shows as its ISO text, never as a serial number."""
    if _is_date(v):
        return datetime_to_iso(_as_datetime(v))
    return _data_value(v)


# ---- the independent second reader for .xlsx/.xlsm -------------------------------------------------------------

class XlsxPackage:
    """Sheet-name -> XML part map, workbook-level unsupported content, and a streaming per-sheet cell scan."""

    def __init__(self, path):
        self.zf = zipfile.ZipFile(path)
        self.names = set(self.zf.namelist())
        self.sheet_parts = {}
        self.external = {}
        self.date1904 = False
        self._shared_filled = None
        wb = "xl/workbook.xml"
        rels = self._rels(wb)
        for _, el in iterparse(self.zf.open(wb)):
            if el.tag == NS_MAIN + "sheet":
                target = rels.get(el.get(NS_REL + "id"))
                if target:
                    self.sheet_parts[el.get("name")] = target
            elif el.tag == NS_MAIN + "workbookPr":
                self.date1904 = el.get("date1904") in ("1", "true")
        n = 0
        for _, el in iterparse(self.zf.open(wb)):
            if el.tag == NS_MAIN + "externalReference":
                n += 1
                part = rels.get(el.get(NS_REL + "id"))
                target = self._rels_raw(part).get("externalLinkPath") if part else None
                self.external[str(n)] = posixpath.basename((target or "").replace("\\", "/")) or None

    def _rels_path(self, part):
        return posixpath.join(posixpath.dirname(part), "_rels", posixpath.basename(part) + ".rels")

    def _rels_raw(self, part):
        """{type suffix: target} (raw, unresolved) for a part's relationships."""
        path = self._rels_path(part)
        out = {}
        if path in self.names:
            for _, el in iterparse(self.zf.open(path)):
                if el.tag == NS_PKG + "Relationship":
                    out[el.get("Type", "").rsplit("/", 1)[-1]] = el.get("Target")
        return out

    def _rels(self, part, want_type=None):
        """{rId: resolved part path} (or [paths] of one type when want_type is given)."""
        path = self._rels_path(part)
        out, typed = {}, []
        if path in self.names:
            base = posixpath.dirname(part)
            for _, el in iterparse(self.zf.open(path)):
                if el.tag != NS_PKG + "Relationship" or el.get("TargetMode") == "External":
                    continue
                target = el.get("Target", "")
                full = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join(base, target))
                out[el.get("Id")] = full
                if want_type and el.get("Type", "").endswith("/" + want_type):
                    typed.append(full)
        return typed if want_type else out

    def workbook_unsupported(self):
        found = []
        mashup = 0
        for name in self.names:
            if name.startswith("customXml/item") and name.endswith(".xml") and "/_rels/" not in name:
                with self.zf.open(name) as f:
                    head = f.read(4096)
                if b"DataMashup" in head or b"D\x00a\x00t\x00a\x00M\x00a\x00s\x00h\x00u\x00p" in head:
                    mashup += 1
        if mashup:
            found.append(("power_query", mashup, "Power Query step logic is not read; only whatever values it "
                                                 "last wrote to a sheet are extracted"))
        if any(n.startswith("xl/model/") for n in self.names):
            found.append(("data_model", 1, "the Excel Data Model (Power Pivot) is not read; its tables/measures/"
                                           "relationships are invisible to this platform even if a PivotTable on a "
                                           "sheet displays their output"))
        if self.external:
            sample = ", ".join(v for v in list(self.external.values())[:3] if v)
            found.append(("external_link", len(self.external),
                          "formulas reference other workbook(s) not extracted here" + (f": {sample}" if sample else "")))
        return found

    def sheet_extras(self, sheet_name):
        """(pivot_tables, charts) attached to one sheet, from its relationships."""
        part = self.sheet_parts.get(sheet_name)
        if not part:
            return None, 0
        pivots = len(self._rels(part, "pivotTable"))
        charts = sum(len(self._rels(d, "chart")) for d in self._rels(part, "drawing"))
        return pivots, charts

    def _shared(self):
        """[bool] per shared string: is it non-empty (all a cell count needs; the text itself is not kept)."""
        if self._shared_filled is None:
            self._shared_filled = []
            path = "xl/sharedStrings.xml"
            if path in self.names:
                for _, el in iterparse(self.zf.open(path)):
                    if el.tag == NS_MAIN + "si":
                        self._shared_filled.append(any((t.text or "") for t in el.iter(NS_MAIN + "t")))
                        el.clear()
        return self._shared_filled

    def scan_sheet(self, sheet_name, data_first):
        """One streaming pass over a sheet's XML. Returns a SheetScan, or None if the sheet has no XML part.

        A byte-level scanner over decompressed chunks cut at row boundaries, not an XML tree builder: the sheet
        XML of a 100 MB workbook is ~1 GB, and building an element per cell costs several times what reading the
        values does. Data rows go through whole-chunk regex counting (C speed) when the XML has the plain shape
        Excel writes; header rows, and any file with an unusual shape, go through a precise per-cell scanner.
        Either way, a misread cell would show up as a count/sum mismatch against the other reader and fail the
        run -- never be silently accepted.
        """
        part = self.sheet_parts.get(sheet_name)
        if not part or part not in self.names:
            return None
        shared = self._shared()
        scan = SheetScan()
        state = {"row_no": 0, "shared_refs": {}, "cols": {}}
        fast = all(shared)                   # an empty shared string must be looked up per cell
        buf, row_close, in_data = b"", None, False
        with self.zf.open(part) as f:
            while True:
                chunk = f.read(1 << 23)
                buf += chunk
                if row_close is None:
                    m = _ROW_OPEN.search(buf)
                    if m:
                        row_close = b"</" + m.group(1) + b"row>"
                        fast = fast and not m.group(1)
                if chunk and row_close is not None:
                    cut = buf.rfind(row_close)
                    if cut < 0:
                        continue
                    cut += len(row_close)
                    work, buf = buf[:cut], buf[cut:]
                elif chunk:
                    continue
                else:
                    work, buf = buf, b""
                if b"filterColumn" in work:
                    scan.filter_active = 1
                if fast and work.count(b"<c ") != work.count(b'<c r="'):
                    fast = False
                if not in_data:
                    split = None
                    for rm in _ROW_NUM.finditer(work):
                        if int(rm.group(1)) >= data_first:
                            split = rm.start()
                            break
                    if split is None:
                        self._scan_precise(work, scan, state, shared, data_first)
                        if not chunk:
                            break
                        continue
                    self._scan_precise(work[:split], scan, state, shared, data_first)
                    work = work[split:]
                    in_data = True
                if fast:
                    self._scan_fast(work, scan, state)
                else:
                    self._scan_precise(work, scan, state, shared, data_first)
                if not chunk:
                    break
        return scan

    def _col(self, state, letters):
        col = state["cols"].get(letters)
        if col is None:
            col = state["cols"][letters] = _col_num(letters.decode())
        return col

    def _scan_fast(self, work, scan, state):
        """Data rows only: per-column counts/sums, errors and formulas by whole-chunk regex scans."""
        counted = Counter(_FAST_FILLED.findall(work))
        for letters, n in counted.items():
            col = self._col(state, letters)
            scan.count[col] = scan.count.get(col, 0) + n
            scan.total += n
        sums = defaultdict(float)
        for letters, v in _FAST_NUM.findall(work):
            sums[letters] += float(v)
        for letters, x in sums.items():
            if math.isfinite(x):
                col = self._col(state, letters)
                scan.sums[col] = scan.sums.get(col, 0.0) + x
        if b't="e"' in work:
            for letters, row, text in _FAST_ERR.findall(work):
                if text:
                    scan.errors[(int(row), self._col(state, letters))] = text.decode()
        if b"<f" in work:
            for letters, row, attrs, body in _FAST_F.findall(work):
                scan.formula(int(row), self._col(state, letters), attrs, body, state["shared_refs"], 0)

    def _scan_precise(self, work, scan, state, shared, data_first):
        """Cell by cell: any attribute order, missing r attributes, namespace prefixes, empty shared strings."""
        n_shared = len(shared)
        count, sums, errors = scan.count, scan.sums, scan.errors
        row_no = state["row_no"]
        for rm in _ROW.finditer(work):
            ra = _R_ATTR.search(rm.group(1))
            row_no = int(ra.group(1)) if ra else row_no + 1
            body = rm.group(2)
            if not body:
                continue
            col_no = 0
            counting = row_no >= data_first
            for cm in _C.finditer(body):
                attrs, inner = cm.group(1), cm.group(2)
                ref = _REF.search(attrs)
                col_no = self._col(state, ref.group(1)) if ref else col_no + 1
                if not inner:
                    continue
                tm = _T_ATTR.search(attrs)
                t = tm.group(1) if tm else b"n"
                fm = _F.search(inner) if b"<" in inner else None
                if fm:
                    scan.formula(row_no, col_no, fm.group(1), fm.group(2), state["shared_refs"], data_first)
                vm = None
                if t == b"inlineStr":
                    filled = any(x for x in _TEXT.findall(inner))
                else:
                    vm = _V.search(inner)
                    if vm is None:
                        continue
                    text = vm.group(1)
                    if t == b"s":
                        try:
                            i = int(text)
                            filled = shared[i] if 0 <= i < n_shared else False
                        except ValueError:
                            filled = False
                    elif t == b"e":
                        filled = bool(text)
                        if filled:
                            errors[(row_no, col_no)] = text.decode()
                    else:
                        filled = bool(text)
                if not filled:
                    continue
                scan.total += 1
                if counting:
                    count[col_no] = count.get(col_no, 0) + 1
                    if t == b"n" and vm is not None:
                        try:
                            x = float(vm.group(1))
                        except ValueError:
                            continue
                        if math.isfinite(x):
                            sums[col_no] = sums.get(col_no, 0.0) + x
        state["row_no"] = row_no


class SheetScan:
    __slots__ = ("total", "count", "sums", "errors", "formulas", "filter_active")

    def __init__(self):
        self.total, self.count, self.sums, self.errors = 0, {}, {}, {}
        self.formulas = {}                  # col -> [cells, first sample, {(book, sheet): cells}]
        self.filter_active = 0

    def formula(self, row, col, attrs, body, shared_refs, data_first):
        from ..lineage import formula_refs
        text = html.unescape(body.decode("utf-8", "replace")) if body else None
        si_m = _SI_ATTR.search(attrs or b"")
        si = si_m.group(1) if si_m else None
        if text:
            refs = formula_refs(text)
            if si is not None and b'shared' in (attrs or b""):
                shared_refs[si] = (text, refs)
        elif si is not None and si in shared_refs:
            text, refs = shared_refs[si]
        else:
            refs = []
        if row < data_first:
            return
        rec = self.formulas.setdefault(col, [0, None, {}])
        rec[0] += 1
        if rec[1] is None and text:
            rec[1] = "=" + text
        for key in refs:
            rec[2][key] = rec[2].get(key, 0) + 1


# ---- one sheet --------------------------------------------------------------------------------------------------

def _visibility(meta):
    name = str(meta.visible).rsplit(".", 1)[-1].lower()           # e.g. "SheetVisibleEnum.VeryHidden"
    return {"visible": "visible", "hidden": "hidden", "veryhidden": "very hidden"}.get(name, "visible")


def _is_worksheet(meta):
    return str(meta.typ).rsplit(".", 1)[-1].lower() == "worksheet"


def _merged(sheet):
    out = []
    for rng in getattr(sheet, "merged_cell_ranges", None) or []:
        try:
            (r0, c0), (r1, c1) = rng
        except (TypeError, ValueError):
            continue
        if (r0, c0) != (r1, c1):
            out.append((r0 + 1, c0 + 1, r1 + 1, c1 + 1))
    return out


def _convert_date_column(vals, raw_dates):
    """A date-typed column: dates -> ISO text, any other number -> its plain text (never a fake date)."""
    out, errs = [None] * len(vals), []
    for i, v in enumerate(vals):
        if v is None:
            continue
        d = raw_dates[i]
        if d is not None:
            out[i] = datetime_to_iso(_as_datetime(d))
        elif v.__class__ is float:
            out[i] = fmt_num(v)
        elif v.__class__ is bool:
            out[i] = "TRUE" if v else "FALSE"
        elif v.__class__ is int:
            errs.append((i, next((t for t, c in ERROR_CODE.items() if c == v), "#ERROR")))
        else:
            out[i] = v
    return out, errs


def _filled_raw(v):
    return v is not None and v != ""


def extract_sheet_direct(sheet, con, res, opts, pkg, is1904):
    """Stream the sheet (bounded memory, like the Excel engine's block reads): extent, header, stats, write."""
    t_start = time.perf_counter()
    start = sheet.start or (0, 0)
    if not sheet.height or not sheet.width:
        res.status, res.message = "skipped", "empty sheet"
        return None
    grid_r0, grid_c0 = start[0] + 1, start[1] + 1            # Excel row/col of the reader's first cell

    def raw_iter():
        t0 = time.perf_counter()
        for row in sheet.iter_rows():
            yield row
        res.read_sec += time.perf_counter() - t0

    # ---- pre-scan: true extent (the reader may include styled-but-empty cells) ----------------------------------
    first_idx = last_idx = None
    c_min, c_max = None, None
    for i, row in enumerate(raw_iter()):
        n = len(row)
        if row.count("") == n:                         # the reader reports empty cells as ""
            continue
        if first_idx is None:
            first_idx = i
        last_idx = i
        lo = 0
        while row[lo] == "" and (c_min is None or lo < c_min):
            lo += 1
        hi = n - 1
        while row[hi] == "" and (c_max is None or hi > c_max):
            hi -= 1
        c_min = lo if c_min is None else min(c_min, lo)
        c_max = hi if c_max is None else max(c_max, hi)
    if first_idx is None:
        res.status, res.message = "skipped", "empty sheet"
        return None
    fr, lr = grid_r0 + first_idx, grid_r0 + last_idx
    fc, lc = grid_c0 + c_min, grid_c0 + c_max
    ncols = lc - fc + 1
    res.first_row, res.last_row, res.first_col, res.last_col = fr, lr, fc, lc

    def rows():
        """(xl_row, raw row) over the extent only."""
        for i, row in enumerate(raw_iter()):
            if i < first_idx:
                continue
            if i > last_idx:
                break
            r = list(row[c_min:c_max + 1])
            if len(r) < ncols:
                r += [""] * (ncols - len(r))
            yield grid_r0 + i, r

    rows_per_block = max(1, opts.block_cells // ncols)
    head_raw = []
    for _, r in rows():
        head_raw.append(r)
        if len(head_raw) >= rows_per_block:
            break
    first_blk = [_row_values(r) for r in head_raw]
    display_head = [[_display_value(v) for v in r] for r in head_raw]
    col_width = sum(1 for c in range(ncols) if any(r[c] is not None for r in first_blk[:200]))
    hdr = find_header(first_blk, col_width)
    res.header_row = fr + hdr if hdr is not None else None
    res.data_first = data_first = (fr + hdr + 1) if hdr is not None else fr
    res.header_confidence, res.header_reasons = header_confidence(first_blk, col_width, hdr)

    scan = pkg.scan_sheet(res.sheet_name, data_first) if pkg is not None else None
    charts = 0
    errors_at = {}
    if scan is not None:
        errors_at = scan.errors
        res.formula_cells = sum(rec[0] for rec in scan.formulas.values())
        res.filter_active = scan.filter_active
        res.pivot_tables, charts = pkg.sheet_extras(res.sheet_name)

    merged = _merged(sheet)
    head_merged = [(f"{col_letter(c0)}{r0}:{col_letter(c1)}{r1}",
                    display_head[r0 - fr][c0 - fc] if 0 <= r0 - fr < len(display_head) and 0 <= c0 - fc < ncols
                    else None)
                   for r0, c0, r1, c1 in merged if fr <= r0 <= min(lr, fr + HEADER_SCAN_ROWS)]
    res.merged_areas = len(head_merged)
    res.merged_in_data = int(any(r1 >= data_first for _, _, r1, _ in merged))
    record_structure(res, first_blk, fr, fc, lr, hdr, head_merged)

    def data_blocks():
        """[(xl_rows, value columns, raw columns)] per block of non-blank data rows; blank rows are counted."""
        xl, vals, raws = [], [], []
        for x, raw in rows():
            if x < data_first:
                continue
            row = _row_values(raw)
            if row.count(None) == ncols and not (errors_at and any((x, fc + c) in errors_at for c in range(ncols))):
                counters["blank"] += 1              # an all-error row reads as empty here, but is not blank
                continue
            if errors_at:
                for c in range(ncols):
                    text = errors_at.get((x, fc + c))
                    if text is not None:
                        row[c] = ERROR_CODE.get(text, -2146826259)
            xl.append(x)
            vals.append(row)
            raws.append(raw)
            if len(xl) >= rows_per_block:
                yield xl, list(zip(*vals)), list(zip(*raws))
                xl, vals, raws = [], [], []
        if xl:
            yield xl, list(zip(*vals)), list(zip(*raws))

    # ---- pass 1: statistics over the whole column (same ColStat as the Excel engine) ----------------------------
    stats = [ColStat() for _ in range(ncols)]
    date_info = [[0, False, True] for _ in range(ncols)]      # date cells, any time part, all time-only
    grid_count = [0] * ncols
    counters = {"blank": 0}
    total_rows = 0
    cache = [] if (lr - fr + 1) * ncols <= opts.cache_cells else None   # small enough: convert once, reuse
    for xl, cols, raws in data_blocks():
        if cache is not None:
            cache.append((xl, cols, raws))
        total_rows += len(xl)
        for c in range(ncols):
            col = cols[c]
            stats[c].update(col)
            grid_count[c] += len(col) - col.count(None)
            raw_col = raws[c]
            if _DATE_TYPES.isdisjoint(set(map(type, raw_col))):
                continue
            info = date_info[c]
            for v in raw_col:
                if _is_date(v):
                    info[0] += 1
                    if isinstance(v, dt.datetime) and (v.hour or v.minute or v.second):
                        info[1] = True
                    if not isinstance(v, dt.time):
                        info[2] = False
    headers = list(display_head[hdr]) if hdr is not None else [None] * ncols
    keep = [c for c in range(ncols) if stats[c].has_data or stats[c].nerr or clean_header(headers[c])]
    if not keep:
        res.status, res.message = "skipped", "no usable columns"
        return None
    names, gen, dup = build_columns([headers[c] for c in keep])
    if gen and hdr is not None:
        log("WARN", f"  {gen} blank header(s) -> col_N")
    if dup:
        log("WARN", f"  {dup} duplicate header(s) renamed with a numeric suffix")
    res.header_cells = sum(1 for c in keep if headers[c] is not None)
    plans = [ColPlan(n, fc + c, headers[c], stats[c], opts.strict) for n, c in zip(names, keep)]
    for p in plans:
        n_dates, has_time, all_time = date_info[p.xl_col - fc]
        if n_dates:
            p.make_date("time" if all_time else "datetime" if has_time else "date", {}, opts.strict)
    res.plans = plans
    res.columns, res.data_rows = len(plans), total_rows
    res.grid_count = {fc + c: n for c, n in enumerate(grid_count)}

    # ---- pass 2: write, one transaction per sheet -----------------------------------------------------------
    table = res.table_name
    insert_sql = f"INSERT INTO {q(table)} VALUES ({','.join('?' * (len(plans) + 1))})"
    blank_rows = counters["blank"]
    counters = {"blank": 0}
    keep_idx = [p.xl_col - fc for p in plans]
    con.execute("BEGIN")
    try:
        con.execute(f"CREATE TABLE {q(table)} (\"_xl_row\" INTEGER, " +
                    ", ".join(f"{q(p.name)} {p.sql_type}" for p in plans) + (") STRICT" if opts.strict else ")"))
        err_total = 0
        for xl, cols, raws in (cache if cache is not None else data_blocks()):
            out, err_rows = [xl], []
            for p, c in zip(plans, keep_idx):
                if p.kind == "date":
                    vals, errs = _convert_date_column(cols[c], [v if _is_date(v) else None for v in raws[c]])
                else:
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
        res.blank_rows_skipped, res.error_cells = blank_rows, err_total
        if hdr:
            pre = []
            for k, row in enumerate(display_head[:hdr]):
                for c, v in enumerate(row):
                    if v is not None:
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
    if scan is not None:
        by_col = {p.xl_col: p.name for p in plans}
        for col, (cells, sample, refs) in sorted(scan.formulas.items()):
            name = by_col.get(col)
            if not name:
                continue
            con.execute("INSERT INTO _formulas VALUES (?,?,?,?)", (table, name, 1, (sample or "")[:200]))
            for (book, ref_sheet), n in refs.items():
                if book and book.isdigit():
                    book = pkg.external.get(book) or f"[{book}]"
                con.execute("INSERT INTO _formula_refs VALUES (?,?,?,?,?,?)",
                            (table, name, book, ref_sheet, n, (sample or "")[:200]))
    for area, val in head_merged:
        con.execute("INSERT INTO _merged_areas VALUES (?,?,?)", (res.sheet_name, area, val))
    write_structure(con, res)
    res.status = "extracted"
    if not total_rows:
        res.message = "header only (no data rows)"
    elif res.error_cells:
        res.message = f"{res.error_cells} Excel error cell(s) stored as NULL (see _cell_errors)"
    res.total_sec = time.perf_counter() - t_start
    res.write_sec = res.total_sec - res.read_sec
    return scan, charts


def verify_direct(con, res, scan):
    """Per-column count/sum and whole-sheet completeness, against the XML scan (or the reader's grid)."""
    rows, checks, bad = [], 0, 0
    stored_cells = res.header_cells + res.preamble_cells
    note = "direct engine: independent XML cell scan" if scan is not None else \
           "direct engine: reader's own cell grid (no second reader for this file format)"
    for p in res.plans:
        tcol = q(res.table_name)
        n_sql = con.execute(f"SELECT COUNT({q(p.name)}) FROM {tcol}").fetchone()[0] + p.stat.nerr
        stored_cells += n_sql
        n_ref = scan.count.get(p.xl_col, 0) if scan is not None else res.grid_count.get(p.xl_col, 0)
        ok = int(n_ref == n_sql)
        rows.append((res.table_name, p.name, "counta", float(n_ref), n_sql, ok, note))
        checks += 1
        bad += 1 - ok
        if p.kind in ("int", "real", "mixed") and scan is not None:
            s_sql, a_sql = con.execute(
                f"SELECT TOTAL(CASE WHEN typeof({q(p.name)}) IN ('integer','real') THEN {q(p.name)} END), "
                f"TOTAL(CASE WHEN typeof({q(p.name)}) IN ('integer','real') THEN abs({q(p.name)}) END) "
                f"FROM {tcol}").fetchone()
            s_ref = scan.sums.get(p.xl_col, 0.0)
            ok = int(math.isclose(s_ref, s_sql, rel_tol=1e-9, abs_tol=1e-9 * max(1.0, a_sql)))
            rows.append((res.table_name, p.name, "sum", s_ref, s_sql, ok, note))
            checks += 1
            bad += 1 - ok
    if scan is None:                    # the reader grid has no independent whole-sheet count; say so, don't fake it
        con.executemany("INSERT INTO _verification VALUES (?,?,?,?,?,?,?)", rows)
        return checks, bad
    total = scan.total
    ok = int(total == stored_cells)
    rows.append((res.table_name, "(whole sheet)", "cells_total", float(total), stored_cells, ok,
                 note if ok else "sheet has cells that are not in the database"))
    checks += 1
    bad += 1 - ok
    con.executemany("INSERT INTO _verification VALUES (?,?,?,?,?,?,?)", rows)
    return checks, bad


# ---- one workbook -----------------------------------------------------------------------------------------------

def process_file_direct(src, db_path, opts):
    """Same signature and return value as the Excel engine's process_file: (exit_code, [SheetResult], message)."""
    t_run = time.perf_counter()
    if _calamine is None:
        msg = ("The direct engine needs the python-calamine package (pip install python-calamine), "
               "or use the Excel engine on Windows.")
        log("ERROR", msg)
        return 1, [], msg
    kind = sniff_file(src, getattr(opts, "wrapper_prefixes", ()))
    if kind == "encrypted":
        msg = "The file is password protected (Office encryption). Remove the password and try again."
        log("ERROR", msg)
        return 1, [], msg
    if kind == "other" and not src.lower().endswith(".xls"):
        with open(src, "rb") as f:
            head = f.read(8)
        if not head.startswith(bytes.fromhex("D0CF11E0")):
            kind = "drm"                          # not a zip, not OLE: a wrapper (rights management) only Excel opens
    if kind == "drm":
        msg = ("DRM-wrapped file: only Excel's rights-management agent can open it. Use the Excel engine "
               "(Windows + Excel, [extract] engine = \"excel\" or \"auto\").")
        log("ERROR", msg)
        return 1, [], msg
    partial = db_path + ".partial"
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    results, checks, mismatches = [], 0, 0
    unsupported = []
    con = None
    try:
        t = time.perf_counter()
        wb = _calamine.CalamineWorkbook.from_path(src)
        pkg = None
        if kind == "zip" and src.lower().endswith((".xlsx", ".xlsm")):
            try:
                pkg = XlsxPackage(src)
            except (KeyError, zipfile.BadZipFile) as e:
                log("WARN", f"Could not index the workbook XML ({e}); verifying against the reader's own grid")
        open_s = time.perf_counter() - t
        is1904 = bool(pkg and pkg.date1904)
        metas = list(wb.sheets_metadata)
        log("INFO", f"Opened directly (no Excel) in {open_s:.2f}s | {len(metas)} sheet(s)")
        if pkg is not None:
            for k, count, detail in pkg.workbook_unsupported():
                unsupported.append(("workbook", None, k, count, detail))
                log("WARN", f"Unsupported content: {k} ({count}) - {detail}")
        else:
            unsupported.append(("workbook", None, "reader_limit", 1,
                                "direct engine on this file format: formulas, Power Query, Data Model and external "
                                "links cannot be inspected, and Excel error cells read as empty; use the Excel "
                                "engine for full fidelity"))
        con = open_db(partial)
        used = set()
        for i, meta in enumerate(metas, 1):
            name = meta.name
            if opts.sheets and name.strip().lower() not in opts.sheets:
                continue
            res = SheetResult(i, name, sanitize_table(name, used), _visibility(meta))
            results.append(res)
            t = time.perf_counter()
            if not _is_worksheet(meta):
                res.status, res.message = "skipped", "chart sheet (no cell data)"
            else:
                try:
                    out = extract_sheet_direct(wb.get_sheet_by_name(name), con, res, opts, pkg, is1904)
                    if out is not None and res.status == "extracted":
                        scan, charts = out
                        if charts:
                            unsupported.append(("sheet", name, "chart", charts,
                                                "chart(s) present; their series/source formulas are not extracted, "
                                                "only the underlying cell values (if those cells are otherwise part "
                                                "of the extracted table)"))
                        if opts.verify:
                            c, b = verify_direct(con, res, scan)
                            checks, mismatches = checks + c, mismatches + b
                            if b:
                                log("ERROR", f"  MISMATCH in '{name}': {b} of {c} checks (see _verification)")
                except Exception as e:                               # one bad sheet never loses the others
                    try:
                        con.execute("ROLLBACK")
                    except Exception:
                        pass
                    res.status, res.message = "error", f"{type(e).__name__}: {e}"
                    log("ERROR", f"Sheet '{name}' failed: {res.message}")
            res.total_sec = time.perf_counter() - t
            if is_silent():
                pass
            elif res.status == "extracted":
                print(f"[{i}/{len(metas)}] {name.strip()}: {res.data_rows:,} rows x {res.columns} cols | header row "
                      f"{res.header_row or 'none'} | {res.total_sec:.2f}s", flush=True)
            else:
                print(f"[{i}/{len(metas)}] {name.strip()}: {res.status.upper()} - {res.message}", flush=True)
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"
        log("ERROR", msg)
        if con is not None:
            con.close()
        for p in (partial, partial + "-journal"):
            if os.path.exists(p):
                os.remove(p)
        return 1, results, msg
    write_log(con, results)
    if unsupported:
        con.executemany("INSERT INTO _unsupported VALUES (?,?,?,?,?)", unsupported)
    total_s = time.perf_counter() - t_run
    meta = {"schema_version": SCHEMA_VERSION, "source_path": src, "source_size": os.path.getsize(src),
            "source_modified": dt.datetime.fromtimestamp(os.path.getmtime(src)).isoformat(timespec="seconds"),
            "extracted_at": dt.datetime.now().isoformat(timespec="seconds"), "tool": "xl2ai direct (python-calamine)",
            "engine": "direct", "excel_restarts": 0, "verify_checks": checks, "verify_mismatches": mismatches,
            "total_seconds": round(total_s, 2)}
    con.executemany("INSERT INTO _meta VALUES (?,?)", [(k, str(v)) for k, v in meta.items()])
    con.execute("PRAGMA optimize")
    con.close()
    os.replace(partial, db_path)
    if not is_silent():
        ok = [r for r in results if r.status == "extracted"]
        print(f"Sheets: {len(ok)} extracted, {sum(r.status == 'skipped' for r in results)} skipped, "
              f"{sum(r.status == 'error' for r in results)} failed | rows: {sum(r.data_rows for r in ok):,} | "
              f"verification: {checks} checks, {mismatches} mismatches | {total_s:.2f}s\nDatabase: {db_path}")
    failed = any(r.status == "error" for r in results)
    return (3 if mismatches else 2 if failed else 0), results, ""

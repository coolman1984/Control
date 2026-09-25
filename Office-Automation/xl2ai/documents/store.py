"""A document -> one SQLite database with the same contract as an extracted workbook, plus its text.

    data tables      every table in the document (Word/PDF/PowerPoint/HTML/Markdown/CSV tables, chart data behind
                     PowerPoint charts, sheets of Excel attachments), typed, one per table -- so the catalog, key
                     numbers, relationships, anomalies and report checks treat them exactly like Excel sheets.
                     `_xl_row` is the row number inside the source table (1 = header).
    _doc_blocks      every text block with its location (attachment, page/slide, heading path) -- searchable.
    _doc_entities    codes, money, dates, percentages, e-mails, phones found in each block, with character spans.
    _doc_meta        title/author/dates, or from/to/cc/subject/sent for e-mail.
    _doc_tables      where each data table came from (page, section, caption).
    _doc_attachments every attachment and what became of it.

Attachments are opened recursively (an e-mail's Word file, a zip-free Excel workbook, an e-mail inside an e-mail)
and land in the same database, each block and table tagged with the attachment it came from.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import tempfile
import time

from ..core.log import is_silent, log
from ..extract.common import SCHEMA_VERSION
from ..extract.names import build_columns, col_letter, q, sanitize_table
from ..extract.store import open_db, write_log
from ..sources.detect import sniff_file
from .entities import find_entities, normalize_digits
from .readers import DOC_KINDS, ReaderMissing, kind_of, read_document

DOC_DDL = """
CREATE TABLE _doc_meta (key TEXT, value TEXT, part TEXT);
CREATE TABLE _doc_blocks (id INTEGER PRIMARY KEY, part TEXT, kind TEXT, page INTEGER, section TEXT, level INTEGER,
  text TEXT);
CREATE TABLE _doc_entities (block_id INTEGER, kind TEXT, value TEXT, normalized TEXT, start INTEGER, "end" INTEGER);
CREATE TABLE _doc_tables (table_name TEXT, part TEXT, page INTEGER, section TEXT, caption TEXT, idx INTEGER);
CREATE TABLE _doc_attachments (part TEXT, name TEXT, kind TEXT, size INTEGER, status TEXT, note TEXT);
"""
EXCEL_KINDS = {"xlsx", "xlsm", "xlsb", "xls"}
MAX_DEPTH = 3
_NUMERIC = re.compile(r"^[-+(]?\s*[$€£]?\s*\d[\d,٬]*(?:\.\d+)?\s*%?\)?$")


class _Result:
    """Sheet-like result so the refresh pipeline reports documents the way it reports workbooks."""

    def __init__(self, name, status, rows=0, cols=0, message=""):
        self.sheet_index, self.sheet_name, self.table_name, self.visibility = 0, name, None, "visible"
        self.status, self.message, self.data_rows, self.columns = status, message, rows, cols
        self.header_row = 1 if status == "extracted" else None
        self.first_row = self.first_col = 1
        self.last_row, self.last_col = rows + 1, cols
        self.blank_rows_skipped = self.error_cells = 0
        self.formula_cells = self.pivot_tables = self.filter_active = None
        self.merged_areas, self.merged_in_data = 0, None
        self.read_sec = self.write_sec = self.total_sec = 0.0
        self.header_confidence, self.header_reasons, self.plans = None, None, []


def parse_number(text):
    """'1,234.50' -> 1234.5, '(300)' -> -300, '12%' -> 0.12, Arabic digits read; None if it is not a number."""
    s = normalize_digits(str(text)).strip()
    if not s or not _NUMERIC.match(s):
        return None
    neg = s.startswith("(") and s.endswith(")") or s.startswith("-")
    pct = s.endswith("%") or s.endswith("%)")
    s = re.sub(r"[^\d.]", "", s.replace("٬", ","))
    if s.count(".") > 1 or not s:
        return None
    v = float(s)
    v = -v if neg else v
    return v / 100 if pct else v


def _typed_columns(rows):
    """Per column: 'int' | 'real' | 'text' | 'mixed' and converted values (numbers when >= 90% parse)."""
    width = len(rows[0]) if rows else 0
    kinds, cols = [], []
    for c in range(width):
        raw = [r[c] for r in rows]
        filled = [v for v in raw if v != ""]
        nums = [parse_number(v) for v in filled]
        ok = sum(n is not None for n in nums)
        if filled and ok / len(filled) >= 0.9:
            conv = [None if v == "" else (parse_number(v) if parse_number(v) is not None else v) for v in raw]
            all_int = all(isinstance(v, float) and v.is_integer() for v in conv if v is not None)
            kind = "mixed" if ok < len(filled) else ("int" if all_int else "real")
            if kind == "int":
                conv = [int(v) if isinstance(v, float) else v for v in conv]
        else:
            conv = [None if v == "" else v for v in raw]
            kind = "text"
        kinds.append(kind)
        cols.append(conv)
    return kinds, cols


SQL_TYPES = {"int": "INTEGER", "real": "REAL", "text": "TEXT", "mixed": "BLOB"}


def _write_table(con, table, used, results, doc_tables, stem=""):
    rows = table.rows
    header = rows[0] if table.has_header else [f"col_{i + 1}" for i in range(len(rows[0]))]
    body = rows[1:] if table.has_header else rows
    first_xl = 2 if table.has_header else 1
    # the document's own name leads, so "table 1" of two documents never collide in cross-file SQL
    name = sanitize_table(f"{stem} {table.name()}" + (f" {table.caption}" if table.caption else ""), used)[:60]
    names, _, _ = build_columns(header)
    kinds, cols = _typed_columns(body)
    con.execute(f"CREATE TABLE {q(name)} (\"_xl_row\" INTEGER, " +
                ", ".join(f"{q(n)} {SQL_TYPES[k]}" for n, k in zip(names, kinds)) + ")")
    con.executemany(f"INSERT INTO {q(name)} VALUES ({','.join('?' * (len(names) + 1))})",
                    [[first_xl + i] + [col[i] for col in cols] for i in range(len(body))])
    checks = 0
    for pos, (n, k, h, col) in enumerate(zip(names, kinds, header, cols), 1):
        stored = con.execute(f"SELECT COUNT({q(n)}) FROM {q(name)}").fetchone()[0]
        read = sum(1 for r in body if r[pos - 1] != "")
        con.execute("INSERT INTO _columns VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (name, pos, n, h, pos, col_letter(pos), SQL_TYPES[k], k, None, stored, 0))
        con.execute("INSERT INTO _verification VALUES (?,?,?,?,?,?,?)",
                    (name, n, "counta", float(read), stored, int(read == stored),
                     "document table: cells stored vs cells read"))
        checks += 1
    sheet = table.name() + (f" -- {table.caption}" if table.caption else "")
    r = _Result(sheet, "extracted", len(body), len(names))
    r.table_name = name
    results.append(r)
    doc_tables.append((name, table.part, table.page, table.section, table.caption, table.index))
    return checks


def _read(path, data, kind, prefer_office, wrappers):
    """Direct reader first; Office (COM) when the file is rights-managed or a legacy binary format."""
    from .com_readers import COM_READERS
    wrapped = False
    if data is None:
        sniff = sniff_file(path, wrappers)
        wrapped = sniff == "drm" or (sniff == "other" and kind in ("docx", "docm", "pptx", "pptm"))
    if kind in ("doc", "ppt") or wrapped or prefer_office:
        if kind not in COM_READERS:
            raise ReaderMissing(f"this .{kind} file is rights-managed; only Office can open it")
        if data is not None:
            with tempfile.TemporaryDirectory() as tmp:
                p = os.path.join(tmp, f"attachment.{kind}")
                with open(p, "wb") as f:
                    f.write(data)
                return COM_READERS[kind](p)
        return COM_READERS[kind](path)
    return read_document(path, data, kind)


def extract_document(src, db_path, opts):
    """Same signature as the workbook engines: (exit_code, [results], message)."""
    t0 = time.perf_counter()
    kind = kind_of(src)
    wrappers = tuple(getattr(opts, "wrapper_prefixes", ()) or ())
    prefer_office = False
    try:
        doc = _read(src, None, kind, prefer_office, wrappers)
    except Exception as e:                                  # unreadable file: say why, write nothing
        msg = f"{type(e).__name__}: {e}" if not isinstance(e, ReaderMissing) else str(e)
        log("ERROR", msg)
        return 1, [], msg
    partial = db_path + ".partial"
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    con = open_db(partial)
    con.executescript(DOC_DDL)
    results, used, doc_tables, checks, excel_later = [], set(), [], 0, []
    stem = os.path.splitext(os.path.basename(src))[0]
    unsupported = [("workbook", None, "reader_limit", 1, w) for w in doc.warnings]

    def store(d, part, depth):
        nonlocal checks
        for k, v in d.meta.items():
            con.execute("INSERT INTO _doc_meta VALUES (?,?,?)", (k, None if v is None else str(v), part))
        for b in d.blocks:
            cur = con.execute("INSERT INTO _doc_blocks (part, kind, page, section, level, text) VALUES (?,?,?,?,?,?)",
                              (part or b.part, b.kind, b.page, b.section, b.level, b.text))
            bid = cur.lastrowid
            con.executemany("INSERT INTO _doc_entities VALUES (?,?,?,?,?,?)",
                            [(bid, k, raw, norm, s, e) for k, raw, norm, s, e in find_entities(b.text)])
        for t in d.tables:
            t.part = part or t.part
            checks += _write_table(con, t, used, results, doc_tables, stem)
        for a in d.attachments:
            akind = kind_of(a.name)
            label = f"{part} > {a.name}" if part else a.name
            status, note = "stored", ""
            if depth >= MAX_DEPTH:
                status, note = "skipped", "attachment nested too deep"
            elif akind in DOC_KINDS:
                try:
                    inner = _read(a.name, a.data, akind, False, wrappers)
                    store(inner, label, depth + 1)
                    status = "read"
                    for w in inner.warnings:
                        unsupported.append(("workbook", None, "reader_limit", 1, f"{label}: {w}"))
                except Exception as e:
                    status, note = "error", f"{type(e).__name__}: {e}"
            elif akind in EXCEL_KINDS:
                excel_later.append((part, a, akind, label))     # needs ATTACH: done after this transaction
                continue
            else:
                status, note = "skipped", "not a readable document type"
            con.execute("INSERT INTO _doc_attachments VALUES (?,?,?,?,?,?)",
                        (part, a.name, akind, len(a.data), status, note))
            if status == "error":
                unsupported.append(("workbook", None, "reader_limit", 1, f"attachment {label}: {note}"))

    try:
        con.execute("BEGIN")
        store(doc, "", 0)
        con.executemany("INSERT INTO _doc_tables VALUES (?,?,?,?,?,?)", doc_tables)
        con.execute("COMMIT")
        for part, a, akind, label in excel_later:
            status, note = _excel_attachment(con, a, label, opts, used, results)
            con.execute("INSERT INTO _doc_attachments VALUES (?,?,?,?,?,?)",
                        (part, a.name, akind, len(a.data), status, note))
            if status == "error":
                unsupported.append(("workbook", None, "reader_limit", 1, f"attachment {label}: {note}"))
    except Exception as e:
        con.close()
        for p in (partial, partial + "-journal"):
            if os.path.exists(p):
                os.remove(p)
        msg = f"{type(e).__name__}: {e}"
        log("ERROR", msg)
        return 1, [], msg
    if not results:
        results.append(_Result("(text only)", "skipped", message="no tables; text is in _doc_blocks"))
    write_log(con, results)
    if unsupported:
        con.executemany("INSERT INTO _unsupported VALUES (?,?,?,?,?)", unsupported)
    blocks = con.execute("SELECT COUNT(*) FROM _doc_blocks").fetchone()[0]
    meta = {"schema_version": SCHEMA_VERSION, "source_path": src, "source_size": os.path.getsize(src),
            "source_modified": dt.datetime.fromtimestamp(os.path.getmtime(src)).isoformat(timespec="seconds"),
            "extracted_at": dt.datetime.now().isoformat(timespec="seconds"), "tool": "xl2ai documents",
            "engine": "document", "document_kind": doc.kind, "pages": doc.pages or "", "blocks": blocks,
            "excel_restarts": 0, "verify_checks": checks, "verify_mismatches": 0,
            "total_seconds": round(time.perf_counter() - t0, 2)}
    con.executemany("INSERT INTO _meta VALUES (?,?)", [(k, str(v)) for k, v in meta.items()])
    bad = con.execute("SELECT COUNT(*) FROM _verification WHERE ok = 0").fetchone()[0]
    con.execute("UPDATE _meta SET value=? WHERE key='verify_mismatches'", (str(bad),))
    con.close()
    os.replace(partial, db_path)
    if not is_silent():
        print(f"{os.path.basename(src)}: {doc.kind} | {blocks} text block(s) | "
              f"{sum(r.status == 'extracted' for r in results)} table(s)", flush=True)
    return (3 if bad else 0), results, ""


def _excel_attachment(con, att, label, opts, used, results):
    """An Excel file attached to an e-mail: extracted with the workbook engine, its sheets copied in here."""
    from ..extract.pipeline import process_file
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, os.path.basename(att.name))
        with open(path, "wb") as f:
            f.write(att.data)
        db = os.path.join(tmp, "att.db")
        code, _, message = process_file(path, db, opts)
        if code in (1,) or not os.path.exists(db):
            return "error", message or "could not be extracted"
        con.execute("ATTACH DATABASE ? AS att", (db,))
        try:
            logs = con.execute("""SELECT sheet_name, table_name, data_rows, columns, header_row, header_confidence,
                                         header_reasons FROM att._extraction_log WHERE status='extracted'""").fetchall()
            for sheet, table, nrows, ncols, hdr, hconf, hreasons in logs:
                new = sanitize_table(f"{os.path.splitext(os.path.basename(label.split(' > ')[0]))[0]} {sheet}", used)[:60]
                con.execute(f"CREATE TABLE {q(new)} AS SELECT * FROM att.{q(table)}")
                con.execute("""INSERT INTO _columns SELECT ?, position, sql_name, original_header, xl_col, xl_col_letter,
                               sql_type, kind, date_format, non_null, error_cells FROM att._columns WHERE table_name=?""",
                            (new, table))
                con.execute("""INSERT INTO _verification SELECT ?, column_name, check_name, excel_value, sqlite_value,
                               ok, note FROM att._verification WHERE table_name=?""", (new, table))
                r = _Result(f"{label} > {sheet}", "extracted", int(nrows or 0), int(ncols or 0))
                r.table_name, r.header_row, r.header_confidence = new, hdr, hconf
                r.header_reasons = json.loads(hreasons) if hreasons else None
                results.append(r)
        finally:
            con.execute("DETACH DATABASE att")
    return "read", f"{len(logs)} sheet(s) extracted"

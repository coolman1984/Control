"""SQLite output: schema, atomic partial DB, log table, output path resolution."""
from __future__ import annotations

import os
import sqlite3


DDL = """
CREATE TABLE _meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE _extraction_log (id INTEGER PRIMARY KEY, sheet_index INTEGER, sheet_name TEXT, table_name TEXT,
  visibility TEXT, status TEXT, message TEXT, header_row INTEGER, first_row INTEGER, last_row INTEGER,
  first_col INTEGER, last_col INTEGER, data_rows INTEGER, columns INTEGER, blank_rows_skipped INTEGER,
  error_cells INTEGER, formula_cells INTEGER, pivot_tables INTEGER, filter_active INTEGER, merged_areas INTEGER,
  merged_in_data INTEGER, read_sec REAL, write_sec REAL, total_sec REAL,
  header_confidence REAL, header_reasons TEXT);
CREATE TABLE _columns (table_name TEXT, position INTEGER, sql_name TEXT, original_header TEXT, xl_col INTEGER,
  xl_col_letter TEXT, sql_type TEXT, kind TEXT, date_format TEXT, non_null INTEGER, error_cells INTEGER);
CREATE TABLE _cell_errors (table_name TEXT, xl_row INTEGER, xl_col INTEGER, error TEXT);
CREATE TABLE _sheet_preamble (table_name TEXT, xl_row INTEGER, xl_col INTEGER, value BLOB);
CREATE TABLE _merged_areas (sheet_name TEXT, area TEXT, value BLOB);
CREATE TABLE _verification (table_name TEXT, column_name TEXT, check_name TEXT, excel_value REAL,
  sqlite_value REAL, ok INTEGER, note TEXT);
CREATE TABLE _unsupported (scope TEXT, sheet_name TEXT, kind TEXT, count INTEGER, detail TEXT);
CREATE TABLE _formulas (table_name TEXT, sql_name TEXT, has_formula INTEGER, sample_r1c1 TEXT);
CREATE TABLE _regions (table_name TEXT, region_no INTEGER, first_row INTEGER, first_col INTEGER, last_row INTEGER,
  last_col INTEGER, header_row INTEGER, kind TEXT, cells INTEGER, complete INTEGER);
CREATE TABLE _formula_refs (table_name TEXT, sql_name TEXT, ref_workbook TEXT, ref_sheet TEXT, cells INTEGER,
  sample TEXT);
CREATE TABLE _header_groups (table_name TEXT, xl_col INTEGER, sql_name TEXT, path TEXT, method TEXT);
"""


def open_db(partial):
    for p in (partial, partial + "-journal"):
        if os.path.exists(p):
            os.remove(p)
    con = sqlite3.connect(partial, isolation_level=None)
    con.execute("PRAGMA journal_mode=MEMORY")     # rollback still works (unlike OFF); DB is disposable until renamed
    con.execute("PRAGMA synchronous=OFF")
    con.execute("PRAGMA temp_store=MEMORY")
    con.execute("PRAGMA cache_size=-262144")
    con.executescript(DDL)
    return con

def resolve_db_path(src, out):
    stem = os.path.splitext(os.path.basename(src))[0].strip()
    if out and out.lower().endswith((".db", ".sqlite", ".sqlite3")):
        return os.path.abspath(out)
    return os.path.abspath(os.path.join(out or os.path.dirname(src), stem + ".db"))


def write_log(con, results):
    import json
    con.executemany(
        "INSERT INTO _extraction_log (sheet_index, sheet_name, table_name, visibility, status, message, header_row,"
        " first_row, last_row, first_col, last_col, data_rows, columns, blank_rows_skipped, error_cells,"
        " formula_cells, pivot_tables, filter_active, merged_areas, merged_in_data, read_sec, write_sec, total_sec,"
        " header_confidence, header_reasons)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [(r.sheet_index, r.sheet_name, r.table_name if r.status == "extracted" else None, r.visibility, r.status,
          r.message, r.header_row, r.first_row, r.last_row, r.first_col, r.last_col, r.data_rows, r.columns,
          r.blank_rows_skipped, r.error_cells, r.formula_cells, r.pivot_tables, r.filter_active, r.merged_areas,
          r.merged_in_data, round(r.read_sec, 3), round(r.write_sec, 3), round(r.total_sec, 3),
          getattr(r, "header_confidence", None),
          json.dumps(getattr(r, "header_reasons", None), ensure_ascii=False) if getattr(r, "header_reasons", None) else None)
         for r in results])

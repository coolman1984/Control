"""Build the run-level catalog: a small registry that gives every extracted table/column a stable identity.

The catalog never copies business rows. It stores metadata and points query/analysis stages at the verified per-source
SQLite databases created by extraction.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import unicodedata

from .core.config import load_config
from .core.errors import Xl2aiError
from .core.runs import current_run_id, load_manifest
from .core.sqliteutil import ro_connection

DDL = """
CREATE TABLE _catalog_meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE _sources (
  source_id TEXT PRIMARY KEY, path TEXT, sha256 TEXT, size INTEGER, mtime TEXT, hash_mode TEXT,
  db_rel TEXT NOT NULL, reused INTEGER NOT NULL DEFAULT 0, reused_from_run TEXT
);
CREATE TABLE _tables (
  table_id TEXT PRIMARY KEY, source_id TEXT NOT NULL, sheet_name TEXT NOT NULL, table_name TEXT NOT NULL,
  db_rel TEXT NOT NULL, row_count INTEGER NOT NULL, column_count INTEGER NOT NULL, header_row INTEGER,
  visibility TEXT, schema_fingerprint TEXT NOT NULL, header_confidence REAL, header_reasons TEXT
);
CREATE TABLE _table_kind (
  table_id TEXT PRIMARY KEY, kind TEXT NOT NULL, confidence REAL, method TEXT, reasons TEXT
);
CREATE TABLE _row_flags (
  table_id TEXT NOT NULL, xl_row INTEGER NOT NULL, flag TEXT NOT NULL, detail TEXT,
  PRIMARY KEY(table_id, xl_row, flag)
);
CREATE TABLE _column_roles (
  column_id TEXT PRIMARY KEY, table_id TEXT NOT NULL, role TEXT NOT NULL, confidence REAL, method TEXT,
  reasons TEXT, unit TEXT, currency TEXT
);
CREATE TABLE _table_grain (
  table_id TEXT PRIMARY KEY, columns_json TEXT, description TEXT NOT NULL, status TEXT NOT NULL,
  confidence REAL, method TEXT
);
CREATE TABLE _time_coverage (
  table_id TEXT NOT NULL, column_id TEXT NOT NULL, min_value TEXT, max_value TEXT,
  PRIMARY KEY(table_id, column_id)
);
CREATE TABLE _duplicate_candidates (
  id TEXT PRIMARY KEY, table_id_a TEXT NOT NULL, table_id_b TEXT NOT NULL, method TEXT, score REAL, evidence TEXT
);
CREATE TABLE _repairs (
  id TEXT PRIMARY KEY, table_id TEXT NOT NULL, column_id TEXT NOT NULL, xl_row INTEGER,
  original_value TEXT, repaired_value TEXT, rule TEXT NOT NULL
);
CREATE INDEX idx_repairs_table ON _repairs(table_id);
CREATE TABLE _columns (
  column_id TEXT PRIMARY KEY, table_id TEXT NOT NULL, position INTEGER NOT NULL, name TEXT NOT NULL,
  original_header TEXT, xl_col INTEGER, xl_col_letter TEXT, sql_type TEXT, kind TEXT,
  non_null INTEGER, error_cells INTEGER
);
CREATE TABLE _table_profiles (
  table_id TEXT PRIMARY KEY, row_count INTEGER NOT NULL, row_fingerprint TEXT, row_hash_mode TEXT
);
CREATE TABLE _row_hashes (
  table_id TEXT NOT NULL, row_hash TEXT NOT NULL, n INTEGER NOT NULL,
  PRIMARY KEY(table_id, row_hash)
);
CREATE TABLE _profile_columns (
  column_id TEXT PRIMARY KEY, n INTEGER, nulls INTEGER, distinct_count INTEGER, min_value TEXT, max_value TEXT,
  mean REAL, top_k TEXT, sample TEXT
);
CREATE TABLE _dq_findings (
  id TEXT PRIMARY KEY, code TEXT, severity TEXT, table_id TEXT, column_id TEXT, count INTEGER,
  examples TEXT, message TEXT
);
CREATE TABLE _keys (
  id TEXT PRIMARY KEY, table_id TEXT, columns_json TEXT, uniqueness REAL, null_rate REAL,
  status TEXT, method TEXT, score REAL
);
CREATE TABLE _relationships (
  id TEXT PRIMARY KEY, from_column TEXT, to_column TEXT, kind TEXT, containment REAL,
  status TEXT, method TEXT, score REAL, evidence TEXT
);
CREATE TABLE _dictionary (
  term TEXT, meaning TEXT, aliases TEXT, unit TEXT, applies_to TEXT, status TEXT, origin TEXT,
  pack TEXT, pack_version TEXT, PRIMARY KEY(term, pack)
);
CREATE TABLE _rule_results (
  rule_id TEXT, pack TEXT, pack_version TEXT, status TEXT, expected TEXT, actual TEXT,
  severity TEXT, message TEXT, evidence TEXT, PRIMARY KEY(rule_id, pack)
);
CREATE TABLE _kpi_results (
  kpi_id TEXT, pack TEXT, pack_version TEXT, value TEXT, unit TEXT, dims TEXT,
  definition_ref TEXT, evidence TEXT, PRIMARY KEY(kpi_id, pack)
);
CREATE TABLE _changes (
  id TEXT PRIMARY KEY, kind TEXT, severity TEXT, subject TEXT, before_value TEXT, after_value TEXT, evidence TEXT
);
CREATE TABLE _unsupported (
  id TEXT PRIMARY KEY, source_id TEXT NOT NULL, table_id TEXT, scope TEXT, sheet_name TEXT, kind TEXT,
  count INTEGER, detail TEXT
);
CREATE TABLE _formulas (
  column_id TEXT PRIMARY KEY, table_id TEXT NOT NULL, has_formula INTEGER, sample_r1c1 TEXT
);
CREATE TABLE _regions (
  table_id TEXT NOT NULL, region_no INTEGER, first_row INTEGER, first_col INTEGER, last_row INTEGER,
  last_col INTEGER, header_row INTEGER, kind TEXT, cells INTEGER, complete INTEGER,
  PRIMARY KEY (table_id, region_no)
);
CREATE TABLE _header_groups (
  column_id TEXT PRIMARY KEY, table_id TEXT NOT NULL, xl_col INTEGER, path TEXT, method TEXT
);
CREATE TABLE _formula_refs (
  column_id TEXT NOT NULL, table_id TEXT NOT NULL, ref_workbook TEXT, ref_sheet TEXT, cells INTEGER, sample TEXT
);
CREATE TABLE _lineage (
  id TEXT PRIMARY KEY, table_id TEXT NOT NULL, column_id TEXT, ref_kind TEXT, ref_workbook TEXT, ref_sheet TEXT,
  target_source_id TEXT, target_table_id TEXT, status TEXT, cells INTEGER, sample TEXT, method TEXT
);
CREATE TABLE _digest (
  id TEXT PRIMARY KEY, table_id TEXT NOT NULL, section TEXT NOT NULL, measure TEXT, dim TEXT, key TEXT,
  value REAL, rows INTEGER, share REAL, rank INTEGER, sql TEXT
);
CREATE INDEX idx_digest_table ON _digest(table_id, section, dim, rank);
CREATE TABLE _digest_tables (
  table_id TEXT PRIMARY KEY, status TEXT, reason TEXT, excluded_rows INTEGER, measures_json TEXT,
  units_json TEXT, dims_json TEXT, date_column TEXT
);
CREATE TABLE IF NOT EXISTS _anomalies (
  id TEXT PRIMARY KEY, table_id TEXT NOT NULL, column_name TEXT, kind TEXT NOT NULL, severity TEXT,
  count INTEGER, detail TEXT, examples TEXT, sql TEXT
);
CREATE TABLE IF NOT EXISTS _reconciliation (
  id TEXT PRIMARY KEY, report_table_id TEXT NOT NULL, report_column TEXT, label_column TEXT,
  source_table_id TEXT, source_column TEXT, dim_column TEXT, agg TEXT, compared INTEGER, matched INTEGER,
  status TEXT, mismatches TEXT, sql TEXT
);
CREATE TABLE _documents (
  source_id TEXT PRIMARY KEY, kind TEXT, title TEXT, author TEXT, created TEXT, modified TEXT, sent TEXT,
  sender TEXT, recipients TEXT, subject TEXT, pages INTEGER, blocks INTEGER, tables INTEGER, attachments INTEGER
);
CREATE TABLE _blocks (
  block_id TEXT PRIMARY KEY, source_id TEXT NOT NULL, part TEXT, kind TEXT, page INTEGER, section TEXT,
  level INTEGER, ord INTEGER, text TEXT
);
CREATE INDEX idx_blocks_source ON _blocks(source_id, ord);
CREATE TABLE _entities (
  source_id TEXT, block_id TEXT, kind TEXT, value TEXT, normalized TEXT, start INTEGER, "end" INTEGER
);
CREATE INDEX idx_entities_norm ON _entities(kind, normalized);
CREATE TABLE _table_origin (
  table_id TEXT PRIMARY KEY, part TEXT, page INTEGER, section TEXT, caption TEXT
);
CREATE TABLE _mentions (
  normalized TEXT, kind TEXT, source_id TEXT, block_id TEXT, table_id TEXT, column_name TEXT, rows INTEGER,
  first_row INTEGER
);
CREATE INDEX idx_columns_table ON _columns(table_id);
CREATE INDEX idx_unsupported_table ON _unsupported(table_id);
CREATE INDEX idx_tables_source ON _tables(source_id);
CREATE INDEX idx_dq_table ON _dq_findings(table_id);
CREATE INDEX idx_rel_from ON _relationships(from_column);
"""


def q(name):
    return '"' + str(name).replace('"', '""') + '"'


def _schema_fingerprint(columns):
    body = [(int(c["position"]), c["sql_name"], c["sql_type"], c["kind"], int(c["xl_col"])) for c in columns]
    return hashlib.sha256(json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def _identity_text(value):
    return unicodedata.normalize("NFKC", str(value or "")).strip().casefold()


def _table_id(source_id, sheet_name):
    # One logical table per sheet is the current extraction contract. Schema is deliberately NOT part of identity.
    digest = hashlib.sha256(_identity_text(sheet_name).encode("utf-8")).hexdigest()[:12]
    return f"{source_id}/sheet-{digest}"


def _column_id(table_id, sql_name):
    # Extractor SQL names are unique inside a table. Position/type changes therefore do not rewrite identity.
    digest = hashlib.sha256(_identity_text(sql_name).encode("utf-8")).hexdigest()[:12]
    return f"{table_id}/col-{digest}"


def _ingest_document(con, src, sid, src_tables, sheet_to_table):
    """Text blocks, entities and metadata of a document source -> catalog (+ full-text index)."""
    from .documents.tools import index_text
    meta = {}
    for k, v, part in src.execute("SELECT key, value, part FROM _doc_meta"):
        if not part:
            meta[k] = v
    kind = dict(src.execute("SELECT key, value FROM _meta")).get("document_kind")
    pages = dict(src.execute("SELECT key, value FROM _meta")).get("pages")
    n_blocks = src.execute("SELECT COUNT(*) FROM _doc_blocks").fetchone()[0]
    n_att = src.execute("SELECT COUNT(*) FROM _doc_attachments").fetchone()[0] if "_doc_attachments" in src_tables else 0
    n_tab = src.execute("SELECT COUNT(*) FROM _extraction_log WHERE status='extracted'").fetchone()[0]
    con.execute("INSERT OR REPLACE INTO _documents VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (sid, kind, meta.get("title"), meta.get("author"), meta.get("created"), meta.get("modified"),
                 meta.get("sent") or meta.get("date"), meta.get("from"),
                 "; ".join(x for x in (meta.get("to"), meta.get("cc")) if x) or None, meta.get("subject"),
                 int(pages) if pages and str(pages).isdigit() else None, n_blocks, n_tab, n_att))
    fts = con.execute("SELECT 1 FROM sqlite_master WHERE name='_blocks_fts'").fetchone() is not None
    for bid, part, bkind, page, section, level, text in src.execute(
            "SELECT id, part, kind, page, section, level, text FROM _doc_blocks ORDER BY id"):
        block_id = f"{sid}#{bid}"
        cur = con.execute("INSERT INTO _blocks VALUES (?,?,?,?,?,?,?,?,?)",
                          (block_id, sid, part, bkind, page, section, level, bid, text))
        if fts:
            con.execute("INSERT INTO _blocks_fts(rowid, text) VALUES (?,?)",
                        (cur.lastrowid, index_text(" ".join(x for x in (section, text) if x))))
    con.executemany("INSERT INTO _entities VALUES (?,?,?,?,?,?,?)",
                    [(sid, f"{sid}#{b}", k, v, n, s, e) for b, k, v, n, s, e in src.execute(
                        'SELECT block_id, kind, value, normalized, start, "end" FROM _doc_entities')])
    if "_doc_tables" in src_tables:
        for table_name, part, page, section, caption in src.execute(
                "SELECT table_name, part, page, section, caption FROM _doc_tables"):
            row = con.execute("SELECT table_id FROM _tables WHERE source_id=? AND table_name=?",
                              (sid, table_name)).fetchone()
            if row:
                con.execute("INSERT OR REPLACE INTO _table_origin VALUES (?,?,?,?,?)",
                            (row[0], part, page, section, caption))


def _extract_stage(manifest):
    return next((s for s in manifest.get("stages", []) if s.get("name") == "extract"), None)


def build_catalog(cfg, run_id, manifest=None):
    manifest = manifest or load_manifest(cfg, run_id)
    stage = _extract_stage(manifest)
    if not stage or stage.get("status") != "passed":
        raise Xl2aiError("E_STAGE_INPUT", "catalog needs a passed extract stage")
    run_dir = os.path.join(cfg.runs_dir, run_id)
    out = os.path.join(run_dir, "catalog.db")
    partial = out + ".partial"
    for p in (partial, partial + "-journal"):
        try:
            os.remove(p)
        except FileNotFoundError:
            pass

    inputs = {x["source_id"]: x for x in manifest.get("inputs", [])}
    con = sqlite3.connect(partial)
    try:
        con.executescript(DDL)
        try:                          # full-text index of document text; optional (needs SQLite's FTS5)
            con.execute('CREATE VIRTUAL TABLE _blocks_fts USING fts5(text, tokenize="unicode61 remove_diacritics 2")')
        except sqlite3.OperationalError:
            pass
        con.execute("INSERT INTO _catalog_meta VALUES (?,?)", ("run_id", run_id))
        con.execute("INSERT INTO _catalog_meta VALUES (?,?)", ("contract_version", str(manifest.get("contract_version", ""))))
        for rec in stage.get("details", {}).get("sources", []):
            if rec.get("exit_code") != 0 or not rec.get("db"):
                continue
            sid = rec["source_id"]
            inp = inputs.get(sid, {})
            con.execute("INSERT INTO _sources VALUES (?,?,?,?,?,?,?,?,?)",
                        (sid, inp.get("path"), inp.get("sha256"), inp.get("size"), inp.get("mtime"), inp.get("hash_mode"),
                         rec["db"], int(bool(rec.get("reused"))), rec.get("reused_from_run")))
            srcdb = os.path.join(run_dir, rec["db"].replace("/", os.sep))
            with ro_connection(srcdb) as src:
                src_tables = {r[0] for r in src.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
                log_cols = {r[1] for r in src.execute("PRAGMA table_info(_extraction_log)").fetchall()}
                has_header_conf = {"header_confidence", "header_reasons"} <= log_cols
                extra = ", header_confidence, header_reasons" if has_header_conf else ""
                logs = src.execute(f"""SELECT sheet_name, table_name, data_rows, columns, header_row, visibility{extra}
                                      FROM _extraction_log
                                      WHERE status='extracted' AND table_name IS NOT NULL
                                      ORDER BY sheet_index""").fetchall()
                sheet_to_table = {}
                for log_row in logs:
                    sheet_name, table_name, rows, ncols, header_row, visibility = log_row[:6]
                    hdr_conf, hdr_reasons = log_row[6:8] if has_header_conf else (None, None)
                    col_rows = src.execute("""SELECT position, sql_name, original_header, xl_col, xl_col_letter,
                                                     sql_type, kind, non_null, error_cells
                                              FROM _columns WHERE table_name=? ORDER BY position""", (table_name,)).fetchall()
                    cols = [{"position": r[0], "sql_name": r[1], "original_header": r[2], "xl_col": r[3],
                             "xl_col_letter": r[4], "sql_type": r[5], "kind": r[6], "non_null": r[7],
                             "error_cells": r[8]} for r in col_rows]
                    fp = _schema_fingerprint(cols)
                    table_id = _table_id(sid, sheet_name)
                    sheet_to_table[sheet_name] = table_id
                    con.execute("INSERT INTO _tables VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                                (table_id, sid, sheet_name, table_name, rec["db"], int(rows or 0), int(ncols or len(cols)),
                                 header_row, visibility, fp, hdr_conf, hdr_reasons))
                    col_ids = {}
                    for c in cols:
                        column_id = _column_id(table_id, c["sql_name"])
                        col_ids[c["sql_name"]] = column_id
                        con.execute("INSERT INTO _columns VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                                    (column_id, table_id, int(c["position"]), c["sql_name"], c["original_header"],
                                     c["xl_col"], c["xl_col_letter"], c["sql_type"], c["kind"],
                                     c["non_null"], c["error_cells"]))
                    if "_formulas" in src_tables:
                        for sql_name, has_formula, sample in src.execute(
                            "SELECT sql_name,has_formula,sample_r1c1 FROM _formulas WHERE table_name=?",
                            (table_name,)).fetchall():
                            cid = col_ids.get(sql_name)
                            if cid:
                                con.execute("INSERT OR REPLACE INTO _formulas VALUES (?,?,?,?)",
                                            (cid, table_id, int(has_formula), sample))
                    if "_regions" in src_tables:
                        con.executemany("INSERT OR REPLACE INTO _regions VALUES (?,?,?,?,?,?,?,?,?,?)",
                                        [(table_id,) + tuple(r) for r in src.execute(
                                            "SELECT region_no, first_row, first_col, last_row, last_col, header_row,"
                                            " kind, cells, complete FROM _regions WHERE table_name=?"
                                            " ORDER BY region_no", (table_name,))])
                    if "_header_groups" in src_tables:
                        for xl_col, sql_name, path, method in src.execute(
                                "SELECT xl_col, sql_name, path, method FROM _header_groups WHERE table_name=?",
                                (table_name,)).fetchall():
                            if sql_name in col_ids:
                                con.execute("INSERT OR REPLACE INTO _header_groups VALUES (?,?,?,?,?)",
                                            (col_ids[sql_name], table_id, xl_col, path, method))
                    if "_formula_refs" in src_tables:
                        for sql_name, book, sheet, cells, sample in src.execute(
                                "SELECT sql_name, ref_workbook, ref_sheet, cells, sample FROM _formula_refs"
                                " WHERE table_name=?", (table_name,)).fetchall():
                            if sql_name in col_ids:
                                con.execute("INSERT INTO _formula_refs VALUES (?,?,?,?,?,?)",
                                            (col_ids[sql_name], table_id, book, sheet, cells, sample))
                if "_doc_blocks" in src_tables:
                    _ingest_document(con, src, sid, src_tables, sheet_to_table)
                if "_unsupported" in src_tables:
                    for scope, sheet_n, kind, count, detail in src.execute(
                        "SELECT scope,sheet_name,kind,count,detail FROM _unsupported").fetchall():
                        table_id = sheet_to_table.get(sheet_n) if scope == "sheet" else None
                        uid = hashlib.sha1(f"{sid}|{scope}|{sheet_n}|{kind}|{detail}".encode()).hexdigest()[:20]
                        con.execute("INSERT OR REPLACE INTO _unsupported VALUES (?,?,?,?,?,?,?,?)",
                                    (uid, sid, table_id, scope, sheet_n, kind, int(count or 0), detail))
        from .lineage import build_lineage
        build_lineage(con)
        con.commit()
        con.execute("PRAGMA optimize")
    finally:
        con.close()
    os.replace(partial, out)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(prog="xl2ai catalog", description="Build/rebuild catalog.db for a completed extraction run.")
    ap.add_argument("--config")
    ap.add_argument("--run", help="run id; default is current promoted run")
    args = ap.parse_args(argv)
    try:
        cfg = load_config(args.config)
        rid = args.run or current_run_id(cfg)
        if not rid:
            raise Xl2aiError("E_STAGE_INPUT", "no current run; run refresh first")
        path = build_catalog(cfg, rid)
    except Xl2aiError as e:
        print(str(e), file=sys.stderr)
        return 1
    print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())

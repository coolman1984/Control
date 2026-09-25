"""Unsupported-content detection (Power Query, Data Model, external links, stale calc, formulas): extract db ->
catalog -> context pack `blind_spots` -> `xl2ai brief` gaps. No Excel required; the extract db is built directly,
the same way test_platform_e2e.py stands in for a real extraction."""
import os
import sqlite3
import tempfile
import unittest

from xl2ai.brief import build_brief
from xl2ai.catalog import build_catalog
from xl2ai.contextpack import build_context_pack
from xl2ai.core.config import load_config
from xl2ai.core.runs import Run
from xl2ai.query import meta


def make_extract(path):
    con = sqlite3.connect(path)
    con.executescript("""
    CREATE TABLE _extraction_log (sheet_index INTEGER, sheet_name TEXT, table_name TEXT, visibility TEXT,
      status TEXT, data_rows INTEGER, columns INTEGER, header_row INTEGER);
    CREATE TABLE _columns (table_name TEXT, position INTEGER, sql_name TEXT, original_header TEXT, xl_col INTEGER,
      xl_col_letter TEXT, sql_type TEXT, kind TEXT, non_null INTEGER, error_cells INTEGER);
    CREATE TABLE _unsupported (scope TEXT, sheet_name TEXT, kind TEXT, count INTEGER, detail TEXT);
    CREATE TABLE _formulas (table_name TEXT, sql_name TEXT, has_formula INTEGER, sample_r1c1 TEXT);
    CREATE TABLE orders (_xl_row INTEGER, order_id INTEGER, total REAL);
    """)
    con.execute("INSERT INTO _extraction_log VALUES (1,'Orders','orders','visible','extracted',3,2,1)")
    for pos, name, typ, kind in [(1, "order_id", "INTEGER", "integer"), (2, "total", "REAL", "real")]:
        con.execute("INSERT INTO _columns VALUES ('orders',?,?,?,?,?,?,?,?,?)",
                    (pos, name, name, pos, chr(64 + pos), typ, kind, 3, 0))
    con.executemany("INSERT INTO orders VALUES (?,?,?)", [(2, 1, 100.0), (3, 2, 200.0), (4, 3, 300.0)])
    con.execute("INSERT INTO _unsupported VALUES ('workbook',NULL,'power_query',2,"
                "'Power Query step logic is not read')")
    con.execute("INSERT INTO _unsupported VALUES ('workbook',NULL,'stale_calculation',1,"
                "'pending recalculation at open')")
    con.execute("INSERT INTO _unsupported VALUES ('sheet','Orders','chart',1,'chart source not extracted')")
    con.execute("INSERT INTO _formulas VALUES ('orders','total',1,'=RC[-1]*2')")
    con.commit(); con.close()


class TestBlindSpots(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        cfgp = os.path.join(self.tmp.name, "xl2ai.toml")
        with open(cfgp, "w", encoding="utf-8") as f:
            f.write('[project]\nname="blind-spots"\n')
        self.cfg = load_config(cfgp)
        self.run = Run.create(self.cfg)
        sid = "book"
        src_path = os.path.join(self.tmp.name, "book.xlsx")
        with open(src_path, "w") as f:
            f.write("x")
        from xl2ai.core.fsutil import format_mtime
        st = os.stat(src_path)
        self.run.m["inputs"] = [{"source_id": sid, "path": src_path, "sha256": "c" * 64, "size": st.st_size,
                                 "mtime": format_mtime(st.st_mtime), "hash_mode": "full"}]
        db = self.run.path("extract", sid + ".db")
        os.makedirs(os.path.dirname(db), exist_ok=True)
        make_extract(db)
        with self.run.stage("extract") as st:
            st.artifact(db)
            st.detail("sources", [{"source_id": sid, "exit_code": 0, "db": self.run.rel(db),
                                   "sheets": {"extracted": 1, "skipped": 0, "error": 0},
                                   "verify_checks": 2, "verify_mismatches": 0}])
        self.catalog = build_catalog(self.cfg, self.run.id, self.run.m)
        self.run.finish()

    def tearDown(self):
        self.tmp.cleanup()

    def test_unsupported_content_reaches_catalog(self):
        con = sqlite3.connect(self.catalog)
        rows = con.execute("SELECT scope,kind,count,table_id FROM _unsupported ORDER BY scope,kind").fetchall()
        con.close()
        kinds = {r[1] for r in rows}
        self.assertEqual(kinds, {"power_query", "stale_calculation", "chart"})
        sheet_row = next(r for r in rows if r[1] == "chart")
        self.assertIsNotNone(sheet_row[3])                 # sheet-scoped finding is linked to its table_id
        workbook_row = next(r for r in rows if r[1] == "power_query")
        self.assertIsNone(workbook_row[3])                 # workbook-scoped finding has no single table

    def test_formula_column_reaches_catalog(self):
        con = sqlite3.connect(self.catalog)
        row = con.execute("SELECT has_formula,sample_r1c1 FROM _formulas").fetchone()
        con.close()
        self.assertEqual(row, (1, "=RC[-1]*2"))

    def test_context_pack_reports_blind_spots(self):
        jp, mp, payload = build_context_pack(self.cfg, self.run.id, self.catalog)
        self.assertEqual(len(payload["blind_spots"]), 3)
        kinds = {b["kind"] for b in payload["blind_spots"]}
        self.assertEqual(kinds, {"power_query", "stale_calculation", "chart"})
        with open(mp, encoding="utf-8") as f:
            md = f.read()
        self.assertIn("Blind spots", md)
        self.assertIn("power_query", md)

    def test_brief_surfaces_blind_spot_as_gap(self):
        out, code = build_brief(self.cfg, self.run.id)
        self.assertEqual(code, 1)                          # needs_review, not not_ready: data itself verified fine
        table = out["tables"][0]
        self.assertEqual(table["readiness"], "needs_review")
        self.assertEqual(table["blind_spots"], 1)           # only the sheet-scoped chart finding is on this table
        self.assertTrue(any(g["kind"] == "blind_spot" for g in out["gaps"]))

    def test_query_meta_unsupported(self):
        out = meta(self.cfg, "unsupported", run_id=self.run.id)
        self.assertTrue(out["ok"])
        kinds = {row[3] for row in out["rows"]}
        self.assertEqual(kinds, {"power_query", "stale_calculation", "chart"})


if __name__ == "__main__":
    unittest.main(verbosity=2)

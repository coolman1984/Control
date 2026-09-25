"""Catalog tests use tiny synthetic SQLite extraction outputs; Excel is not required."""
import os
import sqlite3
import tempfile
import unittest

from xl2ai.catalog import build_catalog
from xl2ai.core.config import load_config
from xl2ai.core.runs import Run


class TestCatalog(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        with open(os.path.join(root, "xl2ai.toml"), "w", encoding="utf-8") as f:
            f.write('[project]\nname="cat"\n')
        self.cfg = load_config(os.path.join(root, "xl2ai.toml"))
        self.run = Run.create(self.cfg)
        self.source_id = "book-a1"
        self.run.m["inputs"] = [{"source_id": self.source_id, "path": os.path.join(root, "book.xlsx"),
                                 "sha256": "a"*64, "size": 10, "mtime": "2026-01-01T00:00:00", "hash_mode": "full"}]
        db = self.run.path("extract", self.source_id + ".db")
        os.makedirs(os.path.dirname(db), exist_ok=True)
        con = sqlite3.connect(db)
        con.executescript("""
        CREATE TABLE _extraction_log (sheet_index INTEGER, sheet_name TEXT, table_name TEXT, visibility TEXT,
          status TEXT, data_rows INTEGER, columns INTEGER, header_row INTEGER);
        CREATE TABLE _columns (table_name TEXT, position INTEGER, sql_name TEXT, original_header TEXT, xl_col INTEGER,
          xl_col_letter TEXT, sql_type TEXT, kind TEXT, non_null INTEGER, error_cells INTEGER);
        CREATE TABLE data (_xl_row INTEGER, id INTEGER, amount REAL);
        """)
        con.execute("INSERT INTO _extraction_log VALUES (1,'Sheet1','data','visible','extracted',3,2,1)")
        con.executemany("INSERT INTO _columns VALUES (?,?,?,?,?,?,?,?,?,?)", [
            ("data",1,"id","ID",1,"A","INTEGER","integer",3,0),
            ("data",2,"amount","Amount",2,"B","REAL","real",3,0),
        ])
        con.executemany("INSERT INTO data VALUES (?,?,?)", [(2,1,10.0),(3,2,20.0),(4,3,30.0)])
        con.commit(); con.close()
        with self.run.stage("extract") as st:
            st.artifact(db)
            st.detail("sources", [{"source_id": self.source_id, "exit_code": 0,
                                   "db": self.run.rel(db), "sheets": {"extracted":1,"skipped":0,"error":0},
                                   "verify_checks": 3, "verify_mismatches": 0}])

    def tearDown(self):
        self.tmp.cleanup()

    def test_builds_stable_registry_without_copying_rows(self):
        path = build_catalog(self.cfg, self.run.id, self.run.m)
        con = sqlite3.connect(path)
        tables = con.execute("SELECT table_id,row_count,column_count,schema_fingerprint FROM _tables").fetchall()
        cols = con.execute("SELECT column_id,name FROM _columns ORDER BY position").fetchall()
        names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        con.close()
        self.assertEqual(len(tables), 1)
        self.assertEqual(tables[0][1:3], (3,2))
        self.assertEqual([x[1] for x in cols], ["id","amount"])
        self.assertNotIn("data", names, "catalog must not duplicate business rows")

    def test_ids_survive_schema_and_position_changes(self):
        p1 = build_catalog(self.cfg, self.run.id, self.run.m)
        c = sqlite3.connect(p1)
        try:
            table_before = c.execute("SELECT table_id FROM _tables").fetchone()[0]
            cols_before = dict(c.execute("SELECT name,column_id FROM _columns"))
        finally:
            c.close()

        db = self.run.path("extract", self.source_id + ".db")
        c = sqlite3.connect(db)
        c.execute("ALTER TABLE data ADD COLUMN new_first TEXT")
        c.execute("UPDATE _extraction_log SET columns=3 WHERE table_name='data'")
        c.execute("""INSERT INTO _columns VALUES (?,?,?,?,?,?,?,?,?,?)""",
                  ("data",3,"new_first","New First",3,"C","TEXT","text",0,0))
        c.commit()
        c.close()

        p2 = build_catalog(self.cfg, self.run.id, self.run.m)
        c = sqlite3.connect(p2)
        try:
            table_after = c.execute("SELECT table_id FROM _tables").fetchone()[0]
            cols_after = dict(c.execute("SELECT name,column_id FROM _columns"))
        finally:
            c.close()

        self.assertEqual(table_before, table_after)
        self.assertEqual(cols_before["id"], cols_after["id"])
        self.assertEqual(cols_before["amount"], cols_after["amount"])
        self.assertNotIn("new_first", cols_before)
        self.assertIn("new_first", cols_after)

    def test_table_id_is_repeatable_for_same_schema(self):
        p1 = build_catalog(self.cfg, self.run.id, self.run.m)
        c = sqlite3.connect(p1)
        try:
            a = c.execute("SELECT table_id FROM _tables").fetchone()[0]
        finally:
            c.close()
        p2 = build_catalog(self.cfg, self.run.id, self.run.m)
        c = sqlite3.connect(p2)
        try:
            b = c.execute("SELECT table_id FROM _tables").fetchone()[0]
        finally:
            c.close()
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main(verbosity=2)

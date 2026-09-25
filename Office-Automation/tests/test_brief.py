"""`xl2ai brief`: the cold-agent orientation call. No Excel required."""
import os
import sqlite3
import tempfile
import unittest

from xl2ai.analyze import analyze_catalog
from xl2ai.brief import build_brief
from xl2ai.catalog import build_catalog
from xl2ai.changes import detect_changes
from xl2ai.core.config import load_config
from xl2ai.core.runs import Run


def make_extract(path, rows):
    con = sqlite3.connect(path)
    con.executescript("""
    CREATE TABLE _extraction_log (sheet_index INTEGER, sheet_name TEXT, table_name TEXT, visibility TEXT,
      status TEXT, data_rows INTEGER, columns INTEGER, header_row INTEGER);
    CREATE TABLE _columns (table_name TEXT, position INTEGER, sql_name TEXT, original_header TEXT, xl_col INTEGER,
      xl_col_letter TEXT, sql_type TEXT, kind TEXT, non_null INTEGER, error_cells INTEGER);
    CREATE TABLE orders (_xl_row INTEGER, order_id INTEGER, amount REAL);
    """)
    con.execute("INSERT INTO _extraction_log VALUES (1,'Orders','orders','visible','extracted',?,2,1)", (len(rows),))
    for pos, name, typ, kind in [(1, "order_id", "INTEGER", "integer"), (2, "amount", "REAL", "real")]:
        con.execute("INSERT INTO _columns VALUES ('orders',?,?,?,?,?,?,?,?,?)",
                    (pos, name, name, pos, chr(64 + pos), typ, kind, len(rows), 0))
    con.executemany("INSERT INTO orders VALUES (?,?,?)", [(i + 2, r[0], r[1]) for i, r in enumerate(rows)])
    con.commit(); con.close()


class TestBrief(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        cfgp = os.path.join(self.tmp.name, "xl2ai.toml")
        with open(cfgp, "w", encoding="utf-8") as f:
            f.write('[project]\nname="brief-test"\n')
        self.cfg = load_config(cfgp)

    def tearDown(self):
        self.tmp.cleanup()

    def _promoted_run(self, rows, verify_mismatches=0):
        run = Run.create(self.cfg)
        sid = "book"
        run.m["inputs"] = [{"source_id": sid, "path": os.path.join(self.tmp.name, "book.xlsx"), "sha256": "a" * 64,
                            "size": 10, "mtime": "2026-01-01T00:00:00", "hash_mode": "full"}]
        with open(run.m["inputs"][0]["path"], "w") as f:
            f.write("x")
        st_result = os.stat(run.m["inputs"][0]["path"])
        from xl2ai.core.fsutil import format_mtime
        run.m["inputs"][0]["size"] = st_result.st_size
        run.m["inputs"][0]["mtime"] = format_mtime(st_result.st_mtime)
        db = run.path("extract", sid + ".db")
        os.makedirs(os.path.dirname(db), exist_ok=True)
        make_extract(db, rows)
        with run.stage("extract") as st:
            st.artifact(db)
            st.detail("sources", [{"source_id": sid, "exit_code": 0, "db": run.rel(db),
                                   "sheets": {"extracted": 1, "skipped": 0, "error": 0},
                                   "verify_checks": 3, "verify_mismatches": verify_mismatches}])
        cat = build_catalog(self.cfg, run.id, run.m)
        analyze_catalog(self.cfg, run.id, cat)
        detect_changes(self.cfg, run.id, previous_run_id=None, catalog_path=cat)
        run.finish()
        return run

    def test_no_run_yet_is_not_ready(self):
        out, code = build_brief(self.cfg)
        self.assertEqual(code, 2)
        self.assertEqual(out["gaps"][0]["kind"], "no_run")

    def test_healthy_run_is_ready(self):
        run = self._promoted_run([(1, 100.0), (2, 200.0)])
        out, code = build_brief(self.cfg, run.id)
        self.assertEqual(code, 0)
        self.assertEqual(out["tables"][0]["readiness"], "ready")
        self.assertEqual(out["gaps"], [])
        self.assertTrue(out["fresh"])

    def test_verification_mismatch_is_not_ready(self):
        run = self._promoted_run([(1, 100.0)], verify_mismatches=1)
        out, code = build_brief(self.cfg, run.id)
        self.assertEqual(code, 2)
        self.assertEqual(out["tables"][0]["readiness"], "not_ready")
        self.assertTrue(any(g["kind"] == "verification_mismatch" for g in out["gaps"]))

    def test_stale_source_is_flagged(self):
        run = self._promoted_run([(1, 100.0)])
        # touch the source after the run so status sees it as changed
        path = run.m["inputs"][0]["path"]
        with open(path, "a") as f:
            f.write("more data")
        out, code = build_brief(self.cfg, run.id)
        self.assertFalse(out["fresh"])
        self.assertTrue(any(g["kind"] == "stale" for g in out["gaps"]))
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""Phase 6 (agent interface): ai/agent_brief.md -- one plain-language file combining brief, semantics, changes
and definitions. No Excel required (synthetic extract db, as elsewhere)."""
import os
import sqlite3
import tempfile
import unittest

from xl2ai.agent_brief import build_agent_brief
from xl2ai.analyze import analyze_catalog
from xl2ai.catalog import build_catalog
from xl2ai.changes import detect_changes
from xl2ai.core.config import load_config
from xl2ai.core.fsutil import format_mtime
from xl2ai.core.runs import Run
from xl2ai.semantics import build_semantics


def make_extract(path, rows):
    con = sqlite3.connect(path)
    con.executescript("""
    CREATE TABLE _extraction_log (sheet_index INTEGER, sheet_name TEXT, table_name TEXT, visibility TEXT,
      status TEXT, data_rows INTEGER, columns INTEGER, header_row INTEGER);
    CREATE TABLE _columns (table_name TEXT, position INTEGER, sql_name TEXT, original_header TEXT, xl_col INTEGER,
      xl_col_letter TEXT, sql_type TEXT, kind TEXT, non_null INTEGER, error_cells INTEGER);
    CREATE TABLE orders (_xl_row INTEGER, order_id INTEGER, amount REAL);
    """)
    con.execute("INSERT INTO _extraction_log VALUES (1,'Orders','orders','visible','extracted',?,2,1)",
                (len(rows),))
    for pos, name, typ, kind in [(1, "order_id", "INTEGER", "integer"), (2, "amount", "REAL", "real")]:
        con.execute("INSERT INTO _columns VALUES ('orders',?,?,?,?,?,?,?,?,?)",
                    (pos, name, name, pos, chr(64 + pos), typ, kind, len(rows), 0))
    con.executemany("INSERT INTO orders VALUES (?,?,?)", [(i + 2, r[0], r[1]) for i, r in enumerate(rows)])
    con.commit(); con.close()


class TestAgentBrief(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        cfgp = os.path.join(self.tmp.name, "xl2ai.toml")
        with open(cfgp, "w", encoding="utf-8") as f:
            f.write('[project]\nname="agent-brief-test"\n')
        self.cfg = load_config(cfgp)

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, rows, previous_run_id=None):
        run = Run.create(self.cfg)
        sid = "book"
        src_path = os.path.join(self.tmp.name, "book.xlsx")
        with open(src_path, "w") as f:
            f.write("x")
        st = os.stat(src_path)
        run.m["inputs"] = [{"source_id": sid, "path": src_path, "sha256": "i" * 64, "size": st.st_size,
                            "mtime": format_mtime(st.st_mtime), "hash_mode": "full"}]
        db = run.path("extract", sid + ".db")
        os.makedirs(os.path.dirname(db), exist_ok=True)
        make_extract(db, rows)
        with run.stage("extract") as st_rec:
            st_rec.artifact(db)
            st_rec.detail("sources", [{"source_id": sid, "exit_code": 0, "db": run.rel(db),
                                       "sheets": {"extracted": 1, "skipped": 0, "error": 0},
                                       "verify_checks": 2, "verify_mismatches": 0}])
        cat = build_catalog(self.cfg, run.id, run.m)
        analyze_catalog(self.cfg, run.id, cat)
        build_semantics(self.cfg, run.id, cat)
        detect_changes(self.cfg, run.id, previous_run_id=previous_run_id, catalog_path=cat)
        run.finish()
        return run, cat

    def test_agent_brief_written_and_readable(self):
        run, cat = self._run([(1, 100.0), (2, 200.0)])
        path = build_agent_brief(self.cfg, run.id, cat)
        self.assertTrue(os.path.isfile(path))
        with open(path, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("orders", text)
        self.assertIn("order_id", text)
        self.assertIn("## Tables", text)
        self.assertIn("## Next commands", text)

    def test_agent_brief_shows_column_roles_by_name(self):
        run, cat = self._run([(1, 100.0), (2, 200.0)])
        path = build_agent_brief(self.cfg, run.id, cat)
        with open(path, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("identifier", text)
        self.assertIn("money", text)

    def test_agent_brief_explains_changes_between_runs(self):
        run1, cat1 = self._run([(1, 100.0), (2, 200.0)])
        run2, cat2 = self._run([(1, 100.0), (2, 250.0), (3, 300.0)], previous_run_id=run1.id)
        path = build_agent_brief(self.cfg, run2.id, cat2)
        with open(path, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("What changed since the previous trusted run", text)
        self.assertIn("volume", text)

    def test_first_run_states_no_baseline_plainly(self):
        run, cat = self._run([(1, 100.0)])
        path = build_agent_brief(self.cfg, run.id, cat)
        with open(path, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("first run", text.lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)

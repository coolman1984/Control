"""Phase 5 (opt-in reversible repairs): null-token normalization, category consolidation, text-as-number
coercion. Disabled by default -> byte-identical output to before this phase existed. Enabled -> every suggestion
lands only in _repairs, never in the data table itself. No Excel required (synthetic extract db, as elsewhere)."""
import os
import sqlite3
import tempfile
import unittest

from xl2ai.analyze import analyze_catalog
from xl2ai.catalog import build_catalog
from xl2ai.core.config import load_config
from xl2ai.core.runs import Run
from xl2ai.query import repaired
from xl2ai.repair import build_repairs


def make_extract(path, rows):
    con = sqlite3.connect(path)
    con.executescript("""
    CREATE TABLE _extraction_log (sheet_index INTEGER, sheet_name TEXT, table_name TEXT, visibility TEXT,
      status TEXT, data_rows INTEGER, columns INTEGER, header_row INTEGER);
    CREATE TABLE _columns (table_name TEXT, position INTEGER, sql_name TEXT, original_header TEXT, xl_col INTEGER,
      xl_col_letter TEXT, sql_type TEXT, kind TEXT, non_null INTEGER, error_cells INTEGER);
    CREATE TABLE customers (_xl_row INTEGER, city TEXT, quantity_text TEXT);
    """)
    con.execute("INSERT INTO _extraction_log VALUES (1,'Customers','customers','visible','extracted',?,2,1)",
                (len(rows),))
    for pos, name, typ, kind in [(1, "city", "TEXT", "text"), (2, "quantity_text", "TEXT", "text")]:
        con.execute("INSERT INTO _columns VALUES ('customers',?,?,?,?,?,?,?,?,?)",
                    (pos, name, name, pos, chr(64 + pos), typ, kind, len(rows), 0))
    con.executemany("INSERT INTO customers VALUES (?,?,?)", [(i + 2, *r) for i, r in enumerate(rows)])
    con.commit(); con.close()


class TestRepairSuggestions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfgp = os.path.join(self.tmp.name, "xl2ai.toml")

    def tearDown(self):
        self.tmp.cleanup()

    def _build(self, rows, repair_enabled=False):
        with open(self.cfgp, "w", encoding="utf-8") as f:
            f.write('[project]\nname="repair-test"\n')
            if repair_enabled:
                f.write('[repair]\nenabled=true\n')
        cfg = load_config(self.cfgp)
        run = Run.create(cfg)
        sid = "book"
        run.m["inputs"] = [{"source_id": sid, "path": "book.xlsx", "sha256": "h" * 64, "size": 1,
                            "mtime": "2026-01-01T00:00:00", "hash_mode": "full"}]
        db = run.path("extract", sid + ".db")
        os.makedirs(os.path.dirname(db), exist_ok=True)
        make_extract(db, rows)
        with run.stage("extract") as st:
            st.artifact(db)
            st.detail("sources", [{"source_id": sid, "exit_code": 0, "db": run.rel(db),
                                   "sheets": {"extracted": 1, "skipped": 0, "error": 0},
                                   "verify_checks": 2, "verify_mismatches": 0}])
        cat = build_catalog(cfg, run.id, run.m)
        analyze_catalog(cfg, run.id, cat)
        build_repairs(cfg, run.id, cat)
        return cfg, run, cat

    def test_disabled_by_default_produces_no_suggestions(self):
        cfg, run, cat = self._build([("Cairo", "100"), ("N/A", "200")], repair_enabled=False)
        con = sqlite3.connect(cat)
        n = con.execute("SELECT COUNT(*) FROM _repairs").fetchone()[0]
        con.close()
        self.assertEqual(n, 0)

    def test_null_token_suggested_when_enabled(self):
        cfg, run, cat = self._build([("Cairo", "100"), ("N/A", "200")], repair_enabled=True)
        con = sqlite3.connect(cat)
        row = con.execute(
            "SELECT original_value, repaired_value, rule FROM _repairs WHERE rule='null_token'").fetchone()
        con.close()
        self.assertEqual(row, ("N/A", None, "null_token"))

    def test_category_consolidation_picks_most_frequent_spelling(self):
        rows = [("Cairo", "1"), ("Cairo", "2"), ("cairo", "3"), (" CAIRO ", "4"), ("Cairo", "5")]
        cfg, run, cat = self._build(rows, repair_enabled=True)
        con = sqlite3.connect(cat)
        suggestions = con.execute(
            "SELECT original_value, repaired_value FROM _repairs WHERE rule='category_consolidation'").fetchall()
        con.close()
        self.assertEqual(len(suggestions), 2)                # "cairo" and " CAIRO " both map to "Cairo"
        for original, canon in suggestions:
            self.assertEqual(canon, "Cairo")
            self.assertIn(original, ("cairo", " CAIRO "))

    def test_text_as_number_suggested(self):
        rows = [("Cairo", "1,234"), ("Cairo", "not a number")]
        cfg, run, cat = self._build(rows, repair_enabled=True)
        con = sqlite3.connect(cat)
        rows_out = con.execute(
            "SELECT original_value, repaired_value FROM _repairs WHERE rule='text_as_number'").fetchall()
        con.close()
        self.assertEqual(rows_out, [("1,234", "1234")])

    def test_extracted_table_never_modified(self):
        rows = [("Cairo", "100"), ("N/A", "1,234"), ("cairo", "5")]
        cfg, run, cat = self._build(rows, repair_enabled=True)
        db = run.path("extract", "book.db")
        con = sqlite3.connect(db)
        stored = con.execute("SELECT city, quantity_text FROM customers ORDER BY _xl_row").fetchall()
        con.close()
        self.assertEqual(stored, rows)                       # byte-identical to what was written, repairs or not

    def test_query_repaired_applies_suggestions_without_touching_source(self):
        rows = [("Cairo", "100"), ("N/A", "200")]
        cfg, run, cat = self._build(rows, repair_enabled=True)
        out = repaired(cfg, "customers", run_id=run.id)
        applied_count = int(out["hint"].split()[0])
        self.assertEqual(applied_count, 1)                    # only the N/A -> NULL repair applies here

    def test_query_repaired_shows_null_token_as_none(self):
        rows = [("N/A", "100")]
        cfg, run, cat = self._build(rows, repair_enabled=True)
        out = repaired(cfg, "customers", run_id=run.id)
        city_idx = out["columns"].index("city")
        self.assertIsNone(out["rows"][0][city_idx])

    def test_query_repaired_is_a_noop_when_disabled(self):
        rows = [("N/A", "100")]
        cfg, run, cat = self._build(rows, repair_enabled=False)
        out = repaired(cfg, "customers", run_id=run.id)
        city_idx = out["columns"].index("city")
        self.assertEqual(out["rows"][0][city_idx], "N/A")     # no suggestions existed, so nothing was applied


if __name__ == "__main__":
    unittest.main(verbosity=2)

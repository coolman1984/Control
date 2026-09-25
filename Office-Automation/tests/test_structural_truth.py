"""Phase 3 (structural truth): header confidence scoring, totals-row detection, table-kind classification.
Header confidence is a pure function (no COM); the rest runs the same synthetic-extract-db pattern as
test_analysis_relations.py -- no Excel required."""
import json
import os
import sqlite3
import tempfile
import unittest

from xl2ai.analyze import analyze_catalog
from xl2ai.brief import build_brief
from xl2ai.catalog import build_catalog
from xl2ai.contextpack import build_context_pack
from xl2ai.core.config import load_config
from xl2ai.core.runs import Run
from xl2ai.extract.layout import header_confidence


class TestHeaderConfidence(unittest.TestCase):
    def test_no_header_scores_zero(self):
        score, reasons = header_confidence([[1, 2], [3, 4]], 2, None)
        self.assertEqual(score, 0.0)
        self.assertIn("no header row found", reasons[0])

    def test_clean_full_header_scores_high(self):
        block = [["Order ID", "Customer", "Amount"], [1, "A", 100], [2, "B", 200]]
        score, reasons = header_confidence(block, 3, 0)
        self.assertGreaterEqual(score, 0.9)
        self.assertTrue(any("3/3" in r for r in reasons))

    def test_sparse_header_scores_lower_than_full(self):
        full_score, _ = header_confidence([["A", "B", "C"], [1, 2, 3]], 3, 0)
        sparse_score, reasons = header_confidence([["A", None, None], [1, 2, 3]], 3, 0)
        self.assertLess(sparse_score, full_score)
        self.assertTrue(any("1/3" in r for r in reasons))

    def test_repeated_labels_reduce_score(self):
        block = [["Q1", "Q1", "Q2"], [1, 2, 3]]
        score, reasons = header_confidence(block, 3, 0)
        self.assertTrue(any("repeat" in r for r in reasons))


def make_extract(path, rows, header_conf=0.9, header_reasons=None):
    con = sqlite3.connect(path)
    con.executescript("""
    CREATE TABLE _extraction_log (sheet_index INTEGER, sheet_name TEXT, table_name TEXT, visibility TEXT,
      status TEXT, data_rows INTEGER, columns INTEGER, header_row INTEGER, formula_cells INTEGER DEFAULT 0,
      pivot_tables INTEGER DEFAULT 0, merged_in_data INTEGER DEFAULT 0,
      header_confidence REAL, header_reasons TEXT);
    CREATE TABLE _columns (table_name TEXT, position INTEGER, sql_name TEXT, original_header TEXT, xl_col INTEGER,
      xl_col_letter TEXT, sql_type TEXT, kind TEXT, non_null INTEGER, error_cells INTEGER);
    CREATE TABLE sales (_xl_row INTEGER, region TEXT, amount REAL);
    """)
    con.execute("INSERT INTO _extraction_log VALUES (1,'Sales','sales','visible','extracted',?,2,1,0,0,0,?,?)",
                (len(rows), header_conf, json.dumps(header_reasons or ["clean header"])))
    for pos, name, typ, kind in [(1, "region", "TEXT", "text"), (2, "amount", "REAL", "real")]:
        con.execute("INSERT INTO _columns VALUES ('sales',?,?,?,?,?,?,?,?,?)",
                    (pos, name, name, pos, chr(64 + pos), typ, kind, len(rows), 0))
    con.executemany("INSERT INTO sales VALUES (?,?,?)", [(i + 2, r[0], r[1]) for i, r in enumerate(rows)])
    con.commit(); con.close()


class TestTotalsAndKind(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        cfgp = os.path.join(self.tmp.name, "xl2ai.toml")
        with open(cfgp, "w", encoding="utf-8") as f:
            f.write('[project]\nname="structural"\n')
        self.cfg = load_config(cfgp)

    def tearDown(self):
        self.tmp.cleanup()

    def _catalog_for(self, rows, header_conf=0.9, promote=False):
        run = Run.create(self.cfg)
        sid = "book"
        src_path = os.path.join(self.tmp.name, "book.xlsx")
        with open(src_path, "w") as f:
            f.write("x")
        from xl2ai.core.fsutil import format_mtime
        st_result = os.stat(src_path)
        run.m["inputs"] = [{"source_id": sid, "path": src_path, "sha256": "d" * 64, "size": st_result.st_size,
                            "mtime": format_mtime(st_result.st_mtime), "hash_mode": "full"}]
        db = run.path("extract", sid + ".db")
        os.makedirs(os.path.dirname(db), exist_ok=True)
        make_extract(db, rows, header_conf=header_conf)
        with run.stage("extract") as st:
            st.artifact(db)
            st.detail("sources", [{"source_id": sid, "exit_code": 0, "db": run.rel(db),
                                   "sheets": {"extracted": 1, "skipped": 0, "error": 0},
                                   "verify_checks": 2, "verify_mismatches": 0}])
        cat = build_catalog(self.cfg, run.id, run.m)
        analyze_catalog(self.cfg, run.id, cat)
        if promote:
            run.finish()
        self.run = run
        return cat

    def test_header_confidence_carried_into_catalog(self):
        cat = self._catalog_for([("North", 100.0), ("South", 200.0)], header_conf=0.85)
        con = sqlite3.connect(cat)
        conf, reasons = con.execute("SELECT header_confidence, header_reasons FROM _tables").fetchone()
        con.close()
        self.assertAlmostEqual(conf, 0.85)
        self.assertIn("clean header", reasons)

    def test_totals_row_detected_by_label(self):
        cat = self._catalog_for([("North", 100.0), ("South", 200.0), ("Total", 300.0)])
        con = sqlite3.connect(cat)
        table_id = con.execute("SELECT table_id FROM _tables").fetchone()[0]
        flags = con.execute("SELECT xl_row, flag, detail FROM _row_flags WHERE table_id=?", (table_id,)).fetchall()
        finding = con.execute("SELECT count FROM _dq_findings WHERE code='DQ_TOTALS_ROW_IN_DATA'").fetchone()
        con.close()
        self.assertEqual(len(flags), 1)
        self.assertEqual(flags[0][1], "totals_candidate")
        self.assertEqual(flags[0][2], "Total")
        self.assertEqual(finding[0], 1)

    def test_no_totals_row_when_no_label_matches(self):
        cat = self._catalog_for([("North", 100.0), ("South", 200.0)])
        con = sqlite3.connect(cat)
        n = con.execute("SELECT COUNT(*) FROM _row_flags").fetchone()[0]
        finding = con.execute("SELECT COUNT(*) FROM _dq_findings WHERE code='DQ_TOTALS_ROW_IN_DATA'").fetchone()[0]
        con.close()
        self.assertEqual(n, 0)
        self.assertEqual(finding, 0)

    def test_table_kind_classified_as_data(self):
        cat = self._catalog_for([("North", 100.0), ("South", 200.0)])
        con = sqlite3.connect(cat)
        kind, conf, method = con.execute("SELECT kind, confidence, method FROM _table_kind").fetchone()
        con.close()
        self.assertEqual(kind, "data")
        self.assertEqual(method, "heuristic")
        self.assertGreater(conf, 0)

    def test_totals_row_downgrades_brief_readiness(self):
        self._catalog_for([("North", 100.0), ("South", 200.0), ("Total", 300.0)], promote=True)
        out, code = build_brief(self.cfg, self.run.id)
        self.assertEqual(code, 1)
        self.assertEqual(out["tables"][0]["readiness"], "needs_review")
        self.assertTrue(any(g["kind"] == "totals_row_in_data" for g in out["gaps"]))

    def test_context_pack_shows_table_kind(self):
        cat = self._catalog_for([("North", 100.0), ("South", 200.0)])
        _, _, payload = build_context_pack(self.cfg, self.run.id, cat)
        self.assertEqual(payload["tables"][0]["kind"], "data")


if __name__ == "__main__":
    unittest.main(verbosity=2)

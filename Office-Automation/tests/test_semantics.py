"""Phase 4 (semantic layer): column roles, units/currency, table grain, time coverage, auto-drafted
definitions, cross-file duplicate detection. Role/unit classification are pure functions (unit-tested directly);
the rest runs the synthetic-extract-db pattern used throughout this suite -- no Excel required."""
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
from xl2ai.core.fsutil import format_mtime
from xl2ai.core.runs import Run
from xl2ai.query import meta
from xl2ai.semantics import build_semantics, classify_column_role, detect_unit_currency


class TestColumnRoleClassification(unittest.TestCase):
    def test_date_column_is_high_confidence(self):
        role, conf, method, reasons = classify_column_role("Order Date", "TEXT", "date", 10, 0, 10, [])
        self.assertEqual(role, "date")
        self.assertGreaterEqual(conf, 0.9)

    def test_unique_id_column_is_identifier(self):
        role, conf, method, reasons = classify_column_role("order_id", "INTEGER", "integer", 100, 0, 100, [1, 2, 3])
        self.assertEqual(role, "identifier")
        self.assertGreater(conf, 0.8)

    def test_money_named_numeric_column(self):
        role, conf, method, reasons = classify_column_role("unit_price", "REAL", "real", 100, 0, 40,
                                                            [10.0, 20.0, 30.0])
        self.assertEqual(role, "money")

    def test_percentage_named_column(self):
        role, _, _, _ = classify_column_role("discount_rate", "REAL", "real", 100, 0, 20, [0.1, 0.2])
        self.assertEqual(role, "percentage")

    def test_low_cardinality_text_is_category(self):
        role, _, _, _ = classify_column_role("status", "TEXT", "text", 100, 0, 3, ["open", "closed", "pending"])
        self.assertEqual(role, "category")

    def test_high_cardinality_text_is_free_text(self):
        role, _, _, _ = classify_column_role("notes", "TEXT", "text", 100, 0, 95, ["a", "b", "c"])
        self.assertEqual(role, "free_text")

    def test_boolean_like_column(self):
        role, _, _, _ = classify_column_role("is_active", "TEXT", "text", 100, 0, 2, ["yes", "no"])
        self.assertEqual(role, "boolean")

    def test_contact_and_geo_by_name(self):
        role_c, _, _, _ = classify_column_role("email", "TEXT", "text", 100, 0, 90, ["a@b.com"])
        role_g, _, _, _ = classify_column_role("city", "TEXT", "text", 100, 0, 20, ["Cairo"])
        self.assertEqual(role_c, "contact")
        self.assertEqual(role_g, "geo")


class TestUnitCurrencyDetection(unittest.TestCase):
    def test_egp_detected(self):
        unit, currency = detect_unit_currency("Price (EGP)")
        self.assertEqual(currency, "EGP")
        self.assertEqual(unit, "currency")

    def test_percent_detected(self):
        unit, currency = detect_unit_currency("discount_pct")
        self.assertEqual(unit, "percent")
        self.assertIsNone(currency)

    def test_no_unit_found(self):
        unit, currency = detect_unit_currency("customer_name")
        self.assertIsNone(unit)
        self.assertIsNone(currency)


def make_extract(path, sheet_name, table_name, rows, cols):
    con = sqlite3.connect(path)
    con.executescript("""
    CREATE TABLE _extraction_log (sheet_index INTEGER, sheet_name TEXT, table_name TEXT, visibility TEXT,
      status TEXT, data_rows INTEGER, columns INTEGER, header_row INTEGER);
    CREATE TABLE _columns (table_name TEXT, position INTEGER, sql_name TEXT, original_header TEXT, xl_col INTEGER,
      xl_col_letter TEXT, sql_type TEXT, kind TEXT, non_null INTEGER, error_cells INTEGER);
    """)
    con.execute("INSERT INTO _extraction_log VALUES (1,?,?,'visible','extracted',?,?,1)",
                (sheet_name, table_name, len(rows), len(cols)))
    ddl = ", ".join(f'"{name}" {typ}' for name, typ, kind in cols)
    con.execute(f'CREATE TABLE "{table_name}" (_xl_row INTEGER, {ddl})')
    for pos, (name, typ, kind) in enumerate(cols, 1):
        nonnull = sum(r[pos - 1] is not None for r in rows)
        con.execute("INSERT INTO _columns VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (table_name, pos, name, name, pos, chr(64 + pos), typ, kind, nonnull, 0))
    marks = ",".join("?" * (len(cols) + 1))
    con.executemany(f'INSERT INTO "{table_name}" VALUES ({marks})', [(i + 2, *row) for i, row in enumerate(rows)])
    con.commit(); con.close()


class TestSemanticsPipeline(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        cfgp = os.path.join(self.tmp.name, "xl2ai.toml")
        with open(cfgp, "w", encoding="utf-8") as f:
            f.write('[project]\nname="semantics-test"\n')
        self.cfg = load_config(cfgp)

    def tearDown(self):
        self.tmp.cleanup()

    def _run_with_orders(self, promote=True):
        run = Run.create(self.cfg)
        sid = "book"
        src_path = os.path.join(self.tmp.name, "book.xlsx")
        with open(src_path, "w") as f:
            f.write("x")
        st = os.stat(src_path)
        run.m["inputs"] = [{"source_id": sid, "path": src_path, "sha256": "e" * 64, "size": st.st_size,
                            "mtime": format_mtime(st.st_mtime), "hash_mode": "full"}]
        db = run.path("extract", sid + ".db")
        os.makedirs(os.path.dirname(db), exist_ok=True)
        rows = [(i, "open" if i % 2 else "closed", 100.0 * i, "2026-01-0" + str(min(i, 9)))
                for i in range(1, 6)]
        make_extract(db, "Orders", "orders", rows,
                     [("order_id", "INTEGER", "integer"), ("status", "TEXT", "text"),
                      ("amount", "REAL", "real"), ("order_date", "TEXT", "date")])
        with run.stage("extract") as st_rec:
            st_rec.artifact(db)
            st_rec.detail("sources", [{"source_id": sid, "exit_code": 0, "db": run.rel(db),
                                       "sheets": {"extracted": 1, "skipped": 0, "error": 0},
                                       "verify_checks": 4, "verify_mismatches": 0}])
        cat = build_catalog(self.cfg, run.id, run.m)
        analyze_catalog(self.cfg, run.id, cat)
        build_semantics(self.cfg, run.id, cat)
        if promote:
            run.finish()
        self.run, self.catalog = run, cat
        return cat

    def test_identifier_and_money_roles_reach_catalog(self):
        cat = self._run_with_orders()
        con = sqlite3.connect(cat)
        roles = {r[0]: r[1] for r in con.execute(
            """SELECT c.name, cr.role FROM _column_roles cr JOIN _columns c ON c.column_id=cr.column_id""")}
        con.close()
        self.assertEqual(roles["order_id"], "identifier")
        self.assertEqual(roles["amount"], "money")
        self.assertEqual(roles["status"], "category")

    def test_grain_detected_from_unique_id_column(self):
        cat = self._run_with_orders()
        con = sqlite3.connect(cat)
        status, description = con.execute("SELECT status, description FROM _table_grain").fetchone()
        con.close()
        self.assertEqual(status, "inferred")
        self.assertIn("order_id", description)

    def test_grain_unknown_when_no_strong_key(self):
        run = Run.create(self.cfg)
        sid = "book2"
        db = run.path("extract", sid + ".db")
        os.makedirs(os.path.dirname(db), exist_ok=True)
        # every value repeats: no column comes close to uniquely identifying a row
        rows = [("A", 1), ("A", 1), ("A", 1), ("B", 2), ("B", 2)]
        make_extract(db, "Log", "log", rows, [("category", "TEXT", "text"), ("code", "INTEGER", "integer")])
        run.m["inputs"] = [{"source_id": sid, "path": "x.xlsx", "sha256": "f" * 64, "size": 1,
                            "mtime": "2026-01-01T00:00:00", "hash_mode": "full"}]
        with run.stage("extract") as st:
            st.artifact(db)
            st.detail("sources", [{"source_id": sid, "exit_code": 0, "db": run.rel(db),
                                   "sheets": {"extracted": 1, "skipped": 0, "error": 0},
                                   "verify_checks": 2, "verify_mismatches": 0}])
        cat = build_catalog(self.cfg, run.id, run.m)
        analyze_catalog(self.cfg, run.id, cat)
        build_semantics(self.cfg, run.id, cat)
        con = sqlite3.connect(cat)
        status = con.execute("SELECT status FROM _table_grain").fetchone()[0]
        con.close()
        self.assertEqual(status, "unknown")

    def test_time_coverage_captured(self):
        cat = self._run_with_orders()
        con = sqlite3.connect(cat)
        row = con.execute("SELECT min_value, max_value FROM _time_coverage").fetchone()
        con.close()
        self.assertIsNotNone(row)
        self.assertLessEqual(row[0], row[1])

    def test_auto_definitions_drafted(self):
        cat = self._run_with_orders()
        con = sqlite3.connect(cat)
        rows = con.execute("SELECT term, meaning FROM _dictionary WHERE origin='auto'").fetchall()
        con.close()
        terms = {r[0] for r in rows}
        self.assertIn("orders", terms)                    # table-level draft
        table_def = next(m for t, m in rows if t == "orders")
        self.assertIn("row(s)", table_def)

    def test_query_meta_exposes_new_sections(self):
        self._run_with_orders(promote=True)
        roles = meta(self.cfg, "column_roles", run_id=self.run.id)
        grain = meta(self.cfg, "grain", run_id=self.run.id)
        coverage = meta(self.cfg, "time_coverage", run_id=self.run.id)
        self.assertTrue(roles["ok"] and grain["ok"] and coverage["ok"])
        self.assertGreater(roles["row_count"], 0)

    def test_context_pack_shows_grain(self):
        cat = self._run_with_orders()
        _, _, payload = build_context_pack(self.cfg, self.run.id, cat)
        self.assertIn("order_id", payload["tables"][0]["grain"])

    def test_brief_flags_unknown_grain_without_forcing_not_ready(self):
        run = Run.create(self.cfg)
        sid = "book3"
        src_path = os.path.join(self.tmp.name, "book3.xlsx")
        with open(src_path, "w") as f:
            f.write("x")
        st = os.stat(src_path)
        run.m["inputs"] = [{"source_id": sid, "path": src_path, "sha256": "g" * 64, "size": st.st_size,
                            "mtime": format_mtime(st.st_mtime), "hash_mode": "full"}]
        db = run.path("extract", sid + ".db")
        os.makedirs(os.path.dirname(db), exist_ok=True)
        rows = [("A", 1), ("A", 1), ("B", 2)]
        make_extract(db, "Log", "log", rows, [("category", "TEXT", "text"), ("code", "INTEGER", "integer")])
        with run.stage("extract") as st_rec:
            st_rec.artifact(db)
            st_rec.detail("sources", [{"source_id": sid, "exit_code": 0, "db": run.rel(db),
                                       "sheets": {"extracted": 1, "skipped": 0, "error": 0},
                                       "verify_checks": 2, "verify_mismatches": 0}])
        cat = build_catalog(self.cfg, run.id, run.m)
        analyze_catalog(self.cfg, run.id, cat)
        build_semantics(self.cfg, run.id, cat)
        run.finish()
        out, code = build_brief(self.cfg, run.id)
        self.assertEqual(code, 0)                          # grain uncertainty alone does not block readiness
        self.assertTrue(any(g["kind"] == "grain_unknown" for g in out["gaps"]))


class TestDuplicateDetection(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        cfgp = os.path.join(self.tmp.name, "xl2ai.toml")
        with open(cfgp, "w", encoding="utf-8") as f:
            f.write('[project]\nname="dup-test"\n')
        self.cfg = load_config(cfgp)

    def tearDown(self):
        self.tmp.cleanup()

    def test_identical_schema_and_rows_across_sources_flagged(self):
        run = Run.create(self.cfg)
        cols = [("customer_id", "INTEGER", "integer"), ("name", "TEXT", "text")]
        rows = [(1, "A"), (2, "B"), (3, "C")]
        inputs = []
        for sid in ("jan", "feb"):
            db = run.path("extract", sid + ".db")
            os.makedirs(os.path.dirname(db), exist_ok=True)
            make_extract(db, "Customers", "customers", rows, cols)
            inputs.append({"source_id": sid, "path": f"{sid}.xlsx", "sha256": sid * 8, "size": 1,
                           "mtime": "2026-01-01T00:00:00", "hash_mode": "full"})
        run.m["inputs"] = inputs
        with run.stage("extract") as st:
            for sid in ("jan", "feb"):
                st.artifact(run.path("extract", sid + ".db"))
            st.detail("sources", [
                {"source_id": sid, "exit_code": 0, "db": run.rel(run.path("extract", sid + ".db")),
                 "sheets": {"extracted": 1, "skipped": 0, "error": 0}, "verify_checks": 2, "verify_mismatches": 0}
                for sid in ("jan", "feb")])
        cat = build_catalog(self.cfg, run.id, run.m)
        analyze_catalog(self.cfg, run.id, cat)
        build_semantics(self.cfg, run.id, cat)
        con = sqlite3.connect(cat)
        dupes = con.execute("SELECT table_id_a, table_id_b, score FROM _duplicate_candidates").fetchall()
        con.close()
        self.assertEqual(len(dupes), 1)
        self.assertGreater(dupes[0][2], 0.9)               # identical rows -> full row-hash overlap


if __name__ == "__main__":
    unittest.main(verbosity=2)

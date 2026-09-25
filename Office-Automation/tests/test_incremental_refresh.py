"""Incremental-refresh tests. No Excel or COM required."""
import os
import tempfile
import unittest

from xl2ai.core.config import load_config
from xl2ai.core.runs import Run
from xl2ai.refresh import find_reusable_extraction, materialize_reuse
from xl2ai.sources.inventory import build_inventory


class TestIncrementalRefresh(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.source_path = os.path.join(self.root, "source.xlsx")
        with open(self.source_path, "wb") as f:
            f.write(b"PK\x03\x04 stable workbook bytes")
        self.config_path = os.path.join(self.root, "xl2ai.toml")
        self._write_config()
        self.cfg = load_config(self.config_path)
        self.source = build_inventory(self.cfg)[0][0]

    def tearDown(self):
        self.tmp.cleanup()

    def _write_config(self, block_cells=500000, keep_runs=3):
        with open(self.config_path, "w", encoding="utf-8") as f:
            f.write(
                '[project]\nname = "incremental-test"\n'
                f'[refresh]\nkeep_runs = {keep_runs}\n'
                f'[extract]\nblock_cells = {block_cells}\n'
                '[[sources]]\npath = "source.xlsx"\n'
            )

    def _promote_prior_extraction(self):
        run = Run.create(self.cfg)
        run.m["inputs"] = [{k: self.source[k] for k in
                            ("source_id", "path", "sha256", "size", "mtime", "hash_mode")}]
        run.save()
        db = run.path("extract", self.source["source_id"] + ".db")
        os.makedirs(os.path.dirname(db), exist_ok=True)
        with open(db, "wb") as f:
            f.write(b"trusted database bytes")
        with run.stage("extract") as st:
            st.artifact(db)
            st.detail("sources", [{
                "source_id": self.source["source_id"], "exit_code": 0, "db": run.rel(db), "message": "",
                "sheets": {"extracted": 2, "skipped": 0, "error": 0},
                "verify_checks": 12, "verify_mismatches": 0,
            }])
        self.assertTrue(run.finish())
        return run, db

    def test_exactly_unchanged_source_is_reusable(self):
        run, db = self._promote_prior_extraction()
        hit = find_reusable_extraction(self.cfg, self.source)
        self.assertIsNotNone(hit)
        self.assertEqual(hit["run_id"], run.id)
        self.assertEqual(os.path.abspath(hit["db"]), os.path.abspath(db))

    def test_changed_source_is_never_reused(self):
        self._promote_prior_extraction()
        with open(self.source_path, "ab") as f:
            f.write(b" changed")
        changed = build_inventory(self.cfg)[0][0]
        self.assertIsNone(find_reusable_extraction(self.cfg, changed))

    def test_extraction_setting_change_invalidates_reuse_but_retention_change_does_not(self):
        self._promote_prior_extraction()

        self._write_config(block_cells=500000, keep_runs=9)
        retention_only = load_config(self.config_path)
        self.assertIsNotNone(find_reusable_extraction(retention_only, build_inventory(retention_only)[0][0]))

        self._write_config(block_cells=250000, keep_runs=9)
        extraction_changed = load_config(self.config_path)
        self.assertIsNone(find_reusable_extraction(extraction_changed, build_inventory(extraction_changed)[0][0]))

    def test_reused_database_is_materialized_inside_new_run(self):
        _, src = self._promote_prior_extraction()
        dst = os.path.join(self.root, "new-run", "extract", "copy.db")
        mode = materialize_reuse(src, dst)
        self.assertIn(mode, {"hardlink", "copy"})
        with open(dst, "rb") as f:
            self.assertEqual(f.read(), b"trusted database bytes")


if __name__ == "__main__":
    unittest.main(verbosity=2)

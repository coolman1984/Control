"""Unit tests for xl2ai.sources.inventory. No Excel needed (files are never opened, only fingerprinted)."""
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from xl2ai.core.config import load_config
from xl2ai.core.errors import Xl2aiError
from xl2ai.sources.inventory import build_inventory, kind_of, source_id


def touch(path, content=b""):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "wb") as f:
        f.write(content)
    return path


def make_cfg(tmp, sources_toml):
    p = os.path.join(tmp, "xl2ai.toml")
    with open(p, "w", encoding="utf-8") as f:
        f.write(sources_toml)
    return load_config(p)


class TestSourceId(unittest.TestCase):
    def test_stable_for_the_same_path(self):
        self.assertEqual(source_id("C:/a/report.xlsx"), source_id("C:/a/report.xlsx"))

    def test_same_stem_different_folder_does_not_collide(self):
        """Phase-1 proof gate: two files named the same in different folders must not share an id."""
        id1, id2 = source_id("C:/dir1/report.xlsx"), source_id("C:/dir2/report.xlsx")
        self.assertNotEqual(id1, id2)
        self.assertTrue(id1.startswith("report-") and id2.startswith("report-"))

    def test_alias_overrides_the_computed_id(self):
        self.assertEqual(source_id("C:/dir1/report.xlsx", alias="Sales EU"), "sales-eu")

    def test_kind_of(self):
        self.assertEqual(kind_of("a.XLSX"), "xlsx")
        self.assertEqual(kind_of("a.csv"), "csv")                 # documents and text are sources too now
        self.assertEqual(kind_of("a.zip"), "other")


class TestBuildInventory(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_same_stem_in_two_folders_both_extracted_without_collision(self):
        d1, d2 = os.path.join(self.tmp.name, "d1"), os.path.join(self.tmp.name, "d2")
        touch(os.path.join(d1, "report.xlsx"), b"aaa")
        touch(os.path.join(d2, "report.xlsx"), b"bbb")
        cfg = make_cfg(self.tmp.name, f'[[sources]]\npath = "d1"\n[[sources]]\npath = "d2"\n')
        sources, warnings = build_inventory(cfg)
        self.assertEqual(len(sources), 2)
        ids = {s["source_id"] for s in sources}
        self.assertEqual(len(ids), 2)
        self.assertEqual(warnings, [])

    def test_missing_source_raises_with_hint(self):
        cfg = make_cfg(self.tmp.name, '[[sources]]\npath = "does_not_exist.xlsx"\n')
        with self.assertRaises(Xl2aiError) as ctx:
            build_inventory(cfg)
        self.assertEqual(ctx.exception.code, "E_SRC_MISSING")

    def test_no_sources_configured_raises(self):
        cfg = make_cfg(self.tmp.name, "")
        with self.assertRaises(Xl2aiError):
            build_inventory(cfg)

    def test_identical_content_across_sources_warns(self):
        d = os.path.join(self.tmp.name, "d")
        touch(os.path.join(d, "a.xlsx"), b"same bytes")
        touch(os.path.join(d, "b.xlsx"), b"same bytes")
        cfg = make_cfg(self.tmp.name, '[[sources]]\npath = "d"\n')
        sources, warnings = build_inventory(cfg)
        self.assertEqual(len(sources), 2)
        self.assertTrue(any("identical content" in w for w in warnings))

    def test_cli_override_replaces_configured_sources(self):
        d1, d2 = os.path.join(self.tmp.name, "d1"), os.path.join(self.tmp.name, "d2")
        touch(os.path.join(d1, "a.xlsx"), b"x")
        touch(os.path.join(d2, "b.xlsx"), b"y")
        cfg = make_cfg(self.tmp.name, '[[sources]]\npath = "d1"\n')
        cfg2 = cfg.with_sources([os.path.join(d2, "b.xlsx")])
        sources, _ = build_inventory(cfg2)
        self.assertEqual([os.path.basename(s["path"]) for s in sources], ["b.xlsx"])

    def test_alias_requiring_a_unique_match_rejects_ambiguity(self):
        d = os.path.join(self.tmp.name, "d")
        touch(os.path.join(d, "a.xlsx"), b"x")
        touch(os.path.join(d, "b.xlsx"), b"y")
        cfg = make_cfg(self.tmp.name, f'[[sources]]\npath = "d"\nalias = "both"\n')
        with self.assertRaises(Xl2aiError):
            build_inventory(cfg)

    def test_wrapper_prefix_from_config_is_detected(self):
        d = os.path.join(self.tmp.name, "d")
        touch(os.path.join(d, "a.xlsx"), b"HEADERBYTES_marker_rest_of_file")
        cfg = make_cfg(self.tmp.name, '[[sources]]\npath = "d"\n[environment]\nwrapper_prefixes = ["HEADERBYTES"]\n')
        sources, _ = build_inventory(cfg)
        self.assertEqual(sources[0]["wrapper"], "drm")

    def test_ordinary_file_has_no_wrapper(self):
        d = os.path.join(self.tmp.name, "d")
        touch(os.path.join(d, "a.xlsx"), b"PK\x03\x04 fake zip content, like a real .xlsx")
        cfg = make_cfg(self.tmp.name, '[[sources]]\npath = "d"\n')
        sources, _ = build_inventory(cfg)
        self.assertEqual(sources[0]["wrapper"], "none")

    def test_unrecognised_content_is_reported_as_other_not_none(self):
        """A real .xlsx is always a zip (starts with PK); anything else is worth flagging, not hiding as 'none'."""
        d = os.path.join(self.tmp.name, "d")
        touch(os.path.join(d, "a.xlsx"), b"plain content, not a zip")
        cfg = make_cfg(self.tmp.name, '[[sources]]\npath = "d"\n')
        sources, _ = build_inventory(cfg)
        self.assertEqual(sources[0]["wrapper"], "other")

    def test_hash_and_size_recorded(self):
        d = os.path.join(self.tmp.name, "d")
        touch(os.path.join(d, "a.xlsx"), b"12345")
        cfg = make_cfg(self.tmp.name, '[[sources]]\npath = "d"\n')
        sources, _ = build_inventory(cfg)
        self.assertEqual(sources[0]["size"], 5)
        self.assertEqual(len(sources[0]["sha256"]), 64)
        self.assertEqual(sources[0]["hash_mode"], "full")


if __name__ == "__main__":
    unittest.main(verbosity=2)

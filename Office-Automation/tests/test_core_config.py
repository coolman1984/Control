"""Unit tests for xl2ai.core.config. No Excel needed."""
import os
import sys
import tempfile
import textwrap
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from xl2ai.core import config as cfgmod
from xl2ai.core.errors import Xl2aiError


def write(path, text):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(textwrap.dedent(text))


class TestConfig(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def toml(self, text):
        p = os.path.join(self.dir, "xl2ai.toml")
        write(p, text)
        return p

    def test_defaults_with_minimal_file(self):
        p = self.toml("""
            [[sources]]
            path = "a.xlsx"
        """)
        cfg = cfgmod.load_config(p)
        self.assertEqual(cfg.keep_runs, 3)
        self.assertFalse(cfg.allow_partial)
        self.assertEqual(cfg.extract["block_cells"], 500_000)
        self.assertEqual(len(cfg.sources), 1)
        self.assertEqual(cfg.sources[0].path, "a.xlsx")

    def test_unknown_section_suggests_close_match(self):
        p = self.toml("[refresch]\nkeep_runs = 5\n")
        with self.assertRaises(Xl2aiError) as ctx:
            cfgmod.load_config(p)
        self.assertEqual(ctx.exception.code, "E_CONFIG")
        self.assertIn("refresh", ctx.exception.hint)

    def test_unknown_key_suggests_close_match(self):
        p = self.toml("[refresh]\nkeep_run = 5\n")
        with self.assertRaises(Xl2aiError) as ctx:
            cfgmod.load_config(p)
        self.assertIn("keep_runs", ctx.exception.hint)

    def test_wrong_type_rejected(self):
        p = self.toml('[refresh]\nkeep_runs = "five"\n')
        with self.assertRaises(Xl2aiError):
            cfgmod.load_config(p)

    def test_bool_is_not_accepted_as_int(self):
        p = self.toml("[refresh]\nkeep_runs = true\n")
        with self.assertRaises(Xl2aiError):
            cfgmod.load_config(p)

    def test_minimum_enforced(self):
        p = self.toml("[refresh]\nkeep_runs = 0\n")
        with self.assertRaises(Xl2aiError):
            cfgmod.load_config(p)

    def test_string_list_type_checked(self):
        p = self.toml("[environment]\nwrapper_prefixes = [1, 2]\n")
        with self.assertRaises(Xl2aiError):
            cfgmod.load_config(p)

    def test_source_missing_path_key(self):
        p = self.toml("[[sources]]\nalias = \"x\"\n")
        with self.assertRaises(Xl2aiError):
            cfgmod.load_config(p)

    def test_source_unknown_key(self):
        p = self.toml('[[sources]]\npath = "a.xlsx"\nalais = "x"\n')
        with self.assertRaises(Xl2aiError) as ctx:
            cfgmod.load_config(p)
        self.assertIn("alias", ctx.exception.hint)

    def test_duplicate_alias_rejected(self):
        p = self.toml("""
            [[sources]]
            path = "a.xlsx"
            alias = "same"
            [[sources]]
            path = "b.xlsx"
            alias = "same"
        """)
        with self.assertRaises(Xl2aiError):
            cfgmod.load_config(p)

    def test_invalid_toml_syntax(self):
        p = self.toml("this is not toml [[[")
        with self.assertRaises(Xl2aiError):
            cfgmod.load_config(p)

    def test_no_config_found_required_raises(self):
        empty = tempfile.TemporaryDirectory()
        try:
            with self.assertRaises(Xl2aiError):
                cfgmod.load_config(None, required=True, start=empty.name)
        finally:
            empty.cleanup()

    def test_no_config_not_required_gives_defaults(self):
        empty = tempfile.TemporaryDirectory()
        try:
            cfg = cfgmod.load_config(None, required=False, start=empty.name)
            self.assertEqual(cfg.sources, [])
            self.assertEqual(cfg.keep_runs, 3)
        finally:
            empty.cleanup()

    def test_find_config_discovers_upward(self):
        p = self.toml("[[sources]]\npath = \"a.xlsx\"\n")
        sub = os.path.join(self.dir, "a", "b", "c")
        os.makedirs(sub)
        found = cfgmod.find_config(sub)
        self.assertEqual(os.path.abspath(found), os.path.abspath(p))

    def test_relative_paths_resolve_against_config_dir(self):
        p = self.toml("[[sources]]\npath = \"sub/a.xlsx\"\n")
        cfg = cfgmod.load_config(p)
        self.assertEqual(cfg.resolve("sub/a.xlsx"), os.path.join(os.path.dirname(p), "sub", "a.xlsx"))

    def test_data_dir_defaults_relative_to_config(self):
        p = self.toml("[[sources]]\npath = \"a.xlsx\"\n")
        cfg = cfgmod.load_config(p)
        self.assertEqual(cfg.data_dir, os.path.join(os.path.dirname(p), "data"))

    def test_with_sources_overrides_cli(self):
        p = self.toml("[[sources]]\npath = \"a.xlsx\"\n")
        cfg = cfgmod.load_config(p)
        cfg2 = cfg.with_sources(["x.xlsx", "y.xlsx"])
        self.assertEqual([s.path for s in cfg2.sources], ["x.xlsx", "y.xlsx"])
        self.assertEqual(cfg.sources[0].path, "a.xlsx")           # original untouched

    def test_fingerprint_stable_and_sensitive(self):
        p1 = self.toml("[[sources]]\npath = \"a.xlsx\"\n")
        cfg1 = cfgmod.load_config(p1)
        cfg1b = cfgmod.load_config(p1)
        self.assertEqual(cfg1.fingerprint(), cfg1b.fingerprint())
        p2 = self.toml("[[sources]]\npath = \"a.xlsx\"\nalias = \"a\"\n")
        cfg2 = cfgmod.load_config(p2)
        self.assertNotEqual(cfg1.fingerprint(), cfg2.fingerprint())

    def test_extract_options_maps_config(self):
        p = self.toml("""
            [extract]
            strict = true
            sheet = ["Sheet1"]
        """)
        # `sheet` is not a real key (the real one is `sheets` as a list) -> must fail loudly, not default silently
        with self.assertRaises(Xl2aiError):
            cfgmod.load_config(p)

    def test_extract_sheets_list_is_lowercased_and_set(self):
        p = self.toml("""
            [[sources]]
            path = "a.xlsx"
            [extract]
            sheets = ["Sheet1", "SHEET2"]
        """)
        cfg = cfgmod.load_config(p)
        opts = cfg.extract_options()
        self.assertEqual(opts.sheets, {"sheet1", "sheet2"})


if __name__ == "__main__":
    unittest.main(verbosity=2)

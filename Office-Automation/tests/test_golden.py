"""Golden regression: extraction output must stay byte-identical (see regression_dump.py for what is compared).

Goldens were captured from the pre-package monolith. If a change is *meant* to alter output, regenerate with
`python tests/regen_golden.py`, list the change in CHANGELOG.md and commit both together.
The specimen files are a real-world stress test only; their tests are skipped when the files are absent.
"""
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
from xl2ai.extract.pipeline import main  # noqa: E402

# fixture_cache -> make_fixtures needs real Excel COM (pythoncom/pywin32) to build synthetic workbooks; importing
# it on a machine without them must not abort test discovery for the whole `tests/` package, only skip this module.
try:
    import fixture_cache  # noqa: E402
    import regression_dump  # noqa: E402
    _FIXTURES_ERROR = None
except ImportError as e:
    fixture_cache = regression_dump = None
    _FIXTURES_ERROR = str(e)

GOLDEN = os.path.join(HERE, "golden", "fingerprints.json")
SPECIMEN_DIR = os.path.join(ROOT, "Price Comparison Data")


def extract(src, out_dir):
    code = main([src, "-o", out_dir])
    db = os.path.join(out_dir, os.path.splitext(os.path.basename(src))[0].strip() + ".db")
    return code, db


@unittest.skipIf(_FIXTURES_ERROR, f"synthetic-fixture tests need Windows + Excel (pywin32): {_FIXTURES_ERROR}")
class TestGolden(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(GOLDEN, encoding="utf-8") as f:
            cls.golden = json.load(f)
        cls.out = tempfile.TemporaryDirectory()

    @classmethod
    def tearDownClass(cls):
        cls.out.cleanup()

    def check(self, key, src):
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()):
            code, db = extract(src, self.out.name)
        self.assertEqual(code, 0, key)
        got = regression_dump.fingerprint(db)
        want = self.golden[key]
        if got["total"] != want["total"]:
            diff = [t for t in sorted(set(got["tables"]) | set(want["tables"]))
                    if got["tables"].get(t) != want["tables"].get(t)]
            self.fail(f"{key}: output changed in tables {diff}. If intended: tests/regen_golden.py + CHANGELOG.md")

    def test_synthetic_edge_cases(self):
        self.check("synthetic/edge_cases", fixture_cache.get()["edge"])

    def test_synthetic_1904(self):
        self.check("synthetic/edge_1904", fixture_cache.get()["d1904"])

    def _specimen(self, name):
        src = os.path.join(SPECIMEN_DIR, name)
        if not os.path.exists(src):
            self.skipTest("specimen file not present")
        self.check("specimen/" + os.path.splitext(name)[0].strip(), src)

    def test_specimen_small_xlsx(self):
        self._specimen("new  Guide.xlsx")

    def test_specimen_large_xlsb(self):
        self._specimen("VD Price Comparison T09 VS S09 & Q4 T09 Vs T0A .xlsb")


if __name__ == "__main__":
    unittest.main(verbosity=2)

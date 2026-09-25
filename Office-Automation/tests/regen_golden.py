"""Regenerate tests/golden/fingerprints.json from the CURRENT code. Only run this for an intended output change."""
import contextlib
import io
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
import fixture_cache  # noqa: E402
import regression_dump  # noqa: E402
import test_golden  # noqa: E402


def main():
    paths = fixture_cache.get()
    jobs = {"synthetic/edge_cases": paths["edge"], "synthetic/edge_1904": paths["d1904"]}
    for name in ("new  Guide.xlsx", "VD Price Comparison T09 VS S09 & Q4 T09 Vs T0A .xlsb"):
        src = os.path.join(test_golden.SPECIMEN_DIR, name)
        if os.path.exists(src):
            jobs["specimen/" + os.path.splitext(name)[0].strip()] = src
    out = {}
    with tempfile.TemporaryDirectory() as tmp:
        for key, src in jobs.items():
            with contextlib.redirect_stdout(io.StringIO()):
                code, db = test_golden.extract(src, tmp)
            assert code == 0, key
            out[key] = regression_dump.fingerprint(db)
            print(key, out[key]["total"][:16])
    os.makedirs(os.path.dirname(test_golden.GOLDEN), exist_ok=True)
    with open(test_golden.GOLDEN, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1, sort_keys=True)


if __name__ == "__main__":
    main()

"""Structural guarantees of the platform (no Excel needed)."""
import builtins
import importlib
import os
import pkgutil
import re
import subprocess
import symtable
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import xl2ai  # noqa: E402


def platform_modules():
    for info in pkgutil.walk_packages(xl2ai.__path__, "xl2ai."):
        yield info.name


def global_refs(table):
    for sym in table.get_symbols():
        # Python 3.12 inlines comprehensions into the containing scope; their
        # iteration variables are assigned there but are not module globals.
        if sym.is_referenced() and sym.is_global() and not sym.is_assigned():
            yield sym.get_name()
    for child in table.get_children():
        yield from global_refs(child)


class TestPlatformStructure(unittest.TestCase):
    def test_every_global_name_resolves(self):
        """Catches a name that a module split or edit left undefined, in code paths no test happens to run."""
        for name in platform_modules():
            mod = importlib.import_module(name)
            with open(mod.__file__, encoding="utf-8") as f:
                table = symtable.symtable(f.read(), mod.__file__, "exec")
            missing = sorted({n for n in global_refs(table) if not hasattr(mod, n) and not hasattr(builtins, n)})
            self.assertEqual(missing, [], f"{name} references undefined names")

    def test_platform_contains_no_specimen_words(self):
        rules = []
        with open(os.path.join(ROOT, "tests", "denylist_specimen.txt"), encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    tok, _, only = line.partition("@")
                    rules.append((tok.strip(), only.strip()))
        offenders = []
        for base, _, files in os.walk(os.path.join(ROOT, "xl2ai")):
            for fn in files:
                if not fn.endswith(".py"):
                    continue
                with open(os.path.join(base, fn), encoding="utf-8") as f:
                    text = f.read()
                for tok, only in rules:
                    if only == fn:
                        continue
                    if re.search(r"\b" + re.escape(tok) + r"\b", text, re.IGNORECASE):
                        offenders.append(f"{fn}: '{tok}'")
        self.assertEqual(offenders, [], "domain/environment words found in the generic platform")

    def test_platform_never_imports_packs(self):
        for base, _, files in os.walk(os.path.join(ROOT, "xl2ai")):
            for fn in files:
                if fn.endswith(".py"):
                    with open(os.path.join(base, fn), encoding="utf-8") as f:
                        self.assertNotRegex(f.read(), r"^\s*(from|import)\s+packs\b", f"{fn} imports packs")

    def test_cli_lists_stages_and_rejects_unknown(self):
        from xl2ai import cli
        self.assertIn("extract", cli.STAGES)
        self.assertEqual(cli.main(["nope"]), 2)
        self.assertEqual(cli.main([]), 0)

    def test_entry_points_are_the_same_program(self):
        for args in (["-m", "xl2ai", "--help"], ["excel_to_sqlite.py", "--help"]):
            r = subprocess.run([sys.executable, *args], cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
        r = subprocess.run([sys.executable, "-m", "xl2ai", "extract", "--help"], cwd=ROOT, capture_output=True, text=True)
        self.assertIn("--no-verify", r.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)

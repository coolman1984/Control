"""Guards proving the SUPPORTED G-MES entrances stay legacy-only.

`src/gmes` was removed in HISTORY.md Phase 57 (CLAUDE.md section 0 is the
authority on the current architecture) - recoverable from git history
(commit `59eb838`, branch `archive/standalone-gmes-before-removal`), never
present as a directory in this tree. These tests exist to fail loudly the
moment somebody reconnects the supported entrance to a same-shaped package,
or recreates it - which is exactly how the situation this restoration undid
came about in the first place: the entrances were repointed while the
legacy engine itself was left untouched, so nothing broke and nothing
complained.

`GMES_Workflow.bat` has TWO branches and they reach two different programs.
Both are guarded here, because testing only the double-click path would have
let the argument branch keep running the frozen package unnoticed.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Every module the supported entrance must resolve to, all flat files at the
# repository root. Named explicitly rather than derived, so that deleting one
# from the chain is a test failure and not a silently shorter list.
REQUIRED_LEGACY = (
    "run_gmes_workflow",
    "gmes_core",
    "gmes_login",
    "gmes_common",
    "gmes_open_screen",
    "gmes_profile",
    "cdp_common",
)

_PROBE = r"""
import importlib, json, sys
importlib.import_module(sys.argv[1])
print(json.dumps({name: getattr(mod, "__file__", None)
                  for name, mod in sys.modules.items()
                  if not name.startswith("_")}))
"""


def modules_loaded_by(module_name):
    """Import `module_name` in a FRESH interpreter; report what it loaded.

    A subprocess, deliberately, and not an in-process import. `src/gmes` is
    gone now, but this guard predates that (it caught real cross-test
    `sys.modules` pollution while the frozen package still existed
    alongside these tests) and stays this way on purpose: a clean
    interpreter is the only check that can never be fooled by whatever
    some other test - today's or a future one's - happened to import first.
    An in-process check would blame the legacy entrance for imports that
    are not its doing, or pass for the wrong reason.
    """
    result = subprocess.run(
        [sys.executable, "-c", _PROBE, module_name],
        cwd=ROOT, capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"importing {module_name!r} in a clean interpreter failed:\n"
            f"{result.stderr.strip()}")
    return json.loads(result.stdout)


def assert_legacy_only(case, module_name, loaded):
    intruders = sorted(n for n in loaded if n == "gmes" or n.startswith("gmes."))
    case.assertEqual(
        intruders, [],
        f"{module_name} pulled in the frozen standalone package: {intruders}. "
        "The supported entrance must reach the flat legacy modules only - see "
        "CLAUDE.md section 0.")


class WorkflowEntranceIsLegacyOnly(unittest.TestCase):
    """GMES_Workflow.bat with no arguments -> python run_gmes_workflow.py"""

    @classmethod
    def setUpClass(cls):
        cls.loaded = modules_loaded_by("run_gmes_workflow")

    def test_it_loads_no_module_from_the_frozen_standalone_package(self):
        assert_legacy_only(self, "run_gmes_workflow", self.loaded)

    def test_every_required_legacy_module_is_a_repo_root_file(self):
        for name in REQUIRED_LEGACY:
            with self.subTest(module=name):
                self.assertIn(
                    name, self.loaded,
                    f"{name} was not imported at all - the legacy chain has "
                    "changed shape")
                path = self.loaded[name]
                self.assertIsNotNone(path, f"{name} has no __file__")
                self.assertEqual(
                    os.path.dirname(os.path.abspath(path)), ROOT,
                    f"{name} resolved to {path!r}, which is not the repo root")


class ReportEntranceIsLegacyOnly(unittest.TestCase):
    """GMES_Workflow.bat WITH arguments -> python gmes_report.py run %*

    A separate execution entrance reaching a different program, so it needs
    its own proof; the workflow test above says nothing about it.
    """

    @classmethod
    def setUpClass(cls):
        cls.loaded = modules_loaded_by("gmes_report")

    def test_it_loads_no_module_from_the_frozen_standalone_package(self):
        assert_legacy_only(self, "gmes_report", self.loaded)

    def test_it_reaches_the_flat_legacy_core(self):
        for name in ("gmes_core", "cdp_common"):
            with self.subTest(module=name):
                self.assertIn(name, self.loaded)
                self.assertEqual(
                    os.path.dirname(os.path.abspath(self.loaded[name])), ROOT)


class LauncherBranches(unittest.TestCase):
    """The .bat is checked as text: it is the one link in the chain that no
    import can prove, and it is where the repointing happened last time."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(ROOT, "GMES_Workflow.bat"),
                  encoding="utf-8", errors="replace") as handle:
            cls.bat = handle.read()
        # Batch is case-insensitive: `PythonPath`, `Python -M Gmes` and
        # `GMES.BAT` all run exactly the same as their lowercase spellings, so
        # a case-sensitive guard could be walked straight past.
        cls.folded = cls.bat.lower()

    def test_the_no_argument_branch_runs_the_legacy_workflow(self):
        self.assertIn("python run_gmes_workflow.py", self.folded)

    def test_the_argument_branch_runs_the_legacy_report_command(self):
        self.assertIn("python gmes_report.py run %*", self.folded)

    def test_the_launcher_cannot_reach_the_frozen_package(self):
        for forbidden in ("pythonpath", "python -m gmes", "gmes.bat"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(
                    forbidden, self.folded,
                    f"GMES_Workflow.bat contains {forbidden!r} (matched "
                    "case-insensitively), which routes the supported entrance "
                    "back into the frozen package")


class NoStandalonePackageInTree(unittest.TestCase):
    """The former src/gmes package (HISTORY.md Phase 57) is recoverable from
    git history (commit 59eb838, branch
    archive/standalone-gmes-before-removal) and from nowhere else. If a
    future change ever recreates it as a tracked directory, that is exactly
    the "second hidden G-MES engine" this restoration exists to prevent -
    this fails loudly rather than silently tolerating it."""

    def test_no_gmes_package_directory_is_tracked(self):
        self.assertFalse(
            (Path(ROOT) / "src" / "gmes").is_dir(),
            "src/gmes exists again - the standalone package should only "
            "ever be recovered from git history, never live in the tree")

    def test_no_pyproject_toml_packages_a_gmes_console_script(self):
        # pyproject.toml itself was removed with the package (it had no
        # remaining purpose - requirements.txt is the only supported
        # dependency mechanism); this guards against it quietly coming back
        # as a vehicle for a `gmes` console script.
        candidate = Path(ROOT) / "pyproject.toml"
        if candidate.is_file():
            self.assertNotIn("gmes.cli.app:main", candidate.read_text(encoding="utf-8"))

    def test_no_duplicate_comparison_entrance_remains(self):
        for name in ("run_gmes_workflow_LEGACY_TEST.py", "GMES_Workflow_LEGACY_TEST.bat"):
            with self.subTest(name=name):
                self.assertFalse((Path(ROOT) / name).exists(), name)


if __name__ == "__main__":
    unittest.main(verbosity=2)

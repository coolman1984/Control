"""Offline semantic guard: .project-eye/ and the .md files must agree with
the code that is actually in this tree.

Two systems have been removed from this repository, each after reaching real
live sessions, and each time the documents went on describing them for a
while afterwards. That is the failure this file exists to make impossible:
HISTORY.md Phase 57.1 found every governance document naming the engine that
was about to be deleted, so an agent could read one file and conclude the
opposite of another and be following the repository either way.
"""
import json
import os
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ProjectEyeTruth(unittest.TestCase):
    def test_manifest_and_governance_agree(self):
        manifest = json.loads((ROOT / ".project-eye" / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["entrypoint"], "PROJECT_EYE.md")
        self.assertEqual(manifest["graph"], ".project-eye/graph.yaml")
        self.assertEqual(manifest["rules"], ".project-eye/rules.yaml")
        self.assertIn("test_project_eye.py", manifest["enforcement"])
        graph = (ROOT / ".project-eye" / "graph.yaml").read_text(encoding="utf-8")
        rules = (ROOT / ".project-eye" / "rules.yaml").read_text(encoding="utf-8")
        self.assertIn("name: gmes\n      status: active", graph)
        self.assertIn("primary: GMES_Workflow.bat", graph)
        for credential_path in ("%LOCALAPPDATA%/GMES_Automation/credentials.dat",
                                "%LOCALAPPDATA%/GMES/credentials.dat"):
            with self.subTest(credential_path=credential_path):
                self.assertIn(credential_path, graph)
                self.assertIn(credential_path, rules)
        self.assertIn("logs/", graph)
        self.assertIn("no-standalone-package-in-tree", rules)
        self.assertIn("gmes-screenshots-use-explicit-targets", rules)
        self.assertFalse((ROOT / "src" / "gmes").exists())
        self.assertFalse((ROOT / "pyproject.toml").exists())

        # These duplicate launchers are historical evidence only. No current
        # document may present a deleted executable entrance as available.
        deleted_entrances = ("GMES_Workflow_LEGACY_TEST.bat",
                             "run_gmes_workflow_LEGACY_TEST.py")
        for document in ROOT.glob("*.md"):
            if document.name == "HISTORY.md":
                continue
            text = document.read_text(encoding="utf-8")
            for entrance in deleted_entrances:
                with self.subTest(document=document.name, entrance=entrance):
                    self.assertNotIn(entrance, text)


class NerpIsGone(unittest.TestCase):
    """N-ERP was removed in HISTORY.md Phase 72 and is recoverable from
    `archive/nerp-before-removal`. It is not coming back into this tree by
    accident - and, more to the point, it must not come back WITHOUT the two
    defects it left unfixed being addressed first (Open Items #3 and #14,
    closed by removal rather than by fix)."""

    NERP_FILES = ("search_tcode.py", "execute_filters.py", "export_to_excel.py",
                  "run_nerp_workflow.py", "NERP_Workflow.bat",
                  "tests/mock_nerp_server.py", "tests/test_live_chrome.py")

    def test_no_nerp_entrance_is_back_in_the_tree(self):
        for name in self.NERP_FILES:
            with self.subTest(name=name):
                self.assertFalse(
                    (ROOT / name).exists(),
                    f"{name} is back. N-ERP was removed in Phase 72; if it is "
                    "being revived, Open Items #3 and #14 are still open in it.")

    def test_the_graph_does_not_present_nerp_as_active(self):
        graph = (ROOT / ".project-eye" / "graph.yaml").read_text(encoding="utf-8")
        self.assertNotIn("name: nerp\n      status: active", graph)
        self.assertIn("name: removed-nerp", graph)


class SharedCdpGuardsSurvive(unittest.TestCase):
    """The near-miss of Phase 72.2, turned into a test.

    `tests/test_unit.py` was named for N-ERP and documented as the N-ERP
    suite, and was simultaneously the only automated proof of two fixes that
    protect G-MES on every run. Deleting it on the strength of its name would
    have left every other suite green with nothing to show that anything had
    been lost. The guards now live in a file named after the module they
    protect - and this makes deleting THAT file fail loudly."""

    def test_the_shared_cdp_guard_file_exists(self):
        self.assertTrue(
            (ROOT / "tests" / "test_cdp_common.py").is_file(),
            "tests/test_cdp_common.py is gone. It holds the only automated "
            "proof of --disable-popup-blocking and capture_screenshot(tab=); "
            "see HISTORY.md Phase 72.2 for why that matters.")

    def test_it_still_guards_the_two_live_proven_fixes(self):
        text = (ROOT / "tests" / "test_cdp_common.py").read_text(encoding="utf-8")
        for needle in ("--disable-popup-blocking",
                       "TestUserProfileChromeLaunchArguments",
                       "TestScreenshotTabOverrideIsBackwardCompatible"):
            with self.subTest(needle=needle):
                self.assertIn(needle, text)

    def test_the_rule_naming_it_is_still_in_force(self):
        rules = (ROOT / ".project-eye" / "rules.yaml").read_text(encoding="utf-8")
        self.assertIn("shared-cdp-guards-must-not-be-deleted-with-a-system", rules)


class ShippedScreensCarryStructureNotPresets(unittest.TestCase):
    """HISTORY.md Phase 79.6: `screens_known/<CODE>.json` is committed and
    shipped to every user - it is the "what does this screen have" half, not
    "what should a report using it mean". `options` shipped from Phase 76
    onward anyway, so a new user could silently inherit someone else's
    "Create Date" vs "Plan Date" decision. `_SHIPPABLE_KEYS` no longer
    includes it; this guards the COMMITTED files staying that way too, not
    just the export code that produces them."""

    def test_no_shipped_screen_carries_an_options_key(self):
        directory = ROOT / "screens_known"
        for path in sorted(directory.glob("*.json")):
            with self.subTest(screen=path.name):
                data = json.loads(path.read_text(encoding="utf-8"))
                self.assertNotIn("options", data,
                                 f"{path.name} still ships an option preset - "
                                 "regenerate it with gmes_profile.export_shippable()")


class CiActuallyRunsWhatItClaimsTo(unittest.TestCase):
    """HISTORY.md Open Item 25 / Phase 79.3: "seven offline suites" was a
    claim in CLAUDE.md and README.md with nothing making it true on GitHub -
    a push to `main` could break every one of them and nothing would say so.
    Mechanically checked here the same way every other governance claim in
    this file is, rather than trusted as a document that agrees with itself."""

    ALL_SEVEN = ("test_cdp_common.py", "test_gmes_core.py",
                "test_legacy_hardening.py", "test_gmes_workflow.py",
                "test_legacy_entrance.py", "test_project_eye.py",
                "test_browser_bootstrap.py")

    def test_the_workflow_file_exists(self):
        self.assertTrue(
            (ROOT / ".github" / "workflows" / "tests.yml").is_file(),
            "no CI workflow runs the offline suites on push/PR - "
            "HISTORY.md Open Item 25")

    def test_it_runs_every_one_of_the_seven_suites(self):
        text = (ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
        for suite in self.ALL_SEVEN:
            with self.subTest(suite=suite):
                self.assertIn(suite, text,
                              f"{suite} is not run by CI - a suite can be silently "
                              "skipped by CI even while README.md claims all seven run")

    def test_it_triggers_on_both_push_and_pull_request_to_main(self):
        # `"main"` alone would be a no-op check - the workflow's own comment
        # says "against main" in prose, so that substring is present no
        # matter what the actual trigger config says. Matched as the real
        # `branches: [main]` line instead, once per trigger.
        text = (ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
        self.assertIn("push:", text)
        self.assertIn("pull_request:", text)
        self.assertEqual(text.count("branches: [main]"), 2,
                         "expected 'branches: [main]' under both push and "
                         "pull_request, found a different count")

    def test_it_runs_on_windows_not_a_platform_this_project_never_targets(self):
        # CLAUDE.md 5: this project is Windows-only. gmes_credentials.py's
        # DPAPI store alone would fail to import on a non-Windows runner.
        text = (ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
        self.assertIn("windows-latest", text)

    def test_it_has_a_timeout_so_a_leaked_network_call_cannot_hang_ci_forever(self):
        # The exact regression HISTORY.md Phase 78.5 found by hand - a test
        # that still reports "ok" while quietly burning real time on a
        # network call. A timeout is the CI-side backstop for that shape
        # recurring undetected.
        #
        # A bare substring check would pass even with the actual YAML key
        # removed, because the workflow's own comment block explains the key
        # in prose (`timeout-minutes` on the job is the backstop...") - found
        # by sabotaging the real key and watching this assertion NOT fail.
        # Matched as an actual mapping key line instead.
        lines = (ROOT / ".github" / "workflows" / "tests.yml").read_text(
            encoding="utf-8").splitlines()
        self.assertTrue(
            any(line.strip().startswith("timeout-minutes:") for line in lines),
            "no 'timeout-minutes:' key found as an actual YAML line - a mention "
            "in a comment does not count")


if __name__ == "__main__":
    unittest.main(verbosity=2)

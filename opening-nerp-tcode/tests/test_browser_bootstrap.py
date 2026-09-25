"""Offline guards for the browser-neutral layer and the first-run bootstrap.

    python tests/test_browser_bootstrap.py

No browser is started, no registry is read, and the user's real profile is
never touched: `winreg`, `tasklist` and `robocopy` are all replaced, and every
path under test is a temporary directory. `GMES_BROWSER_STATE` is redirected
in `setUp` for every class that can write, so a test run cannot read or
overwrite the developer's own first-run record (CLAUDE.md 2.1a).

What this file protects, and why each part is not optional:

  TestDefaultBrowserDetection
      first run picks the browser the employee actually uses. Getting this
      wrong copies the wrong profile and produces a session-less automation
      profile that then tries a real ADFS sign-in - the expensive failure
      (HISTORY.md Phase 74.1).
  TestProfileEnumeration
      a corporate machine routinely has several browser profiles. Picking
      "the first one" copies the wrong person's session, or an empty one.
  TestCandidateSources
      the default-browser preference and the fallback to the other supported
      browser, which is the whole of requirement 3.
  TestLocalStateNormalisation / TestPreferencesMerge
      a copied profile that is internally inconsistent shows a profile picker
      or loses the automation settings. Both fail in ways that look like
      something else entirely.
  TestEnsureBootstrapped
      the decision table. The two entries that matter most are "already
      onboarded -> copy nothing" and "already has a profile -> copy nothing":
      a second copy would overwrite the session the automation has since
      earned, which is the same class of loss the `--refresh-profile` warning
      exists for (HISTORY.md Phase 20).
  TestFailureRecovery
      an interrupted first run must leave nothing that a later run will
      launch. Promotion is a rename after verification, so a partial copy is
      never the profile being driven.
  TestBrowserNeutralLaunch
      Edge has to reach the same launcher, with the same flags - including
      --disable-popup-blocking, without which the AD SSO window is swallowed
      silently (HISTORY.md Phase 56.1).
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cdp_common  # noqa: E402
import gmes_browsers  # noqa: E402


def write_json(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)


def make_profile(user_data, name, cookies=True, modern=True):
    """A believable profile directory inside a user-data directory."""
    path = os.path.join(user_data, name)
    os.makedirs(path, exist_ok=True)
    write_json(os.path.join(path, "Preferences"), {"profile": {"name": name}})
    if cookies:
        if modern:
            os.makedirs(os.path.join(path, "Network"), exist_ok=True)
            open(os.path.join(path, "Network", "Cookies"), "wb").close()
        else:
            open(os.path.join(path, "Cookies"), "wb").close()
    return path


class TempStateMixin:
    """Redirect the first-run record into a temp file.

    Without this a test run would read - and `_record()` would OVERWRITE - the
    developer's own `%LOCALAPPDATA%\\GMES_Automation\\browser.json`, which is
    exactly the kind of runtime-data damage CLAUDE.md 2.1a forbids."""

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp(prefix="gmes-bootstrap-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        patcher = mock.patch.dict(
            os.environ, {"GMES_BROWSER_STATE": os.path.join(self.tmp, "browser.json")})
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in ("GMES_BROWSER", "GMES_BOOTSTRAP"):
            os.environ.pop(name, None)


class TestBrowserSpecTable(unittest.TestCase):
    """Chrome and Edge differ by a table row, not by a code path."""

    def test_both_supported_browsers_are_fully_described(self):
        for key in gmes_browsers.SUPPORTED:
            with self.subTest(browser=key):
                info = gmes_browsers.spec(key)
                for field in ("label", "short", "exe", "path_env",
                              "user_data_env", "user_data", "prog_ids",
                              "candidates"):
                    self.assertIn(field, info)
                self.assertTrue(info["prog_ids"])
                self.assertTrue(info["candidates"])

    def test_an_unknown_browser_is_a_clear_error(self):
        with self.assertRaises(ValueError) as ctx:
            gmes_browsers.spec("firefox")
        self.assertIn("firefox", str(ctx.exception))


class TestBrowserDiscovery(unittest.TestCase):
    """Neither browser is always in Program Files. Edge in particular installs
    under "Program Files (x86)" even on 64-bit Windows, and a per-user Chrome
    lands in %LOCALAPPDATA% - the case that used to die with a bare
    WinError 2."""

    def test_env_override_wins_for_each_browser(self):
        for key, env in (("chrome", "CHROME_PATH"), ("edge", "EDGE_PATH")):
            with self.subTest(browser=key):
                with mock.patch.dict(os.environ, {env: __file__}):
                    self.assertEqual(gmes_browsers.find_executable(key), __file__)

    def test_an_override_pointing_at_nothing_is_ignored_not_trusted(self):
        with mock.patch.dict(os.environ, {"EDGE_PATH": r"C:\nope\msedge.exe"}), \
             mock.patch("os.path.isfile", return_value=False), \
             mock.patch("shutil.which", return_value=None), \
             mock.patch.object(gmes_browsers, "_registry_roots", return_value=()):
            self.assertIsNone(gmes_browsers.find_executable("edge"))

    def test_a_known_install_path_is_found(self):
        wanted = gmes_browsers.BROWSERS["edge"]["candidates"][0]
        with mock.patch.dict(os.environ, {"EDGE_PATH": ""}), \
             mock.patch("os.path.isfile", side_effect=lambda p: p == wanted):
            self.assertEqual(gmes_browsers.find_executable("edge"), wanted)

    def test_path_lookup_is_used_before_the_registry(self):
        with mock.patch.dict(os.environ, {"CHROME_PATH": ""}), \
             mock.patch("os.path.isfile", return_value=False), \
             mock.patch("shutil.which", return_value=r"D:\portable\chrome.exe"):
            self.assertEqual(gmes_browsers.find_executable("chrome"),
                             r"D:\portable\chrome.exe")

    def test_the_registry_is_consulted_for_an_unusual_install(self):
        odd = r"D:\Corporate Apps\Edge\msedge.exe"
        with mock.patch.dict(os.environ, {"EDGE_PATH": ""}), \
             mock.patch("shutil.which", return_value=None), \
             mock.patch.object(gmes_browsers, "_registry_roots", return_value=("HKLM",)), \
             mock.patch.object(gmes_browsers, "_registry_value", return_value=odd), \
             mock.patch("os.path.isfile", side_effect=lambda p: p == odd):
            self.assertEqual(gmes_browsers.find_executable("edge"), odd)

    def test_a_missing_browser_is_none_not_an_exception(self):
        # "not installed" is a normal answer here - candidate_sources() has to
        # be able to skip it and try the other one.
        with mock.patch.dict(os.environ, {"CHROME_PATH": "", "EDGE_PATH": ""}), \
             mock.patch("os.path.isfile", return_value=False), \
             mock.patch("shutil.which", return_value=None), \
             mock.patch.object(gmes_browsers, "_registry_roots", return_value=()):
            for key in gmes_browsers.SUPPORTED:
                with self.subTest(browser=key):
                    self.assertIsNone(gmes_browsers.find_executable(key))

    def test_cdp_common_still_raises_an_actionable_error_for_chrome(self):
        # find_chrome() is the seam the rest of the project and its tests use;
        # it must keep turning "not found" into an actionable RuntimeError.
        with mock.patch.object(gmes_browsers, "find_executable", return_value=None):
            with self.assertRaises(RuntimeError) as ctx:
                cdp_common.find_chrome()
        self.assertIn("CHROME_PATH", str(ctx.exception))

    def test_cdp_common_names_the_right_variable_for_edge(self):
        with mock.patch.object(gmes_browsers, "find_executable", return_value=None):
            with self.assertRaises(gmes_browsers.BrowserNotFound) as ctx:
                cdp_common.find_browser("edge")
        self.assertIn("EDGE_PATH", str(ctx.exception))
        self.assertIn("msedge.exe", str(ctx.exception))


class TestDefaultBrowserDetection(unittest.TestCase):
    """Which browser Windows opens https links with.

    Read from the UserChoice association rather than guessed. A machine whose
    default is Firefox is a real and ordinary case: the answer is "no
    supported default", not an error, and the fallback picks up from there."""

    def _with_prog_id(self, prog_id):
        return mock.patch.object(gmes_browsers, "_registry_value",
                                 return_value=prog_id)

    def test_chrome_is_recognised(self):
        with self._with_prog_id("ChromeHTML"):
            self.assertEqual(gmes_browsers.default_browser_key(), "chrome")

    def test_edge_is_recognised(self):
        with self._with_prog_id("MSEdgeHTM"):
            self.assertEqual(gmes_browsers.default_browser_key(), "edge")

    def test_channel_and_version_suffixes_still_match(self):
        # Real ProgIds carry a per-install suffix, e.g. "ChromeHTML.8XQ...".
        for prog_id, expected in (("ChromeHTML.7F2A9C", "chrome"),
                                  ("MSEdgeHTM.9ABCDEF", "edge"),
                                  ("MSEdgeBETA", "edge"),
                                  ("MSEdgeDEV", "edge")):
            with self.subTest(prog_id=prog_id):
                with self._with_prog_id(prog_id):
                    self.assertEqual(gmes_browsers.default_browser_key(), expected)

    def test_matching_is_case_insensitive(self):
        with self._with_prog_id("chromehtml"):
            self.assertEqual(gmes_browsers.default_browser_key(), "chrome")

    def test_an_unsupported_default_is_none_not_a_guess(self):
        for prog_id in ("FirefoxURL-308046B0AF4A39CB", "OperaStable", "IE.HTTPS"):
            with self.subTest(prog_id=prog_id):
                with self._with_prog_id(prog_id):
                    self.assertIsNone(gmes_browsers.default_browser_key())

    def test_a_missing_association_is_none(self):
        with self._with_prog_id(None):
            self.assertIsNone(gmes_browsers.default_browser_key())

    def test_no_registry_at_all_does_not_raise(self):
        with mock.patch.dict(sys.modules, {"winreg": None}):
            self.assertIsNone(gmes_browsers.default_browser_key())


class TestRealProfileProtection(unittest.TestCase):
    """The user's real profile is read-only here, for BOTH browsers.

    The launcher guard existed for Chrome; Edge reaching the same launcher
    without the same guard would have been a way to point remote debugging at
    a real profile (CLAUDE.md 2.1)."""

    def test_each_browser_has_its_own_real_user_data_dir(self):
        with mock.patch.dict(os.environ, {"LOCALAPPDATA": r"C:\fake\Local"}):
            os.environ.pop("CHROME_USER_DATA_DIR", None)
            os.environ.pop("EDGE_USER_DATA_DIR", None)
            chrome = gmes_browsers.real_user_data_dir("chrome")
            edge = gmes_browsers.real_user_data_dir("edge")
        self.assertIn("Google", chrome)
        self.assertIn("Edge", edge)
        self.assertNotEqual(chrome, edge)

    def test_env_overrides_are_honoured(self):
        with mock.patch.dict(os.environ, {"EDGE_USER_DATA_DIR": r"E:\edge"}):
            self.assertEqual(gmes_browsers.real_user_data_dir("edge"), r"E:\edge")

    def test_an_exact_real_profile_is_matched(self):
        for key in gmes_browsers.SUPPORTED:
            with self.subTest(browser=key):
                real = gmes_browsers.real_user_data_dir(key)
                self.assertEqual(gmes_browsers.protected_match(real), key)

    def test_a_path_inside_a_real_profile_is_matched_too(self):
        # "...\\User Data\\Default" is just as much the user's own profile.
        real = gmes_browsers.real_user_data_dir("edge")
        self.assertEqual(
            gmes_browsers.protected_match(os.path.join(real, "Default")), "edge")

    def test_the_tools_own_profile_is_not_protected(self):
        self.assertIsNone(
            gmes_browsers.protected_match(cdp_common.automation_profile_dir()))
        self.assertIsNone(gmes_browsers.protected_match(None))

    def test_the_match_is_case_insensitive_like_windows_itself(self):
        # os.path.abspath normalises separators but NOT case, so comparing two
        # abspath results declared C:\Users\... and c:\users\... to be
        # different places - and the guard whose entire job is to refuse the
        # real profile waved a lower-cased GMES_PROFILE_DIR straight through.
        # Verified live before the fix: protected_match() returned None for
        # the real profile path in lower case.
        for key in gmes_browsers.SUPPORTED:
            real = gmes_browsers.real_user_data_dir(key)
            for variant in (real.lower(), real.upper(),
                            real.replace("\\", "/")):
                with self.subTest(browser=key, variant=variant):
                    self.assertEqual(gmes_browsers.protected_match(variant), key)

    def test_a_case_differing_path_inside_a_real_profile_is_matched(self):
        real = gmes_browsers.real_user_data_dir("chrome")
        inside = os.path.join(real, "Default").lower()
        self.assertEqual(gmes_browsers.protected_match(inside), "chrome")

    def test_same_path_does_not_treat_a_sibling_as_inside(self):
        # "...\profiles\default2" must not count as inside "...\profiles\default".
        base = os.path.join(self.__class__.__name__, "profiles", "default")
        self.assertFalse(gmes_browsers.same_path(base, base + "2"))
        self.assertTrue(gmes_browsers.same_path(base, os.path.join(base, "x")))


class TestProfileEnumeration(unittest.TestCase):
    """A corporate machine routinely has several browser profiles."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gmes-profiles-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.user_data = os.path.join(self.tmp, "User Data")
        os.makedirs(self.user_data)

    def _local_state(self, info_cache, last_used="", last_active=None):
        write_json(os.path.join(self.user_data, "Local State"), {
            "os_crypt": {"encrypted_key": "BASE64KEY"},
            "profile": {"info_cache": info_cache, "last_used": last_used,
                        "last_active_profiles": last_active or []},
        })

    def test_profiles_are_listed_with_their_display_names(self):
        make_profile(self.user_data, "Default")
        make_profile(self.user_data, "Profile 1")
        self._local_state({"Default": {"name": "Personal"},
                           "Profile 1": {"name": "Work"}})
        found = {p["dir"]: p["name"]
                 for p in gmes_browsers.list_profiles(user_data_dir=self.user_data)}
        self.assertEqual(found, {"Default": "Personal", "Profile 1": "Work"})

    def test_the_last_used_profile_is_ranked_first(self):
        make_profile(self.user_data, "Default")
        make_profile(self.user_data, "Profile 2")
        self._local_state({"Default": {"name": "Personal"},
                           "Profile 2": {"name": "Work"}}, last_used="Profile 2")
        profiles = gmes_browsers.list_profiles(user_data_dir=self.user_data)
        self.assertEqual(gmes_browsers.preferred_profile(profiles)["dir"], "Profile 2")

    def test_a_profile_with_a_session_outranks_one_without(self):
        make_profile(self.user_data, "Default", cookies=False)
        make_profile(self.user_data, "Profile 1", cookies=True)
        self._local_state({"Default": {"name": "Empty"},
                           "Profile 1": {"name": "Real"}})
        profiles = gmes_browsers.list_profiles(user_data_dir=self.user_data)
        self.assertEqual(gmes_browsers.preferred_profile(profiles)["dir"], "Profile 1")

    def test_both_cookie_locations_count_as_a_session(self):
        # Chromium moved cookies to <profile>\Network\Cookies; a corporate
        # image can still be running a build that uses the old place.
        make_profile(self.user_data, "Default", modern=True)
        make_profile(self.user_data, "Profile 1", modern=False)
        self._local_state({})
        sessions = {p["dir"]: p["has_session"]
                    for p in gmes_browsers.list_profiles(user_data_dir=self.user_data)}
        self.assertEqual(sessions, {"Default": True, "Profile 1": True})

    def test_a_profile_on_disk_but_not_in_the_index_is_still_found(self):
        make_profile(self.user_data, "Profile 3")
        self._local_state({})
        dirs = [p["dir"] for p in gmes_browsers.list_profiles(user_data_dir=self.user_data)]
        self.assertIn("Profile 3", dirs)

    def test_an_indexed_profile_that_no_longer_exists_is_dropped(self):
        make_profile(self.user_data, "Default")
        self._local_state({"Default": {"name": "Personal"},
                           "Profile 9": {"name": "Deleted"}})
        dirs = [p["dir"] for p in gmes_browsers.list_profiles(user_data_dir=self.user_data)]
        self.assertEqual(dirs, ["Default"])

    def test_a_missing_or_corrupt_local_state_is_survivable(self):
        make_profile(self.user_data, "Default")
        self.assertEqual(
            [p["dir"] for p in gmes_browsers.list_profiles(user_data_dir=self.user_data)],
            ["Default"])
        with open(os.path.join(self.user_data, "Local State"), "w", encoding="utf-8") as fh:
            fh.write("{not json")
        self.assertEqual(
            [p["dir"] for p in gmes_browsers.list_profiles(user_data_dir=self.user_data)],
            ["Default"])
        self.assertEqual(gmes_browsers.read_local_state(self.user_data), {})

    def test_a_user_data_dir_that_does_not_exist_is_empty_not_an_error(self):
        self.assertEqual(
            gmes_browsers.list_profiles(user_data_dir=os.path.join(self.tmp, "nope")), [])
        self.assertIsNone(gmes_browsers.preferred_profile([]))


def make_cookies_db(path, host_keys=()):
    """A genuine, queryable SQLite Cookies-shaped database - not the empty
    stand-in `make_profile()` uses, because proving the POSITIVE match path
    needs a real file `_has_gmes_evidence()` can actually query. The
    encrypted value is a fake, non-decryptable blob on purpose: nothing in
    this project may ever read or attempt to decrypt a cookie value."""
    import sqlite3
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE cookies (host_key TEXT, name TEXT, "
                     "encrypted_value BLOB)")
        for host in host_keys:
            conn.execute("INSERT INTO cookies VALUES (?, 'JSESSIONID', ?)",
                         (host, b"v20-not-actually-decryptable"))
        conn.commit()
    finally:
        conn.close()


def make_history_db(path, urls=()):
    import sqlite3
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE urls (url TEXT, title TEXT)")
        for url in urls:
            conn.execute("INSERT INTO urls VALUES (?, 'title')", (url,))
        conn.commit()
    finally:
        conn.close()


class TestGmesEvidenceScoring(unittest.TestCase):
    """`_has_gmes_evidence()` - the difference between "this profile has
    browsed somewhere" (`has_session`) and "this profile has actually reached
    G-MES" (HISTORY.md Phase 79: ranking by recency alone could copy a
    Personal profile used more often over a Work profile that has the real
    session)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gmes-evidence-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_a_cookie_scoped_to_the_gmes_host_is_confirmed_evidence(self):
        cookies = os.path.join(self.tmp, "Network", "Cookies")
        make_cookies_db(cookies, host_keys=["seegmes4.sec.samsung.net"])
        self.assertIs(gmes_browsers._has_gmes_evidence(self.tmp), True)

    def test_a_visited_gmes_url_in_history_is_also_confirmed_evidence(self):
        history = os.path.join(self.tmp, "History")
        make_history_db(history, urls=[
            "http://seegmes4.sec.samsung.net/mes4/sm/nexacro/index_ext_2318.html"])
        self.assertIs(gmes_browsers._has_gmes_evidence(self.tmp), True)

    def test_old_style_cookies_location_is_also_checked(self):
        cookies = os.path.join(self.tmp, "Cookies")
        make_cookies_db(cookies, host_keys=["seegmes4.sec.samsung.net"])
        self.assertIs(gmes_browsers._has_gmes_evidence(self.tmp), True)

    def test_cookies_for_unrelated_sites_are_confirmed_absence_not_unknown(self):
        cookies = os.path.join(self.tmp, "Network", "Cookies")
        make_cookies_db(cookies, host_keys=["mail.example.com", ".samsung.com"])
        self.assertIs(gmes_browsers._has_gmes_evidence(self.tmp), False)

    def test_no_database_at_all_is_unknown_not_false(self):
        # No Cookies, no History - a genuinely never-used profile directory.
        self.assertIsNone(gmes_browsers._has_gmes_evidence(self.tmp))

    def test_a_file_that_exists_but_is_not_a_real_database_is_unknown(self):
        # make_profile()'s fixture: an empty stand-in file, not a real
        # SQLite database - must not raise and must not be misread as
        # "checked, found nothing" (which the ranking treats identically to
        # unknown, but the distinction still matters for correctness).
        cookies = os.path.join(self.tmp, "Network", "Cookies")
        os.makedirs(os.path.dirname(cookies))
        open(cookies, "wb").close()
        self.assertIsNone(gmes_browsers._has_gmes_evidence(self.tmp))

    def test_it_works_while_the_browser_holds_a_reserved_lock(self):
        # A RESERVED lock (a writer that has started but not yet committed)
        # still lets a read-only connection through - Phase 75 deliberately
        # does not gate the copy on the browser being closed, so the common
        # case (a writer mid-transaction, not mid-commit) has to work either
        # way. Simulated here by holding a second connection open across the
        # check without ever letting it actually commit.
        import sqlite3
        cookies = os.path.join(self.tmp, "Network", "Cookies")
        make_cookies_db(cookies, host_keys=["seegmes4.sec.samsung.net"])
        holder = sqlite3.connect(cookies)
        try:
            holder.execute("BEGIN IMMEDIATE")
            self.assertIs(gmes_browsers._has_gmes_evidence(self.tmp), True)
        finally:
            holder.rollback()
            holder.close()

    def test_a_genuinely_locked_database_is_unknown_not_no_evidence(self):
        # HISTORY.md Open Item 38: `immutable=1` was a promise the file would
        # not change while open, made about a file a running browser could
        # still be writing to - SQLite's own docs call the result of a broken
        # immutable promise undefined. An EXCLUSIVE lock (mid-commit) DOES
        # block a read-only connection; that must count as "could not be
        # checked", never as "checked, no evidence" - the profile most likely
        # to be mid-write is the one actually signed into G-MES right now.
        import sqlite3
        cookies = os.path.join(self.tmp, "Network", "Cookies")
        make_cookies_db(cookies, host_keys=["seegmes4.sec.samsung.net"])
        holder = sqlite3.connect(cookies, timeout=0)
        try:
            holder.execute("BEGIN EXCLUSIVE")
            self.assertIsNone(gmes_browsers._has_gmes_evidence(self.tmp))
        finally:
            holder.rollback()
            holder.close()

    def test_immutable_is_never_asserted_against_a_live_file(self):
        import inspect
        source = inspect.getsource(gmes_browsers._has_gmes_evidence)
        self.assertNotIn("immutable", source.split('"""', 2)[-1])

    def test_only_host_key_and_url_are_ever_queried_never_a_cookie_value(self):
        # Cookie values are App-Bound-Encrypted and this project never
        # decrypts one anywhere. Proven by reading the function's own CODE
        # (not its docstring, which legitimately explains that in prose) for
        # the column it actually selects and for any decryption call.
        import inspect
        source = inspect.getsource(gmes_browsers._has_gmes_evidence)
        body = source.split('"""', 2)[-1]   # drop the docstring
        self.assertNotIn("encrypted_value", body)
        self.assertNotIn("value", body)
        self.assertNotIn("CryptUnprotectData", body)
        self.assertNotIn("decrypt(", body)

    def test_the_profile_with_confirmed_gmes_evidence_wins_over_mere_recency(self):
        # The exact scenario Phase 79 fixes: Personal is used MORE OFTEN
        # (last_used) but Work is the one with the real G-MES session.
        user_data = os.path.join(self.tmp, "User Data")
        make_profile(user_data, "Default")     # "Personal"
        make_profile(user_data, "Profile 1")   # "Work"
        write_json(os.path.join(user_data, "Local State"), {
            "profile": {"info_cache": {"Default": {"name": "Personal"},
                                       "Profile 1": {"name": "Work"}},
                        "last_used": "Default", "last_active_profiles": ["Default"]}})
        make_cookies_db(os.path.join(user_data, "Profile 1", "Network", "Cookies"),
                        host_keys=["seegmes4.sec.samsung.net"])

        profiles = gmes_browsers.list_profiles(user_data_dir=user_data)
        winner = gmes_browsers.preferred_profile(profiles)
        self.assertEqual(winner["dir"], "Profile 1",
                         "recency won over confirmed G-MES evidence")

    def test_recency_still_decides_between_two_profiles_with_no_evidence_either_way(self):
        # has_gmes_evidence is False/None for both - existing tiebreakers
        # (last_used, has_session, ...) must still work exactly as before.
        user_data = os.path.join(self.tmp, "User Data")
        make_profile(user_data, "Default")
        make_profile(user_data, "Profile 2")
        write_json(os.path.join(user_data, "Local State"), {
            "profile": {"info_cache": {}, "last_used": "Profile 2",
                        "last_active_profiles": []}})
        profiles = gmes_browsers.list_profiles(user_data_dir=user_data)
        self.assertEqual(gmes_browsers.preferred_profile(profiles)["dir"], "Profile 2")


class TestCandidateSources(TempStateMixin, unittest.TestCase):
    """Requirement 3: prefer the Windows default browser, fall back safely."""

    def setUp(self):
        super().setUp()
        self.chrome_data = os.path.join(self.tmp, "chrome", "User Data")
        self.edge_data = os.path.join(self.tmp, "edge", "User Data")
        os.makedirs(self.chrome_data)
        os.makedirs(self.edge_data)
        make_profile(self.chrome_data, "Default")
        make_profile(self.edge_data, "Default")
        patcher = mock.patch.dict(os.environ, {
            "CHROME_USER_DATA_DIR": self.chrome_data,
            "EDGE_USER_DATA_DIR": self.edge_data,
        })
        patcher.start()
        self.addCleanup(patcher.stop)

    def _sources(self, default="edge", installed=("chrome", "edge")):
        with mock.patch.object(gmes_browsers, "default_browser_key", return_value=default), \
             mock.patch.object(gmes_browsers, "find_executable",
                               side_effect=lambda k: f"{k}.exe" if k in installed else None):
            return gmes_browsers.candidate_sources()

    def test_the_default_browser_comes_first(self):
        self.assertEqual([s["browser"] for s in self._sources(default="edge")],
                         ["edge", "chrome"])
        self.assertEqual([s["browser"] for s in self._sources(default="chrome")],
                         ["chrome", "edge"])

    def test_the_default_browser_is_flagged_as_such(self):
        sources = self._sources(default="edge")
        self.assertTrue(sources[0]["is_default_browser"])
        self.assertFalse(sources[1]["is_default_browser"])

    def test_an_unsupported_default_still_offers_both(self):
        # Default is Firefox. Neither is preferred, but both remain usable.
        keys = [s["browser"] for s in self._sources(default=None)]
        self.assertEqual(sorted(keys), ["chrome", "edge"])

    def test_a_browser_that_is_not_installed_is_not_a_candidate(self):
        self.assertEqual([s["browser"] for s in self._sources(default="edge",
                                                              installed=("chrome",))],
                         ["chrome"])

    def test_a_browser_with_no_session_is_not_a_candidate(self):
        shutil.rmtree(os.path.join(self.edge_data, "Default"))
        make_profile(self.edge_data, "Default", cookies=False)
        self.assertEqual([s["browser"] for s in self._sources(default="edge")],
                         ["chrome"])

    def test_nothing_usable_gives_no_candidates_rather_than_an_error(self):
        self.assertEqual(self._sources(default=None, installed=()), [])

    def test_gmes_browser_forces_the_choice(self):
        with mock.patch.dict(os.environ, {"GMES_BROWSER": "chrome"}):
            sources = self._sources(default="edge")
        self.assertEqual(sources[0]["browser"], "chrome")
        self.assertTrue(sources[0]["is_default_browser"])

    def test_an_unknown_gmes_browser_value_is_ignored(self):
        with mock.patch.dict(os.environ, {"GMES_BROWSER": "netscape"}):
            sources = self._sources(default="edge")
        self.assertEqual(sources[0]["browser"], "edge")


class TestMachineIdentity(unittest.TestCase):
    """A copy belongs to the PC it was made on. Chrome 140+ binds cookie
    encryption to the machine, so a carried-over profile decrypts nothing -
    silently (GMES_SKILL.md #1), which is the failure shape this project
    refuses to ship."""

    def test_it_is_stable_within_a_machine(self):
        self.assertEqual(gmes_browsers.machine_id(), gmes_browsers.machine_id())

    def test_it_changes_with_the_machine(self):
        with mock.patch.object(gmes_browsers, "_registry_value",
                              return_value="GUID-ONE"):
            one = gmes_browsers.machine_id()
        with mock.patch.object(gmes_browsers, "_registry_value",
                              return_value="GUID-TWO"):
            two = gmes_browsers.machine_id()
        self.assertNotEqual(one, two)

    def test_it_does_NOT_change_with_the_logon_account(self):
        # It used to. Under a Scheduled Task or a different logon context the
        # id changed, the record stopped matching, the "different PC" branch
        # fired, and the bootstrap copied a SECOND time into a new directory -
        # orphaning the profile holding the earned G-MES session. %LOCALAPPDATA%
        # already scopes this file per Windows account, so the account added
        # instability and nothing else.
        with mock.patch.object(gmes_browsers, "_registry_value",
                              return_value="STABLE-GUID"):
            with mock.patch.dict(os.environ, {"USERNAME": "alice",
                                              "USERDOMAIN": "CORP"}):
                one = gmes_browsers.machine_id()
            with mock.patch.dict(os.environ, {"USERNAME": "SYSTEM",
                                              "USERDOMAIN": "NT AUTHORITY"}):
                two = gmes_browsers.machine_id()
        self.assertEqual(one, two)

    def test_it_survives_a_machine_rename(self):
        # MachineGuid is written once at install; the hostname is not.
        with mock.patch.object(gmes_browsers, "_registry_value",
                              return_value="STABLE-GUID"):
            with mock.patch("platform.node", return_value="OLD-NAME"):
                one = gmes_browsers.machine_id()
            with mock.patch("platform.node", return_value="NEW-NAME"):
                two = gmes_browsers.machine_id()
        self.assertEqual(one, two)

    def test_it_falls_back_to_the_hostname_when_the_guid_is_unreadable(self):
        with mock.patch.object(gmes_browsers, "_registry_value", return_value=None), \
             mock.patch("platform.node", return_value="SOME-PC"):
            self.assertTrue(gmes_browsers.machine_id())

    def test_it_does_not_contain_the_machine_identity_in_clear(self):
        # The record is a diagnostic people paste into chats.
        with mock.patch.object(gmes_browsers, "_registry_value",
                              return_value="4f3c1e22-SECRET-GUID"):
            value = gmes_browsers.machine_id()
        self.assertNotIn("SECRET", value)
        self.assertNotIn("4f3c1e22", value)

    def test_the_real_machine_guid_is_actually_readable_here(self):
        # If this fails on a real Windows machine the fallback is silently
        # doing all the work, and the stability the fix was for is not there.
        self.assertIsNotNone(
            gmes_browsers._registry_value(
                gmes_browsers._hklm(), r"SOFTWARE\Microsoft\Cryptography",
                "MachineGuid"),
            "MachineGuid could not be read; machine_id() is falling back to "
            "the hostname, which changes on a rename")


class TestStateFile(TempStateMixin, unittest.TestCase):
    """The record is the single source of truth for "which browser, which
    profile", and is read by every process - including a second terminal
    resolving the same DevToolsActivePort."""

    def test_round_trip(self):
        gmes_browsers.write_state({"completed": True, "browser": "edge"})
        self.assertEqual(gmes_browsers.read_state()["browser"], "edge")

    def test_a_missing_or_corrupt_record_reads_as_empty(self):
        self.assertEqual(gmes_browsers.read_state(), {})
        with open(gmes_browsers.state_path(), "w", encoding="utf-8") as fh:
            fh.write("{ broken")
        self.assertEqual(gmes_browsers.read_state(), {})

    def test_a_non_dict_record_reads_as_empty(self):
        with open(gmes_browsers.state_path(), "w", encoding="utf-8") as fh:
            json.dump(["not", "a", "record"], fh)
        self.assertEqual(gmes_browsers.read_state(), {})

    def test_writing_leaves_no_temp_file_behind(self):
        gmes_browsers.write_state({"completed": True})
        leftovers = [n for n in os.listdir(self.tmp) if ".tmp-" in n]
        self.assertEqual(leftovers, [])

    def _complete(self, profile_dir, machine=None, browser="edge"):
        gmes_browsers.write_state({
            "completed": True, "browser": browser,
            "machine": machine or gmes_browsers.machine_id(),
            "profile_dir": profile_dir})

    def test_a_completed_record_resolves_the_profile_and_browser(self):
        profile = os.path.join(self.tmp, "profiles", "default")
        os.makedirs(profile)
        self._complete(profile)
        self.assertEqual(gmes_browsers.recorded_profile_dir(), profile)
        self.assertEqual(gmes_browsers.recorded_browser(), "edge")

    def test_an_incomplete_record_resolves_to_nothing(self):
        gmes_browsers.write_state({"completed": False, "profile_dir": self.tmp})
        self.assertIsNone(gmes_browsers.recorded_profile_dir())
        self.assertIsNone(gmes_browsers.recorded_browser())

    def test_a_record_from_another_pc_is_refused(self):
        profile = os.path.join(self.tmp, "profiles", "default")
        os.makedirs(profile)
        self._complete(profile, machine="a-different-machine")
        self.assertIsNone(gmes_browsers.recorded_profile_dir())
        self.assertIsNone(gmes_browsers.recorded_browser())

    def test_a_record_naming_a_profile_that_has_gone_resolves_to_nothing(self):
        self._complete(os.path.join(self.tmp, "profiles", "vanished"))
        self.assertIsNone(gmes_browsers.recorded_profile_dir())

    def test_an_unknown_browser_in_the_record_is_not_returned(self):
        profile = os.path.join(self.tmp, "profiles", "default")
        os.makedirs(profile)
        self._complete(profile, browser="netscape")
        self.assertIsNone(gmes_browsers.recorded_browser())

    def test_cdp_common_resolves_the_recorded_profile(self):
        profile = os.path.join(self.tmp, "profiles", "default")
        os.makedirs(profile)
        self._complete(profile)
        self.assertEqual(cdp_common.active_profile_dir(), profile)

    def test_cdp_common_falls_back_to_the_computed_path(self):
        self.assertEqual(cdp_common.active_profile_dir(),
                         cdp_common.automation_profile_dir())


class TestLocalStateNormalisation(unittest.TestCase):
    """The copy has one profile and calls it `Default`; the index it inherited
    described several under other names. Left disagreeing, the browser shows a
    profile picker instead of the page - with --profile-directory=Default on
    the command line, so the symptom looks nothing like a profile problem."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gmes-localstate-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _write(self, payload):
        write_json(os.path.join(self.tmp, "Local State"), payload)

    def _read(self):
        with open(os.path.join(self.tmp, "Local State"), encoding="utf-8") as fh:
            return json.load(fh)

    def test_the_index_is_collapsed_onto_default(self):
        self._write({"profile": {
            "info_cache": {"Profile 2": {"name": "Work"},
                           "Default": {"name": "Personal"}},
            "last_used": "Profile 2",
            "last_active_profiles": ["Profile 2"]}})
        self.assertTrue(gmes_browsers.normalise_local_state(self.tmp))
        profile = self._read()["profile"]
        self.assertEqual(list(profile["info_cache"]), ["Default"])
        self.assertEqual(profile["last_used"], "Default")
        self.assertEqual(profile["last_active_profiles"], ["Default"])

    def test_the_cookie_encryption_key_is_never_touched(self):
        # This is the entire reason the user-data-dir ROOT is copied at all.
        self._write({"os_crypt": {"encrypted_key": "DPAPI-WRAPPED-KEY"},
                     "profile": {"info_cache": {"Default": {"name": "P"}}}})
        gmes_browsers.normalise_local_state(self.tmp)
        self.assertEqual(self._read()["os_crypt"]["encrypted_key"],
                         "DPAPI-WRAPPED-KEY")

    def test_unrelated_settings_survive(self):
        self._write({"browser": {"enabled_labs_experiments": ["x"]},
                     "profile": {"info_cache": {}}})
        gmes_browsers.normalise_local_state(self.tmp)
        self.assertEqual(self._read()["browser"]["enabled_labs_experiments"], ["x"])

    def test_a_missing_or_corrupt_local_state_is_reported_not_raised(self):
        self.assertFalse(gmes_browsers.normalise_local_state(self.tmp))
        with open(os.path.join(self.tmp, "Local State"), "w", encoding="utf-8") as fh:
            fh.write("{ broken")
        self.assertFalse(gmes_browsers.normalise_local_state(self.tmp))

    def test_a_local_state_with_no_profile_section_still_gains_one(self):
        self._write({"os_crypt": {"encrypted_key": "K"}})
        self.assertTrue(gmes_browsers.normalise_local_state(self.tmp))
        self.assertEqual(self._read()["profile"]["last_used"], "Default")


class TestPreferencesMerge(unittest.TestCase):
    """The copied Preferences ARE the user's real configuration - their sites,
    permissions and certificate decisions - and keeping them is most of why
    copying is worth doing. Only the handful of automation keys are forced."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gmes-prefs-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.path = os.path.join(self.tmp, "Default", "Preferences")

    def _apply(self, existing=None):
        if existing is not None:
            write_json(self.path, existing)
        gmes_browsers.apply_automation_preferences(self.tmp, cdp_common._SEED_PREFERENCES)
        with open(self.path, encoding="utf-8") as fh:
            return json.load(fh)

    def test_the_automation_settings_are_applied(self):
        prefs = self._apply({})
        self.assertFalse(prefs["credentials_enable_service"])
        self.assertFalse(prefs["profile"]["password_manager_enabled"])
        self.assertEqual(prefs["profile"]["default_content_setting_values"]["popups"], 1)
        self.assertFalse(prefs["download"]["prompt_for_download"])
        self.assertTrue(prefs["profile"]["exited_cleanly"])

    def test_the_users_own_settings_survive_the_merge(self):
        prefs = self._apply({
            "profile": {"name": "Work", "content_settings": {"exceptions": {"a": 1}}},
            "intl": {"accept_languages": "en-GB,en"},
            "extensions": {"settings": {"abc": {"state": 1}}},
        })
        self.assertEqual(prefs["profile"]["name"], "Work")
        self.assertEqual(prefs["profile"]["content_settings"]["exceptions"], {"a": 1})
        self.assertEqual(prefs["intl"]["accept_languages"], "en-GB,en")
        self.assertEqual(prefs["extensions"]["settings"]["abc"]["state"], 1)
        # and the forced keys still landed inside that same nested dict
        self.assertFalse(prefs["profile"]["password_manager_enabled"])

    def test_a_conflicting_user_setting_is_overridden(self):
        prefs = self._apply({"profile": {"password_manager_enabled": True}})
        self.assertFalse(prefs["profile"]["password_manager_enabled"])

    def test_a_missing_or_corrupt_preferences_file_is_created(self):
        prefs = self._apply(None)
        self.assertFalse(prefs["credentials_enable_service"])
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("{ broken")
        prefs = gmes_browsers.apply_automation_preferences(
            self.tmp, cdp_common._SEED_PREFERENCES)
        self.assertTrue(prefs)


class TestCopyProfile(unittest.TestCase):
    """Two passes: the user-data-dir root one level deep (for `Local State`,
    which carries the cookie key), then the chosen profile recursively without
    its caches."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gmes-copy-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.user_data = os.path.join(self.tmp, "User Data")
        make_profile(self.user_data, "Profile 2")
        write_json(os.path.join(self.user_data, "Local State"),
                   {"os_crypt": {"encrypted_key": "K"}})
        self.dest = os.path.join(self.tmp, "copy")
        self.source = {
            "browser": "edge", "label": "Microsoft Edge",
            "executable": "msedge.exe", "user_data_dir": self.user_data,
            "profile": {"dir": "Profile 2", "name": "Work",
                        "path": os.path.join(self.user_data, "Profile 2")},
        }

    def _fake_robocopy(self, results=None):
        """Stand in for robocopy, but actually produce the files it would, so
        the verification step is exercised rather than mocked away."""
        calls = []
        results = list(results or [])

        def run(args):
            calls.append(args)
            code = results.pop(0) if results else 0
            if code < 8:
                src, dst = args[0], args[1]
                if "/LEV:1" in args:
                    os.makedirs(dst, exist_ok=True)
                    for entry in os.listdir(src):
                        full = os.path.join(src, entry)
                        if os.path.isfile(full):
                            shutil.copy2(full, os.path.join(dst, entry))
                else:
                    shutil.copytree(src, dst, dirs_exist_ok=True)
            return code, ""

        return calls, run

    def test_it_copies_the_root_then_the_profile(self):
        calls, run = self._fake_robocopy()
        with mock.patch.object(gmes_browsers, "_robocopy", side_effect=run):
            gmes_browsers.copy_profile(self.source, self.dest, verbose=False)

        self.assertEqual(len(calls), 2)
        self.assertIn("/LEV:1", calls[0])
        self.assertEqual(os.path.abspath(calls[0][0]), os.path.abspath(self.user_data))
        self.assertIn("/E", calls[1])
        self.assertTrue(calls[1][0].endswith("Profile 2"))

    def test_the_chosen_profile_lands_as_default(self):
        # So --profile-directory=Default stays true whatever it was called.
        _, run = self._fake_robocopy()
        with mock.patch.object(gmes_browsers, "_robocopy", side_effect=run):
            gmes_browsers.copy_profile(self.source, self.dest, verbose=False)
        self.assertTrue(os.path.isdir(os.path.join(self.dest, "Default")))
        self.assertTrue(os.path.isfile(os.path.join(self.dest, "Local State")))

    def test_caches_are_excluded(self):
        calls, run = self._fake_robocopy()
        with mock.patch.object(gmes_browsers, "_robocopy", side_effect=run):
            gmes_browsers.copy_profile(self.source, self.dest, verbose=False)
        self.assertIn("/XD", calls[1])
        for cache in ("Cache", "Code Cache", "GPUCache", "Crashpad"):
            with self.subTest(dir=cache):
                self.assertIn(cache, calls[1])

    def test_a_locked_file_asks_the_user_to_close_that_browser(self):
        _, run = self._fake_robocopy(results=[8])
        with mock.patch.object(gmes_browsers, "_robocopy", side_effect=run):
            with self.assertRaises(gmes_browsers.ProfileLocked) as ctx:
                gmes_browsers.copy_profile(self.source, self.dest, verbose=False)
        message = str(ctx.exception)
        self.assertIn("Microsoft Edge", message)
        self.assertIn("Close", message)
        self.assertIn("GMES_BOOTSTRAP", message)   # the documented way out

    def test_a_fatal_robocopy_error_is_NOT_reported_as_a_lock(self):
        # robocopy 16 is a serious/usage error - a bad destination, access
        # denied, a path that cannot be created. "Close your browser and try
        # again" cannot fix any of those, and sending someone to close a
        # browser that is already closed is a confidently wrong diagnosis.
        _, run = self._fake_robocopy(results=[16])
        with mock.patch.object(gmes_browsers, "_robocopy", side_effect=run):
            with self.assertRaises(gmes_browsers.CopyFailed) as ctx:
                gmes_browsers.copy_profile(self.source, self.dest, verbose=False)
        message = str(ctx.exception)
        self.assertNotIn("Close", message)
        self.assertIn("not a locked file", message)

    def test_a_fatal_error_is_not_a_ProfileLocked_subclass_by_accident(self):
        # They need opposite handling upstream: a lock stops the run and asks a
        # person to fix it, a fatal error falls back to the empty profile.
        self.assertFalse(issubclass(gmes_browsers.CopyFailed,
                                    gmes_browsers.ProfileLocked))
        self.assertFalse(issubclass(gmes_browsers.ProfileLocked,
                                    gmes_browsers.CopyFailed))

    def test_a_hung_robocopy_is_abandoned_rather_than_waited_out_forever(self):
        # An unattended 02:00 job cannot hang for ever on a roaming or
        # OneDrive-backed profile that never answers.
        with mock.patch.object(gmes_browsers.subprocess, "run",
                               side_effect=gmes_browsers.subprocess.TimeoutExpired(
                                   cmd="robocopy", timeout=900)):
            with self.assertRaises(gmes_browsers.CopyFailed) as ctx:
                gmes_browsers.copy_profile(self.source, self.dest, verbose=False)
        self.assertIn("did not finish", str(ctx.exception))

    def test_robocopy_is_given_a_timeout_at_all(self):
        with mock.patch.object(gmes_browsers.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=0, stdout="")
            gmes_browsers._robocopy(["a", "b"])
        self.assertEqual(run.call_args.kwargs.get("timeout"),
                         gmes_browsers.ROBOCOPY_TIMEOUT)

    def test_a_lock_on_the_second_pass_is_reported_the_same_way(self):
        _, run = self._fake_robocopy(results=[0, 8])
        with mock.patch.object(gmes_browsers, "_robocopy", side_effect=run):
            with self.assertRaises(gmes_browsers.ProfileLocked):
                gmes_browsers.copy_profile(self.source, self.dest, verbose=False)

    def test_robocopy_success_codes_below_eight_are_not_failures(self):
        # 1 = files copied, 2 = extra files, 3 = both. All ordinary.
        for code in (0, 1, 2, 3, 7):
            with self.subTest(code=code):
                dest = os.path.join(self.tmp, f"copy-{code}")
                _, run = self._fake_robocopy(results=[code, code])
                with mock.patch.object(gmes_browsers, "_robocopy", side_effect=run):
                    gmes_browsers.copy_profile(self.source, dest, verbose=False)

    def test_a_copy_missing_the_cookie_key_is_refused(self):
        os.unlink(os.path.join(self.user_data, "Local State"))
        _, run = self._fake_robocopy()
        with mock.patch.object(gmes_browsers, "_robocopy", side_effect=run):
            with self.assertRaises(RuntimeError) as ctx:
                gmes_browsers.copy_profile(self.source, self.dest, verbose=False)
        self.assertIn("Local State", str(ctx.exception))

    def test_a_copy_that_arrives_without_cookies_is_refused(self):
        # A source is only ever chosen because it HAS a cookie store, so one
        # that arrives without it lost it in transit.
        shutil.rmtree(os.path.join(self.user_data, "Profile 2", "Network"))
        _, run = self._fake_robocopy()
        with mock.patch.object(gmes_browsers, "_robocopy", side_effect=run):
            with self.assertRaises(RuntimeError) as ctx:
                gmes_browsers.copy_profile(self.source, self.dest, verbose=False)
        self.assertIn("signed out", str(ctx.exception))

    def test_the_saved_password_and_autofill_databases_are_not_copied(self):
        # The session is in the cookies. Copying "Login Data" would put a
        # second copy of the user's passwords on disk for no functional gain,
        # and nothing in this automation ever reads a browser-saved password
        # (CLAUDE.md 2.2 - the Knox credential comes from the DPAPI store).
        calls, run = self._fake_robocopy()
        with mock.patch.object(gmes_browsers, "_robocopy", side_effect=run):
            gmes_browsers.copy_profile(self.source, self.dest, verbose=False)
        self.assertIn("/XF", calls[1])
        for secret in ("Login Data", "Login Data For Account", "Web Data"):
            with self.subTest(file=secret):
                self.assertIn(secret, calls[1])

    def test_the_exclusions_come_after_their_own_switch(self):
        # robocopy consumes arguments until the next switch, so /XD's list
        # must not run into /XF's - that would silently exclude the wrong
        # things and copy the ones meant to be left behind.
        calls, run = self._fake_robocopy()
        with mock.patch.object(gmes_browsers, "_robocopy", side_effect=run):
            gmes_browsers.copy_profile(self.source, self.dest, verbose=False)
        args = calls[1]
        xd, xf = args.index("/XD"), args.index("/XF")
        self.assertLess(xd, xf)
        self.assertEqual(args[xd + 1:xf], gmes_browsers.SKIP_DIRS)
        self.assertEqual(args[xf + 1:], gmes_browsers.SKIP_FILES)

    def test_it_refuses_to_copy_a_profile_into_itself(self):
        inside = os.path.join(self.user_data, "copy-here")
        with mock.patch.object(gmes_browsers, "_robocopy") as robocopy:
            with self.assertRaises(RuntimeError):
                gmes_browsers.copy_profile(self.source, inside, verbose=False)
        robocopy.assert_not_called()


class TestPreferredInstalledBrowser(unittest.TestCase):
    """`preferred_installed_browser()` - which browser to LAUNCH when there is
    nothing to copy from. Distinct from `candidate_sources()`, which requires
    a profile with a session; this only asks whether the browser exists at
    all (HISTORY.md Phase 78)."""

    def test_prefers_the_windows_default_when_it_is_installed(self):
        with mock.patch.object(gmes_browsers, "default_browser_key", return_value="edge"), \
             mock.patch.object(gmes_browsers, "find_executable", return_value=r"C:\x.exe"):
            self.assertEqual(gmes_browsers.preferred_installed_browser(), "edge")

    def test_falls_through_to_whatever_is_actually_installed(self):
        # Default is Chrome, but only Edge is present - the whole point.
        with mock.patch.object(gmes_browsers, "default_browser_key", return_value="chrome"), \
             mock.patch.object(gmes_browsers, "find_executable",
                               side_effect=lambda k: r"C:\edge.exe" if k == "edge" else None):
            self.assertEqual(gmes_browsers.preferred_installed_browser(), "edge")

    def test_gmes_browser_override_wins_if_installed(self):
        with mock.patch.dict(os.environ, {"GMES_BROWSER": "edge"}), \
             mock.patch.object(gmes_browsers, "default_browser_key", return_value="chrome"), \
             mock.patch.object(gmes_browsers, "find_executable",
                               side_effect=lambda k: r"C:\edge.exe" if k == "edge" else None):
            self.assertEqual(gmes_browsers.preferred_installed_browser(), "edge")

    def test_falls_back_to_chrome_only_when_nothing_is_found_at_all(self):
        # Honest failure preserved: find_chrome() will still raise "not
        # found", which is correct when nothing really is installed.
        with mock.patch.object(gmes_browsers, "default_browser_key", return_value=None), \
             mock.patch.object(gmes_browsers, "find_executable", return_value=None):
            self.assertEqual(gmes_browsers.preferred_installed_browser(), "chrome")

    def test_a_discovery_exception_does_not_propagate(self):
        with mock.patch.object(gmes_browsers, "default_browser_key", return_value=None), \
             mock.patch.object(gmes_browsers, "find_executable",
                               side_effect=OSError("registry exploded")):
            self.assertEqual(gmes_browsers.preferred_installed_browser(), "chrome")


class TestEnsureBootstrapped(TempStateMixin, unittest.TestCase):
    """The decision table. Everything here is about doing the copy exactly
    once, and never at all when there is already a profile."""

    def setUp(self):
        super().setUp()
        self.profile_dir = os.path.join(self.tmp, "profiles", "default")
        self.source_data = os.path.join(self.tmp, "src", "User Data")
        make_profile(self.source_data, "Default")
        write_json(os.path.join(self.source_data, "Local State"),
                   {"os_crypt": {"encrypted_key": "K"},
                    "profile": {"info_cache": {"Default": {"name": "Work"}}}})
        self.source = {
            "browser": "edge", "label": "Microsoft Edge",
            "executable": r"C:\fake\msedge.exe", "user_data_dir": self.source_data,
            "profile": {"dir": "Default", "name": "Work",
                        "path": os.path.join(self.source_data, "Default")},
            "is_default_browser": True,
        }

    def _run(self, sources=None, running=False, verbose=False):
        def fake_copy(source, dest, verbose=True):
            shutil.copytree(source["user_data_dir"], dest)

        with mock.patch.object(gmes_browsers, "candidate_sources",
                               return_value=sources if sources is not None else [self.source]), \
             mock.patch.object(gmes_browsers, "is_running", return_value=running), \
             mock.patch.object(gmes_browsers, "copy_profile", side_effect=fake_copy):
            return gmes_browsers.ensure_bootstrapped(
                self.profile_dir, cdp_common._SEED_PREFERENCES, verbose=verbose)

    def test_a_first_run_copies_the_preferred_browsers_profile(self):
        outcome = self._run()
        self.assertEqual(outcome["strategy"], "copied")
        self.assertEqual(outcome["browser"], "edge")
        self.assertTrue(outcome["first_run"])
        self.assertTrue(os.path.isdir(os.path.join(self.profile_dir, "Default")))
        self.assertTrue(os.path.isfile(os.path.join(self.profile_dir, "Local State")))

    def test_the_copy_is_finished_off_before_it_is_used(self):
        self._run()
        with open(os.path.join(self.profile_dir, "Local State"), encoding="utf-8") as fh:
            state = json.load(fh)
        self.assertEqual(state["profile"]["last_used"], "Default")
        self.assertEqual(state["os_crypt"]["encrypted_key"], "K")
        with open(os.path.join(self.profile_dir, "Default", "Preferences"),
                  encoding="utf-8") as fh:
            prefs = json.load(fh)
        self.assertEqual(prefs["profile"]["default_content_setting_values"]["popups"], 1)

    def test_the_second_run_copies_nothing(self):
        self._run()
        with mock.patch.object(gmes_browsers, "copy_profile") as copy, \
             mock.patch.object(gmes_browsers, "candidate_sources") as sources:
            outcome = gmes_browsers.ensure_bootstrapped(
                self.profile_dir, cdp_common._SEED_PREFERENCES, verbose=False)
        copy.assert_not_called()
        sources.assert_not_called()
        self.assertEqual(outcome["strategy"], "recorded")
        self.assertFalse(outcome["first_run"])
        self.assertEqual(outcome["profile_dir"], self.profile_dir)

    def test_an_existing_profile_from_before_this_feature_is_left_alone(self):
        # Backward compatibility: a Phase 73 user already has a tool-built
        # profile and no record. It may hold a hard-won session; replacing it
        # would throw that away, which is the Phase 20 mistake.
        os.makedirs(os.path.join(self.profile_dir, "Default"))
        write_json(os.path.join(self.profile_dir, "Default", "Preferences"),
                   {"session": "earned the hard way"})
        with mock.patch.object(gmes_browsers, "copy_profile") as copy:
            outcome = self._run()
        copy.assert_not_called()
        self.assertEqual(outcome["strategy"], "existing")
        self.assertFalse(outcome["first_run"])
        with open(os.path.join(self.profile_dir, "Default", "Preferences"),
                  encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), {"session": "earned the hard way"})

    def test_an_existing_profile_with_no_recorded_browser_still_installs_correctly(self):
        # A legacy state.json with no "browser" key (or none at all) used to
        # default straight to "chrome" here too - same Edge-only failure as
        # the "fresh" branches, just reached through a different door.
        os.makedirs(os.path.join(self.profile_dir, "Default"))
        write_json(os.path.join(self.profile_dir, "Default", "Preferences"), {})
        with mock.patch.object(gmes_browsers, "find_executable",
                               side_effect=lambda k: (r"C:\fake\msedge.exe"
                                                      if k == "edge" else None)), \
             mock.patch.object(gmes_browsers, "default_browser_key", return_value=None):
            outcome = self._run()
        self.assertEqual(outcome["strategy"], "existing")
        self.assertEqual(outcome["browser"], "edge")

    def test_an_empty_profile_directory_does_not_block_the_copy(self):
        # os.replace onto an existing directory raises on Windows even when it
        # is empty, and an empty profile directory is what an earlier
        # interrupted attempt leaves. Before this was handled the promotion
        # failed, the run fell back to "fresh", and seed_automation_profile()
        # then declined to seed because the directory existed - producing an
        # unseeded profile that looked deliberate.
        os.makedirs(self.profile_dir)
        outcome = self._run()
        self.assertEqual(outcome["strategy"], "copied")
        self.assertTrue(os.path.isfile(os.path.join(self.profile_dir, "Local State")))

    def test_a_resident_browser_process_does_NOT_block_the_copy(self):
        # Measured on a real Windows 11 machine: 12 msedge.exe processes with
        # not one visible window, because Edge's Startup Boost keeps it
        # resident. Gating on the process list would refuse the first run with
        # an instruction the user cannot satisfy - on the browser this feature
        # most exists to support. The lock itself is the evidence, not a proxy
        # for it (CLAUDE.md 3.2).
        outcome = self._run(running=True)
        self.assertEqual(outcome["strategy"], "copied")

    def test_a_genuinely_locked_profile_asks_for_the_browser_to_be_closed(self):
        def locked(source, dest, verbose=True):
            raise gmes_browsers.ProfileLocked(
                gmes_browsers._locked_message(source, ""))

        with mock.patch.object(gmes_browsers, "candidate_sources",
                               return_value=[self.source]), \
             mock.patch.object(gmes_browsers, "copy_profile", side_effect=locked):
            with self.assertRaises(gmes_browsers.ProfileLocked) as ctx:
                gmes_browsers.ensure_bootstrapped(
                    self.profile_dir, cdp_common._SEED_PREFERENCES, verbose=False)
        self.assertIn("Microsoft Edge", str(ctx.exception))
        self.assertFalse(os.path.isdir(self.profile_dir),
                         "a profile was created despite the copy failing")

    def test_the_edge_lock_message_explains_the_background_process(self):
        # Someone who HAS closed every Edge window needs to be told why it is
        # still holding the profile, not left doubting themselves.
        message = gmes_browsers._locked_message(self.source, "")
        self.assertIn("background", message.lower())
        self.assertIn("Startup boost", message)

    def test_a_lock_on_one_browser_still_tries_the_other_first(self):
        # Copying the session the employee actually uses is worth more than
        # failing fast, so a lock is remembered and re-raised only if nothing
        # else works.
        chrome = dict(self.source, browser="chrome", label="Google Chrome")

        def copy(source, dest, verbose=True):
            if source["browser"] == "chrome":
                raise gmes_browsers.ProfileLocked("chrome is open")
            shutil.copytree(source["user_data_dir"], dest)

        with mock.patch.object(gmes_browsers, "candidate_sources",
                               return_value=[chrome, self.source]), \
             mock.patch.object(gmes_browsers, "copy_profile", side_effect=copy):
            outcome = gmes_browsers.ensure_bootstrapped(
                self.profile_dir, cdp_common._SEED_PREFERENCES, verbose=False)
        self.assertEqual(outcome["strategy"], "copied")
        self.assertEqual(outcome["browser"], "edge")

    def test_a_non_lock_copy_failure_falls_back_instead_of_aborting(self):
        # A full disk or a denied path is not something "close your browser"
        # can fix, so it must not be reported that way and must not abort the
        # run - the empty-profile path still works.
        def broken(source, dest, verbose=True):
            raise gmes_browsers.CopyFailed("the disk is full")

        with mock.patch.object(gmes_browsers, "candidate_sources",
                               return_value=[self.source]), \
             mock.patch.object(gmes_browsers, "copy_profile", side_effect=broken):
            outcome = gmes_browsers.ensure_bootstrapped(
                self.profile_dir, cdp_common._SEED_PREFERENCES, verbose=False)
        self.assertEqual(outcome["strategy"], "fresh")

    def test_it_refuses_to_build_inside_a_real_browser_profile(self):
        # The launcher guards this too, but only AFTER this function has swept,
        # created and renamed directories (CLAUDE.md 2.1).
        real = os.path.join(gmes_browsers.real_user_data_dir("chrome"), "Default")
        with mock.patch.object(gmes_browsers, "copy_profile") as copy:
            with self.assertRaises(RuntimeError) as ctx:
                gmes_browsers.ensure_bootstrapped(
                    real, cdp_common._SEED_PREFERENCES, verbose=False)
        copy.assert_not_called()
        self.assertIn("real Chrome profile", str(ctx.exception))

    def test_nothing_to_copy_from_falls_back_to_a_fresh_profile(self):
        outcome = self._run(sources=[])
        self.assertEqual(outcome["strategy"], "fresh")
        self.assertTrue(outcome["first_run"])
        self.assertEqual(outcome["profile_dir"], self.profile_dir)

    def test_the_fresh_fallback_records_a_browser_that_is_actually_installed(self):
        # HISTORY.md Phase 78: every "fresh" outcome used to hardcode
        # "chrome" unconditionally. On a machine with ONLY Edge installed and
        # nothing to copy, that recorded a browser that does not exist -
        # executable_for() trusts the record, calls find_chrome(), and the
        # very next launch fails outright. The one path that is supposed to
        # always work was not, for exactly the audience it exists to serve.
        with mock.patch.object(gmes_browsers, "find_executable",
                               side_effect=lambda k: (r"C:\fake\msedge.exe"
                                                      if k == "edge" else None)), \
             mock.patch.object(gmes_browsers, "default_browser_key", return_value=None):
            outcome = self._run(sources=[])
        self.assertEqual(outcome["strategy"], "fresh")
        self.assertEqual(outcome["browser"], "edge")

    def test_the_fresh_fallback_prefers_the_windows_default_when_both_exist(self):
        with mock.patch.object(gmes_browsers, "find_executable", return_value=r"C:\fake\x.exe"), \
             mock.patch.object(gmes_browsers, "default_browser_key", return_value="edge"):
            outcome = self._run(sources=[])
        self.assertEqual(outcome["browser"], "edge")

    def test_the_opt_out_skips_the_copy_entirely(self):
        with mock.patch.dict(os.environ, {"GMES_BOOTSTRAP": "off"}):
            with mock.patch.object(gmes_browsers, "candidate_sources") as sources:
                outcome = gmes_browsers.ensure_bootstrapped(
                    self.profile_dir, cdp_common._SEED_PREFERENCES, verbose=False)
        sources.assert_not_called()
        self.assertEqual(outcome["strategy"], "fresh")

    def test_the_opt_out_also_records_an_installed_browser_not_chrome_blindly(self):
        with mock.patch.dict(os.environ, {"GMES_BOOTSTRAP": "off"}), \
             mock.patch.object(gmes_browsers, "find_executable",
                               side_effect=lambda k: (r"C:\fake\msedge.exe"
                                                      if k == "edge" else None)), \
             mock.patch.object(gmes_browsers, "default_browser_key", return_value=None):
            outcome = gmes_browsers.ensure_bootstrapped(
                self.profile_dir, cdp_common._SEED_PREFERENCES, verbose=False)
        self.assertEqual(outcome["browser"], "edge")

    def test_every_opt_out_spelling_is_honoured(self):
        for value in ("0", "off", "no", "false", "never", "OFF"):
            with self.subTest(value=value):
                with mock.patch.dict(os.environ, {"GMES_BOOTSTRAP": value}):
                    self.assertTrue(gmes_browsers.bootstrap_disabled())
        for value in ("", "1", "on", "yes"):
            with self.subTest(value=value):
                with mock.patch.dict(os.environ, {"GMES_BOOTSTRAP": value}):
                    self.assertFalse(gmes_browsers.bootstrap_disabled())

    def test_a_failing_source_falls_through_to_the_next_one(self):
        bad = dict(self.source, browser="chrome", label="Google Chrome")

        def copy(source, dest, verbose=True):
            if source["label"] == "Google Chrome":
                raise RuntimeError("that profile is damaged")
            shutil.copytree(source["user_data_dir"], dest)

        with mock.patch.object(gmes_browsers, "candidate_sources",
                               return_value=[bad, self.source]), \
             mock.patch.object(gmes_browsers, "is_running", return_value=False), \
             mock.patch.object(gmes_browsers, "copy_profile", side_effect=copy):
            outcome = gmes_browsers.ensure_bootstrapped(
                self.profile_dir, cdp_common._SEED_PREFERENCES, verbose=False)
        self.assertEqual(outcome["strategy"], "copied")
        self.assertEqual(outcome["browser"], "edge")

    def test_every_source_failing_still_records_an_installed_browser(self):
        def copy(source, dest, verbose=True):
            raise RuntimeError("nope")

        with mock.patch.object(gmes_browsers, "candidate_sources",
                               return_value=[self.source]), \
             mock.patch.object(gmes_browsers, "is_running", return_value=False), \
             mock.patch.object(gmes_browsers, "copy_profile", side_effect=copy), \
             mock.patch.object(gmes_browsers, "find_executable",
                               side_effect=lambda k: (r"C:\fake\msedge.exe"
                                                      if k == "edge" else None)), \
             mock.patch.object(gmes_browsers, "default_browser_key", return_value=None):
            outcome = gmes_browsers.ensure_bootstrapped(
                self.profile_dir, cdp_common._SEED_PREFERENCES, verbose=False)
        self.assertEqual(outcome["strategy"], "fresh")
        self.assertEqual(outcome["browser"], "edge")

    def test_every_source_failing_still_reaches_a_working_fresh_profile(self):
        def copy(source, dest, verbose=True):
            raise RuntimeError("nope")

        with mock.patch.object(gmes_browsers, "candidate_sources",
                               return_value=[self.source]), \
             mock.patch.object(gmes_browsers, "is_running", return_value=False), \
             mock.patch.object(gmes_browsers, "copy_profile", side_effect=copy):
            outcome = gmes_browsers.ensure_bootstrapped(
                self.profile_dir, cdp_common._SEED_PREFERENCES, verbose=False)
        self.assertEqual(outcome["strategy"], "fresh")
        self.assertIn("errors", outcome)

    def test_discovery_blowing_up_is_not_fatal(self):
        with mock.patch.object(gmes_browsers, "candidate_sources",
                               side_effect=OSError("registry exploded")):
            outcome = gmes_browsers.ensure_bootstrapped(
                self.profile_dir, cdp_common._SEED_PREFERENCES, verbose=False)
        self.assertEqual(outcome["strategy"], "fresh")

    def test_a_record_from_another_pc_sets_this_one_up_beside_it(self):
        # Never reuse a profile copied from another machine (its cookies
        # cannot decrypt here), and never delete it either - CLAUDE.md 2.1a
        # requires recovery to be additive.
        foreign = os.path.join(self.tmp, "profiles", "default")
        os.makedirs(os.path.join(foreign, "Default"))
        write_json(os.path.join(foreign, "Default", "Preferences"), {"from": "other PC"})
        gmes_browsers.write_state({
            "completed": True, "browser": "chrome", "machine": "some-other-pc",
            "profile_dir": foreign})

        outcome = self._run()

        self.assertNotEqual(outcome["profile_dir"], foreign)
        self.assertEqual(os.path.basename(outcome["profile_dir"]),
                         gmes_browsers.machine_id())
        self.assertTrue(os.path.isfile(os.path.join(foreign, "Default", "Preferences")),
                        "the other machine's profile was disturbed")

    def test_an_unreadable_copied_local_state_fails_the_copy(self):
        # `_verify_copy()` only proves Local State is PRESENT. A malformed one
        # passed verification, failed normalisation silently (the return value
        # was discarded), and got promoted - leaving the index pointing at a
        # profile directory the copy does not have, which shows a profile
        # picker instead of the page. The suite could not see this because
        # `_run()` replaces copy_profile and never reaches the finishing steps.
        def copy_with_broken_index(source, dest, verbose=True):
            shutil.copytree(source["user_data_dir"], dest)
            with open(os.path.join(dest, "Local State"), "w", encoding="utf-8") as fh:
                fh.write("{ not json")

        with mock.patch.object(gmes_browsers, "candidate_sources",
                               return_value=[self.source]), \
             mock.patch.object(gmes_browsers, "copy_profile",
                               side_effect=copy_with_broken_index):
            outcome = gmes_browsers.ensure_bootstrapped(
                self.profile_dir, cdp_common._SEED_PREFERENCES, verbose=False)

        self.assertEqual(outcome["strategy"], "fresh")
        self.assertFalse(os.path.isdir(self.profile_dir),
                         "a copy with an unusable profile index was promoted")

    def test_the_finishing_steps_really_run_on_a_promoted_copy(self):
        # The counterpart to the test above: prove the normal path actually
        # reaches normalise_local_state() and apply_automation_preferences()
        # rather than having them mocked out of the picture.
        outcome = self._run()
        self.assertEqual(outcome["strategy"], "copied")
        with open(os.path.join(self.profile_dir, "Local State"), encoding="utf-8") as fh:
            index = json.load(fh)["profile"]
        self.assertEqual(list(index["info_cache"]), ["Default"])
        self.assertEqual(index["last_used"], "Default")

    def test_the_record_is_written_so_the_next_run_can_skip_all_of_this(self):
        self._run()
        state = gmes_browsers.read_state()
        self.assertTrue(state["completed"])
        self.assertEqual(state["browser"], "edge")
        self.assertEqual(state["machine"], gmes_browsers.machine_id())
        self.assertEqual(state["profile_dir"], self.profile_dir)
        self.assertEqual(state["source"]["profile"], "Default")

    def test_the_record_never_contains_a_password_or_a_token(self):
        self._run()
        with open(gmes_browsers.state_path(), encoding="utf-8") as fh:
            raw = fh.read().lower()
        for forbidden in ("password", "token", "cookie", "secret"):
            with self.subTest(word=forbidden):
                self.assertNotIn(forbidden, raw)


class TestFailureRecovery(TempStateMixin, unittest.TestCase):
    """An interrupted first run must leave nothing a later run will launch.

    Promotion is a rename that happens only after the copy has been verified,
    so the failure mode of losing power halfway is "first run has not happened
    yet" rather than "a broken profile is now the one being driven"."""

    def setUp(self):
        super().setUp()
        self.profile_dir = os.path.join(self.tmp, "profiles", "default")
        os.makedirs(os.path.dirname(self.profile_dir))
        self.source = {
            "browser": "chrome", "label": "Google Chrome",
            "executable": r"C:\fake\chrome.exe",
            "user_data_dir": os.path.join(self.tmp, "src"),
            "profile": {"dir": "Default", "name": "Personal"},
            "is_default_browser": True,
        }

    def test_a_copy_that_fails_halfway_promotes_nothing(self):
        def half_a_copy(source, dest, verbose=True):
            os.makedirs(os.path.join(dest, "Default"), exist_ok=True)
            raise RuntimeError("the disk filled up")

        with mock.patch.object(gmes_browsers, "candidate_sources",
                               return_value=[self.source]), \
             mock.patch.object(gmes_browsers, "is_running", return_value=False), \
             mock.patch.object(gmes_browsers, "copy_profile", side_effect=half_a_copy):
            outcome = gmes_browsers.ensure_bootstrapped(
                self.profile_dir, cdp_common._SEED_PREFERENCES, verbose=False)

        self.assertEqual(outcome["strategy"], "fresh")
        self.assertFalse(os.path.isdir(self.profile_dir),
                         "a half-finished copy was promoted to the live profile")
        leftovers = [n for n in os.listdir(os.path.dirname(self.profile_dir))
                     if n.startswith(gmes_browsers.STAGING_PREFIX)]
        self.assertEqual(leftovers, [], "a staging directory was left behind")

    def test_a_keyboard_interrupt_mid_copy_also_leaves_nothing(self):
        def interrupted(source, dest, verbose=True):
            os.makedirs(dest, exist_ok=True)
            raise KeyboardInterrupt

        with mock.patch.object(gmes_browsers, "candidate_sources",
                               return_value=[self.source]), \
             mock.patch.object(gmes_browsers, "is_running", return_value=False), \
             mock.patch.object(gmes_browsers, "copy_profile", side_effect=interrupted):
            with self.assertRaises(KeyboardInterrupt):
                gmes_browsers.ensure_bootstrapped(
                    self.profile_dir, cdp_common._SEED_PREFERENCES, verbose=False)

        self.assertFalse(os.path.isdir(self.profile_dir))
        self.assertFalse(gmes_browsers.read_state().get("completed"))

    def test_staging_left_by_an_earlier_interrupted_run_is_swept(self):
        stale = os.path.join(os.path.dirname(self.profile_dir),
                             f"{gmes_browsers.STAGING_PREFIX}9999")
        os.makedirs(os.path.join(stale, "Default"))
        cleared = gmes_browsers.sweep_staging(self.profile_dir, verbose=False)
        self.assertEqual(cleared, 1)
        self.assertFalse(os.path.isdir(stale))

    def test_the_sweeper_only_ever_removes_staging_directories(self):
        # The one rmtree in this project. It must not be able to reach a real
        # profile, an onboarded one, or the protected CDP Profile copy.
        parent = os.path.dirname(self.profile_dir)
        keep = os.path.join(parent, "default")
        os.makedirs(keep)
        gmes_browsers.sweep_staging(self.profile_dir, verbose=False)
        self.assertTrue(os.path.isdir(keep))

        self.assertFalse(gmes_browsers._clear_staging(keep, parent, verbose=False))
        self.assertTrue(os.path.isdir(keep))

    def test_the_sweeper_refuses_a_staging_name_outside_the_profile_parent(self):
        # Fence 2. A GMES_PROFILE_DIR mistake must not let this reach a
        # staging-named directory belonging to something else entirely.
        elsewhere = os.path.join(self.tmp, "somewhere-else",
                                 f"{gmes_browsers.STAGING_PREFIX}1")
        os.makedirs(elsewhere)
        self.assertFalse(gmes_browsers._clear_staging(
            elsewhere, os.path.dirname(self.profile_dir), verbose=False))
        self.assertTrue(os.path.isdir(elsewhere))

    def test_a_staging_directory_that_cannot_be_removed_is_not_reported_cleared(self):
        # rmtree(ignore_errors=True) is silent about failure, and a Chrome
        # profile routinely holds paths past MAX_PATH that robocopy can create
        # and rmtree cannot remove. Claiming success there would be this
        # project's characteristic bug (CLAUDE.md 3.5).
        parent = os.path.dirname(self.profile_dir)
        staging = os.path.join(parent, f"{gmes_browsers.STAGING_PREFIX}7")
        os.makedirs(staging)
        with mock.patch.object(gmes_browsers.shutil, "rmtree"):   # silently does nothing
            self.assertFalse(gmes_browsers._clear_staging(staging, parent, verbose=False))

    def test_sweeping_a_directory_that_does_not_exist_is_harmless(self):
        self.assertEqual(
            gmes_browsers.sweep_staging(os.path.join(self.tmp, "nope", "x"),
                                        verbose=False), 0)

    def test_an_unwritable_record_does_not_fail_the_run(self):
        # Losing the record costs a directory check next time, nothing more.
        with mock.patch.object(gmes_browsers, "write_state",
                               side_effect=OSError("read-only")), \
             mock.patch.object(gmes_browsers, "candidate_sources", return_value=[]):
            outcome = gmes_browsers.ensure_bootstrapped(
                self.profile_dir, cdp_common._SEED_PREFERENCES, verbose=False)
        self.assertEqual(outcome["strategy"], "fresh")


class TestBrowserNeutralLaunch(unittest.TestCase):
    """Edge reaches the same launcher, with the same flags.

    --disable-popup-blocking is the load-bearing one: without it this
    machine's Chrome GPO swallows the AD SSO window outright and sign-in fails
    with nothing to see (HISTORY.md Phase 56.1). An Edge path that quietly
    dropped it would reintroduce that, on a browser nobody had tested."""

    PROFILE = r"C:\fake\automation-profile"

    def setUp(self):
        self._saved = cdp_common.ACTIVE_PORT
        cdp_common.ACTIVE_PORT = None
        self.addCleanup(setattr, cdp_common, "ACTIVE_PORT", self._saved)

        # The two refusal tests below hand the launcher a path that IS a real
        # profile directory. They must not hand it the real one: if the guard
        # ever regresses, the launcher carries on into `_clear_devtools_port()`
        # and the port-wait loop, which would mean an unlink attempt inside the
        # user's own browser profile and a 45-second offline test. Both
        # browsers are redirected at temporary directories so the guard is
        # exercised on something disposable (CLAUDE.md 2.1).
        self.tmp = tempfile.mkdtemp(prefix="gmes-fake-real-profiles-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        fake = {}
        for key, env in (("chrome", "CHROME_USER_DATA_DIR"),
                         ("edge", "EDGE_USER_DATA_DIR")):
            path = os.path.join(self.tmp, key, "User Data")
            os.makedirs(os.path.join(path, "Default"))
            fake[env] = path
        patcher = mock.patch.dict(os.environ, fake)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _launch(self, browser):
        reads = iter([None, (9999, "/devtools/browser/abc")])
        with mock.patch.object(cdp_common, "read_devtools_port",
                               side_effect=lambda _p: next(reads, (9999, ""))), \
             mock.patch.object(cdp_common, "cdp_is_up", return_value=True), \
             mock.patch.object(cdp_common, "_clear_devtools_port"), \
             mock.patch.object(cdp_common, "seed_automation_profile",
                               return_value=(self.PROFILE, True)), \
             mock.patch.object(gmes_browsers, "find_executable",
                               side_effect=lambda k: rf"C:\fake\{k}.exe"), \
             mock.patch.object(cdp_common.subprocess, "Popen") as popen, \
             mock.patch.object(cdp_common.time, "sleep"):
            popen.return_value = mock.Mock()
            cdp_common.launch_automation_chrome(
                profile=self.PROFILE, port=9999, wait_seconds=1,
                verbose=False, browser=browser)
        return popen.call_args.args[0]

    def test_edge_is_launched_when_it_is_the_chosen_browser(self):
        self.assertEqual(self._launch("edge")[0], r"C:\fake\edge.exe")

    def test_chrome_remains_the_default_when_nothing_was_recorded(self):
        with mock.patch.object(gmes_browsers, "recorded_browser", return_value=None):
            self.assertEqual(self._launch(None)[0], r"C:\fake\chrome.exe")

    def test_the_recorded_browser_is_used_when_the_caller_names_none(self):
        with mock.patch.object(gmes_browsers, "recorded_browser", return_value="edge"):
            self.assertEqual(self._launch(None)[0], r"C:\fake\edge.exe")

    def test_edge_gets_every_flag_chrome_gets(self):
        edge, chrome = self._launch("edge"), self._launch("chrome")
        self.assertEqual(edge[1:], chrome[1:],
                         "the two browsers were launched with different flags")

    def test_the_popup_flag_survives_on_the_edge_path(self):
        self.assertEqual(self._launch("edge").count("--disable-popup-blocking"), 1)

    def test_it_refuses_to_launch_against_a_real_edge_profile(self):
        real = gmes_browsers.real_user_data_dir("edge")
        with mock.patch.object(cdp_common, "cdp_is_up", return_value=False), \
             mock.patch.object(cdp_common.subprocess, "Popen") as popen, \
             mock.patch.object(cdp_common.time, "sleep"):
            with self.assertRaises(RuntimeError) as ctx:
                cdp_common.launch_automation_chrome(profile=real, verbose=False,
                                                    browser="edge", wait_seconds=1)
        popen.assert_not_called()
        self.assertIn("real Edge profile", str(ctx.exception))

    def test_it_refuses_a_path_inside_a_real_profile(self):
        inside = os.path.join(gmes_browsers.real_user_data_dir("chrome"), "Default")
        with mock.patch.object(cdp_common, "cdp_is_up", return_value=False), \
             mock.patch.object(cdp_common.subprocess, "Popen") as popen, \
             mock.patch.object(cdp_common.time, "sleep"):
            with self.assertRaises(RuntimeError) as ctx:
                cdp_common.launch_automation_chrome(profile=inside, verbose=False,
                                                    wait_seconds=1)
        popen.assert_not_called()
        self.assertIn("real Chrome profile", str(ctx.exception))

    def test_a_named_profile_never_triggers_the_bootstrap(self):
        # Callers that name a profile already know what they want; only the
        # no-profile call is the first-run entry point.
        with mock.patch.object(gmes_browsers, "ensure_bootstrapped") as boot:
            self._launch("chrome")
        boot.assert_not_called()

    def test_no_profile_runs_the_bootstrap_once(self):
        with mock.patch.object(gmes_browsers, "ensure_bootstrapped",
                               return_value={"profile_dir": self.PROFILE,
                                             "browser": "edge"}) as boot, \
             mock.patch.object(cdp_common, "read_devtools_port",
                               return_value=(9999, "/x")), \
             mock.patch.object(cdp_common, "cdp_is_up", return_value=True):
            cdp_common.launch_automation_chrome(verbose=False)
        boot.assert_called_once()


class TestReadOnlySelfCheck(TempStateMixin, unittest.TestCase):
    """`python gmes_browsers.py` is a diagnostic. It must never write."""

    def setUp(self):
        super().setUp()
        # Point both browsers at temp directories. Without this the report
        # reads the DEVELOPER's real Chrome/Edge `Local State` and enumerates
        # their real profile names - read-only, but real state, and it makes
        # the test's output depend on whose machine it runs on.
        fake = {}
        for key, env in (("chrome", "CHROME_USER_DATA_DIR"),
                         ("edge", "EDGE_USER_DATA_DIR")):
            path = os.path.join(self.tmp, key, "User Data")
            make_profile(path, "Default")
            fake[env] = path
        patcher = mock.patch.dict(os.environ, fake)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_report_runs_and_changes_nothing(self):
        text = gmes_browsers.report()
        self.assertIn("Windows default browser", text)
        self.assertIn("Google Chrome", text)
        self.assertIn("Microsoft Edge", text)
        self.assertFalse(os.path.exists(gmes_browsers.state_path()),
                         "the read-only report wrote a state file")

    def test_it_says_when_a_record_belongs_to_another_pc(self):
        gmes_browsers.write_state({"completed": True, "browser": "edge",
                                   "machine": "elsewhere",
                                   "profile_dir": r"C:\x", "strategy": "copied"})
        self.assertIn("different machine", gmes_browsers.report())


if __name__ == "__main__":
    unittest.main(verbosity=2)

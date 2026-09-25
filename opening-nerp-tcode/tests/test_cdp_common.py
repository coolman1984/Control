"""Offline guards for the shared CDP layer that every G-MES run depends on.

    python tests/test_cdp_common.py

These tests used to live in `tests/test_unit.py`, the N-ERP suite, because
`cdp_common.py` was shared by both systems and N-ERP happened to be the side
that grew a test file first. N-ERP was removed in HISTORY.md Phase 72, and
these particular guards had nothing to do with N-ERP: they cover
`cdp_common.py` behaviour that G-MES uses on every single run, and two of
them exist because of live G-MES incidents specifically.

Migrating them here BEFORE deleting the N-ERP suite is the whole point. The
same mistake in reverse - deleting a test file named after the thing being
removed, without checking what else it was protecting - would have silently
dropped the only automated proof that the AD SSO popup flag is still passed.

What each class protects, and why it is not optional:

  TestUserProfileChromeLaunchArguments
      `--disable-popup-blocking`. Without it this machine's Chrome GPO
      swallows the AD SSO window outright and sign-in fails with nothing to
      see (HISTORY.md Phase 56.1, ported to the legacy launcher in 57.7).
  TestScreenshotTabOverrideIsBackwardCompatible
      the optional `tab=` parameter that lets G-MES name its own tab, so a
      diagnostic screenshot cannot silently photograph a leftover SSO popup
      and be mistaken for evidence (Phase 56.4, ported in 57.8).
  TestProxyBypass
      GMES_SKILL.md gotcha #1 / the corporate gateway. Without this every
      localhost CDP call comes back 403 URLBlocked.
  TestMessageIds
      hand-picked CDP message ids could collide across overlapping helpers
      on one socket, so a caller could read another's reply.
  TestChromeDiscovery
      Chrome is not always in Program Files; a per-user install used to die
      with a bare WinError 2.
  TestPageTabSelection
      `get_page_tab()`, reached live from `gmes_connect.py` (with "gmes")
      and from `capture_screenshot`/`navigate_page` (with None).

No browser is launched anywhere in this file: `subprocess.Popen`, the
port-wait loop, `connect` and `send` are all mocked.
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


# Tab fixtures shaped like the ones these functions actually meet. A foreign
# page target is kept deliberately: the automation drives a COPY of the
# user's own Chrome profile, so an unrelated tab really can be open beside
# G-MES, and "picks the right one out of several" is the property under test.
# tests/test_legacy_hardening.py uses the same technique for the strict
# G-MES screenshot resolver.
GMES_PAGE = {"type": "page", "title": "G-MES",
             "url": "http://seegmes4.sec.samsung.net/mes4/sm/nexacro/index_ext_2318.html",
             "webSocketDebuggerUrl": "ws://gmes"}
SSO_PAGE = {"type": "page", "title": "Sign in",
            "url": ("https://stseu.secsso.net/adfs/ls/?SAMLRequest=abc"
                    "&RelayState=http%3A%2F%2Fseegmes4.sec.samsung.net%2Fmes4%2Fadsso"),
            "webSocketDebuggerUrl": "ws://sso"}
BLANK_PAGE = {"type": "page", "title": "New Tab", "url": "chrome://newtab/",
              "webSocketDebuggerUrl": "ws://newtab"}
AN_IFRAME = {"type": "iframe", "title": "embedded",
             "url": "http://seegmes4.sec.samsung.net/mes4/sm/nexacro/frame.html",
             "webSocketDebuggerUrl": "ws://iframe"}


class ConsoleOutputSurvivesNonUtf8(unittest.TestCase):
    """HISTORY.md - external review of 1957ba9, live-confirmed by a real CI
    failure (run 35091480354): `print(f"It said: {text!r}")` with G-MES's
    own Korean lockout-warning text raised UnicodeEncodeError under a
    legacy console codepage. That is the single most important safety
    message this project ever prints - "attempt 1 of 5 before this account
    locks" - crashing while printing it, rather than after, is worse than
    not checking for it at all."""

    def test_the_guard_reconfigures_both_streams_to_utf8(self):
        import io
        fake_out = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
        fake_err = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
        with mock.patch.object(cdp_common.sys, "stdout", fake_out), \
             mock.patch.object(cdp_common.sys, "stderr", fake_err):
            cdp_common._make_console_output_never_crash()
        self.assertEqual(fake_out.encoding.lower(), "utf-8")
        self.assertEqual(fake_err.encoding.lower(), "utf-8")

    def test_a_stream_with_no_reconfigure_is_skipped_not_crashed(self):
        # A test runner or CI log collector can replace sys.stdout with
        # something that has no .reconfigure() at all (e.g. a bare
        # io.StringIO) - must be a no-op there, never an AttributeError.
        import io
        with mock.patch.object(cdp_common.sys, "stdout", io.StringIO()):
            cdp_common._make_console_output_never_crash()   # must not raise

    def test_reconfigure_raising_is_swallowed_not_fatal(self):
        broken = mock.Mock()
        broken.reconfigure.side_effect = ValueError("already detached")
        with mock.patch.object(cdp_common.sys, "stdout", broken):
            cdp_common._make_console_output_never_crash()   # must not raise

    def test_the_exact_korean_lockout_text_survives_a_cp1252_stream(self):
        import io
        korean = "아이디 또는 비밀번호가 일치하지 않습니다. (시도횟수1/5)"
        buf = io.BytesIO()
        stream = io.TextIOWrapper(buf, encoding="cp1252")
        with mock.patch.object(cdp_common.sys, "stdout", stream):
            cdp_common._make_console_output_never_crash()
            print(repr(korean))   # must not raise UnicodeEncodeError
            stream.flush()
        self.assertIn(korean.encode("utf-8"), buf.getvalue())


class EventListenerIsASeparateConnection(unittest.TestCase):
    """HISTORY.md Phase 82.6: `send()`'s own docstring says it "discards
    the event traffic... that arrives in between" while waiting for a
    command reply - `open_event_listener()` exists specifically so Network
    events are never read on that same socket, confirmed live by tracing a
    real Inquiry click on a dedicated connection."""

    def _fake_ws_module(self, recv_side_effect=()):
        fake_socket = mock.Mock()
        fake_socket.recv = mock.Mock(side_effect=list(recv_side_effect))
        fake_module = mock.Mock()
        fake_module.create_connection = mock.Mock(return_value=fake_socket)
        return fake_module, fake_socket

    def test_it_enables_every_named_domain_and_sets_a_short_recv_timeout(self):
        fake_module, fake_socket = self._fake_ws_module(
            recv_side_effect=[json.dumps({"id": 1, "result": {}}),
                              json.dumps({"id": 2, "result": {}})])
        with mock.patch.object(cdp_common, "_require_websocket", return_value=fake_module):
            listener = cdp_common.open_event_listener(
                "ws://x", domains=("Network", "Page"), recv_timeout=0.25)
        self.assertIs(listener, fake_socket)
        fake_socket.settimeout.assert_called_with(0.25)
        sent_methods = [json.loads(c.args[0])["method"] for c in fake_socket.send.call_args_list]
        self.assertEqual(sent_methods, ["Network.enable", "Page.enable"])

    def test_drain_events_returns_everything_waiting_without_blocking(self):
        events = [{"method": "Network.requestWillBeSent", "params": {}},
                 {"method": "Network.responseReceived", "params": {}}]
        fake_socket = mock.Mock()
        fake_socket.recv = mock.Mock(
            side_effect=[json.dumps(e) for e in events] + [cdp_common.websocket.WebSocketTimeoutException()])
        self.assertEqual(cdp_common.drain_events(fake_socket), events)

    def test_a_malformed_frame_is_skipped_not_raised(self):
        fake_socket = mock.Mock()
        fake_socket.recv = mock.Mock(
            side_effect=["not json", cdp_common.websocket.WebSocketTimeoutException()])
        self.assertEqual(cdp_common.drain_events(fake_socket), [])


class TestPageTabSelection(unittest.TestCase):
    """`get_page_tab()` - which top-level page target gets driven.

    A long-lived CDP session accumulates duplicate and stale page targets, so
    `next(t for t in tabs if t['type'] == 'page')` picks whichever stale one
    happens to be listed first. Both live G-MES call sites are covered here:
    `gmes_connect.py:84` passes "gmes", while `capture_screenshot()` and
    `navigate_page()` both pass None explicitly.
    """

    def test_prefers_a_tab_matching_the_substring(self):
        # gmes_connect.py's real call: several pages open, one is G-MES.
        with mock.patch.object(cdp_common, "get_tabs",
                               return_value=[BLANK_PAGE, GMES_PAGE]):
            picked = cdp_common.get_page_tab(prefer_url_substring="gmes")
        self.assertEqual(picked["webSocketDebuggerUrl"], "ws://gmes")

    def test_the_preference_wins_regardless_of_list_order(self):
        for order in ([GMES_PAGE, BLANK_PAGE], [BLANK_PAGE, GMES_PAGE]):
            with self.subTest(order=[t["title"] for t in order]):
                with mock.patch.object(cdp_common, "get_tabs", return_value=list(order)):
                    picked = cdp_common.get_page_tab(prefer_url_substring="gmes")
                self.assertEqual(picked["webSocketDebuggerUrl"], "ws://gmes")

    def test_falls_back_to_any_page_when_none_match(self):
        with mock.patch.object(cdp_common, "get_tabs", return_value=[BLANK_PAGE]):
            picked = cdp_common.get_page_tab(prefer_url_substring="gmes")
        self.assertEqual(picked["webSocketDebuggerUrl"], "ws://newtab")

    def test_no_preference_takes_the_first_page_target(self):
        # capture_screenshot() and navigate_page() both call it this way.
        with mock.patch.object(cdp_common, "get_tabs",
                               return_value=[BLANK_PAGE, GMES_PAGE]):
            picked = cdp_common.get_page_tab(prefer_url_substring=None)
        self.assertEqual(picked["webSocketDebuggerUrl"], "ws://newtab")

    def test_never_returns_an_iframe_as_the_page_target(self):
        # Page.captureScreenshot fails outright on a non-top-level target, so
        # handing back an iframe here breaks the diagnostic, not just the pick.
        with mock.patch.object(cdp_common, "get_tabs",
                               return_value=[AN_IFRAME, BLANK_PAGE]):
            self.assertEqual(cdp_common.get_page_tab()["type"], "page")

    def test_no_page_targets_at_all_does_not_raise(self):
        with mock.patch.object(cdp_common, "get_tabs", return_value=[AN_IFRAME]):
            self.assertEqual(cdp_common.get_page_tab(), AN_IFRAME)
        with mock.patch.object(cdp_common, "get_tabs", return_value=[]):
            self.assertIsNone(cdp_common.get_page_tab())


class TestProxyBypass(unittest.TestCase):
    """GMES_SKILL.md gotcha #1 - without this every localhost CDP call is
    routed through the corporate gateway and blocked ("403 URLBlocked",
    Skyhigh Secure Web Gateway). urllib reads these variables at call time,
    so setting them in-process is enough, but it must happen before the
    first request - which is why `cdp_common` calls it at import."""

    def test_sets_both_casings(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            cdp_common.apply_proxy_bypass()
            for name in ("NO_PROXY", "no_proxy"):
                self.assertIn("localhost", os.environ[name])
                self.assertIn("127.0.0.1", os.environ[name])

    def test_preserves_an_existing_value(self):
        with mock.patch.dict(os.environ, {"NO_PROXY": "corp.internal"}, clear=True):
            cdp_common.apply_proxy_bypass()
            self.assertIn("corp.internal", os.environ["NO_PROXY"])
            self.assertIn("localhost", os.environ["NO_PROXY"])

    def test_is_idempotent(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            cdp_common.apply_proxy_bypass()
            first = os.environ["NO_PROXY"]
            cdp_common.apply_proxy_bypass()
            self.assertEqual(first, os.environ["NO_PROXY"])


class TestMessageIds(unittest.TestCase):
    """The original code hand-picked ids per call site (2, 10, 11, 30, ...)
    with ad-hoc offsets, which is a latent correlation bug: two overlapping
    helpers on the same socket can reuse an id and read each other's reply."""

    def test_ids_are_unique_and_increasing(self):
        ids = [cdp_common.next_id() for _ in range(200)]
        self.assertEqual(len(set(ids)), 200)
        self.assertEqual(ids, sorted(ids))


class TestChromeDiscovery(unittest.TestCase):
    """Chrome is not always machine-wide in Program Files; a per-user install
    lands in %LOCALAPPDATA% and the original hardcoded path died with a bare
    WinError 2."""

    def test_env_override_wins(self):
        with mock.patch.dict(os.environ, {"CHROME_PATH": __file__}):
            self.assertEqual(cdp_common.find_chrome(), __file__)

    def test_missing_chrome_raises_an_actionable_error(self):
        with mock.patch.dict(os.environ, {"CHROME_PATH": ""}), \
             mock.patch("os.path.isfile", return_value=False), \
             mock.patch("shutil.which", return_value=None), \
             mock.patch.dict(sys.modules, {"winreg": None}):
            with self.assertRaises(RuntimeError) as ctx:
                cdp_common.find_chrome()
        self.assertIn("CHROME_PATH", str(ctx.exception))


class TestUserProfileChromeLaunchArguments(unittest.TestCase):
    """AD SSO opens ADFS via window.open(); without --disable-popup-blocking
    this machine's Chrome GPO silently swallows that popup and the sign-in
    wait times out with nothing to show for it (HISTORY.md Phase 56.1,
    live-proven; ported to this launcher in Phase 57.7). No live browser is
    launched here; subprocess.Popen and the port-wait loop are both mocked.

    `launch_chrome_with_user_profile()` is the ONLY launcher left in the
    project - it drives the protected `CDP Profile` copy (CLAUDE.md 2.1a).
    """

    def test_disable_popup_blocking_is_present_exactly_once(self):
        # cdp_is_up is called once up front (must be False, or the function
        # assumes Chrome is already running and never launches anything) and
        # again inside the wait loop to detect the port coming up; True on
        # that second call ends the loop without a real 45s timeout or a
        # real browser.
        with mock.patch.object(cdp_common, "cdp_is_up", side_effect=[False, True]), \
             mock.patch.object(cdp_common, "clone_user_profile", return_value="C:\\fake\\profile"), \
             mock.patch.object(cdp_common, "find_chrome", return_value="C:\\fake\\chrome.exe"), \
             mock.patch.object(cdp_common.subprocess, "Popen") as popen, \
             mock.patch.object(cdp_common.time, "sleep"):
            popen.return_value = mock.Mock()
            cdp_common.launch_chrome_with_user_profile(port=9999, wait_seconds=1)

        args = popen.call_args.args[0]
        self.assertEqual(
            args.count("--disable-popup-blocking"), 1,
            f"expected the flag exactly once, got: {args}")

    def test_every_previously_required_argument_still_present(self):
        with mock.patch.object(cdp_common, "cdp_is_up", side_effect=[False, True]), \
             mock.patch.object(cdp_common, "clone_user_profile", return_value="C:\\fake\\profile"), \
             mock.patch.object(cdp_common, "find_chrome", return_value="C:\\fake\\chrome.exe"), \
             mock.patch.object(cdp_common.subprocess, "Popen") as popen, \
             mock.patch.object(cdp_common.time, "sleep"):
            popen.return_value = mock.Mock()
            cdp_common.launch_chrome_with_user_profile(port=9999, wait_seconds=1)

        args = popen.call_args.args[0]
        for required in ("--remote-debugging-port=9999",
                         "--user-data-dir=C:\\fake\\profile",
                         "--profile-directory=Default",
                         "--remote-allow-origins=*",
                         "--no-first-run", "--no-default-browser-check"):
            with self.subTest(arg=required):
                self.assertIn(required, args)

    def test_no_session_restore_switch_is_passed_in_any_form(self):
        # HISTORY.md Phase 82.17. Chrome switches are presence-based:
        # `--restore-last-session=false` REQUESTS restore, it does not
        # disable it. The "=false" spelling was in this launcher for months.
        with mock.patch.object(cdp_common, "cdp_is_up", side_effect=[False, True]), \
             mock.patch.object(cdp_common, "clone_user_profile", return_value="C:\\fake\\profile"), \
             mock.patch.object(cdp_common, "find_chrome", return_value="C:\\fake\\chrome.exe"), \
             mock.patch.object(cdp_common.subprocess, "Popen") as popen, \
             mock.patch.object(cdp_common.time, "sleep"):
            popen.return_value = mock.Mock()
            cdp_common.launch_chrome_with_user_profile(port=9999, wait_seconds=1)
        for arg in popen.call_args.args[0]:
            self.assertNotIn("restore-last-session", arg)


class TestScreenshotTabOverrideIsBackwardCompatible(unittest.TestCase):
    """cdp_common.capture_screenshot()/screenshot_on_failure() carry an
    optional `tab` parameter so G-MES can name its own tab correctly
    (HISTORY.md Phase 56.4, ported in 57.8). `gmes_common.capture_screenshot`
    resolves the G-MES host itself and passes it in; the unset case must stay
    exactly what it was for every caller that does not. No browser is
    launched; Page.captureScreenshot itself is mocked via `send`."""

    def test_unset_tab_resolves_through_get_page_tab_exactly_as_before(self):
        fake_tab = {"webSocketDebuggerUrl": "ws://x/1"}
        with mock.patch.object(cdp_common, "get_page_tab", return_value=fake_tab) as gpt, \
             mock.patch.object(cdp_common, "connect"), \
             mock.patch.object(cdp_common, "send",
                               return_value={"result": {"data": ""}}), \
             mock.patch("builtins.open", mock.mock_open()):
            cdp_common.capture_screenshot("out.png", port=1234)

        gpt.assert_called_once_with(prefer_url_substring=None, port=1234)

    def test_a_provided_tab_skips_get_page_tab_entirely(self):
        named_tab = {"webSocketDebuggerUrl": "ws://x/named"}
        with mock.patch.object(cdp_common, "get_page_tab") as gpt, \
             mock.patch.object(cdp_common, "connect") as connect, \
             mock.patch.object(cdp_common, "send",
                               return_value={"result": {"data": ""}}), \
             mock.patch("builtins.open", mock.mock_open()):
            cdp_common.capture_screenshot("out.png", tab=named_tab)

        gpt.assert_not_called()
        connect.assert_called_once_with(named_tab["webSocketDebuggerUrl"],
                                        timeout=20, enable_runtime=False)

    def test_no_tab_available_returns_none_same_as_before(self):
        with mock.patch.object(cdp_common, "get_page_tab", return_value=None):
            result = cdp_common.capture_screenshot("out.png")
        self.assertIsNone(result)

    def test_screenshot_on_failure_passes_tab_through_unchanged_by_default(self):
        with mock.patch.object(cdp_common, "capture_screenshot", return_value=None) as cap:
            cdp_common.screenshot_on_failure("gmes_failure")
        self.assertIsNone(cap.call_args.kwargs["tab"])


class TestAutomationProfileLocation(unittest.TestCase):
    """The profile the tool owns lives beside credentials.dat, so everything
    this tool keeps for a user is under one directory they can delete."""

    def test_default_sits_under_the_automation_root(self):
        with mock.patch.dict(os.environ, {"LOCALAPPDATA": r"C:\fake\Local"}, clear=False):
            with mock.patch.object(cdp_common, "AUTOMATION_ROOT",
                                   r"C:\fake\Local\GMES_Automation"):
                os.environ.pop("GMES_PROFILE_DIR", None)
                path = cdp_common.automation_profile_dir()
        self.assertTrue(path.endswith(os.path.join("profiles", "default")), path)
        self.assertIn("GMES_Automation", path)

    def test_env_override_wins_outright(self):
        with mock.patch.dict(os.environ, {"GMES_PROFILE_DIR": r"C:\somewhere\else"}):
            self.assertEqual(cdp_common.automation_profile_dir(), r"C:\somewhere\else")

    def test_a_named_profile_gets_its_own_directory(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GMES_PROFILE_DIR", None)
            one = cdp_common.automation_profile_dir("worker-1")
            two = cdp_common.automation_profile_dir("worker-2")
        self.assertNotEqual(one, two)


class TestSeedAutomationProfile(unittest.TestCase):
    """Seeding happens once, at creation, and never again.

    Chrome rewrites `Preferences` every time it exits. Re-seeding an existing
    profile would therefore throw away the session, the cookies and everything
    the profile has earned - which is the one thing it exists to keep."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gmes-profile-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.profile = os.path.join(self.tmp, "profile")

    def test_creates_and_seeds_a_new_profile(self):
        path, created = cdp_common.seed_automation_profile(self.profile, verbose=False)
        self.assertTrue(created)
        self.assertEqual(path, self.profile)
        prefs_path = os.path.join(self.profile, "Default", "Preferences")
        self.assertTrue(os.path.isfile(prefs_path))
        with open(prefs_path, encoding="utf-8") as fh:
            prefs = json.load(fh)
        # The settings that have to travel with the tool.
        self.assertFalse(prefs["credentials_enable_service"])
        self.assertFalse(prefs["profile"]["password_manager_enabled"])
        self.assertEqual(prefs["profile"]["default_content_setting_values"]["popups"], 1)
        self.assertFalse(prefs["download"]["prompt_for_download"])
        self.assertTrue(prefs["profile"]["exited_cleanly"])

    def test_an_existing_profile_is_never_reseeded(self):
        cdp_common.seed_automation_profile(self.profile, verbose=False)
        prefs_path = os.path.join(self.profile, "Default", "Preferences")
        # Stand in for everything Chrome writes back on exit.
        with open(prefs_path, "w", encoding="utf-8") as fh:
            json.dump({"session": "belongs to the user, must survive"}, fh)

        path, created = cdp_common.seed_automation_profile(self.profile, verbose=False)

        self.assertFalse(created, "an existing profile was reported as created")
        with open(prefs_path, encoding="utf-8") as fh:
            after = json.load(fh)
        self.assertEqual(
            after, {"session": "belongs to the user, must survive"},
            "re-seeding overwrote a live profile's Preferences")


class TestLaunchAutomationChrome(unittest.TestCase):
    """The launcher that ships. No browser is started anywhere here:
    subprocess.Popen, the port-wait loop and the profile seed are all mocked."""

    def setUp(self):
        # ACTIVE_PORT is module state the launcher sets; leaking it between
        # tests would let one test's port answer another test's question.
        self._saved = cdp_common.ACTIVE_PORT
        cdp_common.ACTIVE_PORT = None
        self.addCleanup(setattr, cdp_common, "ACTIVE_PORT", self._saved)

    def _launch(self, profile=r"C:\fake\automation-profile", port=9999):
        # read_devtools_port is asked twice: once for "is one already running"
        # (None - nothing is), then inside the wait loop once Chrome has
        # "started" and written the file.
        reads = iter([None, (port, "/devtools/browser/abc")])
        with mock.patch.object(cdp_common, "read_devtools_port",
                               side_effect=lambda _p: next(reads, (port, ""))), \
             mock.patch.object(cdp_common, "cdp_is_up", return_value=True), \
             mock.patch.object(cdp_common, "_clear_devtools_port"), \
             mock.patch.object(cdp_common, "seed_automation_profile",
                               return_value=(profile, True)), \
             mock.patch.object(cdp_common, "find_chrome", return_value="C:\\fake\\chrome.exe"), \
             mock.patch.object(cdp_common.subprocess, "Popen") as popen, \
             mock.patch.object(cdp_common.time, "sleep"):
            popen.return_value = mock.Mock()
            cdp_common.launch_automation_chrome(profile=profile, port=port,
                                                wait_seconds=1, verbose=False)
        return popen.call_args.args[0]

    def test_disable_popup_blocking_is_present_exactly_once(self):
        # Same guard as the copied-profile launcher, for the same live reason
        # (Phase 56.1): without it the AD SSO window is swallowed silently.
        self.assertEqual(self._launch().count("--disable-popup-blocking"), 1)

    def test_it_drives_the_profile_it_was_given(self):
        args = self._launch(r"C:\fake\some-profile")
        self.assertIn(r"--user-data-dir=C:\fake\some-profile", args)
        self.assertIn("--profile-directory=Default", args)

    def test_every_required_argument_is_present(self):
        args = self._launch()
        for required in ("--remote-debugging-port=9999",
                         "--remote-allow-origins=*",
                         "--no-first-run", "--no-default-browser-check",
                         "--disable-background-networking",
                         "--disable-component-update", "--disable-sync"):
            with self.subTest(arg=required):
                self.assertIn(required, args)

    def test_no_session_restore_switch_is_passed_in_any_form(self):
        # HISTORY.md Phase 82.17, measured live and then proven by removal:
        # Chrome switches are presence-based, so `--restore-last-session=false`
        # REQUESTS a restore instead of disabling one. Every launch reopened
        # every G-MES tab from every earlier run (5 pages after a few
        # launches; exactly 1 once the flag was gone), each a live Nexacro
        # application handshaking the same account - and G-MES allows one
        # session per account, which is what its "Currently being used by
        # another PC" popup reports. Checked as a substring so no spelling of
        # it - with or without "=false" - can come back.
        for arg in self._launch():
            with self.subTest(arg=arg):
                self.assertNotIn("restore-last-session", arg)

    def test_the_shared_flag_list_cannot_carry_it_either(self):
        for flag in cdp_common._COMMON_CHROME_FLAGS:
            with self.subTest(flag=flag):
                self.assertNotIn("restore-last-session", flag)


class TestDevToolsActivePort(unittest.TestCase):
    """Reading back the port Chrome was actually given.

    A fixed port is one browser; a port RANGE has a documented pick-then-bind
    race (SeleniumHQ/selenium #8794, #12585). Asking for port 0 and reading
    what the OS assigned has neither problem - as long as the file is treated
    as a claim to verify, not as the answer."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gmes-port-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self._saved = cdp_common.ACTIVE_PORT
        cdp_common.ACTIVE_PORT = None
        self.addCleanup(setattr, cdp_common, "ACTIVE_PORT", self._saved)

    def _write(self, text):
        with open(os.path.join(self.tmp, cdp_common.DEVTOOLS_PORT_FILE),
                  "w", encoding="utf-8") as fh:
            fh.write(text)

    def test_reads_the_port_and_the_browser_path(self):
        self._write("51734\n/devtools/browser/9f2a\n")
        self.assertEqual(cdp_common.read_devtools_port(self.tmp),
                         (51734, "/devtools/browser/9f2a"))

    def test_a_missing_file_is_none_not_an_error(self):
        self.assertIsNone(cdp_common.read_devtools_port(self.tmp))

    def test_a_malformed_file_is_none_not_a_crash(self):
        for junk in ("", "not-a-port\n", "\n\n"):
            with self.subTest(content=junk):
                self._write(junk)
                self.assertIsNone(cdp_common.read_devtools_port(self.tmp))

    def test_a_port_with_no_browser_path_still_reads(self):
        self._write("51734")
        self.assertEqual(cdp_common.read_devtools_port(self.tmp), (51734, ""))

    def test_clearing_removes_the_file_and_tolerates_it_being_absent(self):
        self._write("51734\n/devtools/browser/x\n")
        cdp_common._clear_devtools_port(self.tmp)
        self.assertIsNone(cdp_common.read_devtools_port(self.tmp))
        cdp_common._clear_devtools_port(self.tmp)   # must not raise


class TestActivePortResolution(unittest.TestCase):
    """`active_port()` is how a SEPARATE process finds the browser this one
    started, now that the number is no longer a constant everybody knows."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gmes-active-port-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self._saved = cdp_common.ACTIVE_PORT
        cdp_common.ACTIVE_PORT = None
        self.addCleanup(setattr, cdp_common, "ACTIVE_PORT", self._saved)

    def test_a_resolved_port_wins(self):
        cdp_common.ACTIVE_PORT = 40001
        self.assertEqual(cdp_common.active_port(self.tmp), 40001)

    def test_it_recovers_the_port_from_the_profile(self):
        with open(os.path.join(self.tmp, cdp_common.DEVTOOLS_PORT_FILE),
                  "w", encoding="utf-8") as fh:
            fh.write("51734\n/devtools/browser/x\n")
        self.assertEqual(cdp_common.active_port(self.tmp), 51734)
        # and caches it, so this is not a file read per CDP call
        self.assertEqual(cdp_common.ACTIVE_PORT, 51734)

    def test_it_falls_back_to_the_historical_default(self):
        self.assertEqual(cdp_common.active_port(self.tmp), cdp_common.CDP_PORT)


class TestLaunchPortSelection(unittest.TestCase):
    """Which port the launcher ASKS Chrome for, and what it does with the
    answer."""

    PROFILE = r"C:\fake\automation-profile"

    def setUp(self):
        self._saved = cdp_common.ACTIVE_PORT
        cdp_common.ACTIVE_PORT = None
        self.addCleanup(setattr, cdp_common, "ACTIVE_PORT", self._saved)

    def _run(self, port_arg, env_port, reads, up=True):
        # Sticky, because the real file is: once Chrome writes
        # DevToolsActivePort it stays there, including after Chrome exits.
        # That stickiness is exactly what makes a stale file dangerous.
        it, last = iter(reads), [None]

        def read(_profile):
            try:
                last[0] = next(it)
            except StopIteration:
                pass
            return last[0]

        with mock.patch.object(cdp_common, "_PORT_FROM_ENV", env_port), \
             mock.patch.object(cdp_common, "read_devtools_port", side_effect=read), \
             mock.patch.object(cdp_common, "cdp_is_up", return_value=up), \
             mock.patch.object(cdp_common, "_clear_devtools_port") as clear, \
             mock.patch.object(cdp_common, "seed_automation_profile",
                               return_value=(self.PROFILE, True)), \
             mock.patch.object(cdp_common, "find_chrome", return_value="C:\\fake\\chrome.exe"), \
             mock.patch.object(cdp_common.subprocess, "Popen") as popen, \
             mock.patch.object(cdp_common.time, "sleep"):
            popen.return_value = mock.Mock()
            try:
                cdp_common.launch_automation_chrome(
                    profile=self.PROFILE, port=port_arg, wait_seconds=1, verbose=False)
                error = None
            except RuntimeError as e:
                error = e
        args = popen.call_args.args[0] if popen.call_args else []
        return args, clear, error

    def test_by_default_it_asks_the_os_for_a_port(self):
        args, _, err = self._run(None, None, [None, (51734, "/x")])
        self.assertIsNone(err)
        self.assertIn("--remote-debugging-port=0", args)
        self.assertEqual(cdp_common.ACTIVE_PORT, 51734,
                         "the OS-assigned port was not recorded")

    def test_an_explicit_env_port_is_honoured(self):
        args, _, err = self._run(None, "9444", [None, (9444, "/x")])
        self.assertIsNone(err)
        self.assertIn("--remote-debugging-port=9444", args)

    def test_an_explicit_argument_beats_the_environment(self):
        args, _, err = self._run(7001, "9444", [None, (7001, "/x")])
        self.assertIsNone(err)
        self.assertIn("--remote-debugging-port=7001", args)

    def test_a_stale_port_file_is_cleared_before_launching(self):
        # Left in place it would be read as the new browser's port, and the
        # wait loop would spend its whole timeout asking about the wrong one.
        _, clear, _ = self._run(None, None, [None, (51734, "/x")])
        clear.assert_called_once_with(self.PROFILE)

    def test_a_recorded_port_that_answers_nothing_is_reported_as_such(self):
        # The file exists, so Chrome started - but nothing is listening.
        # That is a different failure from "Chrome never started" and the
        # message has to say which.
        _, _, err = self._run(None, None, [None, None, (51734, "/x")], up=False)
        self.assertIsNotNone(err)
        self.assertIn("51734", str(err))
        self.assertIn("nothing is answering", str(err))

    def test_no_port_file_at_all_is_a_different_message(self):
        _, _, err = self._run(None, None, [None], up=False)
        self.assertIsNotNone(err)
        self.assertIn(cdp_common.DEVTOOLS_PORT_FILE, str(err))

    def test_a_browser_already_serving_this_profile_is_reused(self):
        with mock.patch.object(cdp_common, "read_devtools_port",
                               return_value=(51734, "/x")), \
             mock.patch.object(cdp_common, "cdp_is_up", return_value=True), \
             mock.patch.object(cdp_common.subprocess, "Popen") as popen:
            result = cdp_common.launch_automation_chrome(
                profile=self.PROFILE, verbose=False)
        popen.assert_not_called()
        self.assertIsNone(result)
        self.assertEqual(cdp_common.ACTIVE_PORT, 51734)


class BrowserGoneMidRun(unittest.TestCase):
    """HISTORY.md Phase 84.4, live-tested by closing the automation browser 24 s
    into a run: the run ended at once, but with `[WinError 10053] An established
    connection was aborted by the software in your host machine` - which reads as a
    network fault and names nothing to do."""

    class Socket:
        def __init__(self, on_send=None, on_recv=None):
            self.on_send, self.on_recv = on_send, on_recv
        def send(self, text):
            if self.on_send:
                raise self.on_send
        def recv(self):
            if self.on_recv:
                raise self.on_recv
            return "{}"

    def test_a_dropped_connection_becomes_a_message_that_says_what_happened(self):
        for exc in (ConnectionAbortedError(10053, "aborted"), ConnectionResetError(),
                    BrokenPipeError()):
            for where in ("send", "recv"):
                with self.subTest(exc=type(exc).__name__, where=where):
                    ws = self.Socket(**{f"on_{where}": exc})
                    with self.assertRaises(cdp_common.BrowserGone) as cm:
                        cdp_common.send(ws, "Runtime.evaluate", msg_id=901)
                    text = str(cm.exception)
                    self.assertIn("connection to the automation browser was lost", text)
                    self.assertIn("closed, crashed or was replaced", text)          # the causes are listed, not asserted
                    self.assertNotIn("closed or crashed while", text)              # a live check showed the browser can be fine
                    self.assertIn("Runtime.evaluate", text)
                    self.assertNotIn("WinError", text)

    def test_a_closed_websocket_is_the_same_message(self):
        ws = self.Socket(on_recv=cdp_common.websocket.WebSocketConnectionClosedException("closed"))
        with self.assertRaises(cdp_common.BrowserGone):
            cdp_common.send(ws, "Page.navigate", msg_id=902)

    def test_it_is_still_a_connection_error_so_existing_handlers_keep_working(self):
        self.assertTrue(issubclass(cdp_common.BrowserGone, ConnectionError))
        self.assertTrue(issubclass(cdp_common.BrowserGone, OSError))

    def test_the_marker_batch_uses_to_recognise_it_is_in_the_message(self):
        err = str(cdp_common.BrowserGone(f"{cdp_common.BROWSER_GONE_TEXT[0].upper()}"
                                         f"{cdp_common.BROWSER_GONE_TEXT[1:]} (x)."))
        self.assertIn(cdp_common.BROWSER_GONE_TEXT, err.lower())

    def test_an_ordinary_timeout_is_still_a_timeout_not_a_dead_browser(self):
        class Silent(self.Socket):
            def recv(self):
                raise cdp_common.websocket.WebSocketTimeoutException()
        with self.assertRaises(TimeoutError):
            cdp_common.send(Silent(), "Runtime.evaluate", msg_id=903, timeout=0.05)

    def test_a_normal_reply_is_returned(self):
        ws = mock.Mock()
        ws.recv.return_value = json.dumps({"id": 7, "result": {"ok": 1}})
        self.assertEqual(cdp_common.send(ws, "X", msg_id=7)["result"], {"ok": 1})

    def test_a_protocol_error_names_its_cause_instead_of_no_value(self):
        # HISTORY.md Phase 91.2: CDP answers {"error": {...}} with no
        # "result" when the page navigates mid-call; evaluate() used to
        # report that as "returned no value" and drop the real reason.
        with mock.patch.object(cdp_common, "send", return_value={
                "id": 1, "error": {"code": -32000,
                                   "message": "Execution context was destroyed."}}):
            with self.assertRaises(RuntimeError) as cm:
                cdp_common.evaluate(mock.Mock(), "1")
        self.assertIn("Execution context was destroyed", str(cm.exception))
        self.assertNotIn("no value", str(cm.exception))


class AbandonedLaunchIsNotLeftRunning(unittest.TestCase):
    """HISTORY.md Phase 84.5, live-tested with a port that accepts connections and
    never answers: the tool gave up after 100 s with a good message but left TEN
    automation-browser processes behind. A launch that never became usable must
    end the process this call started (and only that one)."""

    def test_a_running_child_is_terminated(self):
        proc = mock.Mock()
        proc.poll.side_effect = [None, 0]
        self.assertTrue(cdp_common._abandon_launch(proc))
        proc.terminate.assert_called_once()

    def test_a_child_that_ignores_terminate_is_killed(self):
        proc = mock.Mock()
        proc.wait.side_effect = [cdp_common.subprocess.TimeoutExpired("x", 1), None]
        proc.poll.side_effect = [None, 0]
        self.assertTrue(cdp_common._abandon_launch(proc))
        proc.kill.assert_called_once()

    def test_a_child_that_already_exited_is_left_alone(self):
        proc = mock.Mock()
        proc.poll.return_value = 0                 # it handed off to a running instance
        self.assertTrue(cdp_common._abandon_launch(proc))
        proc.terminate.assert_not_called()

    def test_nothing_launched_is_fine(self):
        self.assertTrue(cdp_common._abandon_launch(None))

    def test_it_never_raises_on_the_failure_path(self):
        proc = mock.Mock()
        proc.poll.side_effect = OSError("gone")
        self.assertFalse(cdp_common._abandon_launch(proc))

    def test_both_failure_branches_of_the_launch_end_the_child_first(self):
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                               "cdp_common.py"), encoding="utf-8") as fh:
            src = fh.read()
        launch = src[src.index("def launch_automation_chrome"):src.index("def _abandon_launch")]
        self.assertLess(launch.index("_abandon_launch(LAST_CHROME_PROCESS)"),
                        launch.index("recorded port"))
        self.assertLess(launch.index("_abandon_launch(LAST_CHROME_PROCESS)"),
                        launch.index("never reported a debugging port"))


class StopIfStartedHereClosesGracefully(unittest.TestCase):
    """Every one-off entrance (gmes_report.py, gmes_batch.py,
    gmes_daily_prodplan.py, gmes_demo.py) used to end its own browser with
    LAST_CHROME_PROCESS.terminate() - a raw process kill, not the graceful
    Browser.close two lines above it in this same module (CLAUDE.md 2.6 already
    says the browser is closed through its own CDP endpoint). A raw kill also
    denies Chrome the chance to write a normal exit into its own Preferences -
    plausibly why exit_type ends up "Crashed", which is what triggers Chrome's
    own crash-session-restore and reopens a stale tab on the next launch."""

    def setUp(self):
        cdp_common.LAST_CHROME_PROCESS = None

    def tearDown(self):
        cdp_common.LAST_CHROME_PROCESS = None

    def test_nothing_to_close_is_fine(self):
        self.assertTrue(cdp_common.stop_if_started_here())

    def test_keep_open_leaves_it_running_and_untouched(self):
        cdp_common.LAST_CHROME_PROCESS = mock.Mock()
        with mock.patch.object(cdp_common, "close_browser") as closer:
            self.assertTrue(cdp_common.stop_if_started_here(keep_open=True))
        closer.assert_not_called()
        self.assertIsNotNone(cdp_common.LAST_CHROME_PROCESS)

    def test_a_browser_this_process_started_is_closed_through_cdp_not_killed(self):
        proc = mock.Mock()
        cdp_common.LAST_CHROME_PROCESS = proc
        with mock.patch.object(cdp_common, "close_browser", return_value=True) as closer:
            self.assertTrue(cdp_common.stop_if_started_here(keep_open=False))
        closer.assert_called_once_with()
        proc.terminate.assert_not_called()
        proc.kill.assert_not_called()
        self.assertIsNone(cdp_common.LAST_CHROME_PROCESS)

    def test_an_unconfirmed_close_is_reported_not_hidden(self):
        cdp_common.LAST_CHROME_PROCESS = mock.Mock()
        with mock.patch.object(cdp_common, "close_browser", return_value=False):
            self.assertFalse(cdp_common.stop_if_started_here(keep_open=False))


class ProfileOverrideIsNotSilentlyIgnored(unittest.TestCase):
    """HISTORY.md Phase 84.10: `GMES_PROFILE_DIR` alone does not redirect a machine
    that has already run once - the recorded profile wins - and nothing said so. A
    clean-machine rehearsal believed it was on a fresh profile and drove the real
    one."""

    def setUp(self):
        cdp_common._OVERRIDE_WARNED = False
        self.addCleanup(setattr, cdp_common, "_OVERRIDE_WARNED", False)

    def active(self, recorded, override):
        env = {"GMES_PROFILE_DIR": override} if override is not None else {}
        with mock.patch.dict(os.environ, env, clear=False), \
                mock.patch.object(cdp_common.gmes_browsers, "recorded_profile_dir", return_value=recorded), \
                mock.patch("builtins.print") as printed:
            if override is None:
                os.environ.pop("GMES_PROFILE_DIR", None)
            result = cdp_common.active_profile_dir()
        return result, [str(c.args[0]) for c in printed.call_args_list if c.args]

    def test_a_different_override_is_warned_about_and_the_recorded_profile_is_still_used(self):
        result, said = self.active("C:\\A\\default", "C:\\B\\fresh")
        self.assertEqual(result, "C:\\A\\default")
        self.assertEqual(len(said), 1)
        self.assertIn("GMES_BROWSER_STATE", said[0])
        self.assertIn("C:\\B\\fresh", said[0])

    def test_it_says_so_once_not_on_every_call(self):
        self.active("C:\\A\\default", "C:\\B\\fresh")
        _, said = self.active("C:\\A\\default", "C:\\B\\fresh")
        self.assertEqual(said, [])

    def test_the_same_directory_written_differently_is_not_a_conflict(self):
        _, said = self.active("C:\\A\\Default", "c:\\a\\default")
        self.assertEqual(said, [])

    def test_no_override_and_no_record_say_nothing(self):
        self.assertEqual(self.active("C:\\A\\default", None)[1], [])
        result, said = self.active(None, "C:\\B\\fresh")
        self.assertEqual((result, said), ("C:\\B\\fresh", []))       # nothing recorded: the override IS the profile

    def test_a_rehearsal_with_both_variables_is_not_warned(self):
        # with a fresh state file nothing is recorded, so the override IS the profile
        result, said = self.active(None, "C:\\B\\fresh")
        self.assertEqual(said, [])

    def test_the_message_says_never_to_point_at_a_real_browser_profile(self):
        _, said = self.active("C:\\A\\default", "C:\\B\\fresh")
        self.assertIn("never point either at your own browser", said[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)

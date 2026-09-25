"""
The Chrome DevTools Protocol (CDP) transport layer, shared by every G-MES
tool in this project.

Everything here exists because of a concrete failure observed against a real
corporate system. The short version:

  * The corporate proxy swallows localhost traffic unless NO_PROXY is set,
    so every local CDP call comes back "403 URLBlocked" without it.
  * Chrome 136+ silently IGNORES --remote-debugging-port when --user-data-dir
    is the real profile directory, and rejects the DevTools websocket
    entirely without --remote-allow-origins=*. Hence the profile copy
    (GMES_SKILL.md gotcha #1) and the flag.
  * element.click() is ignored by Nexacro's controls - they are <div>s wired
    to mousedown/mouseup with no click handler to invoke - so clicks must be
    real Input.dispatchMouseEvent sequences.
  * A websocket held open and idle across a page load goes stale; the next
    recv() times out. Close it, wait with no socket open, reconnect fresh.
  * Nothing about render timing is predictable, so every wait polls until the
    thing is actually there, with a generous safety cap - never a tuned fixed
    sleep (CLAUDE.md 3.1).

This file was shared with N-ERP until HISTORY.md Phase 72. Its N-ERP-only
half - the SAP WebGUI iframe resolver, the selection-screen readiness poll,
the busy-indicator wait, the text/title element finders and the throwaway-
profile launcher - was removed there, verified unused by any G-MES file
first. `tests/test_cdp_common.py` guards what remains.
"""
import base64
import itertools
import json
import os
import subprocess
import sys
import time
import urllib.request

# Browser discovery, profile enumeration and the first-run bootstrap live in
# their own flat module because they are about BROWSERS, not about the CDP
# transport this file is. The dependency runs one way only - gmes_browsers
# imports nothing from here - so there is one implementation of "where is
# Chrome" and one of "where is Edge", not a Chrome path with an Edge path
# grown beside it (HISTORY.md Phase 75).
import gmes_browsers


# --------------------------------------------------------------------------
# Windows console output must never crash on G-MES's own text
# --------------------------------------------------------------------------
#
# Confirmed live and by a real CI failure: `print(f"  It said: {text!r}")`
# with G-MES's own Korean lockout-warning text raised UnicodeEncodeError
# under a legacy console codepage (cp1252) - reproduced locally by forcing
# stdout to cp1252 and printing the exact lockout string this project's own
# tests use as a fixture. The crash lands on the single most important
# safety message this project prints - "attempt 1 of 5 before this account
# locks" - so it must never be what silently disappears. Every G-MES script
# imports this module (directly or via gmes_common), so reconfiguring here
# covers every entry point without editing each one individually.
# `sys.stdout`/`stderr` are guarded with `hasattr` because a test runner or
# CI log collector can replace either with an object that has no
# `.reconfigure()` at all - a no-op there, not a crash.
def _make_console_output_never_crash():
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


_make_console_output_never_crash()


# --------------------------------------------------------------------------
# Configuration (single source of truth - do not re-declare these elsewhere)
# --------------------------------------------------------------------------

# The env var keeps its historical name. It is the live knob for this
# engine - renaming it would break any machine or scheduled task that
# already sets it, for a cosmetic gain - so it is recorded here rather than
# changed (HISTORY.md Phase 72.4). Accepting a GMES_-prefixed alias
# alongside it would be additive and safe, if that is ever wanted.
CDP_PORT = int(os.environ.get("NERP_CDP_PORT", "9444"))

# Always address the DevTools endpoint by its IPv4 literal, never by name.
# On Windows "localhost" resolves to ::1 first; Chrome listens on IPv4 only,
# so every call waits for the IPv6 attempt to fail before falling back.
# Measured: 2.05s per /json/list via "localhost" against 0.013s via
# 127.0.0.1 - a 150x difference paid by every target lookup and every
# websocket connect, which is most of what these tools do.
CDP_HOST = "127.0.0.1"

# Set only when somebody explicitly asks for a fixed port. Left unset, the
# launcher asks Chrome for port 0 and the OS hands back a free one - see
# `read_devtools_port()` for why that is better than picking a number.
_PORT_FROM_ENV = os.environ.get("NERP_CDP_PORT")

# The port a running browser was actually given. Resolved through
# `active_port()`, which can also recover it from the profile directory, so a
# SEPARATE process (gmes_data.py, gmes_inspect.py, a second terminal) can
# still find the browser this one started.
ACTIVE_PORT = None

_PROXY_BYPASS = "localhost,127.0.0.1,::1"


def apply_proxy_bypass():
    """HTTP_PROXY/HTTPS_PROXY point at a corporate gateway with no localhost
    exception, so even http://localhost:9444 is routed through it and blocked
    ("403 URLBlocked", Skyhigh Secure Web Gateway). urllib reads these env
    vars at call time, so setting them in this process is enough - but it
    must happen before the first request.

    Note this covers OUR calls to the CDP endpoint only. Chrome's own page
    requests still go through the corporate proxy, which is what the real
    G-MES portal needs.

    Idempotent; every entry point calls it at import time."""
    for name in ("NO_PROXY", "no_proxy"):
        current = os.environ.get(name, "")
        if all(host in current for host in ("localhost", "127.0.0.1")):
            continue
        os.environ[name] = _PROXY_BYPASS if not current else f"{current},{_PROXY_BYPASS}"


apply_proxy_bypass()


try:
    import websocket  # websocket-client
except ImportError:  # offline diagnostics and unit tests do not need CDP
    websocket = None


def _require_websocket():
    if websocket is None:
        raise RuntimeError(
            "websocket-client is not installed. Install it with: "
            f"{sys.executable} -m pip install websocket-client")
    return websocket


# --------------------------------------------------------------------------
# Chrome discovery / launch
# --------------------------------------------------------------------------

def find_chrome():
    """Locate chrome.exe without hardcoding one install path.

    The original code assumed "C:\\Program Files\\Google\\Chrome\\
    Application\\chrome.exe", which is only correct for a machine-wide
    install; a per-user install lands in %LOCALAPPDATA% instead and the
    script would die with a bare WinError 2.

    The search itself now lives in `gmes_browsers.find_executable()`, which
    does the same thing for Edge. This wrapper is kept because it is the name
    every caller and test already uses, and because "no Chrome at all" is a
    hard error here while it is merely "not a candidate" there."""
    path = gmes_browsers.find_executable("chrome")
    if path:
        return path
    raise RuntimeError(
        "Could not find chrome.exe. Set the CHROME_PATH environment variable "
        "to its full path and retry.")


def find_browser(key):
    """The executable for a supported browser, or an actionable error."""
    path = gmes_browsers.find_executable(key)
    if path:
        return path
    info = gmes_browsers.spec(key)
    raise gmes_browsers.BrowserNotFound(
        f"Could not find {info['exe']}. Set the {info['path_env']} environment "
        "variable to its full path and retry.")


def executable_for(browser=None):
    """Which browser binary to launch.

    In order: what the caller named, what first-run recorded, then Chrome -
    the historical default, so a machine that has never run the bootstrap
    behaves exactly as it did before this existed."""
    key = browser or gmes_browsers.recorded_browser() or "chrome"
    if key == "chrome":
        # Deliberately through find_chrome(), not find_browser(): it is the
        # seam every existing test patches, and routing Chrome around it would
        # quietly unhook those guards.
        return find_chrome()
    return find_browser(key)


# The Popen handle for the Chrome this process started, so a caller (the
# test suite) can shut down exactly that instance instead of taskkilling
# every chrome.exe on the machine.
LAST_CHROME_PROCESS = None


def default_user_profile_dir():
    """The real Chrome profile - the one with the user's logins, extensions
    and certificates.

    Read-only, always: it is the source of the first-run copy and the one
    directory the launcher refuses to drive (CLAUDE.md 2.1). Edge's equivalent
    is `gmes_browsers.real_user_data_dir("edge")`, and both are covered by the
    guard in `launch_automation_chrome()`."""
    return gmes_browsers.real_user_data_dir("chrome")


def chrome_is_running():
    return gmes_browsers.is_running("chrome")


def working_profile_dir():
    """Where the debuggable COPY of the user's profile lives.

    The original strategy, and still the one this developer machine uses.
    `automation_profile_dir()` below is the one that ships - see why there."""
    override = os.environ.get("CHROME_CDP_PROFILE_DIR")
    if override:
        return override
    return os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Google\Chrome\CDP Profile")


# ---------------------------------------------------------------------------
# The profile the tool owns, builds empty, and can ship
# ---------------------------------------------------------------------------
#
# Copying the user's real Chrome profile works on ONE machine and cannot be
# distributed, for a reason that is worth stating plainly because it is not
# obvious and it is not a matter of degree:
#
#   Chrome 140+ wraps every cookie on Windows in App-Bound Encryption (the
#   "v20" prefix). That key is derived through Chrome's own elevation service
#   and is bound to the machine. A profile copied to a DIFFERENT PC cannot
#   decrypt its own cookies - it fails with 0x57 and yields nothing. So there
#   is no such thing as shipping a pre-authenticated profile in an installer.
#
# Each user therefore signs in once, on their own machine. That costs one ADFS
# round trip on the very first run and nothing afterwards, because this profile
# is PERSISTENT: the session it earns lives here and every later run reuses it,
# which is the same benefit the copy provides today - without dragging along
# the user's personal cookies, history and extensions.
#
# Rooted next to credentials.dat on purpose: everything the tool owns for a
# given user is then in one place, under one directory they can delete if they
# ever want to start over.

AUTOMATION_ROOT = gmes_browsers.AUTOMATION_ROOT


def automation_profile_dir(name="default"):
    """The browser profile this tool creates and owns, for `name`.

    A pure path computation, deliberately: it must give the same answer in
    every process without reading any state. `GMES_PROFILE_DIR` overrides it
    outright, which is how a clean-machine rehearsal is done without a second
    PC."""
    override = os.environ.get("GMES_PROFILE_DIR")
    if override:
        return override
    return os.path.join(AUTOMATION_ROOT, "profiles", name)


def active_profile_dir():
    """The profile a run should actually drive.

    Usually `automation_profile_dir()`. It differs only when first-run had to
    place the profile somewhere else - the case that matters being a record
    carried over from another PC, where this machine gets its own directory
    beside the foreign one rather than reusing or deleting it
    (`gmes_browsers.ensure_bootstrapped`).

    Reading it from the recorded state rather than recomputing it is what lets
    a SECOND process - `gmes_data.py` in another terminal - resolve the same
    profile, and therefore the same DevToolsActivePort, as the process that
    started the browser.

    `GMES_PROFILE_DIR` alone does NOT redirect a machine that has already run
    once: the recorded profile wins. That is by design (see above) but it was
    silent - a clean-machine rehearsal believed it was on a fresh profile and
    drove the real one (HISTORY.md Phase 84.10). It now says so, once, and names
    the second variable that a rehearsal needs."""
    global _OVERRIDE_WARNED
    recorded = gmes_browsers.recorded_profile_dir()
    override = os.environ.get("GMES_PROFILE_DIR")
    if (recorded and override and not _OVERRIDE_WARNED
            and _same_path(recorded, override) is False):
        _OVERRIDE_WARNED = True
        print(f"  NOTE: GMES_PROFILE_DIR is set to '{override}' but this machine already "
              f"recorded its profile as '{recorded}', so the RECORDED one is used. To "
              "rehearse a clean machine, also set GMES_BROWSER_STATE to a new file "
              "(and never point either at your own browser's profile).")
    return recorded or automation_profile_dir()


_OVERRIDE_WARNED = False


def _same_path(a, b):
    """True/False, or None when they cannot be compared."""
    try:
        return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))
    except (TypeError, ValueError):
        return None


# Written into a NEW profile before Chrome first opens it. Only the settings
# automation actually needs - this is not a place to express preferences.
#
#   password manager off   we type a real Knox password into the ADFS form;
#                          a "save password?" bubble over it is both a modal
#                          in the way and somewhere the password should not go
#   popups allowed         AD SSO opens ADFS with window.open(). The machine's
#                          own GPO allowlist does not cover it (Phase 56.1),
#                          and a swallowed popup looks exactly like a slow one
#   download prompt off    the file must arrive without a dialog; CDP sets the
#                          directory per connection (GMES_SKILL #16)
#   exited_cleanly         suppresses "Chrome didn't shut down correctly -
#                          restore pages?", which is a real bubble that would
#                          sit on top of the work screen after any hard stop
#   signin off             no "sign in to Chrome" prompts in a corporate profile
_SEED_PREFERENCES = {
    "credentials_enable_service": False,
    "profile": {
        "password_manager_enabled": False,
        "password_manager_leak_detection": False,
        "default_content_setting_values": {"popups": 1, "notifications": 2},
        "exit_type": "Normal",
        "exited_cleanly": True,
    },
    "download": {"prompt_for_download": False, "directory_upgrade": True},
    "browser": {"has_seen_welcome_page": True, "check_default_browser": False},
    "signin": {"allowed": False},
}


def seed_automation_profile(path=None, verbose=True):
    """Create the tool's profile if it is not there, and seed it ONCE.

    Returns (path, created). Seeding only ever happens at creation: Chrome
    rewrites `Preferences` every time it exits, so writing over an existing
    one would throw away the session, the cookies and anything the profile has
    learned - which is precisely what this profile exists to keep.

    Never touches the user's real Chrome profile, and never deletes anything.
    """
    path = path or automation_profile_dir()
    if os.path.isdir(path):
        return path, False

    default_dir = os.path.join(path, "Default")
    os.makedirs(default_dir, exist_ok=True)
    with open(os.path.join(default_dir, "Preferences"), "w", encoding="utf-8") as fh:
        json.dump(_SEED_PREFERENCES, fh)
    if verbose:
        print(f"Created this tool's own browser profile: {path}")
        print("  The first sign-in will be a real one; after that the session "
              "lives here and runs start immediately.")
    return path, True


# ---------------------------------------------------------------------------
# Which port - asked for, not chosen
# ---------------------------------------------------------------------------
#
# A fixed port is one browser. A port RANGE is worse than it looks: picking a
# free port and then handing it to a subprocess to bind leaves a gap in which
# something else can take it, which is a documented race in Selenium's own
# PortProber (SeleniumHQ/selenium #8794, #12585).
#
# Chrome solves it natively. `--remote-debugging-port=0` makes the OS assign a
# port and hand it over already bound, and Chrome writes it into
# DevToolsActivePort INSIDE that instance's own profile directory:
#
#     line 1:  51734                          <- the port
#     line 2:  /devtools/browser/<uuid>        <- the browser websocket path
#
# Because the file lives in the profile, the port is a property of the
# profile. One profile, one browser, one discoverable port - which is what
# makes several instances possible later without any registry at all.

DEVTOOLS_PORT_FILE = "DevToolsActivePort"


def read_devtools_port(profile):
    """The port Chrome bound for `profile`, or None.

    Returns (port, browser_ws_path). **Presence of the file proves nothing**:
    Chrome leaves it behind when it exits, so a stale file names a port that
    nothing is listening on. Callers that need a LIVE browser must check
    `cdp_is_up(port)` as well - `launch_automation_chrome()` does."""
    path = os.path.join(profile, DEVTOOLS_PORT_FILE)
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        return int(lines[0].strip()), (lines[1].strip() if len(lines) > 1 else "")
    except (OSError, ValueError, IndexError):
        return None


def _clear_devtools_port(profile):
    """Remove a stale DevToolsActivePort before launching.

    Chrome's own scratch file, inside the profile this tool created and owns -
    never the user's real profile, and never anything Chrome cannot rebuild on
    its next start."""
    try:
        os.unlink(os.path.join(profile, DEVTOOLS_PORT_FILE))
    except OSError:
        pass


def active_port(profile=None):
    """The port to use when a caller has not named one.

    In order: a port already resolved in this process; the port recorded in
    the profile by a browser some OTHER process started; an explicit
    NERP_CDP_PORT; the historical default. The middle step is what lets
    `gmes_data.py` in a second terminal reach the browser `gmes_report.py`
    started, now that the number is no longer fixed."""
    global ACTIVE_PORT
    if ACTIVE_PORT:
        return ACTIVE_PORT
    found = read_devtools_port(profile or active_profile_dir())
    if found:
        ACTIVE_PORT = found[0]
        return ACTIVE_PORT
    return CDP_PORT


# Caches are large, regenerate themselves, and carry nothing we need. One
# list, shared with the first-run copy, so the two cannot drift into
# disagreeing about what a profile copy is allowed to leave behind.
_PROFILE_SKIP_DIRS = gmes_browsers.SKIP_DIRS


def clone_user_profile(dest=None, refresh=False, verbose=True):
    """Copy the real Chrome profile to a directory we are allowed to debug.

    Chrome 136 and later REFUSE --remote-debugging-port whenever
    --user-data-dir points at the default profile directory. The flag is not
    rejected loudly; it is silently ignored and the port simply never opens,
    which looks exactly like a launch failure. This is a deliberate security
    fix (it stopped malware from reading cookies out of a live browser via
    DevTools), so it is not something to work around in place - the
    supported approach is to debug a copy.

    The copy keeps extensions, cookies and saved logins, so the automated
    browser is signed in the same way the user's is. Session-only cookies do
    not survive, so a site may ask to log in once inside the copy.

    Copying is skipped when the destination already exists, unless refresh
    is set - re-copying on every run would be slow and would throw away the
    logged-in state built up inside the copy."""
    src = default_user_profile_dir()
    dest = dest or working_profile_dir()

    if not os.path.isdir(src):
        raise RuntimeError(f"Could not find the Chrome profile at {src!r}. "
                           "Set CHROME_USER_DATA_DIR to its location.")

    if os.path.isdir(dest) and not refresh:
        if verbose:
            print(f"Using the existing debuggable profile copy: {dest}")
        return dest

    if verbose:
        print(f"Copying your Chrome profile (this happens once, ~1 minute):")
        print(f"  from {src}")
        print(f"  to   {dest}")

    excludes = []
    for name in _PROFILE_SKIP_DIRS:
        excludes.append(name)
    cmd = ["robocopy", src, dest, "/E", "/R:0", "/W:0",
           "/NFL", "/NDL", "/NJH", "/NJS", "/NP", "/XJ",
           "/XD"] + excludes
    result = subprocess.run(cmd, capture_output=True, text=True)
    # robocopy uses exit codes as a bitmask; 0-7 are success, 8+ are errors.
    if result.returncode >= 8:
        raise RuntimeError(
            f"Copying the profile failed (robocopy exit {result.returncode}). "
            f"{(result.stdout or '').strip()[-400:]}")
    if verbose:
        print("Profile copy ready.")
    return dest


# Shared by both launchers so they cannot drift apart.
#
# --disable-popup-blocking is the load-bearing one: G-MES's "AD SSO Login"
# opens ADFS via window.open(), this machine's Chrome popup-allowlist GPO does
# not cover that origin, and the popup was silently swallowed with nothing for
# Runtime.evaluate to see - which reads as "the SSO window never opened" and
# then falls through to a password attempt (HISTORY.md Phase 56.1). The same
# flag Selenium and Puppeteer both set by default.
_COMMON_CHROME_FLAGS = [
    "--remote-allow-origins=*",
    "--no-first-run",
    "--no-default-browser-check",
    # NEVER add `--restore-last-session` here, with or without `=false`.
    # Chrome command-line switches are presence-based - `HasSwitch()` does
    # not read the value - so `--restore-last-session=false` does not
    # disable restore, it REQUESTS it. This list used to carry exactly that
    # (HISTORY.md Phase 82.17), and every launch reopened every G-MES tab
    # from every earlier run: measured live, 5 G-MES pages after a few
    # launches with the flag, exactly 1 without it, stable across repeated
    # relaunches. Each page is a live Nexacro application handshaking the
    # same account, and G-MES allows one session per account - hence its
    # "Currently being used by another PC" popup, over and over.
    "--disable-popup-blocking",
]

# Only for the profile the tool owns. Quietening background traffic matters
# more here than it looks: every one of these goes through the corporate
# proxy, and this profile has no reason to want any of it.
#
# NOT included, deliberately: --enable-automation. It would suppress the
# password-save UI for free, but it also sets navigator.webdriver = true,
# which a corporate application can read. The seeded preference turns that UI
# off without announcing the automation to the site.
_AUTOMATION_ONLY_FLAGS = [
    "--disable-background-networking",
    "--disable-component-update",
    "--disable-sync",
]


def launch_automation_chrome(profile=None, port=None, url=None, wait_seconds=45,
                             verbose=True, browser=None):
    """Start the automation browser on the profile THIS TOOL owns.

    Chrome or Edge - the flags are identical because both are Chromium, and
    which one it is was decided once, on the first run, by
    `gmes_browsers.ensure_bootstrapped()`. The name is unchanged because every
    caller and every test already uses it.

    **When `profile` is not given, this is also the first-run entry point.**
    That placement is deliberate: `gmes_login.ensure_browser()` and
    `gmes_connect.py` both call this with no profile, so both get the
    bootstrap from one place instead of each remembering to ask for it. A
    caller that names a profile is telling us it already knows which one it
    wants, and is left alone.

    On first run the bootstrap copies the profile the employee already uses,
    so a session they are already signed in with comes across and the run goes
    straight through. Afterwards it does nothing at all - the copy happens
    once (HISTORY.md Phase 75). Never copies, reads or deletes the user's real
    profile at any later point, and never deletes this one either: the session
    inside it is the whole point.

    The user's own browser being open is not a conflict - Chromium runs a
    second instance happily on a different --user-data-dir (GMES_SKILL.md
    #44). It only matters during the one-off first-run copy, where the files
    holding the session are locked while the browser has them open.

    The port is not chosen here. Unless one is named - by argument or by
    NERP_CDP_PORT - the browser is asked for port 0 and the OS assigns a free
    one, which is then read back out of the profile. See
    `read_devtools_port()`."""
    global ACTIVE_PORT, LAST_CHROME_PROCESS

    if profile is None:
        chosen = gmes_browsers.ensure_bootstrapped(
            automation_profile_dir(), _SEED_PREFERENCES, verbose=verbose)
        profile = chosen["profile_dir"]
        browser = browser or chosen["browser"]

    # A wrong --user-data-dir here would put remote debugging on the user's
    # real browser, which is the one thing CLAUDE.md 2.1 exists to prevent.
    # Chrome 136+ would refuse it anyway - silently, by ignoring the port -
    # so refusing it here is what makes it say why. Both supported browsers
    # are covered, and so is a path INSIDE one of them.
    real = gmes_browsers.protected_match(profile)
    if real:
        raise RuntimeError(
            f"Refusing to launch automation against the real "
            f"{gmes_browsers.spec(real)['short']} profile "
            f"({profile!r}). That profile holds the user's own logins and "
            "history; the tool has its own at automation_profile_dir().")

    # Already serving this profile? Reuse it. Both halves matter: the file
    # survives the browser exiting, so it is only evidence when the port
    # answers.
    running = read_devtools_port(profile)
    if running and cdp_is_up(running[0]):
        ACTIVE_PORT = running[0]
        return None

    seed_automation_profile(profile, verbose=verbose)

    requested = port if port is not None else (
        int(_PORT_FROM_ENV) if _PORT_FROM_ENV else 0)

    # A leftover file from a previous run names a port nothing is listening
    # on. Left in place, the loop below would read it, ask cdp_is_up() about
    # the wrong port, and wait out the whole timeout for a browser that had
    # already started perfectly well on a different one.
    _clear_devtools_port(profile)

    chrome = executable_for(browser)
    args = [chrome, f"--remote-debugging-port={requested}",
            f"--user-data-dir={profile}",
            "--profile-directory=Default",
            *_COMMON_CHROME_FLAGS,
            *_AUTOMATION_ONLY_FLAGS]
    if url:
        args.append(url)

    LAST_CHROME_PROCESS = subprocess.Popen(
        args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        found = read_devtools_port(profile)
        if found and cdp_is_up(found[0]):
            ACTIVE_PORT = found[0]
            if verbose and requested == 0:
                print(f"  the browser is listening on port {found[0]} "
                      "(assigned by the operating system).")
            return LAST_CHROME_PROCESS
        time.sleep(0.5)

    # The browser this call started never became usable. Left running it is an
    # orphan nothing will ever close - measured live: ten automation-browser
    # processes survived a failed first launch, one set per attempt. Only the
    # process THIS call started is ended (never anything found by name -
    # CLAUDE.md 2.6).
    _abandon_launch(LAST_CHROME_PROCESS)

    # Say which of the two things failed - they have different causes.
    name = gmes_browsers.spec(
        browser or gmes_browsers.recorded_browser() or "chrome")["short"]
    stale = read_devtools_port(profile)
    if stale:
        raise RuntimeError(
            f"{name} recorded port {stale[0]} for the profile at {profile!r} "
            f"but nothing is answering on it. A {name} policy on this machine "
            "may be blocking remote debugging.")
    raise RuntimeError(
        f"{name} started but never reported a debugging port for the profile "
        f"at {profile!r} (no {DEVTOOLS_PORT_FILE} appeared). Usual causes: "
        f"that profile is already open in another {name} instance, or {name} "
        "failed to start at all.")


def _abandon_launch(process, grace=5):
    """End a browser process this module started and could not use. Returns
    True if it is gone. Never raises: it runs on a failure path that is about
    to raise something more useful."""
    if process is None:
        return True
    try:
        if process.poll() is not None:
            return True                 # already gone (it handed off and exited)
        process.terminate()
        try:
            process.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=grace)
        return process.poll() is not None
    except Exception:                                     # noqa: BLE001
        return False


def launch_chrome_with_user_profile(port=None, url=None, wait_seconds=45,
                                    refresh_profile=False):
    """Start Chrome on a debuggable COPY of the user's own profile.

    The original strategy, kept for this developer machine and for the
    explicit `--refresh-profile` escape hatch (CLAUDE.md 2.1a). New machines
    use `launch_automation_chrome()` instead, because a copied profile cannot
    be moved to another PC at all - see the App-Bound Encryption note above
    `automation_profile_dir()`.

    Never deletes or modifies the real profile - see clone_user_profile for
    why a copy is required at all."""
    port = port or CDP_PORT
    if cdp_is_up(port):
        return None  # already listening; reuse it

    profile = clone_user_profile(refresh=refresh_profile)
    chrome = find_chrome()
    args = [chrome, f"--remote-debugging-port={port}",
            f"--user-data-dir={profile}",
            "--profile-directory=Default",
            *_COMMON_CHROME_FLAGS]
    if url:
        args.append(url)

    global LAST_CHROME_PROCESS
    LAST_CHROME_PROCESS = subprocess.Popen(
        args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        if cdp_is_up(port):
            return LAST_CHROME_PROCESS
        time.sleep(0.5)

    raise RuntimeError(
        f"Chrome started but never opened the debugging port {port}. Usual "
        "causes: another Chrome window is still open (check the system tray), "
        f"or the profile copy at {profile!r} is in use by another instance.")


def close_browser(port=None, timeout=15):
    """Close the automation browser, and only that one.

    Through its own DevTools endpoint rather than `taskkill /IM chrome.exe`,
    which would take every Chrome window the user has open (CLAUDE.md 2.6).
    Returns True once the port has actually gone."""
    global ACTIVE_PORT
    port = port or active_port()
    ACTIVE_PORT = None       # whatever it was, it is not ours any more
    if not cdp_is_up(port):
        return True
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"http://{CDP_HOST}:{port}/json/version", timeout=5) as response:
            info = json.loads(response.read().decode())
        ws = connect(ipv4(info["webSocketDebuggerUrl"]), enable_runtime=False)
        try:
            send(ws, "Browser.close", timeout=5)
        finally:
            try:
                ws.close()
            except Exception:
                pass
    except Exception:
        pass        # it may have gone on its own between the check and the call

    deadline = time.time() + timeout
    while time.time() < deadline:
        if not cdp_is_up(port):
            return True
        time.sleep(0.5)
    return False


def stop_if_started_here(keep_open=False):
    """Close the automation browser THIS process launched, gracefully.

    Every one-off entrance (`gmes_report.py`, `gmes_batch.py`,
    `gmes_daily_prodplan.py`, `gmes_demo.py`) used to call
    `LAST_CHROME_PROCESS.terminate()` directly at exit - a raw process kill,
    not the graceful `close_browser()` two lines above it, despite
    CLAUDE.md 2.6 already saying the browser is closed "through its own CDP
    endpoint". `close_browser()` asks Chrome itself to shut down
    (`Browser.close`), which is also the only way Chrome's own `Preferences`
    records a normal exit rather than `exit_type: "Crashed"` - and a crashed
    exit is what triggers Chrome's own crash-session-restore on the next
    launch, silently reopening a stale tab (the duplicate-tab/session-kick
    bug traced to exactly this in an earlier session). Never touches a
    browser some OTHER process is using - `LAST_CHROME_PROCESS` is only ever
    set by the launch this same process performed.

    Returns True if there was nothing to close, `keep_open` was set, or the
    close was confirmed; False if a close was attempted and never confirmed."""
    global LAST_CHROME_PROCESS
    if keep_open or not LAST_CHROME_PROCESS:
        return True
    closed = close_browser()
    LAST_CHROME_PROCESS = None
    return closed


def cdp_is_up(port=None, timeout=2):
    try:
        get_tabs(port=port, timeout=timeout)
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------
# Low-level CDP transport
# --------------------------------------------------------------------------

_msg_ids = itertools.count(1)


def next_id():
    """Monotonic CDP message ids.

    The original code hand-picked ids per call site (2, 10, 11, 30, 90, 95,
    ...) with ad-hoc "+10" offsets, which is a latent correlation bug: two
    overlapping helpers on the same socket can reuse an id and read each
    other's reply. A shared counter makes collisions impossible."""
    return next(_msg_ids)


def ipv4(url):
    """Rewrite a DevTools URL to the IPv4 literal.

    Chrome hands back webSocketDebuggerUrl values pointing at "localhost".
    Connecting to those pays the same IPv6-first penalty as the HTTP
    endpoint - about two seconds per websocket, on every connect."""
    if not url:
        return url
    return url.replace("://localhost:", f"://{CDP_HOST}:")


def get_tabs(port=None, timeout=5):
    port = port or active_port()
    req = urllib.request.Request(f"http://{CDP_HOST}:{port}/json/list")
    # Explicitly bypass any configured proxy handler; setting NO_PROXY covers
    # urlopen's default opener, but being explicit also survives a caller
    # that installed its own opener earlier in the process.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=timeout) as response:
        return json.loads(response.read().decode())


def close_tab(target_id, port=None, timeout=5):
    """Close ONE tab in the automation browser, by target id.

    Through DevTools' own `/json/close/<id>`, which needs no websocket and
    cannot reach anything outside the browser this port belongs to - which is
    always the automation browser, never the user's (CLAUDE.md 2.6). Closing a
    tab is not closing a browser: `close_browser()` remains the only thing
    that ends a session, and nothing here calls taskkill.

    Returns True if DevTools accepted it. The CALLER must confirm the tab has
    actually gone by re-listing, because "accepted" is not "closed" - the same
    discipline the popup closer needed (GMES_SKILL.md #48)."""
    port = port or active_port()
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"http://{CDP_HOST}:{port}/json/close/{target_id}",
                         timeout=timeout) as response:
            response.read()
        return True
    except Exception:
        return False


BROWSER_GONE_TEXT = ("the connection to the automation browser was lost while the "
                     "run was in progress")


class BrowserGone(ConnectionError):
    """The connection to the automation browser (or its tab) was lost under a
    running command. Deliberately NOT "the browser closed or crashed": that is one
    cause, and a live check on a page that was still restarting after a reload
    showed the browser fully alive with only the tab's connection aborted
    (HISTORY.md Phase 84.4). What is known is worded as known; the likely causes
    are listed. A ConnectionError, so every existing `except OSError`/`except
    Exception` still catches it. It used to surface as `[WinError 10053] An
    established connection was aborted by the software in your host machine`,
    which reads as a network fault and names nothing to do."""


def send(ws, method, params=None, msg_id=None, timeout=20):
    """Send one CDP command and return its matching reply, discarding the
    event traffic (Runtime.consoleAPICalled etc.) that arrives in between."""
    msg_id = next_id() if msg_id is None else msg_id
    try:
        ws.send(json.dumps({"id": msg_id, "method": method, "params": params or {}}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                resp = json.loads(ws.recv())
            except websocket.WebSocketTimeoutException:
                continue
            if resp.get("id") == msg_id:
                return resp
    except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError,
            websocket.WebSocketConnectionClosedException) as e:
        raise BrowserGone(f"{BROWSER_GONE_TEXT[0].upper()}{BROWSER_GONE_TEXT[1:]} "
                          f"(the browser or its tab closed, crashed or was replaced; "
                          f"{type(e).__name__} during {method}).") from e
    raise TimeoutError(f"No response for {method}")


def evaluate(ws, js, timeout=20):
    """Run JS that returns a JSON string, and give back the parsed object.

    Two real problems this removes:
      * Several call sites omitted returnByValue and only worked by accident
        (primitives are inlined in the reply; anything else would not be).
      * A JS exception surfaced as a bare KeyError deep in the caller. Now it
        raises a RuntimeError naming the failing expression."""
    resp = send(ws, "Runtime.evaluate",
                {"expression": js, "returnByValue": True}, timeout=timeout)
    if "error" in resp:
        # A protocol-level refusal ("Execution context was destroyed." while
        # the page navigates, "Cannot find context with specified id") has no
        # "result" at all - it used to fall through to the generic "returned
        # no value" below and lose the only words that named the cause
        # (HISTORY.md Phase 91.2).
        raise RuntimeError(
            f"JS evaluation refused by the browser: "
            f"{resp['error'].get('message') or resp['error']}")
    result = resp.get("result", {})
    if "exceptionDetails" in result:
        detail = result["exceptionDetails"]
        text = (detail.get("exception", {}).get("description")
                or detail.get("text") or "unknown JS error")
        raise RuntimeError(f"JS evaluation failed: {text}")
    value = result.get("result", {}).get("value")
    if value is None:
        raise RuntimeError("JS evaluation returned no value (expected a JSON string)")
    return json.loads(value)


def connect(ws_url, timeout=20, enable_runtime=True):
    ws = _require_websocket().create_connection(ipv4(ws_url), timeout=timeout)
    if enable_runtime:
        send(ws, "Runtime.enable", timeout=timeout)
    return ws


def open_event_listener(ws_url, domains=("Network",), recv_timeout=0.4, timeout=20):
    """A SEPARATE connection to `ws_url`, dedicated to receiving events -
    never used for `send()`/`evaluate()`.

    `send()`'s own docstring says it "discards the event traffic... that
    arrives in between" while waiting for a specific command's reply -
    confirmed live (HISTORY.md Phase 82.6): enabling Network on the SAME
    socket already used for `evaluate()` calls would silently swallow
    `Network.requestWillBeSent`/`responseReceived` events that happen to
    arrive during any of those waits. One socket, one purpose - commands on
    one connection, events on another, to the same target.

    The returned socket's receive timeout is set short (`recv_timeout`) so
    a caller can poll it in a loop without blocking indefinitely on a
    quiet connection; each domain named in `domains` is enabled before
    returning."""
    ws = _require_websocket().create_connection(ipv4(ws_url), timeout=timeout)
    for domain in domains:
        send(ws, f"{domain}.enable", timeout=timeout)
    ws.settimeout(recv_timeout)
    return ws


def drain_events(ws):
    """Every event message currently waiting on an event-listener socket
    (see `open_event_listener()`), parsed, non-blocking beyond its own
    short receive timeout. A malformed or non-JSON frame is skipped rather
    than raised - an event listener must never crash the run it is only
    there to corroborate."""
    events = []
    while True:
        try:
            raw = ws.recv()
        except websocket.WebSocketTimeoutException:
            break
        except Exception:
            break
        try:
            events.append(json.loads(raw))
        except (TypeError, ValueError):
            continue
    return events


def navigate_page(url, port=None, timeout=15):
    """Point the top-level page target at `url` on a short-lived connection.

    The socket is opened and closed around the navigation on purpose: holding
    one open and idle across a page load makes the next recv() time out
    (gotcha #4).

    The Page.navigate acknowledgement is treated as optional. Navigating away
    from a chrome:// page (chrome://newtab is what a freshly launched Chrome
    shows) is a cross-process navigation that swaps renderers, and Chrome can
    tear the DevTools session down before the reply is written - observed as
    a bare ConnectionResetError WinError 10054, which used to abort the whole
    run. By then the navigation has already been issued, and the caller polls
    for the loaded page regardless, so a lost ack is not a failure."""
    tab = get_page_tab(prefer_url_substring=None, port=port)
    if tab is None:
        raise RuntimeError("No page target available. Is Chrome running with CDP enabled?")

    client = _require_websocket()
    ws = None
    try:
        ws = client.create_connection(ipv4(tab["webSocketDebuggerUrl"]), timeout=timeout)
        send(ws, "Page.enable", timeout=timeout)
        send(ws, "Page.navigate", {"url": url}, timeout=timeout)
    except (OSError, TimeoutError, client.WebSocketException) as e:
        print(f"(no navigate acknowledgement: {e!r} - the navigation was still "
              "issued; the poll that follows confirms the load)")
    finally:
        if ws:
            try:
                ws.close()
            except Exception:
                pass
    return tab


# --------------------------------------------------------------------------
# Input simulation
# --------------------------------------------------------------------------

def click_element_by_rect(ws, x, y, msg_id_start=None):
    """Real mouse event simulation. Required because element.click() is
    ignored by Nexacro's controls - they are <div>s wired to mousedown/
    mouseup, with no click handler to invoke at all.

    msg_id_start is accepted for backwards compatibility and ignored; ids
    now come from the shared counter."""
    send(ws, "Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y})
    send(ws, "Input.dispatchMouseEvent", {
        "type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1})
    time.sleep(0.1)
    send(ws, "Input.dispatchMouseEvent", {
        "type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1})


def dispatch_key_combo(ws, key, code, vk, ctrl=False, shift=False, alt=False,
                       msg_id_start=None):
    """Dispatch a keyboard shortcut (e.g. Ctrl+Shift+F7) to whichever target
    ws is connected to. modifiers bitmask: Alt=1, Ctrl=2, Meta=4, Shift=8."""
    mods = (1 if alt else 0) | (2 if ctrl else 0) | (8 if shift else 0)
    params = {
        "modifiers": mods,
        "key": key,
        "code": code,
        "windowsVirtualKeyCode": vk,
        "nativeVirtualKeyCode": vk,
    }
    send(ws, "Input.dispatchKeyEvent", {"type": "rawKeyDown", **params})
    send(ws, "Input.dispatchKeyEvent", {"type": "keyUp", **params})


# JS snippet shared by every "type into a field" call site. A bare
# `.value =` assignment is not observed - the framework listens for the
# events, not the property - so they must be fired too.
JS_SET_VALUE = """
    el.focus();
    el.value = value;
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
"""


# --------------------------------------------------------------------------
# Element lookup
# --------------------------------------------------------------------------

# Shared JS predicate: an element is only clickable if it has a non-zero box
# AND that box actually intersects the viewport (CLAUDE.md 3.3). Some menus
# and dropdown widgets pre-render off-screen to measure themselves before
# repositioning - observed at y = -99984 with a perfectly valid width and
# height - so a size-only check finds them, clicks empty space, and the whole
# flow fails silently with no error anywhere.
JS_IS_VISIBLE = """
    function(el) {
        const r = el.getBoundingClientRect();
        if (r.width === 0 || r.height === 0) return false;
        if (r.bottom <= 0 || r.right <= 0) return false;
        if (r.top >= window.innerHeight || r.left >= window.innerWidth) return false;
        return true;
    }
"""


# --------------------------------------------------------------------------
# Target selection
# --------------------------------------------------------------------------

def get_page_tab(prefer_url_substring="nerps", port=None):
    """Pick the top-level page target to drive.

    Prefers a tab whose URL contains `prefer_url_substring`; falls back to
    any page target. The preference exists because a long-lived CDP session
    accumulates duplicate and stale page targets, so
    `next(t for t in tabs if t['type'] == 'page')` picks whichever stale one
    happens to be listed first.

    **The `"nerps"` default is dead and deliberately left alone.** Every
    surviving caller passes the argument explicitly - `gmes_connect.py` sends
    `"gmes"`, while `navigate_page()` and `capture_screenshot()` both send
    `None` - so the default is unreachable in this codebase. It was N-ERP's,
    and N-ERP was removed in HISTORY.md Phase 72. Changing it would be a
    behaviour change on the shared screenshot path for no practical gain, so
    it was recorded rather than edited; `tests/test_cdp_common.py` covers the
    two forms that are actually used."""
    tabs = get_tabs(port=port)
    pages = [t for t in tabs if t.get("type") == "page"]
    if not pages:
        return tabs[0] if tabs else None
    if prefer_url_substring:
        preferred = [t for t in pages if prefer_url_substring in t.get("url", "")]
        if preferred:
            return preferred[0]
    return pages[0]


# --------------------------------------------------------------------------
# Diagnostics
# --------------------------------------------------------------------------

def capture_screenshot(path, port=None, timeout=20, tab=None):
    """Save a PNG of the browser window.

    Page.captureScreenshot fails with "Command can only be executed on
    top-level targets" if called on an iframe target's own connection, so
    this always connects to a page-type target. Iframe content is still
    visible in the result, since it renders inside that page.

    `tab`, optional: use this exact CDP target instead of resolving one via
    `get_page_tab(prefer_url_substring=None, ...)` (i.e. whichever page
    target happens to be listed first). That default is correct when only
    one page target normally exists, which is why every caller so far has
    been fine leaving `tab` unset. It stops being correct the moment a
    second page target exists at the same time - a leftover popup, an old
    tab - and the diagnostic silently photographs the wrong page while
    looking like evidence (HISTORY.md Phase 56.4). A caller that can name
    its own tab correctly (see `gmes_common.capture_screenshot`) should."""
    if tab is None:
        tab = get_page_tab(prefer_url_substring=None, port=port)
    if not tab:
        return None
    ws = None
    try:
        ws = connect(tab["webSocketDebuggerUrl"], timeout=timeout, enable_runtime=False)
        send(ws, "Page.enable", timeout=timeout)
        resp = send(ws, "Page.captureScreenshot", {"format": "png"}, timeout=timeout)
        data = resp["result"]["data"]
        with open(path, "wb") as fh:
            fh.write(base64.b64decode(data))
        return path
    except Exception as e:
        print(f"(screenshot failed: {e!r})")
        return None
    finally:
        if ws:
            ws.close()


def screenshot_on_failure(prefix="gmes_failure", tab=None):
    """Best-effort diagnostic snapshot next to the scripts, named by time.

    `tab`: see `capture_screenshot()` - unset preserves the exact existing
    behaviour for every caller that does not need to name one. Prefer
    `gmes_common.screenshot_on_failure()`, which resolves the G-MES tab
    strictly rather than taking whichever page is listed first.

    The default prefix was `nerp_failure` until HISTORY.md Phase 72; no
    caller relies on it (every one passes its own), and `.gitignore` covers
    `gmes_*.png`."""
    name = f"{prefix}_{time.strftime('%Y%m%d_%H%M%S')}.png"
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), name)
    saved = capture_screenshot(path, tab=tab)
    if saved:
        print(f"Diagnostic screenshot saved: {saved}")
    return saved

"""
Browser-neutral Chromium handling: discovery, profile enumeration, and the
FIRST-RUN bootstrap that gives the automation the browser environment the
employee already uses with G-MES.

Why this module exists
----------------------
HISTORY.md Phase 73.1 replaced the copied profile with one the tool builds
empty, because a copied profile **cannot be shipped**: Chrome 140+ wraps every
cookie on Windows in App-Bound Encryption whose key is bound to the machine,
so a profile copied to a DIFFERENT PC decrypts nothing (GMES_SKILL.md #1).
That reasoning is about *distribution*, and it still holds - nothing here
changes it.

It was never an argument against copying a profile **on the machine it was
made on**, which is the one case where the encryption works perfectly. A new
user on a new PC is already signed in to G-MES in their own Edge or Chrome;
starting from an empty profile throws that away and makes them sit through an
ADFS round trip that can fail for reasons unrelated to their password
(Phase 74.1 - and a failed corporate sign-in is the most expensive failure
this project has, because the old code answered it by spending one of five
attempts before the account locks).

So: on first run only, copy the profile they already use; afterwards, reuse
the copy exactly as before. If anything about that is not possible - no
supported browser, no usable profile, a locked file, a copy that fails
halfway - fall back to the empty tool-built profile from Phase 73, which is
still a completely working path.

The rules this module must not break
------------------------------------
* CLAUDE.md 2.1 / 2.1a - the user's real profile is **read-only** here. It is
  never launched, never debugged, never written to, never deleted. Neither is
  an automation profile that has already been onboarded: recovery is always
  ADDITIVE (a new directory beside the old one), never a reset.
* CLAUDE.md 2.6 - a browser that is in the way is reported, never killed.
* CLAUDE.md 3.1 - nothing here sleeps a fixed duration.
* CLAUDE.md 3.5 - a copy that returned without error is not a copy that
  worked. `_verify_copy()` checks the files that carry the session.

Browser-neutral means browser-neutral: Chrome and Edge differ by a table
entry (`BROWSERS`), not by a code path. Adding Brave or Vivaldi later is a
row, not a fork.

Read-only self-check, safe to run at any time:

    python gmes_browsers.py
"""
import hashlib
import json
import os
import platform
import shutil
import sqlite3
import subprocess
import time

# Mirrors gmes_common.GMES_HOST - a deliberate, small duplication of a fixed
# literal (CLAUDE.md 5's own environment table) rather than an import.
# gmes_browsers imports nothing from the rest of the project on purpose (see
# the module docstring); importing gmes_common here would reach back through
# cdp_common, which imports gmes_browsers, and cycle.
GMES_COOKIE_HOST = "seegmes4.sec.samsung.net"


# ---------------------------------------------------------------------------
# Where everything the tool owns for a user lives
# ---------------------------------------------------------------------------
#
# Same root as credentials.dat, for the same reason: one directory the user can
# delete if they ever want to start over. `cdp_common.AUTOMATION_ROOT` is an
# alias of this - one definition, imported, not restated.

AUTOMATION_ROOT = os.path.join(os.environ.get("LOCALAPPDATA", ""), "GMES_Automation")

# The record of what first-run decided. It is the single source of truth for
# "which browser, which profile directory", readable by any process - which is
# what lets `gmes_data.py` in a second terminal resolve the same profile (and
# therefore the same DevToolsActivePort) as the process that started the
# browser.
STATE_FILE = "browser.json"
STATE_VERSION = 1


def state_path():
    override = os.environ.get("GMES_BROWSER_STATE")
    if override:
        return override
    return os.path.join(AUTOMATION_ROOT, STATE_FILE)


# ---------------------------------------------------------------------------
# The supported browsers, as data
# ---------------------------------------------------------------------------
#
# `prog_ids` are what Windows records as the user's chosen https handler. Edge
# ships several channel-specific ids (MSEdgeHTM for stable, MSEdgeBETA/DEV for
# the others) so the match is a case-insensitive prefix, not equality.
#
# Note Edge's machine-wide install lands under "Program Files (x86)" even on
# 64-bit Windows - it is listed first because that is where it actually is on
# a normal corporate image.

BROWSERS = {
    "chrome": {
        "key": "chrome",
        "label": "Google Chrome",
        "short": "Chrome",
        "exe": "chrome.exe",
        "path_env": "CHROME_PATH",
        "user_data_env": "CHROME_USER_DATA_DIR",
        "user_data": r"Google\Chrome\User Data",
        "prog_ids": ("chromehtml",),
        "candidates": (
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            ("LOCALAPPDATA", r"Google\Chrome\Application\chrome.exe"),
        ),
    },
    "edge": {
        "key": "edge",
        "label": "Microsoft Edge",
        "short": "Edge",
        "exe": "msedge.exe",
        "path_env": "EDGE_PATH",
        "user_data_env": "EDGE_USER_DATA_DIR",
        "user_data": r"Microsoft\Edge\User Data",
        "prog_ids": ("msedgehtm", "msedgebeta", "msedgedev", "msedgecanary"),
        "candidates": (
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            ("LOCALAPPDATA", r"Microsoft\Edge\Application\msedge.exe"),
        ),
    },
}

SUPPORTED = ("chrome", "edge")


class BrowserNotFound(RuntimeError):
    """No supported browser could be located on this machine."""


class ProfileLocked(RuntimeError):
    """The source profile could not be copied because the browser holds it
    open. The only fix is for a person to close that browser once - this tool
    does not kill browsers (CLAUDE.md 2.6)."""


def spec(key):
    try:
        return BROWSERS[key]
    except KeyError:
        raise ValueError(f"unsupported browser {key!r}; expected one of {SUPPORTED}")


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def _registry_value(root, path, name=""):
    """One registry read, or None. Isolated so tests can replace it without
    a real registry and so a non-Windows import does not explode."""
    if root is None:
        return None
    try:
        import winreg
    except ImportError:
        return None
    try:
        with winreg.OpenKey(root, path) as key:
            value, _kind = winreg.QueryValueEx(key, name)
        return value
    except OSError:
        return None


def _registry_roots():
    try:
        import winreg
        return (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER)
    except ImportError:
        return ()


def find_executable(key):
    """Locate a browser's exe without hardcoding one install path.

    The original `find_chrome()` assumed Program Files, which is only right
    for a machine-wide install; a per-user install lands in %LOCALAPPDATA%
    and the script died with a bare WinError 2. The same is true of Edge, and
    of an unusual corporate install path - hence the App Paths registry read,
    which is authoritative when a browser is installed somewhere odd.

    Returns a path, or None. Callers that need one raise their own error, so
    that "Chrome is missing" and "Edge is missing" can be reported together.
    """
    info = spec(key)

    override = os.environ.get(info["path_env"])
    if override and os.path.isfile(override):
        return override

    for candidate in info["candidates"]:
        if isinstance(candidate, tuple):
            env_name, tail = candidate
            base = os.environ.get(env_name, "")
            candidate = os.path.join(base, tail) if base else ""
        if candidate and os.path.isfile(candidate):
            return candidate

    found = shutil.which(info["exe"])
    if found:
        return found

    for root in _registry_roots():
        path = _registry_value(
            root,
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{}".format(info["exe"]))
        if path and os.path.isfile(path):
            return path

    return None


def real_user_data_dir(key):
    """The browser's REAL user-data directory - the one holding the user's own
    logins, cookies and extensions.

    Read-only as far as this project is concerned. It is the source of the
    first-run copy and the thing `cdp_common` refuses to launch against
    (CLAUDE.md 2.1)."""
    info = spec(key)
    override = os.environ.get(info["user_data_env"])
    if override:
        return override
    return os.path.join(os.environ.get("LOCALAPPDATA", ""), info["user_data"])


def protected_user_data_dirs():
    """Every real profile directory the automation must never drive.

    Used by the launcher's guard. It covers both browsers, so pointing the
    automation at the user's real Edge is refused for the same reason, and
    with the same message shape, as the Chrome case that guard was written
    for."""
    dirs = []
    for key in SUPPORTED:
        try:
            path = real_user_data_dir(key)
        except ValueError:
            continue
        if path:
            dirs.append(os.path.abspath(path))
    return dirs


def same_path(a, b):
    """Are these the same Windows path, or is `b` inside `a`?

    `os.path.abspath()` normalises separators but **not case**, and Windows
    paths are case-insensitive: comparing two abspath results therefore says
    `C:\\Users\\...` and `c:\\users\\...` are different places. That is a
    security hole in a guard whose entire job is to refuse the user's real
    profile - `GMES_PROFILE_DIR` set in lower case, or an 8.3 short path from
    an older tool, would walk straight past it (verified: `protected_match()`
    returned None for the real profile path in lower case).

    `realpath` resolves 8.3 names, junctions and symlinks; `normcase`
    flattens case and separators. Both sides need both."""
    if not a or not b:
        return False
    a = os.path.normcase(os.path.realpath(a))
    b = os.path.normcase(os.path.realpath(b))
    return b == a or b.startswith(a + os.sep)


def protected_match(path):
    """Which browser's REAL profile `path` is, or sits inside - else None.

    `path` being inside one counts: `...\\User Data\\Default` is just as much
    the user's own profile as `...\\User Data`, and Chrome 136+ would silently
    ignore remote debugging for it rather than say so (GMES_SKILL.md #1), so
    the refusal has to come from here to be legible at all."""
    if not path:
        return None
    for key in SUPPORTED:
        if same_path(real_user_data_dir(key), path):
            return key
    return None


def default_browser_key():
    """Which supported browser Windows opens https links with, or None.

    Reads the UserChoice association rather than guessing. A machine whose
    default is Firefox, or something with no ProgId we recognise, returns
    None - which is a real answer ("no supported default"), not a failure:
    `candidate_sources()` then falls back to whichever supported browser is
    actually installed."""
    prog_id = _registry_value(
        _hkcu(),
        r"SOFTWARE\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice",
        "ProgId")
    if not prog_id:
        return None
    lowered = str(prog_id).strip().lower()
    for key in SUPPORTED:
        for known in spec(key)["prog_ids"]:
            if lowered.startswith(known):
                return key
    return None


def _hkcu():
    try:
        import winreg
        return winreg.HKEY_CURRENT_USER
    except ImportError:
        return None


def is_running(key):
    """Is this browser's process present right now?

    **This is a proxy signal and must never be used as a gate.** Measured on
    this machine: 12 `msedge.exe` processes with not one visible window, because
    Edge's Startup Boost and background-extension host keep it resident after
    every window is closed. Treating that as "Edge is open, ask them to close
    it" would refuse the first run on a normal Windows 11 corporate image with
    an instruction the user cannot satisfy - and Edge is exactly the browser
    this feature exists to support.

    So the copy is attempted regardless and the LOCK ITSELF is the evidence
    (CLAUDE.md 3.2: wait for the specific thing, not a proxy for it). This is
    used only to make the resulting message better - "Edge still has its
    profile open" reads very differently when Edge genuinely is open. Nothing
    here kills a browser (CLAUDE.md 2.6)."""
    info = spec(key)
    try:
        out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {info['exe']}"],
                             capture_output=True, text=True, timeout=15)
        return info["exe"] in (out.stdout or "")
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Profiles inside a user-data directory
# ---------------------------------------------------------------------------

LOCAL_STATE = "Local State"


def read_local_state(user_data_dir):
    """`Local State` as a dict, or {} if it cannot be read.

    This file matters twice over. It names the profiles (`profile.info_cache`)
    and which one was last used, and at its root it carries `os_crypt`, the
    DPAPI-wrapped key **without which the copied cookies decrypt to nothing**.
    That is why the copy takes the user-data-dir root and not just the profile
    folder."""
    try:
        with open(os.path.join(user_data_dir, LOCAL_STATE), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def list_profiles(key=None, user_data_dir=None):
    """Every profile directory that exists on disk, best-evidence first.

    A browser can hold many profiles ("Default", "Profile 1", work/personal).
    Each entry is:

        {"dir": "Profile 1", "name": "Work", "path": ..., "last_used": bool,
         "has_session": bool, "has_gmes_evidence": bool|None}

    `has_session` means the profile carries a cookie store AT ALL, which is
    true of any profile that has ever browsed anywhere - it is not evidence
    of G-MES specifically. `has_gmes_evidence` is that stronger check
    (`_has_gmes_evidence()`): a cookie or visited-page row actually scoped to
    the G-MES host. It is exactly three-valued on purpose - True is the only
    value that gets ranked ahead of anything; False and None (could not be
    determined at all) are deliberately NOT distinguished for ranking, since
    neither is grounds to prefer one profile over another the way a confirmed
    match is.

    A missing or malformed `Local State` is not fatal: the directories on disk
    are scanned directly, so a profile is still found when the index is not.
    """
    if user_data_dir is None:
        user_data_dir = real_user_data_dir(key)
    if not os.path.isdir(user_data_dir):
        return []

    state = read_local_state(user_data_dir)
    profile_section = state.get("profile") or {}
    info_cache = profile_section.get("info_cache") or {}
    last_used = profile_section.get("last_used") or ""
    last_active = profile_section.get("last_active_profiles") or []

    # Union of "what the index claims" and "what is actually on disk". Either
    # source alone has been wrong: the index lists profiles that have been
    # deleted, and a profile can exist on disk before the index is rewritten.
    names = set(info_cache)
    try:
        for entry in os.listdir(user_data_dir):
            if entry == "Default" or entry.startswith("Profile "):
                names.add(entry)
    except OSError:
        pass

    profiles = []
    for name in sorted(names):
        path = os.path.join(user_data_dir, name)
        if not os.path.isdir(path):
            continue
        profiles.append({
            "dir": name,
            "name": (info_cache.get(name) or {}).get("name") or name,
            "path": path,
            "last_used": name == last_used,
            "last_active": name in last_active,
            "has_session": _has_session(path),
            "has_gmes_evidence": _has_gmes_evidence(path),
        })

    # Order: CONFIRMED G-MES evidence first - a cookie or visited page
    # actually scoped to the G-MES host, not just "this profile has browsed
    # somewhere" - then the browser's own recency signals as a tiebreaker
    # among profiles with no such confirmation either way (Phase 75 originally
    # ranked by recency alone, which could copy a Personal profile used more
    # OFTEN over a Work profile that actually has the G-MES session -
    # HISTORY.md Phase 79).
    def rank(p):
        return (p["has_gmes_evidence"] is not True,
                not p["last_used"], not p["last_active"], not p["has_session"],
                p["dir"] != "Default", p["dir"])

    profiles.sort(key=rank)
    return profiles


def _has_session(profile_path):
    """Does this profile carry a cookie store?

    Chrome moved cookies to `<profile>\\Network\\Cookies` some versions ago and
    Edge followed; both locations are checked because a corporate image can be
    running either."""
    for relative in (os.path.join("Network", "Cookies"), "Cookies"):
        if os.path.isfile(os.path.join(profile_path, relative)):
            return True
    return False


def _has_gmes_evidence(profile_path):
    """Has this profile actually been used with G-MES - not merely "has a
    cookie database at all" (`_has_session()`, true of any profile that has
    ever browsed anywhere).

    Checked live (HISTORY.md Phase 79): `preferred_profile()` used to rank by
    recency alone, so a machine with a Personal profile the employee happens
    to use MORE OFTEN than their Work one - the one that actually has a G-MES
    session - would copy the wrong one. The copy is still read-only and still
    only from a supported browser's own real data either way, so this was
    never a safety issue - but it defeats the entire point of Phase 75, which
    is landing on a session that is actually there.

    Checks the plaintext columns only - a cookie's `host_key` and a visited
    page's `url` - never a cookie VALUE, which is App-Bound-Encrypted and is
    never decrypted anywhere in this project. Opened `mode=ro` (never
    `immutable=1`, HISTORY.md Open Item 38: that flag is a PROMISE the file
    will not change while open, made here about a file a running browser can
    still be writing to - SQLite's own docs call the result of a broken
    immutable promise undefined, which could mean a torn read rather than a
    clean error), so this works whether or not the browser is currently
    running (Phase 75 deliberately does not gate the copy on that). A short
    busy timeout (the `timeout=` on `connect()`, backed by
    `sqlite3_busy_timeout`) gives a lock genuinely being held by the running
    browser a moment to clear before this counts as inconclusive rather than
    "no evidence".

    Returns True/False when a database could be opened and queried, or None
    when NOTHING could be checked at all (every candidate file missing,
    unreadable, malformed, OR busy/locked for the whole timeout). `None` must
    not be treated as "no evidence" - ranking an UNCHECKABLE profile the same
    as a genuinely empty one would silently prefer whichever profile merely
    happens to sort first, which is the exact bug being fixed. A candidate
    that was busy/locked is exactly as uncheckable as one that was missing -
    counting it as "checked, found nothing" would let a locked Cookies file
    on the profile that is ACTUALLY signed into G-MES right now (the one most
    likely to have a lock held on it) rank as if it had no evidence at all."""
    candidates = (
        (os.path.join(profile_path, "Network", "Cookies"), "cookies", "host_key"),
        (os.path.join(profile_path, "Cookies"), "cookies", "host_key"),
        (os.path.join(profile_path, "History"), "urls", "url"),
    )
    checked_any = False
    inconclusive = False
    for path, table, column in candidates:
        if not os.path.isfile(path):
            continue
        try:
            uri = f"file:{path.replace(chr(92), '/')}?mode=ro"
            with sqlite3.connect(uri, uri=True, timeout=0.5) as conn:
                cur = conn.execute(
                    f"SELECT 1 FROM {table} WHERE {column} LIKE ? LIMIT 1",
                    (f"%{GMES_COOKIE_HOST}%",))
                checked_any = True
                if cur.fetchone():
                    return True
        except sqlite3.OperationalError as e:
            if "lock" in str(e).lower() or "busy" in str(e).lower():
                inconclusive = True
            continue
        except sqlite3.Error:
            continue
    if inconclusive:
        return None
    return False if checked_any else None


def preferred_profile(profiles):
    """The profile that appears to be the user's active one, or None."""
    return profiles[0] if profiles else None


def preferred_installed_browser():
    """Which supported browser to launch when there is nothing to copy from.

    Distinct from `candidate_sources()`, which additionally requires a
    profile WITH A SESSION - this only asks "is it actually installed here at
    all". Order: `GMES_BROWSER` override, the Windows default browser (if it
    is one of the two supported), then any other supported browser that
    resolves to a real executable.

    **Why this exists at all** (HISTORY.md Phase 78): every "fresh" outcome
    of `ensure_bootstrapped()` used to hardcode `"browser": "chrome"`
    unconditionally - bootstrap disabled, no candidate profile found, every
    candidate failed to copy. On a machine with Edge only and no Chrome at
    all, that recorded a browser that does not exist, and the very next
    launch failed outright: `executable_for()` trusts the record and calls
    `find_chrome()`, which raises. The empty-profile fallback is supposed to
    be the one path that always works; it was not, for exactly the audience
    "prefer whichever the employee already has" (Phase 75) exists to serve.

    Falls back to `"chrome"` only when NOTHING resolves at all - neither
    browser is installed, or discovery itself failed - so a genuinely broken
    machine still gets the same honest "could not find chrome.exe" it always
    did, rather than a fabricated one for a browser that was never there."""
    forced = (os.environ.get("GMES_BROWSER") or "").strip().lower()
    preferred = forced if forced in SUPPORTED else default_browser_key()
    ordered = [preferred] if preferred else []
    for key in SUPPORTED:
        if key not in ordered:
            ordered.append(key)
    for key in ordered:
        try:
            if find_executable(key):
                return key
        except Exception:
            continue
    return "chrome"


def candidate_sources():
    """Where a first-run copy could come from, best first.

    The Windows default browser is preferred - it is the one the employee
    actually uses, so it is the one most likely to hold a live G-MES session.
    The other supported browser follows as the fallback. A browser that is not
    installed, or that has no profile with a real session, is not a candidate
    at all.
    """
    forced = (os.environ.get("GMES_BROWSER") or "").strip().lower()
    # One registry read, not one per candidate.
    preferred = forced if forced in SUPPORTED else default_browser_key()

    ordered = [preferred] if preferred else []
    for key in SUPPORTED:
        if key not in ordered:
            ordered.append(key)

    sources = []
    for key in ordered:
        executable = find_executable(key)
        if not executable:
            continue
        user_data = real_user_data_dir(key)
        profiles = [p for p in list_profiles(user_data_dir=user_data) if p["has_session"]]
        profile = preferred_profile(profiles)
        if not profile:
            continue
        sources.append({
            "browser": key,
            "label": spec(key)["label"],
            "executable": executable,
            "user_data_dir": user_data,
            "profile": profile,
            "is_default_browser": key == preferred,
        })
    return sources


# ---------------------------------------------------------------------------
# Machine identity - a copy belongs to the PC it was made on
# ---------------------------------------------------------------------------

def machine_id():
    """A stable, non-identifying fingerprint of this PC.

    Hashed rather than stored plainly: the state file is a diagnostic people
    will paste into a chat, and a machine GUID is more than it needs to carry.

    This is the mechanism behind "never copy profiles between different PCs".
    Chrome's App-Bound Encryption would make a carried-over profile useless
    anyway (it fails with 0x57 and yields nothing, silently - GMES_SKILL.md
    #1), and "silently yields nothing" is exactly the failure shape this
    project exists to refuse. Detecting it and saying so beats discovering it
    as an unexplained signed-out session.

    **Stability is the whole requirement, and the obvious inputs are not
    stable.** An earlier version mixed in `USERNAME`/`USERDOMAIN`; under a
    Scheduled Task or a different logon context those change, the record stops
    matching, the "different PC" branch fires, and the bootstrap copies a
    SECOND time into a new directory - orphaning the profile holding the
    earned G-MES session, and presenting as an automation that is mysteriously
    signed out. They also added nothing: `%LOCALAPPDATA%` already scopes this
    file per Windows account.

    Windows' own `MachineGuid` is written once at install and survives a
    rename, so it is the right identity. `platform.node()` is the fallback for
    a machine where the key cannot be read - less stable, but it only has to
    be better than nothing."""
    guid = _registry_value(
        _hklm(), r"SOFTWARE\Microsoft\Cryptography", "MachineGuid")
    raw = str(guid or platform.node() or "unknown-machine").lower()
    return hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:16]


def _hklm():
    try:
        import winreg
        return winreg.HKEY_LOCAL_MACHINE
    except ImportError:
        return None


# ---------------------------------------------------------------------------
# The first-run record
# ---------------------------------------------------------------------------

def read_state():
    try:
        with open(state_path(), encoding="utf-8") as fh:
            state = json.load(fh)
        return state if isinstance(state, dict) else {}
    except (OSError, ValueError):
        return {}


def write_state(state):
    """Write the record atomically.

    Temp file plus `os.replace`, so an interrupted write cannot leave a
    half-parsed record that reads as "onboarded" on the next run. (The same
    gap `gmes_credentials.save()` still has - noted in ARCHITECTURE.md's gaps
    list - but this file is new, so it starts correct.)"""
    path = state_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp-{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2)
    os.replace(tmp, path)


def recorded_profile_dir():
    """The automation profile a COMPLETED bootstrap chose on THIS machine.

    None when first-run has not finished, when the record is for another PC,
    or when the directory it names has since gone. Every caller falls back to
    `cdp_common.automation_profile_dir()`, so None is a normal answer and not
    an error."""
    state = read_state()
    if not state.get("completed"):
        return None
    if state.get("machine") != machine_id():
        return None
    path = state.get("profile_dir")
    if path and os.path.isdir(path):
        return path
    return None


def recorded_browser():
    """Which browser a completed, same-machine bootstrap selected, or None."""
    state = read_state()
    if state.get("completed") and state.get("machine") == machine_id():
        key = state.get("browser")
        if key in BROWSERS:
            return key
    return None


# ---------------------------------------------------------------------------
# Copying - the one place that reads the user's real profile
# ---------------------------------------------------------------------------

# Caches are large, regenerate themselves, and carry nothing we need. The first
# eleven are the list `cdp_common.clone_user_profile()` has always used; the
# rest are the same idea applied to directories that only appeared once a
# whole profile (rather than a whole user-data-dir) was being copied.
SKIP_DIRS = [
    "Cache", "Code Cache", "GPUCache", "DawnCache", "DawnGraphiteCache",
    "DawnWebGPUCache", "GrShaderCache", "ShaderCache", "Media Cache",
    "component_crx_cache", "extensions_crx_cache", "Crashpad",
    "Safe Browsing", "optimization_guide_model_store",
    "CacheStorage", "ScriptCache", "blob_storage", "Download Service",
]

# Files deliberately NOT copied, even though they sit in the profile.
#
# The session this copy exists to carry is in the COOKIES. The saved-password
# and autofill databases are not needed by any part of this automation - the
# Knox credential is typed into ADFS from the DPAPI store (CLAUDE.md 2.2) and
# nothing here ever relies on a browser-saved password - so copying them would
# put a second copy of the user's password and card data on disk for no
# functional gain at all. `credentials_enable_service` is already forced off in
# the copied profile, so nothing will write them there either.
SKIP_FILES = [
    "Login Data", "Login Data For Account", "Login Data-journal",
    "Web Data", "Web Data-journal", "Affiliation Database",
]

# What has to be on the other side for the copy to have been worth doing.
# Checked because a copy that returns without error is not a copy that worked
# (CLAUDE.md 3.5): robocopy skips a file it cannot open and still reports
# success for everything else it managed, so "no error" and "the session came
# across" are different claims.
REQUIRED_AFTER_COPY = (LOCAL_STATE,)


#: robocopy's exit code is a bitmask. 0-7 are success (bit 0 = files copied,
#: bit 1 = extra files, bit 2 = mismatches). Bit 3 (8) means "some files could
#: not be copied", which for us usually - but NOT always - means the browser
#: has them open. Bit 4 (16) is a serious/usage error: a bad destination,
#: access denied, a path that cannot be created. Those two must not produce
#: the same advice, because "close your browser and try again" cannot fix a
#: full disk and sends the user somewhere there is nothing to find.
ROBOCOPY_SOME_FILES_FAILED = 8
ROBOCOPY_FATAL = 16

#: A generous cap, not an estimate (CLAUDE.md 3.1). A first copy is normally
#: well under a minute; this exists so an unattended 02:00 job cannot hang for
#: ever on a roaming or OneDrive-backed profile that never answers.
ROBOCOPY_TIMEOUT = 900


class CopyFailed(RuntimeError):
    """The copy could not be completed for a reason that is NOT a lock.

    Distinct from `ProfileLocked` because the two need opposite handling: a
    lock is worth stopping for and asking a person to fix, while this is worth
    falling back from."""


def _robocopy(args):
    """Run robocopy and translate its bitmask exit code.

    Returns (code, output). Raises `CopyFailed` for a hang - a subprocess that
    never returns is the one failure the caller cannot otherwise see."""
    try:
        result = subprocess.run(["robocopy", *args, "/R:0", "/W:0",
                                 "/NFL", "/NDL", "/NJH", "/NJS", "/NP", "/XJ"],
                                capture_output=True, text=True,
                                timeout=ROBOCOPY_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise CopyFailed(
            f"copying the profile did not finish within {ROBOCOPY_TIMEOUT}s "
            "and was abandoned")
    except OSError as e:
        raise CopyFailed(f"robocopy could not be run ({e})")
    return result.returncode, (result.stdout or "")


def copy_profile(source, dest, verbose=True):
    """Copy ONE profile out of a real user-data directory into `dest`.

    `source` is an entry from `candidate_sources()`. The copy is two passes,
    for a reason that is easy to get wrong:

      1. the user-data-dir ROOT, one level only - this is what brings
         `Local State` and therefore the key the cookies are encrypted with;
      2. the selected profile directory, recursively, minus the caches.

    The selected profile is written as `Default` regardless of what it was
    called, so the automation profile always has the same shape and the
    launcher's `--profile-directory=Default` stays true for every browser and
    every source profile.

    The source is opened for reading and nothing else. Nothing is written to
    it, nothing is deleted from it, and it is never the thing that gets
    launched (CLAUDE.md 2.1).
    """
    user_data = source["user_data_dir"]
    profile_dir = source["profile"]["dir"]

    src_root = os.path.abspath(user_data)
    dst_root = os.path.abspath(dest)
    if same_path(src_root, dst_root):
        raise RuntimeError(
            f"Refusing to copy {src_root!r} into itself ({dst_root!r}).")

    if verbose:
        print(f"  copying {source['label']} profile "
              f"{source['profile']['name']!r} (this happens once)...")

    _check(source, *_robocopy([src_root, dst_root, "/LEV:1"]))
    _check(source, *_robocopy([os.path.join(src_root, profile_dir),
                               os.path.join(dst_root, "Default"),
                               "/E", "/XD", *SKIP_DIRS, "/XF", *SKIP_FILES]))

    _verify_copy(dst_root)


def _check(source, code, out):
    """Turn a robocopy exit code into the right kind of failure, or nothing.

    The distinction matters more than it looks. Reporting every failure as
    "your browser is open" is a confidently wrong diagnosis for a full disk or
    a denied path - it sends someone to close a browser that is already closed,
    and it is the shape of failure this project exists to refuse."""
    if code >= ROBOCOPY_FATAL:
        raise CopyFailed(
            f"robocopy could not write the copy (exit {code}). This is not a "
            "locked file: usual causes are a full disk, a denied path, or a "
            "destination that cannot be created."
            + (f" It said: {out.strip()[-300:]}" if out.strip() else ""))
    if code >= ROBOCOPY_SOME_FILES_FAILED:
        raise ProfileLocked(_locked_message(source, out))


def _locked_message(source, detail):
    label = source["label"]
    extra = ""
    if source["browser"] == "edge":
        # Edge stays resident with no window open (Startup Boost / background
        # extensions), so "close it" is not enough on its own and someone who
        # HAS closed every window needs to be told that rather than left
        # doubting themselves.
        extra = (f"\n  {label} keeps running in the background after its last "
                 "window closes. If closing it\n  is not enough, turn off "
                 "Settings > System > 'Startup boost' and 'Continue running "
                 "background\n  extensions', or just use GMES_BOOTSTRAP=off "
                 "below.")
    return (
        f"{label} still has its profile open, so the files that carry your "
        f"G-MES session could not be copied.\n"
        f"  Close {label} completely - including any window hiding in the "
        f"system tray - and run this once more."
        + extra +
        f"\n  Nothing was changed in your own browser profile, and nothing "
        f"needs undoing.\n"
        f"  (If you would rather not copy a profile at all, set "
        f"GMES_BOOTSTRAP=off and the tool will start from an empty profile "
        f"and sign in once.)"
        + (f"\n  robocopy said: {detail.strip()[-300:]}" if detail.strip() else ""))


def _verify_copy(dest):
    """A copy that returned without error is not a copy that worked.

    All three checks are about the same single question - can this copy still
    sign in? `Local State` carries the key the cookies are encrypted with, the
    profile directory carries the cookies, and a source is only ever chosen
    when it HAS a cookie store, so a copy that arrives without one lost it in
    transit rather than never having had it."""
    missing = [name for name in REQUIRED_AFTER_COPY
               if not os.path.isfile(os.path.join(dest, name))]
    if missing:
        raise RuntimeError(
            "the copy is missing " + ", ".join(repr(m) for m in missing)
            + " - without it the copied cookies cannot be decrypted")
    default = os.path.join(dest, "Default")
    if not os.path.isdir(default):
        raise RuntimeError("the copy has no 'Default' profile directory")
    if not _has_session(default):
        raise RuntimeError(
            "the copy has no cookie store, so it would start signed out")


def normalise_local_state(dest):
    """Make the copied `Local State` agree with the copied profile.

    The source may have had several profiles and named the chosen one
    `Profile 2`; the copy has exactly one and calls it `Default`. Left alone,
    the index would point `last_used` at a directory that is not there, which
    is how a browser ends up showing a profile picker instead of the page -
    with `--profile-directory=Default` on the command line, no less, so the
    symptom would look like nothing to do with profiles at all.

    `os_crypt` is deliberately untouched: it holds the key the copied cookies
    are encrypted with, and it is the whole reason the root is copied.
    """
    path = os.path.join(dest, LOCAL_STATE)
    try:
        with open(path, encoding="utf-8") as fh:
            state = json.load(fh)
    except (OSError, ValueError):
        return False
    if not isinstance(state, dict):
        return False

    profile = state.get("profile")
    if not isinstance(profile, dict):
        profile = {}
    info = profile.get("info_cache")
    entry = {}
    if isinstance(info, dict):
        # Keep whichever entry describes the profile we copied, so the display
        # name survives; fall back to any entry, then to nothing.
        for name in ("Default",):
            if isinstance(info.get(name), dict):
                entry = info[name]
                break
        if not entry:
            for value in info.values():
                if isinstance(value, dict):
                    entry = value
                    break
    profile["info_cache"] = {"Default": entry}
    profile["last_used"] = "Default"
    profile["last_active_profiles"] = ["Default"]
    state["profile"] = profile

    tmp = f"{path}.tmp-{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh)
    os.replace(tmp, path)
    return True


def apply_automation_preferences(dest, seed_preferences, verbose=True):
    """Merge the settings automation needs into the COPIED Preferences.

    A merge, not a replacement. The copied file is the user's real profile
    configuration - their sites, their permissions, their certificates
    decisions - and that is most of what makes copying worth doing. Only the
    handful of keys in `seed_preferences` are forced, each for a reason
    recorded where that constant is defined (password bubble over the ADFS
    form, popups for AD SSO's window.open, no download prompt, no
    "restore pages?" bubble).

    Runs once, before the browser has ever opened the copy - the same
    discipline as `seed_automation_profile()`, and for the same reason: Chrome
    rewrites Preferences on exit, so doing this to a live profile would throw
    away the session it has earned.
    """
    path = os.path.join(dest, "Default", "Preferences")
    try:
        with open(path, encoding="utf-8") as fh:
            prefs = json.load(fh)
        if not isinstance(prefs, dict):
            raise ValueError("Preferences is not an object")
    except (OSError, ValueError) as e:
        # Starting from {} is the right move - the automation settings matter
        # more than the copied ones and the profile still works - but it
        # quietly discards the user's real configuration, which is most of why
        # copying was worth doing. Say so rather than let it look intentional.
        if verbose:
            print(f"  note: the copied Preferences could not be read ({e}); "
                  "starting from the\n  automation defaults only. The session "
                  "and cookies are unaffected.")
        prefs = {}

    _deep_merge(prefs, seed_preferences)

    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp-{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(prefs, fh)
    os.replace(tmp, path)
    return True


def _deep_merge(target, updates):
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_merge(target[key], value)
        else:
            target[key] = value
    return target


# ---------------------------------------------------------------------------
# First run
# ---------------------------------------------------------------------------

STAGING_PREFIX = ".bootstrap-"


def _staging_dir(profile_dir):
    return os.path.join(os.path.dirname(profile_dir),
                        f"{STAGING_PREFIX}{os.getpid()}")


def _clear_staging(path, expected_parent, verbose=True):
    """Remove a staging directory - and only a staging directory.

    The one place in this project that deletes a directory tree, so the fences
    are checked rather than described:

      1. the name must carry the staging prefix;
      2. it must sit DIRECTLY inside the directory that holds the automation
         profile - so no `GMES_PROFILE_DIR` mistake can point this at a tree
         somewhere else;
      3. it must be a real directory, not a link to one.

    An onboarded profile, the protected `CDP Profile` and the user's real
    profile can none of them satisfy (1), and nothing outside the profile
    parent can satisfy (2) (CLAUDE.md 2.1 / 2.1a).

    Returns True only if the directory is actually GONE afterwards. `rmtree`
    with `ignore_errors` is silent about failure, and a Chrome profile
    routinely contains paths past MAX_PATH that robocopy can create and
    `rmtree` cannot remove - so reporting "cleared" without looking would be
    this project's characteristic bug in miniature (CLAUDE.md 3.5)."""
    normalised = os.path.normpath(path)
    base = os.path.basename(normalised)
    if not base.startswith(STAGING_PREFIX):
        return False
    if not same_path(expected_parent, os.path.dirname(normalised)):
        return False
    if not os.path.isdir(normalised) or os.path.islink(normalised):
        return False

    shutil.rmtree(normalised, ignore_errors=True)
    if os.path.isdir(normalised):
        if verbose:
            print(f"  could not fully remove an interrupted first-run copy: "
                  f"{normalised}\n  (it is harmless - nothing launches it - "
                  "but it can be deleted by hand)")
        return False
    if verbose:
        print(f"  cleared an interrupted first-run copy: {normalised}")
    return True


def sweep_staging(profile_dir, verbose=True):
    """Tidy up staging directories left by an interrupted earlier run.

    An interrupted first run leaves a partial copy. It is never promoted -
    promotion is a rename that happens only after the copy has been verified -
    so the worst it can do is take up disk. Cleared here so a retry starts
    clean rather than merging into half a profile."""
    parent = os.path.dirname(profile_dir)
    try:
        entries = os.listdir(parent)
    except OSError:
        return 0
    cleared = 0
    for entry in entries:
        if entry.startswith(STAGING_PREFIX):
            if _clear_staging(os.path.join(parent, entry), parent, verbose=verbose):
                cleared += 1
    return cleared


def _directory_has_content(path):
    try:
        # The iterator has to be closed. `any()` short-circuits on the first
        # entry and leaves the handle open, which on Windows keeps the
        # directory itself busy - and the very next thing this code may do is
        # rmdir/replace that directory.
        with os.scandir(path) as entries:
            return any(entries)
    except OSError:
        return False


def bootstrap_disabled():
    """`GMES_BOOTSTRAP=off` opts out of copying entirely.

    The escape hatch for anyone who would rather not have their browser
    profile copied at all: the tool then behaves exactly as it did in Phase
    73, building an empty profile and signing in once."""
    value = (os.environ.get("GMES_BOOTSTRAP") or "").strip().lower()
    return value in ("0", "off", "no", "false", "never")


def ensure_bootstrapped(profile_dir, seed_preferences, verbose=True):
    """Decide - once - which browser and profile the automation will use.

    Returns a dict describing the outcome:

        {"browser": "edge"|"chrome", "executable": path|None,
         "profile_dir": path, "strategy": ..., "first_run": bool,
         "source": {...}|None}

    `strategy` is one of:

        "recorded"  a completed first run on this machine; nothing was copied
        "existing"  a profile was already there from before this feature
        "copied"    first run, and the user's own profile was copied into it
        "fresh"     first run, and there was nothing usable to copy from

    The only branch that touches the user's real profile is "copied", and it
    reads. Every other branch is pure bookkeeping.

    **It never copies twice.** Once a run has completed, the record says so
    and this returns "recorded" without looking at a real profile at all -
    which is what stops a nightly job re-copying a 300 MB profile every night,
    and more importantly stops it overwriting the session the automation has
    since earned for itself.
    """
    # Before ANY filesystem work. `launch_automation_chrome()` has this guard
    # too, but it runs after this function has already swept, created and
    # renamed directories - so a `GMES_PROFILE_DIR` pointing into a real
    # browser profile would be operated on first and refused second
    # (CLAUDE.md 2.1).
    real = protected_match(profile_dir)
    if real:
        raise RuntimeError(
            f"Refusing to build the automation profile inside the real "
            f"{spec(real)['short']} profile ({profile_dir!r}). Point "
            "GMES_PROFILE_DIR somewhere of its own.")

    recorded = recorded_profile_dir()
    if recorded:
        return {"browser": recorded_browser() or preferred_installed_browser(),
                "executable": None, "profile_dir": recorded,
                "strategy": "recorded", "first_run": False, "source": None}

    state = read_state()
    if state.get("completed") and state.get("machine") != machine_id():
        # The record came from another PC. Chrome 140+ binds cookie encryption
        # to the machine, so that copy decrypts nothing here - and reusing it
        # would present as a mysteriously signed-out session rather than an
        # error. Start this machine's own, beside it. Nothing is deleted.
        if verbose:
            print("NOTE: the recorded browser profile was set up on a different "
                  "PC, so it cannot be reused here.\n"
                  "      Setting this machine up separately; nothing was removed.")
        profile_dir = os.path.join(os.path.dirname(profile_dir), machine_id())

    # A profile that is already there predates this feature (or is a completed
    # run whose record was lost). Either way it is the automation's own
    # profile, it may hold a hard-won session, and it is not ours to replace.
    if os.path.isdir(profile_dir) and _directory_has_content(profile_dir):
        # `state.get("browser")` is only trustworthy when THIS state actually
        # named one; a legacy or lost record must not silently become
        # "chrome" on a machine that may not have it (HISTORY.md Phase 78).
        outcome = {"browser": state.get("browser") or preferred_installed_browser(),
                   "executable": None, "profile_dir": profile_dir,
                   "strategy": "existing", "first_run": False, "source": None}
        _record(outcome)
        return outcome

    sweep_staging(profile_dir, verbose=verbose)

    if bootstrap_disabled():
        outcome = {"browser": preferred_installed_browser(), "executable": None,
                   "profile_dir": profile_dir, "strategy": "fresh",
                   "first_run": True, "source": None}
        if verbose:
            print("First run: GMES_BOOTSTRAP is off, so the tool will build an "
                  "empty profile and sign in once.")
        _record(outcome)
        return outcome

    try:
        sources = candidate_sources()
    except Exception as e:                      # discovery must never be fatal
        if verbose:
            print(f"  (could not inspect the installed browsers: {e!r})")
        sources = []

    if not sources:
        outcome = {"browser": preferred_installed_browser(), "executable": None,
                   "profile_dir": profile_dir, "strategy": "fresh",
                   "first_run": True, "source": None}
        if verbose:
            print("First run: no Chrome or Edge profile was found to start "
                  "from, so the tool will\n  build an empty profile and sign "
                  "in once.")
        _record(outcome)
        return outcome

    # No `is_running()` gate here, deliberately. It is a proxy signal and a
    # bad one - Edge is resident with no window open on a normal Windows 11
    # machine (see `is_running`), so gating on it would refuse the first run
    # for the browser this feature most needs to support. The copy is
    # attempted and the lock itself is the evidence.
    errors, locked = [], None
    for source in sources:
        try:
            outcome = _copy_into_place(source, profile_dir,
                                       seed_preferences, verbose=verbose)
            _record(outcome)
            return outcome
        except ProfileLocked as e:
            # Remembered, not raised yet: another browser may still work, and
            # copying the session the employee actually uses is worth more
            # than failing fast. If nothing works, this is the message to give
            # them, because it is the only one they can act on.
            locked = locked or e
            errors.append(f"{source['label']}: profile in use")
            if verbose:
                print(f"  {source['label']} has its profile open; "
                      "trying anything else available first.")
        except Exception as e:
            # A source that cannot be copied is not the end of the first run;
            # the other browser, and ultimately the empty profile, still work.
            errors.append(f"{source['label']}: {e}")
            if verbose:
                print(f"  could not use {source['label']}: {e}")

    # A lock is the one failure a person can actually fix, and fixing it keeps
    # the session that makes the first report instant. Stop and say so rather
    # than quietly falling back to an empty profile and a real sign-in.
    if locked:
        raise locked

    outcome = {"browser": preferred_installed_browser(), "executable": None,
               "profile_dir": profile_dir, "strategy": "fresh",
               "first_run": True, "source": None, "errors": errors}
    if verbose:
        print("  falling back to an empty profile; the first sign-in will be "
              "a real one.")
    _record(outcome)
    return outcome


def _copy_into_place(source, profile_dir, seed_preferences, verbose=True):
    """Copy into a staging directory, then promote it with one rename.

    The rename is what makes an interrupted first run safe. A half-finished
    copy lives under a name nothing will ever launch, and only a copy that has
    been verified is moved to the name the launcher uses - so the failure mode
    of losing power halfway through is "first run did not happen yet", not "a
    broken profile is now the one we drive"."""
    parent = os.path.dirname(profile_dir)
    staging = _staging_dir(profile_dir)
    _clear_staging(staging, parent, verbose=False)
    os.makedirs(parent, exist_ok=True)

    if verbose:
        print(f"First run: setting up from your {source['label']} profile "
              f"{source['profile']['name']!r}.")
        print("  Your own browser profile is only read - never changed, never "
              "driven, never deleted.")

    try:
        copy_profile(source, staging, verbose=verbose)
        if not normalise_local_state(staging):
            # Not cosmetic. `_verify_copy()` only proves `Local State` is
            # PRESENT; an unreadable one passes that and then leaves the index
            # pointing at a profile directory the copy does not have, which is
            # how a browser ends up showing a profile picker instead of the
            # page - with --profile-directory=Default on the command line, so
            # it looks like anything but a profile problem. Fail the copy and
            # let the caller try the next source.
            raise CopyFailed(
                "the copied 'Local State' could not be read, so the profile "
                "index could not be pointed at the copied profile")
        apply_automation_preferences(staging, seed_preferences, verbose=verbose)
        # os.replace onto an EXISTING directory raises on Windows, even when
        # that directory is empty - and an empty profile directory is exactly
        # what an earlier interrupted attempt can leave. Without this the
        # promotion fails, the run falls back to "fresh", and
        # `seed_automation_profile()` then declines to seed because the
        # directory already exists - leaving an unseeded profile that looks
        # deliberate. `os.rmdir` cannot remove a directory with anything in
        # it, so this can only ever clear the empty case.
        if os.path.isdir(profile_dir):
            os.rmdir(profile_dir)
        os.replace(staging, profile_dir)
    except BaseException:
        _clear_staging(staging, parent, verbose=False)
        raise

    if verbose:
        print(f"  ready: {profile_dir}")
        print("  If G-MES is already signed in there, this run will go "
              "straight through.")

    return {"browser": source["browser"], "executable": source["executable"],
            "profile_dir": profile_dir, "strategy": "copied",
            "first_run": True, "source": {
                "user_data_dir": source["user_data_dir"],
                "profile": source["profile"]["dir"],
                "profile_name": source["profile"]["name"]}}


def _record(outcome):
    """Write the outcome down, so the next run does none of this again."""
    try:
        write_state({
            "version": STATE_VERSION,
            "completed": True,
            "machine": machine_id(),
            "browser": outcome["browser"],
            "executable": outcome.get("executable"),
            "profile_dir": outcome["profile_dir"],
            "strategy": outcome["strategy"],
            "source": outcome.get("source"),
            "onboarded_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        })
    except OSError as e:
        # Not fatal. Without the record the next run re-derives the same
        # answer from the profile that is now on disk ("existing"), which is
        # the correct outcome anyway - it just costs a directory check.
        print(f"(could not record the browser choice: {e})")


# ---------------------------------------------------------------------------
# Read-only self-check
# ---------------------------------------------------------------------------

def report():
    default = default_browser_key()
    lines = [f"Windows default browser : {default or 'not Chrome or Edge'}"]
    for key in SUPPORTED:
        info = spec(key)
        executable = find_executable(key)
        lines.append("")
        lines.append(f"{info['label']}")
        lines.append(f"  executable : {executable or 'not found'}")
        user_data = real_user_data_dir(key)
        lines.append(f"  user data  : {user_data}"
                     f"{'' if os.path.isdir(user_data) else '  (not present)'}")
        for profile in list_profiles(user_data_dir=user_data):
            marks = []
            if profile["last_used"]:
                marks.append("last used")
            if profile["has_session"]:
                marks.append("has cookies")
            if profile["has_gmes_evidence"]:
                marks.append("has used G-MES")
            elif profile["has_gmes_evidence"] is None:
                marks.append("G-MES use unknown")
            lines.append(f"    - {profile['dir']:<12} {profile['name']!r}"
                         f"{'  [' + ', '.join(marks) + ']' if marks else ''}")

    state = read_state()
    lines.append("")
    lines.append(f"First-run record: {state_path()}")
    if not state:
        lines.append("  (none yet - the next run will set one up)")
    else:
        same = state.get("machine") == machine_id()
        lines.append(f"  browser    : {state.get('browser')}")
        lines.append(f"  profile    : {state.get('profile_dir')}")
        lines.append(f"  strategy   : {state.get('strategy')}")
        lines.append(f"  onboarded  : {state.get('onboarded_at')}")
        lines.append(f"  this PC    : {same}"
                     + ("" if same else "   <- recorded on a different machine"))
    return "\n".join(lines)


if __name__ == "__main__":
    print(report())

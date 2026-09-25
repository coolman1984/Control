"""
Read-only preflight check: is this machine ready to run the G-MES automation
at all, before the first real sign-in spends any of the account's patience
on a problem this could have caught in under a second.

    python gmes_preflight.py

`GMES_Workflow.bat` runs this before doing anything else - the same
"`where python` failed, stop here" discipline the launcher already had,
generalised to the checks a fresh machine actually needs:

  1. Python version           - a floor this project's own syntax needs.
  2. websocket-client         - the one third-party dependency; everything
                                 else is stdlib (CLAUDE.md 4.5).
  3. A supported browser      - Chrome or Edge (gmes_browsers.py).
  4. The runtime directory is writable - credentials.dat, the browser
                                 profile, and the first-run record all live
                                 under %LOCALAPPDATA%\\GMES_Automation.

Exit code 0 if every check passes, 1 otherwise, and every check still runs
and reports even after an earlier one fails - so fixing one problem does not
mean running this five times to discover the next.

**What this deliberately does NOT check: whether Chrome or Edge will actually
answer on a CDP port, or whether AD SSO will complete.** Either can only be
proven by actually launching the browser and signing in - which is the
expensive step this tool exists to fail BEFORE, not repeat. This is a
preflight, not a dry run.
"""
import importlib.util
import os
import sys

MIN_PYTHON = (3, 10)


def check_python_version():
    # Indexed, not `.major`/`.minor`/`.micro`: the real sys.version_info
    # supports both, but comparing it against the plain tuple MIN_PYTHON
    # already relies on tuple semantics, so working entirely in tuple terms
    # keeps this testable with an ordinary tuple standing in for it.
    have = tuple(sys.version_info[:3])
    ok = have >= MIN_PYTHON
    have_str = ".".join(str(v) for v in have)
    need_str = ".".join(str(v) for v in MIN_PYTHON)
    if ok:
        return True, f"Python {have_str} (>= {need_str})"
    return False, (f"Python {have_str} is older than {need_str}. Install a "
                   "newer Python 3 and run the scripts with it.")


def check_websocket_client():
    if importlib.util.find_spec("websocket") is None:
        return False, ("websocket-client is not installed. Install it with:  "
                       f"{sys.executable} -m pip install -r requirements.txt")
    try:
        import websocket
        version = getattr(websocket, "__version__", None) or "installed"
    except Exception as e:
        return False, f"found but could not import websocket-client ({e})"
    return True, f"websocket-client {version}"


def check_browser():
    try:
        import gmes_browsers
    except Exception as e:
        return False, f"could not import gmes_browsers.py ({e})"
    found = []
    for key in gmes_browsers.SUPPORTED:
        path = gmes_browsers.find_executable(key)
        if path:
            found.append(f"{gmes_browsers.spec(key)['short']} ({path})")
    if found:
        return True, ", ".join(found)
    return False, ("Neither Chrome nor Edge was found. Install one of them, "
                   "or set CHROME_PATH / EDGE_PATH to its full path.")


def check_runtime_directory():
    try:
        import gmes_browsers
    except Exception as e:
        return False, f"could not import gmes_browsers.py ({e})"
    root = gmes_browsers.AUTOMATION_ROOT
    probe = os.path.join(root, ".preflight-write-test")
    try:
        os.makedirs(root, exist_ok=True)
        with open(probe, "w", encoding="utf-8") as fh:
            fh.write("ok")
        os.unlink(probe)
    except OSError as e:
        return False, (f"cannot write to {root!r} ({e}). Credentials, the "
                       "browser profile and the first-run record all live "
                       "there - check permissions on %LOCALAPPDATA%.")
    return True, root


# ---------------------------------------------------------------------------
# New-machine checks (HISTORY.md Phase 84.15). Each returns (ok, detail) where ok
# is True (fine), False (blocks the tool) or None (a WARNING: it may bite, it does
# not block). They came from a clean-machine rehearsal and from research, not from
# a list: `git clone` itself failed at a 221-character path, a scheduled launcher
# died on an Arabic install path, and Windows refuses a 260-character path unless
# long paths are switched on.
# ---------------------------------------------------------------------------

# The longest file the tool writes under the project, apart from the project path:
#   \Data Hub Folder\GMES\batch_YYYYMMDD_HHMMSS\  (45)
#   .gmes-download-<32 hex>_<title>_<14 digits>.xlsx  (15 + 32 + 1 + title + 1 + 14 + 5)
# A title is capped at gmes_core.SAFE_NAME_MAX; 60 is a long real one.
_FOLDER_PART = 45
_STAGING_PART = 15 + 32 + 1 + 1 + 14 + 5
_ASSUMED_TITLE = 60
_MAX_PATH_USABLE = 259

_SYNC_MARKERS = ("onedrive", "dropbox", "google drive", "googledrive", "icloud", "box sync", "\\box\\")


def _long_paths_enabled(read=None):
    if read is not None:
        return read()
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SYSTEM\CurrentControlSet\Control\FileSystem") as key:
            return winreg.QueryValueEx(key, "LongPathsEnabled")[0] == 1
    except (ImportError, OSError):
        return False


def check_install_path(root=None, long_paths=None):
    """The project's own path: too long for Windows, or inside a sync folder."""
    root = root or os.path.dirname(os.path.abspath(__file__))
    worst = len(root) + _FOLDER_PART + _STAGING_PART + _ASSUMED_TITLE
    problems = []
    if worst > _MAX_PATH_USABLE and not _long_paths_enabled(long_paths):
        problems.append(
            f"the project path is {len(root)} characters; with a {_ASSUMED_TITLE}-character report "
            f"title the longest file would be {worst}, over Windows' {_MAX_PATH_USABLE}-character "
            "limit, and the export would fail AFTER the query ran. Move the project to a short "
            "folder such as C:\\gmes (git clone also fails on long paths unless "
            "`git config --global core.longpaths true`)")
    lowered = root.lower()
    hit = next((m for m in _SYNC_MARKERS if m in lowered), None)
    if hit:
        problems.append("the project is inside a cloud-sync folder: the sync client holds "
                        "fresh exports open (WinError 32) and lengthens every path - a folder "
                        "outside it, such as C:\\gmes, is safer")
    if problems:
        return None, "; ".join(problems)
    return True, f"{len(root)} characters, not in a sync folder"


def check_disk_space(root=None, free=None, minimum=2 * 1024 ** 3):
    root = root or os.path.dirname(os.path.abspath(__file__))
    try:
        free = free if free is not None else __import__("shutil").disk_usage(root).free
    except OSError as e:
        return None, f"could not read the free space ({e})"
    gb = free / 1024 ** 3
    if free < minimum:
        return None, (f"only {gb:.1f} GB free where exports are written - a full disk "
                      "fails a run after its query")
    return True, f"{gb:.0f} GB free"


def check_credentials_present(path=None):
    """Existence only - the file is never opened here (CLAUDE.md 2.1a)."""
    if path is None:
        import gmes_credentials
        path = gmes_credentials.STORE_PATH
    if os.path.isfile(path):
        return True, "a saved sign-in exists (not read)"
    return None, ("no saved sign-in yet - the first run cannot sign in until you run:  "
                  f"{sys.executable} gmes_credentials.py set")


_POLICY_KEYS = {"chrome": r"SOFTWARE\Policies\Google\Chrome",
                "edge": r"SOFTWARE\Policies\Microsoft\Edge"}


def _remote_debugging_policy(browser, read=None):
    """0 when policy forbids remote debugging for this browser, else None."""
    if read is not None:
        return read(browser)
    try:
        import winreg
    except ImportError:
        return None
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(hive, _POLICY_KEYS[browser]) as key:
                value = winreg.QueryValueEx(key, "RemoteDebuggingAllowed")[0]
                if value == 0:
                    return 0
        except OSError:
            continue
    return None


def check_remote_debugging_policy(installed=None, read=None):
    """`RemoteDebuggingAllowed = 0` (a Chrome/Edge policy, common on hardened
    corporate images and in CIS benchmarks) makes the browser ignore the debugging
    port this tool drives it with - the run then fails late with a generic timeout
    (HISTORY.md Open Item 43)."""
    if installed is None:
        try:
            import gmes_browsers
            installed = [k for k in gmes_browsers.SUPPORTED if gmes_browsers.find_executable(k)]
        except Exception:                                 # noqa: BLE001
            installed = []
    blocked = [b for b in installed if _remote_debugging_policy(b, read) == 0]
    if not installed:
        return True, "no browser to check"
    if len(blocked) == len(installed):
        return False, (f"policy RemoteDebuggingAllowed=0 is set for {', '.join(blocked)}: the "
                       "browser will refuse the debugging port this tool needs - ask IT for an "
                       "exception; nothing on this PC can fix it")
    if blocked:
        return None, f"policy blocks remote debugging in {', '.join(blocked)}; the other browser is fine"
    return True, "no policy blocks remote debugging"


def check_python_source(executable=None):
    executable = executable or sys.executable
    if "\\windowsapps\\" in executable.lower():
        return None, ("this Python comes from the Microsoft Store (an app-execution alias). It "
                      "runs interactively, but scheduled tasks and some environments cannot "
                      "always start it - if a scheduled run never starts, install Python from "
                      "python.org (not verified on this project's machines)")
    return True, executable


# Order matters only in that each is independent - nothing here depends on an
# earlier check having passed, deliberately, so one failure never hides
# another (HISTORY.md Phase 79 - the review that asked for this named
# exactly that gap: GMES_Workflow.bat's only check was `where python`).
CHECKS = (
    ("Python version", check_python_version),
    ("websocket-client", check_websocket_client),
    ("Browser (Chrome or Edge)", check_browser),
    ("Runtime directory writable", check_runtime_directory),
    ("Browser policy", check_remote_debugging_policy),
    ("Install location", check_install_path),
    ("Free disk space", check_disk_space),
    ("Saved sign-in", check_credentials_present),
    ("Python source", check_python_source),
)


def run(verbose=True):
    """Run every check, in order, and keep going past a failure.

    Returns True only if every single one passed. A check that raises
    something its own try/except did not anticipate is caught here too -
    a bug in this tool must never look like "everything is fine" by
    accident, and must never itself crash the launcher it is meant to
    protect."""
    all_ok = True
    for name, check in CHECKS:
        try:
            ok, detail = check()
        except Exception as e:
            ok, detail = False, f"check itself raised: {e!r}"
        # None is a WARNING: shown, never blocking (only False stops the launcher).
        all_ok = all_ok and ok is not False
        if verbose:
            mark = "OK  " if ok else ("WARN" if ok is None else "FAIL")
            print(f"  [{mark}] {name:<28} {detail}")
    return all_ok


def main():
    print("G-MES preflight check")
    print("=" * 70)
    ok = run()
    print("=" * 70)
    if ok:
        print("Ready.")
        return 0
    print("Not ready - fix the FAIL line(s) above before running a report.")
    return 1


if __name__ == "__main__":
    sys.exit(main())

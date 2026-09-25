"""
The core of G-MES control: one screen, driven completely, without the tool
having been taught that screen first.

This is a library, not a command. The commands are thin callers:

    gmes_report.py          non-interactive: describe / run any UI number
    run_gmes_workflow.py    the by-hand front end
    gmes_daily_prodplan.py  the one hardcoded nightly job

What "control" means here
-------------------------
READ-ONLY. This module opens a screen, sets everything the screen offers,
runs its Inquiry, verifies what came back and exports it. It never clicks
Save, Submit, Approve or Delete, and it has no code path that could
(CLAUDE.md 2.5). Every write it performs goes into a *filter*, which is
input to a query.

The five mechanisms that make any of the 809 screens reachable
--------------------------------------------------------------
1. `gdsMenuList` is a client-side catalogue: menuId <-> sysScreenId <->
   English name <-> breadcrumb. The top search box opens any of them by
   code, so no menu walking (gmes_open_screen.py).
2. `_findForms(screenCode)` walks Nexacro's `_frames` AND its nested
   `Div.form` trees, so a screen is addressable by its stable code while the
   window id (winPPM0219_0_516) is renumbered on every open.
3. `form.binds` maps each control to the dataset column behind it, so a
   screen describes its own filters instead of being taught them.
4. Controls the screen does NOT bind are still typed into, with real key
   events, because a programmatic value assignment fills the box and changes
   nothing Nexacro can see.
5. Results come from the Dataset, never the grid: a Nexacro grid only builds
   the rows you can see, so reading the page turns 5,000 rows into 20 and
   reports success.

The order of operations is not arbitrary
----------------------------------------
    options -> organisation -> dates -> clear stale -> filters -> Inquiry

Switching a category tab or a Quick View REBUILDS the left panel and
discards whatever was set before it, so the panel options go first. Stale
values are cleared after the dates because the dates are values this run
asked for. See GMES_SKILL.md #32 and #34.

Everything here verifies its own outcome. On these systems the normal
failure produces no error at all: a filter that did not take, a query
answered from the previous screen's dataset, an export of the wrong day.
"""
import json
import os
import re
import shutil
import tempfile
import threading
import time
import unicodedata
import uuid
from datetime import datetime, timedelta, timezone

import cdp_common
from cdp_common import click_element_by_rect, dispatch_key_combo, evaluate, send
import gmes_common
import gmes_data
import gmes_login
import gmes_open_screen
import gmes_profile
from gmes_common import connect_gmes

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "Data Hub Folder", "GMES")

# Two gmes_report.py/run_gmes_workflow.py processes share ONE Chrome/CDP
# session (CLAUDE.md section 0) with nothing isolating them from each
# other. Live-proven: running two at once, the second process's screen-open
# landed on a row the first process's screen had made temporarily not
# visible, and failed with "The result row could not be clicked (grid row
# not visible)" - a real symptom that gives no hint a second run is the
# cause. This lock turns that into an immediate, explicit refusal instead.
#
# Keyed to the PROFILE this run actually drives (cdp_common.active_profile_dir()),
# not to this checkout's own screens/ directory (HISTORY.md Open Item 40): two
# separate checkouts sharing one %LOCALAPPDATA%\GMES_Automation profile - the
# exact scenario a clean-machine rehearsal deliberately creates (Phase 84) -
# used to each hold their own lock file inside their own repo and never see
# each other, so the lock protected "two runs from THIS checkout" rather than
# "two runs against this profile", which is the resource that actually matters.
def run_lock_path():
    return os.path.join(cdp_common.active_profile_dir(), ".gmes_run.lock")


class RunLocked(RuntimeError):
    """Another G-MES run already holds this lock."""


def _pid_alive(pid):
    if os.name == "nt":
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


LOCK_MAX_AGE_HOURS = 8           # the scheduled task's own limit is 6 h; nothing legitimate holds it longer
LOCK_UNREADABLE_MINUTES = 10     # an empty/garbled lock this old is not being written any more

# A lock written by this version carries a HEARTBEAT (HISTORY.md Phase 92.2):
# while it is held, a background thread touches the file every
# LOCK_HEARTBEAT_SECONDS. Its age then means "time since the holder last
# proved it is alive", not "time since the run started" - so a lock whose
# heartbeat stopped is known to be abandoned after minutes instead of 8 hours,
# and a genuinely long run is never mistaken for an abandoned one just for
# being long. Locks without the marker (an older version's) keep the 8-hour rule.
LOCK_HEARTBEAT_SECONDS = 60
LOCK_HEARTBEAT_STALE_MINUTES = 5          # five missed beats
LOCK_HEARTBEAT_MARK = "heartbeat"
_heartbeats = {}                          # token -> threading.Event that stops its beat


def _process_image(pid):
    """The executable name of a running process (lower case), or '' when it
    cannot be read. Used only to tell "the run that made this lock" from "some
    other program that was handed its recycled PID"."""
    if os.name != "nt":
        return ""
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    handle = kernel32.OpenProcess(0x1000, False, pid)        # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(1024)
        buffer = ctypes.create_unicode_buffer(1024)
        ok = kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size))
        return os.path.basename(buffer.value).lower() if ok else ""
    finally:
        kernel32.CloseHandle(handle)


def lock_is_stale(holder, age_seconds, alive, image=""):
    """Should this lock file be ignored? -> (stale, why).

    HISTORY.md Phase 84.13, found by probing lock files: `_pid_alive()` was the
    only test, so
      * an EMPTY lock (a crash between creating it and writing the pid) or a
        garbled one refused every run forever - a scheduled night would exit 3
        again and again until someone read the log;
      * a lock whose pid Windows had since handed to an unrelated program
        (explorer, a browser...) was "alive" and refused every run forever - PIDs
        are recycled within hours;
    Pure, so every rule has a test. An unreadable lock that is RECENT is still
    respected: another run may be writing it at this instant."""
    try:
        pid = int(str(holder).split()[0])
    except (ValueError, IndexError):
        pid = None
    if pid is None or pid <= 0:
        if age_seconds > LOCK_UNREADABLE_MINUTES * 60:
            return True, f"it holds no process id and is over {LOCK_UNREADABLE_MINUTES} minutes old"
        return False, "it holds no process id yet"
    if not alive:
        return True, f"process {pid} is no longer running"
    if image and not image.startswith("py"):
        return True, f"process {pid} is now {image}, not a run of this tool (the number was reused)"
    if LOCK_HEARTBEAT_MARK in str(holder).split("\t")[3:4]:
        if age_seconds > LOCK_HEARTBEAT_STALE_MINUTES * 60:
            return True, (f"its heartbeat stopped {int(age_seconds // 60)} minutes ago - "
                          "the run that held it is frozen or gone")
        return False, ""
    if age_seconds > LOCK_MAX_AGE_HOURS * 3600:
        return True, f"it is over {LOCK_MAX_AGE_HOURS} hours old - no run lasts that long"
    return False, ""


def _move_aside(lock_path, expected):
    """Take a stale lock out of the way ATOMICALLY, or return False.

    HISTORY.md Phase 92.3 (Open Item 85): this used to be read, judge, then
    `os.unlink(lock_path)` - two runs that both judged the same old lock stale
    could each unlink, and the slower one deleted the lock the faster one had
    just created, so both drove the browser. A rename is atomic and only one
    caller can win it; the winner then checks it moved the file it JUDGED
    (`expected`) and not a fresh lock another run created in between - if it
    got the wrong one it puts it back, never over a file already there."""
    aside = f"{lock_path}.stale-{uuid.uuid4().hex}"
    try:
        os.rename(lock_path, aside)
    except OSError:
        return False                      # gone already, or another run moved it first
    try:
        with open(aside, encoding="utf-8", errors="replace") as fh:
            moved = fh.read().strip()
    except OSError:
        moved = None
    if moved != expected:
        try:
            os.link(aside, lock_path)     # put it back - fails rather than overwrite
        except OSError:
            pass
        try:
            os.unlink(aside)
        except OSError:
            pass
        return False
    try:
        os.unlink(aside)
    except OSError:
        pass                              # a leftover .stale-* file blocks nothing
    return True


def _start_heartbeat(lock_path, token):
    stop = threading.Event()

    def beat():
        while not stop.wait(LOCK_HEARTBEAT_SECONDS):
            if _lock_token(lock_path) != token:
                return                    # no longer ours - never touch someone else's lock
            try:
                os.utime(lock_path, None)
            except OSError:
                pass

    threading.Thread(target=beat, name="gmes-run-lock-heartbeat", daemon=True).start()
    _heartbeats[token] = stop


def acquire_run_lock(_retry=True):
    """Claim this profile's run lock for this process, or raise RunLocked.

    os.O_EXCL makes the create-if-absent check and the create itself one
    atomic filesystem operation, so two processes racing to start at the
    same instant cannot both believe they got the lock.

    Returns an OWNERSHIP TOKEN (an opaque nonce), not a bare `True` - pass it
    to `release_run_lock(token)` so a release only ever removes the lock THIS
    call created. Without that, `release_run_lock()` used to delete whatever
    file was sitting at the path unconditionally: if this process's own lock
    had since been judged stale and reclaimed by a NEW run (the every-8-hour/
    dead-pid cases just above), this process finishing later would delete the
    new owner's live lock, not its own already-gone one.
    """
    lock_path = run_lock_path()
    os.makedirs(os.path.dirname(lock_path), exist_ok=True)
    token = uuid.uuid4().hex
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        holder = ""
        try:
            with open(lock_path, encoding="utf-8", errors="replace") as fh:
                holder = fh.read().strip()
        except OSError:
            pass
        try:
            age = max(0.0, time.time() - os.path.getmtime(lock_path))
        except OSError:
            age = 0.0
        try:
            pid = int(holder.split()[0])
        except (ValueError, IndexError):
            pid = None
        alive = bool(pid and pid > 0 and _pid_alive(pid))
        stale, why = lock_is_stale(holder, age, alive, _process_image(pid) if alive else "")
        if stale and _retry:
            # Crashed, killed, frozen, or its number was reused: a stale lock
            # must not block every run after it forever.
            if _move_aside(lock_path, holder):
                print(f"  (an old run lock was removed: {why})")
            return acquire_run_lock(_retry=False)
        raise RunLocked(
            "Another G-MES run already has the browser "
            f"(lock held by pid {holder or 'unknown'}). Two runs sharing one Chrome/CDP "
            "session interfere with each other - wait for it to finish, or "
            f"delete {lock_path} if you are sure it is not really running.")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(f"{os.getpid()}\t{datetime.now():%Y-%m-%d %H:%M:%S}\t{token}"
                 f"\t{LOCK_HEARTBEAT_MARK}\n")
    _start_heartbeat(lock_path, token)
    return token


def _lock_token(lock_path):
    try:
        with open(lock_path, encoding="utf-8", errors="replace") as fh:
            parts = fh.read().strip().split("\t")
    except OSError:
        return None
    return parts[2].strip() if len(parts) >= 3 else None


def release_run_lock(token=None):
    """Release this process's run lock.

    `token` is what `acquire_run_lock()` returned. When given, the lock is
    removed only if it still names THAT token - never a different run's lock
    that has since taken the same path (see `acquire_run_lock()`'s docstring).
    `token=None` is the permissive fallback for a caller with no token to
    check (manual cleanup, a test's `addCleanup`) and removes the lock
    unconditionally, exactly like before this ownership check existed."""
    lock_path = run_lock_path()
    for held, stop in list(_heartbeats.items()):
        if token is None or held == token:
            stop.set()
            _heartbeats.pop(held, None)
    if token is not None and _lock_token(lock_path) != token:
        return
    try:
        os.unlink(lock_path)
    except OSError:
        pass

# The only ids that may be hardcoded: the shell has exactly one of each.
EXCEL_BTN = "mainframe.vFrameSet1.vFrameSet2.mdiFrame.form.btnExcel"
TAB_PREFIX = "mainframe.vFrameSet1.vFrameSet2.mdiFrame.form.divTab.form.TAB_"

# Nexacro names a control by its type, and that prefix is the only reliable
# way to tell an input from a read-only display.
INPUT_PREFIXES = ("edt", "msk", "cbo", "chk", "rdo", "cal", "spn", "txt", "lst")


# ===========================================================================
# Discovery
# ===========================================================================

JS_DISCOVER = r"""
(function() {
    %s
    const isVisible = %s;
    const screenCode = %s;

    const forms = _findForms(screenCode);
    if (!forms.length) return JSON.stringify({found: false, reason: 'no forms for this screen code'});

    // The work window is the ancestor whose name starts with "win". Keep the
    // full path INCLUDING the "application." prefix: _findForms returns paths
    // with it, so a stripped prefix silently matches nothing and the screen
    // looks as though it has no filters at all.
    const anchor = forms[0].path;
    const parts = anchor.split('.');
    const winIdx = parts.findIndex(p => /^win/.test(p));
    if (winIdx < 0) return JSON.stringify({found: false, reason: 'no work window'});
    const winPath = parts.slice(0, winIdx + 1).join('.');

    // A component path inside a form maps to a DOM id by inserting ".form"
    // after the window and after every nested Div.
    function domId(formPath, comp) {
        const p = formPath.replace(/^application\./, '').split('.');
        const i = p.findIndex(x => /^win/.test(x));
        if (i < 0) return null;
        let id = p.slice(0, i + 1).join('.') + '.form';
        for (const seg of p.slice(i + 1)) id += '.' + seg + '.form';
        return id + '.' + comp;
    }

    // The same trick JS_LEFT_OPTIONS already uses for option identity
    // (HISTORY.md Phase 76): a window id embeds an instance number that
    // changes on every open (winPPM0219_0_926 one visit, _0_315 the next),
    // so it can never be part of an identity meant to survive a reopen or
    // be written to a saved profile. Everything after it is authored in
    // the screen's XFDL and is the same for every user in every language.
    function relativePath(path) {
        const p = path.split('.');
        const i = p.findIndex(x => /^win.*_\d+_\d+$/.test(x));
        return i < 0 ? path : p.slice(i + 1).join('.');
    }

    // What a control currently shows. A focused Nexacro edit renders a real
    // <input>; an unfocused one is a div carrying the text.
    function shownValue(el) {
        if (!el) return '';
        if (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA') return el.value || '';
        const inner = el.querySelector('input, textarea');
        if (inner) return inner.value || '';
        return (el.textContent || '').trim();
    }

    // The label for a control: the nearest visible Static to its left on the
    // same line, else the nearest one directly above it.
    const statics = [];
    for (const el of document.querySelectorAll('div')) {
        const cls = (typeof el.className === 'string') ? el.className : '';
        if (!/\bStatic\b/.test(cls)) continue;
        if (!isVisible(el)) continue;
        const t = (el.textContent || '').trim();
        if (!t || t.length > 40) continue;
        // Calendar day cells are Statics too and sit right beside the date
        // boxes, which made the date filters come back labelled "6" and "8".
        if (/^[\d.,:\/-]+$/.test(t)) continue;
        if (t.length < 3) continue;
        if (/^(mon|tue|wed|thu|fri|sat|sun)\.?$/i.test(t)) continue;
        const r = el.getBoundingClientRect();
        statics.push({t: t, l: r.left, r: r.right, cy: r.top + r.height / 2, top: r.top});
    }
    function labelFor(rect) {
        let best = null, bestD = 1e9;
        for (const s of statics) {
            const sameLine = Math.abs(s.cy - (rect.top + rect.height / 2)) < 12;
            if (sameLine && s.r <= rect.left + 8) {
                const d = rect.left - s.r;
                if (d >= 0 && d < 260 && d < bestD) { best = s.t; bestD = d; }
            }
        }
        if (best) return best;
        for (const s of statics) {
            const above = rect.top - s.top;
            if (above > 0 && above < 46 && Math.abs(s.l - rect.left) < 130) {
                if (above < bestD) { best = s.t; bestD = above; }
            }
        }
        return best || '';
    }

    const INPUTS = %s;
    function kindOf(name) {
        const n = (name || '').toLowerCase();
        for (const p of INPUTS) if (n.indexOf(p) === 0) return 'input';
        if (/^(sta|img|btn|grd|div)/.test(n)) return 'display';
        return 'unknown';
    }
    // kindOf() guesses from the CONTROL'S NAME, which is only ever a
    // naming CONVENTION - live-caught on M3912UM00 (HISTORY.md Phase 82.7):
    // its filter panel binds fromDate/toDate/searchTypeCode/searchStartNo/
    // searchModelCode/searchUse/searchProductCode, none of which start with
    // any of INPUTS, so every one of its 7 real filters was silently
    // dropped - "Filters bound to a dataset (0)" on a screen with a visible
    // Period/Type/Product/Start No./Model panel. The element's own live
    // Nexacro type - MaskEdit, Combo, Edit, CheckBox, Radio, Spin, Calendar,
    // ListBox, TextArea, resolved from `el.className`'s first token exactly
    // like `kind` already is a few lines below - is authored by the
    // platform, not a person, and is checked FIRST wherever the element can
    // actually be resolved. The name-prefix guess survives only as the
    // fallback for a control JS_DISCOVER cannot resolve at all yet.
    const INPUT_KIND_RE = /^(edit|maskedit|combo|checkbox|radio|spin|calendar|listbox|textarea)$/i;
    function isInputControl(kind, name) {
        if (kind) return INPUT_KIND_RE.test(kind);
        return kindOf(name) === 'input';
    }
    // The shell's own forms carry binds, grids and trees that belong to the
    // FRAME, not to the report - the My Menu panel, the widget list, the
    // module bar. Observed leaking into a live describe of P1112UM00: 21 of
    // the 22 "category trees" and 4 of the 8 "result grids" were shell.
    //
    // WidgetFilter.xfdl.js was believed covered by this same comment (it IS
    // "the widget list") but never actually matched the pattern below - live
    // on P3151WM00 (HISTORY.md Phase 82.12) its grid, Grid02/dsGrid00, sat
    // on screen (unlike the screen's real, currently inactive-tab grids) and
    // so won as the default "biggest visible grid" pick in choose_grid(),
    // even though a read against it returns nothing: it belongs to the
    // divLeft filter panel every G-MES screen carries, not to the report.
    // Confirmed the same WidgetFilter/dsGrid00 pair recurs verbatim under
    // four different work windows in one live session, so this is generic
    // shell chrome, not a P3151WM00 peculiarity.
    const SHELL = /WorkMainTitle|WorkMain\.xfdl|WorkTemplate|MyMenu|TopMenu|LeftMenu|LeftMain|PortalMain|WidgetFilter/i;

    const filters = [], unbound = [], grids = [], datasets = {};
    const quickViews = [], seenQV = {};

    // Dataset ids that are UI CHROME, not a result set - an org tree
    // (commonName + _checked, gotcha #40) or the Quick View widget
    // (sysScreenId + menuId + quickViewId, gotcha #46) - collected by name
    // in one pass over every form in this window. A grid's OWN form does not
    // always carry the dataset it renders as an own property: Nexacro
    // resolves `binddataset` through the form's ancestor scope at render
    // time, so `grdWidgetList`'s form does not have `dsWidget` on it even
    // though it displays it - looking the id up only on the grid's own form
    // silently found nothing and let it straight through.
    const chromeDatasets = {};
    for (const h of _findForms(null)) {
        if (h.path.indexOf(winPath) !== 0) continue;
        let keys = [];
        try { keys = Object.keys(h.form); } catch (e) { continue; }
        for (const k of keys) {
            let d = null;
            try { d = h.form[k]; } catch (e) { continue; }
            if (!d || _typeName(d) !== 'Dataset') continue;
            const cols = [];
            try { const n = d.getColCount();
                  for (let i = 0; i < n; i++) cols.push(d.getColID(i)); } catch (e) { continue; }
            const isTree = cols.indexOf('commonName') >= 0 && cols.indexOf('_checked') >= 0;
            const isQV = cols.indexOf('sysScreenId') >= 0 && cols.indexOf('menuId') >= 0
                        && cols.indexOf('quickViewId') >= 0;
            if (isTree || isQV) chromeDatasets[k] = true;
        }
    }

    for (const h of _findForms(null)) {
        if (h.path.indexOf(winPath) !== 0) continue;   // only this work window

        try {
            for (const k of Object.keys(h.form)) {
                let d = h.form[k];
                if (!d || _typeName(d) !== 'Dataset') continue;
                const cols = [];
                try { const n = d.getColCount();
                      for (let i = 0; i < n; i++) cols.push(d.getColID(i)); } catch (e) {}
                datasets[k] = {rows: (function(){ try { return d.getRowCount(); } catch(e){ return -1; } })(),
                               cols: cols.length, form: h.file || ''};
            }
        } catch (e) {}

        // Bound controls -> settable through the dataset.
        try {
            const b = SHELL.test(h.file || '') ? null : h.form.binds;
            if (b && b.length !== undefined) {
                for (let i = 0; i < b.length; i++) {
                    const x = b[i];
                    const ds = String(x.datasetid || ''), col = String(x.columnid || '');
                    if (!ds || !col) continue;
                    const comp = String(x.compid || '');
                    const leaf = comp.split('.').pop();
                    const id = domId(h.path, comp);
                    let label = '', value = '', visible = false, kind = '';
                    const el = id ? document.getElementById(id) : null;
                    if (el) {
                        visible = isVisible(el);
                        if (visible) label = labelFor(el.getBoundingClientRect());
                        const cls = (typeof el.className === 'string') ? el.className : '';
                        kind = (cls.split(/\s+/)[0] || '');
                    }
                    if (!isInputControl(kind, leaf)) continue;   // a display, not a filter
                    try {
                        const d = h.form[ds];
                        const n = d ? d.getRowCount() : 0;
                        // A bound control shows whichever row is CURRENTLY
                        // SELECTED (Nexacro's `rowposition`), not always row
                        // 0 - only read here for display, so an invalid
                        // position falls back to '' (a discovery gap) rather
                        // than failing outright; the write path
                        // (js_set_values) makes the same call a hard refusal
                        // instead, since a write can silently land in the
                        // wrong row where a read merely under-reports one.
                        let r = 0;
                        if (n > 1) {
                            const rp = d.rowposition;
                            r = (rp !== undefined && rp !== null && rp >= 0 && rp < n) ? rp : -1;
                        }
                        if (d && n > 0 && r >= 0) value = String(d.getColumn(r, col) || '');
                    } catch (e) {}
                    // What the control itself DISPLAYS, kept beside the dataset
                    // value: on B3320UM00 the Period boxes show the date while
                    // the bound dataset row is empty (HISTORY.md Phase 83.3).
                    filters.push({dataset: ds, column: col, control: leaf,
                                  label: label, value: value, visible: visible,
                                  shown: visible ? shownValue(el) : '',
                                  kind: kind, id: id || '', form: h.file || '',
                                  path: h.path, stable_path: relativePath(h.path),
                                  bound: true});
                }
            }
        } catch (e) {}

        // Visible inputs with NO binding. Not every screen binds its filters
        // - Q2241UM00 sets them in code - so those screens would otherwise
        // report "no filters" and look broken. They cannot be written through
        // a dataset, but they CAN be typed into.
        try {
            const bound = {};
            const b2 = h.form.binds;
            if (b2 && b2.length !== undefined)
                for (let i = 0; i < b2.length; i++)
                    bound[String(b2[i].compid || '')] = true;
            const comps = h.form.components;
            if (comps && comps.length !== undefined && !SHELL.test(h.file || '')) {
                for (let i = 0; i < comps.length; i++) {
                    const c = comps[i];
                    if (!c || bound[c.name]) continue;
                    const id = domId(h.path, c.name);
                    const el = id ? document.getElementById(id) : null;
                    if (!el || !isVisible(el)) continue;
                    const cls = (typeof el.className === 'string') ? el.className : '';
                    const kind = (cls.split(/\s+/)[0] || '');
                    if (!isInputControl(kind, c.name)) continue;
                    unbound.push({control: c.name, form: h.file || '', id: id,
                                  label: labelFor(el.getBoundingClientRect()),
                                  value: shownValue(el), visible: true,
                                  kind: kind, bound: false,
                                  dataset: '', column: '', path: h.path,
                                  stable_path: relativePath(h.path)});
                }
            }
        } catch (e) {}

        // Grids -> candidate result sets. The shell's grids (My Menu, the
        // widget list) are inside the work window's form tree too, so they
        // have to be excluded here as well as from the binds - otherwise they
        // are offered as places the report's rows might be.
        try {
            const comps = SHELL.test(h.file || '') ? null : h.form.components;
            if (comps && comps.length !== undefined) {
                for (let i = 0; i < comps.length; i++) {
                    const c = comps[i];
                    if (!c) continue;
                    if (!/Grid/i.test(_typeName(c))) continue;
                    // The Excel export builds a throwaway clone of the grid
                    // it is exporting (grdPrnMpp__EXCEL__) and leaves it on
                    // the form. It is the same dataset, so it changes no
                    // answer - but it appears and disappears with the last
                    // export, which is exactly the kind of thing that must
                    // not look like the screen having changed.
                    if (/__EXCEL__/.test(c.name || '')) continue;
                    const bd = String(c.binddataset || '');
                    if (!bd) continue;
                    // An org tree or the Quick View widget, rendered as a
                    // Grid: chrome, not this screen's own result set. The
                    // SHELL filename test above does not reach them because
                    // they sit on forms of their own (OrgCategory_GDS.xfdl.js,
                    // WidgetFilter.xfdl.js), not a shell one, so this is by
                    // dataset shape instead - see chromeDatasets above.
                    if (chromeDatasets[bd]) continue;
                    // A widget embedded INSIDE the left filter panel is
                    // chrome no matter what its own .xfdl file is called -
                    // WidgetFilter.xfdl.js itself is caught by SHELL above,
                    // but a date-range calendar picker dropped into that
                    // same panel ships as its own file, CalendarD.xfdl.js,
                    // not shell by name, with a dataset (dsCalendar) shaped
                    // like neither a tree nor a Quick View. Live on
                    // P3151WM00 (HISTORY.md Phase 82.12) that grid had a
                    // real bounding box while every one of the screen's
                    // actual result grids read area 0 (not on the active
                    // tab yet), so it won choose_grid()'s "biggest" default
                    // outright. The path segment 'divFilter.divWidgetMain'
                    // is specific to this left-panel container - the WORK
                    // area's own, unrelated widget container is named
                    // 'divWork.divWidgetMain', so this cannot also exclude
                    // a screen's real result grid.
                    if (h.path.indexOf('divFilter.divWidgetMain') !== -1) continue;

                    const id = domId(h.path, c.name);
                    const el = id ? document.getElementById(id) : null;
                    const r = el ? el.getBoundingClientRect() : null;
                    grids.push({name: c.name, dataset: bd, form: h.file || '',
                                area: r ? Math.round(r.width * r.height) : 0,
                                visible: !!(el && isVisible(el)), path: h.path,
                                stable_path: relativePath(h.path)});
                }
            }
        } catch (e) {}

        // Quick View - a screen-embedded SHORTCUT TO A DIFFERENT SCREEN,
        // never a filter. Found by shape: a dataset carrying menuId +
        // sysScreenId + quickViewId is a slice of the same catalogue behind
        // the top search box (gotcha #21), scoped to this screen's siblings.
        // It renders as a Grid, not a Button/CheckBox, so JS_LEFT_OPTIONS
        // never sees it - it must not be offered as a left-panel option,
        // because clicking a row changes which SCREEN is open, not what the
        // query means (see HISTORY.md Phase 27).
        try {
            for (const k of Object.keys(h.form)) {
                let d = null;
                try { d = h.form[k]; } catch (e) { continue; }
                if (!d || _typeName(d) !== 'Dataset') continue;
                const cols = [];
                try { const n = d.getColCount();
                      for (let i = 0; i < n; i++) cols.push(d.getColID(i)); } catch (e) { continue; }
                if (cols.indexOf('sysScreenId') < 0 || cols.indexOf('menuId') < 0
                    || cols.indexOf('quickViewId') < 0) continue;
                let rows = 0;
                try { rows = d.getRowCount(); } catch (e) { continue; }
                for (let r = 0; r < rows; r++) {
                    let sid = '', nm = '';
                    try { sid = String(d.getColumn(r, 'sysScreenId') || '').trim(); } catch (e) {}
                    try { nm = String(d.getColumn(r, 'enMsgCont')
                                    || d.getColumn(r, 'msgCont') || '').trim(); } catch (e) {}
                    if (!sid || seenQV[sid]) continue;
                    seenQV[sid] = true;
                    quickViews.push({screen: sid, name: nm, active: sid === screenCode});
                }
            }
        } catch (e) {}
    }

    // One dataset column can be bound to several controls WITHIN ONE FORM (a
    // value shown in two places on the same panel). Keep one entry per
    // path+dataset.column, preferring the visible control - that is the one
    // a person means when they name a label.
    //
    // Keyed by path too, not dataset.column alone: a reusable component
    // (divWidgetFilterPPM0222, this file's own live example) can appear more
    // than once, each instance carrying the SAME dataset.column at a
    // DIFFERENT path - that is a genuinely different control, not a second
    // rendering of one value, and collapsing it here would throw the
    // distinction away before Python - or the exact-path write Phase 80
    // built - ever sees it (HISTORY.md - external review of 1957ba9/cff282b,
    // finding #2).
    const seen = {};
    const unique = [];
    for (const f of filters) {
        const key = f.path + '|' + f.dataset + '.' + f.column;
        const prev = seen[key];
        if (prev === undefined) { seen[key] = unique.length; unique.push(f); }
        else if (f.visible && !unique[prev].visible) { unique[prev] = f; }
    }
    filters.length = 0;
    Array.prototype.push.apply(filters, unique);

    // A bind records the control by its FULL path from the owning form
    // (divBasic.form.divCal.form.mskDateFrom) while a component knows only
    // its own name, and the two are usually recorded on different forms -
    // comparing them directly listed bound controls as unbound. `f.path`
    // is the form that owns the DATASET (needed for the write), which is
    // not the same form as the one actually containing a deeply-nested
    // control - live-caught on M3912UM00 (HISTORY.md Phase 82.7):
    // fromDate/toDate/searchTypeCode/... are bound on divFilter but live
    // several Divs below it, so keying this check by `path + control`
    // (Phase 82.1's own fix, for a DIFFERENT problem - two SEPARATE
    // instances of a reusable component) broke it here, reporting all 7
    // filters a second time as unbound. `id` - the fully expanded DOM id,
    // already computed identically by both loops for the same physical
    // element regardless of which form is doing the reporting - is the
    // one identity that is actually correct for "is this the same
    // control", never a guess about which form "owns" it.
    const boundLeaves = {};
    for (const f of filters) boundLeaves[f.id] = true;
    const seenUnbound = {};
    const trulyUnbound = [];
    for (const u of unbound) {
        if (boundLeaves[u.id] || seenUnbound[u.id]) continue;
        seenUnbound[u.id] = true;
        trulyUnbound.push(u);
    }

    grids.sort((a, b) => b.area - a.area);

    const inquiry = Array.from(document.querySelectorAll('div')).find(el => {
        const cls = (typeof el.className === 'string') ? el.className : '';
        return /btn_LF_Search_New/.test(cls) && isVisible(el);
    });
    const excel = document.getElementById(%s);

    // Every list below used to be cut at 8 / 40 with nothing saying so
    // (HISTORY.md Open Item 37). The caps are now far above anything a real
    // screen has, and the TRUE totals travel with the lists so `discover()`
    // can refuse a cut rather than hand back a confident, partial picture.
    const GRID_CAP = 24, UNBOUND_CAP = 200;
    return JSON.stringify({found: true, screen: screenCode,
                           window: winPath.split('.').pop(),
                           filters: filters, unbound: trulyUnbound.slice(0, UNBOUND_CAP),
                           grids: grids.slice(0, GRID_CAP), datasets: datasets,
                           quickViews: quickViews,
                           hasInquiry: !!inquiry,
                           hasExcel: !!(excel && isVisible(excel)),
                           truncated: !!_findForms.truncated,
                           totals: {grids: grids.length, unbound: trulyUnbound.length}});
})()
"""


# The left panel carries dimensions that `form.binds` cannot see, and every
# one of them changes what the query returns:
#
#   Org / Prod / Fac / Proc     which category tree the selection comes from
#   STD / PLANT                 the organisation attribute
#   Including Past Org.         whether closed organisations are included
#   Plan Date / Create Date     WHICH date the period applies to
#   General / Compare / OI      the search mode
#   Quick View entries          Master vs Detail Prod. Plan - a different result
#
# Running "yesterday" against Create Date instead of Plan Date returns a
# plausible, completely different answer, and nothing looks wrong. Nexacro
# encodes the chosen state in the CSS class, so they can be listed with their
# state and set by label.
# Checkbox state used to be read from a `.checked` CSS class
# (`el.querySelector('.checked')`) - confirmed live, HISTORY.md Phase 69,
# that this Nexacro CheckBox type never adds one at all: its checked/
# unchecked look is drawn by swapping the icon IMG, not by toggling a
# class anywhere in its subtree. `hasCheckedSelector` was `false` both
# before AND after a real, confirmed-working click that correctly flipped
# the component's own `value` between its `truevalue`/`falsevalue` - so
# this test reported "unchecked" 100% of the time regardless of the real
# state. That silently broke two things at once: `set_option()`'s
# already-on guard could never see a checkbox as already checked, and its
# post-click verification could never see one as checked either, so every
# checkbox click - even a fully successful one - ended in "could not prove
# option was selected". Read the component's own `value`/`truevalue`
# instead, resolved from the DOM id by walking it as a `nexacro.
# getApplication()` property path - confirmed live to resolve correctly,
# the same technique `gfnCloseWorkFarme` used for closing a tab (HISTORY.md
# Phase 60). Falls back to the DOM-class heuristic only if that resolution
# fails, rather than assuming every checkbox on every screen is built the
# same way.
JS_LEFT_OPTIONS = r"""
(function() {
    const isVisible = %s;
    function resolveNexacro(id) {
        try {
            let obj = nexacro.getApplication();
            for (const part of id.split('.')) {
                if (obj == null) break;
                obj = obj[part];
            }
            return obj;
        } catch (e) { return null; }
    }
    function checkboxState(obj, el) {
        if (obj != null && obj.value !== undefined && obj.truevalue !== undefined) {
            return String(obj.value) === String(obj.truevalue) ? 'checked' : 'unchecked';
        }
        return el.querySelector('.checked') ? 'checked' : 'unchecked';
    }
    // The component path with the work-window segment removed. Window ids
    // embed an instance number that changes on every open (winPPM0219_0_926
    // one visit, _0_315 the next - GMES_SKILL.md #7), so the window segment
    // can never be part of an identity. Everything after it is authored in
    // the screen's XFDL and is the same for every user in every language.
    function relativePath(id) {
        const parts = id.split('.');
        for (let i = 0; i < parts.length; i++) {
            if (/^win.*_\d+_\d+$/.test(parts[i])) return parts.slice(i + 1).join('.');
        }
        return id;
    }
    const out = [];
    const seen = {};
    for (const el of document.querySelectorAll('div')) {
        const id = el.id || '';
        if (!/\.divLeft\.form\./.test(id) && !/divTabBtnArea/.test(id)) continue;
        if (/:icontext$|:text$/.test(id)) continue;
        const cls = (typeof el.className === 'string') ? el.className : '';
        const isBtn = /\bButton\b/.test(cls), isChk = /\bCheckBox\b/.test(cls);
        if (!isBtn && !isChk) continue;
        if (!isVisible(el)) continue;
        const text = (el.textContent || '').trim();
        if (!text || text.length > 32) continue;
        if (seen[text]) continue;
        seen[text] = true;
        const r = el.getBoundingClientRect();
        const obj = resolveNexacro(id);
        let state = 'unknown';
        if (/_Sel\b|_Sel$|ToggleSearchV2|Category_Sel/.test(cls)) state = 'selected';
        else if (/_Dis\b|_Dis$|_Default/.test(cls)) state = 'not selected';
        else if (isChk) state = checkboxState(obj, el);
        // The CSS suffix `_Dis`/`_Default` names the DESELECTED visual
        // style, not "disabled" despite the name - a button can be
        // deselected and perfectly clickable at the same time. Whether it
        // can actually be clicked right now is the live Nexacro object's
        // own `enable` flag, which is a completely separate axis: a click
        // on btnProduce ('실적일') sent to its exact on-screen coordinates
        // changed nothing at all, live-traced down to the class never
        // moving before or after - not a detection bug like Phase 69.1's
        // checkbox, but a genuinely disabled control the click can never
        // affect. Reported here so callers can refuse BEFORE clicking
        // rather than after failing to prove a click that could not have
        // worked, which otherwise looks identical to a real bug.
        const enabled = (obj != null && obj.enable === false) ? false : true;
        // `name` is the component's own Nexacro name - btnCreate, btnPlan,
        // chkDisuseYn, tabTitle_Org. It is authored in the XFDL, so it is
        // identical whatever language the UI renders in, which is the whole
        // basis of language-independent replay (HISTORY.md Phase 76). Live
        // evidence: the control rendering '생성일' is named btnCreate, and its
        // rendered text sits in a SEPARATE property, `_displaytext`.
        // Falls back to the last id segment, which is that same name.
        const name = (obj != null && obj.name) ? String(obj.name)
                                               : (id.split('.').pop() || '');
        out.push({label: text, id: id, name: name, path: relativePath(id),
                  cls: cls.slice(0, 46), state: state,
                  kind: isChk ? 'checkbox' : 'button', enabled: enabled,
                  x: r.left + r.width / 2, y: r.top + r.height / 2});
    }
    return JSON.stringify({count: out.length, options: out});
})()
""" % cdp_common.JS_IS_VISIBLE


# Organisation trees. The nightly Production Plan job addresses exactly one -
# OrgCategory_GDS.dsCatCommonTreeNodeDVO - which is correct for that screen
# and wrong as a general rule: the left panel offers Org / Prod / Fac / Proc,
# and each tab has a tree of its own. They are found by SHAPE instead: a
# dataset carrying a `commonName` column and a `_checked` flag is a category
# tree, whatever it is called and wherever it lives.
#
# Scoped to the CURRENT screen's own window (confirmed live, HISTORY.md
# Phase 65): with three unrelated screens open at once, this used to find
# THREE identical-looking "OrgCategory_GDS.dsCatCommonTreeNodeDVO" trees -
# one per open window, not one per tab on any single screen - because it
# walked `_findForms(null)` with no window filter at all, unlike
# `JS_DISCOVER`. Since every copy shares the exact same form and dataset
# name, no `--tree` value could ever tell them apart, so `select_org()`
# refused to run at all: "'VD' appears in multiple category trees" on a
# screen that, on its own, may not be ambiguous. Left unnoticed until now
# because a single-screen CLI run rarely has another screen already open;
# an interactive session or a batch - the two ways this tool is actually
# used - very often does, since screens are not closed between reports by
# default.
JS_ORG_TREES = r"""
(function() {
    %s
    const screenCode = %s;
    const forms = _findForms(screenCode);
    if (!forms.length) return JSON.stringify({count: 0, trees: [],
        reason: 'no forms for this screen code'});
    const anchor = forms[0].path;
    const parts = anchor.split('.');
    const winIdx = parts.findIndex(p => /^win/.test(p));
    const winPath = winIdx >= 0 ? parts.slice(0, winIdx + 1).join('.') : null;

    // The shell's My Menu panels keep datasets with a commonName column too,
    // so a pure shape test found 22 "category trees" on a screen that has
    // one. They are not organisation trees and must not be offered as them.
    const SHELL = /WorkMainTitle|WorkMain\.xfdl|WorkTemplate|MyMenu|TopMenu|LeftMenu|LeftMain|PortalMain/i;
    const out = [];
    for (const h of _findForms(null)) {
        if (winPath !== null && h.path.indexOf(winPath) !== 0) continue;
        if (SHELL.test(h.file || '')) continue;
        let keys = [];
        try { keys = Object.keys(h.form); } catch (e) { continue; }
        for (const k of keys) {
            let ds = null;
            try { ds = h.form[k]; } catch (e) { continue; }
            if (!ds || _typeName(ds) !== 'Dataset') continue;
            let cols = [], rows = 0;
            try {
                rows = ds.getRowCount();
                const n = ds.getColCount();
                for (let i = 0; i < n; i++) cols.push(ds.getColID(i));
            } catch (e) { continue; }
            if (cols.indexOf('commonName') < 0) continue;
            if (rows === 0) continue;      // an empty tree cannot hold a division
            const names = [], checked = [];
            for (let r = 0; r < rows; r++) {
                let nm = '', ck = '';
                try { nm = String(ds.getColumn(r, 'commonName') || ''); } catch (e) {}
                try { ck = String(ds.getColumn(r, '_checked') || ''); } catch (e) {}
                if (nm) names.push(nm);
                if (ck === '1') checked.push(nm);
            }
            out.push({form: h.file || '', dataset: k, rows: rows,
                      settable: cols.indexOf('_checked') >= 0,
                      names: names.slice(0, 60), checked: checked,
                      path: h.path});
        }
    }
    return JSON.stringify({count: out.length, trees: out});
})()
"""


JS_TICK_ORG = r"""
(function() {
    %s
    const treeName = %s;
    const wanted = %s;
    const exclusive = %s;
    const screenCode = %s;
    const paths = %s;

    // EVERY instance of the tree, not the first one found - but only within
    // THIS run's own work window, never app-wide.
    //
    // A screen can hold the same category tree several times - Work Calendar
    // has THREE copies of OrgCategory_GDS.dsCatCommonTreeNodeDVO, one per
    // panel tab - and `_dataset()` returns whichever the form walk reaches
    // first. Writing to that one and reporting success is how a run announced
    // "Tick the division ... VD" while the screen still had MOBILE ticked by
    // hand, and then exported MOBILE's rows under a VD heading. They are the
    // same logical tree, so writing all of them is both safe and the only
    // way to be sure the visible one was included.
    //
    // `screenCode` here is the TREE's own shared form name (e.g.
    // "OrgCategory_GDS"), never the work screen's own code - it is a
    // reusable component, embedded on many unrelated screens, exactly like
    // the WidgetFilter panel finding #1/#2 already found this same failure
    // mode in. `_findForms(screenCode)` alone would match every instance of
    // it ANYWHERE in the app, including a completely different window left
    // open by an earlier interactive session, and tick a division in a
    // screen nobody asked to change. `paths` - the exact, already
    // window-scoped instances `org_trees()`/`Screen.trees()` already found -
    // is what actually restricts this to the current work window; the
    // `screenCode`-only search is kept only as the fallback a caller with no
    // window-scoped discovery available (yet) can still use (HISTORY.md -
    // external review of 1957ba9/cff282b, finding #5).
    const targets = [];
    const wantForms = paths && paths.length
        ? _findForms(null).filter(h => paths.indexOf(h.path) >= 0)
        : _findForms(screenCode);
    for (const h of wantForms) {
        let ds = null;
        try { ds = h.form[treeName]; } catch (e) { continue; }
        if (!ds || _typeName(ds) !== 'Dataset') continue;
        let cols = [];
        try { const n = ds.getColCount();
              for (let i = 0; i < n; i++) cols.push(ds.getColID(i)); } catch (e) { continue; }
        if (cols.indexOf('commonName') < 0) continue;
        if (cols.indexOf('_checked') < 0) continue;
        targets.push({ds: ds, file: h.file});
    }
    if (!targets.length)
        return JSON.stringify({found: false, reason:
            'no instance of this tree has a _checked column'});

    const want = wanted.map(w => String(w).trim().toLowerCase());
    const ticked = [], cleared = [], present = {};
    let available = [];

    for (const t of targets) {
        const ds = t.ds;
        for (let r = 0; r < ds.getRowCount(); r++) {
            let nm = '';
            try { nm = String(ds.getColumn(r, 'commonName') || '').trim(); }
            catch (e) { continue; }
            const low = nm.toLowerCase();
            // Case-insensitive: a user typing "vd" must find "VD". An exact
            // comparison once failed a run with the answer sitting in the
            // very error message it printed.
            const isWanted = want.indexOf(low) >= 0;
            let was = '';
            try { was = String(ds.getColumn(r, '_checked') || ''); } catch (e) {}

            if (isWanted) {
                present[low] = true;
                try {
                    ds.setColumn(r, '_checked', 1);
                    let pathKey = '';
                    try { pathKey = String(ds.getColumn(r, 'commonPathKey') || ''); }
                    catch (e) {}
                    ticked.push({name: nm, pathKey: pathKey,
                                 now: String(ds.getColumn(r, '_checked'))});
                } catch (e) {}
            } else if (exclusive && was === '1') {
                // A tick SURVIVES between runs, and can also have been made
                // by hand a moment ago. Leaving one behind means the next run
                // silently queries a different organisation and answers
                // confidently with the wrong scope.
                try { ds.setColumn(r, '_checked', 0); cleared.push(nm); } catch (e) {}
            }
        }
        if (!available.length) {
            for (let r = 0; r < ds.getRowCount(); r++) {
                try { const n = String(ds.getColumn(r, 'commonName') || '').trim();
                      if (n) available.push(n); } catch (e) {}
            }
        }
    }

    const missing = want.filter(w => !present[w]);
    return JSON.stringify({found: missing.length === 0, ticked: ticked,
                           cleared: cleared, missing: missing,
                           instances: targets.length,
                           file: targets[0].file, available: available});
})()
"""


# The screen's own summary of what organisation is selected - the ground
# truth, and the only thing that would have caught the MOBILE-under-a-VD-label
# run. Reads "Org VD l Prod All l Proc All".
JS_ORG_SELECTION = r"""
(function() {
    const isVisible = %s;
    let best = null;
    for (const el of document.querySelectorAll('div')) {
        const id = el.id || '';
        if (!/staCategory(Ori)?(:text)?$/.test(id)) continue;
        if (!isVisible(el)) continue;
        const text = (el.textContent || '').trim();
        if (!/^Org\s/.test(text) || text.length > 120) continue;
        if (!best || text.length < best.length) best = text;
    }
    if (best === null) return JSON.stringify({found: false});
    // "Org VD l Prod All l Proc All" - the separator renders as a lowercase
    // L in one place and a pipe in another, so both are accepted.
    const first = best.split(/\s+[l|]\s+/)[0];
    return JSON.stringify({found: true, text: best,
                           org: first.replace(/^Org\s+/, '').trim()});
})()
"""


# What a control shows right now - used to confirm that a typed value landed.
JS_CONTROL_VALUE = r"""
(function() {
    const el = document.getElementById(%s);
    if (!el) return JSON.stringify({found: false, reason: 'no such id'});
    let v = '';
    if (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA') v = el.value || '';
    else {
        const inner = el.querySelector('input, textarea');
        v = inner ? (inner.value || '') : (el.textContent || '').trim();
    }
    return JSON.stringify({found: true, value: v});
})()
"""


# Closing a tab. The close control is looked for INSIDE the tab element and
# matched by class or id, never guessed at by position - and if there is no
# such control this reports that instead of clicking something unknown
# (CLAUDE.md 3.9).
JS_TAB_CLOSE_TARGET = r"""
(function() {
    const isVisible = %s;
    const tab = document.getElementById(%s);
    if (!tab) return JSON.stringify({found: false, reason: 'no tab with that id'});
    const cands = [];
    for (const el of tab.querySelectorAll('div, span, button')) {
        const cls = (typeof el.className === 'string') ? el.className : '';
        const id = el.id || '';
        if (!/close/i.test(cls) && !/close/i.test(id)) continue;
        if (!isVisible(el)) continue;
        const r = el.getBoundingClientRect();
        cands.push({id: id, cls: cls.slice(0, 50), area: r.width * r.height,
                    x: r.left + r.width / 2, y: r.top + r.height / 2});
    }
    if (!cands.length)
        return JSON.stringify({found: false, reason: 'the tab has no close control'});
    cands.sort((a, b) => a.area - b.area);
    return JSON.stringify({found: true, target: cands[0]});
})()
"""


def _js(template, *args):
    return template % args


# Fallback for closing a work-screen tab when it has no separate DOM close
# control to click (confirmed live 2026-09-13, HISTORY.md Phase 60.1: a real
# open "Production Plan by Order(Line)" tab's element had only a label child,
# no close button at all, so JS_TAB_CLOSE_TARGET correctly reported "the tab
# has no close control" on every single run - `close_after=True` therefore
# never actually closed a tab of this shape, and it kept accumulating behind
# every later command, which is the same tab-reuse state Phase 58.2 found
# inflating discovery). `gfnCloseWorkFarme` - the G-MES application's own
# close-tab handler (found live by dumping the tab bar's method list and
# reading its source) - reduces to exactly this one call; it needs only the
# win_id we already have, not any DOM element at all.
JS_CLOSE_WORK_FRAME = """
(function() {
    try {
        window.nexacro.getApplication().gvMdiFrame.form.fnRemoveForm(%s);
        return JSON.stringify({ok: true});
    } catch (e) {
        return JSON.stringify({ok: false, reason: 'threw: ' + e.message});
    }
})()
"""


def close_work_frame(ws, win_id):
    result = evaluate(ws, JS_CLOSE_WORK_FRAME % cdp_common.json.dumps(win_id))
    return bool(result.get("ok"))


def discover(ws, screen_code):
    info = evaluate(ws, _js(JS_DISCOVER, gmes_data.JS_HELPERS,
                            cdp_common.JS_IS_VISIBLE,
                            cdp_common.json.dumps(screen_code),
                            cdp_common.json.dumps(list(INPUT_PREFIXES)),
                            cdp_common.json.dumps(EXCEL_BTN)))
    problem = discovery_cut(info)
    if problem:
        raise RuntimeError(problem)
    return info


def discovery_cut(info):
    """Why this reading of a screen cannot be trusted, or None.

    A partial picture is worse than none: the same accident - a form walk
    stopping early because several windows were open - once reported P3111UM00
    as having no division tree and Q2277UM00 as having no filters at all, and
    was only noticed because the numbers looked wrong (HISTORY.md Phase
    82.20). The walk and the result lists now say when they were cut, and a
    cut reading is refused instead of used."""
    if not isinstance(info, dict):
        return None
    if info.get("truncated"):
        return ("reading this screen was cut short - too many forms are open "
                "for the form walk to finish, so filters, grids or the "
                "division tree may be missing. Close the other G-MES work "
                "windows and try again.")
    totals = info.get("totals") or {}
    for key, label in (("grids", "result grids"), ("unbound", "unbound inputs")):
        have = len(info.get(key) or [])
        if totals.get(key, have) > have:
            return (f"only {have} of this screen's {totals[key]} {label} were "
                    "read, so the picture of the screen is incomplete.")
    return None


def left_options(ws):
    return evaluate(ws, JS_LEFT_OPTIONS)


# ---------------------------------------------------------------------------
# Left-panel options, identified by something other than what they say
# ---------------------------------------------------------------------------
#
# A remembered option used to be a visible label - `"options": ["Create Date"]`.
# That works exactly as long as the screen keeps rendering the language it was
# rendering when the screen was taught, and G-MES does not: the same account on
# the same machine renders `생성일` instead, and replay stopped dead with "no
# left-panel option called 'Create Date'" (HISTORY.md Open Item 19, closed in
# Phase 76).
#
# The fix is not a translation table. Live probing of the real screen found
# that every one of these controls carries its own Nexacro `name`, authored in
# the screen's XFDL and identical in every language:
#
#     생성일           -> btnCreate            과거 조직도 포함 -> chkDisuseYn
#     계획일           -> btnPlan              DB 조회        -> chkPoSearch
#     실적일           -> btnProduce           일반 검색       -> btnSearchNormal
#     Org/Prod/...    -> tabTitle_Org/...     비교 검색       -> btnSearchCompare
#
# So the identity is the component name, the semantic key is that name
# normalised, and the visible text is demoted to display metadata.

#: Nexacro control-type prefixes. `tabtitle_` first: it starts with `tab`, and
#: stripping the shorter one would leave `title_org` instead of `org`.
_OPTION_PREFIXES = ("tabtitle_", "btn", "chk", "rdo", "cbo", "spn", "tab")


def option_key(name):
    """The normalised, language-independent semantic key for an option.

    `btnCreate` -> `create`, `chkDisuseYn` -> `disuseyn`, `tabTitle_Org` ->
    `org`, `btnstd` -> `std`. Case and the control-type prefix carry no
    meaning, so neither survives."""
    text = re.sub(r"[^a-z0-9_]+", "", (name or "").strip().lower())
    for prefix in _OPTION_PREFIXES:
        if text.startswith(prefix) and len(text) > len(prefix):
            text = text[len(prefix):]
            break
    return text.replace("_", "")


def option_words(text):
    """The ASCII words in a requested option name, lowercased.

    Korean and punctuation fall away deliberately: this is only ever used for
    the alias step, which exists to rescue an ENGLISH label saved by an older
    profile. A Korean request is matched by its label directly, exactly."""
    return [w for w in re.split(r"[^a-z0-9]+", (text or "").lower()) if w]


def option_identity(opt):
    """What gets written into a profile: the stable identity plus, separately,
    the text that happened to be on screen when it was learned.

    The label is kept because a person reading their own profile should be able
    to tell what `btnCreate` was, and because it still serves as the migration
    path for anything recorded before this existed. It is never matched on
    first."""
    return {"key": option_key(opt.get("name")),
            "name": opt.get("name") or "",
            "path": opt.get("path") or "",
            "label": opt.get("label") or ""}


def option_display(wanted):
    """One human-readable token for a log line, from either profile shape."""
    if isinstance(wanted, dict):
        label, key = wanted.get("label"), wanted.get("key") or wanted.get("name")
        if label and key:
            return f"{label} [{key}]"
        return label or key or "?"
    return str(wanted)


def _alias_candidates(found, wanted_text):
    """Options whose semantic key is derivable from an English request.

    This is the migration rescue, and it is deliberately narrow. Only two
    rules, both exact:

      * the whole request, punctuation removed, IS the key  - "PLANT" -> plant
      * the request's FIRST word is the key                 - "Create Date"
                                                              -> create

    Anything looser guesses. A "key appears anywhere in the request" rule was
    written first and rejected after testing it against this very screen:
    "Including Past Org." contains the word "org", so it matched the Org
    CATEGORY TAB (`tabTitle_Org`) instead of the `chkDisuseYn` checkbox it
    means - a confidently wrong match that would silently change what the
    query returns. Nothing is better than that, because nothing stops and
    says so (CLAUDE.md 3.9).

    Note "Plan Date" -> `plan` and "PLANT" -> `plant` stay distinct under
    these rules, which a prefix rule would not have managed."""
    words = option_words(wanted_text)
    if not words:
        return []
    joined = "".join(words)
    hits = []
    for opt in found:
        key = option_key(opt.get("name"))
        if not key:
            continue
        if key == joined or key == words[0]:
            hits.append(opt)
    return hits


def _option_diagnostics(found):
    """Every option with everything needed to name one unambiguously."""
    return ", ".join(
        f"{o.get('label')!r} [{option_key(o.get('name'))}]" for o in found[:20])


def resolve_option(found, wanted):
    """Find the one option `wanted` means, or raise saying why not.

    `wanted` is either a profile entry (a dict carrying the stable identity) or
    a plain string - what `--option` passes, and what every profile written
    before Phase 76 contains.

    The order is strongest-evidence-first, and each step must produce exactly
    one match or the next is tried:

      1. component name      language-independent, the identity we now store
      2. component path      distinguishes two controls sharing a name
      3. semantic key        the normalised form of 1
      4. exact label         what a person reading the screen types
      5. English alias       rescues a pre-Phase-76 profile (see above)
      6. label substring     the historical last resort, unchanged

    Returns (option, how). Raises on no match AND on ambiguity - never picks
    one of several (CLAUDE.md 3.9). The message lists every option with its
    key, so the answer to "then what should I have said" is in the failure
    itself rather than requiring another run to find out."""
    if isinstance(wanted, dict):
        attempts = [
            ("component name", lambda o: (o.get("name") or "").lower()
             == (wanted.get("name") or "\0").lower()),
            ("component path", lambda o: (o.get("path") or "").lower()
             == (wanted.get("path") or "\0").lower()),
            ("semantic key", lambda o: option_key(o.get("name"))
             == (wanted.get("key") or "\0")),
        ]
        text = wanted.get("label") or wanted.get("name") or ""
    else:
        text = str(wanted)
        attempts = [
            ("component name", lambda o: (o.get("name") or "").lower() == text.lower()),
            ("semantic key", lambda o: option_key(o.get("name")) == option_key(text)),
        ]

    attempts += [
        ("label", lambda o: (o.get("label") or "").strip().casefold()
         == text.strip().casefold()),
    ]

    for how, predicate in attempts:
        matches = [o for o in found if predicate(o)]
        if len(matches) == 1:
            return matches[0], how
        if len(matches) > 1:
            raise RuntimeError(
                f"{option_display(wanted)!r} matches {len(matches)} left-panel "
                f"options by {how}: {_option_diagnostics(matches)}. Refusing to "
                "guess - name one by its key.")

    for how, matches in (("english alias", _alias_candidates(found, text)),
                         ("label fragment",
                          [o for o in found
                           if text.strip() and text.strip().casefold()
                           in (o.get("label") or "").casefold()])):
        if len(matches) == 1:
            return matches[0], how
        if len(matches) > 1:
            raise RuntimeError(
                f"{option_display(wanted)!r} matches {len(matches)} left-panel "
                f"options by {how}: {_option_diagnostics(matches)}. Refusing to "
                "guess - name one by its key.")

    raise RuntimeError(
        f"no left-panel option matching {option_display(wanted)!r}. "
        f"This screen offers: {_option_diagnostics(found)}. "
        "The name in [brackets] is language-independent and is what a "
        "recorded profile stores; pass one of those with --option.")


def org_trees(ws, screen_code):
    return evaluate(ws, _js(JS_ORG_TREES, gmes_data.JS_HELPERS,
                            cdp_common.json.dumps(screen_code)))


# A work-screen validation alert (e.g. "Start Date is later than End Date")
# is not the title-bar popup `find_child_popups()` detects - that mechanism
# was built for sign-in Notice popups (HISTORY.md Phase 59.1) and reads the
# POPUP'S OWN title bar text, which came back empty for this alert when
# confirmed live: `poll_inquiry()` correctly detected that SOMETHING opened
# and safely stopped, but could only report `['']`, losing the real message
# G-MES had already put on screen. The real text lives in a `txt_WF_alert`
# Static nested under the work window itself (`<winId>.Info_N.form.divBody.
# form.staContents`), confirmed by live DOM dump - reached by CLASS, not the
# `Info_N` suffix, which is exactly the kind of generated, renumbered id
# CLAUDE.md 3.4 already rules out. Scoped to the current screen's window for
# the same reason `org_trees()` had to be (HISTORY.md Phase 65.1): an
# unrelated alert on a DIFFERENT open screen must not bleed into this one's
# error message.
JS_ALERT_TEXT = r"""
(function() {
    %s
    const screenCode = %s;
    const forms = _findForms(screenCode);
    if (!forms.length) return JSON.stringify({texts: []});
    const anchor = forms[0].path;
    const parts = anchor.split('.');
    const winIdx = parts.findIndex(p => /^win/.test(p));
    if (winIdx < 0) return JSON.stringify({texts: []});
    // _findForms() paths carry an "application." prefix that real DOM ids
    // never have (the same translation JS_DISCOVER's own domId() already
    // does) - comparing the raw object-path winPath against el.id matched
    // nothing at all, confirmed live: every real .txt_WF_alert id started
    // with "mainframe...", not "application.mainframe...".
    const winPath = parts.slice(0, winIdx + 1).join('.').replace(/^application\./, '');

    const texts = [];
    for (const el of document.querySelectorAll('.txt_WF_alert')) {
        if ((el.id || '').indexOf(winPath) !== 0) continue;
        const t = (el.textContent || '').trim();
        if (t) texts.push(t);
    }
    return JSON.stringify({texts: texts});
})()
"""


def alert_text(ws, screen_code):
    """The real message behind a work-screen validation alert, if any -
    see JS_ALERT_TEXT above. Best-effort: a read failure here must never
    hide the original "a dialog opened" error underneath it."""
    try:
        result = evaluate(ws, _js(JS_ALERT_TEXT, gmes_data.JS_HELPERS,
                                  cdp_common.json.dumps(screen_code)))
        return result.get("texts", [])
    except Exception:
        return []


# ===========================================================================
# Pure helpers - no browser, so they are unit-testable offline
# ===========================================================================

def normalise_date(value):
    """Accept the ways people actually type a date; return G-MES's YYYYMMDD.

    A date typed as 2026-09-07 used to be written into the filter verbatim.
    G-MES stores YYYYMMDD, so the query ran against a value it could not
    interpret - no error, just a different answer. Anything not resolvable to
    a real calendar date is rejected rather than passed through and hoped
    for."""
    raw = ascii_digits((value or "").strip())
    if not raw:
        return None
    digits = re.sub(r"[^\d]", "", raw)
    if len(digits) != 8:
        raise ValueError(f"{value!r} is not a date. Use YYYYMMDD (20260907) or "
                         "YYYY-MM-DD (2026-09-07).")
    try:
        datetime.strptime(digits, "%Y%m%d")
    except ValueError:
        raise ValueError(f"{value!r} is not a real calendar date.")
    return digits


def fit_date_to_field(yyyymmdd, current_value, remembered_width=None):
    """Match the width the field is actually storing.

    Not every G-MES period field holds a full date. Month fields (`stdYm`,
    `paramYm`) hold YYYYMM, and writing eight digits into one of those is the
    same class of mistake as writing "2026-09-07" into a YYYYMMDD field: it is
    accepted, and the query then answers something else. The width already in
    the box is the screen telling us which it wants.

    HISTORY.md Open Item 41: an EMPTY field has no width of its own to read,
    so it always defaulted to eight - wrong for a YYYYMM field that merely
    happens to be blank right now. `remembered_width` (a profile's
    `gmes_profile.field_ref()`, from the last time this field was seen
    NON-empty) is consulted only then; a field with a real value in it right
    now is always trusted over anything remembered, since it is live evidence
    the width check exists to use in the first place."""
    existing = re.sub(r"[^\d]", "", (current_value or "").strip())
    if len(existing) == 6:
        return yyyymmdd[:6]
    if len(existing) == 4:
        return yyyymmdd[:4]
    if not existing and remembered_width in (4, 6):
        return yyyymmdd[:remembered_width]
    return yyyymmdd


_DATE_WORDS = {"date", "dates", "ymd", "ym", "dt", "day", "period", "yyyymmdd"}
_FROM_WORDS = {"from", "start", "fr", "st", "begin"}
_TO_WORDS = {"end", "to", "thru", "through", "until", "last"}


def words(name):
    """Split an identifier into lowercase words: paramFromDate -> from, date.

    Substring matching is not safe here and produced a concrete trap:
    `paramVendorCode` contains "end", so a plain search classified a vendor
    code as the period's end date. Only whole camel-case or underscore words
    count."""
    parts = re.split(r"[^A-Za-z0-9]+|(?<=[a-z0-9])(?=[A-Z])", name or "")
    return {p.lower() for p in parts if p}


def is_date_field(flt):
    """Whether a discovered filter is a date or period field.

    Two signals, because neither holds alone. The column or label naming a
    date is the strong one. Failing that, Nexacro's own control type: a date
    is a masked edit or a calendar (`msk`, `cal`) holding four, six or eight
    digits. The shape of the value is deliberately NOT a signal on its own -
    plenty of eight-digit codes are not dates."""
    named = (words(flt.get("column")) | words(flt.get("label"))) & _DATE_WORDS
    if named:
        return True
    control = (flt.get("control") or "").lower()
    value = digits_only(flt.get("value"))
    return control.startswith(("msk", "cal")) and len(value) in (0, 4, 6, 8)


def date_named_columns(columns):
    """Which of a result's own column names merely LOOK like a date - naming
    only, no value shape checked. Used to suggest real `--verify` candidates
    before Inquiry has ever run, when there are no row values yet to confirm
    against. `Screen.date_like_columns()` re-checks the shape once rows
    exist; this is the weaker, earlier-available half of that same test."""
    return [c for c in columns if not c.startswith("_") and words(c) & _DATE_WORDS]


def date_targets(info):
    """Split the screen's date fields into (from, to, singles).

    The previous rule matched only `*fromdate*` / `*enddate*`, so a screen
    storing `paramYmd` or `stdYm` was reported as having no date filter at all
    and the run silently used whatever the screen had in it."""
    dates = [f for f in info.get("filters", []) if is_date_field(f)]

    def tagged(vocab):
        for f in dates:
            if words(f.get("column")) & vocab or words(f.get("control")) & vocab:
                return f
        return None

    frm = tagged(_FROM_WORDS)
    to = tagged(_TO_WORDS)
    singles = [f for f in dates if f is not frm and f is not to]
    return frm, to, singles


def match_filter(info, key, include_unbound=True):
    """Resolve a name to a discovered control.

    Accepts the column name, the visible label, or the control name - in that
    order of confidence - so a screen can be driven either the way it reads on
    screen or the way it is stored. Returns one control, a list when the name
    is ambiguous, or None."""
    pool = list(info.get("filters", []))
    if include_unbound:
        pool += list(info.get("unbound", []))
    k = key.strip().lower()

    for attr in ("column", "label", "control"):
        exact = [f for f in pool if (f.get(attr) or "").lower() == k]
        if len(exact) == 1:
            return exact[0]
        if exact:
            # One column can be bound on several sub-forms (P4115UM00: a
            # tab per view, each with its own copy of startTerm/endTerm).
            # When exactly ONE of them is on screen, that is the control a
            # person means by the name; the rest belong to tabs not shown.
            # Each entry still carries its own exact path, so the write
            # lands on the one chosen and nowhere else (HISTORY.md 84.20).
            shown = [f for f in exact if f.get("visible")]
            return shown[0] if len(shown) == 1 else exact
    partial = [f for f in pool
               if k in (f.get("label") or "").lower()
               or k in (f.get("column") or "").lower()
               or k in (f.get("control") or "").lower()]
    if len(partial) == 1:
        return partial[0]
    return partial or None


def choose_grid(info, prefer=None):
    """Pick the grid holding the report's rows, and say when the pick is a guess.

    The default is the biggest visible grid, which is right on a single-result
    screen and a coin toss on a master-detail one. A silent coin toss is
    exactly the failure this project keeps hitting, so when a second grid is
    comparable in size the choice is reported as ambiguous and the caller
    prints it. `prefer` (a grid or dataset name) settles it outright.

    Returns (grid, alternatives) - alternatives is empty when the pick is
    unambiguous."""
    grids = info.get("grids", [])
    if not grids:
        return None, []

    if prefer:
        p = prefer.strip().lower()
        for attr in ("dataset", "name"):
            exact = [g for g in grids if g[attr].lower() == p]
            if exact:
                return exact[0], []
        partial = [g for g in grids
                   if p in g["dataset"].lower() or p in g["name"].lower()]
        if len(partial) == 1:
            return partial[0], []
        if partial:
            return None, partial
        return None, grids

    visible = [g for g in grids if g["visible"]]
    # Sorted here as well as in the JS, so this function is correct on its own
    # terms rather than on an assumption about its caller.
    pool = sorted(visible or grids, key=lambda g: g["area"], reverse=True)
    best = pool[0]
    # Only a grid on a DIFFERENT dataset is a rival. Several grids bound to
    # one dataset are that dataset shown in different tabs or variants -
    # identical rows, so exporting it is not a guess. P3131UM00 has nine
    # grids on `dsP3131UM0003DVO` (Main/Sub/Smd x 01-03) and the run refused
    # with "more than one plausible result grid" naming seven of them, all
    # the same data (HISTORY.md Phase 82.22). gmes_profile.fingerprint()
    # already treats them as one result set for the same reason.
    rivals = [g for g in pool[1:]
              if g["area"] > best["area"] * 0.4 and g["dataset"] != best["dataset"]]
    return best, rivals


def ascii_digits(text):
    """Every decimal digit of any script becomes its ASCII digit: Arabic-Indic
    (٢٠٢٦), Extended Arabic-Indic, full-width (２０２６), and the rest of Unicode's
    Nd class. Everything else is left alone.

    An Arabic keyboard layout types Arabic-Indic digits; `int()` and `\\d` accept
    them but G-MES stores Latin digits, and `strptime` refused them - so a date
    typed as ٢٠٢٦٠٩١٩ was rejected with "is not a real calendar date", which it
    is (HISTORY.md Phase 84.8). The selection grammar had accepted `٣` all along,
    so the same person got two different answers to the same habit."""
    out = []
    for ch in str(text if text is not None else ""):
        try:
            out.append(str(unicodedata.decimal(ch)))
        except (ValueError, TypeError):
            out.append(ch)
    return "".join(out)


def digits_only(value):
    return re.sub(r"[^\d]", "", ascii_digits(value or ""))


def is_pure_number(text):
    """Whether `text` is safe to compare by DIGITS ALONE - a bare number or
    a date written with only digit-grouping punctuation this codebase
    actually uses for dates (-, /, :, space), the shape `digits_only()`
    exists for (2026-09-08 == 20260908).

    Confirmed live, a second time: `.` is a DECIMAL POINT, not a date
    separator this project's own `normalise_date()` ever produces or
    accepts - `values_match("1.2", "12")` was `True`, both reducing to
    digits `"12"`, before `.` was excluded here. A leading `-` or `+` is a
    SIGN, not the mid-string date separator in `2026-09-08` -
    `values_match("-1", "1")` was also `True`, both reducing to `"1"`,
    before a leading sign was excluded. Neither collision is theoretical:
    a quantity field ("Remain Q'ty", a negative adjustment) sits right next
    to date columns on the very screens this tool drives. A mid-string `-`
    or `/` is still accepted (a real date), and a value that fails this
    check still compares correctly as exact text in `values_match()` - it
    only loses the date-reformatting equivalence, which a non-date value
    never needed anyway.

    A narrower residual case is NOT handled: a `/`-separated value shaped
    like a small fraction (`"1/2"`) still reduces to digits like a date
    would (`digits_only("1/2") == "12"`, colliding with a bare `"12"`) -
    `/` cannot simply be excluded the way `.` was, since real dates
    (`2026/09/08`) depend on it. Accepted as an open, narrower risk rather
    than guessed at with a date-shape validator this session could not
    verify against enough real screens to trust."""
    text = str(text or "").strip()
    if not text or text[0] in "+-":
        return False
    return bool(re.fullmatch(r"[\d\-/: ]+", text)) and bool(digits_only(text))


def values_match(wanted, got):
    """The one comparison `verify_rows()`, `apply()` and `type_text()` all
    need: equal as digits-only when BOTH sides are genuinely numbers, equal
    as casefolded text otherwise.

    Checking only ONE side's shape is not enough, and this project shipped
    that exact mistake twice. `is_pure_number(expected)` alone let a result
    of MODEL-B1 verify against an expected MODEL-A1 (both reduce to digit
    "1"); checking only the WANTED side the other way let a wanted "123"
    silently accept an actual value of "X123", confirmed live:
    `verify_rows(..., column, "123")` against a row holding "X123" returned
    `problem=None` - accepted - because "123" alone looked like a number
    worth reducing to digits, and nothing checked what the row actually
    held before doing the same reduction to it. Both sides have to look
    like the same kind of value before comparing them as one."""
    wanted_text = str(wanted or "").strip()
    got_text = str(got or "").strip()
    if is_pure_number(wanted_text) and is_pure_number(got_text):
        return digits_only(wanted_text) == digits_only(got_text)
    return wanted_text.casefold() == got_text.casefold()


# ===========================================================================
# Dataset-level operations
#
# These take a screen code and a dataset name rather than a Screen, so the
# one hardcoded job (gmes_daily_prodplan.py) drives exactly the same code as
# the generic runner. The alternative - a second implementation for the job -
# is how the lying row count in Phase 10.4 happened.
# ===========================================================================

def tick_org(ws, form_code, dataset, names, exclusive=True, paths=None):
    """Tick entries in a category tree by their visible name.

    `paths` - the exact, already window-scoped tree instances a caller's own
    `trees()`/`org_trees()` discovery already found - restricts the write to
    THIS run's own work window. Without it, `form_code` (the tree's own
    shared form name, e.g. "OrgCategory_GDS" - not the work screen's code)
    is searched app-wide, which can reach a completely different window's
    copy of the same reusable tree component (HISTORY.md - external review
    of 1957ba9/cff282b, finding #5). Kept as the fallback for a caller with
    no window-scoped discovery available."""
    if isinstance(names, str):
        names = [names]
    return evaluate(ws, _js(JS_TICK_ORG, gmes_data.JS_HELPERS,
                            cdp_common.json.dumps(dataset),
                            cdp_common.json.dumps(list(names)),
                            "true" if exclusive else "false",
                            cdp_common.json.dumps(form_code),
                            cdp_common.json.dumps(list(paths) if paths else [])))


def org_selection(ws):
    """What the SCREEN says is selected, e.g. {'org': 'VD'}.

    Read back after ticking, because the dataset write is not proof. The
    write went into one of three copies of the tree while the visible one
    still had MOBILE ticked by hand; the run reported VD and exported
    MOBILE's rows. This label is what the screen itself believes."""
    try:
        return evaluate(ws, _js(JS_ORG_SELECTION, cdp_common.JS_IS_VISIBLE))
    except Exception as e:
        return {"found": False, "reason": str(e)}


def read_rows(ws, form_code, dataset, limit=-1, path=None):
    return gmes_data.read_dataset(ws, form_code, dataset, limit=limit, path=path)


def date_keys_of_json(text):
    """`{"20260919": {...}, "Total": {...}}` -> {"20260919"}: the real calendar
    dates among an object's keys. None when the text is not a JSON object.
    Non-date keys ("Total") are ignored, and an 8-digit key that is not a real
    date (20261399) does not count as one."""
    text = (text or "").strip()
    if not text.startswith("{"):
        return None
    try:
        obj = json.loads(text)
    except ValueError:
        return None
    if not isinstance(obj, dict):
        return None
    keys = set()
    for key in obj:
        key = str(key)
        if re.fullmatch(r"\d{8}", key):
            try:
                datetime.strptime(key, "%Y%m%d")
            except ValueError:
                continue
            keys.add(key)
    return keys


def json_date_keys(values):
    """The dates a column carries INSIDE its values, for a column of date-keyed
    JSON objects - `(keys, problem)`, or `(None, None)` when the column is not
    that shape (the caller then compares the values themselves).

    HISTORY.md Phase 83.3: B3320UM00's result has no date column at all. Its
    date is a KEY inside `jsonObj` ({"20260919": {...}, "Total": {...}}) and in
    the column headers built from it, and `baseDate` is empty on every row - so
    verifying the rows against a date had nothing to compare. A column that
    holds such objects is verified by the dates it names."""
    values = [v for v in values if v]
    if not any(v.lstrip().startswith("{") for v in values):
        return None, None
    keys = set()
    for v in values:
        found = date_keys_of_json(v)
        if found is None:
            return set(), "some of its values are not readable JSON objects"
        keys |= found
    if not keys:
        return set(), "none of its values holds a date key"
    return keys, None


def timestamp_date_part(value):
    """The `YYYYMMDD` half of a `YYYYMMDDHHMMSS` timestamp, or None.

    Exactly 14 digits, the first 8 a real calendar date, the last 6 a real
    `HH:MM:SS` - a length check alone would also accept a 14-digit value that
    is not a date-plus-time at all (an id, a lot number). A plain 8-digit
    value is not this shape either; the caller's ordinary date comparison
    already handles those, and must keep doing so unweakened.

    Q3211UM00/Q3341UM00 (HISTORY.md Phase 84.24, Open Item 66): `--verify
    outStopRegDt=20260920` refused a genuinely same-day row because the
    column stores `20260920083443` and both `verify_rows()` and
    `verify_date_range()` compared it as an exact 8-digit value instead of
    checking whether ITS DATE falls where asked."""
    digits = digits_only(value)
    if len(digits) != 14:
        return None
    date_part, time_part = digits[:8], digits[8:]
    try:
        datetime.strptime(date_part, "%Y%m%d")
        datetime.strptime(time_part, "%H%M%S")
    except ValueError:
        return None
    return date_part


def blank_rows_note(column, blanks, total):
    """A sentence saying how many rows verification could NOT check, or None.

    HISTORY.md Phase 92.4 (Open Item 86): rows whose checked column is empty
    are skipped - rightly, for Nexacro's filler/subtotal rows (CLAUDE.md 3.6) -
    but silently, so 900 blank-date rows beside 10 right-date rows read as
    "verified". Never a refusal (filler rows are legitimate); always said."""
    if not blanks:
        return None
    text = (f"{blanks} of {total} rows have no {column} value, so only the other "
            f"{total - blanks} were checked against the requested value")
    if blanks * 2 > total:
        text += " - MOST of the result is unverified"
    return text


def verify_rows(ws, form_code, dataset, column, expected, sample=None, path=None,
                notes=None):
    """Confirm the returned rows carry the value that was asked for.

    A stale result set looks exactly like a fresh one, and an export of the
    wrong day is worse than no export. Digits are compared rather than text,
    so 2026-09-08 and 20260908 are the same answer.

    Returns (values_seen, problem). `problem` is None when the result agrees;
    the caller decides whether a disagreement is fatal."""
    result = read_rows(ws, form_code, dataset, limit=-1, path=path)
    if not result.get("found"):
        return None, "the result dataset disappeared before verification"
    if not result["rows"]:
        return None, "the result dataset contains no rows to verify"
    if column not in result["columns"]:
        near = [c for c in result["columns"] if column.lower() in c.lower()]
        return None, (f"the result has no {column!r} column"
                      + (f" - did you mean {near[:4]}?" if near else ""))
    expected_text = str(expected or "").strip()
    if not expected_text:
        return None, "verification needs an expected value"
    # Compared per row, not by one global "is the EXPECTED value a number"
    # flag - a row's own value can be a different shape than what was
    # asked for, and only checking the expected side let a wanted "123"
    # silently accept an actual "X123" (see values_match()'s docstring).
    raw = [str(r.get(column) or "").strip() for r in result["rows"]]
    blanks = sum(1 for v in raw if not v)
    raw = [v for v in raw if v]
    if not raw:
        return [], f"the results contain no values in {column!r}"
    note = blank_rows_note(column, blanks, len(result["rows"]))
    if note and notes is not None:
        notes.append(note)
    keys, why = json_date_keys(raw)
    if why:
        return [], f"{column!r} looked like dates inside JSON, but {why}"
    if keys is not None:
        seen = sorted(keys)
        if keys != {digits_only(expected_text)}:
            return seen, (f"the results carry {column} dates {seen}, not exactly "
                          f"the requested {expected_text}")
        return seen, None
    seen = sorted(set(raw))
    try:
        expected_date = normalise_date(expected_text)
    except ValueError:
        expected_date = None
    # A raw value that fails the ordinary exact comparison gets ONE more
    # chance, and only when `expected` is itself a real date: a
    # YYYYMMDDHHMMSS timestamp whose OWN date matches is a same-day answer,
    # not a mismatch, even though its 14 digits never equal 8. This must not
    # fire for a non-date `expected` (P3111UM00's plantCode=P701) - that is
    # exactly the "both sides must look like the same kind of value" mistake
    # values_match()'s own docstring already warns about.
    def matches(v):
        if values_match(expected_text, v):
            return True
        return expected_date is not None and timestamp_date_part(v) == expected_date
    if any(not matches(v) for v in raw):
        return seen, (f"the results carry {column}={seen}, not exactly the "
                      f"requested {expected_text}")
    return seen, None


def verify_date_range(ws, form_code, dataset, column, date_from, date_to, path=None,
                      notes=None):
    """Confirm every row's date column falls WITHIN [date_from, date_to]
    inclusive - `verify_rows()`'s range equivalent.

    Confirmed live: a genuine multi-day query (`--from 20260901 --to
    20260910` on P1112UM00, 6529 rows) correctly returned rows spanning
    that whole window - and `verify_rows()`, which only ever compares
    every row to ONE expected value (`date_from`, since `--verify COLUMN`
    with no explicit `=VALUE` had no other value to use), rejected the
    entire, genuinely correct answer: "the results carry
    creYmd=['20260901', '20260902', ..., '20260909'], not exactly the
    requested 20260901". A multi-day report could never pass verification
    at all, which is worse than not verifying - it teaches a person to
    stop using `--verify` rather than trust it.

    Returns (values_seen, problem), matching `verify_rows()`'s shape."""
    result = read_rows(ws, form_code, dataset, limit=-1, path=path)
    if not result.get("found"):
        return None, "the result dataset disappeared before verification"
    if not result["rows"]:
        return None, "the result dataset contains no rows to verify"
    if column not in result["columns"]:
        near = [c for c in result["columns"] if column.lower() in c.lower()]
        return None, (f"the result has no {column!r} column"
                      + (f" - did you mean {near[:4]}?" if near else ""))
    lo, hi = digits_only(date_from), digits_only(date_to)
    if len(lo) != 8 or len(hi) != 8:
        return None, f"the range {date_from}-{date_to} is not two YYYYMMDD dates"
    raw = [str(r.get(column) or "").strip() for r in result["rows"]]
    blanks = sum(1 for v in raw if not v)
    raw = [v for v in raw if v]
    if not raw:
        return [], f"the results contain no values in {column!r}"
    note = blank_rows_note(column, blanks, len(result["rows"]))
    if note and notes is not None:
        notes.append(note)
    keys, why = json_date_keys(raw)
    if why:
        return [], f"{column!r} looked like dates inside JSON, but {why}"
    if keys is not None:
        seen = sorted(keys)
        outside = [k for k in seen if not (lo <= k <= hi)]
        if outside:
            return seen, (f"the results carry {column} dates outside the "
                          f"requested {date_from}-{date_to}: {outside}")
        return seen, None
    seen = sorted(set(raw))
    # Same-width YYYYMMDD strings sort and compare lexicographically the
    # same as chronologically; a value that does not even reduce to 8
    # digits cannot be compared at all UNLESS it is a YYYYMMDDHHMMSS
    # timestamp, whose own date part can be - and counts as out of range
    # rather than being silently skipped otherwise.
    def in_range(v):
        digits = digits_only(v)
        if len(digits) == 8:
            return lo <= digits <= hi
        ts_date = timestamp_date_part(v)
        return ts_date is not None and lo <= ts_date <= hi
    out_of_range = sorted({v for v in raw if not in_range(v)})
    if out_of_range:
        return seen, (f"the results carry {column} values outside the "
                      f"requested {date_from}-{date_to}: {out_of_range}")
    return seen, None


class InquirySettle:
    """The settle-decision core of `poll_inquiry()`, isolated from the
    browser calls so it can be unit-tested against a synthetic sequence of
    row-count readings instead of only ever being verified live.

    Requiring `changed` (the count differing from either the pre-click
    snapshot OR the previous poll) was meant to stop a dataset left
    populated by an earlier run from being mistaken for a finished query.
    Live-traced instead of assumed (HISTORY.md Phase 65.2): replaying
    M4131UM00 with the SAME division right after recording it produced the
    SAME correct 10-row answer, and the count did not merely fail to differ
    from `before` - it stayed at exactly 10 on every single poll, never
    dipping through 0, for 17+ seconds of direct observation. Nexacro does
    not always visibly clear a dataset before refilling it; sometimes a
    re-query updates it in place with no observable transition at all. A
    live probe for any OTHER signal (an overlay/spinner class, the Inquiry
    button's own class toggling) found none either - row count genuinely is
    the only thing available to poll here.

    So `changed` can no longer be a hard requirement without reintroducing
    the 300s hang this was traced to fix, but dropping it outright would
    remove the one thing standing between "the query genuinely re-ran" and
    "the click landed but nothing happened, and stale data is just sitting
    there" - `click_control()` finding and clicking the right element is
    real but not total assurance the query actually ran. The compromise:
    settle quickly (`settle_checks`) when a change WAS observed - the
    common, unambiguous case, unchanged from before - and settle only after
    much longer, sustained stability (`unconfirmed_settle_checks`, default
    3x) when no change was ever observed. Real confidence is still rewarded
    with a fast return; its absence costs patience, not a five-minute
    failure on a result that was correct the entire time.

    A zero count used to be a hard exception to all of this: `step()` reset
    `stable` to 0 on every zero reading and never returned a settled 0, no
    matter how long the count held there. Live-traced (HISTORY.md Phase
    71): a query against a date range with genuinely no matching rows -
    completely ordinary in production, e.g. no orders yet for a future
    date - burned the full 300s `max_wait` and then failed with "the query
    had not settled", every time, for an answer that was correct within a
    second of being clicked. Zero is not special for WHETHER it settles:
    the same asymmetric settle/unconfirmed-settle logic already trusted
    for a nonzero count applies to it too, so a genuinely empty result
    settles rather than an unconditional 300s failure.

    Zero IS treated specially for what counts as evidence of `changed`,
    though - see `step()`'s own docstring (HISTORY.md Phase 82.16): a
    stale nonzero count dropping to 0 on click is NOT trusted as
    confirmation the query re-ran, only as proof the click registered,
    because Nexacro clears the dataset to 0 unconditionally before the
    real answer (0 or otherwise) has arrived - a stable zero always pays
    the full `unconfirmed_settle_checks` patience, whether or not a stale
    nonzero count preceded it."""

    def __init__(self, before, settle_checks=4, unconfirmed_settle_checks=None):
        self.before = before
        self.previous = before
        self.last = None
        self.stable = 0
        self.changed = False
        self.settle_checks = settle_checks
        self.unconfirmed_settle_checks = unconfirmed_settle_checks or settle_checks * 3

    def step(self, count):
        """Feed one new reading. Returns the settled count, or None to
        keep polling. Zero is not a special case for WHETHER it can
        settle - the same confirmed/unconfirmed threshold applies to it as
        to any other value, so a genuinely empty result settles too, just
        with the same patience an unconfirmed nonzero one already needs.

        Zero IS a special case for what counts as `changed`, though - never
        treated as confirming evidence on its own. Live-caught replaying
        Q2111UM00 (HISTORY.md Phase 82.16): `before` was 175, a stale count
        left over from an earlier successful query on the same screen: the
        very first poll after clicking Inquiry read 0 (Nexacro clears the
        dataset immediately, unconditionally, before the server has
        answered at all - true whether the real answer will be 0 or 175
        again), `count != self.before` fired, `changed` became True, and
        the FAST threshold accepted 4 consecutive 0-reads as "confirmed" -
        while the real answer, also 175, was still in flight and arrived
        moments later. A direct manual re-click of the same Inquiry, same
        filters, immediately afterward returned 175 correctly, proving the
        data was never the problem - only how fast 0 was trusted. Zero
        during a round trip is NOT distinguishable, from a single reading,
        between "still clearing" and "the real, final answer" - so it
        never contributes to `changed` on its own. Only a NONZERO reading
        that differs from the pre-click baseline is real evidence the
        query actually re-ran; a stable zero always pays the same, longer
        `unconfirmed_settle_checks` patience a stable-but-never-observed-
        to-change nonzero count already pays, whether or not a stale
        nonzero count preceded it."""
        if count != 0 and (count != self.before or count != self.previous):
            self.changed = True
        self.previous = count
        self.stable = self.stable + 1 if count == self.last else 0
        self.last = count
        threshold = self.settle_checks if self.changed else self.unconfirmed_settle_checks
        if self.stable >= threshold:
            return count
        return None


def poll_inquiry(ws, form_code, dataset, max_wait=300, settle_checks=4,
                 poll_interval=1.0, path=None, report=None):
    """Click Inquiry and wait for THIS screen's result set to settle.

    Polling a dataset by a hardcoded name reported 875 rows - the count still
    sitting in the PREVIOUS screen's dataset - for a screen that had returned
    17. The export was right because it used the discovered dataset; only the
    number and the wait were wrong, which is the more dangerous combination:
    the file is correct, the log lies, and nobody checks.

    An empty result set does not mean the query finished either. Nexacro
    clears the dataset the instant Inquiry is pressed and refills it when the
    server answers, so the count sits at 0 for the whole round trip. Treating
    ANY stable zero as settled immediately reported 0 rows and refused to
    export while 790 rows were on their way.

    So: prefer the count to have been seen CHANGING, so a dataset left
    populated by an earlier run - or, symmetrically, a zero-row round trip
    still in flight - is not mistaken for a finished query on its first
    stable reading. Zero itself is not otherwise a special case (Phase 71):
    a genuinely empty result settles the same way an unconfirmed nonzero one
    does, after sustained stability, not never. Neither cap is an estimate of
    how long the query takes - the loop exits the moment it has its answer,
    which is what makes a generous cap free.

    "Changed" cannot be a hard requirement, though - confirmed by two rounds
    of live investigation, not assumed (HISTORY.md Phase 65.2). Replaying
    M4131UM00 with the same division right after recording it produced the
    same correct 10-row answer, and directly tracing the poll loop showed
    the count sitting at exactly 10 on EVERY single reading, never dipping
    through 0, for 17+ seconds - not merely "equal to the pre-click value",
    genuinely never changing at all, poll to poll, the whole time. A
    fresh query with an unchanged, correct answer can look, from row count
    alone, IDENTICAL to a click that silently did nothing. A live search for
    any other observable signal (a busy overlay, the Inquiry button's own
    class toggling) found none either - row count is genuinely all there is
    to poll on this screen. See `InquirySettle`'s docstring for how the two
    cases are told apart anyway: fast settlement when a change WAS seen,
    slower (but bounded, not infinite) settlement when it was not."""
    def row_count():
        r = read_rows(ws, form_code, dataset, limit=0, path=path)
        return r.get("total", -1) if r.get("found") else -1

    before = row_count()
    button = gmes_common.click_control(ws, cls="btn_LF_Search_New", text="Inquiry")
    if not button:
        raise RuntimeError("the Inquiry button was not found on this screen")

    tracker = InquirySettle(before, settle_checks)
    started = time.time()

    while time.time() - started < max_wait:
        time.sleep(poll_interval)
        count = row_count()

        # An alert instead of results - usually "no data found", or a
        # validation rejection (e.g. "Start Date is later than End Date").
        # The popup's own title-bar text is unreliable here (confirmed
        # live: empty for a validation alert) - alert_text() reads the
        # real on-screen message instead, when there is one.
        popups = gmes_common.find_child_popups(ws)
        if popups.get("count"):
            texts = alert_text(ws, form_code)
            if texts:
                raise RuntimeError(f"a dialog opened instead of results: {'; '.join(texts)}")
            names = [p["name"] for p in popups["popups"]]
            raise RuntimeError(f"a dialog opened instead of results: {names}")

        settled = tracker.step(count)
        if settled is not None:
            if report is not None:
                report.update(before=before, changed=tracker.changed)
            return settled

    raise RuntimeError(f"the query had not settled after {max_wait}s "
                       f"(last count {tracker.last})")


# ===========================================================================
# Network-level proof that Inquiry actually reached the server
# ===========================================================================
#
# HISTORY.md - external review of 1957ba9/cff282b, finding #14/32: row-count
# settling (InquirySettle above) cannot tell "the click landed and the
# server answered" apart from "the click did nothing and stale data just
# sat there" - both can look identical from row count alone (Phase 65.2's
# own finding). Live-traced rather than guessed at (Phase 82.6): every
# Inquiry click on P1112UM00 produced one or more `POST .../nexacro.do`
# requests (Nexacro's own server-transaction servlet - observed on this
# account as both `.../sm/nexacro.do` and `.../pm/nexacro.do`, the module
# prefix following the screen), each answered with HTTP 200 and a real
# response body (519,530 bytes for a 738-row query; 9,000 bytes for a
# smaller accompanying call the same click also produced). This is that
# evidence, made checkable: TransactionProof is the pure decision logic,
# tested offline against synthetic events; watch_nexacro_transaction() is
# the live glue, using a connection dedicated to events only - see
# cdp_common.open_event_listener()'s own docstring for why sharing the
# evaluate() connection would silently lose these events.
#
# NOT yet wired into poll_inquiry() as a required check: confirmed live
# only on this one screen and account so far, and doing so would mean
# passing every caller's target websocket URL through Screen/open_screen()
# for a connection that today only exists here. Available as a
# supplementary, opt-in check a caller can use directly; making it the
# default is the natural next step, tracked as an open item.

class TransactionProof:
    """Has a real `POST .../nexacro.do` round trip been observed - fed CDP
    Network domain events, never opinion about what SHOULD have happened.

    `feed()` takes a list of raw CDP event dicts (as `cdp_common.
    drain_events()` returns them) and updates `.confirmed`. `.proven` is
    true once at least one matching request got a 2xx response. A request
    seen without its response yet is tracked in `_pending` and resolved
    when (if) the matching `Network.responseReceived` arrives - order
    across two events is not assumed."""

    URL_MARK = "/nexacro.do"

    def __init__(self):
        self._pending = {}
        self.confirmed = []

    def feed(self, events):
        for event in events:
            method = event.get("method", "")
            params = event.get("params", {})
            if method == "Network.requestWillBeSent":
                request = params.get("request", {})
                url = request.get("url", "")
                if request.get("method") == "POST" and self.URL_MARK in url:
                    request_id = params.get("requestId")
                    if request_id:
                        self._pending[request_id] = {"url": url}
            elif method == "Network.responseReceived":
                request_id = params.get("requestId")
                pending = self._pending.pop(request_id, None) if request_id else None
                if pending is None:
                    continue
                status = params.get("response", {}).get("status")
                if isinstance(status, int) and 200 <= status < 300:
                    self.confirmed.append({"url": pending["url"], "status": status})

    @property
    def proven(self):
        return bool(self.confirmed)


def watch_nexacro_transaction(ws_url, max_wait=30, poll_interval=0.3):
    """Watch, on a connection dedicated to events only, for real evidence
    that a Nexacro server round trip happened - see `TransactionProof`'s
    own docstring for what that evidence is and how it was found.

    Best-effort and never fatal on its own: a listener that cannot even be
    opened (e.g. the Network domain refused) returns an unproven
    `TransactionProof` rather than raising, since this is supplementary
    evidence, not the primary proof a caller depends on to decide whether
    a query ran (row-count settling remains that)."""
    proof = TransactionProof()
    try:
        listener = cdp_common.open_event_listener(ws_url)
    except Exception:
        return proof
    try:
        deadline = time.time() + max_wait
        while time.time() < deadline and not proof.proven:
            proof.feed(cdp_common.drain_events(listener))
            time.sleep(poll_interval)
    finally:
        try:
            listener.close()
        except Exception:
            pass
    return proof


# ===========================================================================
# Typing into a control that has no dataset behind it
# ===========================================================================

def _key_events(ch):
    """CDP key parameters for one character.

    `char` carries the actual text, which is what the input consumes; the
    keyDown/keyUp pair is what Nexacro's own onkeyup handlers watch for. The
    code/virtual-key values are only right for letters and digits, and that is
    enough - punctuation still arrives through the `char` event."""
    if ch.isalpha():
        return f"Key{ch.upper()}", ord(ch.upper())
    if ch.isdigit():
        return f"Digit{ch}", ord(ch)
    return "", 0


# How long to let a clicked control open its editor before the first key, one
# entry per attempt. Each retry waits longer: whatever made the first attempt
# race (a busy page, a late focus) has had more time to finish.
TYPE_SETTLE_SECONDS = (0.3, 0.8, 1.5)


def _type_once(ws, dom_id, text, clear, commit, settle):
    """One click-clear-type-commit pass. Returns nothing: the caller reads the
    control back, because that - not the typing - is the evidence."""
    box = evaluate(ws, gmes_common.js_find_by_id(dom_id))
    if not box.get("found"):
        raise RuntimeError(f"the control {dom_id.split('.')[-1]} is not on screen "
                           f"({box.get('reason')})")
    click_element_by_rect(ws, box["x"], box["y"])
    time.sleep(settle)

    if clear:
        dispatch_key_combo(ws, "a", "KeyA", 65, ctrl=True)
        dispatch_key_combo(ws, "Delete", "Delete", 46)
        time.sleep(0.1)

    for ch in str(text):
        code, vk = _key_events(ch)
        for kind in ("keyDown", "char", "keyUp"):
            params = {"type": kind, "key": ch, "code": code}
            if kind == "char":
                params["text"] = ch
            elif vk:
                params["windowsVirtualKeyCode"] = vk
            send(ws, "Input.dispatchKeyEvent", params)
        time.sleep(0.05)

    if commit:
        # Nexacro commits an edit on blur. Without this the value sits in the
        # editor and the Inquiry runs on the previous one.
        dispatch_key_combo(ws, "Tab", "Tab", 9)
        time.sleep(0.3)


def type_text(ws, dom_id, text, clear=True, commit=True, verify=True):
    """Type into a control with real key events, then confirm what it shows.

    Nexacro's own `set_value()` fills a box and changes nothing the control
    reacts to - that is how the screen search box was filled while searching
    for nothing. The suggestion lists, validation and value commits all hang
    off the key handlers, so the keys have to be real.

    A read-back that does not match is retried (HISTORY.md Phase 83): the
    identical typing into R3220UM00's masked date field worked in two runs and
    then produced '9196-0_-__' in a third, with nothing on screen to explain
    it - the click had not finished opening the editor when the first keys
    arrived. Failing an unattended run on that would throw away a nightly
    batch for a hiccup a second try clears. The retry changes nothing but a
    filter's text, and every attempt is still READ BACK: what is accepted is
    what the control shows, never that the keys were sent."""
    if not verify:
        _type_once(ws, dom_id, text, clear, commit, TYPE_SETTLE_SECONDS[0])
        return str(text)
    seen = []
    for settle in TYPE_SETTLE_SECONDS:
        _type_once(ws, dom_id, text, clear, commit, settle)
        shown = evaluate(ws, _js(JS_CONTROL_VALUE, cdp_common.json.dumps(dom_id)))
        got = (shown.get("value") or "").strip()
        # A third, separate call site with the exact class of one-sided/ungated
        # digits_only() comparison found in verify_rows() and apply() (HISTORY.md
        # Phase 66) - here not even gated on the WANTED side being a number, so
        # typing "MODEL-A1" and reading back "MODEL-B1" (both reduce to digit
        # "1") was accepted as having taken correctly. values_match() requires
        # BOTH sides to look like the same kind of value before comparing them
        # as digits at all.
        if values_match(text, got):
            return got
        seen.append(got)
    tried = (f" (after {len(seen)} attempts, it showed {seen})" if len(seen) > 1 else "")
    raise RuntimeError(f"typing into {dom_id.split('.')[-1]} did not take - "
                       f"it shows {seen[-1]!r}, not {str(text)!r}{tried}")


# ===========================================================================
# One open screen
# ===========================================================================

class Screen:
    """A live handle on one open, active G-MES work screen."""

    def __init__(self, ws, code, opened, info):
        self.ws = ws
        self.code = code.strip().upper()
        self.menu_id = opened.get("menuId", "")
        self.win_id = opened.get("winId", "")
        self.title = opened.get("title", "") or self.code
        self.info = info
        self.warnings = []
        self.last_tree = None       # which category tree the division came from
        self.last_inquiry = {}      # what poll_inquiry() saw: before / changed

    # -- introspection ------------------------------------------------------

    def refresh(self):
        """Re-read the screen. Required after anything that rebuilds the left
        panel - a category tab or a Quick View entry replaces the controls,
        so a handle taken before the click points at elements that no longer
        exist."""
        self.info = discover(self.ws, self.code)
        return self.info

    @property
    def filters(self):
        return self.info.get("filters", [])

    @property
    def unbound(self):
        return self.info.get("unbound", [])

    def options(self):
        return left_options(self.ws).get("options", [])

    def trees(self):
        return org_trees(self.ws, self.code).get("trees", [])

    def grid(self, prefer=None):
        grid, rivals = choose_grid(self.info, prefer)
        if grid is None:
            if rivals:
                names = ", ".join(f"{g['name']} -> {g['dataset']}" for g in rivals[:6])
                raise RuntimeError(f"which grid? this screen has: {names}. "
                                   "Name one with --grid.")
            raise RuntimeError("no result grid was found on this screen")
        if rivals:
            names = ", ".join(f"{g['name']}({g['dataset']})" for g in rivals[:6])
            raise RuntimeError(
                f"more than one plausible result grid: {grid['name']}({grid['dataset']}), {names}. "
                "Pass --grid to prove which dataset is the report.")
        return grid

    def follow_grid_rebind(self, grid):
        """The same grid COMPONENT as `grid`, if it is bound to a different
        dataset now than when the screen was discovered - else None.

        Some Nexacro grids are re-bound by the screen's own code once a
        query returns. Live-caught on R5216UM00 (HISTORY.md Phase 82.18):
        before Inquiry `grdDetail` is bound to `dsMntDetailListTemp`, a
        1-column placeholder that never holds a row; once the query
        answers, the screen re-binds it to `dsMntDetailList` (259 rows,
        the "Total 259" on screen). Discovery necessarily ran before that,
        so the run polled a dataset that could never fill and reported
        "the query returned no rows" for a screen showing 259.

        Matched by component name AND form path - a component name is
        unique within its form, so this cannot mistake a neighbouring grid
        that happens to hold the same rows."""
        info = discover(self.ws, self.code)
        for g in info.get("grids", []):
            if (g["name"] == grid["name"] and g.get("path") == grid.get("path")
                    and g["dataset"] != grid["dataset"]):
                return g
        return None

    @staticmethod
    def form_code(entry):
        """The screen code a dataset lives on. `_findForms` matches on the
        file name, so the .xfdl.js suffix has to come off."""
        return (entry.get("form") or "").replace(".xfdl.js", "")

    # -- setting ------------------------------------------------------------

    def set_option(self, wanted, verify_wait=6):
        """Select a left-panel option, unless it already is, and confirm it.

        `wanted` is a profile entry carrying the component's stable Nexacro
        name, or a plain string (what `--option` passes, and what every profile
        written before HISTORY.md Phase 76 holds). `resolve_option()` decides
        which; this method is deliberately not where that logic lives, so the
        matching can be tested without a browser.

        Returns a dict: `outcome` for the log, plus the resolved stable
        identity, so the caller can write THAT into the profile rather than
        whatever text it was asked with. That is what makes an old
        English-labelled profile heal itself on its next successful run."""
        found = left_options(self.ws)["options"]
        opt, how = resolve_option(found, wanted)
        identity = option_identity(opt)

        def done(outcome):
            return {"outcome": outcome, "matched_by": how, **identity}

        # JS_LEFT_OPTIONS reports a BUTTON-style option as "selected"/"not
        # selected" but a CHECKBOX-style one as "checked"/"unchecked" - only
        # "selected" counted as already-on here, so asking to switch on an
        # option that was a checkbox and was ALREADY checked fell through
        # to the click below and toggled it OFF instead of leaving it
        # alone, silently changing what the query means in the opposite
        # direction from what was asked.
        if opt["state"] in ("selected", "checked"):
            return done(f"{opt['label']} [{identity['key']}] "
                        f"(already {opt['state']})")

        # Live-traced (HISTORY.md Phase 71.2): a click on a genuinely
        # disabled option (Nexacro's own `enable` flag false - a screen-state
        # precondition, e.g. a date-kind toggle only meaningful under a
        # different category tab) lands at the exact right coordinates and
        # changes nothing at all. Caught here, before clicking, so the
        # failure says WHY rather than looking identical to an unproven
        # click that might have actually worked.
        if not opt.get("enabled", True):
            raise RuntimeError(
                f"option {opt['label']!r} is disabled in the current screen "
                "state - nothing was clicked")

        click_element_by_rect(self.ws, opt["x"], opt["y"])

        deadline = time.time() + verify_wait
        outcome = None
        while time.time() < deadline:
            time.sleep(0.5)
            now = left_options(self.ws)["options"]
            # Re-found by component NAME, not by label. The click can rebuild
            # the panel, and a rebuilt panel is exactly where a label could
            # come back rendered differently - re-identifying the control by
            # the thing that does not change is the point of this whole phase.
            cur = next((o for o in now
                        if (o.get("name") or "").lower() == (opt.get("name") or "").lower()),
                       None)
            if cur and cur["state"] in ("selected", "checked"):
                outcome = f"{cur['label']} [{identity['key']}] -> {cur['state']}"
                break
        # The panel may have been rebuilt by that click, which invalidates
        # every control path discovered before it.
        self.refresh()
        if outcome is None:
            raise RuntimeError(
                f"could not prove option {opt['label']!r} "
                f"[{identity['key']}] was selected")
        return done(outcome)

    def select_org(self, names, tree=None, prefer=None, exclusive=True):
        """Tick one or more entries in a category tree.

        Without this the query returns nothing at all and the screen says
        "Select Search Criteria" - the single most likely cause of a silent
        empty export.

        The tree is found by shape rather than by its name, so the Prod / Fac
        / Proc tabs work as well as Org. `exclusive` clears ticks this run did
        not ask for: a tick survives between runs exactly as a typed filter
        does, and one left behind widens the query without saying so."""
        if isinstance(names, str):
            names = [names]
        names = [n for n in names if n and n.strip()]
        if not names:
            return None

        found = self.trees()
        settable = [t for t in found if t["settable"]]
        if not settable:
            raise RuntimeError("this screen has no category tree to tick")

        wanted_low = {n.strip().lower() for n in names}
        if tree:
            low = tree.lower()
            pool = [t for t in settable
                    if low in t["dataset"].lower() or low in t["form"].lower()]
            if not pool:
                have = ", ".join(f"{t['form']}.{t['dataset']}" for t in settable[:6])
                raise RuntimeError(f"no category tree matching {tree!r}. Present: {have}")
        else:
            # Choose by DATA: the tree that actually contains what was asked
            # for. Picking the first tree, or the biggest, is a guess that
            # fails silently on a screen with four of them.
            pool = [t for t in settable
                    if wanted_low <= {n.strip().lower() for n in t["names"]}]
            if not pool:
                have = sorted({n for t in settable for n in t["names"]})[:20]
                raise RuntimeError(
                    f"{', '.join(names)} is not in any category tree on this "
                    f"screen. Present: {have}")
            if len(pool) > 1:
                # A name can live in more than one tree - "VD" is in the Org
                # tree and the Prod one. `prefer` is the tree a previous run
                # actually proved, so a remembered screen stops re-deciding
                # this. It is only a preference: if the tree that worked
                # before does not hold what is being asked for now, the
                # data-driven choice still applies.
                chosen = [t for t in pool if prefer
                          and prefer.lower() in (t["dataset"].lower(),
                                                 t["form"].lower())]
                if chosen:
                    pool = chosen
                else:
                    choices = ", ".join(f"{t['form']}.{t['dataset']}" for t in pool[:6])
                    raise RuntimeError(
                        f"{names[0]!r} appears in multiple category trees: {choices}. "
                        "Pass --tree to select the intended one.")

        target = pool[0]
        # Remembered so a successful run can record WHICH tree the division
        # came from - a screen with four trees can have the same name in more
        # than one, and next time we want the one that worked.
        self.last_tree = {"form": self.form_code(target) or target["form"],
                          "dataset": target["dataset"], "entry": names[0]}
        # Every COPY of this same logical tree, but only the ones `trees()`
        # itself already found - which is window-scoped (org_trees() anchors
        # on self.code, this screen's own work-screen code, not the tree's
        # shared form name). Passing their exact paths is what stops
        # tick_org()'s own app-wide fallback search from reaching a
        # DIFFERENT window's copy of the same reusable tree component
        # (HISTORY.md - external review of 1957ba9/cff282b, finding #5).
        same_tree_paths = [t["path"] for t in found
                           if t["form"] == target["form"]
                           and t["dataset"] == target["dataset"] and t.get("path")]
        result = tick_org(self.ws, self.last_tree["form"],
                          target["dataset"], names, exclusive=exclusive,
                          paths=same_tree_paths)
        if not result.get("found"):
            raise RuntimeError(
                f"could not tick {', '.join(names)}: "
                f"{result.get('reason') or 'missing ' + str(result.get('missing'))}. "
                f"Present: {result.get('available', '(tree not loaded)')}")

        # Now ask the SCREEN what it thinks is selected. Writing the dataset
        # is not proof: a run announced "VD" while the screen still had MOBILE
        # ticked by hand, queried MOBILE, and delivered 288 rows of MOBILE
        # data in a file labelled VD. Nothing in the log looked wrong.
        deadline = time.time() + 10
        shown = {}
        while time.time() < deadline:
            shown = org_selection(self.ws)
            if not shown.get("found"):
                break                          # no such label on this screen
            got = (shown.get("org") or "").strip().lower()
            if any(got == n.strip().lower() for n in names):
                result["confirmed"] = shown.get("org")
                return result
            time.sleep(0.5)

        if shown.get("found"):
            raise RuntimeError(
                f"the division did not take: asked for {', '.join(names)}, but "
                f"the screen still shows {shown.get('text')!r}. Refusing to "
                f"query the wrong organisation.")
        raise RuntimeError(
            f"could not confirm that {', '.join(names)} is active on the screen")

    def set_filter(self, key, value):
        """Set one filter by label, column or control name.

        Bound controls are written through their dataset - Nexacro binds the
        two, so the visible field follows and there is no date picker or combo
        widget to fight. Unbound controls are typed into instead; a screen
        that sets its filters in code (Q2241UM00 binds none) used to be
        undriveable for that reason."""
        flt = match_filter(self.info, key)
        if flt is None:
            hint = ""
            if any(w in key.lower() for w in ("division", "org", "category",
                                              "attribute", "plant", "std")):
                hint = (" That looks like an organisation choice: use the "
                        "Division prompt / --division, or --option for the "
                        "Org / Prod / Fac / Proc and STD / PLANT controls.")
            raise RuntimeError(f"no filter matches {key!r} on this screen "
                               f"(run 'describe {self.code}' to see them)." + hint)
        if isinstance(flt, list):
            names = ", ".join(f["label"] or f["column"] or f["control"] for f in flt[:6])
            raise RuntimeError(f"{key!r} is ambiguous - matches: {names}")
        return flt, self.apply(flt, value)

    def apply(self, flt, value):
        """Write one discovered control, whichever mechanism it needs.

        The read-back is checked, but a difference is only fatal when the
        field came back EMPTY - that is the failure mode that matters, a write
        that did not land. A field that came back changed is usually Nexacro
        normalising its own value (a mask reformatting a date, a combo storing
        a code for a label), so that is reported rather than treated as a
        failure and used to abort a good run."""
        if flt.get("bound"):
            result = gmes_data.set_filter(self.ws, self.form_code(flt),
                                          flt["dataset"], {flt["column"]: value},
                                          path=flt.get("path"))
            if not result.get("found"):
                raise RuntimeError(f"could not write {flt['dataset']}.{flt['column']}")
            applied = result["applied"].get(flt["column"])
            wanted, got = str(value or "").strip(), str(applied or "").strip()
            if not values_match(wanted, got):
                raise RuntimeError(f"{flt['column']} did not take: asked for {value!r}, "
                                   f"but reads back as {applied!r}")
            return applied
        if not flt.get("id"):
            raise RuntimeError(f"{flt.get('control')} has no dataset behind it and "
                               "no reachable element - it cannot be set")
        return type_text(self.ws, flt["id"], value)

    def find_ref(self, ref):
        """Locate the control a saved profile refers to, on the screen as it
        is right now. Matched by dataset+column, or by control name for the
        unbound ones - never by anything positional.

        When the saved reference carries a `stable_path` (every profile
        written since HISTORY.md - external review of 1957ba9/cff282b,
        finding #3), only a control at that exact relative path counts as a
        match - a dataset+column pair that merely exists somewhere else is
        not "close enough", it is evidence a reusable component now has more
        than one instance, and this returns None exactly as if the control
        had vanished, rather than silently replaying against whichever one
        happens to be found. Profiles written before that fix carry no
        `stable_path` at all and keep matching by dataset+column alone,
        unchanged - the precision only applies going forward.

        Returns None if it has gone, which is the caller's signal to stop
        trusting the profile."""
        if not ref:
            return None
        for f in self.filters + self.unbound:
            if ref.get("stable_path") and f.get("stable_path") != ref["stable_path"]:
                continue
            if ref.get("column") and f.get("column") == ref["column"] \
                    and f.get("dataset") == ref["dataset"]:
                return f
            if not ref.get("column") and f.get("control") == ref.get("control"):
                return f
        return None

    def set_date_range(self, from_value, to_value, profile=None):
        """Put the caller's own two dates into this screen's period fields.

        No date is ever calculated here. The two values arrive already
        decided; this only finds where they go and confirms they landed.

        A profile, when one has been proven, says which fields those are. On
        an unlearned screen they are worked out from the screen itself, and
        anything genuinely ambiguous stops the run rather than picking."""
        frm = self.find_ref(profile.get("from")) if profile else None
        to = self.find_ref(profile.get("to")) if profile else None
        # A profile's own ref (gmes_profile.field_ref()) carries the width a
        # NON-empty sighting of this field once proved (Open Item 41) - kept
        # alongside the freshly re-found control, since find_ref() returns
        # the live discovered filter, which has no memory of its own.
        from_width = (profile.get("from") or {}).get("width") if profile else None
        to_width = (profile.get("to") or {}).get("width") if profile else None

        if frm is None and to is None:
            frm, to, singles = date_targets(self.info)
            from_width = to_width = None       # freshly discovered - nothing remembered yet
            if frm is None and to is None:
                if not singles:
                    raise RuntimeError("this screen has no date field for the requested period")
                if from_value != to_value:
                    names = ", ".join(s["column"] or s["control"] for s in singles)
                    raise RuntimeError(
                        f"this screen has no from/to pair - only {names}. "
                        "Give --from and --to the same value, or name the field "
                        "with --set.")
                frm, to = singles[0], None

        written = []
        targets = ((frm, from_value, "--from", from_width),)
        if to is not None:
            targets += ((to, to_value, "--to", to_width),)
        for flt, value, which, remembered_width in targets:
            if value is None:
                continue
            if flt is None:
                # Refusing here rather than setting one end of the range and
                # querying a period nobody asked for.
                have = ", ".join(f["column"] for f in self.filters
                                 if is_date_field(f)) or "none"
                raise RuntimeError(f"this screen has no field for {which} "
                                   f"(date fields found: {have})")
            fitted = fit_date_to_field(value, flt.get("value"), remembered_width)
            self.apply(flt, fitted)
            written.append((flt, fitted))
        return written

    def set_date(self, yyyymmdd):
        """Apply a date to whatever period fields the screen has.

        Returns a list of (column, written) so the caller can print exactly
        what happened - including nothing, on a screen with no date at all."""
        frm, to, singles = date_targets(self.info)
        pair = [f for f in (frm, to) if f]
        if pair:
            targets = pair
        elif singles:
            # One date field is unambiguous. Several, with no from/to naming
            # among them, are not - setting them all would be a guess about
            # what each one means, so only the first is set and the rest are
            # named so the caller can set them explicitly.
            targets = singles[:1]
            if len(singles) > 1:
                others = ", ".join(f["column"] or f["control"] for f in singles[1:])
                self.warnings.append(
                    f"this screen has more than one date field; only "
                    f"{targets[0]['column'] or targets[0]['control']} was set. "
                    f"Others left alone: {others}")
        else:
            targets = []

        written = []
        for flt in targets:
            value = fit_date_to_field(yyyymmdd, flt.get("value"))
            self.apply(flt, value)
            written.append((flt["column"] or flt["control"], value))
        return written

    def clear_stale(self, keep=()):
        """Blank the free-text filters left behind by an earlier run.

        G-MES keeps a screen alive behind its tab, and a value typed into a
        filter STAYS there. A run asking only for a date returned zero rows
        because a Production Order from the previous run was still in the box:
        the date was right, the division was right, and the answer was empty
        with nothing to indicate why.

        Only `edt` text boxes are cleared, and only ones this run did not set.
        Combos and checkboxes hold meaningful defaults (`paramTecoYn` = "All",
        a status list = "1^2^3^4") and emptying those breaks the query a
        different way. Unbound boxes are only REPORTED: they are filled by the
        screen's own code, so a blank one may be a state the screen never
        expects to see."""
        cleared, noted, failed = [], [], []
        for f in self.filters:
            if not (f.get("control") or "").lower().startswith("edt"):
                continue
            if f["column"] in keep or not (f.get("value") or "").strip():
                continue
            try:
                self.apply(f, "")
                cleared.append(f"{f['label'] or f['column']}={f['value']}")
            except RuntimeError:
                # This used to be silently swallowed - exactly the failure
                # mode this method exists to prevent (a leftover filter from
                # an earlier run quietly narrowing or emptying the answer),
                # just moved one level down: instead of an OLD value never
                # being asked to leave, it is a value that refused to leave
                # when asked. Refusing to proceed matches how every other
                # "the screen still shows a value we did not want" case in
                # this project is already handled (verify_column's strict
                # mode, select_org() refusing an unconfirmed division) -
                # the query must not run with a leftover value still in a
                # filter box this run tried and failed to clear.
                failed.append(f"{f['label'] or f['column']}={f['value']}")
        if failed:
            raise RuntimeError(
                f"could not clear the leftover value(s) {failed} from an earlier "
                f"run - refusing to query with them possibly still in effect.")
        for u in self.unbound:
            if not (u.get("control") or "").lower().startswith("edt"):
                continue
            if (u.get("value") or "").strip():
                noted.append(f"{u['label'] or u['control']}={u['value']}")
        if noted:
            self.warnings.append("left as-is (the screen fills these in code): "
                                 + ", ".join(noted))
        return cleared

    # -- running ------------------------------------------------------------

    def inquiry(self, grid, **kwargs):
        """Click Inquiry and wait for THIS screen's result set to settle.

        What the poll saw - the count before the click, and whether it ever
        changed - is kept on `last_inquiry`, because "it never changed" is
        the one observable that separates an answer from static content
        (HISTORY.md Phase 82.21)."""
        self.last_inquiry = {}
        return poll_inquiry(self.ws, self.form_code(grid), grid["dataset"],
                            path=grid.get("path"), report=self.last_inquiry, **kwargs)

    def rows(self, grid, limit=-1):
        return read_rows(self.ws, self.form_code(grid), grid["dataset"], limit=limit,
                         path=grid.get("path"))

    def verify_column(self, grid, column, expected, sample=8, strict=True):
        """Confirm the returned rows really carry the value that was asked for."""
        seen, problem = verify_rows(self.ws, self.form_code(grid), grid["dataset"],
                                    column, expected, sample=sample, path=grid.get("path"),
                                    notes=self.warnings)
        if problem:
            if strict:
                raise RuntimeError(problem + ". Refusing to export the wrong data.")
            self.warnings.append(problem)
        return seen

    def verify_date_range(self, grid, column, date_from, date_to, strict=True):
        """Confirm every row's date column falls within [date_from, date_to]."""
        seen, problem = verify_date_range(self.ws, self.form_code(grid), grid["dataset"],
                                          column, date_from, date_to, path=grid.get("path"),
                                          notes=self.warnings)
        if problem:
            if strict:
                raise RuntimeError(problem + ". Refusing to export the wrong data.")
            self.warnings.append(problem)
        return seen

    def date_like_columns(self, grid, sample=3):
        """Result columns that really are dates - what `--verify` can be given.

        The first version asked only whether every sampled value was six or
        eight digits. On the Production Plan result that reported `prodTime`
        (000025 - a time), `planWeekno` (202636 - a week) and `modelDesc`
        (65856560 - a model) as "dates that came back", under a heading
        promising dates.

        It is the same mistake as offering every dataset with a `commonName`
        column as an organisation tree: **shape is not identity**. The column
        has to be NAMED like a date as well as look like one."""
        result = self.rows(grid, limit=sample)
        if not result.get("found") or not result["rows"]:
            return []
        out = []
        for c in date_named_columns(result["columns"]):
            values = [digits_only(r.get(c)) for r in result["rows"]]
            values = [v for v in values if v]
            if values and all(len(v) in (6, 8) for v in values):
                out.append(c)
        # A column whose values are date-keyed JSON objects (B3320UM00's
        # `jsonObj`) carries its dates inside - offered as a verify column too.
        for c in result["columns"]:
            if c in out:
                continue
            keys, why = json_date_keys([str(r.get(c) or "").strip() for r in result["rows"]])
            if keys and not why:
                out.append(c)
        return out

    def candidate_verify_columns(self, grid):
        """Column names worth suggesting for `--verify`, before Inquiry has
        run and there are no rows to confirm a real date shape against.

        Reads only the dataset's own column list (`getColID`), which exists
        independently of row count, so this works on the 0-row screen a
        RECORD run always starts from. Naming-only - a person still decides;
        `verify_column()` is what actually enforces the answer once rows
        exist."""
        try:
            result = self.rows(grid, limit=0)
        except Exception:
            return []
        if not result.get("found"):
            return []
        return date_named_columns(result.get("columns", []))

    # -- output -------------------------------------------------------------

    def export_excel(self, target_dir, timeout=240):
        return download_excel(self.ws, target_dir, timeout=timeout)

    def to_csv(self, grid, path):
        """Write the result dataset as CSV.

        Only completely empty rows are dropped. That is deliberately weaker
        than the Production Plan job, which drops rows with no `poNo` because
        it knows those are LINE SUM / PROC SUM subtotals. On an unknown screen
        there is no equivalent key, and guessing one would silently discard
        real data - filtering a single PO returned four dataset rows for one
        visible line, three of them continuation rows the grid merges.

        Returns (path, written, total).

        Columns that look like a credential (CLAUDE.md 2.3: `dsAnyframeDVO`
        carries `tokenId`/`refreshTokenId` for the signed-in session) are
        withheld, the same redaction `gmes_log.py` already applies to
        console/log output - applied here too, since a CSV file is exactly
        the kind of place CLAUDE.md 2.3 already says never to paste one."""
        import csv as _csv
        result = self.rows(grid, limit=-1)
        if not result.get("found") or not result["rows"]:
            return None, 0, 0
        cols, dropped = gmes_data.redact_sensitive_columns(result["columns"])
        if dropped:
            self.warnings.append(
                f"withheld from CSV, column name looks like a credential: {dropped}")
        real = [r for r in result["rows"]
                if any((r.get(c) or "").strip() for c in cols)]
        directory = os.path.dirname(os.path.abspath(path)) or "."
        os.makedirs(directory, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".gmes-csv-", suffix=".partial", dir=directory)
        try:
            with os.fdopen(fd, "w", newline="", encoding="utf-8-sig") as fh:
                writer = _csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(gmes_data.safe_rows_for_csv(real, cols))
            os.replace(temporary, path)
        except Exception:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise
        return path, len(real), len(result["rows"])

    # -- lifecycle ----------------------------------------------------------

    def activate(self, max_wait=20):
        return gmes_open_screen.activate_screen(self.ws, self.win_id, max_wait=max_wait)

    def _is_open(self):
        open_now = {r.get("winId") for r in
                    gmes_open_screen.open_screens(self.ws).get("rows", [])}
        return self.win_id in open_now

    def close(self, timeout=20):
        """Close this screen's tab, and confirm it actually went.

        Screens accumulate: every one stays alive behind its tab holding its
        filters, its result set and its memory, and a long batch ends with a
        dozen of them. Returns (True, detail) or (False, reason) - it never
        clicks something it cannot identify as a close control.

        Tries the visible X first, since that is what a person would do and
        it needs no knowledge of Nexacro internals - but not every tab shape
        has one (a real "Production Plan by Order(Line)" tab's element had
        only a label child, confirmed live, HISTORY.md Phase 60.1). Falls
        back to calling the application's own close-tab handler directly,
        which needs only the win_id already in hand."""
        target = evaluate(self.ws, _js(JS_TAB_CLOSE_TARGET, cdp_common.JS_IS_VISIBLE,
                                       cdp_common.json.dumps(TAB_PREFIX + self.win_id)))
        if target.get("found"):
            click_element_by_rect(self.ws, target["target"]["x"], target["target"]["y"])
            deadline = time.time() + timeout
            while time.time() < deadline:
                if not self._is_open():
                    return True, f"closed {self.win_id}"
                time.sleep(0.5)
            # Clicking a control we found is not the same as it working -
            # gotcha #48 found exactly this for popups. Fall through to the
            # direct call rather than reporting failure while another way
            # to close it has not been tried yet.

        if close_work_frame(self.ws, self.win_id):
            deadline = time.time() + timeout
            while time.time() < deadline:
                if not self._is_open():
                    return True, f"closed {self.win_id} (direct)"
                time.sleep(0.5)

        if target.get("found"):
            return False, f"{self.win_id} was still open {timeout}s after clicking its X and calling fnRemoveForm"
        return False, f"{target.get('reason', 'no close control')}, and fnRemoveForm did not close it either"


# ===========================================================================
# Opening
# ===========================================================================

def recover_from_session_kick(ws, log=print):
    """Quick, cheap check for G-MES's own "used by another PC" session-
    conflict popup (HISTORY.md Phase 82.14) reappearing on an ALREADY
    signed-in tab, not just during an active sign-in attempt.

    Phase 82.14's fix only checked for this popup INSIDE `gmes_login.main()`'s
    own login-state branch, so it recovered a sign-in that was actively
    failing - but G-MES can invalidate a WORKING session at any moment, not
    only while one is being established. Live-caught recording a batch of
    screens back-to-back (HISTORY.md Phase 82.15): the popup appeared
    between two successful runs, with nothing mid-sign-in there to catch it,
    and sat blocking the account until the next full sign-in attempt
    happened to run.

    One JS lookup when there is nothing to find - the overwhelming common
    case - so this runs before every screen open unconditionally rather than
    only after something has already gone wrong. Returns True if a kick was
    found and recovery succeeded, False if there was nothing to recover
    from. Raises if the popup was there but a fresh sign-in could not
    complete - there is no screen worth trying to open at that point
    anyway."""
    if not gmes_login.close_login_ip_check(ws):
        return False
    log("  popups   : closed a 'used by another PC' session warning")
    if not sign_in():
        raise RuntimeError("the session was interrupted by another PC's "
                           "sign-in and could not be recovered")
    return True


SHAPE_GRACE_SECONDS = 45


def open_screen(ws, code, ready_wait=90, settle_checks=2, poll_interval=1.0, log=print,
                expected_fingerprint=None, grid_aliases=None, shape_grace=SHAPE_GRACE_SECONDS):
    """Open a screen by code or name, bring it to the FRONT, and wait until
    it has actually built itself.

    `expected_fingerprint` (and the recording's `grid_aliases`): when a recording
    exists, "built" means "has the shape the recording saw", for up to
    `shape_grace` seconds after the counts settle - see the loop below.

    Both halves matter. A background screen still accepts dataset writes, so
    filters apply cleanly and the Inquiry click then lands on whichever screen
    is really in front - that produced a run which set the date and division
    correctly, queried a completely different report, and reported zero rows.

    And a tab existing is not the same as a screen being built: Nexacro
    constructs the whole UI in JavaScript long after the tab appears, so this
    polls for the screen's own forms rather than reading them once and
    declaring the screen unreadable.

    "Has a grid or a filter" is not the same as "is finished". Confirmed live
    on P1114WM00 (HISTORY.md Phase 62): its category tree and result grid are
    ready almost immediately, so the old one-shot check returned right away -
    but the screen's own "Detail" filter panel (a reusable Widget Filter
    component, the same late-binding pattern already known from the Quick
    View widget) attaches `Module Name`, `MES P/O`, `Mail` and `PO` several
    seconds LATER. A run recorded at the first "ready" moment never saw those
    fields at all - not an error, just a screen offer and a set of settable
    filters that were quietly short. So readiness now requires the discovered
    shape (filter/unbound/grid counts) to read the SAME on two consecutive
    polls, the same settle discipline `poll_inquiry` already uses for the
    result count, before this screen is handed to the caller.

    Closing every OTHER open screen first was tried (HISTORY.md Phase 62.5)
    to stop a Quick View sibling's leftover filter form leaking into this
    screen's own discovery (P1114WM01 left `workYmd`/`poNo`/`modelCode` in
    P1114WM00 after a switch), and reverted the same session: it traded that
    problem for a worse one. Confirmed live over a 90s trace - a fully cold
    P1114WM00, opened with NOTHING else already open, never populated its
    own Detail filter widget at all (`Module Name`, `MES P/O`, `Mail`, `PO`
    stayed missing the entire time). P1114WM00 is a "Detail Schedule"
    child of P1114UM00 in the menu breadcrumb, and every prior successful
    discovery of it happened with P1114UM00 already open alongside it - the
    widget filter panel appears to depend on that parent screen's own
    state to populate, so force-closing "everything but the target" closed
    something this screen actually needed. The narrower Quick View
    contamination is not reachable through the normal front end any more
    (`run_gmes_workflow.py`'s Quick View question was reverted to
    informational-only in the same session), so there is nothing left in
    this codebase that opens a Quick View sibling programmatically - the
    blanket close bought no live safety it does not already have."""
    code = code.strip()

    # First, always: a session kicked out from under an otherwise-working
    # run (HISTORY.md Phase 82.15) leaves every step below driving a login
    # page instead of the screen asked for, with no clear symptom pointing
    # back to the actual cause.
    recover_from_session_kick(ws, log=log)

    # A screen already open is reached by clicking its tab. Driving the search
    # box again would work, but it types a code one character at a time and
    # waits on a suggestion list to reach a screen that is already there - and
    # a batch re-runs the same screen constantly.
    # Only a full screen code or menu id takes this path. A partial one
    # ("P111") would match several open screens and silently pick whichever
    # came first; a name goes through the catalogue, which validates it.
    opened = None
    if gmes_profile.looks_like_code(code):
        rows = gmes_open_screen.open_screens(ws).get("rows", [])
        for row in rows:
            haystack = f"{row.get('pageUrl', '')} {row.get('menuId', '')}".upper()
            if code.upper() in haystack:
                opened = row
                break
        if opened is None:
            opened = gmes_open_screen.tab_for_embedded_form(ws, code, rows)
    if opened is None:
        opened = gmes_open_screen.open_screen(ws, code, log=log)

    if not gmes_open_screen.activate_screen(ws, opened.get("winId", "")):
        raise RuntimeError(f"{code} is open as {opened.get('winId')} but its tab "
                           "could not be brought to the front")

    # A reused tab (the branch above, `opened = row`) skips `open_screen()`
    # entirely, and with it the ONE popup-closing call on this path
    # (HISTORY.md Phase 82.10 already taught this lesson for
    # `download_excel()`'s own trailing popup: a generic closer elsewhere in
    # the codebase does not help unless every place a popup can legitimately
    # appear actually calls it). A tab left open since an earlier session can
    # be sitting behind its own leftover popup - a stuck "Save to Excel"
    # dialog, a "Notification: completed." from a prior export, an AD SSO
    # leftover - and G-MES is fully modal while one is open, so every click
    # this function makes next (ticking Division, typing dates, Inquiry,
    # Excel) would silently land on nothing. Safe unconditionally, exactly
    # as Phase 82.10 reasoned: this is cleanup, not a decision about the
    # screen's own state.
    closed = gmes_common.close_child_popups(ws)
    if closed:
        log(f"  popups   : closed {closed}")

    deadline = time.time() + ready_wait
    info, last = None, "the screen never reported any forms"
    shape, stable = None, 0
    settled_at, held = None, None
    while time.time() < deadline:
        info = discover(ws, code)
        if info.get("found") and (info.get("grids") or info.get("filters")):
            current = (len(info.get("filters", [])), len(info.get("unbound", [])),
                       len(info.get("grids", [])))
            if current == shape:
                stable += 1
                if stable >= settle_checks:
                    # Two identical readings is a PROXY for "finished". When a
                    # recording says exactly what the finished screen looks like,
                    # wait for THAT (CLAUDE.md 3.2). On a brand-new profile the
                    # late-binding widgets took longer than two polls, the tool
                    # hashed a partial shape (the `dsGuide` grid missing) and
                    # refused to replay a recording that matched the finished
                    # screen exactly - checked minutes later, the fingerprints
                    # were identical (HISTORY.md Phase 84.17). Bounded, so a screen
                    # that has genuinely changed is still reported promptly.
                    screen = Screen(ws, code, opened, info)
                    if (not expected_fingerprint
                            or gmes_profile.fingerprint(info, grid_aliases) == expected_fingerprint):
                        return screen
                    held = screen
                    settled_at = settled_at or time.time()
                    if time.time() - settled_at >= shape_grace:
                        return held
            else:
                shape, stable = current, 1
        else:
            shape, stable = None, 0
        last = info.get("reason", last)
        time.sleep(poll_interval)
    if held is not None:
        return held                    # the caller reports the mismatch, having waited
    raise RuntimeError(f"{code} opened but never finished building ({last})")


def signed_in_after_interruption(seconds=90, sleep=None, clock=None):
    """After a sign-in that was cut short: wait (patiently, up to `seconds`)
    for G-MES to show the signed-in user, and clear any notice popup that
    arrived meanwhile - the interrupted run never got to it, and G-MES is fully
    modal while one is open. Never submits anything."""
    try:
        ws = connect_gmes(timeout=30)
    except Exception:                                     # noqa: BLE001
        return False
    try:
        signed_in, _ = gmes_login.wait_until_signed_in(ws, seconds, "", sleep=sleep, clock=clock)
        if signed_in:
            print("  G-MES did sign in - continuing.")
            gmes_login.transient(lambda: gmes_common.close_child_popups(ws))
        return signed_in
    finally:
        try:
            ws.close()
        except Exception:                                 # noqa: BLE001
            pass


def sign_in(attempts=2):
    """Sign in, retrying only what is worth retrying.

    Session expiry is real: after a long idle, clicking AD SSO produced no SSO
    window and the run failed; a second attempt signed in normally. So one
    expired session should not take a whole batch with it.

    But a retry is only ever right for a transient failure. When G-MES has
    said "Auth bad credentials", the second attempt sends the same password
    to the same server and gets the same answer - it just doubles the time
    the user waits for news they could have had immediately. Worse, repeated
    attempts with a bad password are how an account gets locked.

    `UNKNOWN_AFTER_SUBMIT` is refused the same way REJECTED is, for a
    different reason: the credentials were actually handed to a login form
    and submitted THIS run, but nothing afterwards proved success or
    rejection either way (HISTORY.md - external review of 1957ba9, finding
    #9). Retrying would mean submitting the same password a second time on
    nothing more than an unclear first result - exactly the risk this
    function's whole reason for existing is to avoid."""
    for attempt in range(1, attempts + 1):
        try:
            result = gmes_login.main()
        except Exception as e:                            # noqa: BLE001
            # Anything the login flow itself did not absorb (a socket dropped,
            # a page too busy to answer). What it means is UNKNOWN - the
            # credentials may already have been submitted - so it is never
            # retried blindly; the only question worth asking is whether
            # G-MES is in fact signed in (HISTORY.md Phase 84.1).
            print(f"\nSign-in was interrupted ({type(e).__name__}: {e}).")
            if signed_in_after_interruption():
                return True
            print("  G-MES is not confirmed signed in, and the credentials may "
                  "already have been submitted - NOT retrying. Look at the "
                  "browser window, or sign in by hand, before running this again.")
            return False
        if result == gmes_login.OK:
            return True
        if result in (gmes_login.REJECTED, gmes_login.UNKNOWN_AFTER_SUBMIT):
            return False        # never retry a credential that was already submitted
        if attempt < attempts:
            print(f"\nSign-in attempt {attempt} did not complete; retrying once "
                  "after rechecking the login state...")
    return False


def connect(timeout=20, port=None):
    return connect_gmes(timeout=timeout, port=port)


# ===========================================================================
# Export
# ===========================================================================

# ===========================================================================
# Is the PC's clock the one G-MES keeps? (HISTORY.md Phase 92.6, Open Item 56)
# ===========================================================================
#
# "Yesterday" is worked out from the PC's clock. A PC whose clock has drifted
# or been set wrong queries the wrong day for EVERY screen, and a screen with
# no date column to verify exports it without a word. The G-MES web server
# states its own time in the standard HTTP `Date` header of every answer, so a
# HEAD request for the page already open - a read, from the page's own origin,
# through the same proxy the page uses - gives a second clock to compare with.
#
# NOT YET SEEN LIVE: that the corporate gateway passes the `Date` header
# through to the page unchanged has not been observed on the real portal.
# When it is absent, the check says so and the run goes on - it never blocks
# a night on its own inability to read a clock.

CLOCK_MAX_SKEW_MINUTES = 15

JS_SERVER_DATE = """
fetch(location.href, {method: 'HEAD', cache: 'no-store', credentials: 'same-origin'})
    .then(r => r.headers.get('date') || '')
    .catch(e => 'ERROR ' + e)
"""


def server_clock(ws, timeout=20):
    """The G-MES server's own time as an aware UTC datetime -> (when, why-not)."""
    from email.utils import parsedate_to_datetime
    try:
        resp = send(ws, "Runtime.evaluate",
                    {"expression": JS_SERVER_DATE, "returnByValue": True,
                     "awaitPromise": True}, timeout=timeout)
    except Exception as e:                                   # noqa: BLE001
        return None, f"the page could not be asked ({type(e).__name__}: {e})"
    value = ((resp.get("result") or {}).get("result") or {}).get("value")
    if not value:
        return None, "the G-MES server's answer carried no Date header"
    if str(value).startswith("ERROR "):
        return None, f"the request failed ({value[6:]})"
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None, f"its Date header could not be read ({value!r})"
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when, None


def clock_problem(pc_now, server_now, max_skew_minutes=CLOCK_MAX_SKEW_MINUTES):
    """A sentence if the PC's clock and the server's disagree, else None.

    Both are aware datetimes, compared as instants, so the PC's and the
    server's time zones do not matter - only whether the PC's clock is right.
    Pure, so every rule has a test."""
    skew = (pc_now - server_now).total_seconds()
    if abs(skew) <= max_skew_minutes * 60:
        return None
    direction = "ahead of" if skew > 0 else "behind"
    hours, minutes = divmod(int(abs(skew)) // 60, 60)
    amount = f"{hours} h {minutes} min" if hours else f"{minutes} min"
    return (f"this PC's clock is {amount} {direction} the G-MES server's "
            f"(PC {pc_now:%Y-%m-%d %H:%M} UTC{pc_now:%z}, server "
            f"{server_now:%Y-%m-%d %H:%M} UTC) - a date worked out from it, "
            "such as 'yesterday', could be the wrong day. Fix the PC's date and time")


def check_pc_clock(ws, now=None, read_server=None):
    """(problem, note): `problem` means do not trust the PC's date; `note` is
    a warning that the check itself could not be made. At most one is set."""
    server_now, why = (read_server or server_clock)(ws)
    if server_now is None:
        return None, f"the PC's clock could not be checked against G-MES: {why}"
    pc_now = now or datetime.now(timezone.utc).astimezone()
    return clock_problem(pc_now, server_now), None


def is_drm_protected(path):
    """G-MES exports come back wrapped by Samsung's NASCA DRM.

    The file opens normally in Excel on a machine running the DRM client, but
    it is NOT a readable workbook: the bytes are encrypted, so openpyxl,
    pandas and every other library see a corrupt file. Worth knowing before
    anything downstream tries to parse it - which is why a CSV is written from
    the data layer alongside."""
    try:
        with open(path, "rb") as fh:
            return b"NASCA DRM" in fh.read(64)
    except OSError:
        return False


def replace_when_free(source, destination, timeout=90, poll=0.5, sleep=time.sleep,
                      clock=time.time):
    """`os.replace`, but wait while something else still has the file open.

    A freshly downloaded workbook is not always ours yet: the DRM agent that
    encrypts every .xlsx on this network, antivirus and the browser itself can
    each hold it for a moment after it lands. Windows then refuses the rename
    at once with WinError 32 (in use) or 5 (access denied) - which failed a
    scheduled R3220UM00 export whose data had been read correctly
    (HISTORY.md Phase 83). Only THOSE two errors are waited out, and only for
    `timeout` seconds, polling - a rename that fails any other way is a real
    fault and is raised immediately. Never a fixed sleep (CLAUDE.md 3.1)."""
    deadline = clock() + timeout
    while True:
        try:
            os.replace(source, destination)
            return
        except PermissionError as e:
            if getattr(e, "winerror", None) not in (5, 32) or clock() >= deadline:
                raise
            sleep(poll)


def download_excel(ws, target_dir, timeout=240):
    """Click the toolbar Excel icon, confirm its dialog, wait for the file.

    Two things that are not obvious. The icon opens a "Save to Excel" dialog
    (PopupExcelExport) with the grid already ticked - it does not download on
    its own. And Chrome's download redirect dies with the connection that set
    it, so `Browser.setDownloadBehavior` is issued on the SAME open connection
    that does the clicking; setting it from a connection that is then closed
    leaves the file in the user's Downloads folder, which is exactly what
    happened the first time. Only the staging folder is watched: a file that
    lands anywhere else ends in a loud timeout, never a guess at which
    workbook in Downloads is this one (HISTORY.md Phase 92.5).

    The popup closer must NOT be running around this: the export dialog is a
    child popup like any other, and closing it would cancel the export."""
    os.makedirs(target_dir, exist_ok=True)
    staging = tempfile.mkdtemp(prefix=".gmes-download-", dir=target_dir)

    try:
        send(ws, "Browser.setDownloadBehavior",
             {"behavior": "allow", "downloadPath": staging, "eventsEnabled": True})
    except Exception:
        send(ws, "Page.setDownloadBehavior",
             {"behavior": "allow", "downloadPath": staging})

    def snapshot(folder):
        try:
            return {f for f in os.listdir(folder)
                    if f.lower().endswith((".xlsx", ".crdownload"))}
        except OSError:
            return set()

    try:
        icon = evaluate(ws, gmes_common.js_find_by_id(EXCEL_BTN))
        if not icon.get("found"):
            raise RuntimeError(f"the Excel Download icon was not visible ({icon.get('reason')})")
        click_element_by_rect(ws, icon["x"], icon["y"])
        # 30 attempts (15s) was live-caught as too short on Q2111UM00
        # (HISTORY.md Phase 82.13): two consecutive real runs raised this
        # exact error against a heavy 175-row/143-column export, back to
        # back with Inquiry itself measurably slowing across the same
        # attempts (14.9s -> 22.7s -> 25.4s) - consistent with the G-MES
        # server needing longer under load to render the dialog too, not a
        # missing button. A third attempt, made only seconds later with no
        # code change, succeeded immediately (found within 1s). Doubled
        # with headroom: `click_control` already polls and returns the
        # instant the button appears, so a generous cap costs nothing in
        # the fast case (CLAUDE.md 3.1).
        #
        # A bare `text="OK"` is not enough: `click_control` returns on the
        # FIRST poll that matches anything, and a result grid can already
        # show cells reading exactly "OK" (a pass/fail column) before this
        # dialog has even rendered - the very first poll then clicks a grid
        # cell, not the dialog, and the export never starts. Live-caught on
        # R4351UM01, 1425 rows with an "OK"-valued column (HISTORY.md Phase
        # 84.26). The dialog's own button lives under the shared `mdiFrame`
        # (`popupExcelExport.form.btnOk`), never under a per-screen work
        # window, so scoping to it is specific without being screen-bound.
        # The bare text search stays as a fallback in case that id is ever
        # wrong for some screen never yet seen.
        if not (gmes_common.click_control(ws, id_regex=r"popupExcelExport\.form\.btnOk",
                                          attempts=90, delay=0.5)
                or gmes_common.click_control(ws, text="OK", attempts=90, delay=0.5)):
            raise RuntimeError("the 'Save to Excel' dialog did not offer an OK button")

        deadline = time.time() + timeout
        last_size, stable = -1, 0
        while time.time() < deadline:
            names = snapshot(staging)
            finished = [name for name in names if name.lower().endswith(".xlsx")]
            downloading = [name for name in names if name.lower().endswith(".crdownload")]
            if len(finished) > 1:
                raise RuntimeError("more than one workbook arrived for one export request")
            if finished and not downloading:
                candidate = os.path.join(staging, finished[0])
                size = os.path.getsize(candidate)
                stable = stable + 1 if size == last_size else 0
                last_size = size
                if stable >= 2:
                    final = os.path.join(
                        target_dir, f".gmes-download-{uuid.uuid4().hex}_{finished[0]}")
                    replace_when_free(candidate, final)
                    # G-MES follows a finished download with its own
                    # "Notification: completed." popup on at least some
                    # screens (live-caught on M3912UM00, HISTORY.md Phase
                    # 82.10) - not the "Save to Excel" dialog already
                    # confirmed above, a SEPARATE one that appears only
                    # once the file itself has actually landed. Nothing
                    # closed it, and G-MES is fully modal while it is
                    # open: the very next screen this tool tried to open
                    # failed with "its tab could not be brought to the
                    # front" because this window's popup was still
                    # blocking the whole application. Safe to close
                    # unconditionally here - the file on disk is already
                    # the real evidence of success, verified above and
                    # again by check_download() right after this returns;
                    # this is strictly cleanup, not a decision about
                    # whether the export worked. Never screen-specific:
                    # every caller of this function gets it.
                    gmes_common.close_child_popups(ws)
                    return final
            time.sleep(0.5)
        raise RuntimeError(f"no complete .xlsx file appeared within {timeout}s")
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def check_download(path, minimum=512):
    """Confirm a delivered file is actually there and is not an empty shell.

    "The download succeeded" has meant three different things here: the click
    worked, a file appeared, and the file has content. Only the third is worth
    reporting, and it is the one that was never checked - a zero-byte or
    stub file arrives looking exactly like a real export.

    This is as far as verification can go for the .xlsx: it is NASCA-DRM
    encrypted, so no library can read it and nothing can confirm what is
    inside. The CSV written from the data layer is the only real evidence of
    content, which is why both are produced."""
    if not os.path.isfile(path):
        raise RuntimeError(f"the export reported success but {path} is not there")
    size = os.path.getsize(path)
    if size < minimum:
        raise RuntimeError(f"{os.path.basename(path)} is only {size} bytes - "
                            "that is not a real export")
    with open(path, "rb") as fh:
        prefix = fh.read(64)
    if not (prefix.startswith(b"PK\x03\x04") or b"NASCA DRM" in prefix):
        raise RuntimeError(f"{os.path.basename(path)} is not an XLSX or a NASCA DRM workbook")
    return size


_WINDOWS_RESERVED_NAME = re.compile(
    r"(?i)^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])$"
)


SAFE_NAME_MAX = 80


def safe_name(text):
    """A file name that survives Windows. Report titles carry '/' and ':'.

    Every export uses this only as a PREFIX to a timestamp+uuid suffix
    (`f"{name}_{stamp}.xlsx"`), so an exact reserved-name collision
    (Windows blocks CON, PRN, AUX, NUL, COM1-9, LPT1-9 exactly, regardless
    of extension - not names merely starting with one) is not reachable
    through that one call site today. Guarded anyway: this function has no
    control over every future caller, and a report legitimately titled
    "AUX" or "NUL" is not implausible in a system with 810 screens."""
    name = re.sub(r'[<>:"/\\|?*]', "-", (text or "").strip())
    # Control characters (a NUL makes open() raise "embedded null byte" AFTER the
    # query has run), then a length cap: 300 characters came back unchanged, and
    # Windows refuses any full path over 260 unless long paths are enabled. The
    # export adds a 26-character stamp, a uuid and a suffix, and the folder above
    # it is already ~110 characters (HISTORY.md Phase 84.9).
    name = re.sub(r"[\x00-\x1f\x7f]", "", name)
    name = name[:SAFE_NAME_MAX].rstrip(". ") or "report"   # Windows also drops a trailing dot/space
    if _WINDOWS_RESERVED_NAME.match(name):
        name = f"_{name}"
    return name


def _filter_key(entry):
    """The identity `intent_mismatches()` matches a filter by across two
    different discovery snapshots - EXACT path + dataset+column for a bound
    control, path + control name for an unbound one. Never positional, for
    the same reason `find_ref()` and CLAUDE.md 3.4 already rule that out
    generally: nothing about a control's position in the discovered list is
    stable.

    `path` is included because dataset+column alone is not always unique
    within one window: a reusable component can appear more than once,
    each instance carrying the same dataset.column at a different path
    (HISTORY.md - external review of 1957ba9/cff282b, finding #1). Without
    path in the key, this dict comprehension would silently let a SECOND
    instance's reading overwrite the first, so a write proven correct
    against one instance could be "confirmed" by reading a different one
    entirely - in the one check this whole phase exists to make trustworthy."""
    if entry.get("column"):
        return (entry.get("path"), entry.get("dataset"), entry.get("column"))
    return (entry.get("path"), None, entry.get("control"))


def intent_mismatches(fresh_info, fresh_options, resolved_options=(),
                      date_fields=(), applied_filters=(),
                      division_wanted=None, division_seen=None, notes=None):
    """What changed between "this run wrote it" and "right now, about to
    click Inquiry" - HISTORY.md, external review of 8ac502a, finding #4.

    Nexacro is event-driven: `setColumn()` can fire `oncolumnchanged`, and
    that handler is free to change or clear a DIFFERENT filter than the one
    just written - a category switch resetting a date field is the textbook
    case, but any later step's handler can just as easily undo an earlier
    one. Every value this run wrote was already confirmed once, right after
    being written - but only once, against itself, in isolation. Nothing
    re-checked whether a LATER step had quietly undone an EARLIER one by the
    time the query actually runs. This is that one last, combined check -
    a single fresh read, compared against everything asked for - immediately
    before the click that actually queries the server.

    Returns a list of human-readable mismatch strings; empty means every
    filter this run applied is still exactly what the screen holds."""
    problems = []

    by_name = {(o.get("name") or "").lower(): o for o in fresh_options}
    for opt in resolved_options:
        cur = by_name.get((opt.get("name") or "").lower())
        label = opt.get("label") or opt.get("key") or opt.get("name")
        if cur is None:
            problems.append(f"option {label} is no longer on the screen")
        elif cur.get("state") not in ("selected", "checked"):
            problems.append(f"option {label} is no longer selected "
                            f"(now {cur.get('state')!r})")

    by_key = {_filter_key(f): f for f in
             fresh_info.get("filters", []) + fresh_info.get("unbound", [])}

    dates = len(tuple(date_fields))
    for n, (flt, value) in enumerate(tuple(date_fields) + tuple(applied_filters)):
        cur = by_key.get(_filter_key(flt))
        label = flt.get("label") or flt.get("column") or flt.get("control")
        if cur is None:
            problems.append(f"{label} is no longer on the screen")
        elif not values_match(value, cur.get("value")):
            # ONE narrow tolerance, dates only (HISTORY.md Phase 83.3): the
            # bound dataset column is EMPTY but the control on screen shows
            # exactly the requested date. B3320UM00 displays its Period from
            # the control and never fills that dataset row, so reading the
            # dataset alone called a correct screen "drifted" and refused to
            # query it. A dataset that holds a DIFFERENT value, an empty
            # control, or any non-date filter is still a mismatch. The rows
            # that come back are still checked against the date (--verify is
            # mandatory for a dated run), which is the real proof.
            shown = (cur.get("shown") or "").strip()
            if (n < dates and not (cur.get("value") or "").strip()
                    and shown and values_match(value, shown)):
                if notes is not None:
                    notes.append(f"{label}: the screen shows {shown!r} but its dataset "
                                 "column is empty; accepted because the control "
                                 "displays the requested date - the result rows are "
                                 "still checked against it")
                continue
            problems.append(f"{label} now reads {cur.get('value')!r}, "
                            f"not the {value!r} this run set")

    if division_wanted:
        seen = (division_seen or "").strip().casefold()
        if seen != division_wanted.strip().casefold():
            problems.append(f"division now shows {division_seen!r}, not the "
                            f"requested {division_wanted!r}")

    return problems


# ===========================================================================
# The whole pipeline for one screen
# ===========================================================================

def reconcile_result(screen, grid, rows, log=print):
    """Which dataset IS the result, now that Inquiry has answered.

    Discovery describes a screen BEFORE it does its work, and a Nexacro grid's
    `binddataset` is a live property the screen's own code may change once a
    query answers - `set_binddataset()` is the platform's documented way to
    build result grids at runtime (Nexacro developer guide), not a G-MES
    oddity, so any screen can do it (HISTORY.md Phase 82.18: R5216UM00,
    placeholder `dsMntDetailListTemp` -> real `dsMntDetailList`).

    So the grid's binding is looked up again ALWAYS, not only when the first
    dataset came back empty: a placeholder holding one header row would have
    slipped past a zero-only test and been exported as the report. What the
    grid is bound to now is, by definition, what the screen is showing.

    Returns (grid, rows, aliases). `aliases` is {re-bound name: name seen at
    discovery} when a re-bind was followed, else {}."""
    rebound = screen.follow_grid_rebind(grid)
    if not rebound:
        return grid, rows, {}
    now = screen.rows(rebound, limit=0)
    now = now.get("total", 0) if now.get("found") else 0
    if now > 0:
        log(f"  rebound  : grid {grid['name']} now shows {rebound['dataset']} "
            f"(it was {grid['dataset']} when the screen was read)")
        return rebound, now, {rebound["dataset"]: grid["dataset"]}
    if rows > 0:
        screen.warnings.append(
            f"grid {grid['name']} is now bound to {rebound['dataset']}, which "
            f"is empty, while the dataset this run read ({grid['dataset']}) "
            f"holds {rows} rows - the screen and the export may disagree")
    return grid, rows, {}


def explain_empty_result(screen, grid):
    """The reason for a zero-row result, and what else on the screen DOES hold
    rows. Never switches grids on its own - which one is the report is the
    person's decision (a master/detail screen has several) - but a bare "no
    rows" beside a screen full of data is exactly the misleading answer this
    project must not give (HISTORY.md Phase 82.20)."""
    message = "the query returned no rows - nothing exported"
    try:
        others = []
        for g in discover(screen.ws, screen.code).get("grids", []):
            if g["dataset"] == grid["dataset"]:
                continue
            read = screen.rows(g, limit=0)
            count = read.get("total", 0) if read.get("found") else 0
            if count > 0:
                others.append(f"{g['name']} ({g['dataset']}): {count} rows")
    except Exception:
        return message
    if others:
        return (f"the query returned no rows in grid {grid['name']} "
                f"({grid['dataset']}) - nothing exported. But another grid on "
                f"this screen DOES hold rows - {'; '.join(others)}. If one of "
                "those is the report, name it with --grid; nothing was guessed")
    return message


def filtered_result_note(read):
    """A warning when the result dataset has a client-side `Dataset.filter()`
    active, else None. `getRowCount()` is the count AFTER the filter, so a
    screen that filters its own result shows - and this tool exports - fewer
    rows than the dataset holds, with nothing else saying so (HISTORY.md Phase
    82.20; Nexacro documents `filter()` as changing what the grid displays)."""
    read = read or {}
    shown, held = read.get("total"), read.get("unfiltered")
    if read.get("filterstr") and held is not None and shown is not None \
            and held != shown:
        return (f"the result dataset has a client-side filter active "
                f"({read['filterstr']!r}): {shown} of {held} rows are shown, "
                f"and {shown} were exported")
    return None


def grid_is_proven(profile, grid):
    """Whether an earlier successful run already vetted THIS grid for this
    screen - the saved profile names it (under either of its dataset names,
    for a grid that re-binds).

    The "result never changed" warning cannot tell static content from a
    repeated answer on count alone: in a window that has already been queried,
    the correct grid returns the same count again (measured live on R3220UM00:
    the right dataset, 1 row before and 1 after, warned exactly like the wrong
    one). What separates the cases is history - a grid a run has already
    produced a checked export from is not the suspect; a first recording, a
    `--relearn`, or a `--grid` naming something new is."""
    if not profile:
        return False
    remembered = (profile.get("grid") or {}).get("dataset")
    if not remembered or not grid:
        return False
    aliases = profile.get("grid_aliases")
    return (gmes_profile.canonical_grid(remembered, aliases)
            == gmes_profile.canonical_grid(grid.get("dataset"), aliases))


def unchanged_result_note(report, rows):
    """A warning when the result dataset held the same rows before Inquiry as
    after and never changed while the query ran, else None.

    Live-caught on R3220UM00 (Operation Analysis): the run was pointed at
    `dsOperAnalCalc`, 37 rows that were there before Inquiry and 37 after -
    the screen's "Formula" legend, a static definitions table, not the
    report. It exported cleanly, verified nothing, saved itself to the
    profile and reported success. The real result (1 row) sat in another
    grid. Nothing in the run could tell the two apart except this: a real
    answer normally MOVES the count at some point; static content never
    does. Only a warning, not a refusal - re-running an identical query
    legitimately gives an identical count (HISTORY.md Phase 65.2) - but it
    is the one hint available, and silence here shipped wrong data."""
    report = report or {}
    if rows and report.get("changed") is False and report.get("before") == rows:
        return (f"the result dataset held {rows} rows before Inquiry and the "
                f"same {rows} after, and never changed while the query ran - "
                "if that is not what the screen shows, it may be static "
                "content (a legend or lookup table) rather than this query's "
                "answer. Check it before relying on it")
    return None


def unverified_date_sets(applied_filters, verify):
    """Labels of date fields typed with --set when no --verify was given.

    `--from/--to` REQUIRE `--verify` because an export of the wrong day looks
    exactly like the right one. Typing the same date with `--set` (the only
    way on a screen whose date fields are not bound to a dataset) has always
    skipped that requirement without saying so - so the export goes out
    unchecked against the very date it was asked for."""
    if verify:
        return []
    labels = []
    for flt, _applied in applied_filters:
        if is_date_field(flt):
            label = flt.get("label") or flt.get("column") or flt.get("control")
            if label not in labels:       # From and To often share one label
                labels.append(label)
    return labels


# The owner's decision, 2026-09-23 (HISTORY.md Phase 94.1): the tool delivers
# the Excel file only. "both" - Excel plus a CSV copy read from the dataset -
# was the built-in default until then, so it is what every saved report group
# and most habits still say; it now means exactly the default. A CSV is still
# available, but only when asked for explicitly as "csv" on the command line.
# Nothing is lost for checking: the rows are verified against the dataset
# itself before any file is written, never against the CSV.
DEFAULT_EXPORT = "xlsx"


def effective_export(value):
    """The export a run will actually make: None, "" and the retired "both"
    are the default (Excel only); anything else is passed through as asked."""
    return DEFAULT_EXPORT if value in (None, "", "both") else value


def destination_to_pin(out_dir, export):
    """What to pass `gmes_profile.save(output_dir=, export=)` for a
    successful run that used `out_dir`/`export` - pure decision logic,
    isolated the way `intent_mismatches()` and `InquirySettle` are, so it
    can be tested directly rather than only through a full run.

    A value is pinned only when it differs from the tool's own built-in
    default (`OUTPUT_DIR`, `DEFAULT_EXPORT`) - an ordinary run of any other screen
    must never start writing a destination into a profile that never had
    one (HISTORY.md Phase 84.28). Returns kwargs ready to splat into
    `gmes_profile.save()`."""
    return {"output_dir": out_dir if out_dir != OUTPUT_DIR else None,
            "export": effective_export(export)
            if effective_export(export) != DEFAULT_EXPORT else None}


def run_screen(ws, screen_code, division=None, date_from=None, date_to=None,
               sets=None, options=(), export=None, out_dir=None,
               grid_name=None, tree=None, verify=None, dry_run=False,
               close_after=False, use_profile=True, trust_profile=True,
               log=print):
    """Open a screen, set everything asked for, run it, verify it, export it.

    The nine steps of the basic workflow, in the order the screen imposes:
    open, read, apply, verify each, Inquiry, wait, export, verify the file,
    and - only if all of that worked - remember what was proven.

    Dates are never calculated here. `date_from` and `date_to` arrive already
    decided by the caller.

    Options come first because switching a category tab or a Quick View
    rebuilds the left panel and discards whatever was set before it.

    `use_profile` and `trust_profile` are deliberately separate knobs
    (HISTORY.md Phase 66). `use_profile` alone used to gate BOTH reading the
    saved profile at step 2 and writing a new one at step 11 - so a caller
    re-teaching a screen had no way to skip trusting the OLD profile
    without also skipping saving the new one. The interactive front end
    worked around that by deleting `screens/<CODE>.json` on disk the moment
    RECORD was chosen for an already-learned screen - before the screen was
    even opened, before anything was confirmed. Cancelling, or any later
    step failing, then meant the old (working) profile was already gone
    with nothing to replace it. `trust_profile=False` (with `use_profile`
    left True) instead skips only the LOAD - step 11's save still runs
    normally on success and atomically replaces the old file via
    `gmes_profile.save()`'s own temp-file-plus-rename write, so an old
    profile is only ever lost by being properly superseded, never by being
    pre-emptively deleted on a guess that a replacement is coming."""
    sets = dict(sets or {})
    started = time.time()
    code = screen_code.strip().upper()
    out = {"screen": code, "ok": False, "rows": 0, "files": [], "error": None,
           "warnings": [], "applied": {}, "options": [], "cleared": [],
           "dry_run": bool(dry_run)}

    log(f"\n{'=' * 70}\n{code}\n{'=' * 70}")

    # 1. Open, bring to the front, wait until it has built itself. The recording
    #    (if any) is read FIRST so the wait can be for the shape it expects.
    profile = gmes_profile.load(code) if (use_profile and trust_profile) else None

    # A screen can PIN its own destination (HISTORY.md Phase 84.28): `None`
    # here means the caller did not ask for anything specific, so the
    # screen's own remembered choice applies before falling back to the
    # tool's built-in default. An explicit caller value always wins outright
    # - this only fills in what was left unsaid.
    if export is None:
        export = (profile or {}).get("export")
    export = effective_export(export)
    if out_dir is None:
        out_dir = (profile or {}).get("output_dir") or OUTPUT_DIR
    if export not in ("xlsx", "csv", "both", "none"):
        raise ValueError(f"unknown export format: {export}")

    screen = open_screen(ws, code, log=log,
                         expected_fingerprint=(profile or {}).get("opening_fingerprint"),
                         grid_aliases=(profile or {}).get("grid_aliases"))
    opening_info = screen.info
    out["title"] = screen.title
    out["menuId"] = screen.menu_id
    out["window"] = screen.win_id
    log(f"  screen   : {screen.title}  [{screen.menu_id}]")

    # 2. Read the screen as it is now, then decide whether anything remembered
    #    about it can still be trusted. A profile is never repaired silently:
    #    if the screen moved, it is dropped and the screen is read fresh.
    if profile:
        opening_fingerprint = profile.get("opening_fingerprint")
        if opening_fingerprint and opening_fingerprint != gmes_profile.fingerprint(
                opening_info, profile.get("grid_aliases")):
            log("  changed  : the screen opening shape changed since this was learned")
            raise RuntimeError(
                "the remembered screen shape changed; refusing to replay saved settings")
        if not opening_fingerprint:
            # Old profiles cannot prove an option-bearing opening shape. The
            # old no-option form remains compatible; option profiles require
            # an explicit relearn rather than a false comparison.
            if profile.get("options"):
                raise RuntimeError("the remembered profile predates opening-shape checks; refusing to replay saved settings")
            problems = gmes_profile.describe_change(profile, opening_info)
            if problems:
                for p in problems:
                    log(f"  changed  : {p}")
                raise RuntimeError("the remembered screen shape changed; refusing to replay saved settings")
        log(f"  learned  : {gmes_profile.summary(profile)}")
    out["used_profile"] = bool(profile)

    # `or {}` after the get, not a default INSIDE it: dict.get(k, {}) returns
    # None when the key exists holding None, which every profile saved without
    # a division does. That crashed a replay with
    # "'NoneType' object has no attribute 'get'".
    grid = screen.grid(grid_name or (gmes_profile.resolve_grid_dataset(profile, screen.info)
                                     if profile else None))
    out["grid"] = f"{grid['name']} -> {grid['dataset']}"
    log(f"  filters  : {len(screen.filters)} bound, {len(screen.unbound)} unbound")
    log(f"  results  : {grid['dataset']} (grid {grid['name']})")

    # 3. Left-panel options first: switching a category tab or a Quick View
    #    rebuilds the panel and discards what was set before it.
    #
    #    A remembered screen re-applies the options chosen when it was taught,
    #    unless this run names its own. They are decisions nothing on the
    #    screen records - "Plan Date" and "Create Date" both look correct and
    #    return different answers - so forgetting them between runs would make
    #    a learned screen quietly stop meaning what it meant.
    if not options and profile:
        options = profile.get("options") or []
        if options:
            log("  learned  : options from last time: "
                + ", ".join(option_display(o) for o in options))
    # What gets REMEMBERED is what was resolved, never what was asked for. A
    # profile holding the English label "Create Date" therefore heals itself
    # into the stable `btnCreate` identity on its next successful run, with no
    # re-record and nothing for the user to do (HISTORY.md Phase 76).
    resolved_options = []
    for wanted in options:
        result = screen.set_option(wanted)
        out["options"].append(result["outcome"])
        resolved_options.append(
            {k: result[k] for k in ("key", "name", "path", "label")})
        if result["matched_by"] not in ("component name", "component path",
                                        "semantic key"):
            # Worth saying out loud: this run matched on something that can
            # change under it, and would have failed had the UI language
            # changed first. It is about to be replaced by one that cannot.
            log(f"  migrated : {option_display(wanted)} matched by "
                f"{result['matched_by']}; remembering [{result['key']}] instead")
        log(f"  option   : {result['outcome']}")
    if options:
        # The panel was rebuilt; re-resolve - but keep to the grid ALREADY
        # chosen (named, remembered, or the unambiguous default). Passing only
        # `grid_name` threw the remembered choice away on every replay, so a
        # screen with two comparable grids could be recorded and never replayed
        # (HISTORY.md Phase 83.5). If that dataset is gone after the rebuild,
        # screen.grid() refuses, as it does for any missing named grid.
        grid = screen.grid(grid_name or grid["dataset"])
    if profile:
        # Saved refs were intentionally captured after options rebuilt the
        # panel. Validate them only now, on that same shape.
        problems = gmes_profile.describe_change(profile, screen.info)
        if problems:
            for p in problems:
                log(f"  changed  : {p}")
            raise RuntimeError("the remembered screen shape changed; refusing to replay saved settings")

    # 3.5 Everything else a taught screen remembers, offered back exactly
    # like options already are - only when THIS call named none of its own.
    # `run_gmes_workflow.py`'s interactive "Run it?" replay has always done
    # this (division/dates/sets/verify straight from `last_values()`), but
    # only there - a caller with no terminal to answer a prompt (every
    # unattended or scripted use, `GMES_Workflow.bat <CODE>` included) got
    # none of it: `gmes_report.py run <CODE>` with no other flags skipped
    # organisation entirely and queried whatever happened to already be
    # ticked, usually nothing. Live-caught (HISTORY.md Phase 82.9): a
    # freshly recorded screen could not be replayed by its own recorded
    # command without retyping the exact division and dates that command
    # had just proven. `use_profile`/`trust_profile` already gate whether
    # `profile` exists at all, so no new on/off switch is needed here.
    if profile:
        remembered = gmes_profile.last_values(profile)
        if not division and remembered.get("division"):
            division = remembered["division"]
            log(f"  learned  : division from last time: {division}")
        if not date_from and not date_to and remembered.get("from"):
            date_from = remembered["from"]
            date_to = remembered.get("to") or date_from
            log(f"  learned  : period from last time: {date_from} to {date_to}")
        if not sets and remembered.get("sets"):
            sets = dict(remembered["sets"])
            log("  learned  : filters from last time: "
                + ", ".join(f"{k}={v}" for k, v in sets.items()))
        if date_from and not verify and remembered.get("verify"):
            verify = remembered["verify"]

    # 4. Organisation.
    if division:
        picked = screen.select_org(
            division, tree=tree,
            prefer=((profile or {}).get("division") or {}).get("dataset"))
        # De-duplicated: the same tree exists several times on some
        # screens, so a single division comes back once per copy.
        names = ", ".join(sorted({t["name"] for t in picked["ticked"]}))
        dropped = sorted(set(picked.get("cleared") or []))
        out["division"] = names
        confirmed = picked.get("confirmed")
        detail = names + (f"  (screen confirms {confirmed})" if confirmed else "")
        if dropped:
            detail += f"  (unticked {len(dropped)}: " \
                      + ", ".join(dropped[:4]) \
                      + ("..." if len(dropped) > 4 else "") + ")"
        log(f"  division : {detail}")

    # 5. The dates the caller gave. Nothing is worked out from today's date.
    date_fields = []
    if date_from or date_to:
        date_fields = screen.set_date_range(date_from, date_to, profile)
        out["dates"] = [(f["column"] or f["control"], v) for f, v in date_fields]
        if not date_fields:
            raise RuntimeError("the requested period was not applied to any field")
        log("  dates    : " + ", ".join(f"{c}={v}" for c, v in out["dates"]))

    # 6. Sweep values inherited from an earlier run, before applying this
    #    run's own, so nothing is carried over in silence.
    keep = {f["column"] for f, _ in date_fields}
    for key in sets:
        m = match_filter(screen.info, key)
        if m is not None and not isinstance(m, list):
            keep.add(m.get("column"))
    out["cleared"] = screen.clear_stale(keep)
    if out["cleared"]:
        log(f"  cleared  : leftover {', '.join(out['cleared'])}")

    # 7. Anything else the caller named.
    applied_filters = []
    for key, value in sets.items():
        flt, applied = screen.set_filter(key, value)
        out["applied"][flt["label"] or flt["column"] or flt["control"]] = applied
        applied_filters.append((flt, applied))
        how = "typed" if not flt.get("bound") else "set"
        log(f"  filter   : {flt['label'] or flt['column']} {how} to {applied!r}")

    if dry_run:
        out["ok"] = True
        out["warnings"] = screen.warnings
        out["seconds"] = round(time.time() - started, 1)
        log("  dry run  : everything above was applied; Inquiry NOT clicked")
        for w in screen.warnings:
            log(f"  warning  : {w}")
        return out

    # What organisation is REALLY in effect as the query runs - read off the
    # screen's own label, not from what was typed.
    #
    # This is what the memory should have been recording all along. A run that
    # named no division still queries whichever one is ticked, and storing ""
    # for it left the screen looking un-taught: P1111UM00 was recorded with
    # VD, then run once with everything blank, and its memory came back empty
    # so the next replay asked cold. Recording what was USED also fixes the
    # spelling for free - a division typed "vd" is stored as the tree's own
    # "VD".
    effective_division = division or ""
    seen_org = org_selection(ws)
    if seen_org.get("found") and seen_org.get("org"):
        effective_division = seen_org["org"]
        if not division:
            log(f"  division : none asked for; the screen has "
                f"{effective_division} in effect")

    # 7.5 Final Intent Verification - one fresh read, right before the click
    # that actually queries the server, confirming every option/date/filter/
    # division this run applied is STILL what the screen holds. See
    # intent_mismatches()'s own docstring for why a per-step check right
    # after each write is not enough on its own.
    screen.refresh()
    # Re-resolve the SAME grid by the dataset name already chosen - reuses
    # screen.grid()'s own existing ambiguity/missing-grid refusal rather
    # than guessing the refreshed panel still means the same thing.
    grid = screen.grid(grid["dataset"])
    out["grid"] = f"{grid['name']} -> {grid['dataset']}"
    problems = intent_mismatches(
        screen.info, screen.options(), resolved_options=resolved_options,
        date_fields=date_fields, applied_filters=applied_filters,
        division_wanted=division, division_seen=seen_org.get("org"),
        notes=screen.warnings)
    if problems:
        for p in problems:
            log(f"  drifted  : {p}")
        raise RuntimeError(
            "the screen no longer matches what was asked for, right before "
            f"Inquiry: {'; '.join(problems)}. Refusing to query.")

    # 8. Inquiry, watching the dataset this screen actually uses.
    rows = screen.inquiry(grid)
    discovered_grid = grid          # what a saved profile must remember (below)
    grid, rows, grid_aliases = reconcile_result(screen, grid, rows, log=log)
    if grid is not discovered_grid:
        out["grid"] = f"{grid['name']} -> {grid['dataset']}"
    out["rows"] = rows
    log(f"  inquiry  : {rows} rows in {time.time() - started:.1f}s")
    if rows == 0:
        raise RuntimeError(explain_empty_result(screen, grid))
    note = filtered_result_note(screen.rows(grid, limit=0))
    if note:
        screen.warnings.append(note)
    result_suspect = False
    if not grid_is_proven(profile, discovered_grid):
        note = unchanged_result_note(getattr(screen, "last_inquiry", None), rows)
        if note:
            screen.warnings.append(note)
            result_suspect = True

    # 9. Verification. Explicit COLUMN=VALUE is strict; otherwise the date
    #    columns are reported so the caller can see what came back without a
    #    guess being made about which column the filter applied to.
    if verify:
        column, _, expected = verify.partition("=")
        column = column.strip()
        if expected:
            seen = screen.verify_column(grid, column, expected, strict=True)
        elif date_to and date_to != date_from:
            # A genuine multi-day range with no explicit =VALUE - checking
            # every row against the single value date_from (the only prior
            # behaviour) rejected every legitimately correct multi-day
            # answer, since real rows span the whole requested window, not
            # one day. Confirmed live: 6529 correct rows over a real 10-day
            # range, all reported as "not exactly the requested" against
            # date_from alone.
            seen = screen.verify_date_range(grid, column, date_from, date_to, strict=True)
        else:
            seen = screen.verify_column(grid, column, date_from, strict=True)
        out["verified"] = {column: seen}
        log(f"  verified : {column} = {seen}")
    elif date_from:
        raise RuntimeError("a date-constrained run requires --verify COLUMN[=VALUE]")
    for label in unverified_date_sets(applied_filters, verify):
        screen.warnings.append(
            f"{label} was typed with --set, so the result was NOT checked "
            "against it (--verify only runs with --from/--to)")

    # 10. Export, and check the file is really there and really has content.
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f") + "_" + uuid.uuid4().hex[:8]
    name = safe_name(screen.title or code)
    if export in ("xlsx", "csv", "both"):
        os.makedirs(out_dir, exist_ok=True)
    downloaded = None
    try:
        if export in ("xlsx", "both"):
            if not screen.activate():
                raise RuntimeError("could not prove the report screen was active for Excel export")
            downloaded = screen.export_excel(out_dir)
            # Validate BEFORE renaming to the final, believable name - not
            # after. A corrupt or truncated download used to be renamed to
            # its real "<title>_<stamp>.xlsx" name first and only checked
            # afterward; a failed check then raised past the point where
            # out["files"] recorded it, so the exception handler's cleanup
            # loop never found it to delete - a corrupt file was left
            # behind under the exact name a real export would have used.
            #
            # Checking it before that rename does NOT, on its own, clean it
            # up on failure: download_excel() already moves the file OUT of
            # its disposable staging directory - into `out_dir`, under a
            # hidden `.gmes-download-<uuid>_...` name - before returning, so
            # by the time `downloaded` reaches here it is no longer inside
            # the staging dir that `download_excel()`'s own
            # `finally: shutil.rmtree(staging, ...)` removes. A prior fix
            # claimed that cleanup happened automatically; it does not - the
            # `downloaded` variable is kept live across this whole block
            # specifically so the except clause below can unlink it.
            size = check_download(downloaded)
            final = os.path.join(out_dir, f"{name}_{stamp}.xlsx")
            if os.path.abspath(downloaded) != os.path.abspath(final):
                replace_when_free(downloaded, final)
            downloaded = None      # renamed away; nothing left to clean up under this name
            out["files"].append(final)
            out["excel_bytes"] = size
            log(f"  excel    : {os.path.basename(final)}  {size / 1024:,.1f} KB"
                + ("  [DRM - opens in Excel, unreadable by other programs]"
                   if is_drm_protected(final) else ""))
        if export in ("csv", "both"):
            path, written, total = screen.to_csv(
                grid, os.path.join(out_dir, f"{name}_{stamp}_data.csv"))
            if not path or not os.path.isfile(path) or written <= 0:
                raise RuntimeError("CSV export did not produce a checked file")
            out["files"].append(path)
            out["csv_rows"] = written
            note = "" if written == total else f"  ({total - written} empty rows dropped)"
            log(f"  csv      : {os.path.basename(path)}  {written} rows{note}")
    except Exception:
        for path in out["files"]:
            try:
                os.unlink(path)
            except OSError:
                pass
        if downloaded:
            try:
                os.unlink(downloaded)
            except OSError:
                pass
        raise

    if close_after:
        ok, detail = screen.close()
        log(f"  tab      : {detail}")
        out["closed"] = ok

    # 11. Only now - after the query, the verification and the file - is any
    #     of this worth remembering. A profile written from a run that failed
    #     would be a guess dressed up as knowledge.
    #
    #     This is outside the export try/except above on purpose - the files
    #     already delivered must not be lost because of it - but it is its
    #     OWN try/except for the same reason: unhandled, an exception here
    #     used to propagate straight out of run_screen(), past the `return
    #     out` that carries the real, already-verified file paths.
    #     run_many()'s caller then only ever sees the exception and builds a
    #     brand new result with files=[] - a genuinely delivered export
    #     reported as "DID NOT FINISH ... nothing was saved" with the real
    #     files sitting on disk, unmentioned. Remembering the screen for
    #     next time is a convenience; failing to remember it must not cost
    #     the report that already succeeded.
    if use_profile and result_suspect:
        # A profile is what a run PROVED. A result that never moved may be
        # static content, so it is exported and warned about but not
        # remembered - saving it made the wrong grid the default for every
        # later replay (HISTORY.md Phase 82.21).
        msg = ("not remembered for next time: the result looked like static "
               "content, and a profile only keeps what a run proved. Confirm "
               "the right grid, then record it again")
        screen.warnings.append(msg)
    elif use_profile:
        try:
            saved = gmes_profile.save(
                code, screen.title, screen.menu_id, screen.info,
                from_ref=gmes_profile.field_ref(date_fields[0][0]) if date_fields else None,
                to_ref=gmes_profile.field_ref(date_fields[1][0]) if len(date_fields) > 1 else None,
                division=(gmes_profile.tree_ref(**screen.last_tree)
                          if screen.last_tree else None),
                grid=discovered_grid, rows=rows, options=resolved_options,
                grid_aliases=grid_aliases,
                values={"division": effective_division, "from": date_from or "",
                        "to": date_to or "", "verify": verify or "", "sets": dict(sets)},
                command=f"--division {division} --from {date_from} --to {date_to}",
                opening_info=opening_info,
                **destination_to_pin(out_dir, export))
            out["profile"] = saved
            log(f"  learned  : saved to {os.path.basename(saved)}")
        except Exception as e:
            msg = f"the export succeeded but this screen could not be remembered for next time: {e}"
            screen.warnings.append(msg)
            log(f"  warning  : {msg}")

    out["ok"] = True
    out["warnings"] = screen.warnings
    for w in screen.warnings:
        log(f"  warning  : {w}")
    out["seconds"] = round(time.time() - started, 1)
    return out


def run_many(ws, specs, log=print):
    """Run several screens SEQUENTIALLY, isolating each one.

    Sequential is a decision, not a limitation left for later:

      * Only one tab is in front, and a background screen still accepts filter
        writes - running two at once reintroduces that bug by design.
      * The Excel button and its "Save to Excel" dialog are global to the
        application; two exports would collide over both.
      * A modal popup blocks the whole application, not one screen.
      * Real parallelism needs a separate Chrome, a ~340 MB profile copy and
        an SSO sign-in each - more authentication load and more ways to fail
        unattended at 02:00.
      * The measured gain is small: 11.8s and 28.3s of server time for two
        reports. G-MES answering is the bottleneck, not this tool.

    A failure stops the batch because the foreground screen can no longer be
    proved safe for the next report."""
    specs = tuple(specs)
    results = []
    for index, spec in enumerate(specs):
        try:
            results.append(run_screen(ws, log=log, **spec))
        except Exception as e:
            code = spec.get("screen_code", "?")
            log(f"  FAILED   : {e}")
            try:
                gmes_common.screenshot_on_failure(f"gmes_{code}")
            except Exception as diagnostic_error:
                log(f"  diagnostic unavailable: {diagnostic_error}")
            results.append({"screen": code, "ok": False, "rows": 0, "files": [],
                            "error": str(e), "warnings": []})
            for skipped in specs[index + 1:]:
                results.append({"screen": skipped.get("screen_code", "?"), "ok": False,
                                "rows": 0, "files": [],
                                "error": "not run because the previous screen left an unknown state",
                                "warnings": []})
            break
    return results


def print_summary(results, log=print):
    log(f"\n{'=' * 70}\nSUMMARY\n{'=' * 70}")
    log(f"  {'SCREEN':<14} {'STATUS':<9} {'ROWS':>7}  FILES / ERROR")
    log("  " + "-" * 84)
    for r in results:
        if r["ok"]:
            files = ", ".join(os.path.basename(f) for f in r["files"]) or "-"
            log(f"  {r['screen']:<14} {'ok':<9} {r['rows']:>7}  {files}")
        else:
            log(f"  {r['screen']:<14} {'FAILED':<9} {'-':>7}  {r['error']}")
    ok = sum(1 for r in results if r["ok"])
    log(f"\n  {ok}/{len(results)} succeeded")
    return ok


def date_from_args(date=None, days_back=None):
    """One place for --date / --days-back, so every command agrees."""
    if days_back is not None:
        return (datetime.now() - timedelta(days=days_back)).strftime("%Y%m%d")
    return normalise_date(date)

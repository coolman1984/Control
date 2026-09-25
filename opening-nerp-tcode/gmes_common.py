"""
Shared helpers for driving GMES (Nexacro).

Why GMES is easier than NERP's SAP screens: Nexacro renders real DOM
elements and gives them stable, meaningful ids that mirror the form tree -
`mainframe.vFrameSet1.loginFrame.form.divLogin.form.btnLogin` is the Login
button and will still be called that tomorrow. So we target ids, not screen
positions, and nothing depends on window size.

What carries over unchanged from the NERP work:
  * Nexacro buttons are <div>s, so element.click() is not reliable - use a
    real dispatched mouse event.
  * Never sleep a fixed duration; poll until the thing is actually there.
"""
import json
import os
import time
from urllib.parse import urlsplit

import cdp_common
from cdp_common import (
    click_element_by_rect, connect, evaluate, get_page_tab, get_tabs,
)

GMES_URL = "http://seegmes4.sec.samsung.net/mes4/sm/nexacro/index_ext_2318.html"
GMES_HOST = urlsplit(GMES_URL).hostname


def is_gmes_page(tab):
    """True only for the actual G-MES page, never a RelayState-bearing SSO tab."""
    return (tab and tab.get("type") == "page"
            and (urlsplit(tab.get("url") or "").hostname or "").lower() == GMES_HOST)


def strict_gmes_tab(port=None):
    """Find a verified G-MES page without any generic-page fallback.

    This deliberately differs from gmes_tab(), whose fallback is useful to
    connection callers during startup. A diagnostic screenshot is evidence;
    an unrelated page is worse than no screenshot.
    """
    try:
        return next((tab for tab in get_tabs(port=port) if is_gmes_page(tab)), None)
    except Exception:
        return None


def gmes_tab(port=None, wait=20):
    """The browser tab showing GMES - waited for, and never guessed at.

    Matched on the EXACT host via `is_gmes_page()` - the same check
    `strict_gmes_tab()` uses for diagnostics, so the SSO window is excluded
    the same principled way in both places rather than by a second, looser
    rule here. (The SSO window carries the G-MES hostname inside its own
    `RelayState` query parameter, which is exactly what fooled an earlier
    substring match on the whole URL into picking the ADFS tab instead.)

    Waited for, not taken on the first look: straight after Chrome starts,
    the G-MES tab is still on about:blank or mid-navigation, so returning
    whatever page happened to be listed handed back a tab that was about to
    be replaced, and attaching to it died with "Connection to remote host
    was lost".

    A closed browser is the most common reason any of these tools fail, so
    that specific case is reported as one sentence rather than as a urllib
    stack trace about a refused connection to a port number.

    **Returns None if no G-MES tab appears within `wait` seconds - never an
    unrelated page.** This used to fall back to "whichever non-SSO tab
    happens to exist" (about:blank, a leftover page from a previous run,
    anything) once the deadline passed, which handed the general driving
    connection (`connect_gmes()`) a real, live websocket to a page that was
    not G-MES at all - filters, Inquiry, everything downstream then ran
    against the wrong page, either failing on some unrelated-looking control
    or, worse, succeeding against whatever was actually on screen (HISTORY.md
    Phase 79). Every caller already treats `None` as the correctly actionable
    "no G-MES tab" failure; a fallback tab that merely happened to exist was
    never actually safer than raising."""
    deadline = time.time() + wait
    while True:
        try:
            tabs = get_tabs(port=port)
        except Exception:
            raise RuntimeError(
                "Cannot reach the automation browser. It is not running, or was "
                "closed by a previous job. Start it with:  python gmes_login.py")
        found = next((t for t in tabs if is_gmes_page(t)), None)
        if found:
            return found
        if time.time() >= deadline:
            return None
        time.sleep(0.5)


#: How many G-MES page tabs one browser should ever have.
#: One. Everything this tool does happens inside a single Nexacro application,
#: which keeps its own work screens behind its own internal tab bar.
GMES_TABS_WANTED = 1

JS_OPEN_SCREEN_COUNT = r"""
(function () {
    try {
        const app = nexacro.getApplication();
        if (!app) return JSON.stringify({built: false, screens: 0});
        let screens = 0;
        const mdi = app.gvMdiFrame;
        if (mdi && mdi.form && mdi.form.divTab && mdi.form.divTab.form) {
            for (const k in mdi.form.divTab.form) if (/^TAB_/.test(k)) screens++;
        }
        return JSON.stringify({built: true, screens: screens});
    } catch (e) { return JSON.stringify({built: false, screens: 0}); }
})()
"""


def _open_screen_count(tab):
    """How many G-MES work screens this page has open. -1 if it cannot say."""
    ws = None
    try:
        ws = connect(tab["webSocketDebuggerUrl"], timeout=10)
        return int(evaluate(ws, JS_OPEN_SCREEN_COUNT).get("screens", 0))
    except Exception:
        return -1
    finally:
        if ws:
            try:
                ws.close()
            except Exception:
                pass


def prune_duplicate_gmes_tabs(port=None, keep=None, log=print):
    """Leave exactly one G-MES page open in the automation browser.

    **Why there is ever more than one.** "AD SSO Login" opens the ADFS page
    with `window.open()` (GMES_SKILL.md #51). When that sign-in succeeds, the
    popup follows its own RelayState back to the G-MES host - so the popup
    stops being an SSO window and becomes a second, fully loaded G-MES
    application with no work screens in it. Nothing closed it: the sign-in
    code only ever noticed when a popup closed ITSELF. Every sign-in that went
    through AD SSO therefore left one behind, and they accumulated - observed
    live at four G-MES tabs, three of them empty (HISTORY.md Phase 76.4).

    They are not harmless. Each is a full Nexacro application holding a
    session, and `gmes_tab()` picks whichever the browser lists first, so the
    run can attach to an empty duplicate while the screens it opened sit in
    another tab - the same class of failure as driving the wrong work screen
    (GMES_SKILL.md #25).

    **Which one survives.** The tab with G-MES work screens open, because that
    is where the run's own state is. Ties, and the case where none has any,
    fall back to the first listed. `keep` names one outright.

    Only G-MES pages are ever considered - never an SSO tab mid-flight, never
    anything else the profile has open - and a tab is only reported as closed
    once re-listing proves it gone. Returns a description, or "" when there
    was nothing to do."""
    try:
        tabs = [t for t in get_tabs(port=port) if is_gmes_page(t)]
    except Exception:
        return ""
    if len(tabs) <= GMES_TABS_WANTED:
        return ""

    keeper = next((t for t in tabs if t.get("id") == keep), None)
    if keeper is None:
        ranked = sorted(tabs, key=lambda t: -_open_screen_count(t))
        keeper = ranked[0]

    closed = []
    for tab in tabs:
        if tab.get("id") == keeper.get("id"):
            continue
        cdp_common.close_tab(tab["id"], port=port)
        closed.append(tab["id"])

    # A close that was accepted is not a close that happened - and a re-list
    # that FAILED is not proof either. It used to default `still` to an empty
    # set on any exception, which made "not in still" true for every closed
    # id and reported a full success with no evidence at all - the opposite
    # of "only report closed once re-listing proves it gone" (the docstring
    # above), silently, for the one failure mode (a transient CDP hiccup)
    # where the caller most needs to be told it does not actually know
    # (HISTORY.md Phase 78).
    try:
        still = {t.get("id") for t in get_tabs(port=port) if is_gmes_page(t)}
    except Exception:
        # Deliberately does NOT say "closed" anywhere - a close request was
        # SENT for `closed` tabs, but with no working re-list there is no
        # evidence any of them actually went away, and the whole point of
        # this project's "verify, don't assume" rule (CLAUDE.md 3.5) is that
        # a request that was sent is not the same claim as an outcome that
        # was proven.
        detail = (f"requested closing {len(closed)} duplicate G-MES tab"
                 f"{'s' if len(closed) != 1 else ''}, but could not verify "
                 "it worked - the browser did not answer")
        if log:
            log(f"  tabs     : {detail}")
        return detail

    gone = [i for i in closed if i not in still]
    stubborn = [i for i in closed if i in still]

    parts = []
    if gone:
        parts.append(f"closed {len(gone)} duplicate G-MES tab"
                     f"{'s' if len(gone) != 1 else ''}")
    if stubborn:
        parts.append(f"{len(stubborn)} would not close")
    detail = ", ".join(parts)
    if detail and log:
        log(f"  tabs     : {detail}")
    return detail


def capture_screenshot(path, port=None, timeout=20, tab=None):
    """G-MES's own screenshot: names the G-MES tab via `gmes_tab()`'s
    already-proven host matching, instead of `cdp_common.capture_screenshot`
    falling back to whichever page target happens to be listed first.

    That fallback is fine with one page target open, which is the ordinary
    case - but a leftover AD SSO popup, or an old tab, makes a second one
    exist at the same time, and the "diagnostic screenshot" then silently
    shows the wrong page while looking exactly like evidence. It nearly
    produced a wrong diagnosis once already (HISTORY.md Phase 56.4). A short
    wait, not the default 20s: by the time a screenshot is worth taking, the
    G-MES tab has necessarily already been open for a while."""
    tab = tab if is_gmes_page(tab) else strict_gmes_tab(port=port)
    if tab is None:
        return None
    return cdp_common.capture_screenshot(path, port=port, timeout=timeout, tab=tab)


def screenshot_on_failure(prefix="gmes_failure"):
    """Best-effort diagnostic snapshot next to the scripts, named by time -
    same contract as `cdp_common.screenshot_on_failure`, but targeting the
    G-MES tab specifically (see `capture_screenshot` above)."""
    name = f"{prefix}_{time.strftime('%Y%m%d_%H%M%S')}.png"
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), name)
    saved = capture_screenshot(path)
    if saved:
        print(f"Diagnostic screenshot saved: {saved}")
    return saved


class TabUnresponsive(RuntimeError):
    """The G-MES tab exists and the browser answers, but the tab itself never
    completes the CDP handshake (`Runtime.enable` unanswered). Distinct from
    "no G-MES tab is open" because the remedy differs: a missing tab is opened,
    a hung one can only be cleared by restarting the automation browser
    (HISTORY.md Phase 84.18)."""


def connect_gmes(timeout=20, port=None, attempts=4):
    """Attach to the G-MES tab, re-resolving it on each attempt.

    A tab that is loading can accept the socket and then drop it mid-handshake
    while Chrome swaps renderers, which surfaced as a bare
    WebSocketConnectionClosedException out of `Runtime.enable`. The tab is
    looked up again each time rather than retried against the same one,
    because by then it is usually a different tab."""
    last = None
    for attempt in range(attempts):
        tab = gmes_tab(port=port)
        if tab is None:
            raise RuntimeError("No G-MES tab is open. Run:  python gmes_login.py")
        try:
            return connect(tab["webSocketDebuggerUrl"], timeout=timeout)
        except Exception as e:
            last = e
            time.sleep(1.5)
    raise TabUnresponsive(f"Could not attach to the G-MES tab after {attempts} "
                          f"attempts ({last}).")


# ---------------------------------------------------------------------------
# The Nexacro engine cache, and why it has to be swept
# ---------------------------------------------------------------------------
#
# G-MES caches its whole Nexacro engine in localStorage under a key made of a
# timestamp and the engine URL:
#
#     1789029501234http://seegmes4.sec.samsung.net/mes4/sm/nexacro/engine
#
# It writes a NEW one and never removes the old. Each is about 99 KB, the
# browser allows about 5 MB per site, so after roughly fifty loads the quota
# is full - and then the bootstrap throws
#
#     QuotaExceededError: Failed to execute 'setItem' on 'Storage'
#
# and the application never starts. The page reports readyState "complete"
# with `nexacro` defined, `nexacro.getApplication()` empty, and three divs.
# There are no failed requests and no HTTP errors, so nothing points at the
# cause, and reloading cannot help because the storage is still full.
#
# Measured on this machine when it happened: 102 entries, 5.00 MB, 52 copies
# of the engine. Removing the 51 stale ones freed 4.94 MB and the app built
# itself in under four seconds.
#
# Automation hits this far sooner than a person does, because it opens the
# page repeatedly all day.

_ENGINE_KEY = r"^\d{10,}http.*\/engine$"

JS_STORAGE_STATE = r"""
(function() {
    let bytes = 0, engine = 0;
    try {
        for (let i = 0; i < localStorage.length; i++) {
            const k = localStorage.key(i);
            bytes += k.length + (localStorage.getItem(k) || '').length;
            if (new RegExp(%s).test(k)) engine++;
        }
        return JSON.stringify({ok: true, entries: localStorage.length,
                               bytes: bytes, engineCopies: engine});
    } catch (e) {
        return JSON.stringify({ok: false, reason: e.message});
    }
})()
"""

JS_PRUNE_ENGINE_CACHE = r"""
(function() {
    const keep = %d;
    try {
        const keys = [];
        for (let i = 0; i < localStorage.length; i++) {
            const k = localStorage.key(i);
            if (new RegExp(%s).test(k)) keys.push(k);
        }
        // The timestamp prefix is fixed width, so a plain sort puts the
        // oldest first. Only the engine cache is touched - anything else the
        // site keeps here (a remembered ID, for one) is left alone.
        keys.sort();
        const stale = keep > 0 ? keys.slice(0, -keep) : keys;
        let freed = 0;
        for (const k of stale) {
            freed += (localStorage.getItem(k) || '').length;
            localStorage.removeItem(k);
        }
        return JSON.stringify({ok: true, found: keys.length,
                               removed: stale.length, freed: freed});
    } catch (e) {
        return JSON.stringify({ok: false, reason: e.message});
    }
})()
"""


def storage_state(ws):
    """How full this site's localStorage is, and how many engine copies."""
    try:
        return evaluate(ws, JS_STORAGE_STATE % cdp_common.json.dumps(_ENGINE_KEY))
    except Exception as e:
        return {"ok": False, "reason": str(e)}


def prune_nexacro_cache(ws, keep=1):
    """Delete the stale engine caches, keeping the newest `keep`.

    Safe to call at any time: this is a cache the application rebuilds on its
    next load. It is not a fix for a slow page - it is the fix for a page that
    cannot start at all."""
    try:
        return evaluate(ws, JS_PRUNE_ENGINE_CACHE
                        % (keep, cdp_common.json.dumps(_ENGINE_KEY)))
    except Exception as e:
        return {"ok": False, "reason": str(e)}


JS_APP_BUILT = """
(function() {
    let app = false;
    try { app = (typeof nexacro !== 'undefined') && !!nexacro.getApplication(); }
    catch (e) {}
    return JSON.stringify({app: app, divs: document.querySelectorAll('div').length,
                           ready: document.readyState});
})()
"""


def app_is_built(ws):
    """Whether the Nexacro application object exists yet.

    A page that is merely slow and a page that has failed to bootstrap look
    identical from the outside - both are blank. This is the difference."""
    try:
        return bool(evaluate(ws, JS_APP_BUILT).get("app"))
    except Exception:
        return False


def js_find_by_id(element_id):
    """Locate one Nexacro element by its exact id and report where it is."""
    return """
    (function() {
        const isVisible = %s;
        const el = document.getElementById(%s);
        if (!el) return JSON.stringify({found: false, reason: 'no such id'});
        if (!isVisible(el)) return JSON.stringify({found: false, reason: 'not visible'});
        const r = el.getBoundingClientRect();
        return JSON.stringify({found: true, text: (el.textContent || '').trim().slice(0, 40),
                               x: r.left + r.width/2, y: r.top + r.height/2});
    })()
    """ % (cdp_common.JS_IS_VISIBLE, cdp_common.json.dumps(element_id))


def click_by_id(ws, element_id, attempts=20, delay=0.5):
    """Click a Nexacro control by id, waiting for it to appear first.

    Polls rather than assuming the control is ready: a Nexacro screen builds
    itself in JavaScript after the page reports 'complete', so a control can
    be seconds away from existing when we first look."""
    for _ in range(attempts):
        info = evaluate(ws, js_find_by_id(element_id))
        if info.get("found"):
            click_element_by_rect(ws, info["x"], info["y"])
            return info
        time.sleep(delay)
    return None


def set_value_by_id(ws, element_id, value):
    """Type into a Nexacro input. Nexacro listens for the events, so a bare
    value assignment is not enough."""
    js = """
    (function() {
        const value = %s;
        const el = document.getElementById(%s);
        if (!el) return JSON.stringify({found: false});
        %s
        el.dispatchEvent(new Event('blur', { bubbles: true }));
        return JSON.stringify({found: true, value: el.value});
    })()
    """ % (cdp_common.json.dumps(value), cdp_common.json.dumps(element_id),
           cdp_common.JS_SET_VALUE)
    return evaluate(ws, js)


JS_SCREEN_REPORT = """
(function() {
    const isVisible = %s;
    const seen = new Set();
    const items = [];
    Array.from(document.querySelectorAll('div, span, button, a, td, li')).forEach(el => {
        const t = (el.textContent || '').trim();
        if (!t || t.length > 40) return;
        if (!isVisible(el) || seen.has(t)) return;
        seen.add(t);
        const r = el.getBoundingClientRect();
        items.push({text: t, id: el.id,
                    x: Math.round(r.left + r.width/2), y: Math.round(r.top + r.height/2)});
    });
    const fields = Array.from(document.querySelectorAll('input, textarea, select'))
        .filter(isVisible)
        .map(el => ({id: el.id, type: el.type || '', value: (el.value || '').slice(0, 40)}));
    return JSON.stringify({
        title: document.title,
        url: location.href,
        loggedIn: !document.getElementById(
            'mainframe.vFrameSet1.loginFrame.form.divLogin.form.btnLogin'),
        textPreview: (document.body ? (document.body.innerText || '') : '')
                        .replace(/\\s+/g, ' ').trim().slice(0, 500),
        fields: fields.slice(0, 40),
        items: items.slice(0, 80)
    });
})()
""" % cdp_common.JS_IS_VISIBLE


def screen_report(ws):
    return evaluate(ws, JS_SCREEN_REPORT)


def print_report(report, item_limit=60):
    print(f"Title      : {report.get('title')!r}")
    print(f"URL        : {report.get('url')}")
    print(f"Logged in  : {report.get('loggedIn')}")
    print(f"\nOn screen  : {report.get('textPreview')!r}\n")
    if report.get("fields"):
        print("Fields:")
        for f in report["fields"]:
            print(f"  id={f['id']!r} type={f['type']!r} value={f['value']!r}")
    if report.get("items"):
        print("\nClickable items:")
        for it in report["items"][:item_limit]:
            print(f"  {it['text']!r:36} id={it['id']!r} at ({it['x']}, {it['y']})")


def wait_until(ws, predicate, max_wait=60, poll_interval=1.0):
    """Poll a screen report until predicate(report) is true. Returns the last
    report either way, so a timeout still shows what was actually on screen."""
    deadline = time.time() + max_wait
    report = {}
    while time.time() < deadline:
        try:
            report = screen_report(ws)
            if predicate(report):
                return True, report
        except Exception:
            pass
        time.sleep(poll_interval)
    return False, report


def list_windows(port=None):
    """Every browser window/tab currently open - SSO often opens a popup."""
    return [t for t in get_tabs(port=port) if t.get("type") == "page"]


# --------------------------------------------------------------------------
# Finding controls
# --------------------------------------------------------------------------
#
# Full ids must NEVER be hardcoded for anything inside a work screen. GMES
# numbers each opened screen instance, so the same Inquiry button was
#   ...workFrameSet.winPPM0219_0_516.form.divLeft.form.btnSearch
# on one visit and
#   ...workFrameSet.winPPM0219_0_315.form.divLeft.form.btnSearch
# on the next. An exact-id rule works once and then silently finds nothing.
#
# The stable parts are the screen code (winPPM0219), the trailing control
# path (form.divLeft.form.btnSearch), the CSS class Nexacro assigns by
# control type (btn_LF_Search_New), and the visible label. Match on those.
#
# Only the shell frames (topFrame, loginFrame, mdiFrame) have genuinely
# fixed ids, because there is only ever one of each.

def js_find_elements(id_regex=None, cls=None, text=None, exact_text=True,
                     visible_only=True, limit=40):
    """`limit` bounds the RETURNED hits, never the scan.

    It used to also cap the scan itself (`break` the moment `limit` matches
    were collected), which silently dropped the real answer whenever a large
    result grid held more than `limit` cells with the exact same text as a
    dialog button. A 1425-row grid with an "OK" column exhausted the cap
    before the DOM walk ever reached the "Save to Excel" dialog's own OK
    button, appended later in document order - `click_control(text="OK")`
    then had nothing but grid cells to choose from and clicked one of those,
    not the dialog, on R4351UM01 (HISTORY.md Phase 84.26). The real button
    was in fact the single smallest match by area even among every "OK" on
    the page - the cap, not the smallest-box rule, was the defect. Every
    match is now collected and sorted before `limit` trims the list, so the
    smallest-box rule sees the whole page, matching CLAUDE.md 4.6: a cap
    that hides data is worse than no cap."""
    return """
    (function() {
        const isVisible = %s;
        const idRe   = %s;
        const cls    = %s;
        const text   = %s;
        const exact  = %s;
        const visOnly = %s;

        const rx = idRe ? new RegExp(idRe) : null;
        const out = [];
        for (const el of document.querySelectorAll('*')) {
            const id = el.id || '';
            const klass = (typeof el.className === 'string') ? el.className : '';
            const t = (el.textContent || '').trim();

            if (rx && !rx.test(id)) continue;
            if (cls && !klass.split(/\\s+/).includes(cls)) continue;
            if (text !== null) {
                if (exact ? t !== text : !t.toLowerCase().includes(text.toLowerCase()))
                    continue;
            }
            if (visOnly && !isVisible(el)) continue;

            const r = el.getBoundingClientRect();
            out.push({id: id, cls: klass.slice(0, 60), text: t.slice(0, 40),
                      tag: el.tagName, w: Math.round(r.width), h: Math.round(r.height),
                      x: r.left + r.width/2, y: r.top + r.height/2,
                      area: r.width * r.height});
        }
        // Smallest first: the real control, not a container wrapping it.
        // Sorted over EVERY match, THEN trimmed - see the docstring above.
        out.sort((a, b) => a.area - b.area);
        return JSON.stringify({count: out.length, hits: out.slice(0, %d)});
    })()
    """ % (cdp_common.JS_IS_VISIBLE,
           cdp_common.json.dumps(id_regex) if id_regex else "null",
           cdp_common.json.dumps(cls) if cls else "null",
           cdp_common.json.dumps(text) if text is not None else "null",
           "true" if exact_text else "false",
           "true" if visible_only else "false",
           limit)


def find_elements(ws, **criteria):
    return evaluate(ws, js_find_elements(**criteria))


def click_control(ws, attempts=20, delay=0.5, **criteria):
    """Click the smallest visible control matching the criteria, waiting for
    it to appear first. Returns the clicked element, or None."""
    for _ in range(attempts):
        found = find_elements(ws, **criteria)
        if found.get("count"):
            target = found["hits"][0]
            click_element_by_rect(ws, target["x"], target["y"])
            return target
        time.sleep(delay)
    return None


# --------------------------------------------------------------------------
# Signed-in state
# --------------------------------------------------------------------------

# The signed-in user's name sits in the top bar. Testing for THIS is right;
# testing for the absence of the Login button is not - Nexacro keeps the
# login frame in the DOM after signing in, merely hidden, so "no login
# button" never becomes true and the check would report a failed login
# forever.
USER_INFO_BTN = "mainframe.vFrameSet1.vFrameSet2.topFrame.form.btnUserInfo"


def is_logged_in(ws):
    info = evaluate(ws, js_find_by_id(USER_INFO_BTN))
    return bool(info.get("found")), info.get("text", "")


# --------------------------------------------------------------------------
# Popups
# --------------------------------------------------------------------------

# Nexacro gives every floating popup a title bar with the same three
# controls. Matching on the CSS class rather than the popup's own name
# matters: the Notice window's id contains Korean text (`공지사항`), and the
# next popup will have a different name again, so a name-based rule would
# only ever close this one popup on this one screen.
#
# The scope is deliberately narrow - only title bars marked
# `titlebarChildFrame`, i.e. floating child windows. The user's actual work
# screen also has a close button, and closing that would throw away the
# thing the automation just opened.
JS_CLOSE_CHILD_POPUPS = """
(function() {
    const isVisible = %s;
    const closed = [];
    const bars = Array.from(document.querySelectorAll('.TitleBarControl.titlebarChildFrame'));
    for (const bar of bars) {
        if (!isVisible(bar)) continue;
        const btn = bar.querySelector('.closebutton');
        if (!btn || !isVisible(btn)) continue;
        const r = btn.getBoundingClientRect();
        closed.push({
            name: (bar.textContent || '').trim().slice(0, 40),
            id: btn.id,
            bar_id: bar.id,
            x: r.left + r.width / 2,
            y: r.top + r.height / 2
        });
    }
    return JSON.stringify({count: closed.length, popups: closed});
})()
""" % cdp_common.JS_IS_VISIBLE


def find_child_popups(ws):
    return evaluate(ws, JS_CLOSE_CHILD_POPUPS)


# A live sign-in on 2026-09-13 hit a Notice popup (`S9502UP01`) whose close
# button reported a real, on-screen bounding box but did not respond to
# clicks there at all - coordinate clicks landed on nothing, every retry
# "clicked" it and changed nothing, and the code above this used to give up
# and carry on with the popup still covering the work screen (HISTORY.md
# Phase 59.1).
#
# Nexacro floating popups are plain JS objects reachable by walking
# `nexacro.getApplication()`: most segments of a popup's dot-path are direct
# properties of their parent, but some (this one's Korean name, `공지사항`,
# among them - see gotcha #10) are only found by searching the parent's
# `_frames` collection for a matching `.name`. Once resolved, calling the
# object's own `_on_closebutton_click()` runs the exact handler the button's
# click would have, without going through screen coordinates or hit-testing
# at all - confirmed live: it closed the popup that coordinate clicks could
# not. There is no public `.close()` on this Nexacro version; a `_closePopup()`
# method also exists and returns without error, but was confirmed live NOT to
# remove the popup - only `_on_closebutton_click()` was proven to work.
JS_FALLBACK_CLOSE_POPUP = """
(function() {
    function resolve(path) {
        var parts = path.split('.');
        var obj = window.nexacro.getApplication();
        for (var i = 0; i < parts.length; i++) {
            var part = parts[i];
            if (obj == null) return null;
            if (obj[part] !== undefined) { obj = obj[part]; continue; }
            var frames = obj._frames;
            var found = null;
            if (frames) {
                if (Array.isArray(frames)) {
                    for (var j = 0; j < frames.length; j++) {
                        if (frames[j] && frames[j].name === part) { found = frames[j]; break; }
                    }
                } else if (typeof frames === 'object') {
                    for (var key in frames) {
                        if (frames[key] && frames[key].name === part) { found = frames[key]; break; }
                    }
                    if (!found && frames[part]) found = frames[part];
                }
            }
            if (found) { obj = found; } else { return null; }
        }
        return obj;
    }
    var barId = %s;
    var path = barId.replace(/\\.titlebar$/, '');
    var frame = resolve(path);
    if (!frame) return JSON.stringify({ok: false, reason: 'frame not found'});
    if (typeof frame._on_closebutton_click !== 'function') {
        return JSON.stringify({ok: false, reason: 'no close handler on frame'});
    }
    try {
        frame._on_closebutton_click();
        return JSON.stringify({ok: true});
    } catch (e) {
        return JSON.stringify({ok: false, reason: 'threw: ' + e.message});
    }
})()
"""


def fallback_close_popup(ws, bar_id):
    """Close one popup by calling its own close handler, not by clicking.

    Used only once coordinate clicks have already been tried and have
    stopped reducing the popup count - it is slower (a full frame-tree walk)
    and reaches into Nexacro internals, so it is a fallback, not the first
    move."""
    if not bar_id:
        return False
    js = JS_FALLBACK_CLOSE_POPUP % json.dumps(bar_id)
    result = evaluate(ws, js)
    return bool(result.get("ok"))


def close_popups_when_they_appear(ws, appear_wait=45, poll_interval=1.0,
                                  quiet_rounds=3, verbose=True):
    """Wait for popups to show up, then close them - and keep watching.

    Checking once straight after signing in finds nothing: the Notice window
    is opened by the app a few seconds AFTER the user name appears in the top
    bar, so a single check races it and reports "none were open" while the
    popup is on its way. It then sits there blocking every later click.

    So this waits for the first popup to appear, closes it, and keeps
    looking until several consecutive checks come back empty - which also
    catches notices that queue up one behind another.

    If clicking stops reducing the count, it falls back to calling the
    popup's own close handler directly (see `fallback_close_popup`) before
    giving up - coordinate clicks alone were confirmed live (2026-09-13) to
    leave a Notice popup on screen while reporting valid click coordinates
    for it (HISTORY.md Phase 59.1)."""
    closed = []
    started = time.time()
    deadline = started + appear_wait
    quiet, stuck = 0, 0

    # `or closed` used to keep this loop alive indefinitely once anything had
    # been closed, and the deadline was pushed out another 8s on every pass
    # that clicked something - so a popup that would not close kept the loop
    # running and the list growing. It is bounded now, in both directions.
    while time.time() < deadline and time.time() - started < appear_wait * 2:
        found = find_child_popups(ws)
        before = found.get("count", 0)
        if before:
            quiet = 0
            for popup in found["popups"]:
                click_element_by_rect(ws, popup["x"], popup["y"])
                name = popup["name"] or popup["id"]
                closed.append(name)
                if verbose:
                    print(f"  closed popup: {name}")
                time.sleep(0.4)
            time.sleep(poll_interval)
            still = find_child_popups(ws)
            if still.get("count", 0) >= before:
                stuck += 1
                if stuck >= 2:
                    if verbose:
                        print("  clicking is not closing it - trying its close "
                              "handler directly instead")
                    for popup in still.get("popups", []):
                        if fallback_close_popup(ws, popup.get("bar_id")):
                            name = popup["name"] or popup["id"]
                            if name not in closed:
                                closed.append(name)
                            if verbose:
                                print(f"  closed popup (fallback): {name}")
                    time.sleep(poll_interval)
                    if find_child_popups(ws).get("count", 0):
                        if verbose:
                            print("  a popup is still not responding - "
                                  "leaving it and carrying on")
                        break
                    stuck = 0
                    continue
            else:
                stuck = 0
                deadline = max(deadline, time.time() + 8)   # let a queued one arrive
            continue

        quiet += 1
        if closed and quiet >= quiet_rounds:
            break
        time.sleep(poll_interval)

    return closed


def close_child_popups(ws, rounds=8, delay=0.5):
    """Click the X on every floating popup, until they are actually gone.

    Repeats because popups queue up: the Notice window can be followed by
    another one that only appears once the first is gone, so a single pass
    leaves the screen blocked.

    But it stops when clicking stops WORKING. A real sign-in reported closing
    the same popup - `S9502UP01` - twenty-three times, because each pass
    re-found the one before it and clicked it again. The count is checked
    after every pass: if it has not dropped twice running, the clicks are not
    closing anything and going round again only burns time. That run spent
    most of its 52 seconds here.

    Once clicking is confirmed not to be working, this tries the popup's own
    close handler directly (`fallback_close_popup`) before giving up -
    clicking a valid on-screen close button was confirmed live (2026-09-13)
    to leave a Notice popup open (HISTORY.md Phase 59.1)."""
    closed, stuck = [], 0
    for _ in range(rounds):
        found = find_child_popups(ws)
        before = found.get("count", 0)
        if not before:
            break
        for popup in found["popups"]:
            click_element_by_rect(ws, popup["x"], popup["y"])
            closed.append(popup["name"] or popup["id"])
            time.sleep(0.3)
        time.sleep(delay)

        still = find_child_popups(ws)
        if still.get("count", 0) >= before:
            stuck += 1
            if stuck >= 2:
                for popup in still.get("popups", []):
                    if fallback_close_popup(ws, popup.get("bar_id")):
                        name = popup["name"] or popup["id"]
                        if name not in closed:
                            closed.append(name)
                time.sleep(delay)
                if find_child_popups(ws).get("count", 0):
                    break
                stuck = 0
        else:
            stuck = 0
    return closed

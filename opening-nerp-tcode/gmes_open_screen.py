"""
Open any G-MES screen by its code or name - the equivalent of typing a
T-code in N-ERP.

    python gmes_open_screen.py P1112WM00           # open by screen code
    python gmes_open_screen.py "Master Prod. Plan" # open by menu name
    python gmes_open_screen.py --find plan         # search the whole screen catalogue
    python gmes_open_screen.py --current           # what is open right now

Why a search box and not the menus: the menu tree is four levels deep and
its labels are translated, while a screen code is short, stable and printed
on every screen's own breadcrumb. One entry point reaches every screen this
account can open - a live, account-owned catalogue (810 at last count),
not a fixed number this project controls, so it is not hardcoded here.

Two things had to be learned to make this reliable, both recorded in
HISTORY.md:

  * Setting the search box value through the Nexacro API fills the box but
    searches nothing. The suggestion list is driven by the control's keyup
    handler, so the text must be typed with real key events.
  * The Notice popup is MODAL. While it is open the whole application is
    greyed out and every click is swallowed silently - the search button
    appeared to do nothing at all until the popup was closed first.
"""
import argparse
import re
import sys
import time

import cdp_common
from cdp_common import evaluate, send
import gmes_common
import gmes_data
from gmes_common import (
    click_element_by_rect, close_child_popups, connect_gmes, find_elements,
    js_find_by_id,
)

SEARCH_EDIT = "mainframe.vFrameSet1.vFrameSet2.topFrame.form.divSearch.form.edtSearch"

# The screen catalogue is `gdsMenuList` - 1177 rows, held client-side.
#
# The row for the Production Plan screen shows the whole model:
#     menuId       PPM0219                          <- the "T-code"
#     sysScreenId  P1112UM00                        <- screen code on the breadcrumb
#     enMsgCont    Production Plan by Order(Line)
#     koMsgCont    오더별(라인별) 생산계획
#     screenSn     UI0000>PPM0001>PPM0087>...       <- ancestors, i.e. the breadcrumb
#     fldrNm       PM_ppn_ppn::                     <- + sysScreenId = gdsOpenMenu.pageUrl
#     menuTypeCode M                                <- M is a screen, D is a folder
#
# menuId is the identifier that matters: it is what gdsOpenMenu records when
# a screen opens, and what the window is named after (winPPM0219_0_603).
#
# Do NOT use `gdsMenuList_` for this. It looks like the same catalogue but
# its menuTitle is Korean only and its menuIds are a different series
# (PM0001 vs PPM0219), so joining or searching it in English finds nothing.
JS_CATALOGUE = r"""
(function() {
    const ds = nexacro.getApplication().gdsMenuList;
    if (!ds) return JSON.stringify({found: false});

    const val = (r, c) => { try { return String(ds.getColumn(r, c) || ''); } catch (e) { return ''; } };

    // Pass one: every menuId's display name, so ancestors can be resolved
    // into a readable breadcrumb.
    const name = {};
    for (let r = 0; r < ds.getRowCount(); r++) {
        const id = val(r, 'menuId');
        if (id) name[id] = val(r, 'enMsgCont') || val(r, 'msgCont') || val(r, 'koMsgCont');
    }
    function breadcrumb(sn) {
        // screenSn is "UI0000>PPM0001>PPM0087>..." - the ancestor chain.
        return sn.split('>')
                 .filter(id => id && id !== 'UI0000')
                 .map(id => name[id] || id)
                 .join(' > ');
    }

    const q = %s.toLowerCase();
    const rows = [];
    let screens = 0;
    // `matched` used to be `rows.length` - since rows itself was capped at
    // 60, a 143-match search silently reported "60 matches" with no sign
    // any were missing. A caller relying on a single unambiguous result
    // among "60" could pick the wrong one, or open the first when the real
    // target was #74 and never displayed. Counted independently of the cap,
    // so `matched > rows.length` is a real, checkable truncation signal
    // (HISTORY.md - external review of 1957ba9/cff282b, finding #12).
    let matched = 0;
    for (let r = 0; r < ds.getRowCount(); r++) {
        const screenId = val(r, 'sysScreenId');
        // Folders have no screen, and external links carry a URL instead.
        if (!screenId || screenId.indexOf('http') === 0) continue;
        screens++;
        const menuId = val(r, 'menuId');
        const en = val(r, 'enMsgCont') || val(r, 'msgCont');
        const ko = val(r, 'koMsgCont');
        const hay = (menuId + ' ' + screenId + ' ' + en + ' ' + ko).toLowerCase();
        if (q && hay.indexOf(q) === -1) continue;
        matched++;
        if (rows.length < 60) {
            rows.push({screenId: screenId, menuTitle: en || ko, korean: ko,
                       path: breadcrumb(val(r, 'screenSn')), menuId: menuId,
                       sysCode: val(r, 'sysCode'),
                       pageUrl: val(r, 'fldrNm') + screenId});
        }
    }
    return JSON.stringify({found: true, total: screens, matched: matched,
                           returned: rows.length, truncated: matched > rows.length,
                           rows: rows});
})()
"""

# gdsOpenMenu lists the screens currently open as tabs.
JS_OPEN_MENU = r"""
(function() {
    const ds = nexacro.getApplication().gdsOpenMenu;
    if (!ds) return JSON.stringify({found: false});
    const cols = [];
    for (let i = 0; i < ds.getColCount(); i++) cols.push(ds.getColID(i));
    const rows = [];
    for (let r = 0; r < ds.getRowCount(); r++) {
        const o = {};
        for (const c of cols) { try { o[c] = String(ds.getColumn(r, c) || ''); } catch (e) {} }
        rows.push(o);
    }
    return JSON.stringify({found: true, rows: rows});
})()
"""


def catalogue(ws, query=""):
    """Search the client-side screen catalogue by code, name, path or menu id."""
    return evaluate(ws, JS_CATALOGUE % cdp_common.json.dumps(query))


def open_screens(ws):
    return evaluate(ws, JS_OPEN_MENU)


# A work-form (...WM00, ...UF00, ...WF00) has its own catalogue entry but is
# never itself a top-level tab: it loads nested inside its ...UM00 shell's
# tab, and gdsOpenMenu only ever records the shell. Its form path carries the
# containing tab's window id as a plain segment (e.g.
# "...workFrameSet.winPPM0221_2_373.divWorkMain..."), which is how a code
# whose shell is already open is still recognised without a catalogue
# search - one that would click the already-open shell, create no new tab,
# and time out waiting for a menu id gdsOpenMenu will never record.
# Ported from the standalone gmes package (HISTORY.md Phase 41.2 /
# GMES_SKILL #47) at explicit user request, as a deliberate exception to
# the migration plan's freeze on legacy scripts - this specific bug was
# reproduced live through run_gmes_workflow.py.
_WIN_ID_RE = re.compile(r"win[A-Za-z0-9]+_\d+_\d+")


def tab_for_embedded_form(ws, code, rows):
    """The already-open tab containing `code`, if it is loaded as a nested
    work-form rather than a top-level tab of its own; else None."""
    for form in gmes_data.list_forms(ws).get("forms", []):
        if form.get("file", "").upper().startswith(code.upper()):
            match = _WIN_ID_RE.search(form.get("path", ""))
            if match:
                return next((r for r in rows if r.get("winId") == match.group(0)), None)
    return None


def type_into_search(ws, text):
    """Clear the search box and type into it with real key events.

    `set_value()` alone is not enough: the suggestion list is produced by
    the control's own keyup handler, so a programmatic assignment fills the
    box and searches nothing."""
    evaluate(ws, """
    (function() {
        const f = nexacro.getApplication().mainframe.vFrameSet1.vFrameSet2.topFrame.form;
        f.divSearch.form.edtSearch.set_value("");
        return JSON.stringify({ok: true});
    })()""")

    box = evaluate(ws, js_find_by_id(SEARCH_EDIT))
    if not box.get("found"):
        raise RuntimeError(f"The search box is not reachable ({box.get('reason')}). "
                           "A modal popup may be covering the application.")
    click_element_by_rect(ws, box["x"], box["y"])
    time.sleep(0.4)

    for ch in text:
        for kind in ("keyDown", "char", "keyUp"):
            params = {"type": kind, "key": ch, "code": f"Key{ch.upper()}"}
            if kind == "char":
                params["text"] = ch
            else:
                params["windowsVirtualKeyCode"] = ord(ch.upper())
            send(ws, "Input.dispatchKeyEvent", params)
        time.sleep(0.08)
    return box


# The suggestion list is a Nexacro grid, `integratedSearch.form.grdResult`,
# bound to `dsSearchResult`. Reading that dataset says exactly which row is
# which, so the right row can be chosen by data instead of by guessing which
# visible element is a result.
JS_SEARCH_RESULTS = r"""
(function() {
    const app = nexacro.getApplication();
    let f = null;
    try { f = app.mainframe.vFrameSet1.vFrameSet2.topFrame.form.integratedSearch.form; }
    catch (e) { return JSON.stringify({found: false, reason: 'popup not created'}); }
    if (!f) return JSON.stringify({found: false, reason: 'popup not created'});
    const ds = f.dsSearchResult;
    if (!ds) return JSON.stringify({found: false, reason: 'no result dataset'});
    const rows = [];
    for (let r = 0; r < ds.getRowCount(); r++) {
        const g = c => { try { return String(ds.getColumn(r, c) || ''); } catch (e) { return ''; } };
        rows.push({index: r, menuId: g('menuId'), screenId: g('sysScreenId'),
                   name: g('enMsgCont') || g('msgCont') || g('koMsgCont')});
    }
    return JSON.stringify({found: true, count: rows.length, rows: rows});
})()
"""


def wait_for_results(ws, max_wait=20, poll_interval=0.5):
    """Wait for the search to return rows in dsSearchResult."""
    deadline = time.time() + max_wait
    last = {}
    while time.time() < deadline:
        last = evaluate(ws, JS_SEARCH_RESULTS)
        if last.get("found") and last.get("count"):
            return last["rows"]
        time.sleep(poll_interval)
    return []


def js_result_row_rect(index):
    """Where to click for result row `index`.

    It must be the GRID row. The first attempt clicked the popup's detail
    panel (`divDetail.form.staTitle`), which shows the same text and is the
    obvious visual match - and clicking it opens nothing at all."""
    return """
    (function() {
        const isVisible = %s;
        const prefix = 'mainframe.vFrameSet1.vFrameSet2.topFrame.form.integratedSearch.form.grdResult.body.gridrow_%d';
        let el = document.getElementById(prefix + '.cell_%d_0') ||
                 document.getElementById(prefix);
        if (!el) return JSON.stringify({found: false, reason: 'grid row not rendered'});
        if (!isVisible(el)) return JSON.stringify({found: false, reason: 'grid row not visible'});
        const r = el.getBoundingClientRect();
        return JSON.stringify({found: true, id: el.id,
                               text: (el.textContent || '').trim().slice(0, 50),
                               x: r.left + r.width/2, y: r.top + r.height/2});
    })()
    """ % (cdp_common.JS_IS_VISIBLE, index, index)


def js_window_visible(win_id):
    return """
    (function() {
        const isVisible = %s;
        const el = document.getElementById(
            'mainframe.vFrameSet1.vFrameSet2.hFrameSet1.workFrameSet.%s');
        if (!el) return JSON.stringify({found: false});
        return JSON.stringify({found: true, visible: isVisible(el)});
    })()
    """ % (cdp_common.JS_IS_VISIBLE, win_id)


def activate_screen(ws, win_id, max_wait=20):
    """Bring an already-open screen to the front by clicking its tab.

    G-MES keeps every opened screen alive behind tabs, so a screen can be
    "open" while a completely different one is in front. Their datasets stay
    reachable either way, which makes this dangerous: filters written to a
    background screen apply fine, and then the Inquiry click lands on
    whatever IS in front. That produced a run that set the date and division
    correctly, queried the wrong screen, and reported zero rows.

    Tabs are named after the window: TAB_<winId>."""
    tab_id = (f"mainframe.vFrameSet1.vFrameSet2.mdiFrame.form.divTab.form."
              f"TAB_{win_id}")
    info = evaluate(ws, js_find_by_id(tab_id))
    if not info.get("found"):
        return False
    click_element_by_rect(ws, info["x"], info["y"])

    deadline = time.time() + max_wait
    while time.time() < deadline:
        state = evaluate(ws, js_window_visible(win_id))
        if state.get("found") and state.get("visible"):
            return True
        time.sleep(0.5)
    return False


FIRST_SEARCH_WAIT = 8


def open_screen(ws, query, timeout=90, log=print):
    """Close blockers, search, click the first suggestion, confirm it opened.

    `log` is how the caller receives the running commentary. It used to
    `print()` straight to stdout, which meant these lines appeared in the
    middle of whatever the caller was drawing, in a different shape from
    everything around them. They are reported in the same `key : detail` form
    as the rest so a front end can lay them out with the others."""
    closed = close_child_popups(ws)
    if closed:
        log(f"  popups   : closed {closed}")

    before = {r.get("winId") for r in open_screens(ws).get("rows", [])}

    # Typed twice at most. The FIRST search of a session returns nothing: the
    # search panel's form does not exist yet (`popupCreated: false`, the panel
    # reports "no result dataset" then stays empty) and the first query is lost
    # while it is created; every later search - the same query typed again
    # included - answers in ~2 s. Reproduced on a fresh session (HISTORY.md Phase
    # 84.3). So the first try only waits `FIRST_SEARCH_WAIT` (answers normally take
    # 2 s), not the full 20 s, before the query is typed again.
    results = []
    for attempt, cap in ((1, FIRST_SEARCH_WAIT), (2, 20)):
        type_into_search(ws, query)
        results = wait_for_results(ws, max_wait=cap)
        if results:
            break
        if attempt == 1:
            log("  search   : nothing came back - typing it once more")
    if not results:
        reason = ""
        try:
            reason = evaluate(ws, JS_SEARCH_RESULTS).get("reason") or ""
        except Exception:                                 # noqa: BLE001
            pass
        raise RuntimeError(
            f"The search returned nothing for {query!r} (typed twice"
            + (f"; the search panel says: {reason}" if reason else "")
            + "). Check the code with:  "
            f"python gmes_open_screen.py --find {query}")

    # Prefer an exact screen-code or menu-id match over the first row.
    wanted = query.strip().upper()
    chosen = next((r for r in results
                   if wanted in (r["screenId"].upper(), r["menuId"].upper())),
                  results[0])
    if len(results) > 1:
        log(f"  found    : {chosen['screenId']} - {chosen['name']} "
            f"({len(results)} matches; took the exact one)")
    else:
        log(f"  found    : {chosen['screenId']} - {chosen['name']}")

    target = evaluate(ws, js_result_row_rect(chosen["index"]))
    if not target.get("found"):
        raise RuntimeError(f"The result row could not be clicked "
                           f"({target.get('reason')}).")
    click_element_by_rect(ws, target["x"], target["y"])

    # Wait for the tab to appear in gdsOpenMenu rather than guessing.
    #
    # This used to fall back to "any new winId not in `before`" the moment
    # ANY new tab showed up, on the theory that this run is the only thing
    # that could have opened one. Live-plausible counterexample this project
    # already documents elsewhere: a notice popup, a leftover AD SSO window,
    # or - the same "another run/session sharing this browser" scenario
    # `acquire_run_lock()` exists for - a screen someone else opened at the
    # same moment. Its own comment ("rather than guessing") already said
    # what the fallback then went on to do. Removed: only the winning
    # exact-menuId match is ever returned; anything else means keep polling,
    # never pick the first new thing that happens to exist (HISTORY.md -
    # external review of 1957ba9/cff282b, finding #10).
    deadline = time.time() + timeout
    last_new_count = 0
    while time.time() < deadline:
        rows = open_screens(ws).get("rows", [])
        for r in rows:
            if r.get("menuId", "").upper() == chosen["menuId"].upper():
                # Covers both a new tab and re-activating one already open.
                return r
        # A work-form (R3224WM00) opens NESTED in its shell's tab, and
        # gdsOpenMenu records that tab under the SHELL's menu id (FFM0520),
        # never the catalogue's (FFM0524) - so the match above can never
        # succeed for it. Reproduced live, from a clean state, every time:
        # the screen was on screen and the run reported "did not open within
        # 90s ... may not be permitted" after 110 s (HISTORY.md Phase 84.2).
        # The form's own file name is the exact, code-specific evidence.
        embedded = tab_for_embedded_form(ws, chosen["screenId"], rows)
        if embedded:
            return embedded
        last_new_count = len([r for r in rows if r.get("winId") not in before])
        time.sleep(1.0)

    detail = (f" ({last_new_count} other new tab(s) opened, none matching "
             f"this menu id)" if last_new_count else "")
    raise RuntimeError(
        f"{chosen['screenId']} ({chosen['menuId']}) did not open within "
        f"{timeout}s{detail}. It may not be permitted for this account.")


def print_rows(rows):
    if not rows:
        print("  (no matches)")
        return
    print(f"  {'SCREEN ID':<14} {'MENU ID':<10} {'SYS':<6} MENU NAME")
    print("  " + "-" * 76)
    for r in rows:
        print(f"  {r['screenId']:<14} {r['menuId']:<10} {r['sysCode']:<6} {r['menuTitle']}")
        if r["path"]:
            print(f"  {'':<32} {r['path']}")


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("target", nargs="?", help="screen code or menu name to open")
    parser.add_argument("--find", metavar="TEXT",
                        help="search the screen catalogue without opening anything")
    parser.add_argument("--current", action="store_true",
                        help="show the screens open right now")
    args = parser.parse_args()

    ws = connect_gmes()
    try:
        if args.current:
            info = open_screens(ws)
            print("Screens currently open:\n")
            for r in info.get("rows", []):
                print(f"  {r.get('menuId','?'):<12} {r.get('title','')}")
                print(f"  {'':<12} pageUrl={r.get('pageUrl','')}  window={r.get('winId','')}")
            return 0

        if args.find is not None:
            info = catalogue(ws, args.find)
            if not info.get("found"):
                print("The screen catalogue (gdsMenuList_) is not loaded yet.")
                return 1
            print(f"Catalogue: {info['total']} screens; matches for {args.find!r}:\n")
            print_rows(info["rows"])
            if info.get("truncated"):
                print(f"\n  ({info['matched']} matched; only the first "
                      f"{info['returned']} are shown. Narrow the search to see the rest.)")
            return 0

        if not args.target:
            parser.print_help()
            return 2

        # Confirm the target exists before driving the UI, so a typo produces
        # a useful list instead of a timeout.
        info = catalogue(ws, args.target)
        matches = info.get("rows", []) if info.get("found") else []
        if matches:
            exact = [m for m in matches
                     if m["screenId"].upper() == args.target.upper()]
            chosen = exact[0] if exact else matches[0]
            print(f"Opening {chosen['screenId']} - {chosen['menuTitle']}")
            if chosen["path"]:
                print(f"  path: {chosen['path']}")
            if not exact and len(matches) > 1:
                total_matched = info.get("matched", len(matches))
                print(f"  ({total_matched} matches; using the first. "
                      f"Use --find to see them all.)")
        else:
            print(f"Opening {args.target!r} (not found in the catalogue - "
                  "trying the search box anyway)")

        opened = open_screen(ws, args.target)
        print(f"\nOpen: {opened.get('title','?')}  "
              f"[menuId {opened.get('menuId','?')}]")
        print(f"  window : {opened.get('winId','')}")
        print(f"  pageUrl: {opened.get('pageUrl','')}")
        return 0

    except RuntimeError as e:
        print(f"ERROR: {e}")
        gmes_common.screenshot_on_failure("gmes_open_screen")
        return 1
    finally:
        ws.close()


if __name__ == "__main__":
    sys.exit(main())

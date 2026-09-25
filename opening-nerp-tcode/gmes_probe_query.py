"""
Watch what GMES does while a query runs.

    python gmes_probe_query.py

Clicks the Inquiry button on the open work screen and samples the page twice
a second for 25 seconds, recording what changes: grid row counts, and any
overlay/spinner element that appears and disappears.

The point is to find GMES's "I am busy" signal. Without one, automation has
to guess how long a query takes - and a guessed wait is wrong in both
directions: too short and it reads an empty grid as a real answer, too long
and every nightly run wastes minutes. Clicking Inquiry is a read-only
search, so this is safe to run against the live system.
"""
import sys
import time

import cdp_common
import gmes_common
from cdp_common import evaluate
from gmes_common import click_control, connect_gmes


JS_SNAPSHOT = """
(function() {
    const isVisible = %s;

    // Anything that looks like a spinner, wait cursor, or modal mask.
    const busyWords = /wait|busy|loading|progress|spinner|mask|dimm/i;
    const busy = [];
    for (const el of document.querySelectorAll('*')) {
        const klass = (typeof el.className === 'string') ? el.className : '';
        const id = el.id || '';
        if (!busyWords.test(klass) && !busyWords.test(id)) continue;
        if (!isVisible(el)) continue;
        const r = el.getBoundingClientRect();
        busy.push({id: id.slice(-60), cls: klass.slice(0, 50),
                   w: Math.round(r.width), h: Math.round(r.height)});
    }

    // Nexacro grids render one element per body row.
    let rows = 0, grids = 0;
    for (const el of document.querySelectorAll('[id*="grd"]')) {
        if (/\\.body\\.gridrow_\\d+$/.test(el.id || '')) rows++;
        if (/\\.body$/.test(el.id || '')) grids++;
    }

    // The screen's own total counter, if it has one.
    let totals = [];
    for (const el of document.querySelectorAll('*')) {
        const t = (el.textContent || '').trim();
        if (/^Total\\s*[\\d,]*$/i.test(t) && t.length < 20 && isVisible(el)) totals.push(t);
    }

    return JSON.stringify({
        busyCount: busy.length, busy: busy.slice(0, 4),
        gridRows: rows, grids: grids,
        totals: Array.from(new Set(totals)).slice(0, 4)
    });
})()
""" % cdp_common.JS_IS_VISIBLE


def snapshot(ws):
    return evaluate(ws, JS_SNAPSHOT)


def main():
    ws = connect_gmes()
    try:
        print("Before clicking Inquiry:")
        before = snapshot(ws)
        print(f"  {before}\n")

        # Matched by class, not by id: the screen instance number in the id
        # changes every time the screen is opened.
        btn = click_control(ws, cls="btn_LF_Search_New", text="Inquiry")
        if not btn:
            print("Could not find the Inquiry button - is a work screen open?")
            return 1
        print(f"Clicked Inquiry: id={btn['id']}\n")

        print(f"{'t+sec':>6}  {'busy':>4}  {'rows':>5}  totals")
        print("-" * 60)
        start = time.time()
        seen_busy = False
        settled_at = None
        last_rows = before.get("gridRows", 0)

        while time.time() - start < 25:
            snap = snapshot(ws)
            elapsed = time.time() - start
            if snap["busyCount"]:
                seen_busy = True
            if snap["gridRows"] != last_rows and settled_at is None and elapsed > 1:
                settled_at = elapsed
            last_rows = snap["gridRows"]
            print(f"{elapsed:6.1f}  {snap['busyCount']:>4}  {snap['gridRows']:>5}  "
                  f"{snap['totals']}")
            if snap["busyCount"]:
                print(f"        busy elements: {snap['busy']}")
            time.sleep(0.5)

        print("-" * 60)
        print(f"A busy/overlay element was seen : {seen_busy}")
        print(f"Rows first changed at           : "
              f"{f'{settled_at:.1f}s' if settled_at else 'no change detected'}")
        gmes_common.capture_screenshot("gmes_after_inquiry.png")
        print("Screenshot: gmes_after_inquiry.png")
        return 0
    finally:
        ws.close()


if __name__ == "__main__":
    sys.exit(main())

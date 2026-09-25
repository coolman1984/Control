"""
Inspect the G-MES search suggestion list.

    python gmes_probe_suggest.py P1111UM00

Types a query into the top search box and dumps every visible row in the
suggestion popup, with ids, sizes and positions - so the right element to
click can be identified instead of guessed. The first attempt clicked a row
that carried a chevron and nothing opened, which is what prompted this.
"""
import sys
import time

import cdp_common
from cdp_common import evaluate
import gmes_common
from gmes_common import close_child_popups, connect_gmes
from gmes_open_screen import type_into_search

JS_POPUP_CONTENTS = r"""
(function() {
    const isVisible = %s;
    const rows = [];
    // Located by position, not by id: the popup's element ids turned out not
    // to contain any of the names its handlers use, so an id filter found
    // nothing at all. Everything in the dropdown's band below the search box
    // is listed instead.
    for (const el of document.querySelectorAll('*')) {
        if (!isVisible(el)) continue;
        const r = el.getBoundingClientRect();
        if (r.top < 35 || r.top > 380) continue;
        if (r.height < 8 || r.height > 60) continue;
        if (r.left < 550 || r.left > 1100) continue;
        const t = (el.textContent || '').trim();
        if (!t || t.length > 60) continue;
        const id = el.id || '';
        rows.push({id: id.slice(-70), text: t, w: Math.round(r.width),
                   h: Math.round(r.height), x: Math.round(r.left + r.width/2),
                   y: Math.round(r.top + r.height/2),
                   cls: (typeof el.className === 'string' ? el.className : '').slice(0, 50)});
    }
    rows.sort((a, b) => (a.w * a.h) - (b.w * b.h));
    return JSON.stringify({count: rows.length, rows: rows.slice(0, 40)});
})()
""" % cdp_common.JS_IS_VISIBLE


def main(query):
    ws = connect_gmes()
    try:
        closed = close_child_popups(ws)
        if closed:
            print(f"closed modal popup(s): {closed}")
        type_into_search(ws, query)
        time.sleep(2.5)

        info = evaluate(ws, JS_POPUP_CONTENTS)
        print(f"\nVisible elements in the search popup: {info['count']}\n")
        for r in info["rows"]:
            print(f"  {r['w']:>4}x{r['h']:<4} ({r['x']},{r['y']})  {r['text']!r}")
            print(f"       id={r['id']!r}")
            if r["cls"]:
                print(f"       class={r['cls']!r}")
        gmes_common.capture_screenshot("gmes_suggest.png")
        print("\nScreenshot: gmes_suggest.png")
        return 0
    finally:
        ws.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))

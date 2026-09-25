"""
Map the parts of the G-MES shell that surround every report: the top module
bar and the whole left panel.

    python gmes_probe_shell.py            # against whatever screen is open

The report runner currently understands only the Division checkbox in the
Org tree. The left panel carries several more dimensions that change what a
query returns - the Org/Prod/Fac/Proc tabs, the STD/PLANT attribute, the
"Including Past Org." flag, the Quick View variant, and whether the period
means Plan Date or Create Date. Any of those set wrongly gives a plausible
but incorrect answer, so they have to be discovered rather than assumed.

Read-only.
"""
import sys

import cdp_common
from cdp_common import evaluate
from gmes_common import connect_gmes

JS_SHELL = r"""
(function() {
    const isVisible = %s;

    function boxOf(el) {
        const r = el.getBoundingClientRect();
        return {x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2),
                w: Math.round(r.width), h: Math.round(r.height)};
    }
    function scan(pattern, opts) {
        opts = opts || {};
        const out = [];
        const seen = {};
        for (const el of document.querySelectorAll('div, span, a')) {
            const id = el.id || '';
            if (!pattern.test(id)) continue;
            if (/:icontext$|:text$/.test(id)) continue;      // inner text node twin
            if (!isVisible(el)) continue;
            const t = (el.textContent || '').trim();
            if (opts.needText && !t) continue;
            if (t.length > 40) continue;
            const cls = (typeof el.className === 'string') ? el.className : '';
            const key = id;
            if (seen[key]) continue;
            seen[key] = true;
            const b = boxOf(el);
            out.push({id: id.split('.').slice(-3).join('.'), text: t,
                      cls: cls.slice(0, 46), x: b.x, y: b.y, w: b.w, h: b.h});
        }
        return out;
    }

    return JSON.stringify({
        // Top module bar: MDE / PPM / MQM / FFM / ALM / AQM / MRM / WP ...
        topMenu: scan(/topMenuFrame\.form\.[^.]*$/, {needText: true}),
        // Left panel of the open work screen.
        categoryTabs: scan(/divCategory\.form\.divTabBtnArea\.form\.[^.]*$/, {needText: true}),
        categoryBody: scan(/divCategory\.form\.divCategory\.form\.tabDiv_\w+\.form\.[^.]*$/, {}),
        leftPanel:   scan(/winPPM\w*\.form\.divLeft\.form\.[^.]*$/, {}),
        leftSub:     scan(/divLeftSub\.form\.[^.]*$/, {}),
        quickView:   scan(/divQuick\w*\.form\.[^.]*$/, {}),
        filterPanel: scan(/divWidgetFilter\w*\.form\.[^.]*$/, {})
    });
})()
""" % cdp_common.JS_IS_VISIBLE


def dump(title, rows, limit=40):
    print(f"\n=== {title} ({len(rows)}) ===")
    for r in rows[:limit]:
        label = (r["text"] or "-")[:28]
        print(f"  {label:<30} {r['w']:>4}x{r['h']:<4} ({r['x']:>4},{r['y']:>4})  "
              f"{r['id']}")
        if r["cls"]:
            print(f"  {'':<30} class={r['cls']}")


def main():
    ws = connect_gmes()
    try:
        info = evaluate(ws, JS_SHELL)
        for key, title in (
            ("topMenu", "TOP MODULE BAR"),
            ("categoryTabs", "LEFT PANEL - category tabs (Org / Prod / Fac / Proc)"),
            ("categoryBody", "LEFT PANEL - inside the active category tab"),
            ("leftPanel", "LEFT PANEL - outer"),
            ("leftSub", "LEFT PANEL - sections"),
            ("quickView", "QUICK VIEW"),
            ("filterPanel", "FILTER PANEL controls"),
        ):
            dump(title, info.get(key, []))
        return 0
    finally:
        ws.close()


if __name__ == "__main__":
    sys.exit(main())

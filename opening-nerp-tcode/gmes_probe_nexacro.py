"""
Probe the Nexacro object model behind GMES - the data layer, not the pixels.

    python gmes_probe_nexacro.py                 # everything
    python gmes_probe_nexacro.py PPM0222         # only paths containing this
    python gmes_probe_nexacro.py --rows          # only datasets that have rows

Why this matters more than clicking around: a Nexacro grid only creates DOM
elements for the rows you can SEE. Scrape the page and a 5,000-row result
silently becomes the 20 rows on screen - the automation reports success and
the data is wrong, which is the worst failure mode for a nightly job.

The rows really live in Nexacro Datasets, in JavaScript. Reaching those lets
us read every row exactly, know when a query has finished, and skip the
Excel round-trip entirely.

Two things had to be learned the hard way here, and both are why a naive
walk reports an empty application:

  * Child frames are in `_frames`, a Nexacro ObjectArray indexed numerically.
    There is no `frames` property to walk.
  * A work screen is NOT a frame. GMES loads it into nested Div components,
    each of which carries its own `.form` - so the grid on PPM0222 lives at
    winPPM0219_x.form.divWorkMain.form.divWork.form...divWidgetMainPPM0222.form.
    Stopping at frames finds only the empty WorkMain shell.

This only reads. It changes nothing.
"""
import sys

import cdp_common
from cdp_common import evaluate
from gmes_common import connect_gmes


JS_PROBE = r"""
(function() {
    const out = {available: typeof nexacro !== 'undefined'};
    if (!out.available) return JSON.stringify(out);

    const app = (typeof nexacro.getApplication === 'function')
                    ? nexacro.getApplication() : null;
    out.hasApplication = !!app;
    if (!app) return JSON.stringify(out);

    function typeName(o) {
        try { return (o && (o._type_name || (o.constructor && o.constructor.name))) || '?'; }
        catch (e) { return '?'; }
    }

    function datasetsOf(form) {
        const found = [];
        if (!form) return found;
        let keys = [];
        try { keys = Object.keys(form); } catch (e) { return found; }
        for (const key of keys) {
            let obj;
            try { obj = form[key]; } catch (e) { continue; }
            if (!obj || typeName(obj) !== 'Dataset') continue;
            let rows = -1, cols = [];
            try { rows = obj.getRowCount(); } catch (e) {}
            try {
                const n = obj.getColCount();
                for (let i = 0; i < Math.min(n, 40); i++) cols.push(obj.getColID(i));
            } catch (e) {}
            found.push({name: key, rows: rows, colCount: cols.length, cols: cols});
        }
        return found;
    }

    const entries = [];

    // A form's own datasets, then any Div components that carry a nested form.
    function walkForm(form, path, depth) {
        if (!form || depth > 14 || entries.length > 400) return;
        const ds = datasetsOf(form);
        const url = form.url || form._url || '';
        if (ds.length || url) {
            entries.push({path: path, screen: url, datasets: ds});
        }
        let comps = null;
        try { comps = form.components; } catch (e) {}
        if (!comps || comps.length === undefined) return;
        for (let i = 0; i < comps.length; i++) {
            let c = null;
            try { c = comps[i]; } catch (e) { continue; }
            if (!c) continue;
            let inner = null;
            try { inner = c.form; } catch (e) {}
            if (inner) walkForm(inner, path + '.' + (c.name || ('c' + i)), depth + 1);
        }
    }

    function walkFrame(node, path, depth) {
        if (!node || depth > 10 || entries.length > 400) return;
        const here = path ? path + '.' + (node.name || '?') : (node.name || 'app');
        let form = null;
        try { form = node.form || null; } catch (e) {}
        if (form) walkForm(form, here, depth);

        let kids = [];
        try {
            const fr = node._frames || node.frames;
            if (fr && fr.length !== undefined) {
                for (let i = 0; i < fr.length; i++) kids.push(fr[i]);
            }
        } catch (e) {}
        try { if (node.mainframe) kids.push(node.mainframe); } catch (e) {}
        kids.forEach(k => walkFrame(k, here, depth + 1));
    }
    walkFrame(app, '', 0);

    out.entries = entries;
    return JSON.stringify(out);
})()
"""


def probe(ws):
    return evaluate(ws, JS_PROBE)


def main(filter_text=None, rows_only=False):
    ws = connect_gmes()
    try:
        info = probe(ws)
        if not info.get("available"):
            print("The page is not a Nexacro app - is GMES actually open?")
            return 1

        entries = info.get("entries", [])
        if filter_text:
            needle = filter_text.lower()
            entries = [e for e in entries
                       if needle in e["path"].lower() or needle in (e["screen"] or "").lower()
                       or any(needle in d["name"].lower() for d in e["datasets"])]

        print(f"Forms found: {len(entries)}\n")
        for e in entries:
            datasets = e["datasets"]
            if rows_only:
                datasets = [d for d in datasets if d["rows"] > 0]
                if not datasets:
                    continue
            # Show the tail of the path; the shell prefix is the same everywhere.
            short = e["path"].replace("app.mainframe.vFrameSet1.vFrameSet2.", "")
            print(f"  {short}")
            if e["screen"]:
                print(f"      screen: {e['screen'].rsplit('/', 1)[-1]}")
            for d in datasets:
                cols = ", ".join(d["cols"][:10])
                more = f" +{d['colCount'] - 10} more" if d["colCount"] > 10 else ""
                print(f"      {d['name']:<28} rows={d['rows']:<6} cols={d['colCount']}")
                if d["cols"]:
                    print(f"          [{cols}{more}]")
            print()
        return 0
    finally:
        ws.close()


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    sys.exit(main(args[0] if args else None, rows_only="--rows" in sys.argv))

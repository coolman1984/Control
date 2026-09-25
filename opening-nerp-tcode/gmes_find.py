"""
Search the GMES page - including every inner frame - for elements whose id
or visible text contains something.

    python gmes_find.py S9502            # find the Notice popup
    python gmes_find.py Close --any      # match id OR text, anywhere

Nexacro renders popups and work screens into nested frames, so a search that
only looks at the top document quietly misses them. This walks the frames
too, which is why it finds things gmes_inspect.py does not.
"""
import json
import sys

import cdp_common
from cdp_common import evaluate
from gmes_common import connect_gmes


def js_search(needle, limit=60):
    return """
    (function() {
        const isVisible = %s;
        const needle = %s.toLowerCase();
        const out = [];

        function scan(doc, framePath) {
            let els;
            try { els = Array.from(doc.querySelectorAll('*')); }
            catch (e) { return; }            // cross-origin frame

            for (const el of els) {
                const id = el.id || '';
                const txt = (el.textContent || '').trim();
                const title = el.getAttribute('title') || '';
                const cls = (typeof el.className === 'string') ? el.className : '';
                const hit = id.toLowerCase().includes(needle) ||
                            title.toLowerCase().includes(needle) ||
                            cls.toLowerCase().includes(needle) ||
                            (txt.length <= 40 && txt.toLowerCase().includes(needle));
                if (!hit) continue;
                const r = el.getBoundingClientRect();
                out.push({
                    frame: framePath, tag: el.tagName, id: id,
                    cls: cls.slice(0, 60), title: title,
                    text: txt.slice(0, 40),
                    visible: isVisible(el),
                    x: Math.round(r.left + r.width / 2),
                    y: Math.round(r.top + r.height / 2),
                    w: Math.round(r.width), h: Math.round(r.height)
                });
                if (out.length >= %d) return;
            }

            let frames;
            try { frames = Array.from(doc.querySelectorAll('iframe, frame')); }
            catch (e) { return; }
            frames.forEach((f, i) => {
                let inner = null;
                try { inner = f.contentDocument; } catch (e) { inner = null; }
                if (inner) scan(inner, framePath + '/' + (f.id || f.name || ('frame' + i)));
            });
        }

        scan(document, '');
        return JSON.stringify({count: out.length, hits: out});
    })()
    """ % (cdp_common.JS_IS_VISIBLE, json.dumps(needle), limit)


def search(ws, needle, limit=60):
    return evaluate(ws, js_search(needle, limit))


def main(needle):
    ws = connect_gmes()
    try:
        result = search(ws, needle)
        print(f"{result['count']} match(es) for {needle!r}:\n")
        for h in result["hits"]:
            vis = "visible" if h["visible"] else "hidden "
            print(f"  [{vis}] <{h['tag']}> {h['w']}x{h['h']} at ({h['x']},{h['y']})")
            print(f"           id    : {h['id']!r}")
            if h["text"]:
                print(f"           text  : {h['text']!r}")
            if h["title"]:
                print(f"           title : {h['title']!r}")
            if h["cls"]:
                print(f"           class : {h['cls']!r}")
            if h["frame"]:
                print(f"           frame : {h['frame']!r}")
            print()
    finally:
        ws.close()
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))

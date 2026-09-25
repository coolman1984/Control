"""
Dump the shape of any JavaScript object inside the GMES page.

    python gmes_dump.py "nexacro.getApplication()"
    python gmes_dump.py "nexacro.getApplication().mainframe"
    python gmes_dump.py "nexacro.getApplication().mainframe.frames"

A generic explorer: it prints an object's properties, their types, and a
short preview of each value, plus which of them look callable. Used to work
out an unfamiliar framework's object model without guessing at names.

Read-only - it lists and previews, it never calls anything.
"""
import sys

import cdp_common
from cdp_common import evaluate
from gmes_common import connect_gmes


def js_dump(expression, limit=80):
    return r"""
    (function() {
        let target;
        try { target = (%s); }
        catch (e) { return JSON.stringify({error: 'could not evaluate: ' + e.message}); }
        if (target === null || target === undefined)
            return JSON.stringify({error: 'expression is ' + String(target)});

        function typeName(o) {
            try {
                if (o === null) return 'null';
                if (Array.isArray(o)) return 'array[' + o.length + ']';
                const t = typeof o;
                if (t !== 'object') return t;
                return (o._type_name) || (o.constructor && o.constructor.name) || 'object';
            } catch (e) { return '?'; }
        }
        function preview(o) {
            try {
                const t = typeof o;
                if (o === null || t === 'undefined') return String(o);
                if (t === 'string') return JSON.stringify(o.slice(0, 60));
                if (t === 'number' || t === 'boolean') return String(o);
                if (t === 'function') return '(function)';
                if (o.name !== undefined && typeof o.name === 'string')
                    return 'name=' + JSON.stringify(o.name.slice(0, 40));
                if (o.length !== undefined) return 'length=' + o.length;
                return '';
            } catch (e) { return '?'; }
        }

        const props = [];
        const seen = new Set();
        let node = target;
        for (let depth = 0; node && depth < 3; depth++) {
            let keys = [];
            try { keys = Object.getOwnPropertyNames(node); } catch (e) {}
            for (const k of keys) {
                if (seen.has(k) || k.startsWith('__')) continue;
                seen.add(k);
                let v;
                try { v = node[k]; } catch (e) { continue; }
                props.push({key: k, type: typeName(v), preview: preview(v),
                            callable: typeof v === 'function', depth: depth});
                if (props.length >= %d) break;
            }
            if (props.length >= %d) break;
            try { node = Object.getPrototypeOf(node); } catch (e) { break; }
        }

        return JSON.stringify({
            type: typeName(target),
            selfName: (target.name !== undefined ? String(target.name) : ''),
            count: props.length,
            props: props
        });
    })()
    """ % (expression, limit, limit)


def main(expression):
    ws = connect_gmes()
    try:
        info = evaluate(ws, js_dump(expression))
        if info.get("error"):
            print(f"ERROR: {info['error']}")
            return 1

        print(f"{expression}")
        print(f"  type : {info['type']}")
        if info.get("selfName"):
            print(f"  name : {info['selfName']!r}")
        print()

        data = [p for p in info["props"] if not p["callable"]]
        funcs = [p for p in info["props"] if p["callable"]]

        print(f"Properties ({len(data)}):")
        for p in data:
            print(f"  {p['key']:<28} {p['type']:<22} {p['preview']}")
        if funcs:
            names = ", ".join(f["key"] for f in funcs[:40])
            print(f"\nMethods ({len(funcs)}): {names}")
        return 0
    finally:
        ws.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))

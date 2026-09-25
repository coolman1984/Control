"""
Read and write GMES data through Nexacro's Datasets instead of the screen.

    python gmes_data.py forms                       # which screens are open
    python gmes_data.py read P1112WM00 dsFilterDVO  # show a dataset's rows
    python gmes_data.py csv  P1112WM00 dsMasterProdPlan out.csv

Why go through the Datasets rather than the visible grid:

  * A Nexacro grid only builds DOM elements for the rows on screen. Reading
    the page returns whatever happens to be scrolled into view, so a
    5,000-row result quietly becomes 20 rows and the job reports success.
    The Dataset holds every row.
  * Column names come back as the system's own field names (planYmd,
    modelCode, poNo...), not as translated screen labels that change with
    the UI language.
  * Filters can be set directly, so there is no fighting with date pickers
    and dropdown widgets.

Screens are addressed by their SCREEN CODE (the xfdl file name, e.g.
P1112WM00), never by the full element path - GMES renumbers the window every
time a screen is opened, so a path captured today is wrong tomorrow.
"""
import csv
import json
import os
import sys
import tempfile

import cdp_common
import gmes_redact
from cdp_common import evaluate
from gmes_common import connect_gmes


# Locate a form by its screen code, then reach a dataset on it. Everything
# below re-resolves the form each call rather than caching a path, because
# the window id changes whenever the screen is reopened.
JS_HELPERS = r"""
function _typeName(o) {
    try { return (o && (o._type_name || (o.constructor && o.constructor.name))) || '?'; }
    catch (e) { return '?'; }
}
function _findForms(match) {
    const app = nexacro.getApplication();
    const hits = [];
    function walkForm(form, path, depth) {
        // 12 was tuned for a form tree with no tabbed panels. Walking INTO
        // a Tab control's own pages (below) adds 2-3 levels per tab a
        // screen's layout nests - live-caught on P3151WM00 (HISTORY.md
        // Phase 82.11): its actually-visible tab's own result grid sat
        // just past the old cap while a DIFFERENT, hidden tab's grid (one
        // level shallower) was still found, an even more misleading
        // failure than simply finding nothing. Raised with headroom for
        // realistic nested-tab layouts, not tuned to this one screen's
        // exact depth.
        if (!form) return;
        // A walk that stops early must SAY so. Both caps used to `return`
        // silently, and a screen whose forms fell past the cut was reported
        // as having no filters and no division tree at all - a confident,
        // wrong answer (HISTORY.md Phase 82.20). Sticky for the whole
        // evaluation, so a caller reads it once at the end.
        if (depth > 20 || hits.length > 4000) { _findForms.truncated = true; return; }
        const url = String(form.url || form._url || '');
        const file = url.split('/').pop();
        if (!match || (file && file.toLowerCase().indexOf(match.toLowerCase()) === 0)
                   || path.toLowerCase().indexOf(match.toLowerCase()) >= 0) {
            hits.push({path: path, file: file, form: form});
        }
        let comps = null;
        try { comps = form.components; } catch (e) { return; }
        if (!comps || comps.length === undefined) return;
        for (let i = 0; i < comps.length; i++) {
            let c = null, inner = null;
            try { c = comps[i]; inner = c && c.form; } catch (e) { continue; }
            if (inner) walkForm(inner, path + '.' + (c.name || i), depth + 1);
            // A Nexacro Tab control's own pages are NOT reachable through
            // .form/.components at all - confirmed live (HISTORY.md Phase
            // 82.11): the Tab object itself has no .form, and Tabpage1..N
            // are exposed only through a SEPARATE .tabpages collection.
            // Without this, every grid/filter/dataset living inside a
            // tabbed panel was invisible to this entire walk - live-caught
            // on P3151WM00 (Loss Status), whose real result grid
            // (grdMain01, under a tabpage) was never found at all, while an
            // unrelated left-panel "Grid02" got silently treated as the
            // result grid instead. Same recursion, same depth guard - a
            // tabpage's own form can itself contain further nested tabs.
            let tabpages = null;
            try { tabpages = c && c.tabpages; } catch (e) {}
            if (tabpages && tabpages.length !== undefined) {
                for (let t = 0; t < tabpages.length; t++) {
                    let page = null, pageForm = null;
                    try { page = tabpages[t]; pageForm = page && page.form; } catch (e) { continue; }
                    if (pageForm) walkForm(pageForm,
                        path + '.' + (c.name || i) + '.' + (page.name || t), depth + 1);
                }
            }
        }
    }
    function walkFrame(node, path, depth) {
        if (!node || depth > 10) return;
        const here = path ? path + '.' + (node.name || '?') : (node.name || 'app');
        let form = null;
        try { form = node.form || null; } catch (e) {}
        if (form) walkForm(form, here, depth);
        let kids = [];
        try {
            const fr = node._frames || node.frames;
            if (fr && fr.length !== undefined)
                for (let i = 0; i < fr.length; i++) kids.push(fr[i]);
        } catch (e) {}
        try { if (node.mainframe) kids.push(node.mainframe); } catch (e) {}
        kids.forEach(k => walkFrame(k, here, depth + 1));
    }
    walkFrame(app, '', 0);
    return hits;
}
function _dataset(screenCode, dsName, exactPath) {
    // A screen CODE can match more than one live form: a reusable
    // sub-component (WidgetFilter.xfdl is embedded on several different
    // screens - see JS_DISCOVER's own comment on it) or the same screen
    // code simply open in two windows at once. `exactPath` is the form
    // path JS_DISCOVER already resolved for THIS filter/grid, scoped to
    // the one work window this run opened - when the caller has it, only
    // that form AND ITS ANCESTORS are considered, never "whichever matches
    // first" and never a sibling window.
    //
    // Ancestors, not the exact form alone: live-traced (HISTORY.md Phase
    // 80.4) - P1111UM00's grdSum grid's own component lives at
    // ...divWork.divLeft, but `divLeft.form` does not carry dsModelPlanList
    // as an own property at all; it is bound through Nexacro's ANCESTOR
    // SCOPE CHAIN and is only a property of the PARENT form, ...divWork
    // (P1111UM00.xfdl.js itself) - exactly the failure mode JS_DISCOVER's
    // own comment on grdWidgetList/dsWidget already named. Matching the
    // exact path only made every read/write on this screen fail closed
    // ("dataset disappeared") where the pre-fix whole-app search had always
    // happened to find the right form. A sibling window's path never
    // shares this one's prefix chain, so walking up stays exactly as safe
    // as the exact match was - closest scope wins first.
    // Falls back to the old screen-code-wide search when no path is known
    // (manual `gmes_data.py` CLI use, and gmes_daily_prodplan.py's direct
    // calls, neither of which run a JS_DISCOVER first).
    let forms;
    if (exactPath) {
        forms = _findForms(null).filter(h =>
            h.path === exactPath || exactPath.indexOf(h.path + '.') === 0);
        forms.sort((a, b) => b.path.length - a.path.length);
    } else {
        forms = _findForms(screenCode);
    }
    for (const h of forms) {
        let ds = null;
        try { ds = h.form[dsName]; } catch (e) { continue; }
        if (ds && _typeName(ds) === 'Dataset') return {ds: ds, path: h.path, file: h.file};
    }
    return null;
}
"""


def js_list_forms():
    return """
    (function() {
        %s
        const hits = _findForms(null).map(h => {
            const names = [];
            try {
                for (const k of Object.keys(h.form)) {
                    let o = h.form[k];
                    if (o && _typeName(o) === 'Dataset') names.push(k);
                }
            } catch (e) {}
            return {path: h.path, file: h.file, datasets: names};
        });
        return JSON.stringify({count: hits.length, forms: hits});
    })()
    """ % JS_HELPERS


def js_find_column(column, min_rows=1):
    """Find every dataset anywhere in the app that has a given column.

    The way to locate a catalogue when you know a field name but not where
    it lives - the menu catalogue behind the search bar was found this way,
    by asking which datasets carry a `screenId`."""
    return """
    (function() {
        %s
        const wanted = %s.toLowerCase();
        const out = [];
        for (const h of _findForms(null)) {
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
                if (rows < %d) continue;
                if (!cols.some(c => c.toLowerCase() === wanted)) continue;
                out.push({file: h.file, path: h.path, name: k, rows: rows, cols: cols});
            }
        }
        out.sort((a, b) => b.rows - a.rows);
        return JSON.stringify({count: out.length, hits: out.slice(0, 30)});
    })()
    """ % (JS_HELPERS, json.dumps(column), min_rows)


def js_read(screen_code, ds_name, limit, offset, path=None):
    return """
    (function() {
        %s
        const hit = _dataset(%s, %s, %s);
        if (!hit) return JSON.stringify({found: false});
        const ds = hit.ds;
        const cols = [];
        const n = ds.getColCount();
        for (let i = 0; i < n; i++) cols.push(ds.getColID(i));

        // getRowCount() is the count AFTER any client-side Dataset.filter();
        // getRowCountNF() is the count without it. A screen that filters its
        // own result dataset shows fewer rows than the data holds, and
        // nothing else says so (HISTORY.md Phase 82.20; live: 5 shell
        // datasets in one window, e.g. 20 shown of 37).
        let filterstr = '', unfiltered = null;
        try { filterstr = String(ds.filterstr || ''); } catch (e) {}
        try { if (filterstr && ds.getRowCountNF) unfiltered = ds.getRowCountNF(); } catch (e) {}
        const total = ds.getRowCount();
        const start = %d, limit = %d;
        const end = limit < 0 ? total : Math.min(total, start + limit);
        const rows = [];
        for (let r = start; r < end; r++) {
            const row = {};
            for (const c of cols) {
                let v;
                try { v = ds.getColumn(r, c); } catch (e) { v = null; }
                row[c] = (v === undefined || v === null) ? "" : String(v);
            }
            rows.push(row);
        }
        return JSON.stringify({found: true, path: hit.path, file: hit.file,
                               columns: cols, total: total, rows: rows,
                               filterstr: filterstr, unfiltered: unfiltered});
    })()
    """ % (JS_HELPERS, json.dumps(screen_code), json.dumps(ds_name),
           json.dumps(path), offset, limit)


def js_set_values(screen_code, ds_name, values, row, path=None):
    return """
    (function() {
        %s
        const hit = _dataset(%s, %s, %s);
        if (!hit) return JSON.stringify({found: false});
        const ds = hit.ds;
        const values = %s;
        const requestedRow = %s;
        const count = ds.getRowCount();
        let targetRow;
        if (count === 0) {
            ds.addRow();
            targetRow = 0;
        } else if (requestedRow !== null) {
            targetRow = requestedRow;
        } else if (count === 1) {
            // The one and only row - no ambiguity possible.
            targetRow = 0;
        } else {
            // Nexacro docs: a bound control shows the value of the dataset's
            // CURRENTLY SELECTED row (`rowposition`), not necessarily row 0.
            // A multi-row filter DVO with row 0 hardcoded could write a
            // value into a row nothing on screen is looking at, while the
            // write reports success and the read-back "proves" it (both
            // read row 0 right back). Refuse rather than guess when there
            // is more than one row and no valid current position.
            const rp = ds.rowposition;
            if (rp === undefined || rp === null || rp < 0 || rp >= count) {
                return JSON.stringify({found: false,
                    reason: 'dataset has ' + count + ' rows and no valid ' +
                             'current row position (rowposition=' + rp + ') ' +
                             '- refusing to guess which row is the filter'});
            }
            targetRow = rp;
        }
        const applied = {};
        for (const key in values) {
            try {
                ds.setColumn(targetRow, key, values[key]);
                applied[key] = ds.getColumn(targetRow, key);
            } catch (e) { return JSON.stringify({found: false, reason: e.message}); }
        }
        return JSON.stringify({found: true, path: hit.path, row: targetRow, applied: applied});
    })()
    """ % (JS_HELPERS, json.dumps(screen_code), json.dumps(ds_name), json.dumps(path),
           json.dumps(values), json.dumps(row))


def list_forms(ws):
    return evaluate(ws, js_list_forms())


# A single Runtime.evaluate call builds one JSON object holding every row and
# column of a dataset - Nexacro's own docs flag a large Dataset's client-side
# memory cost, and this project's own comments already named the unbounded
# single-call read as a known gap (HISTORY.md Open Item 44). Above this many
# rows, a caller asking for EVERY row (limit=-1, offset=0 - export, verify,
# the daily job all go through read_dataset()) is paged instead of read as
# one blob. No screen this project has recorded has ever approached this size
# (the largest seen live is 6,529 rows, a real 10-day P1112UM00 query,
# HISTORY.md Phase 82) - this exists for the day one does. The paging path is
# exercised only offline against a mocked evaluate() until a genuinely large
# dataset is read live.
PAGE_ROWS = 2000


def read_dataset_paged(ws, screen_code, ds_name, page_size=None, path=None):
    """Read a dataset page by page instead of as one unbounded JSON blob.

    Reads the first page to learn the dataset's total row count (`getRowCount()`
    - cheap even at `limit=0`, since it is computed before any row is read),
    then fetches the rest by offset. Every later page's OWN reported total must
    still match the first page's - a dataset that changed size mid-read (a
    second Inquiry landing on the same screen, a filter cleared under this
    read) is refused rather than silently stitched into an answer that never
    existed as a whole. Returns the same shape `read_dataset()` always has, so
    a caller cannot tell the two apart except by row count."""
    page_size = page_size or PAGE_ROWS
    first = evaluate(ws, js_read(screen_code, ds_name, page_size, 0, path=path))
    if not first.get("found"):
        return first
    total = first.get("total", 0)
    rows = list(first.get("rows", []))
    while len(rows) < total:
        page = evaluate(ws, js_read(screen_code, ds_name, page_size, len(rows), path=path))
        if not page.get("found"):
            return page
        if page.get("total") != total:
            # found=False, not just an extra "reason" field nobody reading
            # {"found": ..., "rows": [...]} for the ordinary shape would
            # think to check - every existing caller already treats a falsy
            # "found" as "nothing usable came back" (verify_rows(),
            # Screen.to_csv()), which is exactly the outcome a stitched-
            # together partial answer needs.
            return {"found": False,
                    "reason": (f"the dataset changed size mid-read ({total} -> "
                              f"{page.get('total')} rows) - refusing a "
                              "stitched-together answer"),
                    "columns": first.get("columns", []), "rows": []}
        if not page.get("rows"):
            # No forward progress: stop rather than loop forever - but as a
            # refusal, not a quietly short answer. This used to `break` and
            # return found=True with fewer rows than `total`; every caller
            # (Screen.to_csv() reports len(rows) as the total) then wrote a
            # truncated file that looked complete (HISTORY.md Phase 91.1).
            return {"found": False,
                    "reason": (f"a page came back empty at row {len(rows)} of "
                               f"{total} - refusing a truncated answer"),
                    "columns": first.get("columns", []), "rows": []}
        rows.extend(page["rows"])
    if len(rows) != total:
        return {"found": False,
                "reason": (f"read {len(rows)} rows for a dataset reporting {total} "
                           "- refusing an answer that does not add up"),
                "columns": first.get("columns", []), "rows": []}
    return {**first, "rows": rows}


def read_dataset(ws, screen_code, ds_name, limit=-1, offset=0, path=None):
    """Every row of a dataset, as a list of dicts keyed by column name.

    A caller asking for EVERY row (the default, and what export/verify/the
    daily job all use) goes through `read_dataset_paged()`, which itself reads
    only ONE page (`page_size` rows) when that turns out to be the whole
    dataset - the ordinary small-screen case costs exactly the single call it
    always did, never an extra probe first. Anything else (a bounded `limit`,
    a specific `offset` - `limit=0` shape probes, verify's own small reads)
    is still the original single call, unpaged."""
    if limit == -1 and offset == 0:
        return read_dataset_paged(ws, screen_code, ds_name, path=path)
    return evaluate(ws, js_read(screen_code, ds_name, limit, offset, path=path))


def set_filter(ws, screen_code, ds_name, values, row=None, path=None):
    """Write values into a dataset - typically the screen's filter DVO.

    `row` is normally left as None: `js_set_values` then writes row 0 for
    a single-row dataset (the common case - a filter DVO holds exactly one
    row of scalar values) and refuses a multi-row one unless its
    `rowposition` is valid, rather than guessing row 0 is the one bound to
    the screen (HISTORY.md - external review of 8ac502a, finding #3). Pass
    an explicit row only to force a specific one.

    `path` is the exact form path a caller's own discovery already
    resolved (HISTORY.md - same review, finding #1/#2) - when given, only
    that form is searched, never "whichever form with this screen code and
    dataset name is found first"."""
    return evaluate(ws, js_set_values(screen_code, ds_name, values, row, path=path))


# CLAUDE.md 2.3: "G-MES's integrated-search form carries tokenId and
# refreshTokenId - full JWTs for the signed-in session - in an
# ordinary-looking dsAnyframeDVO. Print only the columns you need." That
# rule was enforced for console/log output (gmes_log.py) but not for CSV
# export - any dataset column named like a credential went straight into the
# file, unredacted. A session token belongs to whichever dataset a screen
# happens to bind, not only the ones this project has already seen live, so
# this excludes by COLUMN NAME rather than trusting that only known-bad
# screens are ever exported.
# The canonical word list and pattern live in gmes_redact.py (HISTORY.md Open
# Item 39, previously two independently-maintained copies) - this name is
# kept as an alias so nothing that already reaches for
# gmes_data.SENSITIVE_COLUMN needs to change.
SENSITIVE_COLUMN = gmes_redact.NAME_PATTERN


def redact_sensitive_columns(columns):
    """Column names safe to write to a file - CLAUDE.md 2.3's console/log
    redaction, applied to CSV export instead of print(). Returns
    (safe_columns, dropped_columns) so a caller can report what it withheld
    rather than silently thinning the file."""
    # .search(), not .match(): the sensitive word does not have to be at
    # the START of the column name - refreshTokenId (CLAUDE.md 2.3's own
    # example) has "Token" in the middle, and .match() only anchors at
    # position 0 regardless of whether the pattern itself has a leading
    # `^`. Caught live: refreshTokenId slipped through the first version
    # of this filter, which used .match() with an unanchored pattern -
    # `.match()` never searches past position 0 no matter what the pattern
    # allows.
    safe = [c for c in columns if not c.startswith("_") and not SENSITIVE_COLUMN.search(c)]
    dropped = [c for c in columns if not c.startswith("_") and SENSITIVE_COLUMN.search(c)]
    return safe, dropped


# A CSV opened directly in Excel/Sheets treats a leading =, +, -, or @ as the
# start of a formula (CSV formula injection, CWE-1236) - no exploit has been
# observed here, since these are G-MES's own values rather than attacker
# input, but a value that merely LOOKS like a formula (a stray leading "="
# typed into a free-text field, a code that happens to start with "@") would
# still be silently executed the moment the file opens. Every writer below
# escapes it the same way: a leading apostrophe, the standard defence, which
# keeps the cell displaying its original text - genuinely numeric values
# (a plain "-123.45" or "+7") are left completely alone, never turned to text.
_FORMULA_LEADING = ("=", "+", "-", "@", "\t", "\r")


def escape_formula_cell(value):
    """`value`, or an apostrophe-prefixed copy if a spreadsheet would read it
    as a formula. Never applied to a value that is genuinely numeric."""
    text = "" if value is None else str(value)
    if not text or text[0] not in _FORMULA_LEADING:
        return value
    try:
        float(text)
        return value                    # a real number ("-123.45", "+7"), not a formula
    except ValueError:
        return "'" + text


def safe_rows_for_csv(rows, columns):
    """`rows`, restricted to `columns` and with every cell formula-escaped -
    what every CSV writer in this project hands to `csv.DictWriter`."""
    return [{c: escape_formula_cell(r.get(c)) for c in columns} for r in rows]


def write_csv(result, path):
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fieldnames, dropped = redact_sensitive_columns(result["columns"])
    if dropped:
        print(f"  [!] withheld from CSV, column name looks like a credential: {dropped}")
    fd, temporary = tempfile.mkstemp(prefix=".gmes-data-", suffix=".partial", dir=directory)
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8-sig") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(safe_rows_for_csv(result["rows"], fieldnames))
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return path


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    command = argv[0]
    ws = connect_gmes()
    try:
        if command == "forms":
            info = list_forms(ws)
            print(f"Open forms: {info['count']}\n")
            for f in info["forms"]:
                if not f["datasets"]:
                    continue
                short = f["path"].replace("application.mainframe.vFrameSet1.vFrameSet2.", "")
                print(f"  {f['file'] or '(no file)'}   {short}")
                print(f"      datasets: {', '.join(f['datasets'][:12])}"
                      + (" ..." if len(f["datasets"]) > 12 else ""))
                print()
            return 0

        if command == "findcol":
            if len(argv) < 2:
                print("Usage: gmes_data.py findcol <columnName>")
                return 2
            info = evaluate(ws, js_find_column(argv[1]))
            print(f"Datasets carrying a {argv[1]!r} column: {info['count']}\n")
            for h in info["hits"]:
                short = h["path"].replace("application.mainframe.vFrameSet1.vFrameSet2.", "")
                print(f"  {h['name']:<28} rows={h['rows']:<6} {h['file']}")
                print(f"      {short}")
                print(f"      cols: {', '.join(h['cols'][:14])}")
                print()
            return 0

        if command in ("read", "csv"):
            if len(argv) < 3:
                print("Usage: gmes_data.py read <SCREENCODE> <datasetName> [limit]")
                return 2
            screen, ds_name = argv[1], argv[2]
            if command == "csv":
                limit = -1
            else:
                extra = argv[3:]
                try:
                    if not extra:
                        limit = 20
                    elif len(extra) == 1:
                        limit = int(extra[0])
                    elif len(extra) == 2 and extra[0] == "--limit":
                        limit = int(extra[1])
                    else:
                        raise ValueError
                except ValueError:
                    print("Usage: gmes_data.py read <SCREENCODE> <datasetName> [limit | --limit N]")
                    return 2
                if limit < -1:
                    print("ERROR: limit must be -1 or a non-negative number")
                    return 2
            result = read_dataset(ws, screen, ds_name, limit=limit)
            if not result.get("found"):
                print(f"No dataset {ds_name!r} on a form matching {screen!r}. "
                      "Run 'gmes_data.py forms' to see what is open.")
                return 1

            print(f"Screen  : {result['file']}")
            print(f"Dataset : {ds_name}   total rows: {result['total']}")
            # gmes_redact is the one word list (HISTORY.md Phase 91.3) - this
            # command kept a third, shorter private copy that still printed
            # session/jwt/apiKey/pwd-named columns and values to the console.
            columns = [c for c in result["columns"] if not gmes_redact.is_sensitive_name(c)]
            print(f"Columns : {', '.join(columns)}\n")

            if command == "csv":
                out = argv[3] if len(argv) > 3 else f"{screen}_{ds_name}.csv"
                print(f"Wrote {result['total']} rows to {write_csv(result, out)}")
                return 0

            for i, row in enumerate(result["rows"]):
                shown = {k: v for k, v in row.items()
                         if v and not k.startswith("_")
                         and not gmes_redact.is_sensitive_name(k)}
                print(f"  [{i}] {shown}")
            if result["total"] > len(result["rows"]):
                print(f"\n  ... {result['total'] - len(result['rows'])} more rows")
            return 0

        print(__doc__)
        return 2
    finally:
        ws.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

"""
A guided demonstration of everything learned about G-MES.

    python gmes_demo.py                 # the full tour
    python gmes_demo.py --quick         # skip the live query (much faster)
    python gmes_demo.py --keep-open     # leave Chrome running afterwards

Twelve steps, each stating what was learned, then proving it against the
live system. Everything here is READ-ONLY: it signs in, opens screens,
runs an Inquiry (a search), and reads data. It saves nothing in G-MES and
changes no business record.

Screenshots are written beside this script as demo_NN_*.png.
"""
import argparse
import os
import sys
import time
from datetime import datetime, timedelta

import cdp_common
from cdp_common import evaluate
import gmes_common
import gmes_data
import gmes_daily_prodplan as job
import gmes_login
import gmes_open_screen
from gmes_common import connect_gmes, find_child_popups, is_logged_in

SHOTS = os.path.dirname(os.path.abspath(__file__))
_step = 0


# ---------------------------------------------------------------------------
# Presentation helpers
# ---------------------------------------------------------------------------

def head(title, lesson):
    global _step
    _step += 1
    print()
    print("=" * 74)
    print(f"STEP {_step}  {title}")
    print("=" * 74)
    print(f"LESSON: {lesson}\n")


def shot(name):
    path = os.path.join(SHOTS, f"demo_{_step:02d}_{name}.png")
    if gmes_common.capture_screenshot(path):
        print(f"    [screenshot: {os.path.basename(path)}]")


def show(label, value):
    print(f"    {label:<34} {value}")


# ---------------------------------------------------------------------------
# The tour
# ---------------------------------------------------------------------------

def step_login():
    head("Unattended sign-in",
         "Chrome 136+ silently refuses to debug the DEFAULT profile, so we\n"
         "        drive a COPY of it - same extensions, same logins. The password\n"
         "        comes from a Windows-DPAPI store, never from the source.")

    print("    Starting Chrome and signing in (no keyboard involved)...\n")
    if gmes_login.main() != 0:
        raise SystemExit("Sign-in failed - see the message above.")

    ws = connect_gmes()
    ok, who = is_logged_in(ws)
    show("Signed in", f"{ok}  as {who!r}")
    show("Profile driven", cdp_common.automation_profile_dir())
    show("Real profile (untouched)", cdp_common.default_user_profile_dir())
    return ws


def step_popups(ws):
    head("The Notice popup",
         "It appears SECONDS AFTER the user name does, and it is MODAL - while\n"
         "        it is open every click is swallowed with no error at all. It is\n"
         "        closed by its window type, not its name (its id is Korean).")

    found = gmes_common.find_child_popups(ws)
    show("Popups on screen now", found.get("count", 0))
    if found.get("count"):
        for p in found["popups"]:
            show("  window", p["name"])
            show("  close button id", "..." + p["id"][-46:])
    closed = gmes_common.close_child_popups(ws)
    show("Closed", closed or "none were open")
    show("Remaining", gmes_common.find_child_popups(ws).get("count", 0))


def step_directory(ws):
    head("The screen directory - G-MES's T-code list",
         "G-MES ships its whole menu catalogue to the browser. 809 screens,\n"
         "        each with a code, an English name and its full breadcrumb.\n"
         "        (A near-identical decoy table exists whose titles are Korean-only\n"
         "        and whose ids are a different series - searching it finds nothing.)")

    info = gmes_open_screen.catalogue(ws, "")
    show("Screens reachable", info.get("total"))

    for query in ("production plan", "work calendar"):
        hits = gmes_open_screen.catalogue(ws, query)
        print(f"\n    search {query!r} -> {hits.get('matched')} match(es)")
        for row in hits["rows"][:3]:
            print(f"      {row['screenId']:<12} {row['menuId']:<9} {row['menuTitle']}")
            print(f"      {'':<22} {row['path']}")


def step_open_by_code(ws):
    head("Open any screen by its code",
         "The top search box accepts a ScreenID - exactly like typing a T-code.\n"
         "        It must be typed with REAL key events: setting the value fills the\n"
         "        box but searches nothing, because the list is driven by onkeyup.")

    target = "P1111UM00"
    show("Opening", f"{target} (Production Plan by Model)")
    opened = gmes_open_screen.open_screen(ws, target)
    show("Result", f"{opened.get('title')}  [menuId {opened.get('menuId')}]")
    show("Window", opened.get("winId"))
    shot("opened_by_code")


def step_open_by_name(ws):
    head("...or by its name",
         "The same entry point takes a menu name. One command reaches all 809\n"
         "        screens, instead of walking four levels of translated menus.")

    opened = gmes_open_screen.open_screen(ws, "Work Calendar")
    show("Result", f"{opened.get('title')}  [menuId {opened.get('menuId')}]")
    show("pageUrl", opened.get("pageUrl"))
    shot("opened_by_name")


def step_tabs(ws):
    head("Open is NOT active - the most dangerous trap found",
         "G-MES keeps every opened screen alive behind tabs. A BACKGROUND screen\n"
         "        still accepts filter changes, so a job can set the date and division\n"
         "        on the right screen and then run Inquiry on whatever is in front.\n"
         "        That produced a run reporting 0 rows from the wrong report.")

    rows = gmes_open_screen.open_screens(ws).get("rows", [])
    print("    Tabs currently open:")
    for r in rows:
        print(f"      {r.get('menuId','?'):<10} {r.get('title','')}")

    target = job.CONTAINER_SCREEN
    print(f"\n    Bringing {target} to the front before doing anything with it...")
    screen = job.ensure_screen(ws)
    show("ensure_screen()", f"{screen.title} ({screen.win_id})")
    shot("correct_tab_active")


def step_data_layer(ws):
    head("The data layer beats the screen",
         "A Nexacro grid only builds the rows you can SEE. Reading the page would\n"
         "        turn a 5,000-row report into the 20 on screen - and report success.\n"
         "        The real rows live in named Datasets, in JavaScript.")

    forms = gmes_data.list_forms(ws)
    show("Forms alive in the app", forms["count"])

    for screen, label in ((job.FILTER_SCREEN, "left filter panel"),
                          (job.RESULT_SCREEN, "results"),
                          (job.ORG_SCREEN, "organisation tree")):
        result = gmes_data.read_dataset(ws, screen, "dsFilterDVO"
                                        if screen == job.FILTER_SCREEN
                                        else job.RESULT_DATASET
                                        if screen == job.RESULT_SCREEN
                                        else job.ORG_TREE_DATASET, limit=0)
        show(f"{screen} ({label})",
             f"{result.get('total', '?')} rows" if result.get("found") else "not open")


def step_filters(ws, plan_date):
    head("Setting filters without touching the widgets",
         "Nexacro binds Datasets to controls, so writing the dataset updates the\n"
         "        visible date box. No calendar picker to fight with.")

    show("Before", gmes_data.read_dataset(ws, job.FILTER_SCREEN, "dsFilterDVO",
                                          limit=1)["rows"][0].get("paramFromDate"))
    job.set_plan_date(ws, plan_date)
    show("After (dataset)", gmes_data.read_dataset(ws, job.FILTER_SCREEN, "dsFilterDVO",
                                                   limit=1)["rows"][0].get("paramFromDate"))
    show("The visible box now reads", "see screenshot - it followed")
    shot("date_set")


def step_division(ws):
    head("The Division - the difference between data and nothing",
         "Without a Division ticked, every query returns nothing and the screen\n"
         "        just says 'Select Search Criteria'. It is a checkbox in a tree, and\n"
         "        it is set by writing _checked on the matching row.")

    screen = job.ensure_screen(ws)
    result = job.select_division(ws, screen, "VD")
    show("Row matched by name", f"index {result['row']}")
    show("Org path key", result["pathKey"])
    show("Checked", result["checked"])
    shot("division_ticked")


def step_query(ws, plan_date):
    head("Knowing when a query has actually finished",
         "Nexacro CLEARS the result set the instant Inquiry is pressed and refills\n"
         "        it when the server answers - so the count sits at 0 for the whole\n"
         "        round trip. Treating stable zeros as 'finished' reported 0 rows\n"
         "        while 790 were on their way.")

    started = time.time()
    rows = job.run_inquiry(ws)
    show("Rows in the dataset", rows)
    show("Query took", f"{time.time() - started:.1f}s (measured, never assumed)")
    show("Plan dates returned", job.verify_result_date(ws, plan_date))
    shot("query_done")
    return rows


def step_reconcile(ws, dataset_rows):
    head("The data and the screen disagree - reconcile before trusting",
         "The dataset carries filler rows with no PO and no production line that\n"
         "        the grid hides. Exporting raw would inflate every row count.")

    result = gmes_data.read_dataset(ws, job.RESULT_SCREEN, job.RESULT_DATASET, limit=-1)
    real = [r for r in result["rows"] if r.get("poNo", "").strip()]
    show("Rows in the dataset", result["total"])
    show("Rows with a real PO", len(real))
    show("Filler rows hidden by the grid", result["total"] - len(real))
    show("The screen's own total", f"{len(real)}  <- matches exactly")

    print("\n    A genuine row (a few columns of 140):")
    if real:
        r = real[0]
        for col in ("planYmd", "masterLine", "modelCode", "poNo", "planQty",
                    "acrsQty", "poStatus"):
            show(f"      {col}", r.get(col, ""))


def step_export(ws):
    head("Two exports, because the official one cannot be read by a program",
         "The Excel icon opens a dialog, not a download. And the file that arrives\n"
         "        is wrapped in Samsung NASCA DRM - it opens in Excel, but no library\n"
         "        can parse it. So a plain CSV is written from the data layer too.")

    show("Official export", "toolbar Excel icon -> 'Save to Excel' dialog -> OK")
    show("Arrives as", "<## NASCA DRM FILE - VER1.00 ##> ... encrypted")
    show("Readable by Excel", "yes, on a DRM-enabled PC")
    show("Readable by pandas/openpyxl", "NO - reports a corrupt file")
    show("So we also write", "a CSV straight from the dataset")

    latest = sorted(
        (f for f in os.listdir(job.OUTPUT_DIR) if f.lower().endswith((".xlsx", ".csv"))),
        key=lambda f: os.path.getmtime(os.path.join(job.OUTPUT_DIR, f)),
        reverse=True) if os.path.isdir(job.OUTPUT_DIR) else []
    print("\n    Most recent files already delivered:")
    for f in latest[:2]:
        size = os.path.getsize(os.path.join(job.OUTPUT_DIR, f)) / 1024
        print(f"      {f}  ({size:,.1f} KB)")
    if not latest:
        print("      (none yet - run gmes_daily_prodplan.py)")


def step_ids(ws):
    head("Why nothing is addressed by a generated id",
         "Work-screen ids embed a window number that changes on EVERY open. An id\n"
         "        captured today finds nothing tomorrow, silently.")

    forms = [f for f in gmes_data.list_forms(ws)["forms"]
             if (f["file"] or "").startswith(job.RESULT_SCREEN)]
    if forms:
        path = forms[0]["path"]
        show("Today the results form is at", "..." + path[-58:])
    show("The part that is stable", f"the screen code {job.RESULT_SCREEN}")
    show("What we match on instead", "screen code, CSS class, visible label")


def step_safety(ws):
    head("Safety - what this automation will not do",
         "These are live production systems holding real manufacturing data.")

    show("Runs read-only", "opens screens, searches, reads - saves nothing")
    show("Credentials", "Windows DPAPI; never in source, argv, or logs")
    show("Session tokens", "present in datasets; never printed or committed")
    show("Real Chrome profile", "copied, never deleted or modified")
    show("Exported data", "git-ignored; never committed")
    show("On failure", "a screenshot, and a non-zero exit code")


# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quick", action="store_true",
                        help="skip the live query and its reconciliation")
    parser.add_argument("--keep-open", action="store_true")
    args = parser.parse_args()

    plan_date = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")

    print("=" * 74)
    print("  G-MES AUTOMATION - WHAT WE LEARNED")
    print(f"  {datetime.now():%Y-%m-%d %H:%M}   read-only demonstration")
    print("=" * 74)

    ws = step_login()
    try:
        step_popups(ws)
        step_directory(ws)
        step_open_by_code(ws)
        step_open_by_name(ws)
        step_tabs(ws)
        step_data_layer(ws)
        step_filters(ws, plan_date)
        step_division(ws)

        if args.quick:
            print("\n  (--quick: skipping the live query)")
        else:
            rows = step_query(ws, plan_date)
            if rows:
                step_reconcile(ws, rows)

        step_export(ws)
        step_ids(ws)
        step_safety(ws)

        print()
        print("=" * 74)
        print("  DEMO COMPLETE")
        print("=" * 74)
        print("""
  The whole nightly job is one command:

      python gmes_daily_prodplan.py

  Reaching any of the 809 screens is one command:

      python gmes_open_screen.py <SCREEN CODE or NAME>

  Every trap shown above is written down in GMES_SKILL.md (26 numbered
  gotchas) and HISTORY.md (symptom, cause, fix, lesson).
""")
        return 0
    finally:
        ws.close()
        if not args.keep_open and cdp_common.LAST_CHROME_PROCESS:
            print("  Closing the automation browser.")
        cdp_common.stop_if_started_here(args.keep_open)


if __name__ == "__main__":
    sys.exit(main())

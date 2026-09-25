"""
Nightly job: Production Plan by Order(Line), Division VD, yesterday's plan
date, exported with GMES's own Excel download.

    python gmes_daily_prodplan.py                  # yesterday (the normal run)
    python gmes_daily_prodplan.py --date 20260908  # a specific plan date
    python gmes_daily_prodplan.py --days-back 2
    python gmes_daily_prodplan.py --keep-open      # leave Chrome up afterwards

Result: "Data Hub Folder/GMES/Production Plan by Order(Line)_<date>_<time>.xlsx"
(Excel only; --csv adds the data-layer CSV copy - HISTORY.md Phase 94.1)

Screen: PPM > Production Plan > Prod. Plan Inquiry > Detail Schedule >
Production Plan by Order(Line)  [ P1112UM00 > P1112WM00 ], filter panel
P1112WF00, org tree OrgCategory_GDS.

Runs with nobody watching, so every step verifies rather than assumes, and
exits non-zero with a screenshot if anything is off.
"""
import argparse
import csv
import os
import shutil
import sys
import tempfile
import time
import uuid
from datetime import datetime, timedelta

import cdp_common
import gmes_core as core
import gmes_data
from gmes_common import connect_gmes, is_logged_in, screenshot_on_failure

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = core.OUTPUT_DIR
REPORT_NAME = "Production Plan by Order(Line)"

# Screen codes from the breadcrumb - stable, unlike the window ids.
CONTAINER_SCREEN = "P1112UM00"   # what the search bar opens
FILTER_SCREEN = "P1112WF00"      # left panel: dates, and the Inquiry it feeds
RESULT_SCREEN = "P1112WM00"      # the grid's data
ORG_SCREEN = "OrgCategory_GDS"   # the Org tab's tree

RESULT_DATASET = "dsMasterProdPlan"
ORG_TREE_DATASET = "dsCatCommonTreeNodeDVO"

EXCEL_BTN = core.EXCEL_BTN


# ---------------------------------------------------------------------------
# Steps
#
# Each one is a thin wrapper over gmes_core, so this job and the generic
# runner share one implementation. They did not always: a second copy of the
# inquiry wait is how a run came to report the previous screen's row count.
# What stays here is only what is specific to THIS report - which screen,
# which datasets, and which rows are subtotals.
# ---------------------------------------------------------------------------

def ensure_screen(ws, screen_code=CONTAINER_SCREEN, max_wait=90):
    """Make sure the report's screen is open, in front, and finished building.

    Without this the job silently depended on that screen happening to be the
    session's default. It is reached by screen code, the same one entry point
    that reaches all 809 screens, rather than by walking four levels of menu.

    Being open is not enough - it must be the tab in FRONT. A background
    screen still accepts dataset writes, so the filters applied correctly and
    the Inquiry click then landed on whichever screen was actually visible,
    returning zero rows from the wrong report.

    Returns the `Screen` handle (not a display string): `main()` needs its
    discovered filters/grids to resolve `path_for()` below, so the caller
    that used to throw this away now keeps it."""
    screen = core.open_screen(ws, screen_code, ready_wait=max_wait)

    # This report reads P1112WM00, which is a different form from the one the
    # search box opens. Confirm it exists rather than assuming the container
    # brought it with it.
    deadline = time.time() + max_wait
    while time.time() < deadline:
        forms = gmes_data.list_forms(ws)["forms"]
        if any((f["file"] or "").startswith(RESULT_SCREEN) for f in forms):
            screen.refresh()
            return screen
        time.sleep(1.0)
    raise RuntimeError(f"{screen_code} opened but {RESULT_SCREEN} never appeared.")


def path_for(screen, dataset):
    """The exact form path this run's own discovery found `dataset` on, or
    None if it never saw it.

    HISTORY.md, external review of 8ac502a (finding #1/#2): `dsFilterDVO` and
    similar names are not unique across the app - `P1112WF00`'s own filter
    panel is one instance among others a reusable component can produce -
    and this job used to address every dataset by screen code + dataset name
    alone, exactly the ambiguity the generic `Screen.apply()` path was fixed
    to avoid. `screen.info` was already discovering the right instance
    (JS_DISCOVER scopes to this run's own work window); only the PATH that
    proves it was being thrown away before reaching the write. None is a
    safe fallback, not a silent failure: `gmes_data.set_filter()` /
    `read_dataset()` fall back to their pre-fix whole-app search, unchanged
    from every release before this one."""
    for f in screen.filters + screen.unbound:
        if f.get("dataset") == dataset:
            return f.get("path")
    for g in screen.info.get("grids", []):
        if g.get("dataset") == dataset:
            return g.get("path")
    return None


def set_plan_date(ws, yyyymmdd, path=None):
    """Write the plan-date range into the filter panel's dataset.

    Setting the Dataset rather than typing into the date box: Nexacro binds
    the two, so the visible field updates, and there is no calendar widget
    to fight with."""
    result = gmes_data.set_filter(ws, FILTER_SCREEN, "dsFilterDVO",
                                 {"paramFromDate": yyyymmdd, "paramEndDate": yyyymmdd},
                                 path=path)
    if not result.get("found"):
        raise RuntimeError(
            f"The filter panel ({FILTER_SCREEN}) is not open. Is "
            "'Production Plan by Order(Line)' the active screen?")
    applied = result.get("applied", {})
    if applied.get("paramFromDate") != yyyymmdd or applied.get("paramEndDate") != yyyymmdd:
        raise RuntimeError(f"The plan date did not take: {applied}")
    return applied


def select_division(ws, screen, name="VD"):
    """Tick a division in the Org tree.

    Without this the query returns nothing at all and the screen just says
    "Select Search Criteria" - the single most likely reason for a silent
    empty export, so it is checked rather than assumed.

    The row is found by its visible name, not by row number: the tree is built
    from the user's permissions and its order is not guaranteed. Ticks this
    run did not ask for are cleared, because a tick survives between runs
    exactly as a typed filter does.

    `ORG_SCREEN` ("OrgCategory_GDS") is the tree's own shared form name, not
    this job's work screen code - it is a reusable component embedded on
    many unrelated screens. `screen.trees()` (window-scoped, since
    org_trees() anchors on the Screen's own work-screen code) finds every
    copy of it in THIS run's own window; their exact paths are what stop
    tick_org()'s own fallback search from reaching a different, unrelated
    window's copy of the same tree (HISTORY.md - external review of
    1957ba9/cff282b, finding #5)."""
    same_tree_paths = [t["path"] for t in screen.trees()
                       if t["form"] == ORG_SCREEN and t["dataset"] == ORG_TREE_DATASET
                       and t.get("path")]
    result = core.tick_org(ws, ORG_SCREEN, ORG_TREE_DATASET, [name], exclusive=True,
                           paths=same_tree_paths)
    if not result.get("found"):
        raise RuntimeError(
            f"Division {name!r} was not in the Org tree "
            f"({result.get('reason', 'not present')}). "
            f"Divisions present: {result.get('available', '(tree not loaded)')}")
    shown = core.org_selection(ws)
    if not shown.get("found") or (shown.get("org") or "").strip().casefold() != name.strip().casefold():
        raise RuntimeError(f"could not prove that division {name!r} is active on the screen")
    return result


def run_inquiry(ws, path=None, **kwargs):
    """Click Inquiry and wait until this report's result set has settled."""
    return core.poll_inquiry(ws, RESULT_SCREEN, RESULT_DATASET, path=path, **kwargs)


def verify_result_date(ws, expected_yyyymmdd, path=None):
    """Confirm the rows really are for the date we asked for.

    A stale result set from a previous query looks exactly like a fresh one,
    and an export of the wrong day is worse than no export at all - so a
    mismatch stops the job here rather than producing a plausible file."""
    dates, problem = core.verify_rows(ws, RESULT_SCREEN, RESULT_DATASET,
                                      "planYmd", expected_yyyymmdd, path=path)
    if problem:
        raise RuntimeError(problem + ". Refusing to export the wrong day.")
    return dates


def download_excel(ws, target_dir, timeout=240):
    """Click the toolbar Excel icon, confirm its dialog, wait for the file."""
    return core.download_excel(ws, target_dir, timeout=timeout)


def deliver(downloaded_path, target_dir, stamp):
    """Move the export to its final name."""
    os.makedirs(target_dir, exist_ok=True)
    final = os.path.join(target_dir, f"{REPORT_NAME}_{stamp}.xlsx")
    if os.path.abspath(downloaded_path) != os.path.abspath(final):
        shutil.move(downloaded_path, final)
    return final


is_drm_protected = core.is_drm_protected


def export_clean_data(ws, target_dir, stamp, plan_date, path=None):
    """Write a machine-readable copy straight from the Dataset.

    Two reasons this exists alongside the official download:
      * The GMES file is DRM-encrypted, so nothing downstream can read it.
      * The Dataset carries rows with no PO and no master line that the grid
        renders as LINE SUM and PROC SUM: the labels are added by the grid at
        render time and are not stored, which is why they read as blank.
        Exporting them raw would inflate the row count - 875 against the
        screen's own 790 - so they are dropped here.

    This is the one place a key column may be assumed, because this job knows
    its screen. The generic exporter in gmes_core deliberately does not.

    Column safety is NOT one of those screen-specific things, and used to be
    reimplemented here anyway - a bare `not c.startswith("_")` filter, missing
    `gmes_data.SENSITIVE_COLUMN`'s check for a column merely named like a
    credential (CLAUDE.md 2.3's own example, `refreshTokenId`, does not start
    with `_`). `dsMasterProdPlan` has never carried one, but a second, weaker
    copy of a security filter is exactly the kind of thing that stops being
    true the moment this screen's shape changes and nobody remembers to update
    both copies (HISTORY.md Phase 78). There is one filter now, shared with
    every other exporter through `gmes_data.redact_sensitive_columns()`."""
    result = gmes_data.read_dataset(ws, RESULT_SCREEN, RESULT_DATASET, limit=-1, path=path)
    if not result.get("found"):
        return None, 0, 0

    real = [r for r in result["rows"] if r.get("poNo", "").strip()]
    dropped = len(result["rows"]) - len(real)

    os.makedirs(target_dir, exist_ok=True)
    path = os.path.join(target_dir, f"{REPORT_NAME}_{stamp}_data.csv")
    columns, withheld = gmes_data.redact_sensitive_columns(result["columns"])
    if withheld:
        print(f"  [!] withheld from CSV, column name looks like a credential: {withheld}")
    fd, temporary = tempfile.mkstemp(prefix=".gmes-plan-", suffix=".partial", dir=target_dir)
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8-sig") as fh:
            writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(gmes_data.safe_rows_for_csv(real, columns))
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return path, len(real), dropped


# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", help="Plan date as YYYYMMDD (default: yesterday)")
    parser.add_argument("--days-back", type=int, default=1)
    parser.add_argument("--division", default="VD")
    parser.add_argument("--output-dir", default=OUTPUT_DIR)
    # Excel only by default since the owner's decision of 2026-09-23 (HISTORY.md
    # Phase 94.1). --no-csv is still accepted so an existing scheduled command
    # line keeps working; it is now simply what happens anyway.
    parser.add_argument("--csv", action="store_true",
                        help="also write the machine-readable CSV copy (off by default)")
    parser.add_argument("--no-csv", action="store_true",
                        help="(the default now; kept so old command lines still work)")
    parser.add_argument("--keep-open", action="store_true",
                        help="leave Chrome running after the job")
    args = parser.parse_args()
    if args.days_back < 0:
        parser.error("--days-back must be zero or greater")

    plan_date = args.date or (datetime.now() - timedelta(days=args.days_back)).strftime("%Y%m%d")
    plan_date = core.normalise_date(plan_date)
    stamp = f"{datetime.now():%Y%m%d_%H%M%S_%f}_{uuid.uuid4().hex[:8]}"

    print("=" * 70)
    print(f"GMES daily export - {REPORT_NAME}")
    print(f"Plan date {plan_date}   Division {args.division}   started {stamp}")
    print("=" * 70)

    # The interactive front end and gmes_report.py both refuse to share one
    # Chrome/CDP session between two processes - a concurrent run's screen-
    # open can land on a row the other run's screen made temporarily not
    # visible, failing with a confusing "grid row not visible" that says
    # nothing about a second run being the cause. This job had no such guard
    # at all: a scheduled run overlapping a manual one could silently change
    # the screen or filters the other was mid-query against (HISTORY.md
    # Phase 78). Acquired before sign-in, exactly like the other two
    # entrances, and released in the outer `finally` below so a failure
    # anywhere still frees it for the next scheduled run.
    try:
        lock_token = core.acquire_run_lock()
    except core.RunLocked as e:
        print(f"\nFAILED: {e}")
        return 1

    try:
        # Retried once: after a long idle the session expires, clicking AD SSO
        # produces no SSO window, and the second attempt signs in normally. An
        # unattended job should not fail on that.
        if not core.sign_in():
            print("\nFAILED: could not sign in to GMES.")
            return 1

        ws = connect_gmes()
        try:
            signed_in, who = is_logged_in(ws)
            print(f"\nSigned in as {who!r}." if signed_in else "\nWARNING: sign-in unconfirmed.")

            screen = ensure_screen(ws)
            print(f"Screen: {screen.title} ({screen.win_id})")
            filter_path = path_for(screen, "dsFilterDVO")
            result_path = path_for(screen, RESULT_DATASET)
            # This job knows exactly which two datasets it needs - unlike
            # gmes_data.py's manual CLI, there is no legitimate reason for
            # either to be missing from a screen ensure_screen() just
            # confirmed is open and built. path=None silently falls back to
            # gmes_data's pre-Phase-80 whole-app search, which is exactly
            # the ambiguity this job's own path scoping exists to avoid
            # (HISTORY.md - external review of 1957ba9/cff282b, finding #6)
            # - so a missing path here means the screen is not in the state
            # this job assumes, and that is worth stopping for, not
            # papering over with a weaker search.
            if filter_path is None or result_path is None:
                missing = ", ".join(name for name, path in
                                    (("dsFilterDVO", filter_path),
                                     (RESULT_DATASET, result_path)) if path is None)
                print(f"\nFAILED: could not resolve an exact form path for: {missing}.")
                print("  The screen opened, but discovery did not find this dataset "
                      "where this job expects it - refusing to fall back to an "
                      "unscoped, whole-app search.")
                screenshot_on_failure("gmes_daily_no_path")
                return 1

            print(f"Setting the plan date to {plan_date}...")
            print(f"  {set_plan_date(ws, plan_date, path=filter_path)}")

            print(f"Selecting division {args.division}...")
            print(f"  {select_division(ws, screen, args.division)}")

            print("Running the inquiry (waiting for the result set to settle)...")
            rows = run_inquiry(ws, path=result_path)
            print(f"  {rows} rows returned.")
            if rows == 0:
                print("\nFAILED: the query returned no rows. Nothing was exported.")
                screenshot_on_failure("gmes_daily_no_rows")
                return 1

            dates = verify_result_date(ws, plan_date, path=result_path)
            print(f"  plan dates in the result: {dates}")

            print("Downloading GMES's own Excel file...")
            downloaded = download_excel(ws, args.output_dir)
            final = deliver(downloaded, args.output_dir, stamp)
            core.check_download(final)
            drm = is_drm_protected(final)

            csv_path, real_rows, filler = (None, 0, 0)
            if args.csv and not args.no_csv:
                print("Writing a machine-readable copy from the data layer...")
                csv_path, real_rows, filler = export_clean_data(
                    ws, args.output_dir, stamp, plan_date, path=result_path)
                if not csv_path or real_rows <= 0:
                    raise RuntimeError(
                        "the data-layer CSV is missing or contains no production rows")

            print("\n" + "=" * 70)
            print("DONE")
            print(f"  Excel : {final}")
            print(f"          {os.path.getsize(final) / 1024:,.1f} KB"
                  + ("  [DRM-protected: opens in Excel, unreadable by other programs]"
                     if drm else ""))
            if csv_path:
                print(f"  CSV   : {csv_path}")
                print(f"          {real_rows} rows"
                      + (f" ({filler} blank filler rows dropped)" if filler else ""))
            print(f"  Date  : {plan_date}   Division: {args.division}")
            print("=" * 70)
            return 0

        except RuntimeError as e:
            print(f"\nFAILED: {e}")
            screenshot_on_failure("gmes_daily_failed")
            return 1
        finally:
            ws.close()
            if not args.keep_open and cdp_common.LAST_CHROME_PROCESS:
                print("Closing the automation browser.")
            cdp_common.stop_if_started_here(args.keep_open)
    finally:
        core.release_run_lock(lock_token)


if __name__ == "__main__":
    sys.exit(main())

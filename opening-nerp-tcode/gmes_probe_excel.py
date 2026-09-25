"""
Find out how GMES's own Excel download actually behaves.

    python gmes_probe_excel.py

Points Chrome's downloads at a scratch folder, clicks the toolbar Excel
icon, and watches both the folder and the page for 40 seconds.

The unknowns worth settling before building the nightly job: whether the
click produces a file directly or opens a dialog first, what GMES names the
file, how long a 700-row export takes, and whether anything appears on
screen that would block the next step.
"""
import os
import sys
import time

import cdp_common
from cdp_common import evaluate, send
import gmes_common
from gmes_common import connect_gmes, find_child_popups

WATCH_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "Data Hub Folder", "GMES", "_probe")

EXCEL_BTN = "mainframe.vFrameSet1.vFrameSet2.mdiFrame.form.btnExcel"


def set_download_dir(ws, path):
    """Send downloads to `path` instead of the user's Downloads folder.

    Browser.setDownloadBehavior applies to the whole browser, so it works
    even when the file is fetched by a frame other than the one we are
    driving - which is how a Nexacro export usually arrives."""
    os.makedirs(path, exist_ok=True)
    try:
        send(ws, "Browser.setDownloadBehavior",
             {"behavior": "allow", "downloadPath": path, "eventsEnabled": True})
        return "Browser.setDownloadBehavior"
    except Exception:
        send(ws, "Page.setDownloadBehavior",
             {"behavior": "allow", "downloadPath": path})
        return "Page.setDownloadBehavior"


def listing(path):
    try:
        return {f: os.path.getsize(os.path.join(path, f)) for f in os.listdir(path)}
    except FileNotFoundError:
        return {}


def main():
    ws = connect_gmes()
    try:
        print(f"Download folder : {WATCH_DIR}")
        print(f"Set via         : {set_download_dir(ws, WATCH_DIR)}")

        before = listing(WATCH_DIR)
        print(f"Files before    : {list(before) or 'none'}\n")

        info = evaluate(ws, gmes_common.js_find_by_id(EXCEL_BTN))
        if not info.get("found"):
            print(f"The Excel button was not visible ({info.get('reason')}).")
            return 1
        print(f"Clicking the Excel icon at ({info['x']:.0f}, {info['y']:.0f})...\n")
        cdp_common.click_element_by_rect(ws, info["x"], info["y"])

        print(f"{'t+sec':>6}  {'popups':>6}  files")
        print("-" * 70)
        start = time.time()
        reported = set()
        while time.time() - start < 40:
            now = listing(WATCH_DIR)
            popups = find_child_popups(ws)
            new = {f: s for f, s in now.items() if f not in before}
            line = ", ".join(f"{f} ({s:,}b)" for f, s in new.items()) or "-"
            print(f"{time.time() - start:6.1f}  {popups.get('count', 0):>6}  {line}")
            for p in popups.get("popups", []):
                if p["name"] not in reported:
                    reported.add(p["name"])
                    print(f"        popup on screen: {p['name']}")
            # A .crdownload file means Chrome is still writing it.
            if new and not any(f.endswith(".crdownload") for f in new):
                print("\nDownload finished.")
                break
            time.sleep(1.5)

        final = {f: s for f, s in listing(WATCH_DIR).items() if f not in before}
        print("-" * 70)
        print(f"New files: {final or 'NONE - the click produced no download'}")
        if reported:
            print(f"Popups seen: {sorted(reported)}")
        gmes_common.capture_screenshot("gmes_after_excel.png")
        print("Screenshot: gmes_after_excel.png")
        return 0
    finally:
        ws.close()


if __name__ == "__main__":
    sys.exit(main())

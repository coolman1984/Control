"""
Learn how the G-MES top search bar behaves.

    python gmes_probe_search.py P1112WM00
    python gmes_probe_search.py "Production Plan by Order"

Types into the search box the way a person does - a real click to focus,
real key events, real Enter - then reports what appeared: new popups, new
datasets carrying results, and a screenshot.

The goal is a single reliable "open any screen by its code" entry point, the
G-MES equivalent of typing a T-code in N-ERP. Guessing at the interaction
would be fragile, so this observes it first.
"""
import sys
import time

import cdp_common
from cdp_common import evaluate, send
import gmes_common
import gmes_data
from gmes_common import connect_gmes, connect_gmes as _c

SEARCH_EDIT = "mainframe.vFrameSet1.vFrameSet2.topFrame.form.divSearch.form.edtSearch"
SEARCH_INPUT = SEARCH_EDIT + ":input"


def type_text(ws, text):
    """Click the box, then send real key events.

    Nexacro Edit controls listen for key events, not just a value
    assignment, and the suggestion list is driven by them - so a bare
    `.value =` fills the box while triggering nothing."""
    info = evaluate(ws, gmes_common.js_find_by_id(SEARCH_EDIT))
    if not info.get("found"):
        raise RuntimeError(f"Search box not found ({info.get('reason')})")
    cdp_common.click_element_by_rect(ws, info["x"], info["y"])
    time.sleep(0.4)

    # Clear whatever placeholder/previous text is there.
    for _ in range(40):
        for key, code, vk in (("Backspace", "Backspace", 8), ("Delete", "Delete", 46)):
            send(ws, "Input.dispatchKeyEvent",
                 {"type": "keyDown", "key": key, "code": code,
                  "windowsVirtualKeyCode": vk, "nativeVirtualKeyCode": vk})
            send(ws, "Input.dispatchKeyEvent",
                 {"type": "keyUp", "key": key, "code": code,
                  "windowsVirtualKeyCode": vk, "nativeVirtualKeyCode": vk})

    send(ws, "Input.insertText", {"text": text})
    time.sleep(0.3)
    return info


def press_enter(ws):
    for kind in ("keyDown", "char", "keyUp"):
        params = {"type": kind, "key": "Enter", "code": "Enter",
                  "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13}
        if kind == "char":
            params["text"] = "\r"
        send(ws, "Input.dispatchKeyEvent", params)


def datasets_with_rows(ws):
    info = gmes_data.list_forms(ws)
    return {f"{f['file']}:{d}" for f in info["forms"] for d in f["datasets"]}


def snapshot_popups(ws):
    return [p["name"] for p in gmes_common.find_child_popups(ws).get("popups", [])]


def main(query):
    ws = connect_gmes()
    try:
        print(f"Query: {query!r}\n")
        before_popups = snapshot_popups(ws)

        type_text(ws, query)
        value = evaluate(ws, """
            (function(){ const el = document.getElementById(%s);
              return JSON.stringify({value: el ? el.value : null}); })()
            """ % cdp_common.json.dumps(SEARCH_INPUT))
        print(f"Box now contains: {value.get('value')!r}")

        print("\nAfter typing (before Enter):")
        for _ in range(6):
            time.sleep(0.7)
            popups = snapshot_popups(ws)
            if popups != before_popups:
                print(f"  popups changed -> {popups}")
                break
        else:
            print("  no popup change")

        gmes_common.capture_screenshot("gmes_search_typed.png")
        print("  screenshot: gmes_search_typed.png")

        print("\nPressing Enter...")
        press_enter(ws)
        for i in range(10):
            time.sleep(1.0)
            popups = snapshot_popups(ws)
            if popups != before_popups:
                print(f"  t+{i+1}s popups -> {popups}")
                break

        gmes_common.capture_screenshot("gmes_search_entered.png")
        print("  screenshot: gmes_search_entered.png")

        # Which screens are open now?
        info = gmes_data.list_forms(ws)
        work = [f for f in info["forms"] if "workFrameSet" in f["path"]]
        print("\nWork screens now open:")
        for f in work:
            short = f["path"].replace("application.mainframe.vFrameSet1.vFrameSet2.", "")
            print(f"  {f['file']:<26} {short}")
        return 0
    finally:
        ws.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))

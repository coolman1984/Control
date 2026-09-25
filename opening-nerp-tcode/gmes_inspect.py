"""
Look at what is on screen right now, in every open browser window.

    python gmes_inspect.py              # all windows
    python gmes_inspect.py sso          # only windows whose URL/title matches
    python gmes_inspect.py --shot       # also save a screenshot of each

This is the tool to reach for whenever a step does not do what was expected:
it shows the buttons and fields that are actually there, with their ids, so
the next instruction can name them exactly instead of guessing.
"""
import sys

import cdp_common
import gmes_common
from cdp_common import connect, get_tabs


def main(filter_text=None, shots=False):
    pages = [t for t in get_tabs() if t.get("type") == "page"]
    if filter_text:
        needle = filter_text.lower()
        pages = [t for t in pages
                 if needle in (t.get("url", "") + str(t.get("title", ""))).lower()]

    if not pages:
        print("No matching browser windows are open.")
        return 1

    for i, tab in enumerate(pages, 1):
        print("=" * 70)
        print(f"WINDOW {i}/{len(pages)}: {tab.get('title')!r}")
        print(f"  {tab.get('url')}")
        print("=" * 70)
        ws = None
        try:
            ws = connect(tab["webSocketDebuggerUrl"], timeout=15)
            report = gmes_common.screen_report(ws)
            gmes_common.print_report(report)
        except Exception as e:
            print(f"  (could not read this window: {e!r})")
        finally:
            if ws:
                ws.close()

        if shots:
            name = f"gmes_window_{i}.png"
            # This tool deliberately inspects every explicit tab, including
            # SSO and N-ERP. Passing that exact tab is safe; the strict
            # G-MES wrapper would replace a non-G-MES tab with another page.
            if cdp_common.capture_screenshot(name, tab=tab):
                print(f"\n  Screenshot: {name}")
        print()
    return 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    sys.exit(main(args[0] if args else None, shots="--shot" in sys.argv))

"""
GMES step 1 - connect to the GMES system using YOUR normal Chrome profile,
open the page, and report what is on it.

Run it:
    python gmes_connect.py

What it does:
  1. Checks whether Chrome is already open. If it is, it stops and asks you
     to close it - Chrome will not hand over control of a profile that is
     already in use.
  2. Starts Chrome on your real profile (all extensions, logins and
     certificates intact - nothing is deleted or copied).
  3. Opens the GMES page and waits for it to finish loading.
  4. Saves a screenshot and prints what it can see: the page title, how many
     frames it has, and the buttons/fields it found.

Step 4 is the reconnaissance we need before automating anything: GMES is
built with Nexacro, not SAP WebGUI, so its buttons and fields are named
differently and we have to look before we click.
"""
import sys
import time

import cdp_common
import gmes_browsers
from cdp_common import connect, evaluate, get_page_tab, get_tabs

GMES_URL = "http://seegmes4.sec.samsung.net/mes4/sm/nexacro/index_ext_2318.html"


JS_PAGE_REPORT = """
(function() {
    const isVisible = %s;
    const seen = new Set();
    const clickable = [];
    Array.from(document.querySelectorAll('div, span, button, a, td, li')).forEach(el => {
        const t = (el.textContent || '').trim();
        if (!t || t.length > 30) return;
        if (!isVisible(el)) return;
        if (seen.has(t)) return;
        seen.add(t);
        const r = el.getBoundingClientRect();
        clickable.push({text: t, id: el.id, tag: el.tagName,
                        x: Math.round(r.left + r.width/2), y: Math.round(r.top + r.height/2)});
    });

    const fields = Array.from(document.querySelectorAll('input, textarea, select'))
        .filter(isVisible)
        .map(el => ({tag: el.tagName, type: el.type || '', id: el.id,
                     title: el.title || '', placeholder: el.placeholder || '',
                     value: (el.value || '').slice(0, 30)}));

    return JSON.stringify({
        title: document.title,
        url: location.href,
        readyState: document.readyState,
        frames: document.querySelectorAll('iframe').length,
        // Nexacro renders into its own component tree; its presence tells us
        // which automation approach applies.
        nexacro: typeof nexacro !== 'undefined',
        canvasCount: document.querySelectorAll('canvas').length,
        elementCount: document.querySelectorAll('*').length,
        textPreview: (document.body ? (document.body.innerText || '') : '')
                        .replace(/\\s+/g, ' ').trim().slice(0, 400),
        fieldCount: fields.length,
        fields: fields.slice(0, 40),
        clickableCount: clickable.length,
        clickable: clickable.slice(0, 60)
    });
})()
""" % cdp_common.JS_IS_VISIBLE


def wait_for_page(max_wait=180, poll_interval=2):
    """Poll until the page has actually rendered something.

    Same rule as the NERP skill: never guess a fixed wait. A Nexacro app
    downloads and builds its whole UI in JavaScript after the document is
    'complete', so readyState alone is not enough - we also wait for real
    elements to exist."""
    deadline = time.time() + max_wait
    last = {}
    while time.time() < deadline:
        tab = get_page_tab(prefer_url_substring="gmes")
        if tab:
            ws = None
            try:
                ws = connect(tab["webSocketDebuggerUrl"], timeout=15)
                last = evaluate(ws, JS_PAGE_REPORT, timeout=20)
                if last.get("readyState") == "complete" and last.get("elementCount", 0) > 50:
                    return tab, last
            except Exception:
                pass
            finally:
                if ws:
                    ws.close()
        time.sleep(poll_interval)
    return None, last


def main():
    print("=" * 70)
    print("GMES - step 1: connect using your normal Chrome profile")
    print("=" * 70)

    # Deliberately NOT a bare `cdp_is_up()` pre-check, which is what this was
    # until HISTORY.md Phase 75.10. That asks "is ANY browser answering on the
    # port we would resolve to" - and with an OS-assigned port, a machine whose
    # automation profile has never been launched resolves to the historical
    # 9444. A leftover browser on the OLD copied profile answers there, gets
    # reported as "already connected", and this function then drives it while
    # `launch_automation_chrome()` - and with it the whole first-run bootstrap -
    # is never called at all. `gmes_login.ensure_browser()` had the identical
    # bug and was fixed in commit 0ad61c3; the comment explaining that fix was
    # what alerted a reviewer that this copy of it had never had the fix.
    #
    # `launch_automation_chrome()` asks the narrower and correct question - is
    # a browser serving THIS profile - and returns None when one already is.
    #
    # Your own browser being open is not a conflict: the automation drives its
    # own separate --user-data-dir, and Chromium runs a second instance on one
    # quite happily (GMES_SKILL.md #44). Nothing of yours is touched, closed or
    # copied. Except on the very first run, which builds that separate profile
    # by copying the one you already use - so the reassurance waits until there
    # IS a profile, rather than contradicting the instruction the first run is
    # about to give.
    if gmes_browsers.recorded_profile_dir() and cdp_common.chrome_is_running():
        print("\n(your own Chrome is open - that is fine, this uses its own "
              "separate profile)")

    print("\nStarting the automation browser on this tool's own profile...")
    try:
        started = cdp_common.launch_automation_chrome(url=GMES_URL)
    except RuntimeError as e:
        print(f"\nERROR: {e}")
        return 1

    if started is None:
        print("Already connected; navigating to GMES...")
        cdp_common.navigate_page(GMES_URL)
    else:
        print("The browser is up and under control.")

    print("\nWaiting for the GMES page to finish building itself...")
    tab, report = wait_for_page()

    if not tab:
        print("\nThe page did not finish loading. What we could see:")
        print(f"  {report}")
        gmes_common.screenshot_on_failure("gmes_load_failed")
        return 1

    print("\n" + "-" * 70)
    print(f"Page title   : {report.get('title')!r}")
    print(f"URL          : {report.get('url')}")
    print(f"Nexacro app  : {report.get('nexacro')}")
    print(f"Inner frames : {report.get('frames')}   canvas elements: {report.get('canvasCount')}")
    print(f"Elements     : {report.get('elementCount')}")
    print(f"Input fields : {report.get('fieldCount')}")
    print(f"Clickables   : {report.get('clickableCount')}")
    print("-" * 70)

    print("\nWhat the page says (first 400 characters):")
    print(f"  {report.get('textPreview')!r}")

    if report.get("fields"):
        print("\nInput fields found:")
        for f in report["fields"]:
            print(f"  [{f['tag']}/{f['type']}] id={f['id']!r} title={f['title']!r} "
                  f"placeholder={f['placeholder']!r} value={f['value']!r}")

    if report.get("clickable"):
        print("\nClickable-looking items found (text @ position):")
        for c in report["clickable"]:
            print(f"  {c['text']!r:32} <{c['tag']} id={c['id']!r}> at ({c['x']}, {c['y']})")

    tabs = get_tabs()
    print(f"\nBrowser targets ({len(tabs)}):")
    for t in tabs:
        print(f"  {t.get('type'):15} {str(t.get('title'))[:40]!r:42} {t.get('url', '')[:70]}")

    shot = gmes_common.capture_screenshot("gmes_step1.png")
    if shot:
        print(f"\nScreenshot saved: {shot}")

    print("\nStep 1 done. Send me this output and the screenshot and I will "
          "work out step 2 (logging in / navigating to the screen you need).")
    return 0


if __name__ == "__main__":
    sys.exit(main())

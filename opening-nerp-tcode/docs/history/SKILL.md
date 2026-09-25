---
name: opening-nerp-tcode
description: Opens the NERP portal (https://nerps.sec.samsung.net) in Chrome via CDP automation, waits for the SAP Fiori/chatbot UI to load, types a given T-code (e.g. MB52, MB51) into the "Search Program" field, clicks "Go", fills selection-screen filter fields (e.g. Material, Plant) and clicks "Execute", and can export the resulting list to a genuine Excel (.xlsx) file via Shift+F4 / Ctrl+Shift+F7 / Shift+F7 / Ctrl+Shift+F9 / toolbar Export icon (whichever the report responds to). Use whenever asked to open NERP, search/run a T-code, filter its selection screen, "execute" on a T-code page, or "export to excel file".
---

# Opening NERP T-code

> Before changing anything here, read [CLAUDE.md](CLAUDE.md) (the operating
> rules) and [HISTORY.md](HISTORY.md) (every incident and its cause).
> **Any behaviour change requires a HISTORY.md entry in the same commit**,
> and a durable discovery about N-ERP belongs in the numbered gotchas below.
> The sibling skill for Samsung G-MES is [GMES_SKILL.md](GMES_SKILL.md).

Automates the full NERP workflow through the Chrome DevTools Protocol (CDP),
not keyboard/mouse simulation (SendKeys was tried repeatedly and never
worked reliably in this environment):

1. Open the NERP portal and search for a T-code — `search_tcode.py`
2. Fill selection-screen filters and click **Execute** — `execute_filters.py`
3. Export the resulting list to Excel — `export_to_excel.py`

`run_nerp_workflow.py` runs all three end to end, either interactively or
from the command line. Double-clicking `NERP_Workflow.bat` starts the
interactive form.

**"Execute"** always means: click the SAP **Execute** button (F8 / "Execute
Emphasized") on whatever selection screen is currently open. Its location is
found dynamically each time, never a fixed screen coordinate.

**"Export to excel file"** always means: on whatever list screen is open
(post-Execute), try **Shift+F4**, then **Ctrl+Shift+F7**, then **Shift+F7**,
then **Ctrl+Shift+F9**, then the toolbar **Export icon** → **"Spreadsheet"**,
in that order, until one opens a recognised dialog — then fill in
`<T-code>_<YYYYMMDD_HHMMSS>.xlsx` and click through to confirm. Which
dialog appears determines which of three completion flows runs; see step 4.

## Setup (once per machine)

```powershell
python -m pip install -r requirements.txt      # websocket-client
```

Chrome is located automatically (Program Files, Program Files (x86),
%LOCALAPPDATA%, PATH, then the registry). Set `CHROME_PATH` to override.

Optional environment overrides: `NERP_CDP_PORT` (default 9444), `NERP_URL`,
`NERP_CHROME_PROFILE`.

## Quick reference

All commands below assume the skill directory is the working directory. If
it is not, use the full path to each script — the scripts import each other
from their own folder, so they can be invoked from anywhere.

| Task | Command |
|---|---|
| Everything, interactively | `python run_nerp_workflow.py` |
| Everything, one line | `python run_nerp_workflow.py MB52 "Material Number=SM-A137FLBHMEB" "Plant=P703"` |
| Stop after Execute | `python run_nerp_workflow.py MB52 "Plant=P703" --no-export` |
| Open a T-code only | `python search_tcode.py MB51` |
| Filter + Execute only | `python execute_filters.py "Plant=P703"` |
| Export only | `python export_to_excel.py MB52` |
| Run the tests | `python tests/test_unit.py` and `python tests/test_live_chrome.py` |

Every script sets its own proxy bypass on import, so no `$env:NO_PROXY`
preamble is needed any more.

## Steps

### Step 1 — launch Chrome with CDP

`run_nerp_workflow.py` does this itself. To do it separately:

```powershell
python -c "import cdp_common; cdp_common.launch_chrome()"
```

This force-restarts Chrome with a clean profile. **It closes every open
Chrome window**, which is deliberate (gotchas #2/#15/#22) but destructive —
pass `--keep-chrome` to `run_nerp_workflow.py`, or
`launch_chrome(kill_existing=False)`, to leave the user's browser alone.
That is safe as long as the dedicated `--user-data-dir` is not already in
use, and it is what the test suite does.

### Step 2 — open the T-code

```powershell
python search_tcode.py MB51
```

Run with `run_in_background: true` and wait for the completion notification
— it polls for the "Go" button rather than sleeping a fixed duration
(typically a few seconds, up to a ~4 minute safety cap), so it exits as soon
as the portal is ready.

After clicking Go it verifies that the screen which came up actually refers
to the requested T-code, and prints a `WARNING` if it cannot confirm that
(gotcha #22). Pass `--no-verify` to skip the check.

### Step 3 — fill filters and Execute

```powershell
python execute_filters.py "Material Number=SM-A137FLBHMEB" "Plant=P703"
```

- Zero or more `"Field Label=Value"` arguments. Each label is matched
  case-insensitively as a substring of the input's `title` attribute.
- With no arguments it just clicks Execute.
- If a label does not match, it prints the labels that **are** on the screen,
  so the right wording can be copied from the error. Add `--strict` to make
  an unmatched filter fatal instead of a warning — worth doing whenever a
  wrong result set would be worse than no result set.
- It waits internally for the result page to finish rendering (polling the
  busy indicator), so **no sleep is needed before step 4**, whatever the
  result size.

### Step 4 — export to excel file

```powershell
python export_to_excel.py MB52
```

- The argument is the T-code, used as the filename prefix. `--name Foo`
  overrides the whole base name.
- Must run after step 3, on whatever WebGUI target is currently open.
- Prints `SUCCESS: Export completed as '<name>.xlsx' (flow A|B|C)` once it
  sees the status-bar `Download ... .xlsx` confirmation.
- On any failure it saves a diagnostic screenshot next to the scripts and
  names it in the output.
- If it reports **an unrecognised dialog**, that dialog's own text is
  printed — read that rather than retrying.
- If it reports **no recognised export dialog**, the screen is most likely
  not a list at all (e.g. CO03's single-record order header). Do not guess
  further shortcuts; look at the screenshot and ask the user.
- The file lands in the SAP GUI download destination shown in the dialog
  (e.g. `Z:\<filename>.xlsx` here).

### Step 5 — the orchestrator

```powershell
python run_nerp_workflow.py MB52 "Material Number=SM-A137FLBHMEB" "Plant=P703"
python run_nerp_workflow.py MB52 "Material Number" SM-A137FLBHMEB Plant P703
python run_nerp_workflow.py                       # prompts for everything
```

Both argument styles work. Flags: `--no-export`, `--keep-chrome`,
`--no-verify`, `--strict`.

Interactive mode pauses with "Press Enter to exit..." so a double-clicked
console window does not vanish. `NERP_Workflow.bat` forwards its arguments,
and only pauses when given none.

## Testing

The portal needs corporate SSO, so the logic is verified against a mock that
reproduces each quirk deliberately — `tests/mock_nerp_server.py` is a trap
course, not a convenience fixture.

```powershell
python tests/test_unit.py           # offline: target selection, parsing, JS shape
python tests/test_live_chrome.py    # real Chrome + real CDP against the mock
python tests/test_live_chrome.py --headed    # watch it happen
```

The live suite uses its own CDP port (9555) and profile and does **not**
touch the user's Chrome. It reproduces the cross-origin iframe by mapping
two fake hostnames to loopback with `--host-resolver-rules` and forcing
out-of-process frames with `--site-per-process`.

To poke at the mock by hand:

```powershell
python tests/mock_nerp_server.py --port 8765
# then open http://localhost:8765/?flow=a   (a|b|c|icon|shiftf4|unknown|none)
```

## Known environment gotchas (all already solved by this skill)

1. **The corporate proxy blocks localhost CDP traffic.** `HTTP_PROXY`/
   `HTTPS_PROXY` point at a gateway with no localhost exception, so even
   `http://localhost:9444/...` is routed through it and blocked ("403
   URLBlocked" / Skyhigh Secure Web Gateway). `cdp_common.apply_proxy_bypass()`
   runs on import and sets `NO_PROXY`/`no_proxy`, and `get_tabs()` also builds
   an opener with an empty ProxyHandler. **Chrome's own requests go through
   that proxy too** — irrelevant for the real portal, which is allowed, but
   it means any local test server needs `--no-proxy-server` (this cost a
   debugging cycle: the mock portal came back as a Skyhigh block page).
2. **Chrome single-instance behaviour.** If Chrome is already running, a new
   `chrome.exe --remote-debugging-port=9444` invocation forwards to the
   existing instance and silently ignores the flag — port 9444 never opens.
   A dedicated `--user-data-dir` is what actually forces a new browser
   process; the force-kill is belt and braces for the stale-state problems
   in #15/#22. The test suite runs with `kill_existing=False` and a
   dedicated profile, which confirms the profile alone is sufficient.
3. **CDP websocket origin rejection.** Chrome answers the DevTools websocket
   handshake with `403 Forbidden` unless launched with
   `--remote-allow-origins=*`.
4. **A websocket held open and idle during a page load goes stale** — the
   next `recv()` times out. Close it after navigating, wait with no socket
   open, and open a **fresh** connection for each poll attempt.
5. **`element.click()` does nothing** on either the Fiori shell buttons or
   the classic WebGUI toolbar buttons (the latter are `<div>`s wired to
   mousedown/mouseup, with no click handler at all). Use the element's
   `getBoundingClientRect()` and simulate a real click with
   `Input.dispatchMouseEvent` (`mouseMoved` → `mousePressed` →
   `mouseReleased`) — `click_element_by_rect()`. The live test asserts this
   directly: a synthetic `.click()` on the mock's Go button is delivered and
   ignored, and only the dispatched mouse event opens the screen.
6. **The T-code's screen is not in the Fiori page's DOM at all.** SAP GUI
   for HTML renders it in a separate, cross-origin CDP target — an
   `iframe`-type entry in `GET /json/list` whose URL contains
   `/sap/bc/gui/sap/its/webgui`. `contentDocument` cannot reach it; open a
   websocket directly to that target. `get_webgui_tab()` does this, and
   excludes the **AppDynamics decoy** whose URL-*encoded* address also
   contains "webgui" (`.../adrum-xd...#https%3A%2F%2F...%2Fwebgui%3B...`).
   Matching the decoy makes every later lookup silently find nothing.
7. **Field ids are unstable; field labels are not.** Dynpro fields get ids
   like `M0:46:::2:34`, regenerated per screen layout. Match on the `title`
   attribute instead — that is the SAP field label ("Material Number",
   "Plant", "Storage Location") and it is stable.
8. **`Page.captureScreenshot` fails with "Command can only be executed on
   top-level targets"** when called on the WebGUI iframe's connection. It
   works on the `page`-type target, and the iframe content still shows,
   since it renders inside that page. `capture_screenshot()` handles this,
   and failures now save one automatically.
9. **Text matching hits huge ancestor containers, not the button.**
   `textContent` is concatenated up the tree, so the whole toolbar — or the
   entire screen container — "contains" the button's text and, being earlier
   in document order, wins a naive `find`. Clicking its centre lands on
   empty space: no error, nothing happens. Filter to visible, in-viewport,
   short-text elements and take the smallest area —
   `find_visible_leaf_by_text()`. **This bit three separate lookups**: the
   WebGUI toolbar buttons (found originally), the portal's "Go" button, and
   the **Execute** button, whose lookup matched the screen container and so
   never actually pressed Execute — the run then waited for results that
   were never coming. All three now share the same discipline, and the
   Execute case has a regression test.
10. **The Export-As dialog's file-name input has a regenerated id too**, so
    it is matched by its auto-populated default value (`EXPORT_YYYYMMDD_HHMMSS`)
    via regex. This is SAP's generic SALV "Export As" dialog (element id
    prefix `SAPLSALV_GUI_CUL_CONFIGURATION...`), used across most ALV
    reports, so the approach generalises well beyond MB52. Detection is now
    read-only and separate from filling the field.
11. **Different SAP list types use genuinely different export mechanisms.**
    `export_to_excel.py` tries triggers in order and identifies the dialog
    by its content:
    - **Flow A** (Ctrl+Shift+F7 on standard ALV grids like MB52; also
      Shift+F4 on MB51): "Export As" dialog, filename defaults to
      `EXPORT_YYYYMMDD_HHMMSS` → **"Export to..."** → "Enter file name to
      save" → **"OK"**.
    - **Flow B** (Shift+F7 on hierarchical/tree reports like ZRPPM400300's
      MRP list): straight to "Enter file name to save", default already ends
      in `.XLSX`, no intermediate step — fill and **"OK"**.
    - **Flow C** (Ctrl+Shift+F9, e.g. ZRMMK121040's "Split xls" list — the
      same path as List → Export → Local File): a "Save list in file..."
      format chooser first (Unconverted / Text with Tabs / Rich Text / HTML
      / Clipboard, no Excel option) → select **"Text with Tabs"** → click
      the icon-only **"Continue"** (found by `title`) → an "Enter file name
      to save" dialog whose **"Save as" dropdown** (`popupDialogFilterCbx`,
      opened via `popupDialogFilterCbx-btn`) defaults to "Text Files
      (*.txt)" and **must** be switched to **"Spreadsheet Files (*.xlsx)"**.
      Without that switch the export silently produces a tab-separated text
      file instead of a workbook. The live test asserts the `.xlsx` outcome
      specifically, so a regression here fails loudly rather than quietly.
    If a report responds to none of them (e.g. a single-record view like
    CO03's order header, which is not a list), don't guess further — a
    screenshot is saved; report back to the user.
12. **Stale, blank WebGUI targets accumulate, and list position does not
    identify the current one** — the stale one has appeared both before and
    after the live one across runs. `get_webgui_tab()` connects to each
    candidate and picks the one rendering the most content. It originally
    picked the one with the most text inputs, which is **backwards after
    Execute**: the live screen is then a result list with *zero* inputs,
    while the stale placeholder still has its one transaction-code box, so
    the stale frame won and every export step silently drove the wrong
    screen. Caught by the test suite, which saw the export connect to
    `?stale=1` while the real list sat in the other frame. Scoring on
    rendered content (element count + visible text + inputs) holds in both
    states, because a stale placeholder is a near-empty document either way.
    Both a unit test and a live test now pin this.
13. **A dialog opened by a shortcut does not always render within a fixed
    2 seconds** — a check right after `sleep(2)` reported "not found" for a
    dialog that was visible in a screenshot moments later. Poll instead.
14. **A synthetic click on a dialog's OK occasionally lands as focus-only**
    (button focused, dialog still open). Poll for the confirmation and
    re-click once partway through the window.
15. **A long-lived CDP session accumulates stale duplicate tabs** — 11+
    duplicate "N-ERP Home" tabs after one testing session, which slows
    `GET /json/list` and makes `next(t for t in tabs if t['type']=='page')`
    pick a stale tab. `get_page_tab()` prefers a tab already on the portal,
    and the orchestrator starts from a clean profile.
16. **Chaining scripts back-to-back exposes timing gaps that manual
    step-by-step calls papered over.** When a human runs each step as a
    separate call, the gap between calls incidentally lets Chrome settle; an
    orchestrator calling them in sequence hits "WebGUI iframe target not
    found" because the iframe has not spun up yet. Worse, the target can
    exist while its DOM is still empty. `wait_for_selection_screen_ready()`
    checks readyState, a visible enabled input, and a visible enabled
    Execute button — not merely that the target exists.
17. **The same shortcut means different things in different t-codes.**
    Shift+F4 did nothing on MB52 but opened flow A directly on MB51 — SAP
    status key bindings are per-transaction. This is exactly why triggers
    are tried in order and the dialog is identified by content, never by a
    fixed shortcut-to-flow mapping.
18. **Every button lookup in the export chain must poll, not just the
    first.** The follow-up "Export to..." and "OK" lookups were single-shot
    after a flat 1.5s; on MB51 that transition was slower and the run died
    with `ERROR: 'OK' confirmation button not found` while the dialog was
    still appearing.
19. **Some reports have no export shortcut at all, only a toolbar "Export"
    icon** (small icon + chevron, `title="Export"` exactly, e.g.
    `_MB_EXPORT102` on ZRPPD410200's Production Order Change History
    Report). Clicking it reveals a dropdown (Spreadsheet / Local File / Send
    / SAPoffice Folders / ABC Analys. / HTML download); "Spreadsheet" lands
    in flow A. Mouse-driven, so it is tried last.
20. **A non-zero bounding box does not mean visible.** Some dropdown widgets
    render off-screen first to measure themselves before repositioning. The
    "Spreadsheet" menu item was found and clicked at `y: -99984`, so the
    click hit nothing and the flow failed with no error — it just looked
    like "no dialog opened". Every lookup now also requires the box to
    intersect the viewport (`JS_IS_VISIBLE` in `cdp_common.py`), which
    protects the whole skill, not just that one menu.
21. **Escape is bound like Back/F3 here, not close-popup.** Pressing it to
    "close a leftover dropdown" navigated back to the selection screen
    entirely. Don't use Escape as a cleanup step; re-click Execute to return
    to the results, or take a screenshot first to see what will be
    dismissed.
22. **`taskkill /F` looks like a crash, and reusing the profile lets
    session-restore reopen an unrelated tab.** After a forced restart, a run
    landed on a stale "Stock Overview: Basic List" for a different material
    from a much earlier test — not the requested t-code at all. Every later
    step then operated on a perfectly valid but completely wrong screen with
    no error. Fix: delete the profile directory before relaunching (not just
    kill), **and** verify the screen matches — `search_tcode.py` now does
    that check, which the original only recommended in prose.
23. **A large export can sit behind a "Stop Application" indicator for well
    over 20 seconds** while SAP prepares the file, and that duration is not
    predictable from outside. A tuned timeout is guessably wrong for some
    dataset size. Since the polling loop exits the instant the target
    appears, make the safety cap generous (120s) instead of estimating.
    Apply this reasoning anywhere a step waits on SAP server-side work.
24. **The same applies in the user-visible direction.** The original fixed
    `time.sleep(30)` for the portal's initial load could be too short on a
    slow network (a hard failure) and needlessly slow on a fast one (seen
    loading in 4s). `search_tcode.py` polls every 2s, reconnecting fresh
    each attempt, with a ~4 minute cap. Don't reintroduce a fixed sleep
    anywhere just because "it usually works in N seconds".
25. **Every transition in the pipeline follows the same rule**, not just
    page load and export dialogs. `wait_for_selection_screen_ready()` covers
    search → selection screen; `wait_for_busy_indicator_clear()` covers
    Execute → results, polling the generic shell indicator
    (`hiddenLoadingToolbarButton`, part of the shell chrome rather than any
    one report) until it has appeared and gone, or never appears at all
    within a short grace window. Callers need no sleep between steps.
26. **Navigating away from `chrome://` drops the DevTools session.** A
    freshly launched Chrome shows `chrome://newtab`, a privileged WebUI
    target: `Page.enable` on it has hung, and navigating away is a
    cross-process swap that can tear the session down before the reply
    arrives — surfacing as a bare `ConnectionResetError WinError 10054` that
    aborted the whole run. Chrome is now launched with `about:blank` as its
    start page, and `navigate_page()` treats the navigate acknowledgement as
    optional, since the navigation has already been issued and the caller
    polls for the load anyway.
27. **Firing the next shortcut into an already-open modal destroys the
    diagnosis.** The original loop kept going after an unrecognised dialog
    appeared, so the final error blamed the last shortcut tried rather than
    the thing actually blocking progress. It now stops at the first
    unrecognised dialog and prints that dialog's own text.
28. **A truncating safety cap hides the answer.** Unrelated to N-ERP but
    learned on the sibling G-MES work and worth carrying here: a walk capped
    at 60 results in an application that had 206 made the target screen look
    as though it did not exist. If a cap is needed, make it generous and say
    when it was hit — a limit that silently drops results is worse than no
    limit at all.

## General notes

- Confirm success from the script's own output, then ask the user to check
  the Chrome window — there is no headless verification of the rendered
  data.
- If a field or button is not found, the WebGUI target may still be
  rendering, or the label wording differs from what was guessed. Both
  `execute_filters.py` (unmatched filters) and
  `wait_for_selection_screen_ready()` (on timeout) print the labels actually
  present, so the correct wording can be read straight out of the error.

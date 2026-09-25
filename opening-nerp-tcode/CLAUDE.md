# Operating rules for AI agents working on this project

**Read this file completely before your first tool call. Read
[HISTORY.md](HISTORY.md) before changing any automation logic.**

This project drives a live enterprise system inside Samsung's corporate
network — G-MES (Nexacro) — with real credentials, against real production
data, unattended, at night. Almost every failure this project has suffered
produced **no error at all**: a click that landed on empty space, an export
of the wrong day, a row count quietly 85 too high. The rules below exist
because of specific incidents, each recorded in HISTORY.md.

It drove a second system, N-ERP (SAP GUI for HTML), until HISTORY.md
Phase 72. That code is gone from this tree and lives on branch
`archive/nerp-before-removal`. Several rules below were learned there and
still apply — they are marked where the origin matters.

---

## 0. The G-MES engine — read this before touching G-MES code

**Decided 2026-09-13 by the project owner; the restoration this section
describes is complete (HISTORY.md Phase 57).**

**There is one G-MES engine.** It is the flat legacy code at the repo
root: `gmes_core.py`, `gmes_login.py`, `gmes_common.py`,
`gmes_open_screen.py`, `gmes_data.py`, `gmes_profile.py`, `gmes_ui.py`,
`gmes_log.py`, `cdp_common.py`. Work goes there.

**There is no second one.** A standalone `src/gmes` Python package existed
from 2026-09-12 to 2026-09-13, reached real live G-MES sessions
(HISTORY.md Phases 1-56), and was deleted after every capability inside it
was classified in [CAPABILITY_RESCUE_MAP.md](docs/history/CAPABILITY_RESCUE_MAP.md) -
two live-proven fixes (a Chrome popup-blocking flag, and correct
screenshot-tab targeting) were ported into the flat engine before deletion
and are enforced by tests; everything else was determined to be already
covered by the legacy engine, preserved as design knowledge for later, or
disproven by direct evidence. **Do not recreate it, and do not build a
second implementation of anything alongside the flat engine to "modernize"
it** - that is exactly the direction that was reversed.

- If you need something that package had: read
  [CAPABILITY_RESCUE_MAP.md](docs/history/CAPABILITY_RESCUE_MAP.md)'s Final Decisions
  table first. Most items there are marked NEEDS LIVE EVIDENCE or
  PRESERVED AS DESIGN KNOWLEDGE, not built - that is deliberate, not an
  oversight to fix reflexively.
- The package is recoverable from **git history only**: commit `59eb838`,
  or branch `archive/standalone-gmes-before-removal`. It is not, and must
  not become, a directory in this tree.
- `tests/test_legacy_entrance.py::NoStandalonePackageInTree` fails loudly
  if `src/gmes` ever exists again.

### Why this was safe to do

The removal never touched the legacy engine's own behaviour first: the
2026-09-13 migration to the package had left `gmes_core.py`,
`gmes_login.py`, `gmes_common.py`, `gmes_open_screen.py` and
`cdp_common.py` byte-identical to their last pre-migration commit
(`c6c7e8a`) - only the *entrances*, tests and docs had been rewired
(HISTORY.md Phase 56.5). Restoring the entrances and deleting the package
therefore could not regress logic that had never moved.

### The two engines were never isolated at runtime, while both existed

Worth remembering if this is ever revisited: both defaulted to **CDP port
9444** (`NERP_CDP_PORT` and `GMES_CDP_PORT` env vars, same default) and
both drove the **same Chrome profile copy**,
`%LOCALAPPDATA%\Google\Chrome\CDP Profile`. The package's own recovery code
could clear Chrome's cache, prune Nexacro `localStorage`, reload the page,
and restart the browser - changing state the legacy engine would later
find, without importing a line from it. Do not move, refresh or "clean up"
that profile - rule 2.1a still applies, and it is the profile known to
work.

---

## 1. The mandatory update rule

**Any change to behaviour requires a HISTORY.md entry in the same commit.**

Add an entry whenever you:

- fix a bug, however small
- discover how one of these systems actually behaves
- change a wait, a selector, a timeout, or an identifier
- find that something documented here is wrong

Use the existing shape — **Symptom / Cause / Fix / Lesson** — and describe
the *observed behaviour*, not your intention. "Waits longer now" is useless;
"Nexacro clears the dataset on Inquiry, so stable zeros are not a finished
query" is the whole point.

This rule exists because the original `SKILL.md` told callers to pipe data
into stdin months after the code had switched to command-line arguments. The
documentation was confidently wrong, and cost real debugging time.

If a discovery is a durable property of one of the systems (not a one-off
bug), also add it to the numbered gotchas in
[GMES_SKILL.md](GMES_SKILL.md).

---

## 2. Safety rules — non-negotiable

### 2.1 Never delete or modify the user's real Chrome profile
`C:\Users\<user>\AppData\Local\Google\Chrome\User Data` holds their logins,
extensions and history.

There are two launchers and **neither deletes anything**:

- `launch_automation_chrome()` — the supported one. Drives a profile the tool
  owns at `%LOCALAPPDATA%\GMES_Automation\profiles\…`. On the first run only,
  that profile is built by **reading** the user's Chrome or Edge profile on the
  same PC, so a G-MES session they already have comes across (Phase 75); when
  there is nothing usable to copy it is built empty exactly as in Phase 73.1.
  The copy happens once and never crosses machines — a copied profile cannot
  leave the machine it was made on (Phase 73.1). The user's real profile is
  **read and nothing else**: never launched, never debugged, never written to,
  never deleted, and `gmes_browsers.protected_match()` makes the launcher
  refuse any `--user-data-dir` that is, or is inside, a real profile of either
  supported browser.
- `launch_chrome_with_user_profile()` — copies the user's real profile to the
  protected `CDP Profile` and never deletes it. Kept as the explicit
  `--refresh-profile` escape hatch, see 2.1a.

A third, `launch_chrome()`, **deleted** its profile directory on every start.
It was N-ERP's and went with N-ERP (Phase 72.4). Nothing in this tree deletes
a profile directory any more. **Do not reintroduce anything that does.**

### 2.1a The developer's own credential store and profile copy are untouchable

**Until the owner says development is finished, nothing may delete, reset,
refresh, overwrite or "clean up" either of these on the developer's machine:**

- `%LOCALAPPDATA%\GMES_Automation\credentials.dat` — the active supported
  legacy store used by `gmes_credentials.py`.
- `%LOCALAPPDATA%\GMES\credentials.dat` — the removed donor package's
  historical store, still protected.
- `%LOCALAPPDATA%\Google\Chrome\CDP Profile` — the debuggable profile copy
  that carries the working signed-in G-MES session.

This is not a style preference. This project is under active development by
its owner, and these three protected items are what make a development run reach a
live screen at all. Deleting any of them stops the owner's work and the project
with it. **Ask first, every time, without exception** — a general instruction
to "make the code robust" or "handle a new machine" is never permission to
inspect, migrate, overwrite, normalize, or delete either credential file.
Repository cleanup never authorizes runtime-data cleanup.

New-machine, new-user and recovery behaviour must therefore be built so that
the fallback path is **additive**: create a separate clean profile, write a
separate per-machine identity file, ask a new user for their own credentials
in their own DPAPI store. Never "reset to a known-good state" by deleting
what is already there.

`launch_chrome_with_user_profile(refresh_profile=True)` re-copies over the
profile copy and destroys the session inside it. It stays available because
it is sometimes the answer, but it runs only when a person explicitly asks
for it in that run — never as an automatic recovery step.

### 2.2 Never write credentials anywhere but the DPAPI store
No passwords in source, arguments, environment variables, log lines, commit
messages, or history entries. `gmes_credentials.py` is the only store.
Reading it out and printing it is a breach, not a debugging aid.

### 2.3 Never print or commit session tokens
Dataset dumps can contain live credentials. G-MES's integrated-search form
carries `tokenId` and `refreshTokenId` - full JWTs for the signed-in
session - in an ordinary-looking `dsAnyframeDVO`. Print only the columns you
need, and never paste raw dataset output into documentation, commits,
issues or chat.

### 2.4 Never commit exported data
`.gitignore` excludes `Data Hub Folder/`, `*.csv`, `*.png`. These files
contain production plans, order numbers and quantities. If you add a new
output location, add it to `.gitignore` in the same commit.

### 2.5 Ask before acting outside a read
These systems are live. Running an inquiry is read-only and fine. Anything
that **saves, submits, approves, deletes, or changes a value in the target
system** requires explicit user confirmation first, every time. Prior
approval for one action is not approval for the next.

### 2.6 Do not kill the user's browser without warning
`taskkill /F /IM chrome.exe` closes everything they have open. **Nothing in
this project may do it.** Close only the automation browser, through its own
CDP endpoint — `cdp_common.close_browser()`. The one caller that used to
force-kill every Chrome window was the N-ERP orchestrator, removed in
HISTORY.md Phase 72.

The user's own Chrome being open is not a conflict: the automation runs on a
separate profile copy, and Chrome starts a second instance on a different
`--user-data-dir` quite happily (GMES_SKILL.md gotcha #44). Never ask anyone
to close their browser to run a report.

---

## 3. Engineering rules

These are distilled from the failures in HISTORY.md. Violating them
reintroduces bugs that have already been fixed once.

### 3.1 Never sleep a fixed duration — poll until observed
A tuned timeout is wrong in both directions: too short on a slow network (a
hard failure), needlessly slow on a fast one. A loop that exits the instant
it detects success makes a generous cap free.

```python
# Wrong
time.sleep(30)
button = find_button()

# Right
deadline = time.time() + 240          # generous cap, not an estimate
while time.time() < deadline:
    button = find_button()
    if button:
        break
    time.sleep(2)
```

### 3.2 Wait for the specific control you are about to use
Not `readyState`, not an element count, not "the target exists". Both
systems build their UI in JavaScript long after the document reports
complete. Waiting on a proxy signal produced a blank-page screenshot and a
"button not found" error while the page was still constructing itself.

### 3.3 Text matching must filter by visibility, length and area
`textContent` is inherited, so every ancestor of a button also "contains"
its label — and the outermost one comes first in document order. Clicking
its centre hits empty space and nothing happens, with no error. Use
`gmes_common.find_elements()` / `click_control()`, which apply exactly that
discipline: visible, inside the viewport, short text, **smallest box wins**.
(`cdp_common.find_visible_leaf_by_text()` enforced the same rule for N-ERP
and went with it in Phase 72; the rule did not.)

A non-zero bounding box is **not** visibility. Some menus pre-render
off-screen (observed at `y = -99984`) before repositioning. `JS_IS_VISIBLE`
also requires the box to intersect the viewport.

### 3.4 Never hardcode a generated id
Work-screen ids embed a window number that changes on every open
(`winPPM0219_0_516` → `_0_315`). Match the screen code from the breadcrumb
(`P1112WM00`), the CSS class, or the visible label.

Only shell frames have fixed ids: `topFrame`, `loginFrame`, `mdiFrame`.

### 3.5 Verify the outcome of every step
Assume nothing succeeded because it did not raise. Confirm the screen that
came up is the code requested; confirm the division the screen itself shows
is the one asked for; confirm the result rows carry the date asked for;
confirm the file actually appeared and has content. Three of the worst bugs
here were a run operating happily on the wrong screen, an export of the
wrong day, and 288 rows of the wrong division delivered in a correctly
labelled file.

### 3.6 Prefer the data layer to the screen
In G-MES, a grid only renders visible rows — reading the page silently
truncates large results. Read Nexacro Datasets (`gmes_data.py`). But
reconcile the counts: the dataset holds filler rows the grid hides.

### 3.7 Address the CDP endpoint by 127.0.0.1, never by name
On Windows `localhost` resolves to `::1` first and Chrome listens on IPv4
only, so every call waits for the IPv6 attempt to fail - 2.05s versus
0.013s, measured. That applies to the websocket URLs Chrome returns as
well; `cdp_common.ipv4()` rewrites them.

### 3.8 Save a screenshot on failure
`cdp_common.screenshot_on_failure()`. Take it on the **page** target; it
fails on an iframe target. An unattended job that fails at 02:00 leaves
nothing else to diagnose from.

### 3.9 Stop at the first thing you do not recognise
Do not fire the next shortcut into an already-open dialog, and do not guess
at further mechanisms. Report what is actually on screen. Continuing past an
unknown state destroys the diagnosis.

---

## 4. How to work on this project

### 4.1 Before changing automation logic
1. Read the relevant phase of HISTORY.md. The behaviour probably has a
   documented cause.
2. Check the numbered gotchas in GMES_SKILL.md.
3. Look at the live page with the inspection tools before writing a
   selector — never guess an id.

### 4.1a Recording, replaying and batching a screen
The procedure is [AGENT_PLAYBOOK.md](AGENT_PLAYBOOK.md) - read it before recording,
replaying or batching any UI number. **After every recording you run the bare replay
yourself, live, and read it, before calling the screen recorded** (owner's standing
rule, 2026-09-21). The detail behind it is [PROJECT_EXPERIENCE.md](PROJECT_EXPERIENCE.md)
section 18: one browser
session (every one-off command restarts it), `describe` first, read the tool's
refusals before overriding them, and replay the recording bare once before calling
it done. Per-screen notes are in section 20 there.

### 4.2 Inspection tools (read-only, safe)
```
python gmes_inspect.py              # what is on screen, every window
python gmes_find.py <text>          # search all frames by id/class/text
python gmes_dump.py "<js expr>"     # shape of any JavaScript object
python gmes_probe_nexacro.py        # the Nexacro form and dataset tree
python gmes_data.py forms           # open screens and their datasets
python gmes_data.py findcol <column>            # which dataset carries a column
python gmes_data.py read <CODE> <dataset> --limit 0   # a dataset's COLUMNS only, no rows
python -c "import gmes_common; gmes_common.capture_screenshot('x.png')"   # the open window
```
All of these attach to an already-open automation browser and start nothing. They
print rows on request - read columns, counts or min/max instead when a result holds
people (operators, employee ids); see PROJECT_EXPERIENCE.md section 21.

### 4.3 Testing
Seven offline suites. All must stay green; none needs a browser or a network.

```
python tests/test_cdp_common.py       # the shared CDP transport every run passes through
python tests/test_gmes_core.py        # G-MES decision logic
python tests/test_legacy_hardening.py # safety gates (screenshot targeting, export checks, popups)
python tests/test_gmes_workflow.py    # the interactive front end's questions and summary
python tests/test_legacy_entrance.py  # proves both GMES_Workflow.bat branches stay legacy-only
python tests/test_project_eye.py      # proves .project-eye/ and every .md agree with reality
python tests/test_browser_bootstrap.py # browser discovery and the first-run profile copy
```

**There is no mock for G-MES**, by design — it is a Nexacro application
behind corporate SSO, and the failures worth catching are live ones. Changes
are verified against the live system with read-only operations and a
screenshot. That makes the offline suites a guard on *decision logic*, not
proof that a run works: a green suite has never been evidence that the
browser half is correct.

`tests/test_cdp_common.py` deserves a specific warning. It holds the only
automated proof of two fixes that protect every run — `--disable-popup-blocking`
for the AD SSO window, and `capture_screenshot(tab=)` for diagnostic
screenshots. Those guards lived in the N-ERP suite until Phase 72 and were
nearly deleted with it because of the file's name. **Do not delete a test
file on the strength of what it is called.**

There used to be a seventh suite, `tests/test_live_chrome.py`, driving real
Chrome against a deliberately quirky N-ERP mock; it went with N-ERP in
Phase 72.

**A green test proves nothing until it has been made to fail.** Break the code it
guards (a scripted mutation, restored in a `finally`) and confirm a test goes red -
see PROJECT_EXPERIENCE.md section 21.1. New tests go into the existing seven suites;
a new test FILE is not picked up by CI or by `test_project_eye.py`.

### 4.4 Committing
- Explain **why**, with the observed symptom. The commit log is part of the
  record.
- Include the HISTORY.md entry in the same commit.
- Never commit data files or screenshots.

### 4.5 GitHub synchronization
This checkout is connected to `origin` at
`https://github.com/coolman1984/opening-nerp-tcode.git`; its shared branch is
`main`. Git uses the Windows Git Credential Manager already configured for
this Windows account. **Never copy, print, save, or ask for a GitHub token.**

When the user asks to synchronize work with GitHub:

1. Run `git fetch origin`, then inspect `git status --short --branch` and the
   ahead/behind count against `origin/main`.
2. If the remote has advanced, integrate it without rewriting history. Use a
   fast-forward pull when possible; if a merge conflict appears, stop and
   report it rather than guessing at a resolution.
3. Review and commit only the intended changes, then run the applicable
   offline tests.
4. Push with `git push origin main`, then verify that `HEAD` and
   `origin/main` name the same commit.

Never force-push, change the remote URL, alter repository visibility, or
publish credentials. A user request to push authorizes the normal push above,
not history rewriting.

### 4.6 Do not
- Reintroduce a fixed sleep "because it usually works".
- Hardcode a path under `C:\Users\<someone>`.
- Add a dependency without a strong reason — this is stdlib plus
  `websocket-client`, and that is a feature in a locked-down corporate
  environment.
- Silently truncate a result. A cap that hides data is worse than no cap;
  one capped the form walk at 60 when the app had 206 and made the target
  screen appear not to exist.

---

## 5. Environment facts

| | |
|---|---|
| Platform | Windows 11, PowerShell |
| Python | 3.12 on PATH; only dependency is `websocket-client` |
| Chrome | 152 — **refuses remote debugging on the default profile** |
| Proxy | Corporate gateway intercepts localhost; `NO_PROXY` is set on import |
| G-MES | `http://seegmes4.sec.samsung.net/mes4/sm/nexacro/index_ext_2318.html` — Nexacro |
| CDP port | **Assigned by the OS** (`--remote-debugging-port=0`), read back from `DevToolsActivePort` in the profile. `NERP_CDP_PORT` pins it instead. |
| Browsers | Chrome **and** Edge, both supported and interchangeable (`gmes_browsers.py`). The Windows default is preferred; `GMES_BROWSER` forces one. |
| Browser profile | `%LOCALAPPDATA%\GMES_Automation\profiles\default`, built once from the user's own Chrome/Edge profile on this PC, or empty if there is nothing to copy. `GMES_PROFILE_DIR` overrides; `GMES_BOOTSTRAP=off` skips the copy. |
| Credentials | `%LOCALAPPDATA%\GMES_Automation\credentials.dat`, DPAPI, per Windows account |

Two separate network paths matter: the automation's calls to the CDP
endpoint (fixed by `NO_PROXY`) and Chrome's own page requests (which still
go through the corporate proxy). The real portal must not be driven with
`--no-proxy-server`.

---

## 6. Where things are

```
cdp_common.py            The CDP transport: launch, connect, click, screenshot
gmes_browsers.py         Chrome/Edge discovery, profiles, first-run bootstrap
GMES_SKILL.md            G-MES skill + 89 numbered gotchas
AGENT_PLAYBOOK.md        How an agent records, replays and batches any UI number
HISTORY.md               Every incident, cause and fix     <- keep updated
README.md                Project overview and setup
ARCHITECTURE.md          Module map and runtime state locations
docs/history/            Documents kept for their reasoning, not their accuracy

gmes_credentials.py      DPAPI credential store
gmes_login.py            G-MES unattended sign-in + notice popups
gmes_common.py           G-MES helpers: find, click, popups, signed-in state
gmes_data.py             Nexacro dataset read/write
gmes_core.py             THE CORE: one screen, driven completely   <- start here
gmes_open_screen.py      Open any screen in the account's catalogue, by code or name
gmes_profile.py          Record/replay: what a successful run proved about a screen
gmes_ui.py               Terminal rendering only
gmes_log.py              Redacted, tee'd operation log
gmes_daily_prodplan.py   The nightly Production Plan export (a caller of core)
gmes_demo.py             Guided read-only demonstration of every lesson
gmes_report.py           CLI over the core: describe / run / find any UI number
gmes_batch.py            Batch runs: all / chosen / saved list, now or scheduled
gmes_schedule.py         Windows Task Scheduler side of a scheduled batch
run_gmes_workflow.py     Interactive front end (GMES_Workflow.bat)
gmes_library.py          Front end's offline views: report status, run history, plain errors
gmes_connect.py          First-contact / reconnaissance
gmes_preflight.py        Read-only preflight: Python, websocket-client, browser, runtime dir
gmes_inspect.py  gmes_find.py  gmes_dump.py  gmes_probe_*.py   Inspection tools

tests/                   Seven offline suites (4.3) - no browser, no network
screens_known/           Screen STRUCTURE - committed, ships to every user
screens/                 What a run here USED: division, dates, values (git-ignored)
logs/                    Redacted run logs (git-ignored)
Data Hub Folder/GMES/    Nightly output (git-ignored)
```

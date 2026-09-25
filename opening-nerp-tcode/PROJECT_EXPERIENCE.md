# Project experience: agent field guide

This is the durable onboarding narrative for the `opening-nerp-tcode`
repository — a G-MES automation project whose name is historical (it began as
an N-ERP T-code opener; see section 4).
It was distilled from the complete HISTORY.md, CLAUDE.md, SKILL.md,
GMES_SKILL.md, the implementation, the tests, and the project-related
conversation context available to the agents.

The chronological incident record remains HISTORY.md. This file is the
fast mental model: it explains what the project does, why it is shaped this
way, which tempting approaches already failed, what was actually verified,
and what remains open. Read the history when the exact incident matters.

**Sections 18-20 (added after Phase 83) are the working field guide:** how to
record a new screen, how to read the tool's refusals, what the later phases
taught, and notes on each screen recorded so far.

The project automates live Samsung enterprise systems on Windows through
Chrome DevTools Protocol (CDP). It is designed around a difficult fact:
these systems often accept an action and produce no error even when nothing
useful happened.

---

## 1. The one-minute understanding

| System | Technology | Supported work |
|---|---|---|
| G-MES | Nexacro manufacturing execution system | Sign in, open any reachable screen, discover/set controls, run read-only Inquiry, verify, export |

A second system, N-ERP (SAP GUI for HTML), was supported until HISTORY.md
Phase 72 and is now on branch `archive/nerp-before-removal`. Section 4 keeps
the lessons it taught, because most of them turned out not to be about SAP.

The safe mental model is:

1. Observe the exact control or state that will be used.
2. Use the event/data mechanism the application actually listens to.
3. Read back the result and prove it is the requested result.
4. Stop at the first unknown state and preserve evidence.

The normal operations are read-only. Opening screens, setting filters, running
Inquiry, reading datasets, and exporting are in scope. Saving, submitting,
approving, deleting, or changing business data requires explicit user
confirmation for each action.

---

## 2. Non-negotiable rules

CLAUDE.md is the authoritative policy and must be read before changing
anything. The following rules summarize the decisions that have repeatedly
prevented production mistakes.

### Safety and data

- Never delete or modify the user’s real Chrome profile. G-MES runs against a
  persistent copy and never deletes it either. Nothing in this tree deletes a
  profile directory any more: the one launcher that did was N-ERP’s and went
  with it in Phase 72. Do not reintroduce anything that does.
- Store credentials only through the Windows DPAPI-backed store used by
  gmes_credentials.py. Never put passwords in source, arguments, environment
  variables, output, logs, screenshots, commits, or documentation.
- Never print or commit G-MES session tokens. The integrated-search dataset can
  contain tokenId and refreshTokenId JWTs. Read only the needed columns.
- Never commit production exports or screenshots. Existing data/output paths
  are ignored; add any new output location to .gitignore in the same change.
- Never kill the user’s browser. taskkill against all chrome.exe closes every
  user window. G-MES closes its own automation browser through CDP.

### Engineering

- Any behavior change requires a HISTORY.md entry in the same commit, using
  Symptom / Cause / Fix / Lesson. Durable system discoveries also belong in
  the numbered gotchas in SKILL.md or GMES_SKILL.md.
- Never sleep a fixed duration. Poll the condition that matters with a
  generous cap; a sleep is acceptable only as the interval inside a poll.
- Wait for the named control that will be used, not readyState, an element
  count, or the mere existence of a target.
- Generated IDs are not addresses. Use stable labels, classes, dataset names,
  screen codes, breadcrumb codes, or fixed shell IDs only.
- Verify every transition: requested screen, active screen, applied filters,
  organization selection, result dataset, result date/value, file path, and
  file size.
- Prefer the application data layer over visible grid scraping.
- Address CDP through 127.0.0.1, never localhost, and rewrite websocket URLs
  the same way.
- Save a failure screenshot from the page target, never from an iframe target.
- Stop at the first unrecognized dialog/state. Do not guess the next action.

### Change procedure

1. Read CLAUDE.md, this guide, and the relevant HISTORY.md phase.
2. Read the relevant numbered gotchas.
3. Inspect the live page/data shape with the read-only tools before writing a
   selector.
4. Reuse shared helpers rather than creating a second wait/target/export path.
5. Add an offline regression test or mock trap when possible.
6. Add the history entry in the same change.
7. Verify the real outcome and report uncertainty honestly.

---

## 3. Browser and CDP mental model

cdp_common.py owns Chrome discovery/launch, proxy bypass, CDP target lookup,
websocket connections, JavaScript evaluation, real mouse/key dispatch,
polling, tab selection, and screenshots.

There are two network paths:

1. Python to Chrome’s local CDP endpoint. Corporate proxy variables can
   intercept it, so imports set NO_PROXY/no_proxy and calls use 127.0.0.1.
2. Chrome to the enterprise pages. Real portals use the corporate proxy.
   The mock browser alone uses no-proxy-server; do not apply that to
   production.

The target list changes during navigation:

- G-MES can have the page, ADFS/SSO windows, child popups, and many work tabs.
- Stale and duplicate targets accumulate.
- An SSO popup's own URL carries the G-MES hostname inside its `RelayState`
  parameter, so a naive substring match on the full URL can pick the ADFS
  window over the real page. Match on the host, and exclude `secsso.net`.

An idle websocket can become stale while a page navigates. Close it during
navigation and attach afresh for each poll. If a target is replaced,
re-resolve it rather than retrying the dead socket.

DOM element.click() is not a reliable enterprise action. Use a visible leaf,
obtain its current bounding rectangle, dispatch mouseMoved/mousePressed/
mouseReleased, then poll for the resulting state. A non-zero rectangle can
still be off-screen; menus have appeared at y=-99984 before repositioning.

---

## 4. Lessons inherited from the removed N-ERP pipeline

N-ERP (SAP GUI for HTML) was the project's first system and was removed in
HISTORY.md Phase 72; the code is on branch `archive/nerp-before-removal` and
its skill document, with all 28 of its numbered gotchas, is kept at
`docs/history/SKILL.md`. Its pipeline is not described here any more.

What survives is the part that was never really about SAP. These were learned
against N-ERP and every one of them has since bitten G-MES too, which is why
they are in CLAUDE.md section 3 as rules rather than in a system-specific
section:

| Symptom | Cause | Rule it became |
|---|---|---|
| A control was "found" as the whole screen and the run waited forever | textContent is inherited, so a large ancestor matches before the button and clicking its centre hits empty space | Require visible, in-viewport, short text and take the smallest box (CLAUDE.md 3.3) |
| A synthetic `.click()` did nothing | The controls listen for mouse events; some have no click handler at all | Dispatch real mouse events at the current rectangle |
| An element ID stopped working | Generated IDs are regenerated per render | Match a stable attribute - label, class, screen code (CLAUDE.md 3.4) |
| A dialog was "absent" after two seconds | Server and render timing vary and are not predictable from outside | Poll for the specific control, with a generous cap (CLAUDE.md 3.1, 3.2) |
| A button stayed focused while the dialog remained open | A click can land as focus-only | Poll for the outcome and re-click once; never count the click itself as success |
| Navigation raised a bare ConnectionResetError | `chrome://newtab` is a privileged WebUI target and navigating away can tear down the CDP session | Launch at `about:blank`; treat the navigate acknowledgement as optional and poll |
| The last shortcut was blamed for an unknown dialog | The loop kept firing shortcuts into an open modal | Stop at the first thing you do not recognise and report it (CLAUDE.md 3.9) |
| A screenshot failed on the target being driven | `Page.captureScreenshot` is top-level-only | Capture on the surrounding page target |
| A fixed 30-second wait was both too short and wasteful | Network and render time vary | Poll for the named control; a loop that exits on detection makes a generous cap free |

The last one is the load-bearing one. "A click that lands on a real,
correctly-identified element is still not proof of anything until the state it
was meant to change is checked" was learned here first, and G-MES needed it
twice more independently - for a Notice popup whose close button did nothing
(Phase 59.1) and a work-screen tab with no close control at all (Phase 60.1).
---

## 5. G-MES application model

G-MES is Nexacro, not SAP. It has real DOM controls with meaningful IDs, but
the complete result data is in Nexacro JavaScript datasets rather than the
visible grid.

The stable address is the screen code printed in the breadcrumb, for example
P1112UM00 or P1112WM00. Work-window IDs contain a changing instance suffix.
Only shell IDs are stable:

~~~text
mainframe.vFrameSet1.vFrameSet2.topFrame
mainframe.vFrameSet1.vFrameSet2.loginFrame
mainframe.vFrameSet1.vFrameSet2.mdiFrame
~~~

The top search box opens screen codes. The client-side gdsMenuList catalogue
has 1177 rows, including 809 reachable screens, with menuId, sysScreenId,
English/Korean titles, and screenSn breadcrumb ancestry. gdsMenuList_ is the
wrong catalogue: Korean-only titles and a different menu-ID series made
English searches silently empty.

gmes_core.py is the shared generic engine. It discovers a screen, applies
options, organization, dates, and filters, activates the tab, runs Inquiry,
waits for the screen’s actual dataset, verifies optional values, exports,
writes CSV, and remembers only after the complete run succeeds.

---

## 6. G-MES launch and sign-in lessons

### Chrome profile restriction

Chrome 136 and later silently ignore remote-debugging-port when launched
against the default profile, so automation must debug a separate
--user-data-dir. Since Phase 75 the tool builds its own at
%LOCALAPPDATA%\GMES_Automation\profiles\default, on the first run by copying
the Chrome OR Edge profile the employee already uses on that same PC — read
only, once, never across machines, and never again afterwards. The older
clone_user_profile() → CDP Profile path remains as the explicit
--refresh-profile escape hatch (historically about 928 MB became 338 MB after
cache exclusion). Extensions and saved logins survive a copy; session-only
cookies may not. No copy is ever deleted.

refresh-profile explicitly re-copies the real profile and warns that it
destroys the session currently stored in the copy. Chrome must be closed
before refreshing because files may be locked.

The user’s own Chrome can remain open. The automation uses a different
profile; an experiment with 30 user Chrome processes showed that the copy
can start normally. Only a stale process holding the copy should block launch.

### Authentication

G-MES has a direct ID/password form and an AD SSO button. The automation uses
AD SSO first and DPAPI credentials if an ADFS window appears. A surviving
session cookie can sign in immediately without any ADFS window, so the wait
must race:

1. signed-in user-name button;
2. ADFS window;
3. a complete timeout with neither.

direct_login() must write through the Nexacro component; setting only an
inner HTML input value looks correct but submits nothing. Read back only the
user ID and a boolean indicating that a password is set.

A stale Auth bad credentials message is not authoritative. It can be left by
an earlier manual attempt while ADFS is still about to open. Signed-in state
wins; an SSO window is completed; only a full wait with neither outcome makes
the message a real failure.

Retry only transient FAILED states (for example session expiry). A terminal
REJECTED credential failure must not be retried toward account lockout.

If a page load replaces the target socket three times, reconnect and say that
it is reconnecting. Never use except Exception: pass to turn a dead socket
into a four-minute apparent hang.

### Notice popups

The Notice popup is modal and silently swallows clicks behind it. It can
arrive seconds after sign-in and more than one may queue. Close by the
Nexacro child-window shape:

~~~text
.TitleBarControl.titlebarChildFrame .closebutton
~~~

Do not match the Korean title or a one-off ID. The Excel dialog is also a
child window, so the generic closer must run only during sign-in. On an
already-signed-in session, sweep once; do not pay the 45-second arrival wait.
Stop if repeated clicks fail to reduce the popup count.

### Blank page and localStorage quota

G-MES writes a timestamped copy of its Nexacro engine into localStorage on
each load and never removes old copies. At about 102 entries and 5 MB,
bootstrap throws QuotaExceededError:

~~~text
ready: complete   nexacro: True   app: False   divs: 3   text: ''
~~~

This is a failed bootstrap, not a slow page or HTTP outage; reload alone does
not help. prune_nexacro_cache() keeps the newest engine and removes stale
copies. One observed cleanup removed 51 copies, freed about 4.94 MB, and the
app rebuilt in under four seconds. After about 20 seconds without a named
login/session control, inspect app_is_built(), report storage, prune once,
and reload.

---

## 7. G-MES discovery and data-layer lessons

### Nexacro object walk

Child frames live in _frames, a numeric ObjectArray, not a useful frames
property. A work screen is nested in Div components and their form objects,
not a frame itself. A frame-only walk finds the shell and falsely reports no
screen.

An earlier cap of 60 forms hid a target in an application with 206 forms.
Never silently truncate a catalogue or result; a visible cap must be
generous and report when reached.

form.binds maps a control to a dataset column, such as:

~~~text
divBasic.form.divCal.form.mskDateFrom -> dsFilterDVO.paramFromDate
~~~

It is the foundation of generic filter discovery, but:

- bound Statics are outputs, not filters;
- shell forms contribute unrelated bindings;
- some screens bind no filters because code drives them;
- result grids are identified through binddataset.

Unbound visible inputs still matter and can be set by real key events.

### Screen search/opening

The suggestion list is produced by onkeyup; set_value() alone does not
search. Focus the box and send per-character keyDown/char/keyUp.

The same suggestion title appears in a detail panel and a result grid.
Text matching can hit the detail panel, which does nothing. Read
dsSearchResult, choose by data, and click the grid row:

~~~text
grdResult.body.gridrow_<n>.cell_<n>_0
~~~

Opening is not activation. Background screens accept dataset writes while
Inquiry lands on the visible screen. Activate the tab
mdiFrame.form.divTab.form.TAB_<winId> and confirm the window is visible.

### Result grids and shell leaks

The grid renders only visible rows; always read the dataset for complete data.
A screen can have several grids. The largest visible grid is a fallback, but a
second grid within 40 percent of its area is reported as ambiguous and the
caller should use --grid. An unmatched explicit preference fails with the
candidate list.

Export creates a temporary __EXCEL__ clone; it must not enter the screen
fingerprint or result-grid candidates.

Quick View is a Grid, not a Button/CheckBox option. dsWidget rows contain
menuId, sysScreenId, and quickViewId. They represent other screens, not
filters. RECORD reports the active entry and sibling screen codes but does
not click them.

WidgetFilter.xfdl.js and OrgCategory_GDS.xfdl.js can leak shell grids into
result candidates. Excluding the entire form would hide a legitimate Org
tree. The correct fix collects org-tree and Quick View dataset names across
all forms, then excludes grids bound to those names. Nexacro can resolve
binddataset through ancestor scope, so a grid-local property lookup is not
enough.

---

## 8. Filters, organization, dates, and options

### Organization

Missing Division produces “Select Search Criteria” and an empty grid with no
hard error. Trees are discovered by shape: commonName plus _checked. Org,
Prod, Fac, and Proc each can have their own tree. Choose by the tree that
contains the requested name, not by first/biggest.

The same logical tree can appear several times on one screen. Work Calendar
had three copies. tick_org() writes all copies, clears unrequested ticks when
exclusive=True, and select_org() verifies the screen’s summary label
staCategory/staCategoryOri. A dataset assignment returning without error is
not verification.

Organization matching is case-insensitive. Save/report the canonical spelling
from the tree, not the caller’s spelling.

### Dates

Dates are usually bound dataset values, not calendar-widget actions. Set the
dataset and read back the control. For unbound fields, click, Ctrl+A/Delete,
send real key events, Tab to commit, and read both focused and unfocused forms.

Names vary: paramFromDate, paramEndDate, paramToDate, planYmd, stdYm,
fromYmd, toYmd, and paramPeriod1 are all real examples. Recognize whole
words date, ymd, ym, dt, period, or day, or masked/calendar control types.
Do not substring-match: paramVendorCode contains “end” but is not an end date.

Fit the value to the field width: YYYYMMDD, YYYYMM, or YYYY as already held.
normalise_date() accepts YYYYMMDD, YYYY-MM-DD, and YYYY/MM/DD, validates a
real calendar date, and rejects partial/nonexistent dates.

If a screen has one date field and two different endpoints are requested,
stop. If several single date fields have no clear pair, set only the proven
target and warn. Do not guess.

### Left-panel options

These query decisions are not ordinary filters:

| Option family | Meaning |
|---|---|
| Org / Prod / Fac / Proc | Category tree source |
| STD / PLANT | Organization attribute |
| Including Past Org. | Include closed organizations |
| Plan Date / Create Date / similar | Meaning of the date period |
| General / Compare / OI | Search mode |
| Quick View | Different screen/result path |

Classes _Sel, Category_Sel, and ToggleSearchV2 mean selected; _Dis and
_Default mean unselected. left_options() lists them and set_option() clicks
by label then verifies the class.

Apply options before organization and dates because rebuilding the left panel
can discard earlier writes. Learned options are reapplied on replay unless
the current run explicitly names its own.

---

## 9. Inquiry, verification, exports, and row counts

### Inquiry wait

Inquiry clears the result dataset immediately, then refills it after the
server responds. Stable zero is not finished. poll_inquiry() watches the
dataset discovered for the current screen, requires a changing count, then
settling above zero. A long grace period is used before calling an all-zero
query genuinely empty.

A previous screen can leave a same-named dataset populated. Watching a
hardcoded dsMasterProdPlan once caused a log to claim 875 rows while the
second report actually returned 17; the file was right but the log was wrong.
That is more dangerous than a visible failure. The wait must be parameterized
by the current screen’s discovered dataset.

### Verification

--verify COLUMN[=VALUE] is strict and blocks export on mismatch. Without it,
date-like result columns are reported with samples, not treated as proof.
Value shape alone is insufficient: prodTime, model codes, and week numbers
can look like six/eight-digit dates. The column must also be named like a
date.

### Excel and CSV

The G-MES Excel icon opens PopupExcelExport and requires OK. Download behavior
must be configured on the same CDP connection that performs the click; a
separate connection can leave the file in Downloads.

The workbook carries the NASCA DRM marker and is readable by Excel with the
Samsung DRM client, but not by openpyxl, pandas, or ZIP readers. check_download()
therefore proves existence and a non-trivial size, while CSV is the content
evidence.

Production Plan has subtotal rows with empty poNo/masterLine. Observed:
875 dataset rows minus 85 blank-key rows equals the grid’s 790 total. The
grid adds LINE SUM and PROC SUM labels at render time. The screen-specific
nightly exporter may drop empty poNo rows. The generic exporter must not
assume that key on an unknown screen; it drops only completely empty rows and
reports both counts.

---

## 10. Profile memory and RECORD/REPLAY

gmes_profile.py remembers stable references only after a fully successful run:
open, apply, verify, query, export, and file check.

Safe references:

- screen code, title, menu ID;
- dataset/column references for dates;
- organization tree dataset and entry;
- result-grid dataset;
- learned option labels;
- last values actually used;
- command summary.

Never remember coordinates, window IDs, dataset contents, passwords, or
tokens. Coordinates are valid only for the evaluate/click moment; IDs
renumber; datasets can contain JWTs.

Fingerprint exactly what the profile relies on: bound dataset.column
identities and result-grid dataset names as sets. Do not fingerprint volatile
visibility, arbitrary control IDs, or the __EXCEL__ clone. If a referenced
control/grid vanishes or the screen shape changes, explain the drift and
rediscover; never repair a profile silently. --relearn explicitly discards it.

Remember what was used, not only what was typed. If no division was supplied,
read the organization summary immediately before Inquiry and save the
organization actually in effect. Merge non-empty new values with old so an
empty run cannot erase proven memory. Older profiles can recover values from
their proved.command string. Null profile fields require
(profile.get("field") or {}) before chaining get().

REPLAY with remembered values should show the remembered settings and ask one
Run it? confirmation. It should not re-ask every known field. A failed or
invalid run returns to the session loop. Closed stdin is different from a
blank answer; InputClosed prevents infinite re-asking.

---

## 11. User-facing G-MES workflow

~~~text
python gmes_report.py find "production plan"
python gmes_report.py describe P1112UM00
python gmes_report.py run P1112UM00 --division VD --days-back 1
python gmes_report.py run P1112UM00 --date 20260908 --verify planYmd
python gmes_report.py run P1112UM00 --set "Production Order=<value>"
python gmes_report.py run P1112UM00 --option PLANT --option "Create Date"
python gmes_report.py run P1112UM00 --dry-run
python gmes_report.py run P1112UM00 P1111UM00 --division VD --days-back 1
python gmes_report.py run P1112UM00 --manifest run.json
python gmes_report.py run P1112UM00 --close-tabs
~~~

The interactive front end (run_gmes_workflow.py or GMES_Workflow.bat):

1. asks for RECORD or REPLAY;
2. validates the screen from the local 809-screen catalogue;
3. in RECORD, opens and activates the screen before asking about controls;
4. shows result grid, date fields, other inputs, divisions, options, and
   Quick View entries actually present;
5. asks only questions supported by that screen;
6. applies options, organization, dates, and named filters in that order;
7. runs and verifies Inquiry;
8. exports and saves memory only after success.

A third answer, **B (Batch)**, runs several recorded screens together (all, a
chosen few, or a saved list), now, on a schedule, or saved for later. It shows
a plan first - what will run, for which dates, what is skipped and why - and
asks nothing further when nothing in the plan can run (Phase 83).

The narrator is a display layer, not a decision layer. Unknown log keys are
printed raw. Long sign-in work remains visible; captured output is printed on
failure. gmes_log.py mirrors stdout to ignored timestamped logs with command,
Python version, questions, answers, durations, warnings, and tracebacks,
while excluding passwords and raw datasets.

---

## 12. Module map

| Module | Responsibility |
|---|---|
| cdp_common.py | CDP transport, browser/profile launch, proxy bypass, input events, target selection, screenshots |
| gmes_browsers.py | Chrome/Edge discovery, default-browser detection, profile enumeration, first-run profile copy (Phase 75) |
| gmes_credentials.py | DPAPI credential storage |
| gmes_common.py | G-MES target, readiness, cache pruning, controls, login state, popups |
| gmes_login.py | AD SSO/direct login, retry classification, status |
| gmes_data.py | Nexacro forms/datasets and safe CSV |
| gmes_open_screen.py | Catalogue search, key events, suggestion row, tab activation |
| gmes_core.py | Generic screen discovery, options, trees, dates, filters, Inquiry, verification, export, profiles |
| gmes_profile.py | Stable references, fingerprints, drift, remembered values |
| gmes_report.py | Generic find/describe/run CLI |
| gmes_batch.py | Batch runs: selection, date policy, plan, isolated run, reports, saved lists, CLI (Phase 83) |
| gmes_schedule.py | Windows Task Scheduler side of a scheduled batch (Phase 83) |
| gmes_daily_prodplan.py | Screen-specific nightly Production Plan wrapper and subtotal cleanup |
| run_gmes_workflow.py | Guided RECORD/REPLAY questions and narration |
| gmes_ui.py | Terminal presentation only |
| gmes_log.py | Timestamped stdout/traceback log |
| inspection/probe scripts | Read-only reconnaissance and diagnosis |

The nightly job and generic runner must share core mechanics. Duplicated
inquiry waits, target selection, or export logic have repeatedly drifted and
caused wrong counts.

---

## 13. Chronological experience

This timeline preserves the important experience of every phase in
HISTORY.md **up to Phase 28**. Phases 29-83 are summarised by theme in
section 19, and how to record a screen is section 18. The exact incident
evidence remains in HISTORY.md.

### Phase 0: original N-ERP

The original skill knew proxy bypass, real mouse input, cross-origin WebGUI
targets, stable labels, and polling, but did not yet guarantee them.

### Phase 1: audit

The project initially could not run because the active Python lacked
websocket-client; docs pointed at another user’s absolute path; docs
contradicted the newer CLI parser; and configuration/chrome discovery was
duplicated. The response was requirements.txt, an actionable import error,
portable docs, and one cdp_common source of truth.

### Phase 2: N-ERP rebuild

The Execute ancestor-match bug, stale-frame heuristic, navigation reset, and
unknown-dialog continuation were fixed. Shared message IDs, named JS errors,
broad connection retries, screenshots, and real T-code verification followed.
The central lesson: a heuristic must be tested in every screen state.

### Phase 3: test harness

The mock portal proved that synthetic click is ignored while dispatched mouse
events work, and that the live frame wins after Execute even with zero inputs.
It also separated Python proxy behavior from Chrome page-request behavior.

### Phase 4: G-MES connection

Chrome’s profile restriction forced the persistent profile-copy design.
Reconnaissance established real DOM controls and no canvas.

### Phase 5: unattended login

DPAPI credentials, a non-interactive Tk prompt, named readiness controls,
delayed Notice watching, class-based popup close, and user-name signed-in
detection were added. The absence of a login button is not sign-in evidence.

### Phase 6: Nexacro data

Visible grids were proven incomplete. Correct _frames/nested-Div traversal
reached datasets. The 60-form cap was removed. Window IDs and breadcrumb
screen codes were separated.

### Phase 7: nightly export

Production Plan taught the project that Division is mandatory, dates are
bound dataset values, Inquiry clears the dataset before refill, Excel is a
dialog, download behavior is connection-scoped, workbooks are NASCA DRM, and
85 blank-key rows are LINE SUM/PROC SUM subtotals. CSV became the readable
evidence.

### Phase 8: any screen

The real gdsMenuList catalogue and ScreenID search were found. Search needed
key events; the suggestion grid row—not the detail panel—had to be clicked;
the active tab had to be confirmed. Session-restored sign-in and clear
connection-refused errors were handled. Two fixes were once shipped without
history entries; that process failure is itself recorded.

### Phase 9: demo

Fourteen read-only steps proved directory, screen activation, date writes,
organization selection, dataset/grid reconciliation, and changing IDs. The
popup demo step may report zero after sign-in already closed the notices.

### Phase 10: generic runner

form.binds made screens self-describing. Bound Statics/shell forms were
excluded, unbound controls were surfaced, dates became vocabulary-driven,
grid ambiguity became explicit, and the hardcoded Production Plan inquiry
wait was removed. Screens run sequentially because tabs, export, and popups
are global.

### Phase 11: interactive workflow

Left-panel options became explicit query decisions. The module bar was mapped
but not automated because ScreenID is the stronger universal path. Generic
CSV stopped guessing poNo. Session expiry was separated from rejection.

### Phase 12: real-user defects

Persisted stale filters caused a confident empty result. Case-insensitive
division matching and strict date normalization fixed input hazards. Division
was explained as a different mechanism from --set. Blank rows were confirmed
as subtotal rows.

### Phase 13: performance

The unnecessary popup vigil and IPv6-first localhost resolution made a
signed-in run take about 58.9 seconds. One-time popup sweep and IPv4 CDP
addressing reduced login to about 2.1 seconds and startup to about 0.41
seconds. A bulk edit briefly introduced an undefined empty_grace variable.

### Phase 14: core

Unbound controls became typeable; date detection covered planYmd/stdYm and
widths; grid ambiguity and organization-tree selection were generalized;
screen opening waited for forms; Inquiry waiting was consolidated;
reason-aware sign-in retry, dry-run, verify, manifest, and close-tabs were
added. Offline core tests reached 37.

### Phase 15: memory

Date ranges and profiles were added. Profiles remember stable references, not
coordinates, IDs, values, or tokens. File existence and minimum size became
download verification. Browser-driven replay was explicitly marked
unverified until live evidence existed.

### Phase 16: first live failure

A visible login refusal was ignored for 90 seconds and retried. Outcomes
became OK, transient FAILED, or terminal REJECTED. Credential rejection is
not retried. The apparent account problem later proved to be a different
manual login path.

### Phase 17: live core success

Real dates produced DRM Excel/CSV. Status crashed under a traceback, shell
panels leaked into candidates, and volatile controls made profiles erase
themselves. All were fixed; offline tests reached 49.

### Phase 18: narrated front end

RECORD/REPLAY became visible, remembered tree preferences were consulted,
Production Plan by Model proved a different date naming, date-like outputs
required date-like names, and prompt numbering stopped skipping.

### Phase 19: management demo

Silent sign-in looked hung, Windows Terminal made process handles useless,
and a focus guard protected the user’s foreground window. The demo exposed
that earlier automatic sign-in evidence was session reuse, not proof of a
stored-password login.

### Phase 20: false rejection

A stale login message was treated as authoritative before ADFS appeared.
Signed-in state was restored as primary. refresh-profile was documented as
session-destroying; browser close moved to CDP; SSO target matching improved;
and manual assist was added. A page message is evidence, not a verdict.

### Phase 21: screen-first recording

RECORD now opens the screen before asking questions and shows its actual
filters, inputs, divisions, options, and Quick View. Dead sockets reconnect,
popup loops stop on no progress, and the mode is selected explicitly.

### Phase 22: blank bootstrap

The blank spinner was diagnosed as localStorage quota exhaustion, not a
network failure. Automatic pruning/reload was added. The guard that rejected
any open user Chrome was removed after proving the separate profile works.

### Phase 23: reusable session

One run returns to a session loop. Invalid screen codes are checked against
the 809-screen catalogue before browser work. Profiles began remembering
values. Closed stdin became distinct from a blank answer.

### Phase 24: replay and logs

REPLAY stopped asking for known values and became a one-confirmation path.
Older command strings recover values. Timestamped logs capture questions,
durations, warnings, and tracebacks without secrets.

### Phase 25: deliberate user challenge

A hand-selected MOBILE tree remained while a remembered VD replay wrote only
one of three tree copies. The tool claimed VD and exported MOBILE data. All
copies are now written and the summary label is verified. Empty runs cannot
erase proven memory; the complete division list is shown.

### Phase 26: remember what was used

Memory stores the organization in effect immediately before Inquiry rather
than the caller’s typed string. Null profile values no longer crash replay.
Duplicate confirmations were consolidated.

### Phase 27: Quick View

Quick View was discovered as a data-shaped Grid pointing to other screens and
is now reported during RECORD. Shell grids are excluded by dataset shape.
The grid-local dataset lookup failed for ancestor-scoped dsWidget; collecting
dataset names across all forms fixed it.

### Phase 28: complete RECORD

Unbound controls are listed with label, control name, current value, and a
set example, wrapped to terminal width. A loose overflow line had hidden
exactly the information needed to learn a new screen.

---

## 14. Tests, evidence, and honest limits

Known commands:

~~~text
python tests/test_cdp_common.py
python tests/test_gmes_core.py
python tests/test_legacy_hardening.py
python tests/test_gmes_workflow.py
python tests/test_legacy_entrance.py
python tests/test_project_eye.py
python tests/test_browser_bootstrap.py
~~~

Seven offline suites, none needing a browser or a network. They cover
discovery, dates, grids, fingerprints, profiles, remembered values, generated
JavaScript, export and batch safety, the run lock, the interactive front
end's questions, both launcher branches, the shared CDP transport, and the
browser-neutral first-run bootstrap (Phase 75).

**There is no G-MES mock**, by design, so live browser behaviour remains
environment-sensitive and a green suite is never evidence that a run works.
A seventh suite drove real Chrome against an N-ERP mock and went with N-ERP
in Phase 72.

`tests/test_cdp_common.py` carries a specific warning: it holds the only
automated proof of `--disable-popup-blocking` and `capture_screenshot(tab=)`.
Those guards sat in the N-ERP suite until Phase 72 and were nearly deleted
with it purely because of that file's name.

At Phase 83 the seven suites hold 739 tests (51 + 367 + 119 + 36 + 10 + 12 + 144).
The number is a snapshot; what matters is that each guard was broken on
purpose and a test went red - section 21.1.

Do not infer live proof from unit tests for a new screen, a new export dialog,
unseen close-tab control, profile refresh/session state, or DRM workbook
contents. The user’s Excel/DRM client is required to inspect workbook
contents.

---

## 15. Open items

1. The DRM xlsx has not been opened by automation and cannot be parsed by
   normal libraries; the user must verify its contents in Excel.
2. The popup closer could close an Excel child dialog if called outside the
   sign-in phase; current safety depends on the calling convention.
3. ~~No scheduled trigger~~ - closed in Phase 83 (`gmes_batch.py schedule`);
   but two of the fixes it forced are not yet re-verified live, and a schedule
   runs only while the user is signed in to Windows (HISTORY.md Open Items 46).
4. Session-only cookies may require occasional interactive sign-in in the
   profile copy.
5. The demo popup step can report zero after sign-in already closed notices.
6. A `/`-separated value shaped like a fraction (`"1/2"`) can still collide
   with a bare `"12"` in `values_match()`; `/` cannot be excluded the way `.`
   was, because real dates depend on it (Phase 68.1).

Closed items include ScreenID opening, transient sign-in retry, false
credential-rejection diagnosis, live core execution, cache-quota recovery,
stale-tree confirmation, Quick View discovery, left-panel checkbox state
(Phase 69.1), the concurrent-run lock (Phase 70.1), and closing a tab with no
close control (Phase 60.1).

Two former items were closed by **removal rather than fix** when N-ERP went
in Phase 72: the never-completed clean N-ERP live run, and N-ERP's export
verifying success by a status-bar text match with no filesystem check. Both
defects are still present on `archive/nerp-before-removal`.

HISTORY.md's own Open Items table is the authority; this list is a summary.

---

## 16. Conversation continuity

The project-related chats that led to this guide are part of the preserved
context and should be understood as follows.

### Codex CLI update check

The user asked whether Codex CLI had an update. The local command reported:

~~~text
codex-cli 0.153.4
~~~

The official Codex CLI documentation page displayed an older example version
in its sample terminal, so the local installed version was already newer than
that documentation example. The current official CLI guidance describes
interactive repository work, codex exec automation, reviews, session resume,
image context, web search, cloud handoff, MCP, skills/plugins, subagents,
permissions, and shell completion. An example version shown in docs is not a
reliable update check; the local version command is the useful evidence.

### Concurrent Claude CLI agent

The user asked whether a Claude CLI agent was visible. Windows denied access
to the process list through both the CIM and tasklist attempts, so no claim
about a running Claude agent could be made. The correct lesson is to state
the visibility limitation, not infer that no agent exists.

### Read-only project review

The user then asked for a read-only understanding of the project. The review
read the operating rules, README, HOW_TO_USE, both skills, all 2039 lines of
HISTORY.md, the core workflow modules, and test/function maps. No project
logic was changed during that review. At that time the worktree contained
uncommitted changes in HISTORY.md, gmes_ui.py, run_gmes_workflow.py, and a
new tests/test_gmes_workflow.py; those were left untouched because they could
belong to the concurrent Claude work.

This document is the requested durable follow-up to that conversation: it
turns the chronological chat/history knowledge into something a future agent
can read once and carry forward.

### Complete project-chat source inventory

The project’s chat archive is the untracked Doc directory. I read the six
JSONL session logs there and folded their substantive decisions and failures
into this guide. The raw logs are not copied into the markdown because they
contain local paths, screenshots, production examples, and potentially
sensitive browser/session context.

| Chat log | Recorded focus |
|---|---|
| d52aec41-2112-4be0-9038-10b8145aea80.jsonl | The original deep dive: N-ERP rebuild, first G-MES connection, DPAPI sign-in, Notice popups, Production Plan, data-layer discovery, ScreenID navigation, demo, universal runner, and the first generic-core design |
| ee759ace-8676-4e24-b647-64e320a1dfac.jsonl | Follow-up project-deep-dive continuation and unfinished work carried into the next session |
| 285bac09-2dad-4f6f-a962-a2b16a358bf5.jsonl | G-MES core architecture, manual-date V1, dry-run, monitoring, polished visible CLI demo, saved login, RECORD/REPLAY, persistent memory, invalid UI handling, the deliberate MOBILE-vs-VD challenge, bug report, sync, and recovery from a wrong Chrome profile |
| f93f3870-017b-45e0-bf82-86351eb9fcad.jsonl | Discovery of the new Quick View case that the first RECORD summary omitted |
| 1e5cf169-26c3-40cd-8d34-b6b237ff81c6.jsonl | Project overview, final documentation awareness, and request to fix/sync to main |
| c67687dd-819c-4918-9688-2d1cf4fa86a7.jsonl | Questions about seeing or connecting to another CLI agent and the read-only project review |

### User intent and product requirements found in the chats

The user’s long-term request stayed consistent even when individual
debugging requests changed:

- Give the tool a G-MES UI number and only the values the user actually
  cares about, such as Division and a manual From/To range.
- Open the correct screen, inspect that screen, find its own controls, apply
  only the requested values, verify each application, run Inquiry, wait for
  the real answer, download Excel, verify the file, and remember only proven
  screen knowledge.
- Keep V1 deliberately small: manual dates first. Automatic “today”,
  “yesterday”, and date arithmetic belong after the manual workflow is stable.
- Prefer one shared automation philosophy/core with per-system adapters, not
  two unrelated engines. *(Stated when the project carried both systems.
  Overtaken by events twice: the `src/gmes` package was an attempt at a
  second engine and was removed in Phase 57, and N-ERP itself was removed in
  Phase 72. There is one engine and one system now.)*
- Record a screen once when useful, replay it quickly later, detect drift,
  and fall back to rediscovery instead of blindly replaying old actions.
- Save semantic identities and relationships, not raw coordinates or
  unstable IDs. The user explicitly asked for an always-remembering JSON
  store, but the chats clarified that it must never store credentials,
  tokens, or unsafe volatile state.
- Make the CLI simple, elegant, visible, and understandable to a
  non-technical person. RECORD must show what it discovered; REPLAY must show
  what it remembers and avoid re-asking known values.
- A completed run must return to a choice of RECORD or REPLAY instead of
  closing the session. Invalid UI numbers, filters, divisions, and dates
  must be rejected locally and re-asked with a useful explanation.
- A management demo must be visible side by side with the browser, narrate
  progress, and never appear frozen while sign-in or server work is ongoing.
- The tool should support several UI numbers, but G-MES reports must run
  sequentially in one application because only one tab is active, exports
  and modal popups are global, and parallelism would multiply browser
  profiles and sign-ins.
- Before shipping, the user asked for categorized bug/logic/edge-case
  reports, testing, history/skill updates, and synchronization with main.

### Communication lessons from the chats

The user is not a browser-automation engineer and repeatedly asked for
concise, plain-language progress. Future agents should report, in this
order:

1. What changed.
2. What worked, with evidence.
3. What failed, with the actual cause if known.
4. What the user should test next.

For a visible demo, narrate phases while they happen. For unattended work,
keep logs and screenshots detailed even if the user-facing summary is short.
If an Arabic keyboard layout produces unreadable text, confirm the intended
command before acting. If another agent or process cannot be observed because
Windows denied process access, say so instead of inferring that it is absent.

---

## 17. Safe workflow for the next agent

When extending or repairing the project:

1. Identify the scope: G-MES screen logic, CDP transport, presentation,
   profile memory, or tests.
2. Read CLAUDE.md, this guide, and the relevant history phase.
3. Search existing code and gotchas before inventing a selector or timeout.
4. Use read-only inspection: gmes_inspect.py, gmes_find.py, gmes_data.py,
   gmes_probe_nexacro.py, gmes_dump.py. Avoid sensitive dataset dumps —
   `dsAnyframeDVO` carries live session JWTs.
5. Reuse cdp_common, gmes_common, and gmes_core helpers.
6. Make the smallest evidence-based change.
7. Add a regression case when possible.
8. Add Symptom / Cause / Fix / Lesson to HISTORY.md in the same commit.
9. Verify screen, active tab, read-back controls, dataset change, requested
   result value/date, file existence/size, and failure screenshot.
10. Report what was proven and what remains an assumption.
11. Recording, replaying or batching a screen: follow AGENT_PLAYBOOK.md (the order of
    work and the judgement) and section 18 (the reasons): one browser, describe first,
    then YOU run the bare replay yourself and read it.

The project’s most useful question is:

> What did the system actually prove, and what am I merely assuming?

That question is the experience this file is meant to preserve.


---

## 18. Field guide: recording a new screen

Written after Phase 83, from recording about twenty screens live with the project
owner. Everything here was learned from a real refusal or a real wrong answer;
the incident is named so it can be read.

### 18.1 The method

1. **Resolve the code.** The catalogue is live only (no offline copy), so a
   search signs in. Search by prefix (`gmes_report.py find R322`) to resolve
   several codes in one go. A code ending `WM00` is a *work form*; check its
   menu id - `R3225WM00` and `R3220UM00` both live under menu `FFM0521`, i.e. the
   same screen, and recording both duplicates work.
2. **One command is one browser.** Every `gmes_report.py` command starts a
   browser, signs in, does one job and closes it. Chaining many one-off commands
   means many restarts and many sign-ins. Do the whole session as few commands as
   possible; `--keep-open` on the first lets later ones reuse the browser
   (`Browser: already running`) - but **only the command that started the browser
   closes it**, so end the session by closing it through its own endpoint
   (`cdp_common.close_browser()`), never a blanket kill (CLAUDE.md 2.6).
   The guided app (`GMES_Workflow.bat`) and Batch already keep one browser for
   many screens.
3. **Do not sign in over and over to re-test.** After a handful of sign-ins in a
   short time the AD SSO window stopped opening (Phase 83.2). The tool then
   refuses, correctly, to submit the saved password. Stop and wait rather than
   loop.
4. **Describe first (read-only).** `gmes_report.py describe CODE` shows the
   grids, the filters (`<- date` marks a date), the division trees and the
   left-panel options, and writes nothing. Read it for the five questions in 18.2.
5. **Run the standing recipe:** division (usually VD) + yesterday, Inquiry,
   Excel + CSV, save the profile:
   `gmes_report.py run CODE --division VD --from YYYYMMDD --to YYYYMMDD
   [--option Daily] [--grid NAME] --verify COLUMN`.
6. **Replay it bare, once:** `gmes_report.py run CODE` with nothing else. A
   recording is only done when it replays (Phase 83.5: a screen recorded with
   `--grid` could not be replayed until the replay path was fixed).
7. **Check the batch sees it:** `gmes_batch.py plan CODE` should say `ready`.
8. **Write down anything new** in HISTORY.md (Symptom / Cause / Fix / Lesson) and,
   for a durable trait of the screen, in 20 below.

### 18.2 The five questions `describe` must answer before any run

| Question | How to tell | If it goes wrong |
|---|---|---|
| Which grid IS the report? | Look at a screenshot after Inquiry, not at row counts. The report is the table that fills with the requested day. | A populated grid beside an empty one is not proof: R3220's 37-row "Formula" legend was populated from the start (Phase 82.21); B3320's Detail grid stays empty until a drill-down click. Pass `--grid` only on evidence. |
| Is the period a date or a month? | The Period control's mode buttons (Daily / Weekly / Monthly). | B3320 defaults to **Monthly**; "yesterday" would return the month. `--option Daily`. |
| Is there a division, and which? | `Category trees` in `describe`. | B3320's tree holds only `SEEG-P` - no VD (Phase 83.3). Ask the owner which scope; do not substitute silently. |
| How can the date be verified? | A result column carrying the date -> `--verify COLUMN`. | The date may sit **inside a value** (`jsonObj` keyed `"20260919"`, Phase 83.4) or in headers only; then `--verify` that JSON column. If the date exists nowhere in the data, there is nothing to verify: say so, do not fake it. |
| Is it a live monitor? | No date field; a refresh timer (`Renewal Cycle (minutes)`). | Nothing to select by date: R3224WM00 is a snapshot of *now*. Record it without a date and say so. |

### 18.3 Reading a refusal (the tool refuses far more often than it lies)

| The tool says | What it means | What to do |
|---|---|---|
| `a date-constrained run needs --verify COLUMN` | No way was named to prove the returned day. | Find the date column (or JSON date key, 83.4). Never pass a guess just to get past it. |
| `more than one plausible result grid: A(dsX), B(dsY)` | Two comparable grids on **different** datasets. Several grids on one dataset are not rivals (82.22). | Screenshot, pick by evidence, `--grid`. |
| `VD is not in any category tree on this screen. Present: [...]` | The screen offers a different scope. | Ask; the usual fallback is the one node present (`SEEG-P`). |
| `the screen no longer matches what was asked for, right before Inquiry` | Final Intent Verification read a filter that differs from what was set. | Look at the screenshot. Real drift -> fix the cause. Screen looks right -> the tool read the wrong source (83.3: Period shows the date, dataset empty). |
| `typing into X did not take - it shows '9196-0_-__'` | A masked date field took scrambled keys. | The tool retries with a longer settle (83.2); if it still fails, the read-back is doing its job. |
| `the query returned no rows` (and `another grid DOES hold rows`) | Either genuinely no data (P3131UM00 has none on Fridays) or the wrong grid. | Read the "another grid" hint and the screenshot before believing either. |
| `WinError 32 ... being used by another process` on the export rename | The DRM agent / antivirus / browser still has the fresh workbook open. | Handled by `replace_when_free()` (83.2); a persistent failure is real. |
| `The Samsung SSO window never opened` (twice) | Sign-in did not complete; the password was deliberately NOT submitted. | Stop. Wait. Check for a session open elsewhere. Do not `--allow-password-login` unless sure the password is current. |
| `The search returned nothing for 'X' (typed twice; ...)` | The top search box gave no result twice. On a cold profile the first search after sign-in is lost (lazy search panel, 84.3); the tool already retries once, so seeing this means both tries failed. | Open the screen by hand once, then retry; note whether the panel said `popup not created`. |
| `The connection to the automation browser was lost while the run was in progress` | The browser or its tab closed, crashed or was replaced (a person, a crash, security software) - the message names the exception and the CDP method, not a guessed cause. A batch restarts it and continues (max 2); a single run just ends. | Do not close the automation window during a run; if it keeps happening, look at the failure screenshot and the log. |
| `Could not attach to the G-MES tab after 4 attempts` | The tab is hung (`Runtime.enable` unanswered) though the browser is alive. Sign-in restarts the automation browser once by itself (84.18). | If it still fails after the restart, close the automation window by hand and run again; never swap the tab from outside - that closes the whole browser. |
| `... did not open within 90s` for a `...WM00` code | Before 84.2 this was the false error for work-forms; after it, the screen really did not open. | Look at the screenshot; check the code against `find`. |
| `these profile files could not be used and were skipped` | A file in `screens/` is empty, not JSON, not a profile, or not UTF-8 (84.7). | Open it in an editor; re-record the screen if it is damaged. |
| `an old run lock was removed: ...` | A previous run died without releasing the lock (84.13). Informational. | Nothing - unless it happens every run, then something is killing runs. |
| `the saved credentials ... cannot decrypt them` | The sign-in file exists but this Windows account cannot read it (password reset, another PC/user) (84.14). | `python gmes_credentials.py set` as this user. |
| `more than one workbook` / `unchanged result` warning | The result did not move after Inquiry. | Suspect a static table; the result is not saved to the profile (82.21). **First ask whether an earlier run in the same browser left the result on screen** (a probe): close that window with `run ... --close-tabs` (`describe --close-tabs` does not close it) and record again from a fresh screen (83.7). |

### 18.4 Working principles that paid for themselves

- **A refusal is evidence, not an obstacle.** Read what it read before overriding
  it. Twice the tool was right (a wrong grid, an empty verify column) and twice it
  was reading the wrong thing (Phase 83.3, 83.5). Both are found only by looking
  at the screen and at what the tool read.
- **Do not override a default without evidence.** The one time a default grid was
  overridden by hand it exported a legend as the report (Phase 82.21).
- **Recorded is not replayed.** Replay every new kind of screen once, bare.
- **Run the real thing through the real scheduler once.** Four defects appeared
  only under Task Scheduler (83.2).
- **Say what was not proven.** HISTORY entries carry a "Not established" line; the
  fix for a cause that was inferred is described as a defence, not a cure.
- **Never save what is suspect.** A result that did not change after Inquiry, or
  that could not be verified, is not written to the profile.

---

## 19. What Phases 29-83 taught (the part sections 1-13 predate)

Sections 1-13 were written at Phase 28. The incident record for everything after
it is HISTORY.md; this is the same material by theme, so the lesson can be found
without reading 8,000 lines.

| Theme | What was learned | Where |
|---|---|---|
| **One engine** | A standalone `src/gmes` package was built, reached live sessions, then deleted; the flat legacy engine is the only engine. Capabilities were classified, not lost. | Phases 29-57, CLAUDE.md 0, `docs/history/CAPABILITY_RESCUE_MAP.md` |
| **N-ERP is gone** | Removed in Phase 72; code lives on `archive/nerp-before-removal`. | Phase 72 |
| **External reviews** | Each pasted review was verified claim by claim, never trusted or dismissed wholesale; the real findings were fixed with tests (Phase 66: four of four; Phase 68: nine). | Phases 54, 55, 64, 66, 68, 78 |
| **Identity of a dataset / window** | A write must land on the exact window's dataset (exact form path), or it lands on another window's copy of a reusable component and reports success. | 80.1, 80.4, 82.1, 82.2, 82.8 |
| **Verify before Inquiry** | Final Intent Verification re-reads every filter/option/division immediately before the click. | 80.3, 83.3 |
| **Sessions and sign-in** | One session per account; the login page has its own popup family (`UserIpCheck`); a failed corporate sign-in is not a failed password; the first run copies (read-only) the employee's own browser profile; the tool never submits a password on an unclear result. | 73, 74, 75, 77, 81.3, 82.14, 82.15, 83.2 |
| **Restoring tabs** | Chrome switches are presence-based: `--restore-last-session=false` REQUESTED a restore, and every launch reopened every earlier G-MES tab. | 82.17 |
| **Options and profiles** | An option is remembered by its Nexacro `name`, never its label (UI language changes); a shipped profile carries structure, not someone's values. | 48, 76, 79.6 |
| **Replay without retyping** | A freshly recorded screen must replay itself from its profile alone. | 82.9, 83.5 |
| **Results are not always where they seem** | Grids can re-bind after the query; several grids can share a dataset; static legends look like results; tabs hide grids; the date can be inside a value. | 82.11, 82.12, 82.18, 82.21, 82.22, 83.4 |
| **Silent truncation** | The form walk fit only three windows; catalogue counts were capped and reported as true; every cap now says so. | 82.5, 82.20 |
| **Empty and stale results** | A stale non-zero count clearing to zero is not a confirmed empty result; an unchanged count after Inquiry is a warning. | 71, 82.16, 82.21 |
| **Exports** | The Save-to-Excel dialog can be slow; a "Notification: completed" popup blocks the next screen; the `.xlsx` is DRM-encrypted (the CSV is the readable evidence); the fresh file can be locked. | 82.10, 82.13, 83.2 |
| **Batch and schedule** | Isolation per screen with health checks; a plan before any browser; date policy applied to typed dates too; Task Scheduler for the current user only while signed in; unattended runs need an unbuffered log and must stop the browser on every exit. | Phase 83 |
| **Documentation discipline** | A HISTORY entry in the same commit as the change; "not established" stated plainly; counts in docs (`67 numbered`) drift and must be re-checked. | CLAUDE.md 1 |

---

## 20. Recorded screens: field notes

Only traits that surprised someone. Values, row counts and production data are
deliberately absent (CLAUDE.md 2.3, 2.4). Structure of shipped screens is in
`screens_known/`; what a run on this machine used is in the git-ignored `screens/`.

| Screen | Notes |
|---|---|
| **P1112UM00** Production Plan | The reference screen: `planYmd` verifies the day; feeds the nightly job. |
| **P3131UM00** On-Time/Fixed Q'ty | Nine grids on one dataset (three category tabs x three views) - one result, not nine rivals (82.22). Two real datasets: the detail grid and a progress summary. Genuinely returns no data on some days (Fridays seen). Only the Main category was recorded. |
| **R3220UM00** Operation Analysis (inner form `R3225WM00`, menu `FFM0521`) | Result is `dsGrpSummary`/`grdSummary`, which builds its columns only after Inquiry. `dsOperAnalCalc` is a static 37-row formula legend - never the report (82.21). Period is two unbound masked boxes, typed (`mskFromDate`/`mskToDate`), so the date is **not** checked against rows; masked typing can race (83.2). |
| **R5216UM00** Mounter Drop Analysis | `grdDetail` re-binds from a one-column temp dataset to the real one after the query (82.18). Dates typed. |
| **B3320UM00** SMD Equipment Operation Efficiency | Defaults to Monthly (`--option Daily`). Org tree holds only `SEEG-P`. Result is the summary (`grdPerson`/`dsData`); the Detail grid fills only after a drill-down click. Date is inside `jsonObj` keys -> `--verify jsonObj` (83.4). Period boxes display the date while the dataset is empty (83.3). |
| **R3224WM00** Equip. Operation Monitoring | A live monitor: no date field, 5-minute auto-refresh. A snapshot of *now*, no date to verify. `grdGuide` beside it is a legend. |
| **Q2111UM00** | Heavy export (wide result); the Save-to-Excel dialog can be slow (82.13). |
| **M3912UM00** | A "Notification: completed." popup follows the download and blocks the next screen unless closed (82.10). |
| **M1642UM00** Certification Status | One grid, per-process certification counts. Period is a **month** range (`startDt1`/`finDt1` = `YYYYMM`; yesterday means its month). Every date column in the result is empty, so the period cannot be verified - recorded with `--set`. A probe run that leaves a result on screen makes the next run look like static content (83.7). Rows describe operators - do not print them. |
| **Q3212UM00** Specialization Inspection (menu `MQM0170`) | `grdInsp` (54 rows, "Inspection Info.") is the report; `grdInspDtl` is a drill-down, stays empty. No date in either dataset: typed, not verified. |
| **Q3341UM00** Outgoing Holding/Clear Mgmt. (menu `MQM0079`) | `grdStopRevokeList` is the report. `outStopRegDt` is a `YYYYMMDDHHMMSS` timestamp - confirmed on screen as the requested day, but `--verify` cannot match it exactly (84.24); recorded with `--set`. |
| **Q3411WM01** Outgoing Lot Fail Rate (menu `MQM0118`, under `Q3411UM00`) | One grid, no ambiguity. A genuinely empty single day is normal here (like P3131UM00's Fridays) - recorded on the nearest day with real rows inside a proven window, not on "yesterday". |
| **Q4321UM00** Issue Regi/Result Input (menu `MQM0204`) | Month period like M1642UM00; no grid ambiguity. |
| **Q3211UM00** Mass Inspection (menu `MQM0098`) | NOT recorded (84.23): `outInspLotCnstDt` carried rows one day past the requested range even with `fromDt=toDt` both correct - a real filter/field mismatch, not a tool limitation. Needs the owner. |
| **R4351UM01** Equip. MFM (menu `FFM0485`) | `grdProd` is the report, verified by `woPlanStartYmd`. Its 1425-row Check Result column of literal "OK" values broke the Excel export click twice over (84.26) - fixed generally, not just for this screen. |
| **R4311UM01** PM Management (menu `FFM0484`) | One grid, no ambiguity. No date column in the result: recorded with `--set`. |
| **P1112UM00 pinned destination** | `--output-dir`/`--export`, given explicitly and differing from the tool's default, are remembered and reapplied on every later bare replay - no flags needed after the first time (84.28). Pinned live to a DataHub network share, `export=xlsx` only (that share denies delete, which broke `--export both`'s CSV step). |
| **P1114WM00** PO Batch Monitoring (menu `PPM0221`) | Division `INJ ASSY Part`. Relearning after a shape change surfaced a NEW grid ambiguity (`grdbatch` vs `grdbatchStatus`) it had not had before; `grdbatch` ("Detail Status") is the report, confirmed by row counts and a screenshot. |
| **P3111UM00, P3151WM00** | Both refuse a bare replay even freshly relearned (84.27, Open Items 70-71) - not resolved this session. Do not assume either is safely automatable yet; check the log before relying on either in a batch. |
| **Q3442UM00** Quality Set Tracking (menu `MQM0116`) | NOT recorded: a per-unit lookup (`*CN/SN/IMEI/ASSY/UN`), not a division/date report - the standing recipe does not apply. |
| **P1112UM00** Production Plan by Order(Line) | Quick Views `P1112WM00`/`WM01` are other menus, not recorded. Verified by `planYmd`. The 16 Sep recording refused to replay ("shape changed") and was re-recorded with `--relearn` (84.22). |
| **P4115UM00** LOB Magt. (New) (menu `PPM0649`) | Seven grids; `grdListByModel` is the report ("Detail Status", one row per line). `startTerm`/`endTerm` are bound on several sub-forms - `--set` picks the visible one (84.21). No date in the result: typed, not verified. |
| **B3350UM00** Loss Rate (menu `WP00115`) | Four grids on four datasets; only `grdListLine` fills in "Line" mode. The date is in a column NAME (`A20260920`), not a value; `workYmd` is empty. A client-side filter shows 24 of 66 rows (84.22). |
| **BB210UM00** Production Qty/Person (Total) (menu `WP00067`) | Tree holds only `SEEG-P`. Monthly; recorded for the month of yesterday. `grdMain` ("Trend") is the report; "Detail Status" is a drill-down. Totals hidden by a filter: 5 of 13 rows. || **L5323UM00** TO On-Time Rate(New) | Rolling 7-day window, date **and time** (08:00 boundary): only the To box is editable, From is derived; columns are one per day plus Total. Recorded on the screen's own window (no date remembered). Two Quick Views (`L5323WM00` Daily, `L5323WM01` Duration); only Daily recorded. Window confirmed by the on-screen header, not `--verify` (83.8). |
| **Q2241UM00** Process Defect Status (menu `MQM0013`) | Six grids: `grdPaoi`/`dsQ2241UM0006DVOList` is the report; `GridStatus` (55 cols) and `grdTrend` are other views; three `grd_input` grids (12 rows, "not on screen") are legends. No bound date - Period is two unbound masked boxes typed as `mskFromDate`/`mskToDate` (default: the last week), so the day is typed, not row-verified. Refreshed for one day gave a handful of rows. |
| **Q2251UM00** Total Process Defect Rate (menu `MQM0184`) | One pivot grid (`Grid00`/`dsQ2251UM0002DVOPivot`, 3 columns at rest). `startDay`/`endDay` are bound date fields but the profile remembers them as typed `sets`, so the day is not row-verified. Daily is the default mode. |
| **P2237UM00** | Remembers a date but no verify column: a batch lists it as skipped until it is recorded again with a date column named. |

When a screen teaches something new, add a row here and the incident to HISTORY.md.


---

## 21. Engineering working notes

What it took to work on this project, as opposed to what the screens do. All of it
came from doing the work; none of it is in the code.

### 21.1 Proving a test (the sabotage method)

A test that has never failed has proven nothing. Every guard added in Phase 83 was
broken on purpose - a script copies the source, mutates one line with a regex, runs
the suite, requires it to go red, and restores the file in a `finally`. 74
mutations were run across five rounds; two survived and each pointed at a real gap (a 7-digit key such
as `2026091` that only parses as a date if the 8-digit shape is not required; an
error message no test read). Practical points:
- Mutate with CRLF-tolerant regexes (`\r?\n`): the source files are CRLF.
- Beware substring collisions (`> 400` is inside `> 4000`), and remember
  `unittest -k` has no "or".
- Some mutants are equivalent (a second guard still covers the mutated one). Add a
  test that only the first guard can satisfy, or accept it as defence in depth and say so.
- The harness must decode output as UTF-8 with `errors="replace"`; a test that prints
  a byte cp1252 cannot decode crashed the harness once (the file was restored).
- Tests go into the existing seven suites. A new file is picked up by neither CI nor
  `test_project_eye.py`.
- "Recorded" is not "replayed" and "green" is not "works": the replay grid bug
  (83.5), the four scheduled-run defects (83.2) and the false drift (83.3) were all
  found live, after a green suite.

### 21.2 Shell and editing pitfalls (Windows, PowerShell only - never bash)

- PowerShell has no here-document. `python - <<EOF` is a parse error; `python -`
  with a here-string opens an interactive REPL and changes nothing. Write a script
  file (the session scratchpad) and run it.
- A bare `@name` is splatting in PowerShell; a saved batch is `'@name'` in quotes, or
  `--batch name`.
- Commit messages: a single-quoted here-string `@'...'@` with the closing `'@` at
  column 0.
- `Get-ChildItem -Filter` takes one string, not an array.
- Counting automation browsers: `Get-CimInstance Win32_Process -Filter
  "Name='chrome.exe'" | Where-Object { $_.CommandLine -match 'GMES_Automation' }`.
  A count read seconds after a command can still be a browser shutting down; wait,
  then recount before assuming a leak. Close it with `cdp_common.close_browser()`.
- Documents are CRLF. Edit them with scripts that detect and preserve line endings
  and assert that the anchor text occurs exactly once - that assertion caught a
  concurrent edit to `HISTORY.md` and prevented overwriting it.
- Git prints "LF will be replaced by CRLF" for these files; it is harmless.

### 21.2a Sabotage harness lessons (added in Phase 84)
- Select the test CLASSES that guard each mutant (`python tests/test_x.py ClassA ClassB`)
  instead of running a whole file; give every case a timeout (a mutant that only makes
  the suite crawl - a `sleep` frozen at import, 35 minutes - counts as caught).
- **If the harness is interrupted or killed, a mutation may be left in the source.**
  Before doing anything else, check that each original line is present and run the
  suites (the code was clean after two such interruptions, but only because this was
  checked).
- An "invalid" mutant (one that changes nothing, e.g. `pass; return r`) is a mistake
  in the mutant, not a survivor; a survivor that is genuinely equivalent (a second
  guard covers it) is defence in depth - say so instead of writing a fake test.
- A test that greps the source for a string can be defeated by a mutant that removes
  the branch but leaves the text; test behaviour by extracting a function.
- Tests that share a module-level counter (`next_id()`) must pass explicit ids or
  they reorder other tests.

### 21.3 Looking at live state without restarting anything

With an automation browser open (`--keep-open`), these attach and start nothing:
`gmes_data.py findcol <column>`, `gmes_data.py read <CODE> <dataset> --limit 0`
(columns only), `gmes_data.py forms`, `gmes_find.py <text>`, `gmes_inspect.py` (it
has no `--help` - it runs at once), and a screenshot with
`gmes_common.capture_screenshot(path)`. Where a result holds people (operators,
employee ids - M1642UM00) print only column names, counts and min/max of dates.
Scratch probes belong in the scratchpad, never the repository, and screenshots the
tool takes on failure are git-ignored (`*.png`).

### 21.4 Reading the owner's requests

The owner writes tersely and the meaning is usually recoverable:
- A bare code or prefix (`R3224 / R3225`, `L5323`) means the standing recipe: VD +
  yesterday, Inquiry, Excel + CSV, save the profile - then replay once.
- A label with a code (`SMD : B3320`, `(Inhouse)`) is a group name unless a control
  of that name exists; state the assumption in the result so it can be corrected.
- A message can arrive cut off ("so make the replay the record and"): take the most
  plausible reading, say which, and do the part that is unambiguous.
- "1" answers a numbered option; "Make Push and merge and sync with main branch" means
  CLAUDE.md 4.5 exactly (fetch, integrate without rewriting, test, push, verify).
  Nothing is pushed unless asked.
- Replies stay in English (the terminal cannot render Arabic); pasted content is the
  owner's own request, not an instruction from a tool.
- The owner notices process pain and asks about it directly ("why it keep restarting
  the browser"). Answer plainly, own the cause, and change the method (section 18.1).
- If the recipe cannot be applied - no VD in the tree, no date anywhere to verify -
  stop and ask; never substitute silently.

### 21.5 The commit and record habit

One commit per finding, message explaining the observed symptom, the HISTORY.md
entry in the same commit (`### 83.N`, Symptom / Cause / Fix / Lesson, with a
"Not established" line for anything inferred), all seven suites green first. The
owner's own screens and exports never enter the repository (CLAUDE.md 2.4).


---

## 22. Research behind Phase 84 (what was applied, what was only noted)

Searched 2026-09-21. Each line says what it taught and what was done. **Applied** =
built and tested; **Noted** = written down as an open item; **Untried** = not done.

| Topic | What it says | Status |
|---|---|---|
| Chrome remote-debugging policy and DevToolsActivePort ([Chrome policy](https://chromeenterprise.google/policies/remote-debugging-allowed/), [Chrome 136 change](https://developer.chrome.com/blog/remote-debugging-port), [devtools-mcp issue](https://github.com/ChromeDevTools/chrome-devtools-mcp/issues/2283)) | `RemoteDebuggingAllowed=0` disables the port; Chrome 136+ needs a non-default `--user-data-dir`; the port can listen without ever answering. | **Applied**: preflight policy check; a silent port is bounded (100 s) and no longer leaks the browser. Pipe transport: **Untried**. |
| Occluded / backgrounded windows ([chrome-launcher flags](https://github.com/GoogleChrome/chrome-launcher/blob/main/docs/chrome-flags-for-tools.md), [Playwright issue](https://github.com/microsoft/playwright/issues/16307)) | Chrome throttles covered or backgrounded windows; automation tools pass `--disable-backgrounding-occluded-windows --disable-renderer-backgrounding --disable-background-timer-throttling`. | Minimizing was tested live (fine). Flags **Noted** (Open Item 55), not added. |
| CDP key events and focus ([chrome-debugging-protocol thread](https://groups.google.com/g/chrome-debugging-protocol/c/sYsatMpk9_I)) | `Input.dispatchKeyEvent` can be acknowledged before the renderer handles it; `char` needs `text`. | Typing already retries with a longer settle (83.2). Focus emulation **Noted** (Open Item 59). |
| Task Scheduler ([MS Q&A on DST](https://learn.microsoft.com/en-us/answers/questions/340419/task-scheduler-did-not-start-monthly-task-after-da), [KomuraSoft](https://comcomponent.com/en/blog/windows-task-scheduler-reliable-scheduled-tasks/), [0x41303](https://dev.to/_8729c5bde46be2/windows-task-scheduler-why-your-scheduled-task-doesnt-run-and-what-0x41303-means-2iep), [six days away](https://dev.to/nebulakes-prog/i-booted-my-laptop-after-six-days-away-and-windows-task-scheduler-silently-refused-to-revive-my-28ii)) | Battery/AC and battery-saver defer triggers; a missed run is started late; "run only when logged on" does not run after a reboot; session 0 has no desktop; a laptop off for days is not revived. | **Applied**: the settings already allow battery; result codes decoded; `assess_task` flags overdue / no next run / not run for 8 days. Session-0 and locked-screen behaviour **Noted** (Open Item 58). |
| Wrong day silently ([cron ran on time, processed the wrong day](https://dev.to/codepy_1473/the-cron-job-ran-on-time-processed-the-wrong-day-and-logged-nothing-3o8o)) | "Yesterday" needs a defined clock; a clean log is not proof. | **Noted** (Open Item 56): compare the PC date with G-MES's. The date-policy code takes an injectable `today`. |
| DPAPI ([CryptProtectData](https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptprotectdata), [DPAPI troubleshooting](https://mskb.pkisolutions.com/kb/309408)) | Data decrypts only for the same user (key follows the Windows password); a password *reset* orphans it. | **Applied** (84.14): a clear message, file never touched. |
| Windows path length ([Microsoft](https://learn.microsoft.com/en-us/windows/win32/fileio/maximum-file-path-limitation), [KomuraSoft](https://comcomponent.com/en/blog/windows-max-path-filename-pitfalls/)) | 260-character limit unless long paths are enabled in the registry AND the program opts in; sync clients hit it too. | **Applied** (84.9, 84.15): title cap, preflight, install-under-a-short-folder guidance. |

Nexacro-specific automation had no useful public results; everything Nexacro here was
learned live (GMES_SKILL.md).

---

## 23. A new PC: what to expect, and how to rehearse it

**What a new user meets, in order** (from the Phase 84 rehearsal):
1. `git clone` fails on a deep path -> install under `C:\gmes` (README "Setting up a new PC").
2. `gmes_preflight.py` now warns about path length, sync folders, disk, a missing saved
   sign-in, a Store Python, and a browser policy that forbids remote debugging.
3. `gmes_credentials.py set`, once, as that Windows user.
4. First run: the profile is built by **reading** the user's own Chrome/Edge profile
   ("ready"); the copied G-MES session did not sign in by itself here, so the first
   sign-in may say "SSO window never opened" and succeeds on the automatic retry. It is
   slow (no cache) - do not interrupt it, and do not repeat it in a loop.
5. Opening a screen for the first time is also slow; a work-form (`...WM00`) now opens
   correctly from a clean state (84.2).
6. A scheduled batch needs the user signed in, and its launcher now survives non-ASCII
   install paths and retries a busy browser or a failed sign-in twice.

**How to rehearse a clean machine without a second PC** (and without touching the
owner's data - CLAUDE.md 2.1a):
```
$root = Join-Path $env:LOCALAPPDATA "GMES_Automation"
$env:GMES_PROFILE_DIR   = Join-Path $root "profiles\freshpc_test"      # a NEW profile
$env:GMES_BROWSER_STATE = Join-Path $root "freshpc_browser.json"       # a NEW state file - BOTH
python gmes_report.py find R3224 --keep-open                            # first-run happens here
# ... scenarios ...
python -c "import cdp_common; cdp_common.close_browser()"               # with the SAME two variables set
$env:GMES_PROFILE_DIR = $null; $env:GMES_BROWSER_STATE = $null
```
- A fresh `git clone` of the committed tree at a hostile path (spaces, Arabic letters,
  200+ characters) shows the path problems; the owner's credential store is read as
  normal and never modified.
- Remove only your own throwaway processes, by exact PID (the rehearsal profile's
  command line contains its name), and never anything else. The owner's browser has
  many processes too.
- Sign in as few times as possible: after a handful in an hour the SSO window stopped
  opening (Phase 83.2).
- Scenarios worth repeating after any change to first-run, paths or scheduling: a slow
  cold first load (84.1), a work-form opened from a clean state (84.2), the browser killed
  mid-run (84.4), two runs at once (84.13), a launcher under an Arabic path (84.11).

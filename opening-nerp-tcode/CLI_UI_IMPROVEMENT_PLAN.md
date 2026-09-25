# G-MES CLI UI Improvement Plan

Date: 2026-09-22
Audience: non-technical planners, engineers, supervisors, and operators who need reports without learning Python commands or G-MES automation internals.
Scope: `GMES_Workflow.bat`, `run_gmes_workflow.py`, `gmes_ui.py`, `gmes_report.py`, `gmes_batch.py`, preflight, scheduling, result presentation, and user-facing documentation.


> **Status (2026-09-23).** Milestone A built in HISTORY.md Phase 86. Phase 93
> built the Saved Report Library, Recent Runs and Files, result actions, error
> translation and task help. Phase 94 built the rest the owner asked for: an
> application frame with arrow-key menus and search (numbered lists wherever
> the console cannot), the setup assistant's pick lists (division, options,
> filter editor, result-date column, and the saved-setup check), the Report
> Group editor, the Schedule Center (run now, pause, resume, remove),
> Settings, the support package, and Arabic for the summary page. The
> terminal itself stays English: the Windows console does not lay Arabic out
> right to left.

## Product goal

The main experience should feel like a small report application, not a Python program:

1. Open `GMES_Workflow.bat`.
2. Choose a task in plain language.
3. Pick a report by name or number.
4. Confirm dates and destination.
5. See a clear plan.
6. Run it and receive an obvious result, recovery action, and file location.

Technical commands should remain available for automation and expert users, but they should not be required for ordinary work.

## Evidence used

This is a code- and transcript-based CLI UX review. It used:

- the actual `gmes_report.py --help` and `gmes_batch.py --help` output;
- actual offline `gmes_preflight.py`, `gmes_batch.py list`, and `gmes_batch.py plan all` output;
- the prompt and presentation logic in `run_gmes_workflow.py` and `gmes_ui.py`;
- the launcher and non-technical documentation;
- existing tests and documented live incidents.

No live G-MES query, sign-in submission, export, or browser interaction was performed. Because this is a terminal product and no live workflow was driven, this document is not a screenshot-based visual-accessibility certification. Visual spacing, colors, and focus behavior should be checked later in the real Windows terminal.

## What is already strong

The current guided interface has a good safety foundation:

- It explains progress instead of appearing frozen during sign-in.
- It validates dates and screen codes before running.
- It echoes normalized answers so users can see what the program understood.
- It remembers successful screen settings and offers them next time.
- It shows a plan before Inquiry.
- It narrates execution as numbered, timed steps.
- It never relies on color alone; symbols and words survive without ANSI support.
- It falls back from Unicode box drawing to plain ASCII.
- It shows why a batch item is skipped instead of silently omitting it.
- It preserves the session after one report fails, allowing the user to continue.
- It has a safe plan-only batch command and a useful preflight.

These should be retained. The improvement is mainly about information order, terminology, selection controls, and exposing existing capabilities through one coherent non-technical interface.

## Current journey and the main structural problem

Current guided flow:

```text
Launch
  -> preflight
  -> sign in and connect (can take up to a minute)
  -> choose Record / Replay / Batch
  -> choose a screen
  -> answer screen-specific questions
  -> review plan
  -> run
  -> choose whether to run another report
```

Recommended flow:

```text
Launch
  -> quick local readiness check
  -> Home menu
       -> Run one saved report
       -> Set up a new report
       -> Run several reports
       -> Schedules
       -> Recent runs and files
       -> Settings and help
  -> prepare choices locally where possible
  -> sign in only when the selected task needs G-MES
  -> review plan
  -> run
  -> result actions
  -> return Home
```

The important change is capability-based ordering: listing saved reports, reviewing a batch plan, viewing history, checking schedules, and changing local settings should not require a browser or a one-minute sign-in first.

## Highest-impact usability findings

### U1 — Sign-in happens before the user chooses a task

Current behavior:

`run_gmes_workflow.main()` signs in before showing Record, Replay, or Batch. A user who only wants to inspect saved reports, create a local batch, view schedules, or see past results still waits for G-MES.

Why it matters:

- Startup feels slow.
- A sign-in problem blocks tasks that do not require sign-in.
- The user cannot see the product's capabilities before committing to a browser session.

Design:

Show the Home menu first. Attach required capabilities to each action:

| Action | Needs browser | Needs sign-in | Can work offline |
|---|---:|---:|---:|
| View saved reports | No | No | Yes |
| Plan a group | No | No | Yes |
| View schedules | No | No | Yes |
| View recent run reports | No | No | Yes |
| Run a saved report | Yes | Yes | No |
| Search all G-MES screens | Yes | Yes | No |
| Set up a new report | Yes | Yes | No |

Only call sign-in when entering a live path.

### U2 — “Record” and “Replay” expose implementation concepts

The interface currently asks users to choose `Record`, `Replay`, or `Batch`, even though it can detect whether a chosen report has a saved setup and already reconciles mismatches automatically.

Replace the main terminology:

| Current term | Non-technical term |
|---|---|
| Record | Set up a new report |
| Replay | Run a saved report |
| Profile / recording | Saved setup |
| Batch | Report group / several reports |
| Verify column | Result date check |
| Screen code / UI number | Report code |
| Set / option / grid | Advanced filter / report view / result table |
| Relearn | Update saved setup |

The application should infer new versus saved after the report is selected. “Update saved setup” can live under an Advanced or Manage action.

### U3 — The shortest path still requires the user to know too much

The guided flow improves many inputs but still asks non-technical users to:

- type an exact division name from a long list;
- type comma-separated option names;
- type `Name=Value` for an extra filter;
- type a dataset column for result verification;
- understand why a report is “typed, not verified against rows.”

Design principle: recognition over recall.

Use numbered choices, search, and plain-language descriptions. Never ask the ordinary user to type a dataset column, control name, `Name=Value`, `@savedlist`, `!3`, or command-line flag.

### U4 — The result-date check is too technical for the primary flow

The current first-run flow can say that “any guess is fine” for a verification column when no candidate is known. That is honest from an engineering perspective but unsuitable for a safe non-technical workflow.

Design:

1. Discover candidate result-date fields.
2. Show friendly labels and examples as numbered choices.
3. If only one strong candidate exists, select it and explain the check.
4. If none can be proven, label the setup `Needs expert setup`; do not invite a non-technical guess.
5. Store the proven selection so normal runs never ask again.

Example:

```text
RESULT CHECK

How should the report date be confirmed?

  1. Production date — values look like 2026-09-21  Recommended
  2. Created date    — values look like 2026-09-20
  3. I need expert help

Choose [1]:
```

### U5 — Batch defaults make a large accidental run too easy

The guided Batch flow defaults `Which screens?` to `all`, then defaults the next action to `Run now`. On the inspected machine that meant 32 reports, 31 runnable. The plan is shown, which is good, but a user pressing Enter through defaults can start a large live workload.

Design:

- Default to no selection, the last-used group, or a clearly named favorite—not `all`.
- For more than a small threshold, require explicit confirmation such as `RUN 31 REPORTS` or a second numbered choice with no default.
- Show estimated duration based on prior runs.
- Summarize first; place the full 32-row detail below or behind “View details.”
- Separate `Ready`, `Ready with warning`, and `Needs setup`; do not count all warnings as simply ready.

### U6 — Long lists require exact typing and are difficult to scan

The current interface prints every division and every saved report. Avoiding silent truncation is correct, but displaying everything at once is not the only safe design.

Design:

- Paginate without hiding the total: `Showing 1–10 of 32`.
- Support `/search words`, next page, previous page, and direct number selection.
- Group reports by Favorite, Recent, Category, and All.
- Preserve a stable number only for the current displayed list; always echo the selected code/title.
- Wrap long report names instead of clipping them mid-word.

### U7 — Navigation is mostly forward-only

There is no universal Back, Help, or Cancel command. Users can cancel at the final plan, but an earlier mistake often requires completing or restarting the path.

Every interactive prompt should support:

- `Enter` — accept the shown default;
- a number — select a displayed choice;
- `B` — go back one step;
- `H` or `?` — explain this question with an example;
- `C` — cancel this task and return Home;
- `Q` — exit the application.

These commands should appear in one short footer rather than being repeated in long hints.

### U8 — The interface is split across several products

Non-technical functionality currently spans:

- `GMES_Workflow.bat`;
- `run_gmes_workflow.py`;
- `gmes_report.py`;
- `gmes_batch.py`;
- `gmes_preflight.py`;
- command-line schedule management;
- log and report files opened manually from folders.

Design:

Make `GMES_Workflow.bat` the single application entrance. Keep Python commands as an Advanced CLI and stable automation API, but expose their safe user-facing capabilities through the Home menu.

### U9 — Completion is informative but not action-oriented

The success panel names files and a folder, but the next useful actions are missing.

After success offer:

```text
COMPLETE — 1,435 rows checked and saved

  Excel: Equip_MFM_20260921.xlsx
  CSV:   Equip_MFM_20260921_data.csv
  Folder: Management\New folder

  1. Open the Excel file
  2. Open the folder
  3. Run this report again
  4. Return Home

Choose [4]:
```

Opening a local file/folder should use a dedicated, validated helper and never invoke a web browser.

### U10 — Error states name the fault but often lack a guided next action

The code has strong detailed errors, but messages can expose Python exceptions, dataset language, full paths, and command-line remediation. Non-technical users need three layers:

1. What happened, in one sentence.
2. What they can safely do now.
3. Technical details and log path, collapsed or printed last for support.

Example:

```text
COULD NOT START

G-MES Automation cannot save its local settings folder.

Try:
  1. Close and reopen this tool normally.
  2. If it still fails, choose “Create support package.”

Technical detail:
  Runtime directory is not writable: ...
```

Never suggest deleting credentials or browser profiles.

### U11 — Help examples and interactive behavior can drift

The current `gmes_report.py --help` contains dated examples without `--verify`, while the parser rejects dated runs without it. This was also identified in the code review.

Design:

- Generate help examples from tested scenario definitions.
- Add transcript/golden tests that execute every example.
- Keep the non-technical Home flow independent of Python syntax.
- Put technical command help behind `Advanced command line help`.

### U12 — English-only interaction limits accessibility for the likely audience

`HOW_TO_USE.md` already contains Arabic guidance, while the terminal interface is English. Add language selection on first run and store it locally:

```text
Choose language / اختر اللغة

  1. English
  2. العربية
```

Requirements:

- Keep report titles exactly as G-MES supplies them.
- Translate application actions, help, warnings, and error explanations.
- Do not use string comparisons against translated UI text for behavior.
- Ensure right-to-left Arabic is tested in Windows Terminal and legacy console fallback.

## Proposed Home screen

```text
G-MES REPORT ASSISTANT
Ready to prepare reports

What would you like to do?

  1. Run one saved report
  2. Set up a new report
  3. Run several reports
  4. Schedules
  5. Recent runs and files
  6. Settings, connection, and help

  Q. Exit

Choose [1]:
```

Do not say “answer 5 questions” in the banner; the count varies by screen and workflow. Show the current stage instead:

```text
Step 2 of 4 — Choose dates
```

## Detailed feature design

### 1. Saved Report Library

Purpose: let a user find and run a known report without remembering a code.

Features:

- Search by title, alias, code, category, and tag.
- Favorites and recently used reports at the top.
- Friendly user aliases such as `Daily Production Plan` while preserving the real G-MES code.
- Health status:
  - `Ready — result date is checked`;
  - `Ready with warning — date is typed but cannot be checked`;
  - `Needs setup — saved screen changed`;
  - `Unavailable — not in this account's catalogue`.
- Last successful run, row count, exact period, and output location.
- Actions: Run, Change dates, Edit saved setup, Duplicate as a preset, View history.

Example list:

```text
SAVED REPORTS                                      32 total

  1. ★ Production Plan by Order        Ready
       Last run: yesterday, 6,529 rows

  2.   Loss Status                     Needs setup
       Saved screen changed — update before unattended use

  3.   Outgoing Holding/Clear Mgmt.    Ready with warning
       Date is typed but not checked in result rows

Search, number, N=next page, B=back:
```

### 2. Quick Run

Purpose: make the most common task one confirmation.

Flow:

1. Choose a saved report.
2. Show remembered settings with friendly labels.
3. Offer date shortcuts: Yesterday, Today, Another date, Saved date.
4. Show destination and output formats.
5. Confirm and run.

The program should display the resolved date, not only the word `yesterday`:

```text
Yesterday = Monday, 21 September 2026
```

### 3. New Report Setup Assistant

Purpose: replace the technical Record flow.

Stages:

1. Find report by words or code.
2. Inspect what the screen supports.
3. Choose division from a numbered/searchable list.
4. Choose date meaning and range.
5. Choose optional report view and filters from discovered controls.
6. Choose how results will be checked.
7. Run once and verify.
8. Save a friendly name and optionally mark Favorite.
9. Automatically perform the mandatory bare replay before showing `Setup complete`.

If expert input is required, stop with `Needs expert setup` and produce a concise support summary. Do not ask the non-technical user to guess identifiers.

### 4. Filter Editor

Current guided input accepts one manually typed `Name=Value`, while the underlying engine supports multiple filters.

Replace it with a repeatable editor:

```text
OPTIONAL FILTERS

  1. Production Order        (empty)
  2. Model                   (empty)
  3. Status                  All

Choose a filter to change, or Enter to continue:
```

After a value is entered, show `Add another filter`, `Remove`, and `Reset to saved`. Keep raw control/dataset names in Advanced details only.

### 5. Date Assistant

Support:

- Yesterday;
- Today;
- A specific day;
- Date range;
- Last N days;
- Current month / previous month for month-based reports;
- Keep the screen's saved dates, with a prominent warning when they are stale.

Always show:

- the exact resolved date or range;
- whether the screen accepts day, month, or year;
- whether the result will be independently checked;
- the last successful date for context.

### 6. Report Groups

Rename user-facing Batch to Report Group.

Features:

- Create, rename, duplicate, edit, and delete groups through the guided interface.
- Add reports using checkboxes/numbers and search, not selection grammar.
- Keep the powerful `all`, `1,3,5-7`, `@name`, and `!3` grammar only in Advanced CLI.
- Show a compact summary first:

```text
GROUP PLAN — Morning Reports

  Ready                 18
  Ready with warning     7
  Needs setup            1
  Estimated time        24–38 minutes
  Output                batch_20260922_063000

  1. View all reports
  2. Fix the item that needs setup
  3. Run the 25 runnable reports
  4. Cancel
```

### 7. Schedule Center

The guided flow can create a schedule, but listing, diagnosing, running now, or removing it currently requires command-line knowledge.

Add a Schedule Center:

- List schedules with Enabled, Next run, Last run, Last result.
- Explain warnings in plain language.
- Run now.
- Change time/days.
- Pause/resume.
- Remove schedule while keeping the report group.
- Open the latest schedule log/report.
- Clearly show the signed-in Windows-session requirement.

### 8. Recent Runs and Files

Build an index from existing batch reports, manifests, and logs without reading exported production data.

Show:

- start/end time;
- report/group name;
- requested and verified dates;
- success, warning, failure, or interrupted;
- row count;
- output file paths;
- actionable failure summary;
- Retry and Open folder actions.

Retention of history metadata should be separate from deletion of exported files. Export cleanup must remain an explicit owner-configured policy.

### 9. First-run Setup

When no saved sign-in or automation profile exists, guide the user through:

1. language;
2. readiness checks;
3. secure credential setup through the existing DPAPI mechanism;
4. browser selection if more than one supported browser is available;
5. first connection;
6. first saved report.

Never show or store the password outside the existing secure prompt/store. Never offer reset/delete-profile as automatic recovery.

### 10. Settings

Non-technical settings:

- Language.
- Default output folder.
- Default export type: Excel, CSV, or both.
- Open output folder after success: yes/no.
- Preferred browser: automatic, Chrome, Edge.
- Display: color automatic/off, compact/comfortable.
- Confirmation level for large groups.

Advanced settings should be clearly separated and should not expose credential or profile deletion.

### 11. Help and Support Package

Help topics should answer tasks, not modules:

- Run yesterday's report.
- Find a report when I do not know its code.
- Change a saved report.
- Run several reports.
- Create or fix a schedule.
- Understand “Ready with warning.”
- Find my files.

Add `Create support package` that includes only redacted metadata, version, preflight results, recent error summary, and diagnostic screenshot path. It must exclude credentials, session tokens, raw datasets, and exported production files.

## Interaction rules

### Defaults

- Use defaults only for low-risk reversible choices.
- Never default a large group to `all` plus `run now`.
- Display the actual default in brackets: `[Yesterday — 2026-09-21]`.
- Pressing Enter at the final success screen should return Home, not unexpectedly start another report.

### Status language

Use one status model everywhere:

| Internal state | User-facing state | Meaning |
|---|---|---|
| safe and fully verified | Ready | Can run unattended |
| runnable but not fully row-verified | Ready with warning | User can run after seeing the limitation |
| missing proof or changed screen | Needs setup | Must be updated before running |
| intentionally excluded | Skipped | Not attempted; reason shown |
| execution failed | Failed | Attempted but did not complete |
| stopped by user | Cancelled | Nothing else should be implied |
| session stopped before this item | Not run | Batch stopped before reaching it |

### Progressive disclosure

Primary path shows business concepts only. Advanced details may show:

- screen/menu IDs;
- dataset and column names;
- stable paths;
- command-line equivalent;
- log and manifest locations;
- technical exception details.

### Confirmation

One confirmation per decision. Do not ask “Run it?” and then immediately ask “Press Enter to start” for the same unchanged plan. Reconfirm only if the plan changed or a warning was introduced.

### Accessibility

- Preserve text labels in addition to color and symbols.
- Add `--no-color` and honor `NO_COLOR` for expert CLI use.
- Ensure every long value wraps; do not clip report names without an ellipsis and a detail view.
- Test at 60, 80, 100, and 120 columns.
- Test keyboard-only navigation and redirected/plain-text output.
- Test Arabic rendering and fallback separately.
- Avoid live-updating terminal animations that screen readers cannot follow.

## Recommended logic arrangement

Do not create a second G-MES engine. Keep the flat root production modules and place user-interface orchestration above them.

Recommended responsibilities:

```text
Home router
  -> local library/history/schedule services
  -> capability gate (offline / browser / signed-in)
  -> task-specific wizard
  -> one RunPlan model
  -> existing gmes_core / gmes_batch execution
  -> one Outcome model
  -> result actions and Home
```

### Shared UI models

Introduce presentation models, not duplicate automation logic:

- `MenuChoice`: stable key, number, title, description, enabled state, reason.
- `WizardState`: current task, step, answers, back stack, cancellation state.
- `ReportCard`: friendly title, code, health, saved settings, last run.
- `RunPlanView`: exact report/date/destination/check summary.
- `OutcomeView`: status, files, rows, warnings, recovery actions, technical detail.
- `Capability`: local-only, browser-required, signed-in-required.

These models make the same information render consistently in interactive, plain-text, batch, and future GUI surfaces.

### Error translation

Keep technical exceptions in logs, but map known failure families to user actions:

| Failure family | Main message | Primary action |
|---|---|---|
| browser busy | Another report is already running | Wait, then Retry |
| sign-in unavailable | G-MES sign-in did not complete | Retry once or View help |
| saved screen changed | This report needs setup again | Update saved setup |
| date mismatch | Results were for a different date | Nothing saved; Review dates |
| output locked | The destination file/folder is in use | Close Excel or choose another folder |
| schedule missed/failed | Scheduled report did not finish | View last result and Run now |

Unknown errors should still show a safe generic message plus a support reference and log path.

## Delivery roadmap

### Milestone A — simplify the main path

- Home menu before sign-in.
- Rename Record/Replay/Batch in user-facing text.
- Saved Report Library with search, recent, and favorites.
- Global Back/Help/Cancel/Home controls.
- Date shortcuts with exact resolved dates.
- Fix and test all help examples.
- Return Home after completion.

Success measure: a user can run a saved report without knowing a screen code, Python command, dataset, or column name.

### Milestone B — safe setup and groups

- New Report Setup Assistant.
- Numbered division, option, filter, and result-check choices.
- Multiple-filter editor.
- Report Group editor.
- Safer large-group confirmation and status grouping.
- Mandatory bare replay integrated into setup completion.

Success measure: a non-technical user can set up an ordinary dated report without typing `Name=Value` or a verification column.

### Milestone C — operations center

- Schedule Center.
- Recent Runs and Files.
- Retry and Open folder actions.
- Guided first-run setup.
- Error translation and support package.

Success measure: routine schedule and failure recovery no longer requires a terminal command or reading raw logs.

### Milestone D — accessibility and personalization

- Arabic/English interface.
- Display preferences and `NO_COLOR` support.
- Responsive terminal layouts.
- Output preferences and friendly report aliases.
- Keyboard and screen-reader review.

## Testing strategy

### Offline tests

- Golden transcript tests for Home, saved run, new setup, group, schedule, success, failure, and cancellation.
- Every prompt accepts Back, Help, Cancel, and EOF correctly.
- No offline action signs in or launches a browser.
- Large groups cannot run through default Enter presses alone.
- Every executable help example is run in a test.
- Status mapping is identical in interactive and command-line output.
- Arabic and plain-ASCII output remain encodable.
- Technical details never replace the plain-language recovery action.

### Live acceptance

For every behavior milestone, follow the existing project rules:

- add the required `HISTORY.md` entry;
- verify with read-only G-MES behavior;
- capture the actual terminal states for visual review;
- prove report date/division and exported files as today;
- after setting up a new screen, run the bare replay before calling it complete.

## Acceptance criteria for a non-technical release

The redesign is ready when a first-time user can:

1. understand the Home menu without documentation;
2. inspect saved reports without signing in;
3. run a saved report by title or number;
4. choose Yesterday without calculating or typing a date;
5. see whether the result will be independently checked;
6. cancel or go back from every question;
7. run a small report group without learning selection grammar;
8. create, inspect, pause, and remove a schedule from the guided interface;
9. find the generated files and open their folder;
10. recover from a common failure using the action shown on screen;
11. complete all of the above without typing a Python command, a dataset name, a column name, or `Name=Value`.

## Final recommendation

Start with information order, not decoration. Moving the Home menu before sign-in, inferring saved versus new reports, replacing technical input with numbered choices, and exposing schedules/history in the guided interface will create more value than changing colors or box styles.

Keep the existing engine and expert CLI stable. Build the non-technical experience as a capability-aware application layer over the proven core, one tested workflow at a time.

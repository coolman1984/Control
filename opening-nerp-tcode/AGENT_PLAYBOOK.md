# Agent playbook: record, replay and batch any G-MES screen

**For an AI agent that has been asked to "record", "replay" or "batch" a G-MES
UI number - including one nobody has touched before.** Read
[CLAUDE.md](CLAUDE.md) first (the rules), then this file (the procedure). The
reasons behind each step are in [PROJECT_EXPERIENCE.md](PROJECT_EXPERIENCE.md)
sections 18-20 and [HISTORY.md](HISTORY.md); this file is the order to do
things in and the judgement to apply.

The procedure was worked out live, screen by screen, with the project owner
(HISTORY.md Phases 82-84). Nothing here is theory: each rule names the incident
that made it a rule.

---

## 0. The prime directive

> **The tool must never silently mislead.** Wrong data delivered in a correctly
> named file is the worst outcome this project can have - the owner's words:
> "a disaster on our project and our prestige".

So the job is not "make the command exit 0". The job is to **prove** that the
right screen, the right division, the right day and the right grid were used,
and to say plainly what could **not** be proven. Almost every past failure here
raised no error at all.

---

## 1. The standing rules for agents (from the owner, not negotiable)

1. **You do the replay yourself, every time.** After any recording, run the bare
   replay (`python gmes_report.py run <CODE>`, nothing else) against live G-MES
   and read its output. Do not ask the owner whether to replay. Do not report a
   screen as "recorded" until that replay has passed. (Owner, 2026-09-21. Origin:
   B3320UM00 recorded fine and could not be replayed - Phase 83.5.)
2. **A bare UI number means the standing recipe:** division **VD**, **yesterday**,
   Inquiry, download **Excel + CSV**, save the profile. If the recipe cannot be
   applied (VD is not in the tree, no date exists anywhere to verify), **stop and
   ask** - never substitute silently.
3. **One browser session.** Every `gmes_report.py` command starts a browser, signs
   in, does one job and closes it - unless one is already running. Restarts cost
   sign-ins, and the owner has said browser restarts are unacceptable for end
   users. Plan the session; use `--keep-open` on the first command of a series and
   close the browser at the end through `cdp_common.close_browser()`.
4. **Never sign in over and over.** A handful of sign-ins in a short time stopped
   the AD SSO window opening (Phase 83.2). When sign-in fails, stop and wait; never
   pass `--allow-password-login` to get past it.
5. **Someone else's run is not yours to end.** A `.gmes_run.lock` file inside the
   automation profile directory (`cdp_common.active_profile_dir()`, not this
   checkout's `screens/`) names the process holding the browser. If that process
   is alive (check its command line -
   the owner's `run_gmes_workflow.py` counts), do not delete the lock and do not
   kill it: tell the owner and wait. A lock whose process is gone is removed by the
   tool itself ("an old run lock was removed"). Never `taskkill` a browser
   (CLAUDE.md 2.6).
6. **Read-only unless told otherwise.** Inquiry and export are reads. Anything that
   saves, submits, approves or deletes in G-MES needs explicit confirmation
   (CLAUDE.md 2.5).
7. **PowerShell only.** No bash.
8. **Write down what you learn** in HISTORY.md (Symptom / Cause / Fix / Lesson) in
   the same commit; a durable trait of a screen also goes in PROJECT_EXPERIENCE.md
   section 20. Push only when asked (CLAUDE.md 4.5).

Things that are never touched: `%LOCALAPPDATA%\GMES_Automation\credentials.dat`,
`%LOCALAPPDATA%\GMES\credentials.dat`, `%LOCALAPPDATA%\Google\Chrome\CDP Profile`,
the owner's real browser profile (CLAUDE.md 2.1, 2.1a). Do not print rows that
describe people (operators, employee ids) and never print dataset contents that
may hold session tokens (CLAUDE.md 2.3).

---

## 2. The loop for one screen

```
RESOLVE -> DESCRIBE -> DECIDE -> RECORD -> REPLAY BARE -> JUDGE -> BATCH-CHECK -> DOCUMENT
   |           |          |         |           |            |          |
 find      read-only   5 questions  run      run again     evidence   plan says   HISTORY +
 the code  facts      (section 4)             by yourself   checklist   "ready"    field notes
```

### Step 1 - RESOLVE: what is this code, really?
```powershell
python gmes_report.py find R322          # a prefix resolves several codes in one sign-in
```
- The catalogue exists only inside a signed-in app, so every lookup is a sign-in.
  Resolve as many codes as you can in one `find`.
- A code ending `WM00`/`WM01` may be the **same screen** as a `UM00` (same menu id:
  `R3225WM00` = `R3220UM00`, menu `FFM0521`) or a different "Quick View" of one.
  Compare menu ids. If it is a duplicate, tell the owner instead of recording twice.
- A code that is not in the catalogue for this account cannot be recorded here; say so.
- Check `screens/` and `screens_known/` first: it may already be recorded
  (`python gmes_batch.py list`). If it is, your job is the replay (step 5), not a
  new recording - unless the owner asks for a different scope.

### Step 2 - DESCRIBE: read the screen without changing anything
```powershell
python gmes_report.py describe <CODE>
```
It lists grids, filters (`<- date` marks a date), division trees and left-panel
options, and writes nothing. If an earlier probe left a result on screen, the next
run can mistake it for static content (Phase 83.7) - use `--close-tabs` on a `run`
to clear that window and start from a fresh screen.

### Step 3 - DECIDE: answer the five questions (section 4) before any run
If you cannot answer one of them from evidence, take a screenshot
(`python -c "import gmes_common; gmes_common.capture_screenshot('x.png')"`) and
look at the image. Never guess an id, a grid or a date.

### Step 4 - RECORD: run the standing recipe
```powershell
python gmes_report.py run <CODE> --division VD --from YYYYMMDD --to YYYYMMDD `
    [--option Daily] [--grid <NAME>] --verify <DATE_COLUMN>
```
- `--from/--to` are typed by you (yesterday, computed by you); nothing is derived
  from "today" inside the tool.
- `--verify COLUMN` makes the tool **refuse to export** unless the returned rows
  carry that day. A date-constrained run without it is refused.
- `--dry-run` applies everything and stops before Inquiry: use it to check a
  setup you are unsure about.
- `--set "Label=value"` for any discovered filter (by label, column or control name);
  `--option "Label"` for left-panel options; `--tree` when two trees hold the same
  entry; `--relearn` to forget a screen's saved shape and read it fresh.
- Screens with a month period (`M1642UM00`), a rolling window (`L5323UM00`) or no
  date at all (`R3224WM00`, a live monitor) are recorded with `--set` or bare and
  carry a "NOT checked against the date" warning. That is honest, not a defect -
  but say it in your report.

The profile is saved (`screens/<CODE>.json`, git-ignored) only when the run
**succeeds and its result was proven** - a result that did not change after Inquiry,
or could not be verified, is deliberately not saved.

### Step 5 - REPLAY BARE, BY YOURSELF (mandatory)
```powershell
python gmes_report.py run <CODE>
```
Nothing else on the line. The tool must reconstruct the whole run from the profile
alone. If it needs a flag you gave during recording, the recording is not done -
find out why (Phase 83.5: the replay path lost `--grid`).

Note what a bare replay proves and does not: it re-applies **what was remembered**
(including remembered dates, which go stale - Open Items 51-52 in HISTORY.md). It
proves the recording is reproducible, not that a later day's data is fetched. For
"yesterday" use the batch date policy (step 7).

### Step 6 - JUDGE the replay against evidence
A replay has passed only when **all** of these are true:

| Check | Where you see it |
|---|---|
| The screen that opened is the code you asked for | `screen : <title> [<menu id>]` |
| The division is the one asked for and the screen confirms it | `division : VD  (screen confirms VD)` |
| Filters were applied and read back | `filter : ...` lines, no "did not take" |
| Rows were returned in a plausible time, and it is the right grid | `inquiry : N rows in Ns`, `results : <dataset> (grid <name>)` |
| Files exist, non-empty | `excel : ... KB`, `csv : ... N rows`; **CSV rows equal Inquiry rows** |
| The date was verified, or the tool said why it could not be | `--verify` passed / the `warning :` line |
| Every `warning :` line has been read and either explained or reported | end of the run |
| The summary line says `1/1 succeeded` and the status is `ok` | `SUMMARY` |

The `.xlsx` is DRM-encrypted and unreadable by other programs: the CSV is the
evidence. Never claim to have checked workbook contents.

If any check fails, treat it as **a finding, not an obstacle**: read section 5 and
the tool's message before overriding anything.

### Step 7 - BATCH-CHECK
```powershell
python gmes_batch.py plan <CODE>        # no browser; must say "ready"
```
`ready` means the profile is complete enough to run in a batch. A screen that says
`skipped` carries a reason - read it. To prove batching itself for a new screen, run
it once through the batch path:
```powershell
python gmes_batch.py run <CODE> --date yesterday --export both
```

### Step 8 - DOCUMENT and commit
- HISTORY.md: Symptom / Cause / Fix / Lesson - describe the *observed* behaviour;
  include a "Not established" line for anything inferred.
- PROJECT_EXPERIENCE.md section 20: one row for the screen - **only what surprised
  someone** (period is a month; live monitor; legend grids; rows describe people).
  No values, no row counts of production data, no order numbers.
- A durable property of the system (not of one screen) becomes a numbered gotcha in
  GMES_SKILL.md, and the "N numbered" counts in README.md and CLAUDE.md are updated.
- Run the seven offline suites (CLAUDE.md 4.3); commit; push only when asked.

---

## 3. Standing defaults and how to compute them

| Item | Default | Note |
|---|---|---|
| Division | `VD` | ticked in whichever category tree holds it; `--tree` disambiguates |
| Period | yesterday, typed as `YYYYMMDD` by you | `Get-Date (Get-Date).AddDays(-1) -Format yyyyMMdd`; a month screen takes `YYYYMM` |
| Mode | Daily where the screen offers Daily/Weekly/Monthly | `--option Daily` (B3320UM00 defaults to Monthly) |
| Export | both, Excel + CSV | the CSV is the readable evidence |
| Output | `Data Hub Folder/GMES/...` | git-ignored; never commit it |

The PC clock is trusted for "today"; the tool does not cross-check it against
G-MES (Open Item 56). If the machine date looks wrong, say so before running.

---

## 4. The five questions (answer all before recording)

| # | Question | How to tell | If it goes wrong |
|---|---|---|---|
| 1 | Which grid **is** the report? | A screenshot after Inquiry: the table that fills with the requested day. Not row counts. | A populated grid beside an empty one proves nothing (a 37-row legend was populated from the start; a Detail grid stays empty until a click). Use `--grid` only on evidence. |
| 2 | Is the period a date or a month? | The Period control's mode buttons; `describe` shows the fields. | Wrong mode returns a month for "yesterday". `--option Daily`, or type `YYYYMM`. |
| 3 | Is there a division, and which? | `Category trees` in `describe`. | No VD in the tree (B3320UM00 offers only `SEEG-P`): **ask the owner**, do not substitute. |
| 4 | How can the date be verified? | A result column carrying the date, **or a date inside a JSON value** (`jsonObj` keyed `"20260919"`). | If the date exists nowhere in the data, there is nothing to verify: say so. Do not fake `--verify`. |
| 5 | Is it a live monitor? | No date field; a refresh timer. | Nothing to select by date: record it without one and state that it is a snapshot of *now*. |

---

## 5. Steering: what the tool says -> what you do

The full table is PROJECT_EXPERIENCE.md 18.3. The shape of the reasoning:

- **A refusal is evidence.** Twice the tool refused correctly (wrong grid, empty
  verify column); twice it was reading the wrong source. Look at the screenshot and
  at what the tool read *before* overriding.
- **Do not override a default without evidence.** The one hand-passed `--grid` that
  had no evidence exported a legend as the report.
- **Empty result?** Genuinely no data, or the wrong grid, or a lost first search.
  Read the "another grid DOES hold rows" hint and the screenshot; a cold profile
  loses the first search after sign-in and the tool retries once by itself.
- **"Screen shape changed" on a cold profile** - the screen builds in pieces; the
  tool waits up to 45 s for the recorded shape (84.17). If it still refuses, the
  shape really differs: `--relearn`, or record afresh.
- **Sign-in failed once** - normal on a fresh profile or after a reload; the tool
  retries by itself. Twice: stop and wait, do not force the password.
- **Hung tab (`Could not attach to the G-MES tab`)** - the tool restarts the
  automation browser once. Never replace a tab from outside; that closes the whole
  browser (84.18).
- **Result unchanged after Inquiry** - suspect a static table or a result left by an
  earlier probe: clear it with `--close-tabs` and record from a fresh screen.
- **Lock held** - see rule 5 in section 1.
- **Bare replay refused with "the remembered screen shape changed"** - look at the
  screenshot first. If the screen is fine, an older recording has gone stale
  (`P1112UM00`, 84.22): back up `screens/<CODE>.json` outside the repo, record again
  with `--relearn` using the same scope, then replay bare. Never edit the profile by hand.
- **"the result looked like static content ... not remembered"** - your own earlier run
  left its result in that window. After ANY exploratory run of a screen (to see which grid
  fills, what a date looks like) run once with `--close-tabs` (add `--export none`), then
  record from the fresh window. The warning is the tool being right (83.7, 84.22).
- **Which of several grids?** Do not guess and do not export to find out. Run once with
  `--export none` and your best hypothesis for `--grid`, then read
  `python gmes_data.py read <FORM> <dataset> --limit 0` for every candidate dataset: the
  one that holds rows is the report (columns and counts only - never print the rows).
  Confirm with a screenshot. Find the form name with `python gmes_data.py forms`.
- **`--set` says "ambiguous"** - the column is bound on several sub-forms. The visible one
  is chosen automatically (84.21); if it still says ambiguous, two are visible or none is:
  read `describe` and screenshot before choosing.
- **`--verify` refuses with dates OUTSIDE the requested range** - do not treat this as
  "nothing to verify" and route around it with `--set`. It can mean the filter genuinely
  let another day's rows through (Q3211UM00, 84.23) - a real finding, worth reporting, not
  hiding. Leave the screen unrecorded and ask, unless you can explain the mismatch from
  evidence.
- **`--verify` refuses with the SAME day but a longer value** (`20260920083443` vs
  `20260920`) - the column carries an embedded time; `--verify` cannot match it exactly
  yet (84.24). Confirm the day is right from a screenshot, then record with `--set`
  instead of `--from/--to`, and say plainly that the day was confirmed by screenshot, not
  by `--verify`.
- **No date field at all, and the screen wants a specific ID instead of a scope** (a
  `CN/SN/IMEI` box, "Enter the character") - the VD+date recipe does not apply. Stop and
  ask what identifier to use rather than guessing one (Q3442UM00, 84.25).
- **A screen genuinely has zero rows for a single day** - not every empty result is a
  wrong grid or a lost search. If a wider window (`--export none` on a few candidate
  single days) shows other days DO have rows, the screen is just sparse (like
  `P3131UM00`'s empty Fridays); record on the nearest day that has real data and say so,
  rather than forcing "yesterday" through a genuinely empty answer.
- **A screen needs to export somewhere other than the usual output folder** - pass
  `--output-dir`/`--export` explicitly on a successful run; if either differs from the
  tool's default, it is PINNED and every later bare replay (CLI or the interactive
  front end's Replay) writes there automatically, no flags needed again (84.28). Before
  trusting `--export both` on an unfamiliar network share, confirm the share allows
  deleting/renaming a file it just accepted - a share that denies delete can silently
  lose an already-successful Excel export when the CSV step's own rename fails and the
  tool's cleanup removes both. `--export xlsx` alone sidesteps that. Always confirm a
  claimed destination FROM the destination (`dir /a` on the share), not from the log.
- **"screen code must be a simple full G-MES screen code"** - fixed in 84.20; if a NEW
  code shape is ever refused, sweep the catalogue for the shape rather than patching one
  code.
- **A "client-side filter" warning (`24 of 66 rows are shown`)** - the export is what the
  screen shows. Report it; whether the hidden rows are wanted is the owner's decision.
- **No date to verify in the data** (or it sits in a column NAME like `A20260920`) - record
  with `--set`, say "not verified" in your report, and check the header/breadcrumb on a
  screenshot (`Period 2026-09-20 ~ 2026-09-20`) as your own evidence.
- **Several UI numbers in one message** - resolve them all with one `find` each on a
  running browser, replay the already-recorded ones first, describe the new ones together
  (`describe A B C` in one command), then record one at a time. Ask the owner only about
  the ones that need a decision (a missing division) and carry on with the rest meanwhile.
If you meet a state that is **not in any table** - stop, take a screenshot, report
what is on screen, and do not fire further shortcuts into it (CLAUDE.md 3.9).

---

## 6. Batch and schedule

Batch replays recorded screens sequentially, each isolated from the others, on one
browser session.

```powershell
python gmes_batch.py list                       # recorded screens, numbered
python gmes_batch.py plan  all                  # what WOULD run; no browser
python gmes_batch.py run   all --date yesterday --export both
python gmes_batch.py run   1,3,5-7              # by number
python gmes_batch.py run   Q2111UM00 P3111UM00  # by code
python gmes_batch.py run   all !3               # all except number 3
python gmes_batch.py save  morning all !3       # a named list  (run with @morning)
python gmes_batch.py run   @morning
python gmes_batch.py batches                    # saved lists
python gmes_batch.py schedule morning --at 06:30 --weekdays --date yesterday
python gmes_batch.py schedules                  # what is scheduled (and health)
python gmes_batch.py run-scheduled morning      # start the task right now
python gmes_batch.py unschedule morning         # remove the schedule (batch kept)
```

- **Date policy** `--date`: `yesterday` (default), `today`, `-N`, `keep` (each
  screen's own remembered dates), `YYYYMMDD`, or `YYYYMMDD:YYYYMMDD`. It applies to
  typed dates too. A profile recorded over a range runs ONE day under a one-day
  policy - the plan says so.
- **Always `plan` first** and read every `skipped` reason and every "recorded over"
  note. The plan needs no browser and touches nothing.
- **Read the summary, not the exit code.** Each screen has its own status; anything
  other than `ok` is a failure to explain. One failing screen does not stop the
  rest, and the report is written even if a screen was interrupted.
- **Schedules** are created for the current Windows user and run only while that
  user is signed in (the credentials are DPAPI-bound and the browser needs a real
  desktop). A locked-but-signed-in session is untested (Open Item 58). Prove a new
  schedule once with `run-scheduled`, then look at the log and the report - four
  defects appeared only under Task Scheduler (83.2).
- A browser that dies mid-batch is restarted and the batch continues, at most twice.
- Batch output goes to a `batch_<time>` folder (`--flat` for the usual folder). It
  grows without limit (Open Item 57): mention disk space for long-running schedules.

### If the whole run dies with no error - not just a browser reconnect

Different from the browser-dies-mid-batch case above (that one self-heals and the
log shows exactly what happened). This is: the `python` PROCESS ITSELF disappears -
no traceback, no final log line, the shell wrapper's own exit code (not one of
0/1/2/3/4). Seen once, cause not established (HISTORY.md Phase 85.12, Open Item 79).
Evidence is perishable - Windows Event Viewer entries roll over and a killed
process leaves nothing of its own - so act immediately, in this order:

1. **Do not re-run yet.** First capture what is still there:
   ```powershell
   Get-Process python -ErrorAction SilentlyContinue
   Get-CimInstance Win32_Process -Filter "Name='chrome.exe'" |
     Where-Object { $_.CommandLine -match 'GMES_Automation' } |
     Select-Object ProcessId, CreationDate, CommandLine
   Get-WinEvent -FilterHashtable @{LogName='Application'; StartTime=(Get-Date).AddMinutes(-10)} -ErrorAction SilentlyContinue
   Get-WinEvent -FilterHashtable @{LogName='System'; StartTime=(Get-Date).AddMinutes(-10)} -ErrorAction SilentlyContinue |
     Select-Object TimeCreated, Id, ProviderName
   Get-WinEvent -FilterHashtable @{LogName='Microsoft-Windows-TaskScheduler/Operational'; StartTime=(Get-Date).AddMinutes(-10)} -ErrorAction SilentlyContinue
   ```
   Save this output before it ages out - it is the only forensic evidence a silent
   kill leaves behind, and it is gone within minutes.
2. **Check the tool's own log**, `logs\gmes_<date>.log` - it flushes every line
   immediately, unlike a piped `python` process's own stdout (which buffers in
   blocks unless run with `python -u`; always use `-u` for a live-monitored batch,
   see below). The last line it wrote is the true last thing that happened.
3. **Close the orphaned browser gracefully** - never `taskkill`:
   `python -c "import cdp_common; cdp_common.close_browser()"` (CLAUDE.md 2.6).
   Verify with the `Get-CimInstance` command above that the `GMES_Automation`
   Chrome processes are actually gone before moving on.
4. **The stale lock self-heals** on the next `acquire_run_lock()` call (dead-pid
   detection, "an old run lock was removed") - do not delete it by hand.
5. **Restart with unbuffered output** so the next attempt is actually watchable
   live, not reconstructed after the fact: `python -u gmes_batch.py run ...`,
   piped to a log file, `run_in_background`.
6. **Tell the owner it happened** even if the retry succeeds. A silent process
   death during a live production run is worth knowing about regardless of cause.
7. If it recurs, the priority is catching it WHILE it is happening - a live
   Task Manager / Resource Monitor watch, or Sysinternals Process Monitor
   capturing the exact moment and who/what ended the process - not another
   after-the-fact log read, which has already been tried and came up empty once.

---

## 7. A worked example: a UI number nobody has recorded

Owner: "**M1642** record this."

1. `python gmes_batch.py list` - is it already there? (Here it was: recorded earlier
   the same day. The correct move is then **not** to re-record blindly but to replay
   it bare and report the result, and to ask only if a different scope is wanted.)
2. If new: `python gmes_report.py find M1642` - confirm code, title, menu id.
3. `python gmes_report.py describe M1642UM00` - one grid; Period is a month range
   (`YYYYMM`); no date column has values, so the period cannot be row-verified.
4. Decide: division VD present; period "yesterday" means yesterday's **month**; record
   with `--set` for the month; state that the result was not date-verified.
5. Record, then **replay bare yourself**:
   `python gmes_report.py run M1642UM00`
   -> `division : VD (screen confirms VD)`, `inquiry : 236 rows`, `csv : 236 rows`,
   `1/1 succeeded`, plus the honest warning that the period was typed with `--set`.
6. `python gmes_batch.py plan M1642UM00` -> `1 ready`.
7. Field-notes row in PROJECT_EXPERIENCE.md section 20 (rows describe operators: do
   not print them); HISTORY entry if anything new was learned; suites; commit.
8. Report: what was proven, what was not, files produced, anything the owner must decide.

---

## 8. Stop and ask the owner when

- VD (or the requested division) is not in the screen's tree.
- No date exists anywhere in the result to verify the day.
- Two comparable grids sit on different datasets and no evidence separates them.
- Another live run holds the lock, or sign-in fails twice.
- The action would write to G-MES, delete data, touch a credential store or profile
  listed above, or push to GitHub without having been asked.
- The tool refuses and you cannot say from evidence whether the tool or the screen is
  wrong.

## 9. Never

- Report a recording as done without your own bare replay.
- Pass `--verify` with a guessed column, or `--grid` without evidence.
- Loop on sign-ins, force the password, kill a browser, delete a lock held by a live
  process, or delete/refresh a protected profile or credential file.
- Print people-level rows, tokens, or commit exports and screenshots.
- Sleep a fixed time instead of polling for the control you need.
- Build a second engine beside the flat one (CLAUDE.md 0).

## 10. Report template (what to tell the owner)

```
Screen:      <CODE> <title> [menu id]        Recorded: new / already recorded
Scope:       division <X>, period <what was used>, mode <Daily/Monthly/...>
Bare replay: PASSED / FAILED  - <rows> rows in <s>s, CSV <n> rows, files <names>
Proven:      <screen, division, grid, date-verified by column X | not verifiable because ...>
Not proven:  <every warning line, in plain words>
Batch:       plan says ready / skipped because ...
Docs:        <HISTORY phase, field-notes row>     Commit: <hash> (not pushed)
Needs you:   <decisions, if any>
```

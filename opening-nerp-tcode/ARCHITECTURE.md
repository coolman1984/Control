# G-MES architecture

**Current, as of 2026-09-13 (HISTORY.md Phase 57).** The flat legacy engine
is the one G-MES implementation in this repository. See
[CLAUDE.md](CLAUDE.md) section 0 for the authority on this, and
[CAPABILITY_RESCUE_MAP.md](docs/history/CAPABILITY_RESCUE_MAP.md) for what a briefly-tried
alternative architecture (`src/gmes`, 2026-09-12 to 2026-09-13) taught and
which of its ideas were ported, preserved, or ruled out with evidence.

**This is a G-MES-only project.** A second system, N-ERP (SAP GUI for HTML),
was removed in HISTORY.md Phase 72 along with its half of `cdp_common.py` —
every symbol verified unused by any G-MES file first. It is recoverable from
branch `archive/nerp-before-removal`, and its skill document is kept at
[docs/history/SKILL.md](docs/history/SKILL.md).

## Entrances

```text
GMES_Workflow.bat
  no arguments   -> python run_gmes_workflow.py       (guided interactive workflow)
  with arguments -> python gmes_report.py run %*      (direct command)
```

Both branches reach the same flat modules and never a package. This is
mechanically enforced, not just documented:
`tests/test_legacy_entrance.py` imports each entrance in a fresh
interpreter and asserts zero `gmes.*` package modules load, and checks the
`.bat` text (case-insensitively) for `PYTHONPATH`, `python -m gmes`, or
`gmes.bat` - the three ways an entrance drifted to a package engine before
(HISTORY.md Phase 56).

## Modules

```text
gmes_browsers.py          Browser-neutral Chromium layer: which browsers are installed, which
                          one Windows opens https with, what profiles they hold, and the
                          FIRST-RUN bootstrap that copies the profile the employee already
                          uses (Phase 75). Chrome and Edge differ by a row in its BROWSERS
                          table, not by a code path. Imports nothing from cdp_common, so the
                          dependency runs one way and there is no second "where is Chrome".

cdp_common.py            The CDP transport: launch, connect, evaluate, click, screenshot.
                          Every entrance passes through it, so a change here must
                          keep tests/test_cdp_common.py and the G-MES suites green.
                          Reduced from 973 to 599 lines in Phase 72 when its
                          N-ERP half was removed.

gmes_credentials.py       DPAPI credential store (%LOCALAPPDATA%\GMES_Automation\credentials.dat)
gmes_login.py             Unattended sign-in: AD SSO attempt, form-login fallback, notice popups
gmes_common.py            Shared G-MES helpers: tab/connection resolution, popups, signed-in state,
                          click/read primitives (js_find_by_id, click_by_id, is_logged_in)
gmes_data.py              Nexacro dataset read/write
gmes_profile.py           Record/replay: screen fingerprints, remembered field/grid/tree references
gmes_core.py              THE screen driver: open, filter, Inquiry, verify, export, profile-save
gmes_open_screen.py       Open any of the 809 screens by code or name (catalogue search)
gmes_report.py            CLI over gmes_core: describe / run / find any UI number
gmes_batch.py             Batch runs over gmes_core: selection, date policy, plan, isolated run,
                          reports, saved lists, CLI (Phase 83)
gmes_schedule.py          Windows Task Scheduler side of a scheduled batch (Phase 83)
run_gmes_workflow.py      Guided interactive front end (the GMES_Workflow.bat no-arg path)
gmes_library.py           What the front end shows offline: report statuses, run history,
                          plain-language errors, opening a produced file (Phase 93)
gmes_daily_prodplan.py    The nightly Production Plan export - a specialized caller of gmes_core,
                          with its own subtotal-row filtering and atomic CSV write
gmes_ui.py                Presentation only (ASCII-degrading terminal rendering)
gmes_log.py               Redacted, tee'd operation log

gmes_preflight.py         Read-only preflight (Phase 79.4) - Python version, websocket-client,
                          a supported browser, a writable runtime directory. Unlike the tools
                          below, this IS part of the supported entrance chain: GMES_Workflow.bat
                          runs it before either branch and stops on failure, and it is covered
                          by tests/test_legacy_hardening.py like the rest of that chain.

gmes_connect.py  gmes_inspect.py  gmes_find.py  gmes_dump.py  gmes_probe_*.py  gmes_demo.py
    Read-only reconnaissance/inspection tools (CLAUDE.md 4.2) - not part of the supported
    entrance chain above, and not covered by the same test gate.

gmes_sso_diagnose.py      Read-only AD SSO network-capture probe (HISTORY.md Phase 56/57.9) -
                          answers "what does the popup actually do" without ever touching
                          the password form. Independent tool, imports only gmes_common/
                          gmes_login/cdp_common.
```

## Runtime state

- **Credentials**: `%LOCALAPPDATA%\GMES_Automation\credentials.dat`, DPAPI-encrypted,
  written only by `gmes_credentials.py`.
- **Screen knowledge**, split in two (Phase 73.3):
  - `screens_known\<CODE>.json` - the STRUCTURE: which control is the from-date,
    which grid holds the result, where the division tree lives, the fingerprints.
    True for anyone with access to the screen, so it is **committed and ships**.
    Produced by `gmes_profile.export_shippable()` via an allowlist.
  - `screens\<CODE>.json` - what a run HERE used: division, dates, filter values,
    the command, the row count. **Git-ignored** - a filter value can be a
    production order number. `load()` lays this over the shipped half.
- **Browser profile**: `%LOCALAPPDATA%\GMES_Automation\profiles\default`
  (`GMES_PROFILE_DIR` overrides). Built on the first run by copying the user's
  Chrome or Edge profile on THIS machine, so a G-MES session they already have
  comes across (Phase 75); built empty and seeded, exactly as in Phase 73.1,
  when there is nothing to copy from or `GMES_BOOTSTRAP=off`. Either way the
  build happens once. A copied profile cannot be moved between machines at all
  - Chrome 140+ binds cookie encryption to the machine (Phase 73.1), which is
  what `browser.json`'s machine fingerprint exists to detect.
- **First-run record**: `%LOCALAPPDATA%\GMES_Automation\browser.json` - which
  browser and profile were chosen, and the machine they were chosen on. Written
  atomically, carries no secret, and is the single source of truth any process
  reads to resolve the same profile (and therefore the same
  `DevToolsActivePort`) as the process that started the browser.
- **CDP port**: assigned by the OS and read back from `DevToolsActivePort`
  inside the profile, so the port is a property of the profile rather than a
  constant (Phase 73.2). `NERP_CDP_PORT` pins it if needed.
- **Logs/screenshots**: written next to the scripts by default
  (`cdp_common.screenshot_on_failure`/`gmes_log.py`) - not unified under one root; see
  docs/history/CAPABILITY_RESCUE_MAP.md's "Runtime-path handling" entry for the (unbuilt) idea of doing so.
- **Browser profile**: `%LOCALAPPDATA%\Google\Chrome\CDP Profile`, a debuggable COPY of the
  user's real Chrome Default profile (CLAUDE.md 2.1a - never the real one, never deleted or
  refreshed automatically).
- **Batch state** (Phase 83), all git-ignored:
  - `screens\batches\<name>.json` - saved batch lists (screen codes, date policy, export).
  - `Data Hub Folder\GMES\batch_<time>\` - the files of ONE batch run.
  - `logs\batches\batch_<time>.json|.txt` - the report of a run, written even when
    everything failed.
  - `logs\batches\batch_<time>_summary.html` and `latest_summary.html` - the
    one-page morning summary: counts, each failure's reason and screenshot
    (HISTORY.md Phase 92.7).
  - `logs\scheduled_<name>.log` - everything an unattended run printed (launched
    with `python -u`, so a run that hangs still leaves evidence).
  - `schedules\run_<name>.cmd` - the launcher a scheduled task runs. It embeds this
    machine's Python and project paths, so it belongs to this machine only.
  - **Windows Task Scheduler** tasks named `GMES_Batch_<name>`: registered for the
    current user, interactive logon, limited run level, no stored password. They
    run only while that user is signed in to Windows.
- **The run lock** `.gmes_run.lock`, inside the automation PROFILE directory
  (not this checkout's `screens/` - HISTORY.md Open Item 40 found that a lock
  living in the repo only ever protected two runs from the SAME checkout, not
  two checkouts sharing one profile): one run at a time drives the browser
  (CLAUDE.md, HISTORY.md Phase 70). A batch takes it, a second run refuses with exit 3.

## Known, not-yet-fixed gaps

Tracked in [CAPABILITY_RESCUE_MAP.md](docs/history/CAPABILITY_RESCUE_MAP.md)'s Final Decisions table with
evidence for each - notably: `gmes_credentials.py`'s `save()` writes directly rather than atomically
(temp file + rename); `gmes_data.py` reads a dataset in one unbounded call rather than paged.
None of these has a recorded live incident; none is fixed in HISTORY.md Phase 57.

# Code Review and Remediation Plan

Reviewed: 2026-09-22
Commit: `be2bca30cd6be9757ef774e83270201fdb42487e` (`main`)
Scope: the flat root G-MES engine, command-line entry points, browser/profile handling, exports, logging, scheduling, tests, and documented open items.

## Executive verdict

The codebase has unusually strong defensive checks for a browser automation project: it verifies UI outcomes, refuses many ambiguous states, protects the real browser profile, keeps credentials in DPAPI, has seven substantial offline suites, and records live failures in `HISTORY.md`.

I would still request changes before describing it as fully production-hardened. The highest-priority problem is a confirmed secret-redaction bypass in exception logging. The next priorities are making the run lock protect the actual shared browser profile, centralizing browser shutdown, and resolving the two recordings that currently fail their own bare replay.

This review did not change source code. It records findings and proposed solutions only.

## Findings, ordered by priority

### R1 — Critical: exception logging bypasses secret redaction

Evidence:

- `gmes_log.py:60-70` redacts text passing through `_Tee.write()`.
- `gmes_log.py:113-121` writes `note()` text directly to the log without redaction.
- `gmes_log.py:124-129` sends full exception tracebacks through `note()`.
- A harmless probe using `RuntimeError("password=DEMO_SECRET")` produced `LEAKS`: the dummy value was present unchanged in the in-memory log.

Impact:

The project promises that credentials and session tokens never reach logs. Any exception message containing a password-, token-, cookie-, or authorization-shaped value bypasses that guarantee. This is a confirmed mechanism; whether a real production exception has already contained such a value was not investigated and should not be tested with real secrets.

Recommended solution:

1. Create one canonical `redact_text(text)` function.
2. Apply it at every disk sink, including `_Tee.write()`, `start()` command logging, `note()`, `failure()`, and any report writer that stores exception text.
3. Redact the fully formatted traceback immediately before writing it, not only selected exception fields.
4. Add regression tests that send dummy secrets through every public logging path and assert that neither the secret nor a partial value is present.
5. Mutate or temporarily remove the redaction in a test rehearsal and prove the new test fails, following the project's sabotage-proof rule.

### R2 — High: the run lock does not reliably protect the shared browser

Evidence:

- `gmes_core.py:78` stores the lock under this checkout's `screens/` directory.
- Two different checkouts can use the same `%LOCALAPPDATA%\GMES_Automation` profile while holding different lock files. This is already documented as `HISTORY.md` Open Item 40.
- `gmes_core.py:204-208` unconditionally deletes the lock on release.
- `tests/test_gmes_core.py:4828-4831` is named as an ownership test, but only checks normal acquire-then-release. It does not replace the lock with another owner's lock and verify that the first process leaves it alone.

Impact:

Two checkouts can simultaneously drive one Chrome/CDP session, causing silent cross-run interference. There is also a smaller race: if a stale lock is replaced while the original process is still alive, the original process can later delete the replacement owner's lock.

Recommended solution:

1. Put the lock beside the automation profile/state, not inside the repository.
2. Key it by the canonical resolved profile directory, for example with a SHA-256 digest of the normalized path.
3. Store `{pid, process_start_time, nonce, profile_path}` in the lock.
4. Return an ownership token from `acquire_run_lock()` and require that token in `release_run_lock()`.
5. Before deletion, reopen the file and verify the nonce still matches. If it does not, do not remove it.
6. Add tests for two checkouts sharing one profile, lock replacement while the old owner is alive, PID reuse, and release by a non-owner.

### R3 — High: sensitive-name filtering is incomplete and duplicated

Evidence:

- `gmes_data.py:340-342` and `gmes_log.py:40-43` maintain separate regular expressions with the same short denylist.
- The list catches `password`, `passwd`, `pwd`, `token`, `secret`, `authorization`, and `cookie`.
- It misses plausible names such as `credential`, `sessionKey`, `sessionId`, `jwt`, `apiKey`, `accessKey`, and `authKey`; this is already documented as `HISTORY.md` Open Item 39.

Impact:

A newly encountered dataset can export a credential-like column to CSV or log a credential-like assignment simply because its name uses a synonym not in the current enumeration. Two copies of the policy can also drift when only one is updated.

Recommended solution:

1. Move sensitive-name classification and text redaction into one small root-level security/redaction module used by both logging and export code.
2. Expand the conservative denylist to cover credential, session, JWT, API/access/auth key, bearer, and refresh/access-token variants.
3. Keep the generic unknown-screen exporter deny-by-default for suspicious column names; allow an explicit reviewed safe-column override only when a real screen proves a false positive.
4. Add table-driven tests for mixed case, separators, prefixes/suffixes, and every known token column.

### R4 — High: browser shutdown is fragmented and contradicts the current operating rule

Evidence:

- `gmes_report.py:333-339`, `gmes_batch.py:756-761`, `gmes_daily_prodplan.py:384-391`, and `gmes_demo.py:349-356` call `LAST_CHROME_PROCESS.terminate()` directly.
- `cdp_common.py:711-742` already provides the graceful, verified CDP `Browser.close` path.
- A reused `--keep-open` browser has no `LAST_CHROME_PROCESS` handle in the next process and therefore remains running; this is `HISTORY.md` Open Item 45.
- Current `CLAUDE.md` section 2.6 says the automation browser is to be closed through its own CDP endpoint.

Impact:

Cleanup behavior depends on which entry point was used and which process originally launched Chrome. Direct process termination is abrupt, while reused browsers can be left behind. This does not target the user's real Chrome profile, but it weakens profile integrity and lifecycle predictability.

Recommended solution:

1. Add one `stop_automation_browser(keep_open=False)` helper in `cdp_common.py`.
2. When `keep_open` is false, call `Browser.close` through the active profile's CDP endpoint and poll until the endpoint disappears.
3. Never use a process-name kill. Keep `_abandon_launch()` only for a process this call started but could never attach to.
4. Decide and document one policy for a reused browser. My recommendation: a one-shot command closes it unless `--keep-open` is explicit; the interactive workflow owns its longer session.
5. Test every entry point against started-here, reused, already-gone, and refuses-to-close cases.

### R5 — High: two recorded screens fail their own replay contract

Evidence:

- `P3111UM00` fails a fresh bare replay because the Period values do not remain as set; the error appears to show the values swapped or reverted. See `HISTORY.md` Open Item 70 and `PROJECT_EXPERIENCE.md:1124`.
- `P3151WM00` can be relearned and replayed once, then fail the next replay with a shape change under an internal tab. See Open Item 71.

Impact:

These recordings cannot be treated as safe unattended automation. A profile that cannot replay itself immediately violates the project's standing recording rule.

Recommended solution:

For `P3111UM00`:

1. Describe the screen and capture the exact bound dataset row, control value, mask, and stable path before writes.
2. Apply only the start value, reread; then only the end value, reread. Determine whether a change event rewrites the paired field or the bindings are reversed.
3. Fix the date-role mapping or commit/change-event order based on that live evidence.
4. Relearn, run a bare replay twice in fresh work windows, and verify result rows before declaring it fixed.

For `P3151WM00`:

1. Record the active internal tab/component identity during discovery.
2. Compare fingerprints for each tab state to prove whether the shape is tab-dependent.
3. If it is, store and restore the semantic tab identity before fingerprint comparison; do not simply weaken the fingerprint.
4. Relearn and run at least two bare replays from different prior tab states.

### R6 — Medium: date verification rejects real timestamp columns

Evidence:

- `gmes_core.py:1687-1729` compares the full digit sequence for single-value verification.
- `gmes_core.py:1757-1783` requires exactly eight digits for date-range verification.
- Real screens `Q3211UM00` and `Q3341UM00` return `YYYYMMDDHHMMSS`; this is `HISTORY.md` Open Item 66.

Impact:

The tool cannot row-verify those screens even when every returned timestamp belongs to the requested day. Users must use weaker `--set`-only recording or leave the screen unrecorded.

Recommended solution:

1. Add a strict date-normalization helper for verification values.
2. Accept exactly eight date digits, or exactly fourteen digits when the first eight form a real date and the final six form a valid time.
3. Compare the extracted day to the requested day/range while retaining original values in diagnostics.
4. Do not truncate arbitrary long digit strings; require a recognized format.
5. Add boundary tests for midnight, `23:59:59`, invalid dates/times, mixed formatting, and genuine non-date identifiers.

### R7 — Medium: empty month/year fields can be written with the wrong width

Evidence:

- `gmes_core.py:1355-1368` infers `YYYY`, `YYYYMM`, or `YYYYMMDD` solely from the field's current value.
- An empty field defaults to eight digits even if its control is designed for `YYYYMM`; this is `HISTORY.md` Open Item 41.

Impact:

G-MES can accept the wrong width and answer a different query without raising an error.

Recommended solution:

1. Extend discovery to capture control mask/format metadata and maximum length.
2. Store the proven date width in the profile's structural reference.
3. Resolve width in this order: explicit mask/format, recorded proven width, current value.
4. If an empty field remains ambiguous, refuse and ask for an explicit width rather than assuming eight digits.

### R8 — Medium: a manifest write failure can misreport successful exports

Evidence:

- `gmes_report.py:313-332` completes the reports and prints the summary before writing the optional manifest.
- A manifest failure cleans only its partial file and then re-raises. No surrounding handler converts it into a warning.
- The successfully exported report files remain on disk, but the CLI exits with a traceback/non-zero status.
- There are no manifest failure-path tests.

Impact:

Automation can report the whole command as failed after the requested data was successfully exported, recreating the same “delivered file reported as failure” class that profile-save handling explicitly prevents at `gmes_core.py:3888-3932`.

Recommended solution:

1. Isolate manifest writing in `write_manifest_safely()`.
2. Preserve the report exit status and file list if only the optional manifest fails.
3. Print a clear warning and include it in the run result/log.
4. Add tests for missing parent directory, permission denial, destination-is-directory, and atomic replace failure.

### R9 — Medium: `--close-tabs` is accepted for `describe` but ignored

Evidence:

- `gmes_report.py:201-202` exposes `--close-tabs` globally.
- `gmes_report.py:274-288` calls `cmd_describe()` without passing or applying that option.
- `cmd_describe()` at `gmes_report.py:59-152` opens a work screen and never closes it.
- The behavior is confirmed as `HISTORY.md` Open Item 49.

Impact:

A describe/probe can leave stale work windows and results that affect the next run's unchanged-result and shape checks.

Recommended solution:

Pass close intent into `cmd_describe()` and close the exact opened screen in a `finally` block, reporting whether closure was proven. Alternatively, make the parser reject `--close-tabs` for commands that do not support it; silently ignoring the flag is the worst option.

### R10 — Medium: full dataset reads cross CDP as one unbounded object

Evidence:

- `gmes_data.py:215-250` builds every selected row and column in one JavaScript array and serializes one JSON result.
- `gmes_data.py:308-310` defaults to `limit=-1`.
- Verification and CSV export routinely request all rows.
- This is documented as `HISTORY.md` Open Item 44.

Impact:

Large screens can exceed browser/CDP message limits, consume substantial memory twice (browser and Python), or time out after the query has already succeeded.

Recommended solution:

1. Read metadata first: path, columns, total count, filter state, and a dataset identity/version marker if available.
2. Fetch fixed-size pages, for example 500-2,000 rows.
3. Confirm the total and identity remain stable between the first and last page; restart or refuse if the dataset changes during export.
4. Stream CSV rows to the partial file instead of retaining the full dataset in Python.
5. Keep small reads (`limit=0`, samples) on the existing fast path.

### R11 — Medium: `immutable=1` is used against live browser databases

Evidence:

- `gmes_browsers.py:457-503` queries Chrome/Edge Cookies and History in place.
- The URI at `gmes_browsers.py:493` asserts `mode=ro&immutable=1` even though a running browser may still modify the file.
- This is documented as `HISTORY.md` Open Item 38.

Impact:

SQLite's immutable promise is false for a live browser database. Reads can miss WAL content or observe an unsafe/inconsistent snapshot, causing the wrong source profile to be selected.

Recommended solution:

Use `mode=ro` with a short busy timeout and treat a locked/busy database as “unknown,” not “no G-MES evidence.” If a reliable snapshot is required, copy the database together with its WAL state into a tool-owned temporary directory and query only that snapshot; never alter the real browser profile.

### R12 — Medium security risk: CSV output is vulnerable to spreadsheet formula interpretation

Evidence:

- `gmes_data.py:371-374`, `gmes_core.py:2751-2754`, and `gmes_daily_prodplan.py:241-244` write dataset values directly with `csv.DictWriter`.
- No handling exists for cells beginning with `=`, `+`, `-`, or `@`.

Impact:

If a G-MES value is attacker-controlled or simply formula-shaped, opening the CSV in Excel can interpret it as a formula. No malicious value was observed during this review, so this is a risk, not a confirmed production incident.

Recommended solution:

Define the output contract explicitly:

- For an Excel-safe human CSV, prefix formula-leading text cells with an apostrophe and record that transformation.
- For a machine-faithful CSV, keep raw values but label/document it as not safe to open directly in a spreadsheet and consider a separate safe export mode.

Add tests for all four formula prefixes, whitespace before a prefix, numeric negatives that must remain numeric, and normal text.

### R13 — Low: two command examples do not match parser behavior

Evidence:

- `gmes_report.py:11-13` shows a dated `--dry-run` without `--verify`.
- `gmes_report.py:18-20` shows a dated multi-screen run without `--verify`.
- `gmes_report.py:239-241` rejects every dated `run` without `--verify`, including the shown dry run.

Impact:

Copying either example produces an immediate usage error.

Recommended solution:

Either add `--verify` to the examples or intentionally exempt `--dry-run` from the verification requirement because it never clicks Inquiry or exports. My preference is to exempt dry-run and keep `--verify` mandatory for real runs. Add a parser-level test for every executable example in the module docstring.

### R14 — Maintainability: critical workflows are too large to review safely

Evidence:

- `gmes_core.py` is 4,002 lines; `run_screen()` is about 440 lines.
- `gmes_login.main()` is about 342 lines.
- `run_gmes_workflow.one_run()` is about 229 lines.

Impact:

The long functions mix policy resolution, browser effects, verification, export, persistence, and error reporting. This raises regression risk even with extensive tests and makes it difficult to prove cleanup behavior across every return path.

Recommended solution:

Keep the owner's “one flat root engine” decision. Do not recreate `src/gmes/` or a second engine. Within the flat engine, extract stage-oriented helpers with explicit inputs/outputs:

1. resolve replay intent;
2. apply and verify screen intent;
3. execute and settle inquiry;
4. verify result;
5. export artifacts;
6. persist profile;
7. cleanup lifecycle.

The goal should be to remove branches from `run_screen()`, not merely move the same shared mutable state into wrappers.

## Known operational limitations that should remain explicit

These are not all code defects, but they materially affect safe use:

- `Q3211UM00` returned rows from the day after the requested range; do not record it until the owner identifies which result date the “Plan” period should constrain (Open Item 67).
- Older recordings may fail current fingerprints and need one controlled `run all` audit with individual relearn decisions (Open Item 63).
- `B3350UM00` and `BB210UM00` export only the screen's currently visible client-side subset; the owner must decide whether hidden rows are required (Open Item 65).
- The PC clock's “today” is not checked against a G-MES date source before the `yesterday` policy is applied (Open Item 56).
- Scheduled execution on a locked workstation and the recent scheduled-run fixes are not live-proven (Open Items 46 and 58).
- Old export folders have no retention policy and can eventually fill the disk (Open Item 57).
- A nested worktree still exists at `.worktrees/gmes-standalone-migration` and contains the removed donor engine. It is a separate Git worktree, not tracked production code, but keeping it physically under the main checkout makes recursive searches and tooling inspect two engines. Move it outside the repository or remove it only after the owner confirms it is no longer needed; do not delete it automatically.

## Proposed implementation order

### Phase 1 — security and concurrency

1. Fix the exception-log redaction bypass.
2. Centralize and broaden sensitive-name handling.
3. Move to a profile-scoped, ownership-token run lock.
4. Centralize graceful browser shutdown.

### Phase 2 — correctness gaps

1. Add strict timestamp-day verification.
2. Discover and persist date field width.
3. Fix `P3111UM00` and `P3151WM00` from live evidence, followed by two bare replays each.
4. Honor `--close-tabs` for describe.
5. Make manifest failure non-destructive to successful result reporting.

### Phase 3 — scale and robustness

1. Page/stream large dataset reads.
2. Stop asserting SQLite immutability on live browser files.
3. Decide and implement the CSV formula-safety contract.
4. Add a retention policy only after the owner chooses the retention period.

### Phase 4 — maintainability

Split the largest workflows into explicit stages while preserving the flat root engine and existing public entry points.

## Verification performed

- All seven documented offline suites passed on commit `be2bca3`:
  - `test_cdp_common.py`
  - `test_gmes_core.py`
  - `test_legacy_hardening.py`
  - `test_gmes_workflow.py`
  - `test_legacy_entrance.py`
  - `test_project_eye.py`
  - `test_browser_bootstrap.py`
- The exception-log redaction bypass was reproduced with a dummy secret only.
- No live G-MES inquiry, export, login submission, profile refresh, credential read, or browser-profile modification was performed for this review.
- Passing offline tests do not prove live Nexacro behavior; the project documentation correctly warns about this distinction.

### Concurrent worktree change observed

During the final status check, `gmes_batch.py` acquired an uncommitted change adding `_retarget_verify()` and forwarding a retargeted explicit verification value. I did not create, edit, or revert that change. It appeared after the clean `be2bca3` baseline inspection, so the findings and verification above deliberately refer to the committed baseline.

Before that concurrent change is committed, it needs its own review, focused regression tests, and the mandatory `HISTORY.md` behavior entry. In particular, tests should cover a single-day recording, a multi-day recording where the explicit value tracks the start or end date, a non-date explicit verifier, malformed old profile values, and the `keep` policy.

## Final recommendation

Do not begin with a broad refactor. First close R1-R4 with focused regression tests and a `HISTORY.md` entry for each behavior change. Then resolve the two broken recordings from live evidence. Only after those safety and correctness issues are stable should the large workflow functions be decomposed.

# G-MES automation

[![Offline test suites](https://github.com/coolman1984/opening-nerp-tcode/actions/workflows/tests.yml/badge.svg)](https://github.com/coolman1984/opening-nerp-tcode/actions/workflows/tests.yml)

Windows automation for **Samsung G-MES**, a Nexacro manufacturing execution
system, driven through the Chrome DevTools Protocol. It signs in unattended
with a DPAPI-stored credential, opens any screen the account can reach, sets
that screen's filters, runs its Inquiry, verifies what came back, and exports
it as G-MES's own Excel download. Excel only, since HISTORY.md Phase 94.1;
a CSV copy from the data layer is still available with `--export csv`.

It works against a **live production system**. Read [CLAUDE.md](CLAUDE.md)
before changing anything, and [HISTORY.md](HISTORY.md) before changing
automation behaviour — almost every failure this project has hit produced no
error at all, and the causes are rarely guessable from the code.

> **About the name.** The repository is called `opening-nerp-tcode` because it
> began as an N-ERP (SAP) T-code opener. N-ERP was removed in HISTORY.md
> Phase 72 and this is a G-MES-only project now; the name is historical. The
> N-ERP code is on branch `archive/nerp-before-removal`.

## Setup — on any PC

```powershell
python -m pip install -r requirements.txt   # websocket-client, and nothing else
python gmes_credentials.py set              # once per person, into their own DPAPI store
.\GMES_Workflow.bat                         # first run: builds its profile, signs in once
```

That is the whole install. Python 3.12 on PATH; Chrome is located
automatically (`CHROME_PATH` overrides). The only third-party dependency is
`websocket-client` — deliberately, because this runs in a locked-down
corporate environment.

**What happens on a new machine.** The tool gives itself a browser profile at
`%LOCALAPPDATA%\GMES_Automation\profiles\default`. On the **first run only**, it
builds that profile by copying the one you already use — Chrome or Edge,
whichever Windows has as your default browser — so the G-MES session you are
already signed in with can come across. **That is the intent, not a promise:** on\nthe one clean-machine rehearsal (HISTORY.md Phase 84.10) the copy completed but the\nsession did not sign in by itself - the first sign-in went through SSO with the saved\ncredentials, after one automatic retry.

That copy happens **once**, on this PC, and the rules around it are strict:

- Your real browser profile is only ever **read**. It is never launched, never
  debugged, never modified and never deleted.
- Because the browser keeps its cookie database locked while it runs, the first\n  run may ask you to close that browser once (on the rehearsal it did not, and the\n  copy finished with Chrome open). Every run after it does not care whether your\n  browser is open.
- Nothing is copied again afterwards. A second copy would overwrite the G-MES
  session the automation has since built up.
- If there is no Chrome or Edge profile to start from, the profile is created
  empty instead and the first sign-in is a real one — which is exactly how the
  tool behaved before, and still a fully working path.
- Prefer not to have your profile copied at all? Set `GMES_BOOTSTRAP=off` and
  you get the empty profile and a single real sign-in.

`python gmes_browsers.py` prints what it can see — your default browser, both
browsers' profiles, and what the first run recorded — and changes nothing.

Nothing carries a session between machines, and nothing can: Chrome 140+ binds
cookie encryption to the machine it is running on, so a copied profile cannot
decrypt its own cookies elsewhere. Each person signs in once, as themselves,
with their own credentials in their own DPAPI store.

What *does* travel with the tool is everything it has learned about the
screens — see `screens_known/` below — so a known screen works on day one
without anyone re-teaching it.

| Where | What | Shared? |
|---|---|---|
| `%LOCALAPPDATA%\GMES_Automation\credentials.dat` | Knox ID + password, DPAPI | Never — per Windows account |
| `%LOCALAPPDATA%\GMES_Automation\profiles\` | the tool's own browser profile | Never — machine-bound |
| `%LOCALAPPDATA%\GMES_Automation\browser.json` | which browser/profile the first run chose | Never — names the machine it was built on |
| `screens_known/` | screen structure: fields, grids, trees | **Yes — committed** |
| `screens/` | what your runs used: division, dates, values | Never — git-ignored |

## Running a report

`GMES_Workflow.bat` is the entrance. With no arguments it asks questions; with
arguments it runs a report directly.

```powershell
.\GMES_Workflow.bat                                     # guided, interactive
.\GMES_Workflow.bat P1112UM00 --division VD --from 20260909 --to 20260909 --verify planYmd
```

**Do not type `run` after `GMES_Workflow.bat`** — the launcher adds it. Typing
it twice makes the tool search for a screen called `RUN`. The word is only
needed when calling the CLI directly:

```powershell
python gmes_report.py run P1112UM00 --division VD --from 20260909 --to 20260909 --verify planYmd
python gmes_report.py describe P1112UM00      # what filters, grids and options a screen has
python gmes_report.py find "production plan"  # search the screen catalogue
python gmes_open_screen.py --find production
python gmes_data.py forms                     # which screens are open, and their datasets
```

A date-constrained run **requires** `--verify COLUMN`, because an export of
the wrong day looks exactly like an export of the right one.

## Setting up a new PC

1. **Install under a short folder**, for example `C:\gmes` - not a OneDrive or
   Documents path. A deep path breaks `git clone` (unless
   `git config --global core.longpaths true`), Windows' 259-character file limit, and a
   sync client holds fresh exports open. Clone with `git clone <repo> C:\gmes`.
2. **Python 3.10+ from python.org** (a Microsoft-Store Python may not start from a
   scheduled task), then `python -m pip install -r requirements.txt`.
3. **`python gmes_preflight.py`** - read-only. `FAIL` lines must be fixed (for example a
   `RemoteDebuggingAllowed=0` browser policy needs IT); `WARN` lines are worth reading
   (path length, cloud-sync folder, no saved sign-in yet).
4. **`python gmes_credentials.py set`** once, as the Windows user who will run it. The
   sign-in is stored encrypted for that user only; if their Windows password is ever
   *reset* (not changed), the tool says it cannot decrypt the file and asks for it again.
5. **The first run is slow and may ask twice.** A new browser profile has no cache, so
   the first sign-in and the first open of each screen take much longer; the first
   sign-in attempt can fail once ("SSO window never opened") and is retried
   automatically. Do not sign in over and over to test it - after a handful of
   sign-ins in a short time the SSO window stopped opening for a while.
6. Record a screen once (`GMES_Workflow.bat`), replay it, then use Batch. See
   [PROJECT_EXPERIENCE.md](PROJECT_EXPERIENCE.md) section 18 (recording) and 23 (a new PC).

## Batch runs

Replay several recorded screens together - all of them, a chosen few, or a
saved list - now or on a schedule. Answer **B** (Batch) in `GMES_Workflow.bat`,
or use the command line:

```powershell
python gmes_batch.py list                          # what is recorded, numbered
python gmes_batch.py plan all                      # what WOULD run; no browser, nothing touched
python gmes_batch.py run all                       # everything, for yesterday
python gmes_batch.py run 1,3,5-7 --date today      # a chosen few
python gmes_batch.py run all !Q2111UM00            # everything except one
python gmes_batch.py save morning 1,3,5-7          # save a list
python gmes_batch.py run --batch morning           # run a saved list
python gmes_batch.py schedule morning --at 06:30 --weekdays
python gmes_batch.py schedules                     # what is scheduled, next run, last result
python gmes_batch.py unschedule morning
```

- `run` needs an explicit selection - it never means "everything" by itself.
- Dates: `yesterday` (default), `today`, `-3`, `20260915`, `20260901:20260907`,
  or `keep` (each screen's own remembered dates). They are applied to every
  screen. A screen that cannot be run safely (not recorded here, or a date with
  no column to verify it against) is listed as **skipped** with the reason.
- One screen failing does not cancel the rest; three failures in a row, or a
  session that cannot be recovered, stop the batch. Files go to a
  `Data Hub Folder\GMES\batch_<time>` folder and a report to `logs\batches\`.
- The morning after, open `logs\batches\latest_summary.html`: what was
  delivered, what was not and why, with a screenshot of each failure.
- A batch whose dates come from the PC's clock (`yesterday`, `today`, `-N`)
  first compares that clock with the G-MES server's; if they are more than
  15 minutes apart it runs nothing and says so.
- Exit code: 0 all ok, 1 something failed, 2 usage, 3 another run holds the
  browser, 4 sign-in failed.
- In PowerShell a saved list is written `'@morning'` in quotes (a bare `@name`
  means something else there), or use `--batch morning`.
- **A schedule runs only while you are signed in to Windows** - the saved
  credentials and the browser need your session. A PC asleep at the time runs
  it on waking. Follow a run in `logs\scheduled_<name>.log`.

## Everyday commands

| Task | Command |
|---|---|
| Guided run | `.\GMES_Workflow.bat` |
| Describe a screen | `python gmes_report.py describe <CODE>` |
| Set up filters without querying | `... run <CODE> ... --dry-run` |
| Nightly Production Plan export | `python gmes_daily_prodplan.py` |
| Run several recorded screens | `.\GMES_Workflow.bat`, choose **3. Run several reports** from the main menu |
| Find a saved report, see if it is ready and how it last went | `.\GMES_Workflow.bat`, **4. Saved reports** - type words to search |
| Find the files of an earlier run, open its folder | `.\GMES_Workflow.bat`, **6. Recent runs and files** |
| Run every recording | `python gmes_batch.py run all` |
| Run a chosen few | `python gmes_batch.py run 1,3,5-7` |
| Schedule a saved list | `python gmes_batch.py schedule morning --at 06:30 --daily` |
| Read a dataset | `python gmes_data.py read <CODE> <dataset>` |
| What is on screen right now | `python gmes_inspect.py` |
| Live demo, three screens | `.\Demo_ForManagement.ps1` |

## Architecture

One engine: the flat modules at the repository root — `gmes_core.py` (the
screen driver), `gmes_login.py`, `gmes_common.py`, `gmes_open_screen.py`,
`gmes_data.py`, `gmes_profile.py`, over the `cdp_common.py` transport.
[ARCHITECTURE.md](ARCHITECTURE.md) has the full map.

Both `GMES_Workflow.bat` branches reach those modules and nothing else, which
`tests/test_legacy_entrance.py` proves in a fresh interpreter. A `src/gmes`
package was tried and removed in Phase 57; see
[docs/history/CAPABILITY_RESCUE_MAP.md](docs/history/CAPABILITY_RESCUE_MAP.md)
for what it taught and what was kept.

## Tests

Seven offline suites. None needs a browser or a network, and all must stay green.

```powershell
python tests/test_cdp_common.py
python tests/test_gmes_core.py
python tests/test_legacy_hardening.py
python tests/test_gmes_workflow.py
python tests/test_legacy_entrance.py
python tests/test_project_eye.py
python tests/test_browser_bootstrap.py
```

There is no mock for G-MES, by design — it sits behind corporate SSO and the
failures worth catching are live ones. A green suite guards decision logic; it
is **not** evidence that a run works. **No test command above is authorization
to use a live authenticated portal.**

## Documentation

| File | What it is |
|---|---|
| [CLAUDE.md](CLAUDE.md) | Operating rules. Read first. |
| [GMES_SKILL.md](GMES_SKILL.md) | 89 numbered G-MES gotchas, each earned live |
| [HISTORY.md](HISTORY.md) | Every incident, cause and fix — keep it updated |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Module map and where runtime state lives |
| [AGENT_PLAYBOOK.md](AGENT_PLAYBOOK.md) | How an AI agent records, replays and batches any UI number |
| [PROJECT_EXPERIENCE.md](PROJECT_EXPERIENCE.md) | The fast mental model for a newcomer |
| [HOW_TO_USE.md](HOW_TO_USE.md) | Usage guide (Arabic) |
| [docs/history/](docs/history/) | Kept for their reasoning, not their accuracy |

## Safety

Credentials live only in the DPAPI store at
`%LOCALAPPDATA%\GMES_Automation\credentials.dat` — never in source, arguments,
logs or commits. `%LOCALAPPDATA%\GMES\credentials.dat` belonged to the removed
package and is equally protected; no migration or inspection of either is
authorized.

The automation drives a profile of its own and never the user's real one. That
profile is built once, by reading the real one; from then on the real profile
is not touched at all, and no code path anywhere launches, debugs, modifies or
deletes it. Every G-MES operation is read-only: it sets filters and runs
queries, and has no code path that saves, submits, approves or deletes anything
in G-MES.
Exported data, run logs and learned screen profiles are all git-ignored and
must stay that way.

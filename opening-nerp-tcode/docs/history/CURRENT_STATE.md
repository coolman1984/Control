# Final restoration state

**Current as of 2026-09-13: complete.**

G-MES has one supported implementation: the flat root-level engine
(`gmes_core.py`, `gmes_login.py`, `gmes_common.py`, `gmes_open_screen.py`,
`gmes_data.py`, `gmes_profile.py`, `gmes_report.py`, and their focused
helpers). N-ERP remains active and shares `cdp_common.py`.

The experimental standalone package was removed. It is not a future work
item and must not be recreated. The donor remains recoverable only from Git
history: commit `59eb838` and branch
`archive/standalone-gmes-before-removal`.

## Completed restoration

| Item | Status |
|---|---|
| Restore both `GMES_Workflow.bat` branches to flat modules | Complete; mechanically guarded by `tests/test_legacy_entrance.py`. |
| Preserve popup-blocking support (E1) | Complete; `--disable-popup-blocking` is in the G-MES profile launcher only. It was live-proven in the donor engine, not yet on the active legacy path. |
| Preserve G-MES screenshot targeting (E2) | Complete; `gmes_common` selects the G-MES host tab and passes it to shared `cdp_common` without changing N-ERP defaults. |
| Classify donor capabilities | Complete; see `CAPABILITY_RESCUE_MAP.md`. |
| Resolve dependents and remove donor code, package configuration, launchers, and tests | Complete; HISTORY.md 57.9–57.10. |

## Protected runtime data

Do not inspect, move, migrate, normalize, or delete either credential file:

- `%LOCALAPPDATA%\GMES_Automation\credentials.dat` is the supported legacy
  runtime’s DPAPI store.
- `%LOCALAPPDATA%\GMES\credentials.dat` belonged to the removed donor
  package and remains protected.

The Chrome CDP profile copy is equally protected. Removal of repository code
does not authorize any runtime-data cleanup.

## Known future work, deliberately not implementation work

Watchdog/heartbeat, checkpoint/resume, recovery ladders, fallback profiles,
alerts, paged reads, and streaming CSV remain evidence-tracked design work;
they are not part of the supported runtime. A future watchdog must use a
PID/run-scoped heartbeat, not shared log mtime. A future checkpoint must be
integrated after each verified screen success, before the next screen starts.

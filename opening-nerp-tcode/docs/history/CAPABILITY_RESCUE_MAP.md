# Capability rescue record

The former `src/gmes` donor package was removed in HISTORY.md Phase 57. It
is recoverable only from Git commit `59eb838` / branch
`archive/standalone-gmes-before-removal`; it is not executable in this tree.
The flat root modules are the sole supported G-MES engine.

| Capability | Final disposition | Evidence / boundary |
|---|---|---|
| Popup-blocking flag | IMPLEMENTED MINIMALLY | `--disable-popup-blocking` is live-proven in the donor incident and ported only to the G-MES profile launcher. |
| Strict G-MES screenshot targeting | IMPLEMENTED MINIMALLY | G-MES snapshots require the exact `seegmes4.sec.samsung.net` page; no verified tab means no screenshot. Shared N-ERP selection is unchanged. |
| SSO auth allowlist flags | DISCARDED WITH EVIDENCE | Phase 56.3 observed a 200 HTML ADFS form, not a Negotiate challenge. |
| SSO diagnostic | IMPLEMENTED MINIMALLY | `gmes_sso_diagnose.py` is read-only and uses flat imports. |
| Production-plan recipe | ALREADY COVERED BY LEGACY | `gmes_daily_prodplan.py` has its specialised subtotal policy and atomic CSV write. |
| Command validation | ALREADY COVERED BY LEGACY | `gmes_report.py` validates before browser side effects. |
| Record/replay opening shape | IMPLEMENTED MINIMALLY | Opening and post-option fingerprints are stored and checked at their respective lifecycle points. |
| Popup close / export retry | NEEDS LIVE EVIDENCE | Base legacy mechanisms exist; donor refinements are not ported without legacy-path proof. |
| Watchdog + heartbeat | NEEDS LIVE EVIDENCE | Shared-log-mtime is explicitly rejected. A future watchdog needs a PID/run-scoped heartbeat. |
| Checkpoint/resume | NEEDS LIVE EVIDENCE | It must be integrated after each verified screen success, keyed by the requested batch, verify its recorded files still exist, and expire; it is not a zero-edit external helper. |
| Recovery ladder / browser fallback | PRESERVED AS DESIGN KNOWLEDGE | Recovery must be bounded, is not replaced by a watchdog, and must never automatically refresh the protected profile. |
| Circuit breaker / alerts / locking | PRESERVED AS DESIGN KNOWLEDGE | Offline donor experiments do not justify growing the supported core. Business refusals never trip retries or circuit breakers. Alerts need separate DPAPI credentials, EHLO before STARTTLS capability checks, and refusal to send credentials over plaintext. |
| Paged reads / streaming CSV | NEEDS LIVE EVIDENCE | The legacy reader remains materialised; donor paging was not live-verified. |
| Doctor / runtime-path consolidation | PRESERVED AS DESIGN KNOWLEDGE | Useful ideas, no supported-runtime regression requiring a new architecture. |
| Atomic writes | ALREADY COVERED BY LEGACY / NEEDS EVIDENCE | Profiles and CSV use replace; credential-store hardening needs separate evidence. |
| Project Eye | IMPLEMENTED MINIMALLY | Root governance tests enforce the one-engine truth. |

## Superseded proposals

Earlier proposals to extract a watchdog from shared log mtime, to treat a
restart as a recovery ladder, or to build a zero-edit external checkpoint
were rejected. They are unsafe or incomplete for the supported flat runtime.
They are lessons, not instructions to recreate the donor architecture.

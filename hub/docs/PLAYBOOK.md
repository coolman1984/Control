# Control — agent guide

Control is one front door to this Windows PC. Connect once (`control mcp`) and use:

| Area | For | Examples |
|---|---|---|
| `win` | any desktop app, through its accessibility tree | `win.windows`, `win.snapshot`, `win.click`, `win.type`, `win.excel-read` |
| `web` | any website, inside Control's own browser profile | `web.launch`, `web.snapshot`, `web.click`, `web.type`, `web.text` |
| `gmes` | Samsung G-MES reports: sign in, filter, verify, save Excel | `gmes.find`, `gmes.describe`, `gmes.run`, `gmes.batch_plan` |
| `sys` | PC health, space, speed, broken or missing parts, and their fixes | `sys.doctor`, `sys.junk`, `sys.health`, `sys.fix` |
| `data` | folders of Excel, Word, PDF, e-mail made queryable | `data.start`, `data.find`, `data.query`, `data.trace` |
| `flow` | durable multi-step automations (runs without a model in the loop, survives a crash) | `flow.list`, `flow.describe`, `flow.approve`, `flow.run`, `flow.dry_run` |
| `run` | the runs a flow produced | `run.list`, `run.get`, `run.resume`, `run.cancel`, `run.retry_step` |

## The loop
1. Look first (read actions). 2. Act once. 3. Check what changed (every wad action reports
`changed:` lines; `web.*` reads values back; `sys.fix` reports freed bytes and journal ids).
4. Look again before the next step. Never repeat an action whose effect you did not check.

## Tiers
- **read** — changes nothing; use freely.
- **safe** — small or undoable change; runs and is written to the journal.
- **risky** — a yes/no box appears on the person's screen. `APPROVAL_DENIED`: do not retry, ask
  them. `APPROVAL_TIMEOUT`: ask in chat, then retry once. You cannot answer that box yourself.
- `sys.fix` with `apply` is safe only when every matched fix is WinSight-safe; otherwise risky.
  Always preview first (`apply` false).

## Journal and undo
Every safe/risky call returns a `journal_id`. `control.journal` lists them; `control.undo <id>`
reverses the ones marked `[undoable]`.

## Recipes across areas
- **Report → analysis:** `gmes.run` (read the saved file paths from the returned `manifest`; do not
  pass `output_dir` unless you mean to change where G-MES saves from now on) → `data.prepare`
  with `workspace` = that folder → `data.start` → `data.query`.
- **Slow PC:** `sys.doctor` → read the top findings → `sys.fix` preview → apply the safe ones →
  `control.journal`.
- **App with no accessibility tree:** `win.screenshot` → `win.ocr` → `win.click-text`.

## Traps learned the hard way
- Corporate proxies swallow local traffic; Control sets `NO_PROXY` for its children — do not unset it.
- Windows 11 Notepad restores old tabs: create and verify a blank tab before typing.
- Excel's ValuePattern lies: write cells with `win.excel-write` (reads back), not `win.type`.
- An app running as administrator is invisible to a non-elevated Control.
- G-MES allows one run at a time: `GMES_BUSY` means wait, not retry in a loop.
- Arabic keyboard layout breaks ribbon key tips: `win.input-lang en` for that window.

## Flows: automating something you would otherwise repeat by hand
A flow is one TOML file at `<CONTROL_HOME>/flows/<name>.toml`: a list of steps, each an action
you already know plus `if`/`for_each`/`parallel`/`wait_for`/`ask_human`/`call_flow`/`set`/`assert`.
Write one when you have already run the same steps for the person once by hand and they want it
to happen again without you.

1. Write the file, then `flow.validate` (or `flow.validate` with `text=` on a draft before saving).
2. `flow.dry_run` to see what it would do without touching anything real.
3. `flow.approve` once the person is happy — this needs their yes, exactly like any other risky
   action, because it authorizes every risky step inside to run unattended from then on. **Any
   edit to the file invalidates the approval** (a new file hash), so re-approve after changing it.
4. `flow.run` from then on runs the whole thing with no more approval boxes, and — unlike calling
   the same actions yourself one by one — a crash or reboot mid-run resumes instead of restarting:
   `run.resume` continues a run that is `waiting` (an `ask_human` step) or picks a `needs_attention`
   run back up once you have looked at what happened to the step it stopped on.
5. An unapproved flow can still be run once with `flow.run` — that call itself asks for a yes to
   start, and any risky step inside it still asks separately, exactly as if you had called it
   directly. Approval is what buys unattended operation, not permission to run at all.

Two things a flow cannot do (by design, not by accident): pause (`ask_human`/`wait_for`) from
inside an `if`/`for_each`/`parallel` branch — put pauses at the top level of the flow; and resume
*inside* a half-finished `if`/`for_each` after a crash — those run start-to-finish as one unit, so
mark a step `idempotent = true` only when re-running it from scratch is truly safe.

### A worked example
```toml
name = "daily-report"
description = "Pull today's G-MES report, analyse it, and stop for a look before sending it on."

[[steps]]
id = "report"
type = "action"
action = "gmes.run"
[steps.args]
report = "daily_prodplan"
[steps.retry]
attempts = 3
backoff_s = 60
on = ["GMES_BUSY", "TIMEOUT"]

[[steps]]
id = "analysis"
type = "action"
action = "data.prepare"
[steps.args]
workspace = "{{ steps.report.manifest.folder }}"

[[steps]]
id = "review"
type = "ask_human"
prompt = "Report ready — send it to the team?"
[[steps.fields]]
name = "send"
required = true
```

## Details per tool
The four tools keep their own docs: `I:\Control\win-agent-desktop\docs\COMMANDS.md`,
`I:\Control\Office-Automation\AI_USAGE.md`, `I:\Control\opening-nerp-tcode\GMES_SKILL.md`,
`I:\Control\Performance\AGENTS.md`.

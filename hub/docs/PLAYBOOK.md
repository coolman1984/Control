# Control — agent guide

Control is one front door to this Windows PC. Connect once (`control mcp`) and use:

| Area | For | Examples |
|---|---|---|
| `win` | any desktop app, through its accessibility tree | `win.windows`, `win.snapshot`, `win.click`, `win.type`, `win.excel-read` |
| `web` | any website, inside Control's own browser profile | `web.launch`, `web.snapshot`, `web.click`, `web.type`, `web.text` |
| `gmes` | Samsung G-MES reports: sign in, filter, verify, save Excel | `gmes.find`, `gmes.describe`, `gmes.run`, `gmes.batch_plan` |
| `sys` | PC health, space, speed, broken or missing parts, and their fixes | `sys.doctor`, `sys.junk`, `sys.health`, `sys.fix` |
| `data` | folders of Excel, Word, PDF, e-mail made queryable | `data.start`, `data.find`, `data.query`, `data.trace` |

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

## Details per tool
The four tools keep their own docs: `I:\Control\win-agent-desktop\docs\COMMANDS.md`,
`I:\Control\Office-Automation\AI_USAGE.md`, `I:\Control\opening-nerp-tcode\GMES_SKILL.md`,
`I:\Control\Performance\AGENTS.md`.

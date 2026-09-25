# Control — piece 1: the front door (design spec)

Date: 2026-09-25 · Status: waiting for owner review · Owner: the only user of this app

## ملخص بالمصري

- برنامج واحد اسمه **Control** في `I:\Control\hub`: أمر واحد `control` ووصلة واحدة للذكاء الاصطناعي `control mcp`.
- الأدوات الأربعة بتدخل جواه كمجموعات: `win.` و`web.` و`gmes.` و`sys.` و`data.`. الفولدرات القديمة بتفضل شغالة زي ما هي.
- كل حركة ليها تصنيف: قراءة أو تغيير آمن أو خطر. الخطر بيطلعلك عليه مربع "أيوه / لأ" على الشاشة.
- كل تغيير بيتسجل في دفتر واحد، و`control undo` بيرجّع أي حاجة ينفع ترجع.
- دليل واحد للذكاء الاصطناعي، جزء منه بيتولد أوتوماتيك من قايمة الأدوات.
- الجزء ده بس هو "الباب". المحرك العام للمواقع، وسد نواقص التحكم في الويندوز، والأوتوميشن بين الأدوات: كل واحد منهم ليه مواصفات لوحده بعدين.

## 1. Goal and scope

One app that an AI agent (Claude Code, Claude Desktop, any MCP client) connects to once and
through which it can see and drive Windows apps, drive websites, pull G-MES reports, diagnose
and fix the PC, and analyse Excel/document folders — with one safety model, one journal and one
guide.

The whole programme is four pieces, each with its own spec → plan → build:

| Piece | What | This spec? |
|---|---|---|
| 1 | Front door: app, registry, safety tiers, approval pop-up, journal, MCP, CLI, guide, adapters for the 4 existing tools | **yes** |
| 2 | General web engine: G-MES's site-independent half (proxy bypass, real mouse events, stale-socket reconnect, polling waits, verify-before-save, screen memory, DPAPI credentials) becomes `web.*`; G-MES becomes the first site plug-in | no |
| 3 | Full Windows control: gaps between wad (UI) and WinSight (system) — elevation, services, registry, scheduled tasks, event subscriptions | no |
| 4 | Cross-tool automations: flows like "pull report → analyse → alert", pre-approved risky steps, schedules | no |

Non-goals for piece 1: changing any code inside the four existing repos; multi-user or
coworker installs (single owner, own PCs only); a graphical UI.

## 2. Existing parts (facts the design relies on)

- **wad** (`I:\Control\win-agent-desktop`, Python ≥3.9, package `wadlib`): `registry.py` holds
  one declaration per command (`@command(name, help, *Arg, group, readonly, image, long)`) that
  feeds CLI, MCP, batch and docs. Commands return `(payload, text)` and raise
  `WadError(code, message, hint)`. ~80 commands in groups observe/act/mouse/keyboard/windows/
  vision/browser/office/system/workflow. Installable with `pip install -e`.
- **xl2ai** (`I:\Control\Office-Automation`, Python ≥3.11, package `xl2ai`): `mcp_server.TOOLS`
  holds 11 tools (start, prepare, find, table, query, facts, region, search, read,
  save_records, trace) with in-process handlers.
- **G-MES bot** (`I:\Control\opening-nerp-tcode`, flat scripts, not a package, only dependency
  `websocket-client`): `gmes_report.py run|describe|find`, `gmes_batch.py`, `gmes_open_screen.py`.
  `run` accepts `--manifest PATH` for a machine-readable result. One run at a time (run lock,
  exit 3 when busy).
- **WinSight** (`I:\Control\Performance`, Go, single .exe): 34 tools, every tool accepts
  `--json`; `fix <id>` previews, `--yes` applies; fixes are tagged safe/moderate/risky and
  journaled; `undo <journal-id>`. Not built on this PC yet (Go 1.26 is installed).

## 3. Architecture

```
I:\Control\hub\
  control\
    registry.py      Action declaration: name, area, tier, args, handler, flags
    errors.py        ControlError(code, message, hint)
    safety.py        tier policy + approval gate (desktop pop-up)
    journal.py       one append-only journal for every change, with undo pointers
    runner.py        execute(): validate → gate → run with timeout/isolation → journal → result
    cli.py           `control <action> ...`, `control undo`, `control journal`, `control guide`
    mcp.py           stdio MCP server (JSON-RPC 2.0), tools generated from the registry
    guide.py         builds AGENT_GUIDE.md = hand-written playbook + generated action reference
    areas\
      win.py         wraps wadlib.COMMANDS (all groups except browser)   → win.*
      web.py         wraps wadlib browser group                          → web.*
      gmes.py        subprocess adapter over the G-MES scripts           → gmes.*
      sys.py         subprocess adapter over winsight.exe --json         → sys.*
      data.py        in-process adapter over xl2ai.mcp_server.TOOLS      → data.*
  docs\PLAYBOOK.md   hand-written part of the guide
  AGENT_GUIDE.md     generated; the one document agents read
  tests\
  pyproject.toml     Python ≥3.11; depends on wadlib and xl2ai via editable local paths
```

**One declaration.** A Control action is declared once: `name` (`area.verb`, e.g.
`win.click`, `sys.junk`), `tier`, args schema, handler, `readonly`, `returns_image`, `long`.
From it come the CLI parser, the MCP tool (schema + annotations), the guide entry, and
validation. Wrapped tools keep their own names under the area prefix (`win.snapshot`,
`data.query`, `sys.fix`), so the existing docs stay recognisable.

**How each area is wired.**

| Area | How | Why |
|---|---|---|
| `win.`, `web.` | import `wadlib`, re-register its `COMMANDS` | same language, already a registry; no subprocess cost |
| `data.` | import `xl2ai.mcp_server`, re-register `TOOLS` | same language, in-process handlers exist |
| `gmes.` | run `python gmes_report.py ...` in its own folder, read `--manifest` | flat scripts with global state and a run lock; isolation protects both sides. Piece 2 revisits this |
| `sys.` | keep one `winsight.exe mcp` child process and call its tools over MCP | different language; its MCP server already returns structured results, fix risks and journal ids |

A missing part (WinSight not built, G-MES folder moved, xl2ai extra not installed) disables
only that area; `control doctor` and the `control.areas` action report it with a fix hint.

**Paths** come from `%LOCALAPPDATA%\Control\config.toml` (defaults point at the four folders
under `I:\Control`), so nothing machine-specific is hard-coded in the source.

## 4. Safety tiers and approval

Every action has exactly one tier:

| Tier | Meaning | Examples | Behaviour |
|---|---|---|---|
| `read` | changes nothing | `win.snapshot`, `web.text`, `sys.health`, `data.query`, `gmes.find` | runs |
| `safe` | changes something small, expected, or undoable | `win.click`, `win.type`, `web.click`, `sys.fix` of a WinSight-`safe` fix, `data.prepare`, `gmes.run` (read-only report export) | runs, journaled |
| `risky` | hard or impossible to undo, or high blast radius | wad `system` group (shell, file-write, process-kill), `win.excel-run`, Outlook send, `web.eval`, `sys.fix` of a moderate/risky fix, anything needing admin | needs approval, journaled |

Default tier mapping for wrapped tools: wad `readonly=True` → read; wad `system` group and a
named list (excel-run, outlook send, browser-eval) → risky; other wad → safe. xl2ai →
read, except `prepare` and `save_records` → safe. WinSight tools → read; `fix`/`clean`/`undo` →
tier taken from the fix's own safe/moderate/risky tag (moderate counts as risky; a pattern that matches no known fix counts as risky); `clean` applies only WinSight-`safe` fixes, so `clean` with apply → safe; `undo` → risky; `brief` (writes files) → safe. Wad `batch` runs steps without passing through Control's gate, so it is risky; wad `readonly=True` wins over its group. A small
override table in `safety.py` holds all exceptions and is covered by a test.

**Approval gate.** A risky action shows a Windows message box on the owner's desktop, sent from
the Control process, not the agent: title "Control — approve?", the action, its arguments
(secrets masked), the tier reason, Yes/No, default No, 120-second timeout → No. The agent only
ever sees the result: `APPROVAL_DENIED` or `APPROVAL_TIMEOUT` with a hint. Because the box
belongs to Control's process, an agent driving the UI through `win.click` must not be able to
answer it: the gate refuses UI actions that target Control's own approval window. Piece 4 adds
pre-approved flows; piece 1 has no bypass except `CONTROL_APPROVAL=off` in the owner's
environment, which is logged at startup and in every journal entry.

## 5. Journal and undo

- One append-only JSONL file: `%LOCALAPPDATA%\Control\journal.jsonl`.
- Entry: id, time, action, args (secrets masked), tier, approval result, outcome, error code,
  duration, and `undo` — either `null` or a pointer to how to reverse it (for `sys.*`: the
  WinSight journal id; for wad writes that read back an old value: the old value).
- `control journal [--last N]` lists; `control undo <id>` reverses when a pointer exists, and
  says plainly "not undoable" otherwise. Undo is itself journaled, and is risky-tier.
- Reads (`read` tier) are not journaled, to keep the file small.

## 6. The agent interface

- **Tool names:** MCP clients such as Claude only accept `[a-zA-Z0-9_-]` in tool names, so the MCP name is the action name with `.` → `_` (`win.snapshot` → `win_snapshot`). The CLI and the journal keep the dotted name.
- **Transport:** MCP over stdio, JSON-RPC 2.0, same protocol revisions wad supports. One
  server: `control mcp`. Registered once: `claude mcp add control -- control mcp`.
- **Tool-count control:** all actions total ~140; clients degrade with that many. The server
  starts with a core set (≈15): `control.areas`, `control.find_action`, `control.describe`,
  `control.journal`, plus the most-used read actions of each area. `control.load_area
  <area>` adds that area's tools and sends `notifications/tools/list_changed`. Clients that
  ignore that notification can still reach every action through `control.call <action> <args>`.
  `control mcp --areas win,sys` preloads chosen areas.
- **Annotations:** `readOnlyHint` for read, `destructiveHint` for risky, so clients can
  auto-approve reads.
- **Results:** compact text by default, `json: true` for the full payload, images for
  screenshots, `isError: true` with `code` + `hint` on every failure.
- **Instructions at connect time:** the short playbook (look → act → verify; tiers; how to
  load areas).
- **CLI parity:** every action is also `control <area>.<verb> ...` with the same arguments.

## 7. The one guide

`AGENT_GUIDE.md` = `docs/PLAYBOOK.md` (hand-written: working loop, safety tiers, which area for
which job, recipes that cross areas, known traps collected from the four projects' "learned the
hard way" notes) + a generated reference of every action (name, tier, args, one-line help).
`control guide` prints it; a test fails when the generated part is stale. The four projects'
own docs stay where they are and are linked, not copied.

## 8. Errors and isolation

- Every failure → `ControlError(code, message, hint)`. Wrapped `WadError`s keep their code;
  xl2ai errors map to `DATA_*`; subprocess failures map to `GMES_*` / `SYS_*` with the exit
  code and last stderr lines.
- Subprocess adapters run with a timeout (G-MES: 30 min default, configurable; WinSight: 5 min;
  fixes: 15 min) and are killed as a process tree on timeout → `TIMEOUT`.
- G-MES exit 3 (run lock held) → `GMES_BUSY` with hint "another G-MES run is active".
- In-process adapters run inside `runner.execute`, which catches every exception; wad's own
  watchdog still applies to its commands. One failing area never stops the server.
- Proxy: all local traffic sets `NO_PROXY=127.0.0.1,localhost` (lesson from both G-MES and wad).

## 9. Testing

- Unit tests with no real desktop: registry → CLI/MCP schema generation; tier mapping for every
  wrapped action (a test enumerates all and fails on any action without a tier); approval gate
  with a fake message box (yes / no / timeout / self-targeting refusal); journal write, list,
  undo pointer, secret masking; MCP handshake, `tools/list`, `load_area`, `call`; subprocess
  adapters against fake scripts (success, failure, timeout, exit 3).
- Guide freshness test.
- Live check on the owner's PC (manual, scripted in `tools/smoke.py`): connect from Claude Code;
  `win.windows`; `sys.health`; `data.start` on a sample folder; `gmes.find` (read); one risky
  action → pop-up appears → No → `APPROVAL_DENIED`; a safe `sys.fix` → `control undo` reverses it.

## 10. Done means

1. `control mcp` connected in Claude Code shows the core set and can load every area.
2. Every action of the four tools is reachable, has a tier, and is in `AGENT_GUIDE.md`.
3. A risky action cannot run without the owner's Yes.
4. Every change appears in the journal; undoable ones reverse with `control undo`.
5. The four original repos are unchanged, and their own tests still pass.
6. All tests above pass; the live check passes on the owner's PC.

## 11. Open risks

- **Python version:** the default `python` here is 3.14 and has none of wad's packages. Control uses its own virtual environment on Python 3.11 (`py -3.11`, already installed), with wad and xl2ai installed editable from their folders. WinSight is built with Go 1.26 into `hub\bin\winsight.exe`, so the Performance folder is not written to.
- **Two MCP servers for the same tool** (wad's own and Control's) could both drive the desktop.
  The guide tells the owner to register only `control` once it works.
- **Message box from a non-interactive session:** if Control runs outside the owner's desktop
  (wad's `desktop-check` → unavailable), the pop-up can't appear; risky actions then fail
  closed with `APPROVAL_UNAVAILABLE`.

## 12. Deferred with rulings (2026-09-25)

- **G-MES scheduling and saved-batch management** (`gmes_batch` save/delete/schedule/unschedule/
  run-scheduled, `gmes_open_screen`) → piece 4 (automations).
- **wad old-value undo pointers** → piece 3.
- **MCP server runs one call at a time** — a long G-MES run blocks every other call on the same
  connection → piece 2 (worker thread + lock), landing after the approval-box guard (item 2 of
  the final-review fix wave) so the two interact correctly.
- **UI-chain bypass** (launch an app, then type, then press, without ever calling a risky action
  directly) is limited by the `win.launch` shell/terminal/registry rule and the approval-box
  guard, but not fully closed — a chain of safe actions can still reach a state a single risky
  action would have needed approval for.
- **Undo is not resumable mid-chain**: if one step of a multi-step undo fails partway, the
  already-undone steps are not retried or rolled forward automatically → piece 3.

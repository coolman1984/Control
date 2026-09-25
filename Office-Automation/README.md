# xl2ai

Local, offline-first preparation layer between business Excel files and AI.

The core idea is simple:

```
Excel -> verified SQLite -> catalog/profile/quality -> keys & relations
      -> rules/KPIs -> run-to-run changes -> compact AI context + safe query tools
```

Excel remains the source of truth. AI never needs to open the raw workbook or ingest a 100 MB table.

## What works now

- Two extraction engines, one output contract (`[extract] engine = "auto" | "excel" | "direct"`):
  - **Excel**: COM extraction on Windows with a private Excel process, read-only open, crash/dialog handling and
    verification by Excel itself. The only engine that opens rights-managed (DRM) workbooks.
  - **Direct**: reads .xlsx/.xlsm/.xlsb/.xls itself (python-calamine), on any OS, no Excel needed, several times
    faster on big files (1M rows x 10 columns, 57 MB .xlsx: ~55 s including full verification). For .xlsx/.xlsm
    every column is verified by a second, independent reader of the sheet XML, which also captures every formula
    and error cell. `auto` uses Excel where it can, otherwise direct.
- Report-shaped sheets are described, not flattened blindly: several tables on one sheet (`query meta regions`,
  `query region <table> <n>` reads one back out under its own header) and grouped multi-row headers
  ("Plan" under "Q1") are detected and recorded.
- Documents too: Word, PowerPoint (including the numbers behind charts), PDF, Outlook .msg / .eml e-mail (with
  attachments, recursively), Markdown, CSV and HTML. Their tables become queryable tables, their text becomes
  searchable (Arabic-aware), codes they mention are linked to the table rows holding them, and agents can turn free
  text into records that are only accepted with a verified source quote (`search`, `read`, `save_records`).
- An agent connection (MCP server, `xl2ai serve`) with eight purpose-named tools, and workspaces: point it at any
  folder of Excel files (`xl2ai open`), nothing else to configure.
- Reports checked against the raw data they summarise (disagreeing rows listed with both numbers), and unusual
  values flagged in advance (outliers, odd months, missing months, impossible dates).
- Key numbers computed in advance for every data table: totals, biggest groups and monthly trends, each with the
  SQL that produced it (`query digest <table>`); totals rows excluded, prices averaged rather than added.
- `query find <text> [--values]`: where a name, header or value lives across every file, Arabic-spelling tolerant.
- Cross-file SQL (`query sql "*" ...`) plus ready JOIN recipes for every detected relationship.
- Several big workbooks are extracted in parallel with the direct engine (`[extract] workers`).
- Formula lineage: which sheet or other workbook each computed column pulls from (`query meta lineage`), shown
  in `brief`, the context pack and the agent brief -- "this report is computed from Raw Sales".
- Incremental refresh: unchanged trusted sources are reused without reopening Excel.
- Run history, live-owner lock, promotion gate and retention. A failed refresh never replaces the last trusted run.
- Stable source/table/column catalog.
- Column profiling and generic quality findings.
- Candidate keys and conservative relationship inference.
- Human-confirmed keys/relations through project rule packs.
- Deterministic business rules, KPIs and dictionary terms.
- Schema, volume, value, category-membership, distribution, KPI and row-multiset change detection.
- Compact token-budgeted AI context pack.
- Read-only capped query tools with row, byte and time limits plus SQLite authorizer protection.
- Trace from a returned row back to its Excel row.
- Fast status and optional deep SHA-256 source-integrity check.
- Human-readable project report and environment doctor.
- Installable package and `xl2ai` command.
- Live, step-by-step run view (`xl2ai watch`) and post-run failure diagnosis (`xl2ai diagnose`), both usable without Excel via `--demo`.
- One-call cold-agent orientation (`xl2ai brief`): readiness per table, explicit gaps, ordered next commands.
- Semantic layer (`xl2ai semantics`): column roles (money/quantity/date/identifier/category/...), unit/currency
  detection, table grain ("one row per X, identified by Y" -- or explicitly "unknown"), time coverage, and
  auto-drafted table/column definitions ready for human confirmation via a pack.
- Reports on what it could not fully read: Power Query, the Data Model, external links, stale-calculation,
  totals rows mixed into data, and unread chart sources -- surfaced as `blind_spots` in the AI context pack and
  as `gaps` in `xl2ai brief`, never silently treated as complete.
- Opt-in, reversible cleanup suggestions (null markers, inconsistent category spelling, text-as-number) that
  never touch the extracted data -- off by default (`[repair].enabled=false`); `xl2ai query repaired` previews
  them applied, on the fly, without writing anything.

## Connecting an agent (start here)

```powershell
python -m xl2ai open "D:\Sales Files"          # once: creates the hidden workspace beside the files
python -m xl2ai refresh --config "D:\Sales Files"   # reads every workbook once (later: only changed ones)
python -m xl2ai connect --workspace "D:\Sales Files" # prints the line to add to Claude Code / Desktop / any MCP host
```

The agent then calls `start` and gets the whole picture in one read. Where everything lives and the order of
events: `CONNECTING.md`.

## Install

Python 3.11+ is required. The Excel engine additionally requires Windows and Microsoft Excel; the direct engine
runs anywhere.

```powershell
python -m pip install -e ".[all]"         # direct Excel engine + Word/PowerPoint/PDF/e-mail readers
xl2ai --help
```

On Windows, the package installs `pywin32` automatically.

## Start a project

```powershell
xl2ai init "C:\Data\Orders.xlsx" "C:\Data\Customers.xlsx" --name "Sales" --with-pack
xl2ai doctor
xl2ai refresh
xl2ai report
```

For explicit file paths, `init` also creates a stable source alias automatically. Use that alias in business-rule selectors.

A project normally contains:

```
xl2ai.toml
packs/
data/
  current.json
  runs/<run_id>/
```

## Daily use

```powershell
xl2ai brief
xl2ai status
xl2ai status --deep
xl2ai refresh
xl2ai report
xl2ai query schema
xl2ai query meta grain
xl2ai query meta column_roles
xl2ai query describe <table-id>
xl2ai query sample <table-id>
xl2ai query aggregate <table-id> amount --op sum --group-by department
xl2ai query compare
xl2ai query trace <table-id> 25
xl2ai watch
xl2ai diagnose
```

`status` is deliberately fast and checks file size + modified time. `status --deep` recomputes full SHA-256 when you need the stronger guarantee.

`watch` runs a refresh with a live, redraw-free terminal view of each stage as it happens; add `--demo` to see it without Excel or a real project. `diagnose` explains a finished run afterwards: what stage it reached, the first real failure (not its downstream symptoms) and the events around it; `--ai` prints that as plain text ready to paste into an AI assistant. Every run writes `data/runs/<run_id>/journal.jsonl`, which `diagnose` reads.

## Keeping data fresh

`xl2ai` does not run as a background service; schedule `xl2ai refresh` with whatever the host already has (cron,
a systemd timer, Windows Task Scheduler). A minimal cron line:

```
0 * * * * cd /path/to/project && xl2ai refresh >> refresh.log 2>&1
```

After any refresh (scheduled or manual), check readiness before trusting the result:

```powershell
xl2ai brief          # exit 0 = ready, 1 = usable but read the gaps, 2 = do not use this data
```

`xl2ai brief`'s exit code is meant to be checked by scripts, not just read by humans -- a scheduled job or an
agent can refuse to proceed on stale, unpromoted or failed data instead of silently using it.

## Trust model

The platform keeps four ideas separate:

1. **Extracted**: what Excel actually returned.
2. **Detected/inferred**: structure, candidate keys and relationships found by generic logic.
3. **Confirmed**: knowledge explicitly declared by a human in a pack.
4. **Calculated**: rules and KPIs produced deterministically by SQL/code.

AI-facing output preserves those distinctions. It does not silently promote an inference into a business fact.

## AI use

Every trusted run can produce:

```
data/runs/<run_id>/ai/context_pack.md
data/runs/<run_id>/ai/context_pack.json
```

The context pack is small by design. When more evidence is needed, use the capped query tools rather than giving the model the raw database.

See `AI_USAGE.md`.

## Business knowledge

Generic code lives only in `xl2ai/`. Department/project knowledge lives in packs.

A pack can define:

- terms and meanings,
- confirmed keys,
- confirmed relationships,
- deterministic assertions,
- KPIs.

See `BUSINESS_RULES.md` and `EXTENDING.md`.

## Current known limits

The platform warns instead of pretending when workbook logic is not fully represented.

Still incomplete:

- several logical tables inside one worksheet are *detected and readable one by one*, but still stored as one wide
  table per sheet (no config-driven re-cut yet),
- grouped headers are recorded per column but do not rename columns,
- formula lineage is at sheet level (which sheet/workbook a column pulls from), not cell-by-cell dependency; the
  Excel engine sees one sample formula per column, the direct engine sees every formula,
- the direct engine cannot open DRM-wrapped files, and on .xlsb/.xls cannot see formulas or error cells (it says
  so as a `reader_limit` blind spot),
- pivot definition/source logic,
- Power Query / Data Model *semantics* (their presence is detected and reported as a blind spot; their internal logic is not read),
- key-based row-level before/after values for confirmed keys,
- a graphical UI.

Current extraction does preserve the visible values and reports structural warning signals such as formulas, pivots and merged data cells.

## Safety guarantees

- source workbooks are opened read-only,
- writes happen inside a new run folder,
- promotion is atomic,
- the previous trusted run survives failure,
- raw SQL is read-only, capped and guarded by SQLite authorization,
- business rows are never copied into the context pack,
- row-diff fingerprints store hashes/counts, not row content.

## Tests

```powershell
python -m unittest discover -s tests
```

The CI matrix runs the COM-free platform tests on Windows and Linux with Python 3.11 and 3.12. Real Excel integration/golden tests are documented in `TESTING.md`.

## Documents

- `ARCHITECTURE.md` design rules and remaining architectural work.
- `DATA_CONTRACT.md` persisted contracts and evidence conventions.
- `EDGE_CASES.md` edge-case coverage.
- `TESTING.md` test strategy.
- `AI_USAGE.md` safe AI workflow.
- `BUSINESS_RULES.md` rule-pack format.
- `EXTENDING.md` how to add a new project/capability.
- `CHANGELOG.md` shipped changes.
- `AGENT_READINESS_PLAN.md` the phased plan behind `brief`/`agent_brief`/`semantics`/`repair` and what remains.

# CHANGELOG

## 0.14.0 - Documents: Word, PowerPoint, PDF and e-mail become data (2026-09-24)

The platform's scope widens from "Excel for agents" to "the data locked in office files, for agents". A workspace now
takes every supported file in the folder (and its sub-folders).

* **Readers** (`xl2ai/documents/readers.py`, one common `Doc` shape: located text blocks, tables, metadata,
  attachments, stated warnings): Word .docx (headings -> section paths, lists, tables with their captions), PowerPoint
  .pptx (slide titles, text, speaker notes, tables, and the numbers stored behind charts), PDF (text per page with
  tables cut out and extracted as tables; scanned pages without a text layer are reported, not silently empty),
  e-mail .eml (headers, plain or HTML body, attachments) and Outlook .msg (read straight from the OLE container --
  subject, sender, recipients, sent time, body, attachments -- no Outlook and no fragile third-party parser;
  rights-protected messages are detected and reported), plus Markdown (headings, pipe tables), CSV, HTML and text.
  Arabic-Windows (cp1256) text is decoded.
* **Office readers** (`com_readers.py`) for rights-managed and legacy files on Windows: Word (paragraph outline
  levels, tables via cells so merged cells work), PowerPoint (titles, text, tables, notes) and Outlook
  (`OpenSharedItem`, attachments saved and read). COM initialised per thread. Tested with fake object models here;
  needs a first run on Windows to validate.
* **Documents as databases** (`documents/store.py`): every table in a document -- including tables of Word files and
  sheets of Excel files attached to e-mails, recursively -- becomes a typed data table ("1,234.50", "(300)", "12%",
  Arabic-Indic digits parsed) with the same contract as a sheet, so the catalog, key numbers, relationships, anomalies,
  report checks and cross-file SQL all work on document tables unchanged. Text goes to `_doc_blocks` with its
  location; `_doc_entities` holds codes, money with currency, dates (numeric, English and Arabic month names),
  percentages, e-mails and phones with character spans.
* **Catalog**: `_documents`, `_blocks` with a full-text index (FTS5; Arabic letter variants, diacritics and the
  و/ف/ب/ك/ل/ال prefixes folded so "والقاهرة" is found by "القاهره"), `_entities`, `_table_origin`.
* **Links** (new `links` stage, `_mentions`): every code, e-mail or phone in a document is looked up in the tables --
  "the e-mail mentions INV-005000 -> row 5003 of Transactions.invoice_no". Listed per document in the agent brief.
* **Agent tools**: `search` (ranked full-text with location and snippet), `read` (a document, attachment, page,
  section, or the blocks around a hit), and `save_records`: records an agent extracts from free text are stored in
  `<data>/extracted.db` only when every field carries a block id and an exact quote, the quote is found in that block
  and the value in the quote (numbers and dates matched however they are written); everything else is rejected with
  the reason. Saved tables join with everything else in `query`. CLI: `xl2ai docs search|read`.
* Cross-file SQL hints now name each cleaned table's raw form exactly (`all rows: <alias>.<table>`).
* Verified with a real Claude Code session on a mixed folder (Excel, Word, PowerPoint, PDF, two e-mails with Word and
  Excel attachments): it summarised every file, turned the order e-mail into a proven record and matched it to the
  sales row, compared the contract with the customer's real purchases, and checked the chart numbers in the deck
  against the data -- all correct, without opening a single file itself.
* New optional extras: `pip install -e ".[all]"` (Excel direct engine + document readers).

## 0.13.2 - Fixes found by a real agent session; Excel engine safe under the agent connection (2026-09-23)

A real Claude Code session (MCP tools only, file tools blocked) was run on a realistic folder: 18k-row sales
workbook with a pasted grand-total row and an entry error, customers and products workbooks, and a management
report with one wrong branch figure. It answered everything correctly, but only by working around the tool. Fixed:

* **Excel engine under `prepare`**: COM is now initialised per calling thread (`process_file_excel`). `prepare`
  runs the refresh in a background thread, where COM refuses every call from a thread that did not initialise it --
  every agent-started build of a DRM/Excel-engine folder would have failed on Windows.
* **Grand-total rows missed**: a "Total" label typed into a *date* column was not checked, so the key-number total
  of the sales table came out doubled. Totals detection now covers date columns, and the digest adds a safety net: a
  row whose amount equals the sum of every other row, with at least half its cells empty, is flagged whatever its
  label says.
* **Report check missed a wrong figure**: a report sheet holding two stacked tables was compared as one, diluting
  label matches. Reconciliation now checks one region at a time; the wrong branch is found automatically.
* **Plain table names are clean**: in cross-file SQL (`query sql "*"`, MCP `query`) a plain table name leaves out
  rows flagged as totals, so an agent's own SUM cannot double-count; `<alias>.<table>` still reads every row.
* **Name guessing**: the agent brief states each table's SQL name, and a "no such table" error lists the usable
  names (the agent had tried source ids as table names five times).
* **Formula lineage on the Excel engine** now reads every formula of a formula column (R1C1 text, one COM call per
  200k rows, distinct formulas parsed once) instead of one sample; an Excel crash during that probe is no longer
  swallowed. The direct engine reports files that are neither zip nor OLE as rights-managed, with the reason.

Measured on the same questions before/after: 29 -> 21 agent turns, cost 0.40 -> 0.21 USD, 92 -> 65 s, no failed
tool calls from wrong table names.

## 0.13.1 - One-command connection to Claude Code, Codex CLI and Claude Desktop (2026-09-23)

* `xl2ai connect --install all|claude-code|codex|claude-desktop [--workspace] [--dry-run]` (`xl2ai/connectors.py`):
  Claude Code via its own `claude mcp add --scope user`; Codex CLI by adding/replacing only the
  `[mcp_servers.xl2ai]` section of `~/.codex/config.toml` (with a 120 s tool timeout for `prepare`); Claude Desktop
  by merging into `claude_desktop_config.json`. Existing settings are preserved and backed up once to `.bak`.
  Verified against a real Claude Code install: `claude mcp list` reports the server as connected.
* The server now defaults to the folder the host started it in when that folder is a workspace or holds Excel
  files, so an agent opened in an Excel folder needs no path at all. An explicit `workspace` always wins.
* The printed settings use the installed program when xl2ai is packaged as a standalone executable.

## 0.13.0 - The agent connection, workspaces, report checks and anomalies (2026-09-23)

The theme: make "where does everything live and how does an agent talk to it" simple and explicit
(`CONNECTING.md`), and answer two more questions before the agent asks them.

* **Workspaces** (`xl2ai open <excel folder>`, `xl2ai/workspace.py`): the Excel folder *is* the project. `open`
  creates a hidden `.xl2ai/` beside the files (config covering every workbook in the folder and its sub-folders,
  an `AGENTS.md` for file-reading agents, and `data/`), or under `~/.xl2ai/workspaces/` with a registry when the
  folder is read-only. Every `--config` now also accepts a folder, and `XL2AI_WORKSPACE` can name one. Each promoted
  run's AI files are copied to `data/latest/` (one stable path). Excel lock files (`~$`), non-workbook files and the
  workspace itself are never sources.
* **MCP server** (`xl2ai serve`, `xl2ai/mcp_server.py`, no third-party dependency): JSON-RPC 2.0 over stdio,
  protocol versions 2025-06-18 / 2025-03-26 / 2024-11-05. Server `instructions` state the workflow; eight tools
  (`start`, `prepare`, `find`, `table`, `query`, `facts`, `region`, `trace`) with read-only annotations (only
  `prepare` writes); resources (`xl2ai://brief`, `xl2ai://context-pack`, `xl2ai://instructions`); a prompt
  (`analyze_excel_folder`). `prepare` runs the refresh in a background thread and reports progress from the
  pipeline's event bus, so long builds never block or time out a tool call. stdout carries protocol only (stray
  output is redirected to stderr). Tool errors are results with a code and hint, never crashes.
  `xl2ai connect [--workspace] [--write]` prints the Claude Code / Claude Desktop / generic MCP settings.
  This supersedes the "CLI first, MCP only if needed" decision in `ARCHITECTURE.md` §5.5: both exist, over the
  same functions.
* **Cross-file SQL without aliases**: `query sql "*"` (and the MCP `query` tool) exposes every table by its plain
  name when that name is unique across workbooks; a name present in several workbooks fails with a message naming
  the qualified choices, instead of SQLite silently picking one.
* **Report reconciliation** (`reconcile` stage, `_reconciliation`): small report tables are matched against raw data
  tables -- report row labels vs a raw category column, report numbers vs SUM/COUNT of each raw amount by that
  category, a "Total" row vs the grand total. Explained columns are listed; disagreeing rows become a
  `report_disagrees_with_data` gap in `brief` with both numbers. Values only, so it also works for pasted-value and
  DRM reports.
* **Anomalies** (`anomalies` stage, `_anomalies`): extreme outliers (3xIQR fences) with examples by Excel row, rare
  negatives in positive measures, months far from the usual level (robust z on the monthly key numbers), months
  with no rows inside the covered range, and dates before 1990 or over a year ahead. Shown in the agent brief,
  context pack, `brief` flags and `query meta anomalies`.
* `query trace` now names the file, sheet and row (and says clearly when a row is not stored). `query` sub-commands
  accept `--config`/`--run` after the command too.

## 0.12.0 - Key numbers, find, cross-file joins, parallel extraction (2026-09-23)

The theme: the questions every agent asks first on unfamiliar data -- "what are the big numbers", "where is X",
"how do I combine these two files" -- are answered before it arrives, and extraction of many large files is faster.

* **Key numbers** (new `digest` stage and `xl2ai digest`, catalog `_digest` / `_digest_tables`): for every plain
  data table, totals/average/range of its amount columns, the ten biggest groups of each category column (with
  share of total, plus an "other" remainder), and the month-by-month trend of its main date column -- each number
  stored with the exact SQL that produced it, so it can be re-run with `query sql`. Honesty rules: flagged totals
  rows are excluded (and counted); per-unit values (price, rate, average...) are averaged, never added up; ids and
  codes are never measures; reports, dashboards and multi-table sheets are skipped with the reason. Shown in
  `ai/agent_brief.md` ("Key numbers"), the context pack, `query digest <table> [--section]` and `query meta digest`.
* **`xl2ai query find <text> [--values]`**: where a concept lives, in one call -- table/sheet names, column
  names/headers, group headers, definitions and profiled values; `--values` also searches every text cell of every
  source within a time budget and says how much it covered. Case-insensitive and forgiving of Arabic spelling
  variants (أ/إ/آ/ا, ة/ه, ى/ي, diacritics, tatweel).
* **Cross-file SQL**: `xl2ai query sql "*" "<SELECT>"` attaches every source database read-only (as
  `<source alias>.<table>`) so tables from different workbooks join in one statement; the same authorizer, query-only
  mode and caps apply. `ai/agent_brief.md` gained "How the tables connect": each relationship with its confidence
  and a ready JOIN clause (cross-file aware).
* **Parallel extraction** (direct engine): several changed workbooks are extracted at once in separate processes
  (`[extract] workers`, 0 = auto, up to 4). The Excel engine stays one-at-a-time (one private Excel). Measured: three
  57 MB workbooks (3M rows) in 39 s. `workers` does not affect the reuse fingerprint.
* **Totals rows**: detection now also looks in mixed-type columns (a "Total" typed into a numeric id column is
  exactly what makes it mixed -- previously missed) and accepts short labelled forms ("Cairo Total", "Total Q1",
  "إجمالي القاهرة"), length-capped so sentences mentioning a total are not flagged.
* **Column roles**: a repeating number named like an id (`customer_id` in an orders table) is now `code`, not
  `quantity`, so it is never summed.
* **Fix**: `ai/agent_brief.md` written by `refresh` always reported "stale" and "not promoted" gaps, because it was
  judged against the previous run while its own run was still being built. The refresh stage now marks the brief as
  in-progress and skips those two self-referential checks (a run is only promoted if every stage passes).

## 0.11.0 - A second extraction engine, report-shaped sheets, formula lineage (2026-09-22)

**No output change for existing Excel-engine projects except three new, additive extract tables** (`_regions`,
`_header_groups`, `_formula_refs`, empty unless something is found) and their catalog counterparts plus
`_lineage`. `[extract] engine` defaults to `auto`, which on a Windows+Excel machine is the Excel engine exactly
as before, and its reuse fingerprint is unchanged, so unchanged workbooks are still reused, not re-extracted.
`tests/golden/fingerprints.json` needs regenerating on Windows (`python tests/regen_golden.py`) because the extract
schema gained tables -- still outstanding, like the two earlier schema additions.

* **Direct engine** (`xl2ai/extract/direct.py`, `[extract] engine = "direct"`, `xl2ai extract --engine direct`):
  reads .xlsx/.xlsm/.xlsb/.xls without Excel via python-calamine (optional extra: `pip install -e ".[direct]"`),
  on any OS. It reuses the Excel engine's header detection, column typing, value conversion, preamble, naming and
  structure code -- only the cell reading differs -- and writes the same database contract.
  - Verification: for .xlsx/.xlsm every column's count and numeric sum, and the whole-sheet cell total, are checked
    against a second, independent reader (a streaming byte-level scan of the sheet XML). For .xlsb/.xls the
    per-column counts come from the reader's own grid and `_verification.note` says so; there is no whole-sheet
    check there rather than a fake one.
  - The same XML scan restores Excel error cells (NULL + `_cell_errors`, as the Excel engine does -- the reader
    alone would show them as empty), captures every formula per column (`_formulas` + all referenced sheets in
    `_formula_refs`), and detects Power Query, the Data Model, external links (resolved to file names), pivots and
    charts from the package parts. On .xlsb/.xls a `reader_limit` blind spot states what could not be seen.
  - DRM-wrapped files are refused with a clear reason (only Excel's rights agent can open them); nothing is written.
  - Measured on a 57 MB .xlsx (1,000,000 rows x 10 columns): ~55 s end to end with all 17 checks passing, ~1.2 GB
    peak with the default `cache_cells`; ~67 s with `cache_cells = 0` (streams twice instead of caching).
  - `auto` = Excel engine when this machine can drive Excel, otherwise direct. `xl2ai doctor` reports which engine
    is in use and whether python-calamine is installed.
* **Several tables on one sheet** (`xl2ai/extract/regions.py`, both engines, no extra Excel calls): blocks
  separated by >= 2 blank rows that start with a header-like row, or by an empty column whose two sides do not
  share the same rows, are recorded in `_regions`. The sheet keeps its identity (still one stored table), is
  marked `needs_review` with a `several_tables_in_sheet` gap in `brief`, and `xl2ai query region <table> <n>`
  returns one region's rows named by that region's own header row.
* **Grouped (multi-row) headers**: up to three label rows directly above the header, bounded by merged areas or
  by label runs (a lone unmerged title is never spread over the header), recorded per column in `_header_groups`
  ("Plan" under "Q1"), shown in `ai/agent_brief.md` and `query meta header_groups`. Column names are not rewritten.
* **Formula lineage** (`xl2ai/lineage.py`, catalog stage): formula text is parsed for `Sheet!`, `'My Sheet'!`,
  `[Book.xlsx]Sheet!`, path-qualified and `[n]`-indexed references; each is resolved to a table in this project
  (same workbook, or another source matched by file name) or marked `external`/`unresolved`. Own-sheet references
  are arithmetic, not lineage, and are skipped. Surfaced in `brief` (`feeds_from`, and a
  `depends_on_unextracted` gap when a source is outside the project), the context pack ("Computed from") and the
  agent brief. Always `inferred`; the Excel engine contributes one sample formula per column, the direct engine
  every formula.
* `query meta` gained `regions`, `header_groups`, `lineage` (older runs answer with an empty result and a hint to
  refresh, never an error). `DQ_FORMULAS_VALUE_ONLY` now points at `query meta lineage`.
* `[extract] engine` is validated (`auto|excel|direct`); a typo is a config error like every other key.

## 0.10.0 - Agent readiness, phase 6: the agent interface and staying fresh (2026-09-22)

* Added `ai/agent_brief.md` (new `agent_brief` stage, `xl2ai agent-brief` standalone command): one plain-language
  file combining `brief`'s readiness verdict, the change log explained in plain words ("why it matters" per
  change kind), every table by its real name with its grain and column roles spelled out, and confirmed/drafted
  definitions and KPIs -- what would otherwise take five separate calls to assemble. Deliberately not a
  replacement for the compact, handle-based `ai/context_pack.md`: the two serve different moments (see
  `ARCHITECTURE.md` §4).
* Documented the CLI-vs-MCP access decision as final, not deferred (`ARCHITECTURE.md` §5.5): an MCP server pays a
  context cost on every call for its tool schemas, a CLI process pays nothing until invoked, and the CLI's
  responses are already the envelope an MCP tool would need anyway. Build a wrapper only where a host cannot
  spawn a process.
* Documented freshness and the readiness gate (`ARCHITECTURE.md` §5.6, `README.md`): scheduling `xl2ai refresh`
  is an external cron/systemd-timer/Task-Scheduler responsibility by design (this is not a daemon); `xl2ai
  brief`'s exit code is meant to be checked by whatever calls it (including a scheduled job's own script) so
  stale, unpromoted or failed data is refused rather than silently used. The readiness-gate mechanism itself
  shipped in phase 1 (0.5.0); this phase documents and completes its intended use.

## 0.9.0 - Agent readiness, phase 5: opt-in reversible repairs (2026-09-22)

**No output change when `[repair].enabled` is left at its default (`false`)**: the new `repair` stage runs, finds
nothing to do, and writes zero rows -- extraction output, catalog content and every existing test are unaffected.

* Added `xl2ai/repair.py`, a new stage between `semantics` and `rules`, gated by `[repair]` in `xl2ai.toml`
  (`enabled = false` by default). When enabled, it suggests three kinds of fix without ever writing to the
  extracted database or any data table an agent queries directly:
  - **null-token normalization**: text values already flagged by `DQ_NULL_TOKEN` ("N/A", "-", "(blank)", ...) get
    a suggested repair to NULL.
  - **category spelling consolidation**: whitespace/case variants of the same category value ("Cairo" / "cairo"
    / " CAIRO ") are mapped onto whichever spelling is most frequent in the table.
  - **text-as-number coercion**: text that reads as a number once thousands separators are stripped ("1,234")
    gets a suggested numeric repair; a value that is already a clean number as text is left alone (no no-op
    suggestions).
* Every suggestion is a row in `_repairs` (`table_id, column_id, xl_row, original_value, repaired_value, rule`)
  and nothing else changes: this is a stronger safety property than an undo log, since there is nothing to undo.
* Added `xl2ai query repaired <table>` to see rows with suggestions applied on the fly, and
  `xl2ai query meta repairs` to inspect the full suggestion list -- both read-only, both leave the source
  database untouched.

## 0.8.0 - Agent readiness, phase 4: the semantic layer (2026-09-22)

**Extraction database schema unchanged** in this phase; only `catalog.db` gained tables (additive, computed
entirely from data already in the catalog -- no re-extraction, no golden-fingerprint impact).

* Added column-role classification (`xl2ai/semantics.py`, new `semantics` stage between `analyze` and `rules`):
  `identifier | date | money | quantity | percentage | category | code | boolean | free_text | geo | contact`,
  a small explicit rule set over signals `analyze` already computed (uniqueness, type, name tokens), always
  `inferred` with a method and reasons -- never a model call, never presented as confirmed. New `_column_roles`.
* Added unit/currency detection from column names only (EGP/USD/EUR/SAR and common symbols, plus "percent"),
  deliberately not from values -- this platform does not yet parse Excel number formats reliably enough for that.
* Added table-grain detection (`_table_grain`): derives "one row per X, identified by columns Y" from the
  strongest confirmed/candidate key, or explicitly states the grain is unknown when no key reaches 90% uniqueness
  -- an unclear grain is stated as unclear, never guessed, since it is the single fact that makes or breaks any
  aggregation run against the table.
* Added time-coverage detection (`_time_coverage`): min/max of every date/datetime column already profiled.
* Added auto-drafted definitions: one sentence per table (grain + time coverage) and a short note per
  money/quantity/percentage column with a detected unit, written into `_dictionary` with `origin='auto'`,
  `status='inferred'` -- the raw material for a human to confirm via a pack (see `BUSINESS_RULES.md`).
* Added cross-file duplicate/version detection (`_duplicate_candidates`): tables with an identical schema
  fingerprint from different source files are flagged, scored higher when their row-hash sets actually overlap
  (already computed by `analyze`'s row-multiset fingerprinting) -- catches "this is last month's copy of that
  file" without a human having to notice it.
* `xl2ai query meta column_roles/grain/time_coverage/duplicates` expose all of this directly. The context pack's
  `tables[]` entries gained `grain`. `xl2ai brief` reports `grain_unknown` as a gap (informational: it does not
  by itself downgrade a table's readiness, since an unclear grain is a property of the data, not a defect in it).

## 0.7.0 - Agent readiness, phase 3: structural truth (2026-09-22)

**Extraction database schema change**: `_extraction_log` gained `header_confidence`/`header_reasons` columns.
Golden fingerprints (`tests/golden/fingerprints.json`) must be regenerated with `python tests/regen_golden.py` on
a Windows machine with Excel before this change is considered fully validated -- that could not be run in this
environment (no Excel/COM available here). `test_golden.py` will fail on a real Windows+Excel run until then;
it is currently skipped wherever pywin32 is unavailable, so this was not caught by this environment's test run.

* Added header-detection confidence scoring (`xl2ai/extract/layout.py:header_confidence`): a score in [0,1] plus
  plain-language reasons (columns filled, text ratio, repeated/grouped labels, whether data follows), computed as
  a pure function of already-read cell values so it is unit-testable without Excel. Carried through
  `_extraction_log` into the catalog's `_tables.header_confidence`/`header_reasons`.
* Added totals/subtotal-row detection (`_row_flags`, flag `totals_candidate`): rows whose text column reads as a
  totals label ("Total", "Grand Total", "Subtotal", "إجمالي", "المجموع", ...) are flagged by `_xl_row`, with a new
  `DQ_TOTALS_ROW_IN_DATA` quality finding -- a default aggregate over such a table would otherwise double-count them.
* Added table-kind classification (`_table_kind`): `data | notes | report | dashboard | empty`, a small explicit
  rule set over signals already computed (row/column count, header presence, formula density, pivot presence,
  totals rows), always `inferred` with a method and reasons, never presented as confirmed.
* `xl2ai query meta table_kind` / `row_flags` expose both directly. The context pack's `tables[]` entries gained
  `kind`. `xl2ai brief` downgrades a table with an unresolved totals row from `ready` to `needs_review` and lists
  it under `gaps`.
* Documented (`EDGE_CASES.md`) that full region detection (several tables per sheet), multi-row/hierarchical
  headers and transposed-table reconstruction remain out of scope for this phase -- they change the extraction
  identity model itself and need validation against real, messy workbooks on Windows+Excel, which this
  environment cannot provide. See `AGENT_READINESS_PLAN.md` phase 3 for the reasoning and what remains.

## 0.6.0 - Agent readiness, phase 2: honest gaps (2026-09-22)

**Extraction database schema change**: new `_unsupported` and `_formulas` tables in both the per-workbook
extraction database and the run catalog. Golden fingerprints must be regenerated with `python tests/regen_golden.py`
on a Windows machine with Excel before this change is fully validated -- see the note on this in 0.7.0 below,
which applies equally here; both phases landed in the same session with no Excel/COM environment available.

* Added detection of content this platform cannot fully read, so a run reports it instead of silently treating
  the workbook as fully understood: Power Query steps, the Excel Data Model, external workbook links (workbook
  scope), and charts (sheet scope), each with a plain-language explanation of what is not captured. New
  `_unsupported` table in both the per-workbook extraction database and the run catalog.
* Added stale-calculation detection: if Excel reports pending recalculation immediately on opening a workbook
  (before this tool's own manual-calculation setting could mask it), that is recorded as a blind spot -- any
  formula-derived value in that workbook may not reflect its latest inputs.
* Added per-column formula detection (`_formulas`): a column backed by at least one formula is now distinguishable
  from one that was typed in, with a sample R1C1 formula for context.
* The AI context pack gained a `blind_spots` section (JSON and Markdown), populated from `_unsupported`, so an
  agent reading only the compact pack still sees what could not be fully read -- not just what could.
* Added `xl2ai query meta unsupported` to inspect the full list without opening the workbook.
* `xl2ai brief`'s per-table readiness now downgrades a table with an unresolved blind spot from `ready` to
  `needs_review`, and lists each one under `gaps`.

## 0.5.0 - Agent readiness, phase 1: a truthful entry point (2026-09-22)

* Added `xl2ai brief`: one bounded call that orients a cold agent — current run, freshness, a per-table readiness
  verdict (`ready`/`needs_review`/`not_ready`) derived from verification and quality findings, an explicit `gaps`
  list, and ordered `next_commands`. Exit codes: 0 ready, 1 needs review, 2 not ready.
* Rewrote `skills/platform-overview/SKILL.md`: it previously told agents that every stage after `extract` was
  still "planned" and to "not invent" commands that had in fact shipped long ago — any agent obeying it would
  bypass the whole platform and read Excel directly. It now lists every shipped command and states the platform's
  actual purpose (pre-digest workbooks once so later agents never re-derive that understanding from raw Excel).
* Added `skills/agent-start/SKILL.md`: the concrete first command a cold agent runs, and how to read `brief`'s
  exit code and `gaps` before proceeding.
* Added `skills/query-playbook/SKILL.md`: a question -> tool map for the query layer, with an explicit "stop
  reading here" rule per tool and the trust-discipline reminders (cite evidence, never flatten inferred into
  confirmed, never silently drop a flagged gap from an answer).

See `AGENT_READINESS_PLAN.md` for the full six-phase plan this starts.

## 0.4.0 - Observability: live run view and failure diagnosis (2026-09-22)

* Added `xl2ai watch`: refreshes with a live, step-by-step terminal view built on a new dependency-free `xl2ai/ui/`
  toolkit (capability detection, colour/glyph fallbacks, display-width-aware tables/panels/trees/bars/spinners,
  flicker-free live region). `--demo` simulates a run so the interface is visible and testable without Excel.
* Added `xl2ai diagnose`: explains a finished run from its event journal, surfacing the first real failure (not its
  downstream symptoms) and the events around it; `--ai` prints that as plain text to paste into an AI assistant.
* Added `xl2ai/observe/`: an event vocabulary, a bus whose subscribers can never break the pipeline, and a JSON
  Lines journal (`data/runs/<run_id>/journal.jsonl`) written by every run and readable even after a killed process.
* Fixed extraction on non-Windows machines failing with an unrelated `AttributeError` instead of reporting plainly
  that Excel and pywin32 are required.
* Fixed rules/KPIs sharing one SQL deadline per pack, letting an earlier slow rule starve or fail later fast ones
  and abort the whole run; each rule/KPI now gets its own deadline and KPI failures are caught and surfaced rather
  than aborting the stage.
* Fixed a lock-acquisition crash when another process's lock file is mid-write, and a `pid_alive()` case where
  "permission denied" was treated as "process is dead" (could let two refreshes hold the lock at once).
* Fixed uncaught tracebacks from SQLite errors in query `aggregate`/`trace`/`sample`, now returned as the documented
  `{ok:false, error}` envelope; `sample()` no longer returns one row more than `--limit`.
* Fixed context-pack building being O(n^2) (300 tables: 5.7s -> 0.04s) by tracking token cost incrementally.
* Fixed row fingerprinting detecting the Excel row-position column by value-type guessing, which could silently
  strip a real integer business column; it is now detected by name (`_xl_row`).
* Fixed relation inference always running before rule-pack keys/relationships were applied, and never using
  confirmed keys as parent candidates; rules now run first, and inference preserves confirmed relationships while
  treating confirmed keys as fully trusted.
* Fixed source-freshness comparison using a local-time string, which could make every source look modified across a
  timezone/DST change; now compared as UTC mtime.
* 36 new tests; full suite 167 pass (138 run by default, 29 skipped without Windows/Excel).

## 0.3.0 - AI-ready platform completion/hardening (2026-09-21)

* Added run catalog with source/sheet/name-based stable identities; schema/type/position drift is tracked separately.
* Added profiling, generic data-quality findings, candidate keys and conservative relationship inference.
* Added TOML rule packs with confirmed terms, keys, relationships, deterministic rules and KPIs.
* Added run-to-run source/schema/volume/value/category/distribution/KPI and exact row-multiset change detection.
* Added compact token-budgeted AI context pack and capped schema/describe/sample/aggregate/compare/trace/SQL tools.
* Added SQLite read-only authorizer protection for AI/rule SQL.
* Added project init, doctor, report and deep SHA-256 status verification.
* Fixed Windows file-handle leaks by explicitly closing read-only SQLite connections.
* Added installable package metadata and the `xl2ai` console command.
* Added Windows/Linux Python 3.11/3.12 CI plus a COM-free cross-stage smoke test.

Output-affecting changes are listed explicitly; the golden fingerprints (`tests/golden/fingerprints.json`) are
regenerated only together with an entry here.

## 0.2.0 - Incremental refresh (2026-09-21)

* `xl2ai refresh` now skips Excel entirely for a source when the previous promoted run passed and the source path,
  SHA-256, size, mtime and extraction-affecting settings are all unchanged.
* Source fingerprinting now uses full SHA-256 by default, including files above 256 MB; the former first/last-chunk
  shortcut remains available only as explicit opt-in and is never used by normal refresh caching.
* Reused databases are materialized inside the new run (NTFS hard link when possible, copy fallback), so retention can
  safely remove old runs without breaking the current dataset.
* Reuse is recorded per source in the manifest with `reused`, `reused_from_run` and `reuse_mode`.
* `xl2ai refresh --force` bypasses reuse and re-extracts every source.
* Added COM-free regression tests proving unchanged reuse, changed-input invalidation, extraction-config invalidation,
  retention-policy independence and self-contained reused artifacts.

## 0.1.0 - Phase 0: package split (2026-09-21)

* `excel_to_sqlite.py` (1,140 lines) split into `xl2ai/extract/` (`common com names dates coltypes layout store sheet verify
  sources pipeline`). `python -m xl2ai extract ...` added; `python excel_to_sqlite.py ...` kept as a shim.
  **Output unchanged**: 4 regression databases (2 synthetic, 2 specimen) byte-identical to the monolith.
* New tests: golden fingerprints, structure (every global name resolves; platform has no specimen words; platform
  never imports packs; CLI/shim entry points), leak checks by exact Excel PID (the tool now logs `Excel started (pid N)`).
* Fixed `xl2ai/__main__.py` running `main()` at import time. Fixture builder now waits for its Excel to exit.
* Environment knowledge (DRM file prefix) isolated in `sources.WRAPPER_PREFIXES`.
* Documentation: README, ARCHITECTURE, DATA_CONTRACT, EDGE_CASES, TESTING; `skills/` for the extract stage.

## Extractor history before the package (same code, earlier state)

* Excel COM extraction with private instance, watchdog, crash restart/retry, dialog detection (password prompt).
* True extent via `Find`; filters cleared in the in-memory copy because `Find` skips filtered rows (a real
  data-loss bug found on the specimen: 92% of one sheet was missing before the fix).
* Header detection with preamble kept; whole-column typing; Excel error cells recorded; dates incl. 1904 and the 1900 leap-year bug.
* Verification against Excel itself, including a whole-sheet `COUNTA` that is independent of the chosen extent.
* Atomic `.partial` database; a failed run keeps the previous database.

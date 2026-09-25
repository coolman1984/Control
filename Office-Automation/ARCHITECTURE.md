# ARCHITECTURE

`xl2ai` is a general-purpose local "data preparation operating system for AI": give it unfamiliar business
workbooks, it extracts and understands them deterministically, and an AI reads a tiny context pack and queries
only small slices. The workbooks in `Price Comparison Data/` are **one test specimen**, not the specification.

Status legend: **[built]** exists and is tested, **[remaining]** is intentionally not complete. See `CHANGELOG.md` for shipped behavior.

## 1. Principles (each one is a testable rule)

1. Never silently lose data: every cell is either stored or recorded as skipped/failed, and totals are verified.
2. Never assume structure: everything is detected, or comes from explicit config, and is labeled which.
3. Generic engine and project knowledge are separate. `xl2ai/` contains no domain words; rule packs hold them.
4. Deterministic code makes every number; AI never calculates what a tool can.
5. AI receives compact meaning + evidence, never raw datasets.
6. Every important result carries provenance (run, source, sheet, row, column, rule).
7. Inferred facts are labeled `inferred` with method + score; only a human can make them `confirmed`.
8. A failed refresh never replaces the last valid dataset.
9. Speed, reliability and token cost are designed in, and measured.
10. Every stage runs alone and through one batch command.

## 2. Review of the implementation [built: `xl2ai/extract/`, split from a 1,140-line monolith in phase 0]

Phase 0 is done: `python -m xl2ai extract` (and the old `python excel_to_sqlite.py`) run the same code; output is
byte-identical to the monolith on 4 regression databases (`tests/golden/`). Modules: `common com names dates
coltypes layout store sheet verify sources pipeline`. The findings below are the review that preceded the split.

### 2.1 Generic vs specimen-specific

| Area | Verdict |
|---|---|
| COM session, watchdog, dialog detection, crash recovery | generic |
| extent detection, filter handling, header detection, preamble, merges | generic (heuristics, thresholds are constants) |
| typing, date/1904/1900-bug handling, error cells, verification, atomic DB | generic |
| tests + `tests/make_fixtures.py` | synthetic, generic edge cases |
| `output/`, `test_output/`, `extract_excel.py` | specimen output / superseded draft; not part of the platform |
| any sheet, column or business name in the engine | **none** now. The first scan missed two leaks that the new lint test (`test_platform_contains_no_specimen_words`) found: a comment naming a specimen column (removed) and a corporate DRM marker (`<## ...` file prefix) hard-coded in source detection (now the isolated, configurable `WRAPPER_PREFIXES` in `sources.py`, moving to config in phase 1) |

So the extractor is a sound generic base. The specimen-specific risk is in *what I planned to build next*,
not in what exists: the earlier plan had a "price/volume/mix" phase. That belongs in a rule pack (section 5).

### 2.2 Architectural risks

| # | Risk | Impact | Planned fix |
|---|---|---|---|
| R1 | One wide table per sheet; multi-row headers and several tables per sheet are flattened | wrong/awkward schema for report-style sheets | mitigated (0.11.0): regions and grouped headers are detected and recorded, `query region` reads one table back out; the stored identity is still one table per sheet |
| R2 | Header detection is a fixed heuristic with no confidence or override | silent mis-detection | score + reasons stored in `_tables`; config override per sheet |
| R3 | Table identity = sanitized sheet name | renamed/re-ordered sheets break change detection | stable `table_id` + rename detection by header fingerprint |
| R4 | DB file name = file stem | `a/report.xlsx` and `b/report.xlsx` overwrite each other | run folder + `source_id` naming |
| R5 | No run history, no lock | no diff, two concurrent runs corrupt each other | run manifest, lock file, retention (phase 1) |
| R6 | Values only; formulas, pivot definitions, names, links not captured | business logic invisible | formula/pivot capture (phase 3) |
| R7 | Scale tested to 5.8M cells / 1 sheet; a 1M-row x 50-col sheet takes two COM passes | time/memory unknown | scale benchmark suite; adaptive block size; single-pass typing option (phase 2) |
| R8 | Dialog detection only sees dialogs of the Excel process; a DRM agent prompt in another process falls back to the timeout | slow failure | also watch new top-level windows of the DRM/parent process tree; shorter default timeout for open-without-progress |
| R9 | No text-number / text-date / null-token normalization | dirty data reaches analysis unlabeled | normalization stage, opt-in and recorded (phase 4) |
| R10 | Only `.db` + console output; nothing for AI | the actual goal is missing | context pack + query tools (phases 6-7) |
| R11 | Single 1,000-line module | risky to extend | **done in phase 0** (package split, golden-verified) |
| R12 | Excel Data Model / Power Query / external data not covered | data invisible to the tool | detect and report as `unsupported_content` at minimum (phase 3) |

## 3. Long-term structure

```
Files -> sources -> extract -> validate -> normalize -> profile -> quality -> schema -> relations
      -> dictionary -> formulas/rules -> kpis/reconcile -> changes -> contextpack -> query -> AI
```

```
xl2ai/                      PLATFORM: no domain knowledge, ever
  core/      config (layered), runs (manifest, lock, promote, retention), contracts, provenance, tokens, log
  sources/   detect (format, DRM, encryption, temp files), inventory (sheets, visibility, pivots, names, links)
  extract/   com, layout, regions*, headers*, coltypes, dates, names, store, sheet, verify, pipeline
             formulas*, pivots*                                        (* = new phases)
  repair.py  opt-in cleanup suggestions (null tokens, category spelling, text-as-number); never writes to a data
             table -- every suggestion lives only in `_repairs`, applied on the fly by `query repaired`
  normalize/ (superseded by repair.py above for the cases it covers) text->date coercion remains planned
  profile/   per-column stats, samples, distributions
  quality/   generic checks -> findings with severity, codes, evidence
  schema/    table registry, schema fingerprints, drift, rename detection
  keys/      candidate (composite) keys
  relations/ inclusion + name/type similarity; status inferred|confirmed|rejected
  semantics.py column roles, unit/currency, table grain, time coverage, auto-drafted definitions, duplicate hints
  dictionary/ persistent meanings/aliases/units; auto-drafts marked inferred
  formulas/  formula patterns, dependencies, reference graph
  rules/     registry + engine: declarative checks, reconciliations, calculators from packs
  kpi/       KPI engine (definitions come only from packs/config)
  changes/   run-to-run diff (schema, volume, values, categories, KPIs)
  contextpack/ builder, token budget, renderers
  query/     safe tools (schema, describe, sample, slice, aggregate, compare, trace, sql)
  cli.py     `python -m xl2ai <stage>` ; `refresh` = all stages
packs/<name>/  PROJECT KNOWLEDGE: pack.yaml, dictionary.yaml, rules.yaml, calculators.py, fixtures/
skills/<topic>/SKILL.md   instructions for AI agents
tests/       unit, integration (real Excel), golden (specimen), synthetic fixtures, failure injection
```

Boundary rules, enforced by tests: (a) `xl2ai/` never imports `packs/`; packs are loaded through a plugin
interface by path from config; (b) a lint test fails if `xl2ai/` contains any word from a denylist built from the
specimen files; (c) the whole platform test suite passes with **no pack installed**.

### 3.1 Run model [built]

Every refresh creates `data/runs/<run_id>/` with `manifest.json` (stages, status, timings, input fingerprints:
path, size, mtime, sha256). Stages write only inside their run folder. The run is **promoted** by atomically
rewriting `data/current.json` only after all required stages pass. A failed/partial run stays inspectable and never
becomes `current`. Last N runs are kept (default 3). A lock file prevents concurrent refreshes of the same project;
a live owner is never displaced based on lock age alone.

### 3.2 Config layering

`built-in defaults < xl2ai.toml (project) < pack config < CLI flags`. Every detected decision that config can
override (header row, table regions, column rename, type override, ignore sheet) is written to `_tables` /
`_columns` with `source = detected | config` so the AI can tell which.

## 4. AI-facing interfaces and token strategy

Read in tiers; stop at the first tier that answers the question.

| Tier | Artifact / tool | Size target | Purpose |
|---|---|---|---|
| -1 | `xl2ai brief` / `ai/agent_brief.md` | one call / one read | cold-start orientation: readiness, gaps, changes explained in plain language, tables by name with roles/grain, next commands |
| 0 | `ai/context_pack.md` (+ `.json`) | <= 4k tokens (configurable, test-enforced) | orientation: sources+freshness, tables (grouped), relationships, definitions, KPIs, changes, warnings |
| 1 | `xl2ai describe <table>` / `ai/profiles/<table>.json` | <= 1.5k tokens | one table: columns, types, stats, samples, quality, keys |
| 2 | `xl2ai query sample|slice|aggregate|compare` | default <= 50 rows / 8 KB | targeted evidence |
| 3 | `xl2ai sql "SELECT ..."` | same caps, read-only | anything else |
| - | `xl2ai trace <table> <row>` | tiny | source workbook/sheet/cell/formula/run for any row |

`brief`/`agent_brief.md` and `context_pack.md` serve different moments, not different agents: `brief` is a single
bounded call for "is this safe to use and where do I start", read once at the start of a session; `context_pack.md`
uses short handles (`t3.c7`) because it may be reread every turn of a long session, where every token spent on
verbose names is a token not spent on the actual question. Reach for `agent_brief.md` when clarity matters more
than size (a first read, a human skimming alongside the agent); reach for `context_pack.md` once oriented and
optimizing for a long session's token budget.

Token-saving techniques: short stable handles (`t3`, `t3.c7`) with a legend; **schema families** (identical
column sets collapse into one entry + list of tables); column groups (`col_1..col_40`); domains as top-k with
counts, not full lists; numbers rounded to significance; sections priority-ranked with explicit
"omitted: N items -> use tool X"; **delta-first pack** (`pack --since <hash>` returns only what changed so a
returning agent does not reread); stable ordering so prompt caching works; every tool returns
`{ok, columns, rows, truncated, total_rows, bytes, hint, evidence}` and refuses to return a whole table.

Safety: query tools open SQLite `mode=ro`, allow a single SELECT, enforce row/byte/time caps, never expose file
paths outside the run folder, and never read Excel.

## 5. Project knowledge lives in packs [built core]

The built pack format is TOML: one `pack.toml` can define confirmed terms, keys, relationships, SQL assertions and
KPIs. Packs are loaded only from paths declared in project config. Results carry pack/version evidence. Python
calculator plugins and automatic applies-when selectors remain optional future extensions; they are not silently
treated as built.

## 5.5. Agent access: CLI first, decided (not just deferred) -- superseded in 0.13.0

**0.13.0:** an MCP server now exists (`xl2ai serve`, see `CONNECTING.md`) because agent hosts that cannot, or should
not, be taught a command line are the common case in practice. It is a thin layer over the same query/brief
functions (so it cannot drift from the CLI), keeps its tool list to eight purpose-named tools with short schemas to
limit the per-call context cost argued below, and returns the same capped, evidence-bearing answers. The original
reasoning follows for the record.

Section 7 has said "tools are CLI first, an MCP wrapper is optional later" since before any agent-readiness work
existed. That default is now a decision, not a placeholder, based on: an MCP server pays a context cost on every
call for its tool schemas, while a CLI process pays nothing until it is actually invoked; the CLI's tool
responses are already the strict, capped, evidence-bearing JSON envelope an MCP tool would need to expose anyway;
and this platform's one hard requirement -- an agent must never open the source workbook itself -- is enforced by
the CLI process boundary regardless of what calls it. Build an MCP (or any other) wrapper only when a specific
host cannot spawn a process; it should be a thin envelope-preserving shim over these same commands, never a
second implementation of the rules in `AI_USAGE.md`.

## 5.6. Freshness and the readiness gate

A stale `ai/context_pack.md` is more dangerous than none: an agent trusts it completely and has no way to tell it
is outdated. Two things make freshness a checked property rather than a hope:

- **Scheduling is external, deliberately.** This is an offline-first CLI tool, not a daemon; running `xl2ai
  refresh` on a schedule is the job of the host's own scheduler (cron, systemd timer, Windows Task Scheduler),
  pointed at the project's `xl2ai.toml`. Building a scheduler into the platform would mean owning process
  supervision, logging and failure alerting that the OS already does better.
- **The readiness gate makes staleness loud.** `xl2ai brief` (and `ai/agent_brief.md`, written every run) is not
  merely informative: its exit code (0 ready, 1 needs review, 2 not ready -- stale, unpromoted or failed) is
  designed to be checked by whatever calls it, including a scheduled job's own script, so an agent or automation
  can refuse to proceed on data it has not verified is current rather than silently using it.

## 6. Roadmap with regression gates

Gate on every phase: the 40 existing tests pass, and the golden fingerprints of the four regression databases
(specimen xlsx/xlsb + 2 synthetic) are unchanged, unless a phase deliberately changes them (then the change is
listed in `CHANGELOG.md` and the goldens regenerated in the same commit).

| Phase | Deliverable | Extra proof |
|---|---|---|
| 0 | package split, CLI entry, no behavior change; docs; golden harness | fingerprints byte-identical |
| 1 | runs/manifest/lock/promote, config, `sources` inventory, `refresh` skeleton | kill mid-run => `current` untouched; two same-stem files do not collide |
| 2 | regions, multi-row headers, schema registry with stable ids, scale benchmarks | synthetic fixtures for each; 1M-row benchmark |
| 3 | formula + pivot + names capture, `unsupported_content` report | formula patterns on synthetic + specimen |
| 4 | normalize, profile, quality, keys | seeded-defect fixtures: every seeded defect found, clean data yields no findings |
| 5 | relations, dictionary, rules/reconcile/KPI engine, pack loader | pack removed => platform tests pass; wrong rule fails loudly |
| 6 | change detection | modified copy of a fixture => exact expected diff |
| 7 | context pack + budget + query tools + skills + remaining docs | pack under budget on every fixture; caps/read-only tests; a cold agent answers questions from the pack alone |

## 7. Defaults chosen (veto any)

Per-workbook `.db` stays as the extraction artifact (unchanged) plus one `catalog.db` per run for analysis
tables; dictionary/rules are YAML (PyYAML installed); tools are CLI first, an MCP wrapper is optional later
(offline-first); retention 3 runs; row-level change detection uses per-table row-hash multisets (no key needed),
key-based diff only for human-confirmed keys; no git commit is made without your say-so (the repo has none).

---
name: platform-overview
description: Start here. What xl2ai is, what tools exist today, and the rules every agent must follow when working with Excel-derived data.
---

# xl2ai overview

xl2ai turns unfamiliar Excel workbooks into a small, verified, pre-digested layer of meaning so an agent never has
to open the raw workbook, guess at its structure, or spend tokens re-deriving what code can already prove.
Extraction happens once (or on a schedule); every agent after that reads a few hundred tokens of compact,
evidence-backed metadata instead of tens of thousands of tokens of raw cells.

**Read `skills/agent-start` next — not this file's command table.** This file exists to state the rules. The
command table you actually run to get oriented lives there, and it is generated from `python -m xl2ai --help`
rather than hand-copied here, so it never goes stale the way this file's table once did.

## The one rule that matters most

**Never open a source workbook with anything other than `xl2ai extract`/`xl2ai refresh` (no openpyxl, no pandas,
no COM of your own).** Everything else in this document exists to keep that rule cheap and safe to follow: the
platform's whole job is to make reading the *workbook* unnecessary.

## What is built (do not assume otherwise — verify with `python -m xl2ai --help`)

Every stage below is implemented and tested. If a command you expect is not listed there, it genuinely does not
exist yet — check `ARCHITECTURE.md` roadmap and `AGENT_READINESS_PLAN.md` before assuming, and never invent one.

- `sources` — inventory and fingerprint configured files (path, size, mtime, SHA-256, duplicate/wrapper detection)
- `extract` — Excel workbook -> verified SQLite, one database per workbook; `engine=excel` checks every column
  against Excel itself, `engine=direct` reads the file without Excel and checks against a second XML reader.
  Also records several-tables-per-sheet (`_regions`), grouped headers and every formula's sheet references
- `catalog` — stable table/column identities for a run, plus formula lineage (which sheet feeds which)
- `analyze` — column profiling, generic data-quality findings, candidate keys, row fingerprints
- `semantics` — column roles, units/currency, table grain, time coverage, auto-drafted definitions
- `repair` — opt-in, reversible cleanup suggestions (never applied in place; see `query repaired`)
- `relations` — conservative inferred relationships between tables (inclusion + name similarity)
- `rules` — deterministic business rules and KPIs from human-authored packs
- `changes` — run-to-run diff: schema, volume, values, categories, distributions, KPIs, row multisets
- `pack` — the compact, token-budgeted AI context pack (`ai/context_pack.md` / `.json`)
- `query` — read-only, capped tools: `schema describe sample repaired aggregate meta region find digest compare trace sql` (`sql "*"` joins across files)
- `digest` — pre-computed key numbers per data table (totals, biggest groups, monthly trend), each with its SQL
- `refresh` — runs every stage above into a new run, promotes it only if it earned it
- `watch` / `diagnose` — live progress view and post-run failure diagnosis
- `status` / `report` / `doctor` / `audit` / `brief` / `agent-brief` — health, freshness and orientation

## Rules (all agents)

1. Never read a source workbook any other way. Sources may be DRM-wrapped or password-protected; only `extract`
   handles that, and it fails loudly and cleanly rather than guessing.
2. Never print or load a whole table. Ask for counts, aggregates, or a small capped slice (`query sample/aggregate`).
3. Every answer distinguishes four trust levels: **extracted** (Excel said so literally), **detected/inferred**
   (generic logic found it, with a method and a score), **confirmed** (a human said so in a pack), **calculated**
   (a deterministic rule/KPI). Never present an inferred fact as confirmed.
4. Cite evidence: run id, source, sheet/table, `_xl_row`, rule id — whatever the tool's `evidence` field gives you.
   A number with no evidence is not a finished answer.
5. If something the workbook contains could not be read (Power Query, Data Model, external links, a totals row
   mixed into data, an unclear table grain) the platform states that explicitly — read it, and repeat the
   limitation rather than silently treating the data as complete. See `query meta quality` and the pack's
   `blind_spots`/`omitted` sections.
6. When a definition, unit, or relationship is missing, the correct answer is "not confirmed yet", never an
   invented interpretation.
7. This runs on any OS the query/analysis tools support (pure Python + SQLite); only `extract` itself needs
   Windows + Excel.

## Minimum-token habit

Read in tiers, and stop at the first tier that answers the question:

0. `python -m xl2ai brief` — one call: run identity, freshness, per-table readiness, known gaps, next commands.
1. `ai/context_pack.md` — orientation: sources, tables, relationships, definitions, KPIs, changes, warnings.
2. `query describe <table>` — one table's columns, types, stats, samples, quality.
3. `query sample / aggregate / compare / trace` — small, targeted evidence, capped by rows/bytes/time.
4. `query sql` — anything else, still capped and read-only.

See `skills/query-playbook` for a question -> tool map, and `AI_USAGE.md` for the full discipline.

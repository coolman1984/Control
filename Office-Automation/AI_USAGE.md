# AI_USAGE

## The rule

Do not give the model the raw workbook or whole SQLite tables unless a human explicitly chooses to do that outside xl2ai.

Use the smallest evidence tier that answers the question.

## Tier -1: is this run safe to use at all

```powershell
xl2ai brief
```

Run this before reading anything else. It returns a per-table readiness verdict (`ready` / `needs_review` /
`not_ready`), an explicit `gaps` list, and ordered next commands. A `not_ready` table (failed verification) must
not be used. A `needs_review` table (quality errors, or a blind spot such as Power Query / Data Model / an
external link / a stale-calculation flag / an unread chart) can be used, but the gap must be named in the answer.

## Tier 0: orientation

Read:

```
data/runs/<run_id>/ai/context_pack.md
```

It contains the run identity, sources, tables, relationships, confirmed definitions, KPIs, changes and important data-quality warnings, within the configured token budget.

## Tier 1: understand one table

```powershell
xl2ai query describe <table-id>
```

Use it to inspect column names/types, nulls, distinct counts, ranges, top values, samples and quality context.

## Tier 2: ask for small evidence

```powershell
xl2ai query sample <table-id>
xl2ai query aggregate <table-id> amount --op sum --group-by department
xl2ai query compare --kind row
xl2ai query meta relationships
xl2ai query meta quality
xl2ai query meta definitions
xl2ai query meta unsupported
xl2ai query meta table_kind
xl2ai query meta row_flags
xl2ai query meta column_roles
xl2ai query meta grain
xl2ai query meta time_coverage
xl2ai query meta duplicates
xl2ai query meta repairs
xl2ai query repaired <table-id>
xl2ai query trace <table-id> 25
```

Every response is bounded by configured row/byte limits.

## Tier 3: read-only SQL

```powershell
xl2ai query sql <source-id> "SELECT ..."
```

Only a single SELECT/WITH statement is accepted. SQLite is opened read-only, `query_only` is enabled, an authorizer rejects writes/schema/attach/pragma operations, and a timeout is enforced.

## Evidence discipline

A good AI answer should distinguish:

- confirmed business definitions,
- inferred keys/relationships,
- deterministic KPI/rule results,
- raw sampled evidence.

When a definition is missing, the correct answer is “definition not confirmed yet”, not an invented interpretation.

When a table has a blind spot (`query meta unsupported`, or `brief`'s `gaps`), mention it in the answer if the
question touches that table's numbers -- a workbook whose real logic lives in Power Query or the Data Model can
have extracted values that no longer match the source. Silence about a known blind spot is treated the same as
not knowing about it.

Before summing or averaging a column, check `query meta row_flags` for `totals_candidate` rows in that table and
exclude them by `_xl_row` -- a totals/subtotal row left inside an aggregate silently doubles the answer.

Before aggregating a table at all, check `query meta grain`. If its `status` is `unknown`, say so and ask what one
row represents rather than assuming -- `SUM`/`COUNT` over a table with an unclear grain can double- or
under-count depending on a fact nobody has confirmed yet. Column roles (`query meta column_roles`) are `inferred`
guesses about what a column *means* (money, a category, an identifier, ...); treat them the same as any other
inferred fact -- useful for orientation, not a substitute for a pack's confirmed definition.

`query meta repairs`/`query repaired` show cleanup *suggestions* (null markers, inconsistent spelling, text
numbers), on by request only (`[repair].enabled=true`) and never applied to the data itself. If a question
depends on whether a value like "N/A" counts as missing, check `query meta repairs` rather than assuming either way.

## Token discipline

Increase `[ai].context_tokens` only when the context pack itself genuinely needs more schema/meaning. For detailed data questions, keep the pack small and query targeted slices instead.

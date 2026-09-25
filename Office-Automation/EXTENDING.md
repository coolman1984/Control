# EXTENDING

## New workbook/project

Prefer configuration and packs before changing platform code.

1. Run `xl2ai init ... --with-pack`.
2. Run `xl2ai doctor`.
3. Run the first `xl2ai refresh`.
4. Inspect `xl2ai report` and `xl2ai query schema`.
5. Confirm important keys/relationships and business terms in the pack.
6. Add rules/KPIs only after their definitions are agreed.
7. Refresh again and inspect changes.

## Add a generic platform capability

A feature belongs in `xl2ai/` only when it is workbook/domain independent.

Requirements:

- deterministic behavior where possible,
- provenance/evidence for derived outputs,
- explicit inferred vs confirmed status,
- bounded AI-facing output,
- a unit test and a failing/edge case,
- no project/company-specific vocabulary inside the platform (check new word lists against
  `tests/denylist_specimen.txt` -- see `xl2ai/semantics.py`'s `IDENTIFIER_WORDS`/`MONEY_WORDS`/etc. for the pattern).

## Add a new detector/classifier (column role, table kind, grain, unit, ...)

Follow the pattern in `xl2ai/semantics.py` and `xl2ai/analyze.py`'s `_classify_table_kind`:

1. Write it as a pure function over already-computed signals (type, uniqueness, name tokens, profile stats) --
   never a fresh Excel read, never a model call. The same inputs must always give the same answer.
2. Return `(label, confidence, method, reasons)`, even when confidence is 0 and the label is `unknown`. A
   detector that cannot decide must say so, not guess.
3. Store the result as `inferred`, never `confirmed`. Only a pack (see `BUSINESS_RULES.md`) can promote a specific
   instance to `confirmed`, and only a human decision does that.
4. Unit-test the pure function directly (no catalog, no run, no Excel) for the cases it should get right and the
   cases where it should honestly say `unknown`/low confidence -- see `tests/test_semantics.py`'s
   `TestColumnRoleClassification` for the shape.
5. Surface it: a `query meta <section>` in `xl2ai/query.py`, and consider whether `xl2ai brief` or
   `ai/agent_brief.md` should mention it when it represents a genuine gap or risk (see how `grain_unknown` and
   `totals_row_in_data` are wired into `xl2ai/brief.py`).

## Add a new command

Register it in `xl2ai/cli.py`, keep imports lazy, add command help and tests, then include it in the CI regression set if it has a dedicated test module.

## Add a new catalog field/table

Prefer additive schema changes. Update:

- `xl2ai/catalog.py`,
- `DATA_CONTRACT.md`,
- context/query/report consumers where relevant,
- tests that prove both writing and reading the new contract.

Per-run catalogs are rebuilt, so migrations are mainly needed only when older runs must be read by a newer command.

## Change extraction output

Extraction is the highest-risk boundary. Any intentional output change should be documented in `CHANGELOG.md` and validated against the real Excel/golden suite before release.

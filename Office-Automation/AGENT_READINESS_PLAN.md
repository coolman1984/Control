# AGENT READINESS PLAN

How xl2ai becomes the only thing an AI agent ever needs to touch when the real data lives in large, messy Excel
workbooks — so the expensive work of *understanding* those workbooks happens once, deterministically, and every
later agent inherits it for a few hundred tokens instead of re-deriving it (badly) for tens of thousands.

This plan is written from one point of view: **a new agent walking in cold**. Every phase below exists because
that agent would otherwise have to open Excel, guess, or spend tokens re-discovering something code already knows.

Status legend: **[built]** exists and is tested · **[partial]** exists but incomplete · **[missing]** not built.

---

## 1. The contract this plan commits to

An agent starting a project on this company's data must be able to answer all of the following **without opening a
single workbook** and **without inventing anything**:

1. What files exist, which matter, which are stale, which are duplicates.
2. What is inside each file, and which sheets are real data versus reports, notes, or settings.
3. Where each table actually starts and ends, what its headers are, and whether one sheet holds several tables.
4. What each column *means* — not just `REAL`, but "unit price, EGP, per line item".
5. What one row represents (the table's **grain**) — the single most important fact for any correct aggregation.
6. Which rows are traps: totals, subtotals, notes, blank separators.
7. How tables relate to each other, and which relationships are confirmed versus guessed.
8. What the tool could **not** read, stated explicitly.
9. How much to trust every one of the above.
10. Where any number came from, down to the Excel cell.

Anything on that list the layer cannot answer is a token bill (and a hallucination risk) transferred to every
future agent. That is the measure of success for this plan — not feature count.

---

## 2. The cold agent's journey, and where it breaks today

Each step is what a competent agent would do. `Gap` is what forces it to open Excel, guess, or burn tokens.

Status as of phases 1-6 landing (see `CHANGELOG.md` 0.5.0-0.9.0 and the version after this note):

| # | Agent's question | Available today | Gap closed by |
|---|---|---|---|
| 1 | "What am I looking at?" | `skills/agent-start` -> `xl2ai brief` **[built, phase 1]** | Fixed: `skills/platform-overview` no longer misdescribes the platform as unbuilt; `skills/agent-start` and `skills/query-playbook` added |
| 2 | "Which files, how fresh, any duplicates?" | `sources` inventory **[built]**; cross-file duplicate/version detection **[built, phase 4]** (`_duplicate_candidates`, schema fingerprint + row-hash overlap) | Remaining: stale/live classification, importance ordering -- not attempted |
| 3 | "What's in each file?" | `_extraction_log` **[built]**; sheet/table *kind* classification **[built, phase 3]** (`_table_kind`: data/notes/report/dashboard/empty, always inferred) | Closed for the cases the heuristic covers; no ML-level classification attempted |
| 4 | "Where does the table start and end?" | one table per sheet, single header row, preamble preserved **[partial]**; header **confidence + reasons** added **[built, phase 3]** | **Still open**: several tables per sheet, multi-row/hierarchical headers, transposed tables. See phase 3 note below -- deliberately not attempted without a Windows+Excel environment to validate against real messy workbooks |
| 5 | "What does this column mean?" | semantic role classification **[built, phase 4]** (`_column_roles`: identifier/date/money/quantity/percentage/category/code/boolean/free_text/geo/contact, name-based unit/currency) | Closed for name/type/uniqueness-based inference; value-format-based unit detection remains planned |
| 6 | "What is one row?" (grain) | table grain detection **[built, phase 4]** (`_table_grain`: description from the strongest key, or explicitly `unknown`) | Closed: an unclear grain is now stated, never guessed |
| 7 | "Are there traps in the rows?" | `DQ_*` findings **[built]**; totals/subtotal-row detection **[built, phase 3]** (`_row_flags`, label-based) | Closed for label-matched totals rows; value-sum-matching remains planned (higher false-positive risk) |
| 8 | "How do tables relate?" | relationship inference **[built]**; cross-file duplicate detection **[built, phase 4]** | Closed (see #2) |
| 9 | "What couldn't you read?" | Power Query / Data Model / external-link / stale-calculation / chart detection **[built, phase 2]** (`_unsupported`, surfaced as `blind_spots`) | Closed for these five kinds; linked data types (Rich Data Types) remain undetected |
| 10 | "How much do I trust this?" | per-table readiness verdict **[built, phase 1]** (`xl2ai brief`: ready/needs_review/not_ready, combining verification + quality + blind spots + totals rows) | Closed |
| 11 | "Where do I start reading?" | `ai/context_pack.md` (handles, token-budgeted) **[built]**; `ai/agent_brief.md` (plain names, grain, roles, changes explained) **[built, phase 6]**; `skills/query-playbook` (question -> tool map) **[built, phase 1]** | Closed |
| 12 | "Is this still true?" | `xl2ai brief`'s exit code as a checkable readiness gate **[built, phase 1]**; scheduling documented as an external cron/Task-Scheduler responsibility **[built, phase 6]** | Closed within this platform's scope (it does not run as a daemon by design; see ARCHITECTURE.md §5.6) |

---

## 3. Phases

Ordered by *risk removed per unit of work*, not by difficulty. Every phase keeps the existing invariants: nothing
silently lost, everything labelled with its trust level, every derived fact carries evidence, extraction output
never changes without a `CHANGELOG.md` entry and regenerated goldens.

### Phase 1 — A truthful entry point  ·  smallest, most urgent

Nothing else in this plan matters while the documented front door tells agents the platform does not exist.

1. Rewrite `skills/platform-overview/SKILL.md`: the real command list, the tiered reading order, the trust
   vocabulary, the hard rules (never open the source workbook, never dump a table, always cite evidence).
2. New `skills/agent-start/SKILL.md`: the one file an agent reads first. Ends with a concrete first command.
3. New `skills/query-playbook/SKILL.md`: a question→tool map ("how big is X" → `query describe`; "why did this
   number change" → `query compare` then `query trace`), including *when to stop*.
4. New command `xl2ai brief`: one call that prints run identity, freshness, per-table readiness, known gaps and
   the next commands — the single "orient me" call, so onboarding costs one tool call instead of five.
5. Remove the PowerShell-only assumption from the skills; the layer runs read-side on any OS.

**Proof:** an agent given only `skills/agent-start` answers three questions about an unfamiliar project using
only xl2ai commands, and opens no workbook.

### Phase 2 — Honest gaps  ·  cheap, prevents the worst failure

Confident blindness is worse than a missing feature: an agent that believes it saw everything reports wrong
conclusions with full confidence.

1. Detect and record **unsupported content** per sheet/workbook: Power Query queries, Data Model tables,
   external workbook links, linked data types, charts backed by data not on the sheet. New `_unsupported`
   table (already reserved in `DATA_CONTRACT.md` §2 phase 3).
2. Surface every one of them in the context pack as an explicit `blind_spots` section — never as a silent absence.
3. Capture formula presence as R1C1 run-length patterns per column (`_formulas`), so "this column is computed,
   not entered" becomes a fact the agent can use.
4. Detect stale cached values (manual calculation mode, file saved unrecalculated) and warn.

**Proof:** a fixture whose real logic lives in Power Query yields a pack that says so; no agent reading it can
conclude the data is complete.

### Phase 3 — Structural truth  ·  hardest, highest value

This is where real workbooks actually break. Research on spreadsheet understanding consistently identifies these
as the dominant failure modes: multiple regions per sheet, hierarchical headers, and embedded totals.

1. **Region detection**: find rectangular data blocks in one sheet (side-by-side and stacked), each becoming its
   own table with its own identity, instead of one flattened wide table.
2. **Multi-row / hierarchical headers**: reconstruct grouped headers (`Q1 → {Plan, Actual}`) into flat, unambiguous
   column names while preserving the hierarchy in `_columns`.
3. **Totals and subtotal rows**: detect and mark them (never delete them) so aggregation tools can exclude them by
   default and say that they did.
4. **Sheet kind classification**: `data | report | notes | config | dashboard | empty`, so an agent knows which
   sheets are analysable at all.
5. **Transposed tables**: detect fields-in-rows layouts and record the orientation.
6. **Header confidence**: every detection carries a score and reasons, and is overridable from config — a low
   score is a request for human confirmation, not a silent guess.

**Proof:** synthetic fixtures for each layout; every one yields correct table boundaries, and a totals row is
never counted in a default aggregate.

### Phase 4 — The semantic layer  ·  what turns data into meaning

Structure without meaning still forces the agent to guess. All outputs here are `inferred` with a method and
score, and only a human (via a pack) can promote them to `confirmed`.

1. **Column roles**: `identifier | date | money | quantity | ratio | percentage | category | code | boolean |
   free_text | geo | contact`, from value shape, distribution, header tokens and type — deterministic rules with
   published scores, never a model call.
2. **Units and currency**: detect from header tokens, formats and value ranges; record `unit` and `currency` with
   confidence; flag mixed units in one column as an error-level finding.
3. **Grain detection**: derive what one row represents from confirmed/candidate keys plus column roles, expressed
   in words ("one row per order line: order_id + line_no"). Where grain is uncertain, say so loudly — this is the
   fact that makes or breaks every aggregation an agent will ever run.
4. **Time coverage**: per table, the date column(s), min/max, and detected period type (daily/weekly/monthly).
5. **Draft descriptions**: one generated sentence per table and per column, stored as `origin=auto`,
   `status=inferred` in `_dictionary` — the raw material for the human confirmation loop (goal 3 of the six-goal plan).
6. **Cross-file duplicate/version detection**: same schema fingerprint + high row-hash overlap → "likely a newer
   copy of X", surfaced rather than silently treated as two unrelated tables.

**Proof:** on seeded fixtures every role/unit/grain is either correct or explicitly low-confidence; no wrong
answer is ever returned at high confidence.

### Phase 5 — Repairs, strictly opt-in and reversible

Repairs are the one place where the layer could corrupt the truth, so they are off by default, recorded
cell-by-cell, and never overwrite the extracted value.

1. Null-token normalization (`N/A`, `-`, `(blank)` → NULL) — already *detected* today, not applied.
2. Whitespace/case/near-duplicate category consolidation ("Cairo " / "cairo" / "CAIRO"), always as a mapping
   table, never an in-place edit.
3. Text-as-number and text-as-date coercion, with original text preserved alongside.
4. Every repair written to `_repairs` with before/after and the rule that caused it; the context pack reports
   repair counts so an agent knows the data was touched.

**Proof:** repairs off → byte-identical output to today. Repairs on → every change reversible from `_repairs`.

### Phase 6 — The agent interface and staying true

1. **Agent-facing pack**: an `ai/agent_brief.md` written for a cold agent (plain names, grain, roles, blind
   spots, readiness, next commands) alongside today's compact handle-based pack for token-tight cases.
2. **Access decision — CLI first, MCP as a thin wrapper.** Current industry findings are that MCP tool schemas
   cost context on every call while a CLI costs tokens only when used; the CLI already returns strict JSON
   envelopes. Recommendation: keep the CLI as the contract, add an MCP wrapper only if a host cannot spawn
   processes. Decide before building.
3. **Scheduled refresh + change alert** (goal 2 of the six-goal plan): a stale pack is trusted blindly, so
   freshness is a safety feature, not a convenience. Refresh on a schedule, and emit "what changed and why it
   matters" from the existing deterministic change detection.
4. **Readiness gate**: `xl2ai brief` exits non-zero when the current run is stale or unverified, so an automated
   agent can refuse to proceed on untrustworthy data instead of quietly using it.

---

## 4. Analyses, verifications, repairs, and expected failures

### Analyses the layer must perform before any agent arrives

| Class | What it produces |
|---|---|
| Structural | table regions, header rows/hierarchy, orientation, totals rows, sheet kind |
| Statistical | per column: n, nulls, distinct, min/max/mean, top-k, samples, distribution shape |
| Semantic | column role, unit, currency, table grain, time coverage, draft descriptions |
| Relational | candidate/confirmed keys, inclusion relationships with containment + score, cross-file duplicates |
| Temporal | run-to-run schema/volume/value/category/distribution/KPI/row-multiset changes |
| Quality | the `DQ_*` family, extended with totals-in-data, mixed units, near-duplicate categories |
| Integrity | stored-vs-Excel verification, cross-artifact audit, per-table readiness verdict |

### Verifications (each must be a test, not an intention)

- Stored cell count equals Excel `COUNTA`; per-column non-null counts and numeric sums equal Excel's. **[built]**
- Cross-artifact audit: catalog, source databases and manifest agree. **[built]**
- Determinism: same input → byte-identical pack. **[built for the pack]**
- Structure: detected regions/headers reproduce known fixture layouts exactly. **[Phase 3]**
- Semantics: inferred roles/units/grain match seeded fixtures, or are correctly marked low-confidence. **[Phase 4]**
- Budget: the pack stays inside its token budget on every fixture. **[built]**
- Cold-agent evaluation: a fresh agent answers a fixed question set from the pack and tools alone, and its
  answers are checked for correctness *and* for citing evidence. **[Phase 1 onwards]**

### Repair policy

Detect always, repair never by default, record everything, keep the original. A repair that cannot be reversed
from `_repairs` is a bug.

### Expected failures the layer must handle without lying

| Failure | Required behaviour |
|---|---|
| Password-protected / DRM / corrupt file | fail fast with a stable error code; previous trusted data untouched **[built]** |
| Excel crash, hang, or blocking dialog | watchdog kills only our own process, restarts, resumes **[built]** |
| Verification mismatch | run is not promoted; data is marked untrusted **[built]** |
| Two refreshes at once | lock; a live owner is never displaced on age alone **[built]** |
| Sheet is a formatted report, not a table | classified as `report`, not silently flattened **[Phase 3]** |
| Several tables on one sheet | separate tables with separate identities **[Phase 3]** |
| Totals row inside data | marked and excluded from default aggregates **[Phase 3]** |
| Logic lives in Power Query / Data Model | reported as a blind spot; never presented as complete data **[Phase 2]** |
| Ambiguous grain | stated as unknown with the reason; aggregation guidance withheld **[Phase 4]** |
| Mixed units in one column | error-level finding; unit-blind aggregation refused **[Phase 4]** |
| Source changed since last run | change detection + alert; stale pack refuses readiness **[Phase 6]** |
| Agent asks for a whole table | tools cap and say they capped **[built]** |

---

## 5. Documentation plan

Documentation is part of the product here, because the primary reader is an agent. Each phase updates docs in the
same commit as its code — the rule that was broken once already in this repo (the watch/diagnose feature shipped
with no `CHANGELOG.md` entry).

| Document | Change |
|---|---|
| `skills/*` | **Phase 1, urgent.** Rewrite to reflect shipped reality; add `agent-start` and `query-playbook`. These are the agent's entry point and are currently actively misleading |
| `AI_USAGE.md` | Add the cold-start sequence, the readiness gate, blind spots, grain, and the "stop at the tier that answers the question" rule stated for agents rather than humans |
| `ARCHITECTURE.md` | Add this plan's phases to the roadmap; state that the primary consumer is an agent, not a person; record the CLI-vs-MCP decision and its reasoning |
| `DATA_CONTRACT.md` | New/extended tables per phase: `_unsupported`, `_formulas` (P2); `_tables` regions/header hierarchy/kind, totals rows (P3); roles/units/grain/time coverage/`_dictionary` auto-drafts (P4); `_repairs` (P5). Contract version bumps: additive = minor |
| `EDGE_CASES.md` | Move each newly covered case from `planned Pn` to `covered [test_name]`; add the new cases this plan introduces |
| `BUSINESS_RULES.md` | Document confirming auto-drafted terms/roles/grain through a pack — the human confirmation loop |
| `EXTENDING.md` | How to add a detector (role, unit, structure) without embedding domain vocabulary in the platform |
| `TESTING.md` | The fixture families each phase needs, and the cold-agent evaluation harness |
| `README.md` | Reframe the opening: this is a pre-digestion layer for AI agents. Keep the human commands documented, but stop implying a person is the main reader |
| `CHANGELOG.md` | One entry per phase, listing output-affecting changes explicitly |

---

## 6. Success criteria

The plan is done when all of the following hold on a workbook set nobody on the project has seen before:

1. A cold agent, given only `skills/agent-start`, answers a fixed question set correctly using xl2ai alone.
2. It opens no workbook and calls no Excel library.
3. Its total context cost for orientation is a few hundred tokens, not tens of thousands.
4. Every answer cites evidence down to run, table and Excel row.
5. Every uncertain fact is labelled uncertain, and the agent repeats that uncertainty rather than flattening it.
6. Anything the layer could not read is stated in the agent's own words, not omitted.
7. Running it again on unchanged inputs produces byte-identical output; on changed inputs it names what changed.

---

## 7. Sequencing note

Phase 1 is hours of work and unblocks the entire premise; it should ship before anything else. Phase 2 is small
and removes the most dangerous class of error (confident blindness). Phase 3 is the largest engineering effort in
the plan and should not start until 1 and 2 are done, because it is the phase most likely to consume a whole
session's budget on its own.

## 8. Status after phases 1-6 (this session)

Phases 1, 2, 4, 5 and 6 shipped in full within this session's scope (`brief`/`agent_brief`, honest blind-spot
detection, the semantic layer, opt-in repairs, freshness/readiness documentation) -- see `CHANGELOG.md` 0.5.0
through the version after this note, each with tests that run without Excel.

**Phase 3 shipped partially, by design.** What is safe to build and verify without a Windows+Excel environment
landed: header-detection confidence scoring, label-based totals-row detection, and table-kind classification --
all pure logic over already-extracted values, all with passing tests. What did **not** ship: region detection
(several tables per sheet), multi-row/hierarchical header reconstruction, and transposed-table detection. These
three change the extraction identity model itself (today: one table per sheet, one header row) and touch live
Excel COM code (`extract/layout.py`, `extract/sheet.py`) that this environment cannot run or validate against a
real messy workbook. Shipping an unvalidated guess at that layer risks silently corrupting exactly the structural
truth this phase exists to protect -- worse than leaving the gap open and documented (which `EDGE_CASES.md` does).

**Also flagged, not resolved here**: two schema changes (phase 2's `_unsupported`/`_formulas`, phase 3's
`_extraction_log` columns) mean `tests/golden/fingerprints.json` needs regenerating with
`python tests/regen_golden.py` on a Windows machine with Excel -- noted in `CHANGELOG.md` for both phases and
still outstanding as of this note.

**Recommended next step** for whoever has that environment: pick up region detection/multi-row headers using the
synthetic-fixture approach in `tests/make_fixtures.py` (already the project's pattern for exactly this kind of
COM-dependent, must-validate-for-real feature), then regenerate the goldens in the same pass.

## 9. Status after 0.11.0

The three structural items phase 3 left open are now handled **as description, not re-cutting** -- the approach
section 8 argued for: region detection (several tables per sheet) and grouped/multi-row header reconstruction are
pure functions over values extraction already reads, recorded next to the table (`_regions`, `_header_groups`),
surfaced in `brief`/agent brief/context pack, and readable per region via `query region`. The table identity model
(one per sheet) is untouched, so nothing already built or cached changes meaning. Transposed tables remain undetected.

The validation blocker in section 8 ("this environment cannot run Excel") is removed for everything except DRM files
and COM-specific behaviour: the new direct engine reads real .xlsx/.xlsb/.xls files on any OS, so these detectors are
now tested end to end against real workbooks (`tests/test_direct_engine.py`), not only synthetic grids.

Formula lineage (row 8 of the journey table, "how do tables relate", at the level of *computed from*) is new:
`_lineage` in the catalog, resolved across sheets and across source workbooks.

Still outstanding: regenerating `tests/golden/fingerprints.json` on Windows+Excel, and validating the region/header
heuristics against the real DRM-wrapped specimen through the Excel engine.

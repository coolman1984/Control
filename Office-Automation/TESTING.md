# TESTING

All commands are PowerShell. Excel must be installed for integration and golden tests (they use real COM).

```powershell
python -m unittest discover -s tests                 # everything (about 2-7 min; fixtures are built by real Excel once per run)
$env:XL2SQL_PERF = "1"; python -m unittest discover -s tests   # + 200k x 12 timing test
python -m unittest tests.test_structure              # fast, no Excel
python -m unittest tests.test_platform_e2e            # whole post-extraction chain, no Excel
python -m unittest tests.test_golden                 # byte-level regression only
```

| Module | What it proves |
|---|---|
| `test_excel_to_sqlite.py` unit classes | dates (1900/1904/phantom leap day), typing, naming, header detection, block iteration |
| `test_excel_to_sqlite.py::TestIntegration` | ~30 edge cases on generated workbooks, crash recovery, password/corrupt files, verification catches truncation, no leaked Excel (exact PIDs) |
| `test_golden.py` | output is byte-identical to `tests/golden/fingerprints.json` (2 synthetic + 2 specimen databases) |
| `test_structure.py` | every global name resolves; platform has no specimen words; platform never imports packs; entry points work |
| `test_incremental_refresh.py` | unchanged-source reuse, invalidation on data/extract-setting change, self-contained reused DBs |
| `test_catalog.py` + `test_analysis_relations.py` | catalog/profile/quality/key/relation contracts |
| `test_rules_changes.py` | packs, KPIs, confirmations and run-to-run changes |
| `test_context_query.py` | context budget, query caps, read-only SQL and evidence |
| `test_platform_e2e.py` | cross-stage compatibility from extraction DB through report/context/query |
| `test_brief.py` | `xl2ai brief`'s readiness verdict, gaps and exit codes (no Excel) |
| `test_blind_spots.py` | Power Query/Data Model/external-link/stale-calc/chart detection surfaced through catalog, context pack and `brief` (no Excel) |
| `test_structural_truth.py` | header-confidence scoring (pure function), totals-row detection, table-kind classification (no Excel) |
| `test_semantics.py` | column-role/unit classification (pure functions), table grain, time coverage, auto-drafted definitions, cross-file duplicate detection (no Excel) |
| `test_repair.py` | opt-in repair suggestions never touch the extracted table; `query repaired` applies them only in the response (no Excel) |
| `test_agent_brief.py` | `ai/agent_brief.md` combines readiness, changes-explained, roles and grain by plain name (no Excel) |

Fixtures: `tests/make_fixtures.py` builds them with Excel (`fixture_cache.py` shares one build per test run).
Case list and coverage status: `EDGE_CASES.md`.

## Rules

1. Regression gate on every phase: the whole suite passes and the goldens are unchanged, or a deliberate change is
   listed in `CHANGELOG.md` and `python tests/regen_golden.py` is run in the same commit.
2. A new capability needs a synthetic fixture for the case it handles, **and** a failing-case test (the check must be
   shown able to fail, e.g. `test_verification_catches_a_truncated_extent`).
3. The specimen files (`Price Comparison Data/`) are a stress test, never the spec; specimen tests skip when absent.
4. Tests must not depend on other Excel activity on the machine: assert on the PIDs the tool reports, not on process counts.
5. `tests/denylist_specimen.txt` lists words the generic platform may never contain.

## Known test caveats

* Excel on this machine wraps every saved file in DRM; saving/launching is slower and timings vary with machine load.
* Performance numbers are recorded, not asserted tightly (the 200k-row test only asserts < 120 s).
* **Outstanding as of the agent-readiness phases (`AGENT_READINESS_PLAN.md`)**: `_extraction_log` gained columns
  (`header_confidence`, `header_reasons`) and the extraction database gained tables (`_unsupported`, `_formulas`)
  in an environment without Windows/Excel/COM available, so `test_golden.py` could not be run there (it is
  skipped whenever `pywin32` is unavailable) and the goldens were not regenerated. Run
  `python tests/regen_golden.py` on a Windows machine with Excel before treating those two changes as fully
  validated; `test_golden.py` will fail on its next real run until that happens.

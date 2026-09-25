# EDGE CASES (test matrix)

Status: **covered** = a passing test exists today (test name in brackets) | **partial** | **planned Pn** = phase n.
Fixtures: synthetic ones are built by real Excel in `tests/make_fixtures.py`; "specimen" = the Price Comparison files
(used as a hard real-world check, never as the spec). Anything marked planned must get a synthetic fixture.

## A. File and environment

| Case | Status |
|---|---|
| DRM-wrapped workbook (opened only through Excel's DRM agent) | covered (all integration tests + specimen) |
| .xlsx / .xlsb | covered (synthetic xlsx, specimen xlsb) |
| .xlsm (macros must not run) | planned P1 (AutomationSecurity is forced off; needs a fixture with an auto-open macro) |
| legacy .xls, .xlt*, .xla | planned P1 (Excel engine); the direct engine reads .xls/.xlsb values but not their formulas/errors, and says so as a `reader_limit` blind spot |
| password-protected file: fail fast with clear message, no leak | covered [test_password_protected_file_fails_cleanly] |
| corrupt / not-Excel file, previous DB kept | covered [test_corrupt_file_fails_cleanly, test_failed_run_keeps_previous_database] |
| file locked / open by another user | planned P1 |
| Excel autosave/recovery dialog, other blocking dialogs | partial (dialog watchdog; only the password dialog is tested) |
| Excel crash mid-run: restart and resume | covered [test_excel_crash_is_recovered] |
| Excel hang (no dialog): watchdog kills only our PID | partial (logic tested by hook; no true hang fixture) P1 |
| no Excel installed / Excel cannot start | planned P1 |
| ~$ temp files, duplicate stems in different folders, long paths, unicode paths, network paths | partial (~$ skipped) ; planned P1 |
| two refreshes at once | planned P1 |
| disk full / DB path not writable | planned P1 |
| partial run never replaces last valid data | covered for the extractor DB; run-level planned P1 |

## B. Sheet and workbook structure

| Case | Status |
|---|---|
| empty sheet, chart sheet | covered [test_sheet_statuses] |
| hidden and very hidden sheets | covered [test_sheet_statuses] |
| phantom UsedRange (formatting a million rows down) | covered [test_phantom_used_range] + specimen |
| filtered rows (sheet AutoFilter, table filter): all rows extracted | covered [test_filtered_rows_are_all_extracted] |
| truncation detected by independent check | covered [test_verification_catches_a_truncated_extent] |
| manually hidden / grouped rows and columns | partial (hidden rows found by Find; no dedicated test) P2 |
| merged cells in header and in data | covered [test_merged_cells] |
| awkward sheet names (apostrophe, dots, digits, `sqlite_`, collisions, 31 chars) | covered [test_awkward_sheet_names] |
| sheet protection, workbook structure protection | planned P1 |
| very wide sheet (16,384 columns) | planned P2 |
| very tall sheet (1,048,576 rows), multi-million-cell scale | partial (5.8M cells, 200k x 12) ; planned P2 benchmark |
| workbook with hundreds of sheets | planned P2 |
| Excel Tables (ListObjects) as the unit of data, structured refs | planned P2 |
| named ranges (thousands, broken `#REF!`) | planned P3 |
| pivot tables: rendered output | covered (specimen) ; pivot definitions/sources planned P3 |
| Power Query, Data Model, external links | covered (`_unsupported`, workbook scope: `power_query`/`data_model`/`external_link`) |
| linked data types (Rich Data Types, e.g. Stocks/Geography) | planned P3 -- not yet detected; a cell's linked-data metadata is invisible, only its displayed value is extracted |
| charts, images, shapes, comments/notes, data validation | planned P3 (report presence only) |

## C. Layout and headers

| Case | Status |
|---|---|
| header on row 1 / below title rows / preamble kept | covered [test_preamble_blank_rows_and_totals] |
| no header (numeric-only data) | covered [test_headerless_sheet] + specimen |
| duplicate, blank, whitespace, numeric, error-valued headers | covered [test_duplicate_and_blank_headers, TestNaming] |
| two-level (grouped) headers, e.g. group label above each column pair | covered [test_structure_lineage.py:TestHeaderGroups, test_direct_engine.py]: up to 3 group levels from merged areas or label runs, recorded per column in `_header_groups` (the upper rows also stay in the preamble); column names are not rewritten |
| several tables on one sheet (side by side / stacked) | covered as *description* [test_structure_lineage.py:TestFindRegions, test_direct_engine.py]: regions recorded in `_regions`, the table is marked `needs_review`, and `xl2ai query region <table> <n>` reads one region back out under its own header. The stored table keeps its identity (one per sheet) -- re-cutting the extraction itself remains deliberately not done |
| blank rows inside data, totals / subtotal rows, trailing notes | covered [test_structural_truth.py]: label-based totals/subtotal row detection (`_row_flags`, `DQ_TOTALS_ROW_IN_DATA`); value-sum-matching detection remains planned P4 (higher false-positive risk) |
| header detection confidence + config override | covered (score + reasons) [test_structural_truth.py:TestHeaderConfidence]; config override remains planned |
| transposed tables (fields in rows) | planned P2 -- same reasoning as multi-table sheets: a wrong reconstruction is worse than an honest "not detected" |
| tables that do not start at A1 | covered (specimen, `Phantom`, preamble) |
| sheet/table kind (data vs. report vs. notes vs. dashboard) | covered [test_structural_truth.py:TestTotalsAndKind], `inferred` only, from row/column count, header presence, formula density, pivots and totals rows |

## D. Values and types

| Case | Status |
|---|---|
| ints, reals (bit-exact doubles), negatives, 1e-300 / 1e300 | covered [test_integers_and_reals] |
| integers beyond 2^53 kept as REAL, not truncated | covered [test_big_numbers_...] |
| text untouched: padding, newlines, quotes, leading zeros, "1E5" | covered [test_text_is_untouched] |
| unicode: CJK, emoji, RTL, accents; 32k-char cells | covered [test_text_is_untouched] |
| booleans, mixed columns, empty columns, empty-string vs NULL | covered [test_bool_mixed_empty] |
| Excel error cells (#DIV/0!, #N/A, #NUM!, #NAME?) | covered [test_error_cells] |
| dates: 1900 system, phantom 1900-02-29, 1904 system, times, elapsed time | covered [test_dates_times, test_1904_workbook] |
| mixed date formats within a column | partial (falls back to .Value mask; untested) P4 |
| text that looks like a number with thousands separators ("1,234") | covered, opt-in, suggested only [test_repair.py:test_text_as_number_suggested] -- `[repair].enabled=true`, never applied in place |
| text that looks like a date ("12/03/2025") | planned P4 (date-format ambiguity -- DD/MM vs MM/DD -- needs locale or explicit config, not a safe default guess) |
| null-marker text ("N/A", "-", "(blank)") -> NULL | covered, opt-in, suggested only [test_repair.py:test_null_token_suggested_when_enabled] |
| currency/percent/scientific formats, custom formats | partial (typed by value; format not kept) P4 |
| lone surrogates / control characters | partial (unit-tested repair) |
| STRICT tables | covered [test_strict_mode] |

## E. Formulas

| Case | Status |
|---|---|
| formulas extracted as values, formula count recorded | covered [test_formulas_are_values] |
| formula presence per column (has_formula + one sample R1C1) | covered (`_formulas`) |
| full formula text / R1C1 run-length patterns / dependencies | planned P3 |
| stale cached values (calc mode manual, file saved unrecalculated) | covered [`_unsupported` kind `stale_calculation`, checked via `Application.CalculationState` right after open] |
| array formulas, dynamic arrays (#SPILL!), volatile functions, circular refs | planned P3 |
| formulas referencing other sheets/workbooks | planned P3 |

## F. Multi-run, multi-file, drift

| Case | Status |
|---|---|
| new / removed / renamed sheets and columns between runs | planned P2 (ids), P6 (diff) |
| column type changes, new categories, new periods/versions | planned P6 |
| row-level changes without a key | planned P6 (row-hash multiset) |
| several unrelated files; several related files; same file different versions | covered [test_semantics.py:TestDuplicateDetection] for exact schema+content duplicates across files (`_duplicate_candidates`); relationship inference across files remains built separately (`relations.py`) |
| unchanged source between refreshes: do not reopen Excel; reuse only trusted identical extraction | covered [test_incremental_refresh.py] |
| relationship discovery: true FK, false-positive small domains, composite keys | planned P5 |

## G. Data quality (seeded-defect fixtures; clean data must yield zero findings)

Nulls, duplicates (exact and by candidate key), near-duplicate spellings, out-of-range and outlier numbers,
impossible dates, mixed types, constant columns, high-cardinality IDs, orphan rows, unit mix. Status: planned P4.

Case/whitespace category variants ("Cairo" / "cairo" / " CAIRO "): covered as an opt-in repair suggestion, not a
finding [test_repair.py:test_category_consolidation_picks_most_frequent_spelling] -- canonical spelling chosen by
frequency, suggested via `_repairs`, never rewritten in place.

## H. Scale and performance benchmarks (recorded, regression-tracked)

| Benchmark | Status |
|---|---|
| 200k x 12 in ~10 s end to end | covered (`XL2SQL_PERF=1`) |
| 5.8M-cell sheet: read 3.8 s, whole workbook ~21 s | measured on specimen |
| 1M rows x 20 cols; 50M cells; 16k columns | planned P2 |
| memory ceiling, block-size auto-tuning | planned P2 |

## I. AI interfaces

Pack under token budget on every fixture; pack determinism (same input, same bytes); delta pack; query caps;
read-only enforcement; SQL injection / multi-statement / PRAGMA / ATTACH refused; tool errors are machine-readable;
cold-agent Q&A evaluation. Status: planned P6-P7.

## J. Semantic layer (column roles, grain, units, auto-drafted definitions)

| Case | Status |
|---|---|
| column role classification (identifier/date/money/quantity/percentage/category/code/boolean/free_text/geo/contact) | covered [test_semantics.py:TestColumnRoleClassification], always `inferred` with method + reasons |
| unit/currency detection | covered from column name only (EGP/USD/EUR/SAR + symbols, percent) [test_semantics.py:TestUnitCurrencyDetection]; from cell number formats remains planned P4 (needs format-string parsing this platform does not yet do reliably) |
| table grain detection | covered [test_semantics.py:test_grain_detected_from_unique_id_column]; explicitly `unknown` (not guessed) when no key reaches 90% uniqueness [test_grain_unknown_when_no_strong_key] |
| time coverage per table | covered [test_semantics.py:test_time_coverage_captured] |
| auto-drafted table/column definitions | covered [test_semantics.py:test_auto_definitions_drafted], `origin=auto`/`status=inferred`; promotion to `confirmed` only via a pack (see `BUSINESS_RULES.md`) |
| cross-file duplicate/version detection | covered for identical schema + row-hash overlap [test_semantics.py:TestDuplicateDetection]; fuzzy/partial-overlap detection remains planned |

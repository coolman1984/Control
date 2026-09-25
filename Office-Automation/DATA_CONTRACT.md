# DATA CONTRACT

Contracts between stages. **[built]** = implemented today (extraction). Everything else is the agreed target;
each stage adds tables/fields, never changes an earlier stage's meaning. `contract_version` is semver:
additive = minor, breaking = major (a major bump requires a migration note in `CHANGELOG.md`).

## 0. Conventions

* Identifiers: `source_id` (stable id of a source file, preferably from config alias), `run_id`
  (`YYYYMMDDTHHMMSS-<4hex>`), `table_id` (source id + normalized sheet identity), and `column_id`
  (table id + normalized unique SQL column name). Schema fingerprints and positions are deliberately separate from identity,
  so adding/reordering columns does not rewrite the identities of unchanged columns. In AI-facing output these are shortened
  to handles (`t3`, `t3.c7`) with a legend.
* Trust labels on every derived fact: `detected | config | inferred | confirmed`, plus `method` and `score` (0-1).
* **Evidence object** `{run_id, source_id, sheet, table, xl_row, xl_col, rule_id, pack}`; fields not applicable are omitted.
* Severity: `info | warn | error`. An `error` finding blocks promotion only if the project config says so.
* Never store secrets; paths are stored as given in config plus `sha256` fingerprint.
* Excel error cells are NULL in data tables and listed in `_cell_errors`. Dates are ISO-8601 text. Nothing is coerced silently.

## 1. Stage `sources` [phase 1]  -> `sources.json` in the run folder

`[{source_id, path, kind: xlsx|xlsm|xlsb|xls|other, size, mtime, sha256, wrapper: none|drm|encrypted, exists, locked}]`

## 2. Stage `extract` -> `<source>.db` per workbook  [built unless marked]

| Table | Contract |
|---|---|
| one data table per extracted table | first column `_xl_row` INTEGER (Excel row); typed columns; NULL = empty or error cell |
| `_meta` [built] | key/value: schema_version, source_path/size/modified, extracted_at, tool, engine (direct engine only; absent = Excel), excel_restarts, verify_checks, verify_mismatches, total_seconds |
| `_extraction_log` [built] | per sheet: status (`extracted|skipped|error`), message, header_row, first/last row/col, data_rows, columns, blank_rows_skipped, error_cells, formula_cells, pivot_tables, filter_active, merged_areas, merged_in_data, read/write/total seconds |
| `_columns` [built] | table_name, position, sql_name, original_header, xl_col, xl_col_letter, sql_type, kind, date_format, non_null, error_cells |
| `_cell_errors`, `_sheet_preamble`, `_merged_areas` [built] | as named |
| `_verification` [built] | table_name, column_name, check_name (`counta|sum|cells_total`), excel_value, sqlite_value, ok (1/0/NULL), note |
| `_tables` extras [built] | `header_confidence` (0-1), `header_reasons` (plain-language list) -- computed from the chosen header row's own values, no COM cost beyond what extraction already reads |
| `_regions` [built] | table_name, region_no, first_row, first_col, last_row, last_col, header_row, kind (`table\|note`), cells, complete (0 = only the first block of a huge sheet was scanned) -- written only when a sheet holds more than one table; one table per sheet remains the identity model |
| `_header_groups` [built] | table_name, xl_col, sql_name, path (JSON list of group labels, outermost first), method (`merged_area\|label_run`) |
| `_formula_refs` [built, direct engine on .xlsx/.xlsm] | table_name, sql_name, ref_workbook, ref_sheet, cells, sample -- every formula of the column read, not a sample |
| `_formulas` [built, per-column only] | table_name, sql_name, has_formula, sample_r1c1 (one sample per column; full run-length R1C1 patterns remain phase 3) |
| `_unsupported` [built] | scope (`workbook`\|`sheet`), sheet_name, kind (`power_query`\|`data_model`\|`external_link`\|`stale_calculation`\|`chart`\|`reader_limit`), count, detail |
| `_pivots`, `_names` [phase 3] | pivot definitions, defined names (pivot *output* and formula *values* are already extracted; their definitions are not) |

Invariants (checked by `_verification`): stored cells == Excel `COUNTA` of the whole sheet; per-column non-null and
numeric sum equal Excel's; a failed sheet is recorded, never dropped. Exit codes: 0 ok, 1 file failed, 2 some sheets failed, 3 verification mismatch.

### Document sources [built]

A document source's database has the workbook tables above (one data table per document table; `_xl_row` = row in
the source table, 1 = header) plus `_doc_meta(key, value, part)`, `_doc_blocks(id, part, kind, page, section, level,
text)`, `_doc_entities(block_id, kind, value, normalized, start, end)`, `_doc_tables(table_name, part, page, section,
caption, idx)`, `_doc_attachments(part, name, kind, size, status, note)`. Reader limits (scanned pages, protected
e-mail, unreadable attachments) are `_unsupported` rows of kind `reader_limit`. Saved agent records live outside runs
in `<data_dir>/extracted.db`: one table per `save_records` table plus `_evidence(table_name, record_id, field,
block_id, source_id, quote, run_id, saved_at)`.

## 3. Later stages -> `catalog.db` per run  [phases 4-7]

| Table | Key fields |
|---|---|
| `_profile_columns` | column_id, n, nulls, distinct, min, max, mean, stddev, top_k (value,count), sample (<=5), date_min/max, pattern |
| `_dq_findings` | id, code (`DQ_*`), severity, column_id/table_id, count, examples (<=3), message, evidence |
| `_keys` | table_id, columns[], uniqueness (0-1), status inferred/confirmed |
| `_relationships` | from_column, to_column, kind (`inclusion|name`), containment (0-1), status, method, score, evidence |
| `_dictionary` | term, meaning, aliases[], unit, applies_to (column_id patterns), status, origin (`pack|config|auto`) |
| `_rule_results` | rule_id, pack, pack_version, status (`pass|fail|error|skipped`), expected, actual, tolerance, evidence |
| `_kpi_results` | kpi_id, value, unit, dims, definition_ref, evidence |
| `_changes` | kind (`schema|volume|value|row|category|distribution|kpi|source|baseline`), severity, subject, before, after, evidence |
| `_unsupported` [built] | source_id, table_id (NULL for workbook-scope findings), scope, sheet_name, kind, count, detail |
| `_formulas` [built] | column_id, table_id, has_formula, sample_r1c1 |
| `_table_kind` [built] | table_id, kind (`data\|notes\|report\|dashboard\|empty`), confidence, method, reasons -- always `inferred` |
| `_row_flags` [built] | table_id, xl_row, flag (`totals_candidate`), detail -- label-matched, never value-sum-matched |
| `_column_roles` [built] | column_id, table_id, role (`identifier\|date\|money\|quantity\|percentage\|category\|code\|boolean\|free_text\|geo\|contact`), confidence, method, reasons, unit, currency |
| `_table_grain` [built] | table_id, columns_json, description, status (`inferred\|confirmed\|unknown`), confidence, method |
| `_time_coverage` [built] | table_id, column_id, min_value, max_value |
| `_duplicate_candidates` [built] | id, table_id_a, table_id_b, method, score, evidence -- cross-file, never same-workbook |
| `_regions`, `_header_groups`, `_formula_refs` [built] | the extract tables above keyed by table_id / column_id |
| `_lineage` [built] | id, table_id, column_id, ref_kind (`sheet\|workbook`), ref_workbook, ref_sheet, target_source_id, target_table_id, status (`resolved\|unresolved\|external`), cells, sample, method (`all_formulas\|formula_sample`) -- always `inferred`; own-sheet references are not lineage |
| `_digest` [built] | id, table_id, section (`total\|by_group\|by_month`), measure (`*` = row count), dim, key, value, rows, share, rank, sql -- every number carries its reproducing SQL; totals rows excluded |
| `_digest_tables` [built] | table_id, status (`computed\|skipped\|error`), reason, excluded_rows, measures_json, units_json, dims_json, date_column |
| `_anomalies` [built] | id, table_id, column_name, kind (`outlier\|negative\|trend_jump\|missing_period\|date_out_of_range`), severity, count, detail, examples (JSON `[[xl_row, value], ...]` or months), sql -- always `inferred`; totals rows excluded |
| `_reconciliation` [built] | id, report_table_id, report_column, label_column, source_table_id, source_column, dim_column, agg (`SUM\|COUNT`), compared, matched, status (`reconciled\|partial`), mismatches (JSON, <= 10, with both numbers and the report's Excel row), sql |
| `_documents` [built] | source_id, kind, title, author, created, modified, sent, sender, recipients, subject, pages, blocks, tables, attachments |
| `_blocks` / `_blocks_fts` [built] | block_id (`<source_id>#<n>`), source_id, part (attachment path, "" = the document), kind, page (page/slide), section (heading path), level, ord, text / folded full-text index |
| `_entities` [built] | source_id, block_id, kind (`code\|money\|date\|percent\|email\|phone`), value as written, normalized, start, end |
| `_table_origin` [built] | table_id, part, page, section, caption -- where a document table came from |
| `_mentions` [built] | normalized, kind, source_id, block_id, table_id, column_name, rows, first_row -- a document value found in a table |
| `_repairs` [built, opt-in] | id, table_id, column_id, xl_row, original_value, repaired_value, rule (`null_token\|category_consolidation\|text_as_number`) -- empty unless `[repair].enabled = true`; never written back to any data table |

## 4. Run manifest [phase 1] `manifest.json`

`{contract_version, run_id, project, started, finished, status: running|passed|failed|partial, promoted: bool,
inputs: [{source_id, sha256}], stages: [{name, status, seconds, artifacts[], warnings, error}]}`.
`data/current.json` = `{run_id}` of the last promoted run; written atomically after every required stage passes.

## 5. AI-facing contracts [phases 6-7]

Context pack JSON: `{contract_version, run_id, generated, budget_tokens, est_tokens, hash, sources[], schema_families[],
tables[], relationships[], definitions[], kpis[], rule_results, changes[], warnings[], omitted[{what, count, use_tool}]}`.
Markdown is rendered from the JSON, never authored separately.

Tool response envelope (all query tools):
`{ok, tool, columns[], rows[], row_count, total_rows|null, truncated, bytes, elapsed_ms, hint, evidence[], error?{code,message}}`.
Caps (config): rows 50, bytes 8192, timeout 5 s. `sql` accepts exactly one SELECT/WITH statement on a `mode=ro`
connection; anything else returns `error.code = "E_NOT_ALLOWED"`.

## 6. Error taxonomy (stable codes)

`E_SRC_MISSING E_SRC_LOCKED E_SRC_ENCRYPTED E_SRC_CORRUPT E_EXCEL_START E_EXCEL_DIED E_EXCEL_BLOCKED E_EXTENT
E_VERIFY_MISMATCH E_STAGE_INPUT E_CONFIG E_RULE E_BUDGET E_NOT_ALLOWED` — each has a one-line remedy in `AI_USAGE.md`.

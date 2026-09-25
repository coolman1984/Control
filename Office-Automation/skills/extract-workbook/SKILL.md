---
name: extract-workbook
description: Convert Excel workbooks (xlsx/xlsm/xlsb/xls, incl. DRM-wrapped) into a verified SQLite database with xl2ai extract, and read the result safely.
---

# extract-workbook

## Run
```powershell
python -m xl2ai extract "C:\path\file.xlsx" -o "C:\out"      # -> C:\out\file.db
python -m xl2ai extract "C:\folder" -o "C:\out"              # all workbooks in the folder
```
Options: `--sheet NAME` (repeatable), `--no-verify`, `--strict` (STRICT tables), `--visible` (debug), `--open-timeout N`.
Takes seconds to a minute; a 5.8M-cell sheet reads in about 4 s. Requires Excel + pywin32. Never edits the source.

## Read the outcome in this order (each step is tiny)
1. Console summary / exit code: 0 ok, 1 file failed, 2 some sheets failed, 3 verification mismatch (do not trust the data).
2. `SELECT sheet_name,status,message,data_rows,header_row,error_cells FROM _extraction_log;`
3. `SELECT count(*) FROM _verification WHERE ok=0;` must be 0. `check_name='cells_total'` proves nothing was left behind.
4. `SELECT sql_name,original_header,sql_type,kind,non_null FROM _columns WHERE table_name='T';` for one table.
5. Only then query data: `SELECT ... FROM T LIMIT 20`.

## What the tables mean
- Data table = one per extracted sheet; `_xl_row` is the Excel row, so any row can be traced back.
- NULL = empty cell OR an Excel error cell; errors are listed in `_cell_errors` (row, column, `#N/A`...).
- `header_row` NULL means no header was detected: columns are `col_N`. Rows above the header are in `_sheet_preamble`.
- Dates are ISO text; big integers beyond 2^53 are REAL; mixed columns keep their original values (BLOB affinity).
- Merged cells are not filled: only the top-left cell holds the value (`_merged_areas` lists header-region merges).

## Never
- Never read the source workbook with anything but this tool. Never edit or "fix" the database by hand.
- Never ignore exit code 2 or 3, or a `skipped` sheet you did not expect: report it.
- Never assume a detected header is right for report-style sheets: check `_sheet_preamble` and `header_row`.

## Failures
| Message | Meaning | Do |
|---|---|---|
| `waiting for user input (dialog 'Password')` | password prompt | ask the owner for an unprotected copy |
| `Excel could not open the file...` | corrupt, protected, or unsupported | try opening once by hand; report |
| `Excel died on ...` then `Restarting Excel` | crash; auto-recovered | none, unless it ends in an error |
| exit 3 / `_verification.ok=0` | stored data differs from Excel | do not use; report table + check |
The previous database is never replaced by a failed run.

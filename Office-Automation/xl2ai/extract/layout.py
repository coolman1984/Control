"""Sheet layout detection: header row, true data extent, filters, merged areas."""
from __future__ import annotations

from .common import HEADER_SCAN_ROWS, XL_BYCOLS, XL_BYROWS, XL_FORMULAS, XL_NEXT, XL_PART, XL_PREV, pywintypes

def looks_like_header(row, width):
    vals = [v for v in row if v is not None and v != ""]
    if not vals or len(vals) < max(1, 0.6 * width):
        return False
    strs = [v.strip().lower() for v in vals if type(v) is str]
    if len(strs) < 0.8 * len(vals):
        return False
    return len(set(strs)) >= 0.5 * len(strs)          # repeated sub-labels under grouped headers are still a header


def find_header(block, width):
    n = min(HEADER_SCAN_ROWS, len(block))
    for i in range(n):
        if looks_like_header(block[i], width) and i + 1 < len(block) and any(v is not None for v in block[i + 1]):
            return i
    return None


def header_confidence(block, width, header_row):
    """(score in [0,1], [reason,...]) for the header row this run chose (or None: no header detected).

    Pure function of already-read values, so an agent's trust in a table's column names has a number and a
    reason attached instead of a silent guess -- and it is unit-testable with no COM involved.
    """
    if header_row is None:
        return 0.0, ["no header row found in the first rows scanned; columns were named generically"]
    row = block[header_row]
    vals = [v for v in row if v is not None and v != ""]
    if not vals:
        return 0.0, ["chosen header row is empty"]
    reasons, score = [], 0.0

    filled_ratio = len(vals) / max(1, width)
    score += 0.4 * min(1.0, filled_ratio)
    reasons.append(f"{len(vals)}/{width} columns have a header value")

    strs = [v.strip().lower() for v in vals if type(v) is str]
    text_ratio = len(strs) / max(1, len(vals))
    score += 0.3 * text_ratio
    reasons.append(f"{len(strs)}/{len(vals)} header values are text")

    uniq_ratio = len(set(strs)) / max(1, len(strs)) if strs else 0.0
    score += 0.2 * uniq_ratio
    if uniq_ratio < 1.0:
        reasons.append("some header labels repeat (may be a grouped/hierarchical header)")

    has_next_data = header_row + 1 < len(block) and any(v is not None for v in block[header_row + 1])
    if has_next_data:
        score += 0.1
    else:
        reasons.append("no data row immediately follows the chosen header")

    if header_row > 0:
        reasons.append(f"{header_row} row(s) above the header were kept as preamble")

    return round(min(1.0, score), 3), reasons


def show_filtered_rows(ws):
    """Un-filter the sheet in Excel's in-memory copy; returns True if a filter was active.

    Range.Find (and SpecialCells LastCell) silently skip rows hidden by an active AutoFilter (verified), which
    would truncate the extent. The workbook is read-only and closed without saving, so the file is never touched.
    """
    was_active = False
    try:
        if ws.FilterMode:
            was_active = True
            ws.ShowAllData()
    except pywintypes.com_error:
        pass
    try:
        for lo in ws.ListObjects:                       # filters on Excel tables are separate from the sheet's
            if lo.AutoFilter is not None and lo.AutoFilter.FilterMode:
                was_active = True
                lo.AutoFilter.ShowAllData()
    except pywintypes.com_error:
        pass
    return was_active


def find_extent(ws):
    """True (first_row, first_col, last_row, last_col) of cells holding a value/formula, else None."""
    def find(order, direction, after):
        return ws.Cells.Find(What="*", After=after, LookIn=XL_FORMULAS, LookAt=XL_PART, SearchOrder=order,
                             SearchDirection=direction, MatchCase=False, SearchFormat=False)
    last_row = find(XL_BYROWS, XL_PREV, ws.Cells(1, 1))
    if last_row is None:
        return None
    last_col = find(XL_BYCOLS, XL_PREV, ws.Cells(1, 1))
    corner = ws.Cells(ws.Rows.Count, ws.Columns.Count)
    first_row = find(XL_BYROWS, XL_NEXT, corner)
    first_col = find(XL_BYCOLS, XL_NEXT, corner)
    # All four searches scan the same non-empty-cell universe, just in different directions, so if one found
    # something the others should too -- but never trust a live COM call that far: a None here would otherwise
    # crash on .Row/.Column below instead of being treated as "no usable extent", same as the check above.
    if last_col is None or first_row is None or first_col is None:
        return None
    return first_row.Row, first_col.Column, last_row.Row, last_col.Column


def find_merged_areas(ws, r0, r1, c0, c1):
    """[(address, top-left value)] for merged areas anchored in rows r0..r1.

    One MergeCells call per row (False => skip the row); only rows that report True/mixed are walked cell by
    cell. Meant for the header region: scanning a whole data area costs ~17 ms per merged area (measured).
    """
    areas, seen = [], set()
    for r in range(r0, r1 + 1):
        if ws.Range(ws.Cells(r, c0), ws.Cells(r, c1)).MergeCells is False:
            continue
        for c in range(c0, c1 + 1):
            if (r, c) in seen or not ws.Cells(r, c).MergeCells:
                continue
            area = ws.Cells(r, c).MergeArea
            for rr in range(area.Row, area.Row + area.Rows.Count):
                for cc in range(area.Column, area.Column + area.Columns.Count):
                    seen.add((rr, cc))
            if area.Cells.Count > 1:
                areas.append((area.Address.replace("$", ""), area.Cells(1, 1).Value2))
    return areas

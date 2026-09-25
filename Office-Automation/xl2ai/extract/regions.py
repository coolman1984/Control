"""Sheet structure beyond "one table, one header row": separate table regions and grouped (multi-row) headers.

Both detectors are pure functions over an already-read value grid (the same Value2-style rows extraction reads
anyway), so they cost no extra Excel/COM calls and are unit-tested without Excel.

They *describe*, they never re-cut: the extracted table keeps its identity (one table per sheet), and what is found
here is recorded next to it (`_regions`, `_header_groups`) so an agent learns "this sheet actually holds three
tables" or "column Plan sits under Q1" instead of silently working on a flattened wide table. Every cell is still in
the data table or the preamble; nothing is dropped or moved.
"""
from __future__ import annotations

import re

from .layout import find_header, looks_like_header

MIN_GAP_ROWS = 2          # blank rows needed before a new block can start (1 blank row is normal inside data)
BAND_MERGE_OVERLAP = 0.9  # side-by-side blocks sharing >= this share of rows are one table with a spacer column
MAX_GROUP_LEVELS = 3


def _filled(v):
    return v is not None and not (v.__class__ is str and not v.strip())


def _runs(flags):
    """[(start, end)] of consecutive True positions."""
    out, start = [], None
    for i, f in enumerate(flags):
        if f and start is None:
            start = i
        elif not f and start is not None:
            out.append((start, i - 1))
            start = None
    if start is not None:
        out.append((start, len(flags) - 1))
    return out


def _rows_used(grid, c_lo, c_hi):
    return [any(_filled(v) for v in row[c_lo:c_hi + 1]) for row in grid]


def _column_bands(grid, width):
    """Blocks of columns separated by fully empty columns; row-aligned neighbours are merged back together."""
    used = [any(c < len(row) and _filled(row[c]) for row in grid) for c in range(width)]
    bands = _runs(used)
    merged = []
    for lo, hi in bands:
        if merged:
            plo, phi = merged[-1]
            a = {i for i, u in enumerate(_rows_used(grid, plo, phi)) if u}
            b = {i for i, u in enumerate(_rows_used(grid, lo, hi)) if u}
            if a and b and len(a & b) / len(a | b) >= BAND_MERGE_OVERLAP:
                merged[-1] = (plo, hi)          # same rows on both sides: a spacer column inside one table
                continue
        merged.append((lo, hi))
    return merged


def find_regions(grid, first_row=1, first_col=1, min_gap_rows=MIN_GAP_ROWS):
    """[{first_row, first_col, last_row, last_col, header_row, kind, cells}] in Excel coordinates.

    A block of rows starts a *new* region only after at least `min_gap_rows` blank rows AND only if its first row
    looks like a header; otherwise it is the same table continuing (grouped data with blank separators). Columns
    split into regions only across a fully empty column whose two sides do not share the same rows.
    kind is `table` (>= 2 rows and >= 2 columns) or `note` (a title, a lone label, a footnote).
    """
    if not grid:
        return []
    width = max(len(r) for r in grid)
    grid = [list(r) + [None] * (width - len(r)) for r in grid]
    regions = []
    for c_lo, c_hi in _column_bands(grid, width):
        used = _rows_used(grid, c_lo, c_hi)
        segments = []
        for s, e in _runs(used):
            if segments and s - segments[-1][1] - 1 < min_gap_rows:
                segments[-1] = (segments[-1][0], e)
            else:
                segments.append((s, e))
        blocks = []
        for s, e in segments:
            sub_width = sum(1 for c in range(c_lo, c_hi + 1) if any(_filled(grid[r][c]) for r in range(s, e + 1)))
            first = next(grid[r][c_lo:c_hi + 1] for r in range(s, e + 1) if used[r])
            if blocks and not looks_like_header(first, sub_width):
                blocks[-1] = (blocks[-1][0], e)
            else:
                blocks.append((s, e))
        for s, e in blocks:
            cols = [c for c in range(c_lo, c_hi + 1) if any(_filled(grid[r][c]) for r in range(s, e + 1))]
            rows = [r for r in range(s, e + 1) if used[r]]
            cells = sum(1 for r in rows for c in cols if _filled(grid[r][c]))
            sub = [grid[r][cols[0]:cols[-1] + 1] for r in range(rows[0], rows[-1] + 1)]
            hdr = find_header(sub, len(cols))
            kind = "table" if len(rows) >= 2 and len(cols) >= 2 else "note"
            regions.append({"first_row": first_row + rows[0], "first_col": first_col + cols[0],
                            "last_row": first_row + rows[-1], "last_col": first_col + cols[-1],
                            "header_row": None if hdr is None else first_row + rows[0] + hdr,
                            "kind": kind, "cells": cells})
    regions.sort(key=lambda g: (g["first_row"], g["first_col"]))
    return regions


def multiple_tables(regions):
    """True when the sheet holds more than one real table (notes/titles alone never count)."""
    return sum(1 for g in regions if g["kind"] == "table") > 1


_ADDR = re.compile(r"^\$?([A-Z]+)\$?(\d+)(?::\$?([A-Z]+)\$?(\d+))?$")


def _col_num(letters):
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch) - 64
    return n


def parse_address(addr):
    """'B2:D3' -> (row1, col1, row2, col2); None if it is not a plain A1 range."""
    m = _ADDR.match(str(addr).replace("$", "").upper())
    if not m:
        return None
    c1, r1 = _col_num(m.group(1)), int(m.group(2))
    c2, r2 = (_col_num(m.group(3)), int(m.group(4))) if m.group(3) else (c1, r1)
    return r1, c1, r2, c2


def header_groups(grid, header_idx, first_row=1, first_col=1, merged=()):
    """{xl_col: [group label, ...]} for grouped header rows sitting directly above the chosen header row.

    `merged` is [(address, value)] as extraction already records it. A label row qualifies when it is directly
    above (no blank row between), has fewer labels than the header has names, and at least one label spans two or
    more header columns. Spans come from merged areas when present; otherwise a label runs until the next label,
    and a lone label with no merge is treated as a title (never spread over the whole header).
    Returns ({}, None) when no grouping is found, else (groups, method).
    """
    if header_idx is None or header_idx <= 0:
        return {}, None
    header = grid[header_idx]
    named = [c for c, v in enumerate(header) if _filled(v)]
    if len(named) < 2:
        return {}, None
    last_named = named[-1]
    spans_by_row = {}
    for addr, _ in merged or ():
        box = parse_address(addr)
        if box:
            r1, c1, r2, c2 = box
            spans_by_row.setdefault(r1 - first_row, {})[c1 - first_col] = c2 - first_col
    groups, methods = {}, set()
    for level in range(1, MAX_GROUP_LEVELS + 1):
        k = header_idx - level
        if k < 0:
            break
        row = grid[k]
        labels = [(c, str(v).strip()) for c, v in enumerate(row) if _filled(v)]
        if not labels or len(labels) >= len(named):
            break
        row_merges = spans_by_row.get(k, {})
        spans = []
        for i, (c, text) in enumerate(labels):
            if c in row_merges:
                spans.append((c, row_merges[c], text))
                methods.add("merged_area")
            elif len(labels) >= 2:
                end = labels[i + 1][0] - 1 if i + 1 < len(labels) else last_named
                spans.append((c, end, text))
                methods.add("label_run")
        wide = [s for s in spans if sum(1 for c in named if s[0] <= c <= s[1]) >= 2]
        if not wide:
            break
        for lo, hi, text in spans:
            for c in named:
                if lo <= c <= hi:
                    groups.setdefault(c, []).insert(0, text)
    if not groups:
        return {}, None
    return {first_col + c: path for c, path in groups.items()}, "+".join(sorted(methods))

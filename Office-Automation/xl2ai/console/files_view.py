"""Where the data actually went.

Answers the question a spreadsheet owner asks after every run: my file went in -- what came out of it, which
sheets became tables, how many rows each holds, and what was quietly skipped and why.
"""
from __future__ import annotations

from ..ui import badge, rule, style as st, tree
from ..ui.measure import truncate
from ..ui.spinner import duration, size_bytes
from ..ui.table import Column, Table
from . import theme


def sources_table(console, timeline):
    table = Table([
        Column("source", priority=1, max_width=34),
        Column("status", priority=1),
        Column("sheets", align="right", priority=3),
        Column("rows", align="right", priority=2),
        Column("size", align="right", priority=4),
        Column("time", align="right", priority=4),
    ])
    for source in sorted(timeline.sources.values(), key=lambda s: s.source_id):
        rows = sum(sheet.rows for sheet in source.sheets)
        table.add(
            console.styled(truncate(source.source_id, 34), st.VALUE),
            badge.badge(console, theme.badge_kind(source.status)),
            f"{len(source.sheets):,}" if source.sheets else console.styled("-", st.FAINT),
            console.styled(f"{rows:,}", st.VALUE) if rows else console.styled("-", st.FAINT),
            console.styled(size_bytes(source.size), st.MUTED),
            console.styled(duration(source.seconds), st.FAINT),
        )
    return table.render(console)


def distribution_tree(console, timeline, max_sheets=None):
    """The nesting: each source file, then every sheet it produced and what happened to it."""
    nodes = []
    for source in sorted(timeline.sources.values(), key=lambda s: s.source_id):
        label = (badge.mark(console, theme.badge_kind(source.status)) + " "
                 + console.styled(source.source_id, st.ACCENT))
        note = source.path or ""
        root = tree.Node(label, console.styled(truncate(note, 46), st.FAINT) if note else "")
        sheets = source.sheets[:max_sheets] if max_sheets else source.sheets
        for sheet in sheets:
            detail = (f"{sheet.rows:,} rows x {sheet.columns} cols"
                      if sheet.status == "extracted" else (sheet.message or sheet.status))
            root.add(badge.mark(console, theme.badge_kind(sheet.status)) + " "
                     + console.styled(sheet.name, st.VALUE), detail)
        hidden = len(source.sheets) - len(sheets)
        if hidden > 0:
            root.add(console.styled(f"{hidden} more sheet(s)", st.FAINT), "")
        nodes.append(root)
    return tree.render(console, nodes)


def render(console, timeline, show_tree=True):
    out = [rule.heading(console, "sources", f"{len(timeline.sources)} file(s)"), ""]
    out += sources_table(console, timeline)
    if show_tree and timeline.sources:
        out += ["", rule.heading(console, "distribution", "what each file produced"), ""]
        out += distribution_tree(console, timeline)
    return out

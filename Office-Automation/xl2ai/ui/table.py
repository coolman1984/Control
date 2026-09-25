"""Tables that stay aligned.

Columns declare intent (alignment, minimum and maximum width, whether they may be dropped when space runs out)
and the table decides the layout from the real content. When the terminal is too narrow, low-priority columns are
dropped rather than letting the whole table wrap into noise.
"""
from __future__ import annotations

from . import style as st
from .measure import pad, width


class Column:
    __slots__ = ("title", "align", "min_width", "max_width", "priority", "style", "header_style")

    def __init__(self, title, align="left", min_width=0, max_width=None, priority=5,
                 style=st.VALUE, header_style=st.LABEL):
        self.title, self.align = title, align
        self.min_width, self.max_width, self.priority = min_width, max_width, priority
        self.style, self.header_style = style, header_style


class Table:
    """Rows are lists of already-rendered (possibly styled) strings, one per column."""

    def __init__(self, columns, indent=2, gap=2, show_header=True):
        self.columns = list(columns)
        self.indent, self.gap, self.show_header = indent, gap, show_header
        self.rows = []

    def add(self, *cells):
        self.rows.append(["" if c is None else str(c) for c in cells])
        return self

    def _natural_widths(self, columns, rows):
        widths = []
        for index, column in enumerate(columns):
            longest = max([width(column.title) if self.show_header else 0]
                          + [width(row[index]) for row in rows])
            size = max(column.min_width, longest)
            if column.max_width:
                size = min(size, column.max_width)
            widths.append(size)
        return widths

    def _fit(self, console):
        """Drop the least important columns until the table fits, then shrink the widest flexible one."""
        columns, indexes = list(self.columns), list(range(len(self.columns)))
        while True:
            rows = [[row[i] for i in indexes] for row in self.rows] or [[""] * len(indexes)]
            widths = self._natural_widths(columns, rows)
            total = sum(widths) + self.gap * (len(widths) - 1) + self.indent
            if total <= console.width or len(columns) <= 1:
                break
            weakest = max(range(len(columns)), key=lambda i: (columns[i].priority, widths[i]))
            if columns[weakest].priority <= 1:            # priority 1 columns are essential: shrink instead
                break
            columns.pop(weakest)
            indexes.pop(weakest)
        overflow = sum(widths) + self.gap * (len(widths) - 1) + self.indent - console.width
        while overflow > 0:
            widest = max(range(len(widths)), key=lambda i: widths[i])
            shrink = min(overflow, max(0, widths[widest] - max(4, columns[widest].min_width)))
            if not shrink:
                break
            widths[widest] -= shrink
            overflow -= shrink
        return columns, indexes, widths

    def render(self, console):
        if not self.rows and not self.show_header:
            return []
        columns, indexes, widths = self._fit(console)
        gap = " " * self.gap
        out = []
        if self.show_header:
            cells = [console.styled(pad(c.title, w, c.align), c.header_style) for c, w in zip(columns, widths)]
            out.append(" " * self.indent + gap.join(cells))
            out.append(" " * self.indent + console.styled(
                gap.join(console.glyph("h") * w for w in widths), st.BORDER))
        for row in self.rows:
            cells = [pad(row[i], w, c.align) for c, w, i in zip(columns, widths, indexes)]
            out.append(" " * self.indent + gap.join(cells))
        return out

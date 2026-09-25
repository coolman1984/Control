"""Tree rendering: how a run's work actually nested -- source, sheet, column -- without the reader guessing."""
from __future__ import annotations

from . import style as st


class Node:
    __slots__ = ("label", "note", "children", "kind")

    def __init__(self, label, note="", kind=None, children=None):
        self.label, self.note, self.kind = label, note, kind
        self.children = list(children or [])

    def add(self, label, note="", kind=None):
        child = Node(label, note, kind)
        self.children.append(child)
        return child

    def adopt(self, node):
        self.children.append(node)
        return node


def render(console, nodes, indent=2, note_style=st.FAINT, connector_style=st.BORDER):
    """Render a forest. Labels and notes are expected to be pre-styled by the caller.

    The note is pushed to the right margin when there is room, which keeps the tree structure readable down the
    left edge instead of being broken up by variable-length detail.
    """
    from .measure import width
    out = []

    def walk(items, prefix):
        for index, node in enumerate(items):
            last = index == len(items) - 1
            joint = console.glyph("last") if last else console.glyph("branch")
            head = " " * indent + console.styled(prefix, connector_style) \
                + console.styled(joint, connector_style) + " " + node.label
            if node.note:
                gap = console.width - width(head) - width(node.note) - 1
                head = head + " " * max(1, gap) + console.styled(node.note, note_style) \
                    if gap > 0 else head + " " + console.styled(node.note, note_style)
            out.append(head)
            if node.children:
                walk(node.children, prefix + (console.glyph("gap") if last else console.glyph("trunk")))

    walk(list(nodes), "")
    return out

"""The one shape every document reader produces."""
from __future__ import annotations

from dataclasses import dataclass, field

KINDS = ("heading", "paragraph", "list_item", "table_caption", "slide_title", "note", "email_header", "email_body",
         "page_text", "text")


@dataclass
class Block:
    """A run of text with its location. `part` names an attachment ("" = the document itself)."""
    kind: str
    text: str
    page: int | None = None           # PDF page / slide number (1-based); None where the format has no pages
    section: str = ""                 # heading path, e.g. "Prices > 2024" (or "Slide 3: Title")
    level: int | None = None          # heading level
    part: str = ""


@dataclass
class Table:
    rows: list                        # list of lists of cell text, first row = header when `has_header`
    has_header: bool = True
    page: int | None = None
    section: str = ""
    caption: str = ""
    part: str = ""
    index: int = 0                    # n-th table of the document (1-based), for a stable name

    def name(self):
        where = f"p{self.page} " if self.page else ""
        prefix = f"{self.part} " if self.part else ""
        return f"{prefix}{where}table {self.index}".strip()


@dataclass
class Attachment:
    name: str
    data: bytes


@dataclass
class Doc:
    kind: str                         # docx | pptx | pdf | msg | eml | txt | md | csv | html | doc | ppt
    meta: dict = field(default_factory=dict)
    blocks: list = field(default_factory=list)
    tables: list = field(default_factory=list)
    attachments: list = field(default_factory=list)
    warnings: list = field(default_factory=list)   # what could not be read, stated (becomes a blind spot)
    pages: int | None = None

    def add_table(self, rows, **kw):
        rows = [[("" if c is None else str(c)).strip() for c in r] for r in rows if r is not None]
        rows = [r for r in rows if any(c for c in r)]
        if len(rows) < 2 or max(len(r) for r in rows) < 2:
            return None                # a one-row or one-column "table" is layout, not data
        width = max(len(r) for r in rows)
        rows = [r + [""] * (width - len(r)) for r in rows]
        t = Table(rows=rows, index=len(self.tables) + 1, **kw)
        self.tables.append(t)
        return t

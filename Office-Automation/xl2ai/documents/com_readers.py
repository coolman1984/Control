"""Office readers (Windows): Word, PowerPoint and Outlook themselves open the file, so rights-managed (DRM/IRM)
documents and legacy .doc/.ppt are read exactly as the user sees them. Same `Doc` output as the direct readers.

Each reader starts a private, invisible Office instance, opens read-only, reads through the object model, and always
quits it. COM is initialised for the calling thread (the agent connection runs builds in a background thread).
These need Microsoft Office; they are exercised by fake-object unit tests here and must be validated on Windows.
"""
from __future__ import annotations

import os
import tempfile
from contextlib import contextmanager

from .model import Doc

WD_WITHIN_TABLE = 12
CELL_END = "\r\x07"


@contextmanager
def _office(progid):
    from ..extract.common import PYWIN32_AVAILABLE, pythoncom, win32
    if os.name != "nt" or not PYWIN32_AVAILABLE:
        raise RuntimeError("reading this file needs Microsoft Office on Windows (rights-managed or legacy format)")
    pythoncom.CoInitialize()
    app = None
    try:
        app = win32.DispatchEx(progid)
        for prop, val in (("Visible", False), ("DisplayAlerts", 0), ("ScreenUpdating", False)):
            try:
                setattr(app, prop, val)
            except Exception:
                pass
        yield app
    finally:
        if app is not None:
            try:
                app.Quit()
            except Exception:
                pass
        app = None
        import gc
        gc.collect()
        pythoncom.CoUninitialize()


def _cell_text(text):
    return (text or "").replace(CELL_END, "").replace("\x07", "").replace("\r", " ").strip()


def read_word_com(path, app=None):
    """Paragraph by paragraph (OutlineLevel gives headings), then every table cell by cell."""
    if app is None:
        with _office("Word.Application") as word:
            return read_word_com(path, word)
    doc = Doc("docx")
    d = app.Documents.Open(FileName=os.path.abspath(path), ReadOnly=True, AddToRecentFiles=False, Visible=False,
                           ConfirmConversions=False)
    try:
        try:
            doc.meta = {k: str(d.BuiltInDocumentProperties(k).Value) for k in ("Title", "Author")
                        if d.BuiltInDocumentProperties(k).Value}
        except Exception:
            pass
        stack = []
        for p in d.Paragraphs:
            rng = p.Range
            if rng.Information(WD_WITHIN_TABLE):
                continue
            text = (rng.Text or "").strip()
            if not text:
                continue
            level = int(p.OutlineLevel or 10)
            from .readers import _block
            if level <= 9:
                stack = stack[:level - 1] + [text]
                doc.blocks.append(_block("heading", text, section=" > ".join(stack[:-1]), level=level))
            else:
                doc.blocks.append(_block("paragraph", text, section=" > ".join(stack)))
        for t in d.Tables:
            nrows, ncols = t.Rows.Count, t.Columns.Count
            grid = [[""] * ncols for _ in range(nrows)]
            for cell in t.Range.Cells:                   # works for merged cells, unlike Cell(r, c)
                r, c = cell.RowIndex - 1, cell.ColumnIndex - 1
                if r < nrows and c < ncols:
                    grid[r][c] = _cell_text(cell.Range.Text)
            doc.add_table(grid)
    finally:
        d.Close(False)
    return doc


def read_powerpoint_com(path, app=None):
    if app is None:
        with _office("PowerPoint.Application") as ppt:
            return read_powerpoint_com(path, ppt)
    from .readers import _block
    pres = app.Presentations.Open(os.path.abspath(path), True, False, False)   # ReadOnly, Untitled, WithWindow
    doc = Doc("pptx")
    try:
        doc.pages = pres.Slides.Count
        for n in range(1, pres.Slides.Count + 1):
            slide = pres.Slides(n)
            title = ""
            try:
                if slide.Shapes.HasTitle:
                    title = slide.Shapes.Title.TextFrame.TextRange.Text.strip()
            except Exception:
                pass
            section = f"Slide {n}" + (f": {title}" if title else "")
            if title:
                doc.blocks.append(_block("slide_title", title, page=n, section=section))
            for i in range(1, slide.Shapes.Count + 1):
                sh = slide.Shapes(i)
                if getattr(sh, "HasTable", False):
                    tb = sh.Table
                    rows = [[tb.Cell(r, c).Shape.TextFrame.TextRange.Text for c in range(1, tb.Columns.Count + 1)]
                            for r in range(1, tb.Rows.Count + 1)]
                    doc.add_table(rows, page=n, section=section, caption=title)
                elif getattr(sh, "HasChart", False):
                    doc.warnings.append(f"slide {n}: a chart's data is not read through PowerPoint (read the .pptx "
                                        "directly when it is not rights-managed)")
                elif getattr(sh, "HasTextFrame", False) and sh.TextFrame.HasText:
                    text = sh.TextFrame.TextRange.Text.strip()
                    if text and text != title:
                        for para in text.replace("\x0b", "\n").split("\r"):
                            if para.strip():
                                doc.blocks.append(_block("paragraph", para.strip(), page=n, section=section))
            try:
                notes = slide.NotesPage.Shapes.Placeholders(2).TextFrame.TextRange.Text.strip()
                if notes:
                    doc.blocks.append(_block("note", notes, page=n, section=section))
            except Exception:
                pass
    finally:
        pres.Close()
    return doc


def read_outlook_com(path, app=None):
    """A saved .msg through Outlook itself: the only way to read rights-protected (IRM) e-mail."""
    if app is None:
        with _office("Outlook.Application") as ol:
            return read_outlook_com(path, ol)
    from .model import Attachment
    from .readers import _block, _email_header_block, _paragraphs
    item = app.Session.OpenSharedItem(os.path.abspath(path))
    doc = Doc("msg")
    doc.meta = {k: str(v) for k, v in {"from": f"{item.SenderName} <{item.SenderEmailAddress}>", "to": item.To,
                                       "cc": item.CC, "subject": item.Subject, "sent": item.SentOn}.items() if v}
    _email_header_block(doc)
    for para in _paragraphs(item.Body or ""):
        doc.blocks.append(_block("email_body", para))
    with tempfile.TemporaryDirectory() as tmp:
        for i in range(1, item.Attachments.Count + 1):
            att = item.Attachments(i)
            target = os.path.join(tmp, f"{i}-{att.FileName}")
            try:
                att.SaveAsFile(target)
                with open(target, "rb") as f:
                    doc.attachments.append(Attachment(att.FileName, f.read()))
            except Exception as e:
                doc.warnings.append(f"attachment '{att.FileName}' could not be saved: {e}")
    return doc


COM_READERS = {"docx": read_word_com, "docm": read_word_com, "doc": read_word_com,
               "pptx": read_powerpoint_com, "pptm": read_powerpoint_com, "ppt": read_powerpoint_com,
               "msg": read_outlook_com}

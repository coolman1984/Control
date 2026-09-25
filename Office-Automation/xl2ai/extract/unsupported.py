"""Detect content this platform cannot fully read, so a run reports it rather than silently omitting it.

Everything here is a hard problem stated honestly: `xl2ai` extracts *values*, not logic. A workbook whose real
business logic lives in Power Query, the Data Model, an external workbook link, or a rich linked-data-type cell
looks, to the extractor, like ordinary values -- unless something explicitly checks. This module is that check.

Every finding is additive and never blocks extraction: an unreadable Power Query step is a fact to report, not a
reason to fail the run. Each COM call is guarded the same way the rest of extraction guards them (see sheet.py's
`info()`): a property Excel does not support on this version, or one that raises for an unrelated reason, is
treated as "not detected" rather than crashing the run.
"""
from __future__ import annotations

from .com import ExcelDied

# xlExcelLinks: the only link-source type Workbook.LinkSources reliably enumerates across Excel versions.
XL_EXCEL_LINKS = 1


def _safe(sess, fn):
    """Best-effort COM read: None on any failure that is not Excel dying (never let a version quirk fail a run)."""
    try:
        return sess.call(fn)
    except ExcelDied:
        raise
    except Exception:
        return None


def detect_workbook_unsupported(sess, wb):
    """[(kind, count, detail)] for content that spans the whole workbook rather than one sheet.

    kind is one of: power_query, data_model, external_link. Absence of a property (older Excel) or zero count
    both mean "nothing to report" -- only a genuinely positive count is returned.
    """
    findings = []

    n = _safe(sess, lambda: wb.Queries.Count)
    if n:
        names = _safe(sess, lambda: [wb.Queries(i + 1).Name for i in range(min(n, 20))]) or []
        findings.append(("power_query", int(n),
                         "Power Query step logic is not read; only whatever values it last wrote to a sheet are "
                         "extracted" + (f" (e.g. {', '.join(names[:5])})" if names else "")))

    model_tables = _safe(sess, lambda: wb.Model.ModelTables.Count)
    if model_tables:
        findings.append(("data_model", int(model_tables),
                         "the Excel Data Model (Power Pivot) is not read; its tables/measures/relationships are "
                         "invisible to this platform even if a PivotTable on a sheet displays their output"))

    links = _safe(sess, lambda: wb.LinkSources(XL_EXCEL_LINKS))
    if links:
        count = len(links) if isinstance(links, (list, tuple)) else 1
        sample = ", ".join(str(x) for x in (links[:3] if isinstance(links, (list, tuple)) else [links]))
        findings.append(("external_link", count,
                         f"formulas reference other workbook(s) not extracted here: {sample}"))

    return findings


def detect_sheet_unsupported(sess, ws):
    """[(kind, count, detail)] for content local to one sheet: charts (data-source logic, not just their output)."""
    findings = []
    chart_count = _safe(sess, lambda: ws.ChartObjects().Count)
    if chart_count:
        findings.append(("chart", int(chart_count),
                         "chart(s) present; their series/source formulas are not extracted, only the underlying "
                         "cell values (if those cells are otherwise part of the extracted table)"))
    return findings


def detect_stale_calculation(sess):
    """True if Excel reports pending recalculation right after opening (xlCalculationState != xlDone(0)).

    Checked immediately after open, before this session's own manual-calculation setting (see com.py's
    ExcelSession.start) could mask what the file itself was saved with. A non-zero state means the workbook was
    saved with formulas not yet recalculated -- any formula-derived *value* extracted may be stale.
    """
    state = _safe(sess, lambda: sess.app.CalculationState)
    return bool(state)

"""Explain a failure, for a person and for an AI.

Two audiences, one analysis. A person needs the first real cause (not the ten downstream symptoms), what it
means, and what to try. An AI needs the same facts as plain, copy-pasteable text with enough surrounding events
to reason about -- which is what `--ai` prints.

The single most useful thing this does is pick the *first* failed stage rather than the last error, because in a
staged pipeline everything after the first failure is noise.
"""
from __future__ import annotations

from ..observe import events as E
from ..ui import panel, rule, style as st
from ..ui.measure import truncate, wrap
from ..ui.spinner import duration
from . import theme

CONTEXT_BEFORE = 12


def root_cause(timeline, all_events):
    """(event, stage) for the first thing that actually went wrong, or (None, None) when the run was clean."""
    stage = timeline.failed_stage
    first_error = next((e for e in all_events if e.level == "error"), None)
    if stage is None and first_error is None:
        return None, None
    return first_error, stage


def _failure_code(error, stage):
    """The error code, wherever it survived: the failing event carries it, and so does the stage record. The
    first error event is often the symptom (a source giving up) while the code lives on the stage that owns it."""
    if error and error.data.get("code"):
        return error.data["code"]
    if stage and isinstance(stage.error, dict) and stage.error.get("code"):
        return stage.error["code"]
    return ""


def context_events(all_events, pivot, before=CONTEXT_BEFORE):
    """The events leading up to the failure: what the pipeline was doing when it broke."""
    if pivot is None:
        return all_events[-before:]
    try:
        index = all_events.index(pivot)
    except ValueError:
        return all_events[-before:]
    return all_events[max(0, index - before):index + 1]


def render(console, timeline, all_events):
    """The human-facing diagnosis."""
    out = ["", console.styled(f"{console.glyph('diamond')} diagnosis", st.HEADING), ""]
    out.append(rule.kv(console, "run", timeline.run_id or "-"))
    out.append(rule.kv(console, "project", timeline.project or "-"))
    out.append(rule.kv(console, "status", timeline.status or ("failed" if timeline.errors else "passed")))
    out.append(rule.kv(console, "duration", duration(timeline.elapsed)))
    out.append(rule.kv(console, "events", f"{len(all_events):,}"))
    out.append("")

    error, stage = root_cause(timeline, all_events)
    if error is None and stage is None:
        out += panel.callout(console, "No failure recorded in this run's journal.", kind="ok", title="clean")
        return out

    code = _failure_code(error, stage)
    message = (error.message if error else "") or (
        (stage.error or {}).get("message", "") if stage and isinstance(stage.error, dict) else "")
    meaning, action = theme.explain(code)

    title = f"{code or 'failure'}" + (f" in stage '{stage.name}'" if stage else "")
    out += panel.callout(console, message or "The run did not complete.", kind="error", title=title)
    out.append("")
    if meaning:
        out.append("  " + console.styled(meaning, st.VALUE))
    if action:
        out.append("  " + console.paint(f"{console.glyph('arrow')} {action}", "accent"))
    if meaning or action:
        out.append("")

    downstream = [s for s in timeline.stages if s.status in ("failed", "aborted") and s is not stage]
    if downstream:
        names = ", ".join(s.name for s in downstream)
        for line in wrap(f"later stages that never got valid input: {names}", console.width - 4):
            out.append("  " + console.styled(line, st.FAINT))
        out.append("")

    out.append(rule.heading(console, "what was happening", "last events before the failure"))
    out.append("")
    for event in context_events(all_events, error):
        out.append(_event_line(console, event))
    out.append("")
    out.append("  " + console.paint(f"{console.glyph('arrow')} add --ai to print this as plain text "
                                    f"you can paste into an AI assistant", "accent"))
    return out


def _event_line(console, event):
    stamp = console.styled(f"{event.elapsed:7.2f}s", st.FAINT)
    tone = theme.level_style(event.level)
    scope = event.scope.get("stage") or event.scope.get("source_id") or ""
    scope_text = console.styled(f"[{truncate(scope, 16)}] ", st.FAINT) if scope else ""
    body = event.message or event.kind
    return f"  {stamp} {scope_text}" + console.styled(truncate(body, max(20, console.width - 34)), tone)


def as_text(timeline, all_events, limit=60):
    """A plain-text dump for an AI assistant: no colour, no boxes, just ordered facts and the failure in context.

    Written to be pasted straight into a chat. It states what the tool is, what ran, what broke and what the
    surrounding events were, so an assistant that has never seen this project can still be useful.
    """
    error, stage = root_cause(timeline, all_events)
    lines = [
        "# xl2ai run diagnosis",
        "",
        "xl2ai extracts business Excel workbooks into verified SQLite and prepares a compact context for AI.",
        "A run is a sequence of stages; each stage writes into a run folder and the run is only promoted to",
        "'current' when every required stage passes. The journal below is that run's event log.",
        "",
        f"run_id: {timeline.run_id or 'unknown'}",
        f"project: {timeline.project or 'unknown'}",
        f"status: {timeline.status or ('failed' if timeline.errors else 'passed')}",
        f"promoted: {timeline.promoted}",
        f"duration_seconds: {round(timeline.elapsed, 2)}",
        f"events: {len(all_events)}",
        "",
        "## stages",
    ]
    for record in timeline.stages:
        detail = ", ".join(f"{k}={v}" for k, v in list(record.details.items())[:6])
        lines.append(f"- {record.name}: {record.status} ({duration(record.seconds)})"
                     + (f" [{detail}]" if detail else ""))
        if record.error:
            lines.append(f"    error: {record.error}")
        for warning in record.warnings[:5]:
            lines.append(f"    warning: {warning}")

    lines += ["", "## sources"]
    for source in sorted(timeline.sources.values(), key=lambda s: s.source_id):
        rows = sum(sheet.rows for sheet in source.sheets)
        lines.append(f"- {source.source_id}: {source.status}, {len(source.sheets)} sheet(s), {rows:,} rows"
                     + (f", path={source.path}" if source.path else ""))
        for sheet in source.sheets:
            if sheet.status != "extracted":
                lines.append(f"    sheet '{sheet.name}': {sheet.status} - {sheet.message}")

    if error or stage:
        code = _failure_code(error, stage)
        meaning, action = theme.explain(code)
        lines += ["", "## first failure",
                  f"stage: {stage.name if stage else 'unknown'}",
                  f"code: {code or 'unknown'}",
                  f"message: {error.message if error else (stage.error if stage else '')}"]
        if meaning:
            lines.append(f"meaning: {meaning}")
        if action:
            lines.append(f"suggested_action: {action}")
        later = [s.name for s in timeline.stages if s.status in ("failed", "aborted") and s is not stage]
        if later:
            lines.append(f"downstream_stages_without_valid_input: {', '.join(later)}")

    lines += ["", "## events leading up to it"]
    for event in context_events(all_events, error, before=limit):
        scope = " ".join(f"{k}={v}" for k, v in event.scope.items())
        data = " ".join(f"{k}={v}" for k, v in list(event.data.items())[:6])
        lines.append(f"{event.elapsed:8.2f}s {event.level:<5} {event.kind:<22} {event.message}"
                     + (f" | {scope}" if scope else "") + (f" | {data}" if data else ""))

    lines += ["", "## what I need",
              "Explain the most likely cause of this failure and the smallest change that would fix it.",
              "If the cause is a data or workbook problem rather than a code problem, say so explicitly."]
    return "\n".join(lines)

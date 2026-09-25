"""The live run view: what is happening, right now, and what already finished.

The screen is split in two on purpose. Finished work is *emitted* -- it scrolls up and stays, so the history of a
long run is still there afterwards. Work in progress lives in a small redrawn block at the bottom. That keeps the
terminal readable during a twenty-minute extraction and leaves a useful transcript when it ends.
"""
from __future__ import annotations

from ..observe import events as E
from ..observe.timeline import Timeline
from ..ui import badge, bar, rule, style as st
from ..ui.live import Live
from ..ui.measure import truncate, width
from ..ui.spinner import Spinner, duration
from . import theme


class RunView:
    """Subscribe to a bus and draw. Construct, call `attach(bus)`, then `start()` / `finish()`."""

    def __init__(self, console, timeline=None, show_sheets=True):
        self.console = console
        self.timeline = timeline if timeline is not None else Timeline()
        self.live = Live(console)
        self.spinner = Spinner.for_console(console)
        self.show_sheets = show_sheets
        self._activity = []                               # recent sheet/file lines shown under the active stage

    # ---- wiring ---------------------------------------------------------------------------------------------
    def attach(self, bus):
        return bus.subscribe(self.handle)

    def start(self):
        self.live.__enter__()
        return self

    def finish(self, tail=None):
        self.live.close(keep=tail or [])

    # ---- event handling -------------------------------------------------------------------------------------
    def handle(self, event):
        self.timeline.consume(event)
        renderer = getattr(self, "_on_" + event.kind.replace(".", "_"), None)
        if renderer:
            renderer(event)
        self._refresh()

    def _on_run_start(self, event):
        run_id = event.data.get("run_id", "")
        project = event.data.get("project", "")
        head = [
            "",
            self.console.styled(f"{self.console.glyph('diamond')} xl2ai refresh", st.HEADING),
            rule.kv(self.console, "project", project),
            rule.kv(self.console, "run", run_id),
            "",
        ]
        self.live.emit(head)

    def _on_stage_start(self, event):
        name = event.data.get("name", "")
        self._activity = []
        self.live.emit([rule.heading(self.console, name, theme.stage_purpose(name))])

    def _on_stage_end(self, event):
        name = event.data.get("name", "")
        record = self.timeline.stage(name)
        status = event.data.get("status", "passed")
        line = "  " + badge.badge(self.console, theme.badge_kind(status)) \
            + self.console.styled(f"  {duration(record.seconds if record else None)}", st.FAINT)
        detail = self._stage_detail_text(record)
        if detail:
            line += self.console.styled("   " + detail, st.MUTED)
        lines = [line]
        if record and record.error:
            lines += self._error_lines(record.error)
        self.live.emit(lines + [""])
        self._activity = []

    def _on_source_reused(self, event):
        self._note(badge.mark(self.console, "reused") + " "
                   + self.console.styled(event.data.get("source_id", ""), st.VALUE)
                   + self.console.styled("  unchanged, Excel not opened", st.FAINT), permanent=True)

    def _on_source_extract_start(self, event):
        self._note(self.console.styled(event.data.get("source_id", ""), st.ACCENT), permanent=True)

    def _on_sheet_end(self, event):
        if not self.show_sheets:
            return
        status = event.data.get("status", "extracted")
        name = event.data.get("name", "")
        rows, columns = event.data.get("rows") or 0, event.data.get("columns") or 0
        detail = f"{rows:,} rows x {columns} cols" if status == "extracted" else (event.message or status)
        self._note("  " + badge.mark(self.console, theme.badge_kind(status)) + " "
                   + self.console.styled(truncate(name, 34), st.VALUE)
                   + self.console.styled("  " + detail, st.FAINT), permanent=True)

    def _on_warning(self, event):
        self._note(self.console.paint(self.console.glyph("warn"), "warn") + " "
                   + self.console.styled(event.message, st.WARN), permanent=True)

    def _on_error(self, event):
        self.live.emit(self._error_lines({"code": event.data.get("code", ""), "message": event.message,
                                          "hint": event.data.get("hint", "")}))

    def _on_run_end(self, event):
        self.live.update([], force=True)

    # ---- helpers --------------------------------------------------------------------------------------------
    def _note(self, line, permanent=False):
        if permanent:
            self.live.emit(["  " + line])
        else:
            self._activity.append(line)
            self._activity = self._activity[-3:]

    def _stage_detail_text(self, record):
        if not record or not record.details:
            return ""
        parts = []
        for key, value in list(record.details.items())[:4]:
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                parts.append(f"{key} {value:,}")
            elif isinstance(value, str) and len(value) < 24:
                parts.append(f"{key} {value}")
        return self.console.glyph("dot").join(f" {p} " for p in parts).strip()

    def _error_lines(self, error):
        code = error.get("code", "") if isinstance(error, dict) else ""
        message = error.get("message", "") if isinstance(error, dict) else str(error)
        hint = error.get("hint", "") if isinstance(error, dict) else ""
        out = ["  " + self.console.paint(f"{self.console.glyph('fail')} {code}", "error", bold=True)
               + " " + self.console.styled(message, st.VALUE)]
        meaning, action = theme.explain(code)
        for text in (hint, action):
            if text:
                out.append("    " + self.console.styled(f"{self.console.glyph('arrow')} {text}", st.MUTED))
        return out

    def _refresh(self):
        stage = self.timeline.current_stage
        if stage is None:
            return
        frame = self.console.paint(self.spinner.frame(), "running")
        head = (f"  {frame} " + self.console.styled(stage.name, st.VALUE)
                + self.console.styled(f"  {duration(self.timeline.elapsed - stage.started)}", st.FAINT))
        self.live.update([head] + ["    " + line for line in self._activity])


def summary(console, timeline):
    """The block printed when the run ends: outcome, how the work divided, and where to look next."""
    out = [""]
    ok = timeline.ok and timeline.status in (None, "passed")
    kind = "passed" if ok else ("partial" if timeline.status == "partial" else "failed")
    label = {"passed": "RUN PASSED", "partial": "RUN PARTIAL", "failed": "RUN FAILED"}[kind]
    promoted = ("promoted" if timeline.promoted else "not promoted - previous dataset kept")
    out.append(" " + badge.pill(console, label, kind) + "  "
               + console.styled(promoted, st.OK if timeline.promoted else st.WARN)
               + console.styled(f"   {duration(timeline.elapsed)}", st.FAINT))
    out.append("")

    totals = timeline.sheet_totals
    parts = [(totals["extracted"], "ok"), (totals["reused"], "reused"),
             (totals["skipped"], "skip"), (totals["error"], "error")]
    if any(count for count, _ in parts):
        out.append("  " + bar.segmented(console, parts, size=min(40, console.width - 20)))
        out.append("  " + bar.legend(console, parts))
        out.append("")

    stage_lines = []
    for record in timeline.stages:
        stage_lines.append(
            "  " + badge.mark(console, theme.badge_kind(record.status)) + " "
            + console.styled(f"{record.name:<13}", st.VALUE)
            + console.styled(f"{duration(record.seconds):>8}", st.FAINT))
    if stage_lines:
        out.append(console.styled("  stages", st.LABEL))
        out += stage_lines
        out.append("")

    if timeline.rows_total:
        out.append(rule.kv(console, "rows extracted", f"{timeline.rows_total:,}"))
    if timeline.warnings:
        out.append(rule.kv(console, "warnings", console.paint(str(len(timeline.warnings)), "warn")))
    if timeline.errors or timeline.failed_stage:
        out.append("")
        out.append("  " + console.styled(f"{console.glyph('arrow')} run `xl2ai diagnose` "
                                         f"for the failure in context", st.ACCENT))
    return out

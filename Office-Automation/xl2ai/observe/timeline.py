"""Turn a stream of events into the shape a human asks about.

A flat log answers "what happened". This answers the questions actually asked after a run: which stages ran and
how long each took, which file produced which tables, how the work divided up, and where exactly it broke. Both
the live view and the after-the-fact diagnosis read this, so they can never disagree about the same run.
"""
from __future__ import annotations

from . import events as E


class StageRecord:
    __slots__ = ("name", "status", "started", "ended", "details", "warnings", "error", "artifacts")

    def __init__(self, name, started=0.0):
        self.name, self.started = name, started
        self.status, self.ended = "running", None
        self.details, self.warnings, self.artifacts = {}, [], []
        self.error = None

    @property
    def seconds(self):
        return None if self.ended is None else max(0.0, self.ended - self.started)

    @property
    def finished(self):
        return self.ended is not None


class SourceRecord:
    __slots__ = ("source_id", "path", "size", "sha256", "status", "sheets", "message", "seconds", "reused_from")

    def __init__(self, source_id, path=""):
        self.source_id, self.path = source_id, path
        self.size = self.sha256 = None
        self.status, self.message, self.reused_from = "pending", "", None
        self.seconds = None
        self.sheets = []


class SheetRecord:
    __slots__ = ("name", "status", "rows", "columns", "message", "seconds", "source_id")

    def __init__(self, name, source_id=""):
        self.name, self.source_id = name, source_id
        self.status, self.message = "running", ""
        self.rows = self.columns = 0
        self.seconds = None


class Timeline:
    """Fold events into records. Feed it with `timeline.consume` subscribed to a bus, or replay a journal."""

    def __init__(self):
        self.run_id = None
        self.project = None
        self.stages = []
        self.sources = {}
        self.warnings = []
        self.errors = []
        self.metrics = {}
        self.promoted = None
        self.status = None
        self.elapsed = 0.0
        self.events = 0

    # ---- ingestion ------------------------------------------------------------------------------------------
    def consume(self, event):
        self.events += 1
        self.elapsed = max(self.elapsed, event.elapsed)
        handler = _HANDLERS.get(event.kind)
        if handler:
            handler(self, event)
        if event.level == "warn" and event.kind not in (E.WARNING,):
            self.warnings.append(event)
        return event

    @classmethod
    def replay(cls, events):
        timeline = cls()
        for event in events:
            timeline.consume(event)
        return timeline

    def attach(self, bus):
        return bus.subscribe(self.consume)

    # ---- lookups --------------------------------------------------------------------------------------------
    def stage(self, name):
        for record in self.stages:
            if record.name == name:
                return record
        return None

    def source(self, source_id):
        if source_id not in self.sources:
            self.sources[source_id] = SourceRecord(source_id)
        return self.sources[source_id]

    @property
    def current_stage(self):
        for record in reversed(self.stages):
            if not record.finished:
                return record
        return None

    @property
    def failed_stage(self):
        for record in self.stages:
            if record.status in ("failed", "aborted", "interrupted"):
                return record
        return None

    @property
    def sheet_totals(self):
        """How the sheet-level work actually divided up, for the summary bar."""
        totals = {"extracted": 0, "skipped": 0, "error": 0, "reused": 0}
        for source in self.sources.values():
            if source.status == "reused":
                totals["reused"] += 1
            for sheet in source.sheets:
                if sheet.status in totals:
                    totals[sheet.status] += 1
        return totals

    @property
    def rows_total(self):
        return sum(sheet.rows for source in self.sources.values() for sheet in source.sheets)

    @property
    def ok(self):
        return not self.errors and self.failed_stage is None


# ---- per-event folding ---------------------------------------------------------------------------------------
def _run_start(tl, event):
    tl.run_id = event.data.get("run_id") or tl.run_id
    tl.project = event.data.get("project") or tl.project


def _run_end(tl, event):
    tl.status = event.data.get("status")
    tl.promoted = event.data.get("promoted")


def _stage_start(tl, event):
    tl.stages.append(StageRecord(event.data.get("name") or event.message, event.elapsed))


def _stage_end(tl, event):
    name = event.data.get("name") or event.message
    record = tl.stage(name) or StageRecord(name, event.elapsed)
    if record not in tl.stages:
        tl.stages.append(record)
    record.status = event.data.get("status", "passed")
    record.ended = event.elapsed
    if event.data.get("error"):
        record.error = event.data["error"]


def _stage_detail(tl, event):
    record = tl.current_stage or (tl.stages[-1] if tl.stages else None)
    if record is not None:
        record.details.update(event.data)


def _source_found(tl, event):
    record = tl.source(event.data.get("source_id", "?"))
    record.path = event.data.get("path", record.path)
    record.size = event.data.get("size", record.size)


def _source_hashed(tl, event):
    record = tl.source(event.data.get("source_id", "?"))
    record.sha256 = event.data.get("sha256", record.sha256)
    record.size = event.data.get("size", record.size)


def _source_reused(tl, event):
    record = tl.source(event.data.get("source_id", "?"))
    record.status = "reused"
    record.reused_from = event.data.get("from_run")
    record.message = event.message


def _source_extract_start(tl, event):
    record = tl.source(event.data.get("source_id", "?"))
    record.status = "running"
    record.path = event.data.get("path", record.path)


def _source_extract_end(tl, event):
    record = tl.source(event.data.get("source_id", "?"))
    record.status = event.data.get("status", "extracted")
    record.message = event.message or record.message
    record.seconds = event.data.get("seconds", record.seconds)


def _sheet_start(tl, event):
    source = tl.source(event.scope.get("source_id") or event.data.get("source_id", "?"))
    source.sheets.append(SheetRecord(event.data.get("name", "?"), source.source_id))


def _sheet_end(tl, event):
    source = tl.source(event.scope.get("source_id") or event.data.get("source_id", "?"))
    name = event.data.get("name", "?")
    record = next((s for s in reversed(source.sheets) if s.name == name), None)
    if record is None:
        record = SheetRecord(name, source.source_id)
        source.sheets.append(record)
    record.status = event.data.get("status", "extracted")
    record.rows = int(event.data.get("rows") or 0)
    record.columns = int(event.data.get("columns") or 0)
    record.seconds = event.data.get("seconds")
    record.message = event.message


def _artifact(tl, event):
    record = tl.current_stage
    if record is not None:
        record.artifacts.append(event.data.get("path") or event.message)


def _metric(tl, event):
    tl.metrics.update(event.data)


def _warning(tl, event):
    tl.warnings.append(event)
    record = tl.current_stage
    if record is not None:
        record.warnings.append(event.message)


def _error(tl, event):
    tl.errors.append(event)


_HANDLERS = {
    E.RUN_START: _run_start,
    E.RUN_END: _run_end,
    E.STAGE_START: _stage_start,
    E.STAGE_END: _stage_end,
    E.STAGE_DETAIL: _stage_detail,
    E.SOURCE_FOUND: _source_found,
    E.SOURCE_HASHED: _source_hashed,
    E.SOURCE_REUSED: _source_reused,
    E.SOURCE_EXTRACT_START: _source_extract_start,
    E.SOURCE_EXTRACT_END: _source_extract_end,
    E.SHEET_START: _sheet_start,
    E.SHEET_END: _sheet_end,
    E.ARTIFACT: _artifact,
    E.METRIC: _metric,
    E.WARNING: _warning,
    E.ERROR: _error,
}

"""The event vocabulary.

One fixed set of names, so a view, the journal and a diagnosis all mean the same thing by the same word. Adding a
name here is cheap; reusing one loosely is not, because the journal is a contract read later by tooling and by an
AI that was not present when the run happened.
"""
from __future__ import annotations

import time

# ---- run ------------------------------------------------------------------------------------------------
RUN_START = "run.start"
RUN_END = "run.end"
RUN_PROMOTED = "run.promoted"

# ---- stage ----------------------------------------------------------------------------------------------
STAGE_START = "stage.start"
STAGE_END = "stage.end"
STAGE_DETAIL = "stage.detail"

# ---- source files ---------------------------------------------------------------------------------------
SOURCE_FOUND = "source.found"
SOURCE_HASHED = "source.hashed"
SOURCE_REUSED = "source.reused"
SOURCE_EXTRACT_START = "source.extract.start"
SOURCE_EXTRACT_END = "source.extract.end"

# ---- sheets ---------------------------------------------------------------------------------------------
SHEET_START = "sheet.start"
SHEET_END = "sheet.end"

# ---- artifacts and outcomes ------------------------------------------------------------------------------
ARTIFACT = "artifact.written"
METRIC = "metric"
WARNING = "warning"
ERROR = "error"
NOTE = "note"

#: Levels used for filtering and for colouring, independent of the event name.
LEVELS = ("debug", "info", "warn", "error")


class Event:
    """One immutable fact. `data` carries whatever that kind of event needs; the journal stores it verbatim."""

    __slots__ = ("kind", "level", "message", "data", "wall", "elapsed", "scope")

    def __init__(self, kind, message="", level="info", scope=None, elapsed=0.0, data=None, wall=None):
        self.kind, self.level, self.message = kind, level, message
        self.scope = scope or {}
        self.data = data or {}
        self.elapsed = elapsed
        self.wall = wall if wall is not None else time.time()

    def as_dict(self):
        return {"t": round(self.elapsed, 4), "wall": round(self.wall, 3), "kind": self.kind,
                "level": self.level, "message": self.message, "scope": self.scope, "data": self.data}

    @classmethod
    def from_dict(cls, raw):
        return cls(kind=raw.get("kind", NOTE), message=raw.get("message", ""), level=raw.get("level", "info"),
                   scope=raw.get("scope") or {}, elapsed=float(raw.get("t") or 0.0),
                   data=raw.get("data") or {}, wall=raw.get("wall"))

    @property
    def failed(self):
        return self.level == "error"

    def __repr__(self):
        return f"Event({self.kind}, {self.level}, {self.message!r})"

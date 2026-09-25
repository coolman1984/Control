"""The event bus: the pipeline announces, subscribers listen.

Two rules make this safe to sprinkle through production code paths:
  1. A bus with no subscribers costs almost nothing, so emitting is never something to feel guilty about.
  2. A subscriber that raises is dropped, not propagated -- a broken renderer must never fail a data refresh.
"""
from __future__ import annotations

import time

from .events import Event


class Bus:
    def __init__(self):
        self._subscribers = []
        self._scope = {}
        self.started = time.monotonic()
        self.counts = {}                                  # per event kind
        self.levels = {}                                  # per severity, kept apart: a kind may be named "error" too

    # ---- wiring ---------------------------------------------------------------------------------------------
    def subscribe(self, handler):
        """Register `handler(event)`. Returns an unsubscribe callable."""
        self._subscribers.append(handler)
        return lambda: self._subscribers.remove(handler) if handler in self._subscribers else None

    def reset(self):
        self._subscribers.clear()
        self._scope.clear()
        self.counts.clear()
        self.levels.clear()
        self.started = time.monotonic()

    # ---- scope ----------------------------------------------------------------------------------------------
    def scope(self, **fields):
        """Attach context (run id, stage, source) to every event emitted inside the `with` block.

        This is why a failure can be explained later: the event that broke carries where it was, without every
        emit site having to remember to say so.
        """
        return _Scope(self, fields)

    # ---- emitting -------------------------------------------------------------------------------------------
    def emit(self, kind, message="", level="info", **data):
        event = Event(kind=kind, message=message, level=level, scope=dict(self._scope),
                      elapsed=time.monotonic() - self.started, data=data)
        self.counts[kind] = self.counts.get(kind, 0) + 1
        self.levels[level] = self.levels.get(level, 0) + 1
        for handler in list(self._subscribers):
            try:
                handler(event)
            except Exception:                             # never let a renderer or log sink break the pipeline
                pass
        return event

    def info(self, kind, message="", **data):
        return self.emit(kind, message, "info", **data)

    def warn(self, kind, message="", **data):
        return self.emit(kind, message, "warn", **data)

    def error(self, kind, message="", **data):
        return self.emit(kind, message, "error", **data)


class _Scope:
    def __init__(self, bus, fields):
        self.bus, self.fields, self.previous = bus, fields, None

    def __enter__(self):
        self.previous = dict(self.bus._scope)
        self.bus._scope.update({k: v for k, v in self.fields.items() if v is not None})
        return self.bus

    def __exit__(self, *exc):
        self.bus._scope.clear()
        self.bus._scope.update(self.previous)
        return False


#: The process-wide bus. Stages use this; tests build their own instead of touching it.
BUS = Bus()

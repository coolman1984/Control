"""The journal: every event of a run, appended as JSON Lines.

This is the file you hand to an AI (or read yourself) when something failed and the terminal has scrolled away.
JSON Lines specifically, because it survives a process that is killed mid-run: every line already written stays
valid and parseable, which is exactly the situation where the log matters most.
"""
from __future__ import annotations

import json
import os

from .events import Event

FILENAME = "journal.jsonl"


class Journal:
    """Append-only writer. Failures to write are swallowed: losing a log line must never fail a refresh."""

    def __init__(self, path):
        self.path = path
        self._handle = None
        self.broken = False

    def open(self):
        if self._handle or self.broken:
            return self
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            self._handle = open(self.path, "a", encoding="utf-8", newline="\n")
        except OSError:
            self.broken = True
        return self

    def write(self, event):
        self.open()
        if not self._handle:
            return
        try:
            self._handle.write(json.dumps(event.as_dict(), ensure_ascii=False, default=str) + "\n")
            self._handle.flush()                          # a crash must not cost the lines leading up to it
        except (OSError, ValueError):
            self.broken = True

    def close(self):
        if self._handle:
            try:
                self._handle.close()
            except OSError:
                pass
            self._handle = None

    def __enter__(self):
        return self.open()

    def __exit__(self, *exc):
        self.close()
        return False

    def attach(self, bus):
        """Subscribe this journal to a bus and return the unsubscribe callable."""
        return bus.subscribe(self.write)


def path_for_run(run_dir):
    return os.path.join(run_dir, FILENAME)


def read(path, limit=None):
    """Load events back. Corrupt or half-written lines are skipped, never fatal: a truncated tail is normal when
    the process was killed, and the lines before it are still the evidence we came for."""
    events = []
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(Event.from_dict(json.loads(line)))
                except (json.JSONDecodeError, TypeError, AttributeError):
                    continue
    except OSError:
        return []
    return events[-limit:] if limit else events

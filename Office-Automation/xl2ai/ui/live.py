"""A live region: a block of lines that is redrawn in place while work runs.

The contract that makes this safe: every line is truncated to the terminal width before it is written, so one
logical line is always exactly one physical row and the cursor arithmetic for the redraw stays exact. On a
non-interactive terminal (a pipe, a file, CI) the region degrades to ordinary append-only output, because a log
full of cursor escapes helps nobody -- which is also what makes this usable for the AI-facing journal.
"""
from __future__ import annotations

import time

from .measure import truncate

HIDE_CURSOR, SHOW_CURSOR = "\x1b[?25l", "\x1b[?25h"
CLEAR_LINE = "\x1b[2K"
UP = "\x1b[{n}A"


class Live:
    def __init__(self, console, min_interval=0.06):
        self.console = console
        self.min_interval = min_interval
        self._drawn = 0
        self._last = 0.0
        self._open = False

    # ---- lifecycle ------------------------------------------------------------------------------------------
    def __enter__(self):
        self._open = True
        if self.console.interactive:
            self.console.write(HIDE_CURSOR)
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def close(self, keep=None):
        """Finish the region. `keep` replaces the live block with a final, permanent rendering."""
        if not self._open:
            return
        if self.console.interactive:
            if keep is not None:
                self._erase()
                for line in keep:
                    self.console.line(truncate(line, self.console.width))
                self._drawn = 0
            self.console.write(SHOW_CURSOR)
        elif keep is not None:
            for line in keep:
                self.console.line(line)
        self._open = False

    # ---- drawing --------------------------------------------------------------------------------------------
    def update(self, lines, force=False):
        """Redraw the region. Throttled, so a fast event stream cannot turn into a flickering screen."""
        now = time.monotonic()
        if not force and now - self._last < self.min_interval:
            return False
        self._last = now
        if not self.console.interactive:
            return False                                  # append-only sinks get permanent lines via `emit`
        self._erase()
        for line in lines:
            self.console.line(truncate(line, self.console.width))
        self._drawn = len(lines)
        return True

    def emit(self, lines):
        """Write permanent lines above the live region: the scrollback history of what already finished."""
        if not self.console.interactive:
            for line in lines:
                self.console.line(line)
            return
        self._erase()
        for line in lines:
            self.console.line(truncate(line, self.console.width))
        self._drawn = 0

    def _erase(self):
        """Clear the drawn block and leave the cursor back at its first row, ready to be rewritten."""
        if not self._drawn:
            return
        count = self._drawn
        self.console.write(UP.format(n=count))            # up to the first row of the region
        for _ in range(count):
            self.console.write(CLEAR_LINE + "\n")         # wipe each row, stepping down
        self.console.write(UP.format(n=count))            # back to the top of the now-empty region
        self._drawn = 0

"""The single place anything is written to the screen.

Holding one object means capability detection, width and the glyph set are decided once per command instead of
being rediscovered (and disagreed about) by every view. It is also the seam tests write through: give a Console a
buffer and `Caps.plain()` and the whole UI becomes a deterministic string.
"""
from __future__ import annotations

import sys

from .caps import Caps
from .glyphs import Glyphs
from . import style as st


class Console:
    def __init__(self, stream=None, caps=None):
        self.stream = stream if stream is not None else sys.stdout
        self.caps = caps if caps is not None else Caps.detect(self.stream)
        self.glyph = Glyphs.for_caps(self.caps)

    # ---- capability shortcuts -------------------------------------------------------------------------------
    @property
    def width(self):
        return self.caps.width

    @property
    def interactive(self):
        return self.caps.interactive

    @classmethod
    def capture(cls, width=100):
        """A Console that renders into a string buffer with no colour: used by tests and by `--plain`."""
        import io
        return cls(stream=io.StringIO(), caps=Caps.plain(width))

    @property
    def captured(self):
        return self.stream.getvalue()

    # ---- writing --------------------------------------------------------------------------------------------
    def write(self, text=""):
        self.stream.write(text)
        self._flush()

    def line(self, text=""):
        self.stream.write(str(text) + "\n")
        self._flush()

    def lines(self, items):
        for item in items:
            self.line(item)

    def blank(self, count=1):
        for _ in range(count):
            self.line("")

    def styled(self, text, style):
        """Apply a style with this console's capabilities; the one call views use to colour anything."""
        return style(text, self.caps)

    def paint(self, text, name, **attrs):
        """Colour by semantic name: `console.paint('failed', 'error', bold=True)`."""
        return st.of(name, **attrs)(text, self.caps)

    def _flush(self):
        try:
            self.stream.flush()
        except Exception:                                 # a closed or non-flushable sink must not break output
            pass

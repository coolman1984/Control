"""A fake wad.py for tests: only `record <out> [--window W] [--seconds S]` and `record-stop`,
just enough for control/areas/record.py's subprocess adapter to exercise against a real process
without needing an actual Windows desktop or the real win-agent-desktop package installed."""
import json
import sys


def main(argv):
    if not argv:
        return 2
    cmd, rest = argv[0], argv[1:]
    if cmd == "record":
        if not rest:
            return 2
        out = rest[0]
        window = None
        i = 1
        while i < len(rest):
            if rest[i] == "--window" and i + 1 < len(rest):
                window = rest[i + 1]
                i += 2
            else:
                i += 1
        steps = [
            {"cmd": "click", "target": "role=Button name=Save", "window": window or "Notepad"},
            {"cmd": "type", "target": "role=Edit", "window": window or "Notepad", "text": "hello world"},
            {"cmd": "focus", "target": "role=Edit name=Password", "window": window or "Notepad"},
            {"cmd": "type", "target": "role=Edit name=Password", "window": window or "Notepad",
             "text": "${ENV:WAD_SECRET}"},
            {"cmd": "select", "target": "role=ComboBox", "window": window or "Notepad", "option": "Yes"},
            {"cmd": "press", "combo": "ctrl+s", "window": window or "Notepad"},
        ]
        with open(out, "w", encoding="utf-8") as fh:
            json.dump({"steps": steps, "recorded": "2026-09-26T00:00:00"}, fh)
        return 0
    if cmd == "record-stop":
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

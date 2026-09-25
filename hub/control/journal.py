"""One append-only record of every change Control made, with how to reverse it."""
import hashlib
import json
import os
import re
import secrets
import time
from pathlib import Path

from . import config
from .errors import ControlError

SECRET = re.compile(r"pass|pwd|secret|token|key|credential", re.I)
FREE_TEXT_KEYS = {"text", "keys", "value"}


def _redact_value(v):
    s = str(v)
    digest = hashlib.sha256(s.encode("utf-8", errors="surrogatepass")).hexdigest()[:8]
    return f"<{len(s)} chars, sha256:{digest}>"


def _is_free_text_action(action):
    return action is not None and (action.endswith(".type") or action in ("win.clipboard", "win.press"))


def mask(args, action=None):
    """Recursively mask secret-looking keys at any depth. For actions that type or move raw
    keystrokes/clipboard text (name ending .type, or win.clipboard / win.press), also replace the
    free-text value itself - not just secret-named keys - so it never reaches the journal."""
    free_text = _is_free_text_action(action)

    def walk(value, key=None):
        if isinstance(value, dict):
            return {k: walk(v, k) for k, v in value.items()}
        if isinstance(value, list):
            return [walk(v, key) for v in value]
        if key is not None and SECRET.search(str(key)):
            return "***"
        if free_text and key in FREE_TEXT_KEYS and isinstance(value, str):
            return _redact_value(value)
        return value

    return walk(dict(args or {}))


class Journal:
    def __init__(self, path):
        self.path = Path(path)

    def _write(self, record):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

    def append(self, *, action, args, tier, approval, ok, code=None, ms=0, undo=None):
        eid = time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)
        self._write({"id": eid, "time": time.strftime("%Y-%m-%dT%H:%M:%S"), "action": action,
                     "args": mask(args, action), "tier": tier, "approval": approval, "ok": ok,
                     "code": code, "ms": ms, "undo": undo,
                     "approval_off": os.environ.get("CONTROL_APPROVAL") == "off"})
        return eid

    def mark_undone(self, eid):
        self._write({"undoes": eid, "time": time.strftime("%Y-%m-%dT%H:%M:%S")})

    def entries(self):
        if not self.path.exists():
            return []
        out, undone = [], set()
        with open(self.path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if "undoes" in rec:
                    undone.add(rec["undoes"])
                elif "id" in rec:
                    out.append(rec)
        for rec in out:
            rec["undone"] = rec["id"] in undone
        return out

    def last(self, n=20):
        return self.entries()[-n:]

    def get(self, eid):
        for rec in self.entries():
            if rec["id"] == eid:
                return rec
        raise ControlError("NOT_FOUND", f"no journal entry {eid!r}", "list them with control.journal")


def default():
    return Journal(config.home() / "journal.jsonl")

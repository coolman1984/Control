"""Stable error codes (see DATA_CONTRACT.md section 6). Every user-facing platform error carries one."""
from __future__ import annotations


class Xl2aiError(Exception):
    def __init__(self, code, message, hint=""):
        super().__init__(f"{code}: {message}")
        self.code, self.message, self.hint = code, message, hint

    def as_dict(self):
        return {"code": self.code, "message": self.message, "hint": self.hint}

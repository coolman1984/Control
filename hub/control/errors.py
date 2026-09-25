"""The one failure shape: a stable code, what happened, what to do next."""


class ControlError(Exception):
    def __init__(self, code, message, hint=""):
        super().__init__(message)
        self.code, self.message, self.hint = code, message, hint

    def payload(self):
        return {"ok": False, "code": self.code, "message": self.message, "hint": self.hint}

    def text(self):
        return f"ERROR {self.code}: {self.message}" + (f"\n  hint: {self.hint}" if self.hint else "")

"""`{{ ... }}` template resolution over a run's context — deliberately not a real expression
language: no `eval`, no arbitrary code, just dotted/indexed lookups into `input`, `steps` and
`vars`. That keeps a malicious or broken flow file from doing anything beyond reading values the
engine already computed, which matters because flow files can be authored by an agent."""
import re

from ..errors import ControlError

_TOKEN = re.compile(r"\{\{\s*(.*?)\s*\}\}")
_PATH = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*|\[\d+\])*$")
_SECRET = re.compile(r"^secret:([A-Za-z0-9_.-]+)$")


class SecretRef:
    """A `{{ secret:name }}` that resolved to *a reference*, not the value: the flow engine swaps
    it for the real value from the vault only immediately before the real call (control/flow/
    engine.py), never here — so a secret can never appear in flow.describe/dry_run output, which
    an agent (and so a model) can read."""
    __slots__ = ("name",)

    def __init__(self, name):
        self.name = name

    def __eq__(self, other):
        return isinstance(other, SecretRef) and other.name == self.name

    def __hash__(self):
        return hash(("SecretRef", self.name))

    def __repr__(self):
        return f"<secret:{self.name}>"


def _lookup(path, ctx):
    if not _PATH.match(path):
        raise ControlError("EXPR_INVALID", f"not a valid path: {path!r}",
                            "paths look like steps.report.manifest.folder or input.date")
    parts = re.findall(r"[A-Za-z_][A-Za-z0-9_]*|\[\d+\]", path)
    value = ctx
    walked = []
    for part in parts:
        walked.append(part)
        try:
            if part.startswith("["):
                value = value[int(part[1:-1])]
            else:
                value = value[part] if isinstance(value, dict) else getattr(value, part)
        except (KeyError, IndexError, TypeError, AttributeError):
            raise ControlError("EXPR_UNRESOLVED", f"{'.'.join(walked)} has no value yet",
                                "check the step id and field name, or that an earlier step ran") from None
    return value


def resolve_template(s, ctx):
    """A string that is *exactly* one `{{ ... }}` returns the raw resolved value (may be a dict,
    list, number, bool). A string with a template embedded in more text gets that piece
    stringified in place. A string with no template is returned unchanged."""
    stripped = s.strip()
    whole = _TOKEN.fullmatch(stripped)
    if whole and stripped == s:
        inner = whole.group(1)
        secret = _SECRET.match(inner)
        if secret:
            return SecretRef(secret.group(1))
        return _lookup(inner, ctx)

    def sub(m):
        inner = m.group(1)
        if _SECRET.match(inner):
            raise ControlError("EXPR_INVALID", f"{{{{ {inner} }}}} must be the whole argument value",
                                "a secret can't be pasted into the middle of a larger string")
        value = _lookup(inner, ctx)
        return value if isinstance(value, str) else repr(value) if value is None else str(value)

    return _TOKEN.sub(sub, s)


def resolve(value, ctx):
    """Walk a step's `args`/`vars` structure, resolving every string that contains a template."""
    if isinstance(value, str):
        return resolve_template(value, ctx) if "{{" in value else value
    if isinstance(value, dict):
        return {k: resolve(v, ctx) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve(v, ctx) for v in value]
    return value


FALSY = (False, None, 0, "", (), [], {})


def truthy(value):
    if isinstance(value, (list, dict, tuple)):
        return len(value) > 0
    return value not in FALSY

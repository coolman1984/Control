"""`{{ ... }}` template resolution over a run's context — deliberately not a real expression
language: no `eval`, no arbitrary code, just dotted/indexed lookups into `input`, `steps` and
`vars`. That keeps a malicious or broken flow file from doing anything beyond reading values the
engine already computed, which matters because flow files can be authored by an agent."""
import re

from ..errors import ControlError

_TOKEN = re.compile(r"\{\{\s*(.*?)\s*\}\}")
_PATH = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*|\[\d+\])*$")


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
        return _lookup(whole.group(1), ctx)

    def sub(m):
        value = _lookup(m.group(1), ctx)
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

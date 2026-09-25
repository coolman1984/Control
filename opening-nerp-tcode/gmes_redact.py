"""One source of truth for what looks like a credential (CLAUDE.md 2.2/2.3).

Used by `gmes_log.py` (console/log redaction) and `gmes_data.py` (CSV export
column filtering) - previously two independent copies of the same short word
list (HISTORY.md Open Item 39), which could drift when only one was updated,
and both were missing plausible real names: `credential`, `sessionKey`,
`sessionId`, `jwt`, `apiKey`, `accessKey`, `authKey`, `bearer`.
"""
import re

# A short word is enough to match deliberately, by substring, not by whole
# word - CLAUDE.md 2.3's own example, `refreshTokenId`, is already caught by
# the bare "token" here with no separate "refreshtoken"/"accesstoken" entry
# needed. Being over-inclusive costs nothing worse than an ordinary value
# being withheld from a CSV or masked in a log line nobody needed unmasked.
SENSITIVE_WORDS = (
    "password", "passwd", "pwd", "token", "secret", "authorization", "cookie",
    "credential", "session", "jwt", "apikey", "accesskey", "authkey", "bearer",
)

_ALTERNATION = "|".join(SENSITIVE_WORDS)
NAME_PATTERN = re.compile(f"(?i)({_ALTERNATION})")
# An assignment-shaped occurrence in free text (HISTORY.md Phase 92.1):
#   group 1  the NAME - the sensitive word plus the rest of its identifier, so
#            `tokenId=`, `refreshTokenId:` and `sessionKey=` all count (the
#            first version stopped at the bare word and missed every one -
#            CLAUDE.md 2.3's own `tokenId` example passed unmasked);
#   group 2  an optional closing quote on the name, for JSON / dict shapes
#            (`"password": "x"`, `{'password': 'x'}`);
#   group 3  the separator: `:` or `=` only. Plain whitespace used to count,
#            which masked ordinary words - "the SSO session timed out" was
#            logged as "the SSO session *** out";
#   then the VALUE: `Bearer <x>`, a quoted string (spaces and all), or a bare
#   run up to the next space or delimiter.
TEXT_PATTERN = re.compile(
    rf"(?i)((?:{_ALTERNATION})[\w.-]*)(['\"]?)(\s*[:=]\s*)"
    r"(?:bearer\s+[^\s,;'\"}\]]+|\"[^\"]*\"|'[^']*'|[^\s,;&'\"}\])]+)")
# A JSON Web Token is recognisable by its value alone (three base64url parts,
# the first two starting `eyJ` = `{"`), whatever the name beside it says -
# or with no name at all, as in a bare `Authorization: Bearer <jwt>` header
# or a token pasted into an error message.
JWT_PATTERN = re.compile(r"\beyJ[\w-]{5,}\.eyJ[\w-]{5,}\.[\w-]*")
BEARER_PATTERN = re.compile(r"(?i)\b(bearer\s+)[^\s,;'\"}\]]+")


def is_sensitive_name(name):
    """Whether a column/field NAME alone looks credential-shaped."""
    return bool(NAME_PATTERN.search(name or ""))


def redact_text(text, placeholder="***"):
    """Mask secret-shaped values in free text: `name=value`, `"name": "value"`,
    `Bearer <x>`, and any JWT-shaped value wherever it appears. The name and
    the separator are kept, so a log line still says WHAT was hidden."""
    text = TEXT_PATTERN.sub(
        lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}{placeholder}", text or "")
    text = BEARER_PATTERN.sub(lambda m: f"{m.group(1)}{placeholder}", text)
    return JWT_PATTERN.sub(placeholder, text)

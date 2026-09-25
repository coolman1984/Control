"""The mapping from xl2ai vocabulary to visual language.

Kept in one file so the whole tool stays consistent: a "partial" stage looks the same in the live run view, the
files view and the diagnosis, because all three ask this module rather than each choosing a colour.
"""
from __future__ import annotations

from ..ui import style as st

#: Stage/source/sheet status -> the badge kind understood by `ui.badge`.
STATUS_BADGE = {
    "passed": "passed", "running": "running", "pending": "pending", "queued": "pending",
    "partial": "partial", "failed": "failed", "aborted": "aborted", "interrupted": "interrupted",
    "extracted": "extracted", "skipped": "skipped", "reused": "reused", "error": "error",
    "pass": "pass", "fail": "fail", "warn": "warn", "ok": "ok",
}

#: What each stage is actually for, shown beside its name so the run explains itself while it runs.
STAGE_PURPOSE = {
    "sources": "find and fingerprint the workbooks",
    "extract": "read Excel into verified SQLite",
    "catalog": "give every table and column a stable identity",
    "analyze": "profile columns, flag quality issues, infer keys",
    "semantics": "infer column roles, table grain, time coverage and draft definitions",
    "repair": "suggest opt-in cleanup fixes, applied to nothing but _repairs",
    "rules": "apply pack knowledge, rules and KPIs",
    "relations": "infer relationships between tables",
    "anomalies": "flag outliers, odd months, rare negatives and impossible dates",
    "reconcile": "check report tables against the raw data they summarise",
    "links": "link codes, e-mails and phones in documents to the table rows that hold them",
    "digest": "pre-compute key numbers: totals, biggest groups, monthly trends",
    "changes": "compare against the previous trusted run",
    "audit": "verify the run's artifacts agree with each other",
    "contextpack": "build the compact AI context pack",
    "agent_brief": "write the plain-language agent brief",
    "report": "write the human-readable summary",
}

#: Error code -> what it means in plain words and what to actually do about it.
ERROR_HELP = {
    "E_CONFIG": ("The project configuration is not valid.",
                 "Check xl2ai.toml: a key is unknown, mistyped or the wrong type."),
    "E_SRC_MISSING": ("A configured source file was not found.",
                      "Check the path in xl2ai.toml. Relative paths resolve against the config file."),
    "E_LOCKED": ("Another refresh holds the project lock.",
                 "Wait for it to finish. Never delete the lock while its owner process is alive."),
    "E_EXTRACT": ("Excel could not open or read a workbook.",
                  "Open the file in Excel yourself: it may be password protected, corrupt or already open."),
    "E_VERIFY_MISMATCH": ("Stored data does not match what Excel reports.",
                          "This blocks promotion on purpose. Inspect the _verification table in that database."),
    "E_RULE": ("A rule pack failed to load or a rule could not execute.",
               "Check the pack's SQL and table selectors with `xl2ai rules`."),
    "E_AUDIT": ("The run's own artifacts disagree with each other.",
                "Run `xl2ai audit --deep` for the full check."),
    "E_STAGE_INPUT": ("A stage was missing an input an earlier stage should have produced.",
                      "Look at the first failed stage above: this is usually a symptom, not the cause."),
    "E_NOT_ALLOWED": ("The operation is blocked by the read-only query guard.",
                      "Query tools accept a single read-only SELECT."),
}

#: Severity -> style, for anything free-form.
LEVEL_STYLE = {"debug": st.FAINT, "info": st.MUTED, "warn": st.WARN, "error": st.ERROR}


def stage_purpose(name):
    return STAGE_PURPOSE.get(name, "")


def badge_kind(status):
    return STATUS_BADGE.get(str(status).lower(), str(status).lower())


def explain(code):
    """(meaning, what to do) for an error code, or a neutral fallback for one we have no guidance for."""
    return ERROR_HELP.get(code, ("", ""))


def level_style(level):
    return LEVEL_STYLE.get(level, st.MUTED)

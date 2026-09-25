"""A simulated run.

Extraction needs Windows and Excel, which makes the interface impossible to see (or review, or regression-test)
anywhere else. This replays a realistic event stream through the exact same bus, view and journal the real
pipeline uses -- so what you see here is what a real run looks like, not a mock-up of it.
"""
from __future__ import annotations

import time

from ..observe import events as E
from ..observe.bus import Bus

SOURCES = [
    ("orders-4f2a91c7", "C:/Data/Orders.xlsx", 2_412_544, [
        ("Orders", "extracted", 128_430, 18, 2.4),
        ("Order Lines", "extracted", 512_004, 24, 6.1),
        ("Lookup", "extracted", 84, 3, 0.2),
        ("Notes", "skipped", 0, 0, 0.1),
    ]),
    ("customers-9b71ee02", "C:/Data/Customers.xlsx", 845_120, [
        ("Customers", "extracted", 9_812, 22, 1.1),
        ("Regions", "extracted", 41, 4, 0.1),
    ]),
    ("prices-1c8d4a55", "C:/Data/Price List.xlsb", 18_903_552, [
        ("Price List", "extracted", 1_204_881, 46, 21.7),
        ("Archive 2024", "skipped", 0, 0, 0.3),
    ]),
]

STAGES = [
    ("sources", {"count": 3}),
    ("extract", {"sources": 3, "reused": 1}),
    ("catalog", {"tables": 8}),
    ("analyze", {"quality_findings": 14, "candidate_keys": 5}),
    ("semantics", {"roles_classified": 22, "tables_with_unknown_grain": 1, "duplicate_candidates": 0}),
    ("repair", {"repair_suggestions": 0}),
    ("rules", {"failed_rules": 1, "rule_errors": 0}),
    ("relations", {"relationships": 3}),
    ("digest", {"tables_summarised": 5}),
    ("anomalies", {"anomalies": 4}),
    ("links", {"documents": 3, "document_links": 7}),
    ("reconcile", {"report_columns_checked": 6, "report_columns_disagreeing": 1}),
    ("changes", {"changes": 27}),
    ("audit", {"checks": 46, "issues": 0}),
    ("contextpack", {"est_tokens": 3820}),
    ("agent_brief", {}),
    ("report", {}),
]


def play(bus, speed=1.0, fail_at=None):
    """Emit a full run onto `bus`. `speed` scales the pauses; `fail_at` names a stage to break, for the
    failure path (which is the path the interface has to be good at)."""
    def rest(seconds):
        if speed > 0:
            time.sleep(seconds * speed)

    bus.emit(E.RUN_START, "run started", run_id="20260922T031500-a4f1", project="Sales")
    rest(0.15)

    for name, details in STAGES:
        with bus.scope(stage=name):
            bus.emit(E.STAGE_START, name, name=name)
            rest(0.1)

            if name == "sources":
                for source_id, path, size, _ in SOURCES:
                    bus.emit(E.SOURCE_FOUND, source_id, source_id=source_id, path=path, size=size)
                    rest(0.05)
                    bus.emit(E.SOURCE_HASHED, source_id, source_id=source_id, size=size,
                             sha256="9f2c" + source_id[-8:] * 2)
                    rest(0.05)

            elif name == "extract":
                for index, (source_id, path, size, sheets) in enumerate(SOURCES):
                    with bus.scope(source_id=source_id):
                        if index == 1:
                            bus.emit(E.SOURCE_REUSED, "unchanged source; reused previous trusted extraction",
                                     source_id=source_id, from_run="20260921T190200-77bc")
                            rest(0.12)
                            continue
                        bus.emit(E.SOURCE_EXTRACT_START, source_id, source_id=source_id, path=path)
                        rest(0.1)
                        for sheet, status, rows, columns, seconds in sheets:
                            bus.emit(E.SHEET_START, sheet, name=sheet)
                            rest(0.12)
                            message = "" if status == "extracted" else "no usable columns"
                            bus.emit(E.SHEET_END, message, name=sheet, status=status,
                                     rows=rows, columns=columns, seconds=seconds)
                            rest(0.05)
                        bus.emit(E.SOURCE_EXTRACT_END, "", source_id=source_id, status="extracted",
                                 seconds=sum(s[4] for s in sheets))
                        rest(0.05)

            elif name == "analyze":
                bus.warn(E.WARNING, "Price List.c31: at least half the column names were generated")
                rest(0.1)

            elif name == "rules":
                bus.warn(E.WARNING, "margin_never_negative: 3 row(s) failed")
                rest(0.1)

            for key, value in details.items():
                bus.emit(E.STAGE_DETAIL, "", **{key: value})

            if fail_at == name:
                bus.error(E.ERROR, "stored data differs from Excel for: prices-1c8d4a55",
                          code="E_VERIFY_MISMATCH",
                          hint="see the _verification table in that database")
                bus.emit(E.STAGE_END, name, name=name, status="failed",
                         error={"code": "E_VERIFY_MISMATCH",
                                "message": "stored data differs from Excel for: prices-1c8d4a55",
                                "hint": "see the _verification table in that database"})
                # The real pipeline stops here: later stages need this stage's output, so they never start.
                bus.emit(E.RUN_END, "run failed", status="failed", promoted=False)
                return

            rest(0.1)
            bus.emit(E.STAGE_END, name, name=name, status="passed")

    bus.emit(E.RUN_PROMOTED, "promoted", run_id="20260922T031500-a4f1")
    bus.emit(E.RUN_END, "run passed", status="passed", promoted=True)


def build_timeline(fail_at=None):
    """Replay the demo with no pauses and return (timeline, events): used by tests and by the static views."""
    from ..observe.timeline import Timeline
    bus, timeline, collected = Bus(), Timeline(), []
    bus.subscribe(collected.append)
    timeline.attach(bus)
    play(bus, speed=0, fail_at=fail_at)
    return timeline, collected

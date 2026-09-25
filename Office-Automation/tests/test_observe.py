"""Event bus, journal and timeline tests.

The journal is what an AI reads when a run failed and nobody was watching, so the properties that matter are:
it survives a killed process, a broken subscriber never breaks the pipeline, and replaying a journal reconstructs
exactly the same picture the live view had.
"""
import json
import os
import tempfile
import unittest

from xl2ai.console import demo, diagnose
from xl2ai.observe import events as E, journal as journal_mod
from xl2ai.observe.bus import Bus
from xl2ai.observe.timeline import Timeline
from xl2ai.ui.console import Console


class TestBus(unittest.TestCase):
    def test_scope_is_attached_and_restored(self):
        bus, seen = Bus(), []
        bus.subscribe(seen.append)
        with bus.scope(stage="extract"):
            with bus.scope(source_id="s1"):
                bus.emit(E.SHEET_END, "inner")
            bus.emit(E.STAGE_END, "outer")
        bus.emit(E.NOTE, "outside")
        self.assertEqual(seen[0].scope, {"stage": "extract", "source_id": "s1"})
        self.assertEqual(seen[1].scope, {"stage": "extract"})
        self.assertEqual(seen[2].scope, {})

    def test_a_broken_subscriber_never_breaks_the_caller(self):
        bus, seen = Bus(), []

        def explode(event):
            raise RuntimeError("renderer is broken")

        bus.subscribe(explode)
        bus.subscribe(seen.append)
        bus.emit(E.NOTE, "still delivered")
        self.assertEqual(len(seen), 1)

    def test_levels_are_counted(self):
        bus = Bus()
        bus.warn(E.WARNING, "w")
        bus.error(E.ERROR, "e")
        self.assertEqual(bus.levels["warn"], 1)
        self.assertEqual(bus.levels["error"], 1)
        self.assertEqual(bus.counts[E.ERROR], 1)          # the kind counter must not be polluted by the level


class TestJournal(unittest.TestCase):
    def test_round_trip_preserves_events(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "journal.jsonl")
            bus = Bus()
            with journal_mod.Journal(path) as journal:
                journal.attach(bus)
                bus.emit(E.RUN_START, "started", run_id="r1", project="p")
                with bus.scope(stage="extract"):
                    bus.error(E.ERROR, "boom", code="E_EXTRACT")
            events = journal_mod.read(path)
            self.assertEqual(len(events), 2)
            self.assertEqual(events[0].data["run_id"], "r1")
            self.assertEqual(events[1].level, "error")
            self.assertEqual(events[1].scope["stage"], "extract")

    def test_a_truncated_tail_does_not_lose_the_earlier_lines(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "journal.jsonl")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(json.dumps({"kind": "run.start", "message": "ok", "t": 0}) + "\n")
                handle.write('{"kind": "sheet.end", "mes')      # killed mid-write
            events = journal_mod.read(path)
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0].kind, "run.start")

    def test_an_unwritable_path_is_not_fatal(self):
        # A regular file standing where a directory should be fails for every user, including root.
        with tempfile.TemporaryDirectory() as root:
            blocker = os.path.join(root, "not-a-directory")
            with open(blocker, "w", encoding="utf-8") as handle:
                handle.write("x")
            journal = journal_mod.Journal(os.path.join(blocker, "journal.jsonl"))
            journal.write(E.Event(E.NOTE, "ignored"))
            self.assertTrue(journal.broken)


class TestTimeline(unittest.TestCase):
    def test_demo_run_folds_into_the_expected_shape(self):
        timeline, events = demo.build_timeline()
        self.assertEqual(timeline.run_id, "20260922T031500-a4f1")
        self.assertEqual(timeline.project, "Sales")
        self.assertTrue(timeline.promoted)
        self.assertTrue(timeline.ok)
        self.assertEqual(len(timeline.stages), 17)
        self.assertEqual(len(timeline.sources), 3)
        totals = timeline.sheet_totals
        self.assertEqual(totals["reused"], 1)
        self.assertEqual(totals["extracted"], 4)
        self.assertEqual(totals["skipped"], 2)
        self.assertGreater(timeline.rows_total, 1_000_000)

    def test_failure_stops_the_pipeline_and_is_located(self):
        timeline, events = demo.build_timeline(fail_at="extract")
        self.assertFalse(timeline.ok)
        self.assertEqual(timeline.failed_stage.name, "extract")
        self.assertFalse(timeline.promoted)
        # The real pipeline breaks out of the stage loop, so nothing after extract should have started.
        self.assertEqual([s.name for s in timeline.stages], ["sources", "extract"])

    def test_replaying_a_journal_reproduces_the_live_picture(self):
        live, events = demo.build_timeline(fail_at="rules")
        replayed = Timeline.replay(events)
        self.assertEqual(replayed.run_id, live.run_id)
        self.assertEqual([s.status for s in replayed.stages], [s.status for s in live.stages])
        self.assertEqual(replayed.rows_total, live.rows_total)


class TestDiagnosis(unittest.TestCase):
    def test_root_cause_points_at_the_first_failure(self):
        timeline, events = demo.build_timeline(fail_at="extract")
        error, stage = diagnose.root_cause(timeline, events)
        self.assertEqual(stage.name, "extract")
        self.assertEqual(error.data.get("code"), "E_VERIFY_MISMATCH")

    def test_ai_text_states_the_code_and_asks_a_question(self):
        timeline, events = demo.build_timeline(fail_at="extract")
        text = diagnose.as_text(timeline, events)
        self.assertIn("E_VERIFY_MISMATCH", text)
        self.assertIn("## first failure", text)
        self.assertIn("## what I need", text)
        self.assertNotIn("\x1b", text)

    def test_clean_run_reports_no_failure(self):
        timeline, events = demo.build_timeline()
        console = Console.capture(90)
        rendered = "\n".join(diagnose.render(console, timeline, events))
        self.assertIn("No failure recorded", rendered)


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""Offline tests for the G-MES interactive summary renderer."""
import contextlib
import io
import os
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gmes_ui as ui  # noqa: E402
import run_gmes_workflow as workflow  # noqa: E402
import gmes_library as library  # noqa: E402



class LastNightOnStartUp(unittest.TestCase):
    """HISTORY.md Phase 92.7: the start-up screen names last night's result."""

    def test_nothing_run_yet_says_nothing(self):
        with mock.patch.object(workflow.gmes_batch, "last_summary", return_value=None):
            self.assertIsNone(workflow.last_batch_line())

    def test_a_good_night_and_a_bad_one(self):
        good = {"started": "2026-09-23 02:00:00", "total": 3,
                "counts": {"ok": 3}, "summary": "C:/x/latest_summary.html"}
        self.assertIn("all 3 delivered", workflow.last_batch_line(good))
        self.assertIn("latest_summary.html", workflow.last_batch_line(good))
        bad = dict(good, counts={"ok": 1, "failed": 2}, summary=None)
        line = workflow.last_batch_line(bad)
        self.assertIn("1 of 3 delivered, 2 need attention", line)
        self.assertNotIn("summary:", line)


class ScreenOffer(unittest.TestCase):
    @staticmethod
    def screen(unbound):
        info = {
            "datasets": {},
            "filters": [],
            "grids": [],
            "quickViews": [],
            "unbound": unbound,
        }
        return types.SimpleNamespace(
            info=info,
            title="PO Batch Monitoring",
            code="P1114WM00",
            menu_id="PPM0221",
            trees=lambda: [],
            options=lambda: [],
        )

    @staticmethod
    def render(screen):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            workflow.show_screen_offer(screen)
        return output.getvalue().replace("\033[0m", "")

    def test_lists_every_other_input_with_set_guidance(self):
        output = self.render(self.screen([
            {"label": "Category", "control": "edtCategory", "value": "A"},
            {"label": "", "control": "edtSecond", "value": ""},
        ]))

        self.assertIn("OTHER INPUTS (2)", output)
        self.assertIn("Category", output)
        self.assertIn("Control   edtCategory", output)
        self.assertIn("Current   A", output)
        self.assertIn('--set "Category=<value>"', output)
        self.assertIn("edtSecond", output)
        self.assertIn('--set "edtSecond=<value>"', output)
        self.assertIn("Current   (empty)", output)

    def test_long_details_wrap_inside_terminal_width(self):
        old_width = ui.WIDTH
        ui.WIDTH = 60
        try:
            output = self.render(self.screen([{
                "label": "A very long category filter label",
                "control": "edtCategoryWithAnUnusuallyLongGeneratedName",
                "value": "A value that is deliberately long enough to require wrapping",
            }]))
        finally:
            ui.WIDTH = old_width

        section = output.split("OTHER INPUTS", 1)[1].split(
            "    Divisions", 1)[0]
        self.assertTrue(all(len(line) <= 60 for line in section.splitlines()))
        self.assertIn("edtCategoryWithAnUnusuallyLongGeneratedNam", section)
        self.assertIn("                  e", section)
        self.assertIn("--set", section)


class RecordOrReplayQuestion(unittest.TestCase):
    """Live-caught (HISTORY.md Phase 62.4): a person typed the screen code
    they wanted straight into "Record or Replay?", and the old
    `answer.startswith("p")` read "P1114WM00" as Replay - almost every
    screen code in this account starts with P."""

    @staticmethod
    def run_with_answers(answers):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), \
                mock.patch("builtins.input", side_effect=answers):
            mode = workflow.question_mode(workflow.Questions())
        return mode, output.getvalue()

    def test_a_screen_code_is_not_silently_read_as_replay(self):
        mode, out = self.run_with_answers(["P1114WM00", "p"])
        self.assertEqual(mode, "replay")
        self.assertIn("looks like a screen code, not R, P or B", out)

    def test_exact_letter_answers_still_work(self):
        self.assertEqual(self.run_with_answers(["r"])[0], "record")
        self.assertEqual(self.run_with_answers(["p"])[0], "replay")
        self.assertEqual(self.run_with_answers(["replay"])[0], "replay")

    def test_batch_is_a_third_answer(self):
        self.assertEqual(self.run_with_answers(["b"])[0], "batch")
        self.assertEqual(self.run_with_answers(["Batch"])[0], "batch")

    def test_a_screen_code_starting_with_b_is_not_read_as_batch(self):
        """The same trap one letter over: a code typed here must be refused,
        not matched on its first letter."""
        mode, out = self.run_with_answers(["B1234WM00", "b"])
        self.assertEqual(mode, "batch")
        self.assertIn("looks like a screen code", out)

    def test_a_wrong_answer_does_not_renumber_the_question(self):
        # input()'s own prompt text (where the question label actually
        # appears) is never echoed to stdout by a mocked input(), so the
        # label is observed by patching the module's ask() instead - which
        # is exactly what Questions.ask()/again() call, and what carries
        # the number.
        labels = []
        answers = iter(["xyz", "p"])

        def fake_ask(label, hint="", default="", help_text="", controls="full"):
            labels.append(label)
            return next(answers).strip() or default

        with contextlib.redirect_stdout(io.StringIO()), \
                mock.patch("run_gmes_workflow.ask", side_effect=fake_ask):
            workflow.question_mode(workflow.Questions())

        self.assertEqual(labels, ["1. Record, Replay or Batch?",
                                  "1. Record, Replay or Batch?"])


class RecordedScreensListShowsEveryRecording(unittest.TestCase):
    """HISTORY.md Phase 82.19, reported by the project owner from a
    screenshot: 17 screens were recorded and the Replay list showed nine.
    `question_screen()` sliced `saved[:9]` in BOTH the listing and the
    number-picker (on the assumption that "a single digit picks"), so the
    last eight recordings were neither displayed nor selectable by number -
    silently, with nothing saying the list was cut. `known()` itself was
    fine; every profile file on disk was returned."""

    CODES = [f"Q{2000 + i}UM00" for i in range(1, 18)]      # 17 recordings

    def profiles(self, count=17):
        return [{"screen": c, "title": f"Report number {i}",
                 "values": {"division": "VD"}}
                for i, c in enumerate(self.CODES[:count], start=1)]

    def ask(self, answers, count=17):
        """Run the real question with a mocked keyboard. Returns
        (chosen screen code, everything that was printed)."""
        out = io.StringIO()
        with contextlib.redirect_stdout(out), \
                mock.patch("builtins.input", side_effect=answers), \
                mock.patch.object(workflow.gmes_profile, "known",
                                  return_value=self.profiles(count)):
            chosen = workflow.question_screen(workflow.Questions(), session=None)
        return chosen, out.getvalue().replace("\033[0m", "")

    def test_all_seventeen_are_listed(self):
        _, printed = self.ask(["1"])
        for code in self.CODES:
            with self.subTest(code=code):
                self.assertIn(code, printed)

    def test_the_heading_says_how_many_there_are(self):
        # A list that states its own length cannot be silently short.
        _, printed = self.ask(["1"])
        self.assertIn("Screens already recorded (17):", printed)

    def test_a_number_above_nine_picks_that_screen(self):
        for number in (10, 12, 17):
            with self.subTest(number=number):
                chosen, _ = self.ask([str(number)])
                self.assertEqual(chosen, self.CODES[number - 1])

    def test_the_numbers_below_ten_still_pick_the_same_screens(self):
        for number in (1, 5, 9):
            with self.subTest(number=number):
                chosen, _ = self.ask([str(number)])
                self.assertEqual(chosen, self.CODES[number - 1])

    def test_a_short_list_is_unchanged(self):
        chosen, printed = self.ask(["3"], count=4)
        self.assertEqual(chosen, self.CODES[2])
        self.assertIn("Screens already recorded (4):", printed)

    def test_numbers_line_up_when_the_list_passes_nine(self):
        # Right-aligned, so " 1" and "17" share a column instead of the
        # screen codes shifting one place at row 10.
        _, printed = self.ask(["1"])
        rows = [l for l in printed.splitlines()
                if "Report number" in l and "UM00" in l]
        self.assertEqual(len(rows), 17)
        starts = {l.index("Q2") for l in rows}
        self.assertEqual(len(starts), 1, "screen codes are not in one column")

    def test_no_hard_coded_cap_is_left_in_the_code(self):
        # Comment lines are skipped: the explanation of why the cap was
        # removed names it, and must be allowed to.
        import inspect
        import re
        code_lines = [l for l in inspect.getsource(workflow.question_screen).splitlines()
                      if not l.lstrip().startswith("#")]
        for line in code_lines:
            with self.subTest(line=line.strip()):
                self.assertIsNone(re.search(r"saved\[:\d+\]", line))


class QuestionFiltersRoundTrip(unittest.TestCase):
    """Accepting the shown default unchanged has to return exactly what was
    remembered, not re-parse the "A=1; B=2" display string this question
    builds to SHOW two or more remembered filters - splitting that on the
    first "=" merged the second filter into the first one's value, and the
    second filter silently vanished."""

    def ask_with(self, raw):
        with contextlib.redirect_stdout(io.StringIO()), \
                mock.patch("builtins.input", return_value=raw):
            return workflow.question_filters(workflow.Questions(),
                                             {"Production Order": "011074232146",
                                              "Model Code": "SM-A137F"})

    def test_accepting_two_remembered_filters_unchanged_keeps_both(self):
        # Blank input makes ask() fall back to the pre-filled default,
        # which IS the "A=1; B=2"-joined string - this is the "just press
        # Enter" path, the one actually reachable through blank input.
        result = self.ask_with("")
        self.assertEqual(result, {"Production Order": "011074232146",
                                  "Model Code": "SM-A137F"})

    def test_a_genuinely_new_single_filter_still_works(self):
        result = self.ask_with("Plant=P703")
        self.assertEqual(result, {"Plant": "P703"})


class ReconcileMode(unittest.TestCase):
    """RECORD over an already-learned screen must NOT discard it on disk
    the moment it is chosen - a live review caught that the earlier version
    of this function did exactly that (gmes_profile.forget(), called here,
    before the screen was even opened or the run confirmed), so cancelling
    or a later failure left the old, working profile gone with nothing to
    replace it. The fix is `relearning`: the caller runs with
    `trust_profile=False` instead, so core.run_screen() does not load or
    trust the old profile but still atomically replaces it via its own
    save() - only on actual success (HISTORY.md Phase 66)."""

    PROFILE = {"learned": "2026-09-01", "values": {"division": "VD"}}

    def call(self, mode, code, profile):
        with contextlib.redirect_stdout(io.StringIO()), \
                mock.patch.object(workflow.gmes_profile, "forget") as forget:
            result = workflow.reconcile_mode(mode, code, profile)
        forget.assert_not_called()   # never touches disk, in any case
        return result

    def test_record_over_a_learned_screen_marks_relearning_without_forgetting(self):
        mode, profile, old_profile, relearning = self.call(
            "record", "P1112UM00", dict(self.PROFILE))
        self.assertEqual(mode, "record")
        self.assertIsNone(profile)
        self.assertEqual(old_profile, self.PROFILE)
        self.assertTrue(relearning)

    def test_replay_on_an_unlearned_screen_falls_back_to_record_not_relearning(self):
        mode, profile, old_profile, relearning = self.call(
            "replay", "P1112UM00", None)
        self.assertEqual(mode, "record")
        self.assertIsNone(profile)
        self.assertIsNone(old_profile)
        self.assertFalse(relearning)   # nothing existed to discard

    def test_the_matching_cases_are_left_alone(self):
        result = self.call("replay", "P1112UM00", dict(self.PROFILE))
        self.assertEqual(result, ("replay", self.PROFILE, None, False))

        result = self.call("record", "P1112UM00", None)
        self.assertEqual(result, ("record", None, None, False))


class BatchAnswers(unittest.TestCase):
    """The small parsers behind the Batch questions. Each one refuses what it
    does not understand - the question is then asked again, never guessed."""

    def test_dates_default_to_yesterday_and_are_validated_now_not_at_run_time(self):
        self.assertEqual(workflow.parse_batch_dates(""), "yesterday")
        self.assertEqual(workflow.parse_batch_dates(" Today "), "today")
        self.assertEqual(workflow.parse_batch_dates("-2"), "-2")
        self.assertEqual(workflow.parse_batch_dates("keep"), "keep")
        for bad in ("someday", "20261399", "tomorrow"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                workflow.parse_batch_dates(bad)

    def test_actions(self):
        for word, want in (("n", "now"), ("Run", "now"), ("s", "schedule"),
                           ("schedule", "schedule"), ("V", "save"), ("save", "save")):
            self.assertEqual(workflow.parse_batch_action(word), want)
        for bad in ("", "x", "yes", "nn"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                workflow.parse_batch_action(bad)

    def test_names(self):
        self.assertEqual(workflow.parse_batch_name(" morning "), "morning")
        for bad in ("", "a b", "..\\x", "x" * 41):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                workflow.parse_batch_name(bad)

    def test_when(self):
        w = workflow.parse_batch_when("06:30 daily")
        self.assertEqual((w.at, w.kind), ("06:30", "daily"))
        self.assertEqual(workflow.parse_batch_when("6:30").kind, "daily")     # daily is the default
        self.assertEqual(workflow.parse_batch_when("07:00 weekdays").days,
                         ["mon", "tue", "wed", "thu", "fri"])
        self.assertEqual(workflow.parse_batch_when("07:00 mon,wed,fri").days,
                         ["mon", "wed", "fri"])
        o = workflow.parse_batch_when("23:00 once 2026-09-25")
        self.assertEqual((o.kind, o.on), ("once", "2026-09-25"))
        for bad in ("", "daily", "25:00 daily", "06:30 fortnightly", "06:30 once soon"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                workflow.parse_batch_when(bad)


class BatchFlow(unittest.TestCase):
    """The interactive Batch dialogue: choose, decide dates, SEE THE PLAN, then
    run now / schedule / save. Nothing may be queried before the plan is shown,
    and a plan with nothing runnable must not ask any further question."""

    PROFILES = [
        {"screen": "A1", "title": "Alpha", "learned": True,
         "values": {"division": "VD", "from": "20260901", "to": "20260901", "verify": "ymd"}},
        {"screen": "B2", "title": "Beta", "learned": True, "values": {"division": "VD"}},
        {"screen": "C3", "title": "Gamma", "learned": False, "values": {}},
    ]

    def flow(self, answers, profiles=None, saved=None, **patches):
        import gmes_batch
        import gmes_schedule
        out = io.StringIO()
        results = [gmes_batch._result(c, "ok", rows=1) for c in ("A1", "B2")]
        run_batch = patches.pop("run_batch", mock.Mock(return_value=results))
        save_batch = mock.Mock()
        create = patches.pop("create", mock.Mock())
        with contextlib.redirect_stdout(out), \
                mock.patch("builtins.input", side_effect=answers), \
                mock.patch.object(workflow.gmes_profile, "known",
                                  return_value=self.PROFILES if profiles is None else profiles), \
                mock.patch.object(gmes_batch, "list_batches", return_value=saved or {}), \
                mock.patch.object(gmes_batch, "run_batch", run_batch), \
                mock.patch.object(gmes_batch, "save_batch", save_batch), \
                mock.patch.object(gmes_batch, "write_report", return_value=("j.json", "r.txt")), \
                mock.patch.object(gmes_schedule, "create", create):
            # A Mock, not a bare object() - batch_flow() now calls
            # session.get() only in the "run it now" branch; a real ws is
            # never actually needed since gmes_batch.run_batch is itself
            # mocked out above.
            done = workflow.batch_flow(workflow.Questions(), mock.Mock())
        return done, out.getvalue(), run_batch, save_batch, create

    def test_run_now_shows_the_plan_first_then_runs_only_what_is_runnable(self):
        done, out, run_batch, save_batch, create = self.flow(["all", "", "n"])
        self.assertTrue(done)
        self.assertLess(out.index("PLAN"), out.index("BATCH SUMMARY"))
        plan = run_batch.call_args[0][1]
        self.assertEqual([(i.code, i.ready) for i in plan],
                         [("A1", True), ("B2", True), ("C3", False)])
        save_batch.assert_not_called()
        create.assert_not_called()

    def test_yesterday_is_the_default_date_and_reaches_the_plan(self):
        import gmes_batch
        _done, out, run_batch, *_ = self.flow(["1", "", "n"])
        expected = gmes_batch.resolve_dates("yesterday")[0]
        self.assertEqual(run_batch.call_args[0][1][0].spec["date_from"], expected)

    def test_a_chosen_subset_runs_only_those_screens(self):
        _d, _o, run_batch, *_ = self.flow(["2", "", "n"])
        self.assertEqual([i.code for i in run_batch.call_args[0][1]], ["B2"])

    def test_a_bad_selection_is_asked_again_with_the_same_question_number(self):
        labels = []
        answers = iter(["99", "nonsense", "1", "", "n"])

        def fake_ask(label, hint="", default="", help_text="", controls="full"):
            labels.append(label)
            return next(answers).strip() or default

        import gmes_batch
        with contextlib.redirect_stdout(io.StringIO()), \
                mock.patch("run_gmes_workflow.ask", side_effect=fake_ask), \
                mock.patch.object(workflow.gmes_profile, "known", return_value=self.PROFILES), \
                mock.patch.object(gmes_batch, "list_batches", return_value={}), \
                mock.patch.object(gmes_batch, "run_batch", return_value=[]), \
                mock.patch.object(gmes_batch, "write_report", return_value=("j", "t")):
            workflow.batch_flow(workflow.Questions(), mock.Mock())
        self.assertEqual(labels[:3], ["1. Which screens?"] * 3)
        self.assertEqual(labels[3], "2. Which dates?")

    def test_a_plan_with_nothing_runnable_asks_nothing_further_and_runs_nothing(self):
        # Exactly two answers are supplied: a third input() would raise StopIteration.
        done, out, run_batch, save_batch, create = self.flow(["1", ""], profiles=[self.PROFILES[2]])
        self.assertFalse(done)
        self.assertIn("None of these can run", out)
        run_batch.assert_not_called()
        save_batch.assert_not_called()

    def test_nothing_recorded_says_so_and_asks_nothing(self):
        done, out, run_batch, *_ = self.flow([], profiles=[])
        self.assertFalse(done)
        self.assertIn("Nothing is recorded", out)
        run_batch.assert_not_called()

    def test_save_only_saves_the_list_and_schedules_nothing(self):
        done, _o, run_batch, save_batch, create = self.flow(["1,2", "-1", "v", "am"])
        self.assertTrue(done)
        save_batch.assert_called_once_with("am", ["A1", "B2"], "-1", "xlsx")
        run_batch.assert_not_called()
        create.assert_not_called()

    def test_schedule_saves_the_list_then_registers_the_task_for_that_name(self):
        done, out, run_batch, save_batch, create = self.flow(
            ["all", "", "s", "morning", "07:15 weekdays"])
        self.assertTrue(done)
        save_batch.assert_called_once_with("morning", ["A1", "B2", "C3"], "yesterday", "xlsx")
        name, when = create.call_args[0]
        self.assertEqual((name, when.at, when.kind), ("morning", "07:15", "weekly"))
        run_batch.assert_not_called()                 # scheduling is not running
        self.assertIn("only while you are signed in", out)

    def test_a_bad_time_is_asked_again_before_anything_is_registered(self):
        _d, _o, _r, _s, create = self.flow(
            ["all", "", "s", "am", "25:99 daily", "yesterday", "06:30 daily"])
        self.assertEqual(create.call_count, 1)
        self.assertEqual(create.call_args[0][1].at, "06:30")

    def test_task_scheduler_refusing_is_reported_not_swallowed(self):
        import gmes_schedule
        done, out, *_ = self.flow(
            ["all", "", "s", "am", "06:30 daily"],
            create=mock.Mock(side_effect=gmes_schedule.ScheduleError("Access is denied")))
        self.assertFalse(done)
        self.assertIn("Access is denied", out)

    def test_a_saved_list_can_be_chosen_by_name(self):
        _d, _o, run_batch, *_ = self.flow(
            ["@am", "", "n"], saved={"am": {"screens": ["B2"], "date": "yesterday"}})
        self.assertEqual([i.code for i in run_batch.call_args[0][1]], ["B2"])

    def test_the_run_reports_failure_when_any_screen_did_not_succeed(self):
        import gmes_batch
        bad = [gmes_batch._result("A1", "ok"), gmes_batch._result("B2", "failed")]
        done, *_ = self.flow(["1,2", "", "n"], run_batch=mock.Mock(return_value=bad))
        self.assertFalse(done)

    def test_the_mode_menu_hands_over_to_the_batch_dialogue(self):
        with contextlib.redirect_stdout(io.StringIO()), \
                mock.patch.object(workflow, "question_mode", return_value="batch"), \
                mock.patch.object(workflow, "batch_flow", return_value=True) as flow:
            result = workflow.one_run(object())
        self.assertTrue(result)
        flow.assert_called_once()


class HomeMenu(unittest.TestCase):
    """The main menu - shown before any sign-in, before any browser."""

    def choose(self, answer, known=True):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), \
                mock.patch("builtins.input", return_value=answer), \
                mock.patch.object(workflow.gmes_profile, "known",
                                  return_value=[{"screen": "A1"}] if known else []):
            action = workflow.home_menu()
        return action, out.getvalue()

    def test_every_numbered_choice_maps_to_the_right_action(self):
        cases = [("1", "run_saved"), ("2", "new_report"), ("3", "report_group"),
                 ("4", "saved_reports"), ("5", "schedules")]
        for typed, want in cases:
            with self.subTest(typed=typed):
                self.assertEqual(self.choose(typed)[0], want)

    def test_all_five_items_and_exit_are_printed(self):
        _action, out = self.choose("1")
        for text in ("Run a saved report", "Set up a new report",
                     "Run several reports", "Saved reports",
                     "View schedules", "Recent runs and files", "Help", "Exit"):
            with self.subTest(text=text):
                self.assertIn(text, out)

    def test_default_is_run_a_saved_report_when_something_is_recorded(self):
        # Blank input falls back to ask()'s default.
        self.assertEqual(self.choose("", known=True)[0], "run_saved")

    def test_default_is_set_up_a_new_report_when_nothing_is_recorded(self):
        # Nothing to run yet - defaulting to "run" would default into a dead end.
        self.assertEqual(self.choose("", known=False)[0], "new_report")

    def test_typing_q_exits_via_the_global_control_mechanism(self):
        with contextlib.redirect_stdout(io.StringIO()), \
                mock.patch("builtins.input", return_value="q"), \
                mock.patch.object(workflow.gmes_profile, "known", return_value=[]):
            with self.assertRaises(workflow.QuitRequested):
                workflow.home_menu()

    def test_a_number_out_of_range_is_asked_again(self):
        with contextlib.redirect_stdout(io.StringIO()), \
                mock.patch("builtins.input", side_effect=["9", "99", "1"]), \
                mock.patch.object(workflow.gmes_profile, "known", return_value=[]):
            self.assertEqual(workflow.home_menu(), "run_saved")


class GlobalPromptControls(unittest.TestCase):
    """back/cancel/help/quit, recognised inside ask() before the caller's own
    answer-parsing ever runs - one mechanism behind every question in this
    file, not a parallel system."""

    def ask_with(self, typed, **kw):
        with contextlib.redirect_stdout(io.StringIO()), \
                mock.patch("builtins.input", return_value=typed):
            return workflow.ask("Question", **kw)

    def test_cancel_raises_task_cancelled(self):
        with self.assertRaises(workflow.TaskCancelled):
            self.ask_with("cancel")

    def test_back_raises_go_back(self):
        with self.assertRaises(workflow.GoBack):
            self.ask_with("back")

    def test_quit_raises_quit_requested(self):
        with self.assertRaises(workflow.QuitRequested):
            self.ask_with("quit")

    def test_bare_letters_work_too_by_default(self):
        for letter, exc in (("b", workflow.GoBack), ("c", workflow.TaskCancelled),
                            ("q", workflow.QuitRequested)):
            with self.subTest(letter=letter), self.assertRaises(exc):
                self.ask_with(letter)

    def test_help_prints_and_re_asks_without_consuming_the_answer(self):
        with contextlib.redirect_stdout(io.StringIO()) as out, \
                mock.patch("builtins.input", side_effect=["help", "real answer"]):
            result = workflow.ask("Question", "a hint", help_text="Detailed help")
        self.assertEqual(result, "real answer")
        self.assertIn("Detailed help", out.getvalue())

    def test_controls_words_only_still_accepts_the_bare_letter_as_a_real_answer(self):
        # question_mode()'s own R/P/B question: bare "b" must still mean
        # Batch, not TaskCancelled, when controls="words".
        result = self.ask_with("b", controls="words")
        self.assertEqual(result, "b")

    def test_controls_words_still_recognises_the_full_word(self):
        with self.assertRaises(workflow.TaskCancelled):
            self.ask_with("cancel", controls="words")

    def test_controls_false_disables_interception_entirely(self):
        # The typed large-batch confirmation phrase must never be read as a
        # command, even if it happened to contain a control word.
        result = self.ask_with("cancel", controls=False)
        self.assertEqual(result, "cancel")

    def test_question_mode_still_treats_bare_b_as_batch_not_cancel(self):
        with contextlib.redirect_stdout(io.StringIO()), \
                mock.patch("builtins.input", return_value="b"):
            self.assertEqual(workflow.question_mode(workflow.Questions()), "batch")

    def test_run_it_still_treats_bare_c_as_change_not_cancel(self):
        # one_run()'s REPLAY shortcut: "c" means "change something", and must
        # keep meaning that under controls="words", not raise TaskCancelled.
        with contextlib.redirect_stdout(io.StringIO()), \
                mock.patch("builtins.input", return_value="c"):
            result = workflow.Questions().ask(
                "Run it?", "Enter to run, or type c to change something",
                default="run", controls="words")
        self.assertEqual(result, "c")

    def test_a_cancel_from_one_run_is_not_swallowed_by_the_generic_handler(self):
        # The regression this project's own comment already warns about for
        # `gmes_common` - an exception raised inside one_run()'s try block
        # must reach the (KeyboardInterrupt, InputClosed, GoBack, ...) tuple,
        # not the generic `except Exception` right below it.
        with contextlib.redirect_stdout(io.StringIO()), \
                mock.patch.object(workflow, "question_mode",
                                  side_effect=workflow.TaskCancelled()):
            with self.assertRaises(workflow.TaskCancelled):
                workflow.one_run(mock.Mock())


class SessionBehaviour(unittest.TestCase):
    """Sign-in and the run lock happen on the FIRST real need, not at
    construction - and only once per process."""

    def test_get_signs_in_and_connects_only_on_first_call(self):
        session = workflow.Session()
        with mock.patch.object(workflow.core, "acquire_run_lock",
                               return_value="tok") as acquire, \
                mock.patch.object(workflow, "sign_in_visibly", return_value=True) as sign_in, \
                mock.patch.object(workflow.core, "connect", return_value=mock.sentinel.ws) as connect:
            first = session.get()
            second = session.get()
        self.assertIs(first, mock.sentinel.ws)
        self.assertIs(second, mock.sentinel.ws)
        acquire.assert_called_once()
        sign_in.assert_called_once()
        connect.assert_called_once()

    def test_a_failed_sign_in_raises_sign_in_failed(self):
        session = workflow.Session()
        with mock.patch.object(workflow.core, "acquire_run_lock", return_value="tok"), \
                mock.patch.object(workflow, "sign_in_visibly", return_value=False):
            with self.assertRaises(workflow.SignInFailed):
                session.get()

    def test_a_held_lock_propagates_run_locked(self):
        session = workflow.Session()
        with mock.patch.object(workflow.core, "acquire_run_lock",
                               side_effect=workflow.core.RunLocked("busy")):
            with self.assertRaises(workflow.core.RunLocked):
                session.get()

    def test_close_before_any_get_does_nothing_harmful(self):
        workflow.Session().close()   # must not raise

    def test_close_releases_the_lock_and_the_browser_it_actually_holds(self):
        session = workflow.Session()
        ws = mock.Mock()
        with mock.patch.object(workflow.core, "acquire_run_lock", return_value="tok"), \
                mock.patch.object(workflow, "sign_in_visibly", return_value=True), \
                mock.patch.object(workflow.core, "connect", return_value=ws), \
                mock.patch.object(workflow.core, "release_run_lock") as release:
            session.get()
            session.close()
        ws.close.assert_called_once()
        release.assert_called_once_with("tok")


class QuestionScreenOfflinePicks(unittest.TestCase):
    """Picking an already-recorded screen by NUMBER needs no browser -
    session.get() must not be called for it, only for 'find <words>' or an
    unrecognised code."""

    PROFILES = [{"screen": "P1112UM00", "title": "Plan"}]

    def test_a_numbered_pick_never_touches_the_session(self):
        session = mock.Mock()
        session.get.side_effect = AssertionError("should not need to connect")
        with contextlib.redirect_stdout(io.StringIO()), \
                mock.patch("builtins.input", return_value="1"), \
                mock.patch.object(workflow.gmes_profile, "known", return_value=self.PROFILES):
            chosen = workflow.question_screen(workflow.Questions(), session)
        self.assertEqual(chosen, "P1112UM00")

    def test_a_preselected_code_never_asks_or_touches_the_session(self):
        session = mock.Mock()
        session.get.side_effect = AssertionError("should not need to connect")
        with contextlib.redirect_stdout(io.StringIO()), \
                mock.patch("builtins.input", side_effect=AssertionError("no question expected")):
            chosen = workflow.question_screen(workflow.Questions(), session,
                                              preselected="P1112UM00")
        self.assertEqual(chosen, "P1112UM00")

    def test_a_find_search_does_touch_the_session(self):
        # A "find" search only ever lists results and prints "type one of
        # those codes above" - it never lets a number pick from ITS OWN
        # listing (that is the recorded-screens list's own behaviour). The
        # second answer must be the real code, not an index.
        session = mock.Mock()
        session.get.return_value = mock.sentinel.ws
        with contextlib.redirect_stdout(io.StringIO()), \
                mock.patch("builtins.input", side_effect=["find plan", "P1112UM00"]), \
                mock.patch.object(workflow.gmes_profile, "known", return_value=[]), \
                mock.patch.object(workflow.gmes_open_screen, "catalogue",
                                  return_value={"rows": [{"screenId": "P1112UM00",
                                                          "menuId": "M1", "menuTitle": "Plan"}],
                                               "total": 1}):
            chosen = workflow.question_screen(workflow.Questions(), session)
        self.assertEqual(chosen, "P1112UM00")
        session.get.assert_called()


class BatchFlowNeverConnectsUntilRunNow(unittest.TestCase):
    """save / schedule / a blocked plan must never reach session.get() - only
    "run it now" is a live action."""

    PROFILES = [{"screen": "A1", "title": "Alpha", "learned": True,
                "values": {"division": "VD", "from": "20260901", "to": "20260901",
                          "verify": "ymd"}}]

    def strict_session(self):
        session = mock.Mock()
        session.get.side_effect = AssertionError("must not connect for this path")
        return session

    def test_save_only_never_connects(self):
        import gmes_batch
        with contextlib.redirect_stdout(io.StringIO()), \
                mock.patch("builtins.input", side_effect=["1", "", "v", "am"]), \
                mock.patch.object(workflow.gmes_profile, "known", return_value=self.PROFILES), \
                mock.patch.object(gmes_batch, "list_batches", return_value={}), \
                mock.patch.object(gmes_batch, "save_batch"):
            done = workflow.batch_flow(workflow.Questions(), self.strict_session())
        self.assertTrue(done)

    def test_a_blocked_plan_never_connects(self):
        import gmes_batch
        with contextlib.redirect_stdout(io.StringIO()), \
                mock.patch("builtins.input", side_effect=["1", ""]), \
                mock.patch.object(workflow.gmes_profile, "known",
                                  return_value=[{"screen": "C3", "title": "Gamma",
                                                "learned": False, "values": {}}]), \
                mock.patch.object(gmes_batch, "list_batches", return_value={}):
            done = workflow.batch_flow(workflow.Questions(), self.strict_session())
        self.assertFalse(done)

    def test_nothing_recorded_never_connects(self):
        with contextlib.redirect_stdout(io.StringIO()), \
                mock.patch.object(workflow.gmes_profile, "known", return_value=[]):
            done = workflow.batch_flow(workflow.Questions(), self.strict_session())
        self.assertFalse(done)


class LargeBatchConfirmation(unittest.TestCase):
    """Live-caught: blank Enter through 'Which screens?' (default 'all') then
    blank Enter through 'Run it now...' (default 'N'/now) started a full live
    batch - 32 screens, 31 runnable - with no confirmation beyond the printed
    plan. Above LARGE_BATCH_CONFIRM, the default is removed and running
    anyway needs the exact ready-count typed on purpose."""

    def profiles(self, n):
        return [{"screen": f"S{i}", "title": f"Screen {i}", "learned": True,
                 "values": {"division": "VD", "from": "20260901", "to": "20260901",
                           "verify": "ymd"}}
                for i in range(n)]

    def flow(self, answers, count=10):
        import gmes_batch
        out = io.StringIO()
        results = [gmes_batch._result(f"S{i}", "ok", rows=1) for i in range(count)]
        with contextlib.redirect_stdout(out), \
                mock.patch("builtins.input", side_effect=answers), \
                mock.patch.object(workflow.gmes_profile, "known",
                                  return_value=self.profiles(count)), \
                mock.patch.object(gmes_batch, "list_batches", return_value={}), \
                mock.patch.object(gmes_batch, "run_batch",
                                  return_value=results) as run_batch, \
                mock.patch.object(gmes_batch, "write_report_safely",
                                  return_value=(None, "r.txt")):
            done = workflow.batch_flow(workflow.Questions(), mock.Mock())
        return done, out.getvalue(), run_batch

    def test_blank_enter_no_longer_defaults_to_running_a_large_group(self):
        # Blank at "Run it now..." must re-ask (no default to fall back to)
        # rather than silently picking "now" - a fourth, explicit "now" is
        # needed before the confirmation phrase is even asked for.
        done, _out, run_batch = self.flow(["all", "", "", "now", "RUN 10 REPORTS"])
        self.assertTrue(done)
        run_batch.assert_called_once()

    def test_typing_now_without_the_confirmation_phrase_runs_nothing(self):
        done, out, run_batch = self.flow(["all", "", "now", "not it"])
        self.assertFalse(done)
        self.assertIn("Not confirmed", out)
        run_batch.assert_not_called()

    def test_the_exact_confirmation_phrase_proceeds(self):
        done, _out, run_batch = self.flow(["all", "", "now", "RUN 10 REPORTS"])
        self.assertTrue(done)
        run_batch.assert_called_once()

    def test_a_small_group_is_unaffected_and_keeps_its_default(self):
        done, _out, run_batch = self.flow(["all", "", "n"], count=3)
        self.assertTrue(done)
        run_batch.assert_called_once()

    def test_the_confirmation_typo_is_rejected(self):
        done, out, run_batch = self.flow(["all", "", "now", "RUN 9 REPORTS"])
        self.assertFalse(done)
        self.assertIn("Not confirmed", out)
        run_batch.assert_not_called()


class FriendlyDateRendering(unittest.TestCase):
    def test_a_known_date_renders_as_weekday_day_month_year(self):
        self.assertEqual(workflow.friendly_date("20260921"), "Monday 21 September 2026")

    def test_the_batch_dates_hint_shows_the_resolved_date_not_only_the_word(self):
        # A mocked input() never actually renders ask()'s prompt text (that
        # is input()'s own OS-level behaviour, bypassed entirely when
        # mocked) - so the hint is observed by patching ask() itself, the
        # same technique used elsewhere in this file for exactly this reason.
        import gmes_batch
        hints = {}
        answers = iter(["1", "yesterday", "n"])

        def fake_ask(label, hint="", default="", help_text="", controls="full"):
            hints[label] = hint
            return next(answers).strip() or default

        with contextlib.redirect_stdout(io.StringIO()), \
                mock.patch("run_gmes_workflow.ask", side_effect=fake_ask), \
                mock.patch.object(workflow.gmes_profile, "known",
                                  return_value=[{"screen": "A1", "title": "Alpha",
                                                "learned": True,
                                                "values": {"division": "VD",
                                                          "from": "20260901",
                                                          "to": "20260901",
                                                          "verify": "ymd"}}]), \
                mock.patch.object(gmes_batch, "list_batches", return_value={}), \
                mock.patch.object(gmes_batch, "run_batch",
                                  return_value=[gmes_batch._result("A1", "ok", rows=1)]), \
                mock.patch.object(gmes_batch, "write_report_safely",
                                  return_value=(None, "r.txt")):
            workflow.batch_flow(workflow.Questions(), mock.Mock())
        expected = workflow.friendly_date(gmes_batch.resolve_dates("yesterday")[0])
        self.assertIn(expected, hints["2. Which dates?"])


class ShowSavedReportsIsOffline(unittest.TestCase):
    """Viewing what is already recorded needs no sign-in and no browser."""

    PROFILES = [{"screen": "A1", "title": "Alpha", "values": {"division": "VD"}}]

    def test_listing_never_touches_the_session(self):
        session = mock.Mock()
        session.get.side_effect = AssertionError("must not connect just to view")
        with contextlib.redirect_stdout(io.StringIO()) as out, \
                mock.patch("builtins.input", return_value=""), \
                mock.patch.object(workflow.gmes_profile, "known", return_value=self.PROFILES), \
                mock.patch.object(workflow.gmes_profile, "unreadable", return_value=[]):
            workflow.show_saved_reports(session)
        self.assertIn("A1", out.getvalue())

    def test_choosing_a_number_runs_that_report(self):
        with contextlib.redirect_stdout(io.StringIO()), \
                mock.patch("builtins.input", return_value="1"), \
                mock.patch.object(workflow.gmes_profile, "known", return_value=self.PROFILES), \
                mock.patch.object(workflow.gmes_profile, "unreadable", return_value=[]), \
                mock.patch.object(workflow, "one_run", return_value=True) as one_run:
            workflow.show_saved_reports(mock.Mock())
        one_run.assert_called_once_with(mock.ANY, preset_mode="replay",
                                        preselected_code="A1")

    def test_nothing_recorded_says_so_and_asks_nothing(self):
        with contextlib.redirect_stdout(io.StringIO()) as out, \
                mock.patch("builtins.input", side_effect=AssertionError("no question expected")), \
                mock.patch.object(workflow.gmes_profile, "known", return_value=[]):
            workflow.show_saved_reports(mock.Mock())
        self.assertIn("Nothing is set up yet", out.getvalue())


class ShowSchedulesCanDelete(unittest.TestCase):
    """Task Scheduler only - no G-MES, no sign-in, no browser. The saved
    report group is always kept; only the timer can be removed."""

    TASKS = [{"batch": "morning", "task": "GMES_Batch_morning", "state": "Ready",
             "trigger": "Daily", "next_run": "2026-09-23T06:30:00",
             "last_run": "2026-09-22T06:30:00", "last_result": 0,
             "last_text": "ok", "last_hex": "0x0", "last_ok": True}]

    def test_listing_never_touches_the_session_or_browser(self):
        import gmes_schedule
        with contextlib.redirect_stdout(io.StringIO()) as out, \
                mock.patch("builtins.input", return_value=""), \
                mock.patch.object(gmes_schedule, "list_tasks", return_value=self.TASKS):
            workflow.show_schedules()
        self.assertIn("morning", out.getvalue())

    def test_nothing_scheduled_says_so_and_asks_nothing(self):
        import gmes_schedule
        with contextlib.redirect_stdout(io.StringIO()) as out, \
                mock.patch("builtins.input", side_effect=AssertionError("no question expected")), \
                mock.patch.object(gmes_schedule, "list_tasks", return_value=[]):
            workflow.show_schedules()
        self.assertIn("Nothing is scheduled", out.getvalue())

    def test_blank_enter_leaves_everything_in_place(self):
        import gmes_schedule
        with contextlib.redirect_stdout(io.StringIO()), \
                mock.patch("builtins.input", return_value=""), \
                mock.patch.object(gmes_schedule, "list_tasks", return_value=self.TASKS), \
                mock.patch.object(gmes_schedule, "delete") as delete:
            workflow.show_schedules()
        delete.assert_not_called()

    def test_choosing_a_number_then_confirming_deletes_that_schedule(self):
        import gmes_schedule
        answers = iter(["1", "3", "y"])        # the schedule, then "Remove the schedule"
        with contextlib.redirect_stdout(io.StringIO()) as out, \
                mock.patch("builtins.input", side_effect=lambda *a, **k: next(answers)), \
                mock.patch.object(gmes_schedule, "list_tasks", return_value=self.TASKS), \
                mock.patch.object(gmes_schedule, "delete", return_value=True) as delete:
            workflow.show_schedules()
        delete.assert_called_once_with("morning")
        self.assertIn("Removed", out.getvalue())

    def test_declining_the_confirmation_removes_nothing(self):
        import gmes_schedule
        answers = iter(["1", "3", "n"])
        with contextlib.redirect_stdout(io.StringIO()), \
                mock.patch("builtins.input", side_effect=lambda *a, **k: next(answers)), \
                mock.patch.object(gmes_schedule, "list_tasks", return_value=self.TASKS), \
                mock.patch.object(gmes_schedule, "delete") as delete:
            workflow.show_schedules()
        delete.assert_not_called()

    def test_a_number_outside_the_list_removes_nothing(self):
        import gmes_schedule
        with contextlib.redirect_stdout(io.StringIO()) as out, \
                mock.patch("builtins.input", side_effect=["9", ""]), \
                mock.patch.object(gmes_schedule, "list_tasks", return_value=self.TASKS), \
                mock.patch.object(gmes_schedule, "delete") as delete:
            workflow.show_schedules()
        delete.assert_not_called()
        self.assertIn("not in the list", out.getvalue())

    def test_task_scheduler_refusing_the_listing_is_reported_not_swallowed(self):
        import gmes_schedule
        with contextlib.redirect_stdout(io.StringIO()) as out, \
                mock.patch("builtins.input", side_effect=AssertionError("no question expected")), \
                mock.patch.object(gmes_schedule, "list_tasks",
                                  side_effect=gmes_schedule.ScheduleError("PowerShell refused")):
            workflow.show_schedules()
        self.assertIn("PowerShell refused", out.getvalue())


class MainReturnsHomeAfterEachTask(unittest.TestCase):
    """No more 'Another report?' y/n - the loop returns to the main menu,
    which is where 'exit' now lives."""

    def test_the_loop_re_enters_the_home_menu_after_a_report(self):
        calls = {"n": 0}

        def fake_home_menu():
            calls["n"] += 1
            if calls["n"] == 1:
                return "run_saved"
            raise workflow.QuitRequested()

        with contextlib.redirect_stdout(io.StringIO()), \
                mock.patch.object(workflow, "home_menu", side_effect=fake_home_menu), \
                mock.patch.object(workflow, "one_run", return_value=True) as one_run, \
                mock.patch.object(workflow.gmes_log, "start", return_value="log.txt"), \
                mock.patch.object(workflow.gmes_log, "finish"), \
                mock.patch.object(workflow.gmes_log, "path", return_value="log.txt"):
            code = workflow.main()
        self.assertEqual(code, 0)
        one_run.assert_called_once()
        self.assertEqual(calls["n"], 2)   # home_menu was re-entered, not asked "Another?"



class LibraryStatusInPlainWords(unittest.TestCase):
    """HISTORY.md Phase 93: every saved report carries a status a person can
    act on, and the reason for it."""

    def card(self, values=None, learned="2026-09-16", last=None):
        profile = {"screen": "A1", "title": "Alpha", "values": values or {}}
        if learned:
            profile["learned"] = learned
        return library.report_card(profile, last)

    def test_a_checked_date_is_ready(self):
        c = self.card({"division": "VD", "from": "20260922", "to": "20260922",
                       "verify": "planYmd"})
        self.assertEqual(c["status"], library.READY)
        self.assertEqual(c["settings"], "VD, date 20260922")

    def test_a_typed_unchecked_date_is_a_warning_with_its_reason(self):
        c = self.card({"sets": {"startDay": "20260922"}})
        self.assertEqual(c["status"], library.READY_WARN)
        self.assertIn("not checked", c["why"])

    def test_a_shipped_report_never_run_here_says_so(self):
        self.assertEqual(self.card(learned=None)["status"], library.NOT_TRIED)

    def test_the_last_result_is_shown_and_a_failure_overrides_the_status(self):
        ok = self.card(last=("2026-09-23 02:00", {"status": "ok", "rows": 6529}))
        self.assertIn("6,529 rows", ok["last_run"])
        bad = self.card(last=("2026-09-23 02:00", {"status": "failed",
                                                   "error": "the remembered screen shape changed"}))
        self.assertEqual(bad["status"], library.LAST_FAILED)
        self.assertIn("changed this screen", bad["why"])

    def test_search_needs_every_word_in_any_case(self):
        cards = [{"code": "P1112UM00", "title": "Production Plan", "settings": "VD"},
                 {"code": "P3151WM00", "title": "Loss Status", "settings": "VD"}]
        self.assertEqual([c["code"] for c in library.search(cards, "plan vd")], ["P1112UM00"])
        self.assertEqual(len(library.search(cards, "")), 2)
        self.assertEqual(library.search(cards, "nothing"), [])

    def test_a_page_always_carries_the_total(self):
        visible, first, last, total, page, pages = library.page_of(list(range(23)), 2)
        self.assertEqual((visible, first, last, total, page, pages), ([20, 21, 22], 21, 23, 23, 2, 3))
        self.assertEqual(library.page_of([], 5)[3:], (0, 0, 1))


class RunHistoryIsReadFromReports(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.d = self._tmp.name

    def write(self, name, payload):
        with open(os.path.join(self.d, name), "w", encoding="utf-8") as fh:
            fh.write(payload if isinstance(payload, str) else __import__("json").dumps(payload))

    def test_newest_first_damaged_files_skipped_and_last_result_per_code(self):
        run = lambda started, status: {"meta": {"started": started, "batch": "N"},
                                        "summary": {"ok": 1},
                                        "results": [{"screen": "A1", "status": status}]}
        self.write("batch_20260922_020000.json", run("22nd", "failed"))
        self.write("batch_20260923_020000.json", run("23rd", "ok"))
        self.write("batch_20260924_020000.json", "{ not json")
        history = library.run_history(self.d)
        self.assertEqual([h["started"] for h in history], ["23rd", "22nd"])
        self.assertEqual(library.last_result_by_code(history)["A1"][0], "23rd")

    def test_no_folder_is_no_history(self):
        self.assertEqual(library.run_history(os.path.join(self.d, "missing")), [])


class ErrorsInPlainWords(unittest.TestCase):
    def test_known_families(self):
        cases = {
            "Another G-MES run already has the browser (lock held by pid 5)": "already running",
            "not run: this PC's clock is 1 h 0 min ahead of the G-MES server's": "date or time is wrong",
            "The connection to the automation browser was lost (x)": "closed or crashed",
            "the remembered screen shape changed; refusing to replay": "changed this screen",
            "the results carry planYmd=['20260921']... Refusing to export the wrong data.": "did not match",
            "[WinError 32] The process cannot access the file": "in use",
            "the query had not settled after 300s": "too slow",
        }
        for text, words in cases.items():
            with self.subTest(text=text):
                what, do = library.explain_error(text)
                self.assertIn(words, what)
                self.assertTrue(do)

    def test_an_unknown_error_still_gets_a_safe_next_step(self):
        what, do = library.explain_error("KeyError: 'zzz'")
        self.assertIn("does not recognise", what)
        self.assertIn("log file", do)

    def test_a_short_word_inside_another_does_not_misfire(self):
        # "sso" alone used to match inside unrelated words.
        self.assertNotIn("Signing in", library.explain_error("processor lesson")[0])


class OpeningFilesIsSafe(unittest.TestCase):
    def test_only_an_existing_path_is_opened_and_only_through_the_opener(self):
        opened = []
        ok, _ = library.open_path(__file__, opener=opened.append)
        self.assertTrue(ok)
        self.assertEqual(opened, [__file__])
        ok, msg = library.open_path("/no/such/file.xlsx", opener=opened.append)
        self.assertFalse(ok)
        self.assertIn("not found", msg)
        self.assertEqual(len(opened), 1)

    def test_without_an_opener_the_path_is_handed_back(self):
        with mock.patch.object(library.os, "startfile", None, create=True):
            ok, msg = library.open_path(__file__)
        self.assertFalse(ok)
        self.assertIn(__file__, msg)


class NewHomeChoices(unittest.TestCase):
    def test_six_and_seven_are_recent_runs_and_help(self):
        for typed, want in (("6", "recent"), ("7", "settings"), ("8", "help")):
            with self.subTest(typed=typed), contextlib.redirect_stdout(io.StringIO()), \
                    mock.patch("builtins.input", return_value=typed), \
                    mock.patch.object(workflow.gmes_profile, "known", return_value=[]):
                self.assertEqual(workflow.home_menu(), want)

    def test_the_menu_says_which_choices_sign_in(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), mock.patch("builtins.input", return_value="1"), \
                mock.patch.object(workflow.gmes_profile, "known", return_value=[]):
            workflow.home_menu()
        self.assertIn("signs in", out.getvalue())
        self.assertIn("no sign-in", out.getvalue())

    def test_status_lines(self):
        self.assertIn("No saved reports", workflow.home_status_lines(known=[], last={})[0])
        lines = workflow.home_status_lines(known=[{}, {}], last={
            "started": "x", "total": 2, "counts": {"ok": 2}})
        self.assertEqual(lines[0], "2 saved report(s)")
        self.assertIn("all 2 delivered", lines[1])

    def test_views_that_need_no_sign_in_never_touch_a_session(self):
        # recent runs and help take no session at all - by signature.
        import inspect
        for view in (workflow.show_recent_runs, workflow.show_help):
            self.assertEqual(list(inspect.signature(view).parameters), [])


class LibraryPagesAndSearch(unittest.TestCase):
    def run_library(self, answers, count=23):
        profiles = [{"screen": f"S{i:02d}", "title": f"Report {i}", "learned": "x"}
                    for i in range(1, count + 1)]
        out = io.StringIO()
        with contextlib.redirect_stdout(out), \
                mock.patch("builtins.input", side_effect=answers), \
                mock.patch.object(workflow.gmes_profile, "known", return_value=profiles), \
                mock.patch.object(workflow.gmes_profile, "unreadable", return_value=[]), \
                mock.patch.object(library, "run_history", return_value=[]), \
                mock.patch.object(workflow, "one_run", return_value=True) as one_run:
            workflow.show_saved_reports(mock.Mock())
        return out.getvalue(), one_run

    def test_pages_show_the_total_and_numbers_stay_global(self):
        out, one_run = self.run_library(["n", "12"])
        self.assertIn("showing 11-20, page 2 of 3", out)
        one_run.assert_called_once_with(mock.ANY, preset_mode="replay", preselected_code="S12")

    def test_search_then_pick_runs_the_matching_report(self):
        # Numbers stay the ones first shown, even inside a search result.
        out, one_run = self.run_library(["report 7", "7"])
        self.assertIn("2 of 23 match 'report 7'", out)          # S07 and S17
        one_run.assert_called_once_with(mock.ANY, preset_mode="replay", preselected_code="S07")

    def test_a_number_outside_the_list_runs_nothing(self):
        out, one_run = self.run_library(["99", ""])
        one_run.assert_not_called()
        self.assertIn("not in the list", out)


class AfterAReport(unittest.TestCase):
    def test_enter_goes_home_and_a_number_opens_that_file(self):
        with contextlib.redirect_stdout(io.StringIO()), \
                mock.patch("builtins.input", return_value=""), \
                mock.patch.object(library, "open_path") as opened:
            workflow.after_success(["C:/x/a.xlsx", "C:/x/a.csv"], "C:/x")
        opened.assert_not_called()
        with contextlib.redirect_stdout(io.StringIO()), \
                mock.patch("builtins.input", return_value="2"), \
                mock.patch.object(library, "open_path", return_value=(True, "ok")) as opened:
            workflow.after_success(["C:/x/a.xlsx"], "C:/x")
        opened.assert_called_once_with("C:/x")

    def test_both_endings_of_a_single_report_are_recorded(self):
        # one_run() drives a live browser, so its two endings are checked at
        # the source: success AND failure each hand the outcome to the history.
        import inspect
        body = inspect.getsource(workflow.one_run)
        self.assertIn("record_single_run(code, r, date_from, date_to, written_to)", body)
        self.assertIn('record_single_run(code, r, date_from, date_to, "")', body)
        self.assertIn("after_success(r[\"files\"], written_to)", body)
        self.assertIn("library.explain_error(r[\"error\"])", body)

    def test_a_single_report_is_added_to_the_history(self):
        written = []
        workflow.record_single_run("A1", {"ok": False, "rows": 0, "files": [],
                                          "error": "boom"}, "20260922", "20260922", "",
                                   write=lambda results, meta, log=None: written.append((results, meta)))
        results, meta = written[0]
        self.assertEqual(results[0]["status"], "failed")
        self.assertEqual(results[0]["dates"], "20260922")
        self.assertIn("A1", meta["batch"])


class QuittingMidTaskIsNotCountedAsARun(unittest.TestCase):
    def test_the_closing_line_counts_only_what_ran(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), \
                mock.patch.object(workflow, "home_menu", return_value="run_saved"), \
                mock.patch.object(workflow, "one_run", side_effect=workflow.QuitRequested()), \
                mock.patch.object(workflow.gmes_log, "start", return_value="log"), \
                mock.patch.object(workflow.gmes_log, "finish"), \
                mock.patch.object(workflow, "home_status_lines", return_value=[]):
            workflow.main()
        self.assertIn("0 report(s)", out.getvalue())



# ---------------------------------------------------------------------------
# HISTORY.md Phase 94 - the application frame, arrow-key menus and new screens
# ---------------------------------------------------------------------------

class KeysAreDecoded(unittest.TestCase):
    def decode(self, first, rest=""):
        buf = list(rest)
        return ui.decode_key(first, lambda: buf.pop(0) if buf else "")

    def test_windows_console_keys(self):
        for code, name in (("H", "up"), ("P", "down"), ("I", "pgup"), ("Q", "pgdn"),
                           ("G", "home"), ("O", "end")):
            self.assertEqual(self.decode("\xe0", code), name)
            self.assertEqual(self.decode("\x00", code), name)

    def test_terminal_escape_sequences_and_a_lone_escape(self):
        self.assertEqual(self.decode("\x1b", "[A"), "up")
        self.assertEqual(self.decode("\x1b", "[B"), "down")
        self.assertEqual(self.decode("\x1b", "[6~"), "pgdn")
        self.assertEqual(self.decode("\x1b"), "esc")

    def test_plain_keys(self):
        self.assertEqual(self.decode("\r"), "enter")
        self.assertEqual(self.decode("\x7f"), "backspace")
        self.assertEqual(self.decode(" "), "space")
        self.assertEqual(self.decode("x"), "x")
        with self.assertRaises(KeyboardInterrupt):
            self.decode("\x03")


class MenuStateMachine(unittest.TestCase):
    ITEMS = [{"label": f"Report {n}", "detail": f"detail {n}"} for n in range(1, 26)]

    def test_moving_wraps_and_enter_chooses_the_highlighted_item(self):
        m = ui.Menu(self.ITEMS, height=5)
        self.assertIsNone(m.handle("up"))                      # wraps to the last
        self.assertEqual(m.handle("enter"), ("choose", 24))
        m = ui.Menu(self.ITEMS, height=5)
        m.handle("down"); m.handle("down")
        self.assertEqual(m.handle("enter"), ("choose", 2))

    def test_typing_filters_and_esc_first_clears_then_goes_back(self):
        m = ui.Menu(self.ITEMS, height=5)
        for ch in "report 2":
            m.handle("space" if ch == " " else ch)
        self.assertEqual([self.ITEMS[i]["label"] for i in m.matches()][:2],
                         ["Report 2", "Report 12"])
        m.handle("down")
        self.assertEqual(m.handle("enter"), ("choose", 11))
        self.assertIsNone(m.handle("esc"))                     # clears the search
        self.assertEqual(m.query, "")
        self.assertEqual(m.handle("esc"), ("back",))

    def test_nothing_matching_is_said_not_chosen(self):
        m = ui.Menu(self.ITEMS)
        for ch in "zzz":
            m.handle(ch)
        self.assertIsNone(m.handle("enter"))
        self.assertIn("Nothing matches", m.message)

    def test_multi_choice_ticks_and_refuses_an_empty_confirm(self):
        m = ui.Menu(self.ITEMS[:4], multi=True)
        self.assertIsNone(m.handle("enter"))
        self.assertIn("Nothing ticked", m.message)
        m.handle("space"); m.handle("down"); m.handle("down"); m.handle("space")
        self.assertEqual(m.handle("enter"), ("many", [0, 2]))
        m = ui.Menu(self.ITEMS[:4], multi=True)
        m.handle("*")
        self.assertEqual(m.handle("enter"), ("many", [0, 1, 2, 3]))
        m.handle("*")
        self.assertEqual(m.chosen, set())

    def test_the_drawn_block_never_changes_height_while_scrolling(self):
        m = ui.Menu(self.ITEMS, height=5)
        heights = set()
        for key in ["down"] * 30 + ["pgup", "end", "home"] + list("rep"):
            m.handle(key)
            heights.add(len(m.lines(78)))
        self.assertEqual(len(heights), 1)

    def test_the_highlighted_item_detail_is_shown_in_full(self):
        m = ui.Menu([{"label": "A", "detail": "a long sentence " * 3}])
        self.assertTrue(any("a long sentence a long sentence" in line for line in m.lines(78)))

    def test_run_menu_drives_the_menu_with_keys_and_folds_it_away(self):
        written = []
        keys = iter(["down", "enter"])
        result = ui.run_menu(ui.Menu(self.ITEMS[:3]), (("Enter", "open"),),
                             reader=lambda: next(keys), write=written.append)
        self.assertEqual(result, ("choose", 1))
        self.assertIn("Report 2", written[-2])                  # the one-line echo


class TablesFitTheConsole(unittest.TestCase):
    def test_a_long_last_column_wraps_and_nothing_passes_the_width(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), mock.patch.object(ui, "WIDTH", 60):
            ui.table([("#", 2, False), ("NAME", 6, True), ("WHAT", 6, True)],
                     [("1", "short", "a sentence long enough that it has to wrap onto lines")])
        lines = [l for l in out.getvalue().splitlines() if l.strip()]
        self.assertTrue(all(len(l) <= 60 for l in lines))
        self.assertIn("lines", out.getvalue())                  # wrapped, not cut

    def test_clip_marks_what_it_cuts(self):
        self.assertEqual(ui.clip("abcdef", 10), "abcdef")
        self.assertTrue(ui.clip("abcdefghijk", 6).endswith(ui.ELLIPSIS))
        self.assertEqual(len(ui.clip("abcdefghijk", 6)), 6)


class ChooseHelper(unittest.TestCase):
    ITEMS = [workflow._item(f"R{n}") for n in range(1, 6)]

    def pick(self, answers, **kw):
        with contextlib.redirect_stdout(io.StringIO()), \
                mock.patch.object(ui, "INTERACTIVE", False), \
                mock.patch("builtins.input", side_effect=answers):
            return workflow.choose(self.ITEMS, **kw)

    def test_numbers_ranges_and_all_in_a_multi_choice(self):
        self.assertEqual(self.pick(["1,3-4"], multi=True), [0, 2, 3])
        self.assertEqual(self.pick(["all"], multi=True), [0, 1, 2, 3, 4])
        self.assertEqual(self.pick(["9", "2"], multi=True), [1])     # 9 refused, re-asked

    def test_blank_with_no_default_goes_back_and_with_one_picks_it(self):
        with self.assertRaises(workflow.GoBack):
            self.pick([""])
        self.assertEqual(self.pick([""], default=2), 2)

    def test_the_arrow_key_menu_is_used_when_the_console_can(self):
        with mock.patch.object(ui, "INTERACTIVE", True), \
                mock.patch.object(ui, "run_menu", return_value=("choose", 3)) as run:
            self.assertEqual(workflow.choose(self.ITEMS), 3)
        run.assert_called_once()
        with mock.patch.object(ui, "INTERACTIVE", True), \
                mock.patch.object(ui, "run_menu", return_value=("back",)):
            with self.assertRaises(workflow.GoBack):
                workflow.choose(self.ITEMS)

    def test_parse_numbers_refuses_what_is_not_in_the_list(self):
        for bad in ("0", "6", "2-9", "x", ""):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                workflow._parse_numbers(bad, 5)


class ReportGroupsScreen(unittest.TestCase):
    GROUP = {"screens": ["A1", "B2"], "date": "yesterday"}
    PROFILES = [{"screen": c, "title": c, "learned": "x"} for c in ("A1", "B2", "C3")]

    def act(self, answers, scheduled=False):
        import gmes_schedule
        tasks = [{"batch": "morning"}] if scheduled else []
        with contextlib.redirect_stdout(io.StringIO()) as out, \
                mock.patch.object(ui, "INTERACTIVE", False), \
                mock.patch("builtins.input", side_effect=answers), \
                mock.patch.object(library, "run_history", return_value=[]), \
                mock.patch.object(gmes_schedule, "list_tasks", return_value=tasks), \
                mock.patch.object(gmes_schedule, "delete", return_value=True) as unschedule, \
                mock.patch.object(workflow.gmes_batch, "save_batch") as save, \
                mock.patch.object(workflow.gmes_batch, "delete_batch") as delete:
            ok = workflow.group_actions(workflow.Questions(), mock.Mock(), "morning",
                                        self.GROUP, self.PROFILES)
        return ok, save, delete, unschedule, out.getvalue()

    def test_changing_its_reports_saves_the_ticked_ones(self):
        _ok, save, _d, _u, _o = self.act(["2", "1,3"])
        save.assert_called_once_with("morning", ["A1", "C3"], "yesterday", "xlsx")

    def test_changing_its_dates(self):
        _ok, save, _d, _u, _o = self.act(["3", "2"])            # Today
        save.assert_called_once_with("morning", ["A1", "B2"], "today", "xlsx")

    def test_rename_is_refused_while_a_schedule_points_at_the_old_name(self):
        ok, save, delete, _u, out = self.act(["5", "evening"], scheduled=True)
        self.assertFalse(ok)
        save.assert_not_called()
        delete.assert_not_called()
        self.assertIn("remove its schedule first", out)

    def test_rename_saves_the_new_name_then_removes_the_old(self):
        _ok, save, delete, _u, _o = self.act(["5", "evening"])
        save.assert_called_once_with("evening", ["A1", "B2"], "yesterday", "xlsx")
        delete.assert_called_once_with("morning")

    def test_delete_asks_first_and_takes_its_schedule_with_it(self):
        _ok, _s, delete, unschedule, _o = self.act(["6", "n"], scheduled=True)
        delete.assert_not_called()
        _ok, _s, delete, unschedule, _o = self.act(["6", "y"], scheduled=True)
        unschedule.assert_called_once_with("morning")
        delete.assert_called_once_with("morning")


class ScheduleActions(unittest.TestCase):
    TASK = {"batch": "morning", "state": "Ready", "trigger": "Daily", "last_ok": True}

    def act(self, answers, state="Ready"):
        import gmes_schedule
        task = dict(self.TASK, state=state)
        with contextlib.redirect_stdout(io.StringIO()), \
                mock.patch.object(ui, "INTERACTIVE", False), \
                mock.patch("builtins.input", side_effect=answers), \
                mock.patch.object(gmes_schedule, "list_tasks", return_value=[task]), \
                mock.patch.object(gmes_schedule, "run_now") as run_now, \
                mock.patch.object(gmes_schedule, "set_enabled") as set_enabled:
            workflow.show_schedules()
        return run_now, set_enabled

    def test_run_now(self):
        run_now, _ = self.act(["1", "1"])
        run_now.assert_called_once_with("morning")

    def test_pause_a_running_one_and_resume_a_paused_one(self):
        _, set_enabled = self.act(["1", "2"])
        set_enabled.assert_called_once_with("morning", False)
        _, set_enabled = self.act(["1", "2"], state="Disabled")
        set_enabled.assert_called_once_with("morning", True)


class FilterEditor(unittest.TestCase):
    def screen(self):
        info = {"filters": [
            {"label": "From", "column": "startDay", "visible": True, "value": ""},
            {"label": "Production Order", "column": "poNo", "visible": True, "value": ""},
            {"label": "Model", "column": "model", "visible": True, "value": "X"},
            {"label": "Hidden", "column": "h", "visible": False, "value": ""}],
            "unbound": [{"label": "Category", "control": "edtCategory", "value": ""}]}
        return types.SimpleNamespace(info=info)

    def test_candidates_are_the_screens_own_inputs_without_its_dates(self):
        with mock.patch.object(workflow.core, "date_targets",
                               return_value=(self.screen().info["filters"][0], None, [])):
            keys = [c[0] for c in workflow.filter_candidates(self.screen())]
        self.assertEqual(keys, ["Production Order", "Model", "Category"])

    def test_pick_give_a_value_repeat_and_done(self):
        answers = ["2", "011074232146", "3", "SM-A137F", "2", "", "1"]
        with contextlib.redirect_stdout(io.StringIO()), \
                mock.patch.object(ui, "INTERACTIVE", False), \
                mock.patch("builtins.input", side_effect=answers), \
                mock.patch.object(workflow.core, "date_targets",
                                  return_value=(self.screen().info["filters"][0], None, [])):
            result = workflow.filter_editor(workflow.Questions(), self.screen())
        # Production Order set, Model set, then Production Order blanked = removed
        self.assertEqual(result, {"Model": "SM-A137F"})


class ConfirmSavedSetup(unittest.TestCase):
    PROFILE = {"values": {"division": "VD", "from": "20260922", "to": "20260922",
                          "verify": "planYmd", "sets": {"Model": "X"}}}

    def confirm(self, answers, ok=True):
        run = mock.Mock(return_value=[{"ok": ok, "rows": 5, "error": None if ok else "boom"}])
        with contextlib.redirect_stdout(io.StringIO()) as out, \
                mock.patch.object(ui, "INTERACTIVE", False), \
                mock.patch("builtins.input", side_effect=answers), \
                mock.patch.object(workflow.gmes_profile, "load", return_value=self.PROFILE):
            result = workflow.confirm_saved_setup(None, "A1", run=run)
        return result, run, out.getvalue()

    def test_not_now_runs_nothing(self):
        result, run, _ = self.confirm(["2"])
        self.assertIsNone(result)
        run.assert_not_called()

    def test_the_check_runs_from_the_saved_setup_alone(self):
        result, run, out = self.confirm([""])                   # Enter = recommended
        self.assertTrue(result)
        spec = run.call_args.args[1][0]
        self.assertEqual((spec["division"], spec["date_from"], spec["verify"], spec["sets"]),
                         ("VD", "20260922", "planYmd", {"Model": "X"}))
        self.assertTrue(spec["trust_profile"])
        self.assertIn("SAVED SETUP CONFIRMED", out)

    def test_a_failed_check_says_so_in_plain_words(self):
        result, _run, out = self.confirm([""], ok=False)
        self.assertFalse(result)
        self.assertIn("DID NOT RUN BY ITSELF", out)


class SettingsAndSupport(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = os.path.join(self._tmp.name, "ui_settings.json")

    def test_missing_damaged_or_odd_settings_are_just_the_defaults(self):
        self.assertEqual(library.load_settings(self.path), library.DEFAULT_SETTINGS)
        with open(self.path, "w") as fh:
            fh.write("{not json")
        self.assertEqual(library.load_settings(self.path), library.DEFAULT_SETTINGS)
        with open(self.path, "w") as fh:
            fh.write('{"language": "fr", "plain": "yes"}')
        self.assertEqual(library.load_settings(self.path), library.DEFAULT_SETTINGS)

    def test_only_known_keys_with_allowed_values_are_saved(self):
        saved = library.save_settings({"language": "ar", "plain": True, "evil": 1,
                                       "open_folder_after": "maybe"}, self.path)
        self.assertEqual(saved, {"language": "ar", "plain": True, "open_folder_after": False})
        self.assertEqual(library.load_settings(self.path)["language"], "ar")

    def test_the_support_package_holds_no_secret_and_no_screenshot(self):
        import json as _json
        import zipfile
        logs = os.path.join(self._tmp.name, "logs")
        os.makedirs(logs)
        with open(os.path.join(logs, "gmes_20260923.log"), "w", encoding="utf-8") as fh:
            fh.write("step one\ntokenId=SECRET_TOKEN\npassword: SECRET_PW\n")
        run_path = os.path.join(self._tmp.name, "batch_1.json")
        with open(run_path, "w", encoding="utf-8") as fh:
            _json.dump({"results": [{"error": "Authorization: Bearer SECRET_BEARER"}]}, fh)
        with open(os.path.join(logs, "gmes_A1_x.png"), "wb") as fh:
            fh.write(b"png")
        with mock.patch.object(library.gmes_profile, "known", return_value=[{"screen": "A1"}]):
            path = library.support_package(root=self._tmp.name,
                                           out_dir=os.path.join(self._tmp.name, "out"),
                                           history=[{"path": run_path}], log_dir=logs)
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            text = "".join(z.read(n).decode("utf-8") for n in names)
        self.assertIn("about.json", names)
        self.assertTrue(any(n.startswith("log/") for n in names))
        self.assertFalse(any(n.endswith(".png") for n in names))
        for secret in ("SECRET_TOKEN", "SECRET_PW", "SECRET_BEARER"):
            self.assertNotIn(secret, text)
        self.assertIn("step one", text)
        self.assertIn('"A1"', text)

    def test_the_folder_opens_by_itself_when_the_person_asked_for_that(self):
        with contextlib.redirect_stdout(io.StringIO()), \
                mock.patch.object(library, "load_settings",
                                  return_value=dict(library.DEFAULT_SETTINGS, open_folder_after=True)), \
                mock.patch("builtins.input", side_effect=AssertionError("no question expected")), \
                mock.patch.object(library, "open_path", return_value=(True, "ok")) as opened:
            workflow.after_success(["C:/x/a.xlsx"], "C:/x")
        opened.assert_called_once_with("C:/x")

    def test_a_plain_screen_can_always_be_forced(self):
        with mock.patch.object(ui, "_interactive", return_value=True):
            ui.set_plain(True)
            self.assertFalse(ui.INTERACTIVE)
            ui.set_plain(False)
            self.assertTrue(ui.INTERACTIVE)
        ui.set_plain(True)                       # leave the suite as it found it
        ui.INTERACTIVE = False


class PlanTable(unittest.TestCase):
    def test_counts_ready_and_shows_why_something_is_skipped(self):
        plan = [types.SimpleNamespace(code="A1", title="Alpha", dates="20260922",
                                      blocked="", notes=[], ready=True),
                types.SimpleNamespace(code="B2", title="Beta", dates="",
                                      blocked="not set up on this PC", notes=[], ready=False)]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            ready = workflow.show_plan(plan, "yesterday")
        self.assertEqual(ready, 1)
        self.assertIn("not set up on this PC", " ".join(out.getvalue().split()))
        self.assertIn("Excel files only", out.getvalue())

if __name__ == "__main__":
    unittest.main(verbosity=2)

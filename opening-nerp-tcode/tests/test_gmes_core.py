"""
Offline tests for the parts of gmes_core.py that do not need a browser.

There is no mock for G-MES (CLAUDE.md 4.3), so the browser-driven half is
verified against the live system by hand. What CAN be tested offline is every
decision the core makes about a screen it has already read: which control a
name refers to, which field is a date, which grid holds the results, how wide
a date to write. Those are exactly the decisions whose failures are silent -
a filter matched to the wrong control, an eight-digit code mistaken for a
date - so each case below is one of those failures.

    python tests/test_gmes_core.py
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from copy import deepcopy
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gmes_core as core  # noqa: E402
import gmes_data  # noqa: E402


def _relative_path(path):
    """Python mirror of JS_DISCOVER's own relativePath(): strips everything
    up to and including the renumbered win*_N_NNN segment, so a fixture's
    default stable_path is derived the same way the real discovery computes
    one, rather than an independently-typed guess."""
    parts = path.split(".")
    for i, part in enumerate(parts):
        if re.fullmatch(r"win.*_\d+_\d+", part):
            return ".".join(parts[i + 1:])
    return path


def flt(column="", label="", control="edtThing", value="", visible=True,
        bound=True, dataset="dsFilterDVO", form="P1112WF00.xfdl.js",
        path="application.mainframe.winTest_0_1.form.divBasic.form",
        stable_path=None):
    return {"column": column, "label": label, "control": control, "value": value,
            "visible": visible, "bound": bound, "dataset": dataset, "form": form,
            "id": "win_0_1.form." + control, "kind": "", "path": path,
            "stable_path": stable_path if stable_path is not None else _relative_path(path)}


def grid(name, dataset, area, visible=True, form="P1112WM00.xfdl.js",
         path="application.mainframe.winTest_0_1.form.divResult.form",
         stable_path=None):
    return {"name": name, "dataset": dataset, "area": area, "visible": visible,
            "stable_path": (stable_path if stable_path is not None
                           else _relative_path(path)),
            "form": form, "path": path}


class _FakeClock:
    """A clock that only moves when the code sleeps - keeps a deadline-poll
    loop deterministic and instant instead of busy-spinning for real wall
    time (mirrors tests/test_legacy_hardening.py's own copy)."""

    def __init__(self, start=1000.0):
        self.now = start

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += max(seconds, 0.01)


class Words(unittest.TestCase):
    def test_splits_camel_case(self):
        self.assertEqual(core.words("paramFromDate"), {"param", "from", "date"})

    def test_splits_underscores_and_digits(self):
        self.assertIn("checked", core.words("_checked"))

    def test_vendor_code_does_not_contain_the_word_end(self):
        # "paramVendorCode" contains the letters "end". A substring match
        # classified it as the period's end date.
        self.assertNotIn("end", core.words("paramVendorCode"))


class DateDetection(unittest.TestCase):
    def test_named_date_column_is_a_date(self):
        self.assertTrue(core.is_date_field(flt(column="paramFromDate")))

    def test_ymd_and_ym_are_dates(self):
        self.assertTrue(core.is_date_field(flt(column="planYmd")))
        self.assertTrue(core.is_date_field(flt(column="stdYm")))

    def test_masked_control_holding_eight_digits_is_a_date(self):
        self.assertTrue(core.is_date_field(
            flt(column="paramPeriod1", control="mskDateFrom", value="20260908")))

    def test_an_eight_digit_code_in_a_text_box_is_not_a_date(self):
        # A Production Order is 12 digits, a vendor code can be 8. Treating
        # the shape of the value as proof would silently overwrite one with
        # today's date.
        self.assertFalse(core.is_date_field(
            flt(column="paramVendorCode", control="edtVendor", value="10748321")))

    def test_a_label_naming_a_date_is_enough(self):
        self.assertTrue(core.is_date_field(
            flt(column="paramP1", control="mskP1", label="Plan Date")))


class DateTargets(unittest.TestCase):
    def test_finds_a_from_to_pair(self):
        info = {"filters": [flt(column="paramFromDate"), flt(column="paramEndDate"),
                            flt(column="paramPo", control="edtPo")]}
        frm, to, singles = core.date_targets(info)
        self.assertEqual(frm["column"], "paramFromDate")
        self.assertEqual(to["column"], "paramEndDate")
        self.assertEqual(singles, [])

    def test_a_screen_with_one_date_reports_it_as_a_single(self):
        # The old rule matched only *fromdate*/*enddate*, so this screen was
        # reported as having no date filter and ran on whatever was in it.
        info = {"filters": [flt(column="planYmd")]}
        frm, to, singles = core.date_targets(info)
        self.assertIsNone(frm)
        self.assertIsNone(to)
        self.assertEqual([s["column"] for s in singles], ["planYmd"])

    def test_a_vendor_code_is_never_the_end_date(self):
        info = {"filters": [flt(column="paramFromDate"),
                            flt(column="paramVendorCode", control="edtVendor",
                                value="10748321")]}
        frm, to, _ = core.date_targets(info)
        self.assertEqual(frm["column"], "paramFromDate")
        self.assertIsNone(to)


class DateWidth(unittest.TestCase):
    def test_full_date_into_an_empty_field(self):
        self.assertEqual(core.fit_date_to_field("20260908", ""), "20260908")

    def test_month_field_gets_six_digits(self):
        # Writing eight digits into a YYYYMM field is accepted and the query
        # then answers something else.
        self.assertEqual(core.fit_date_to_field("20260908", "202608"), "202609")

    def test_year_field_gets_four(self):
        self.assertEqual(core.fit_date_to_field("20260908", "2025"), "2026")

    def test_a_formatted_existing_value_still_counts_as_eight(self):
        self.assertEqual(core.fit_date_to_field("20260908", "2026-08-01"), "20260908")

    # -- Open Item 41: an empty field falls back to a REMEMBERED width -----

    def test_an_empty_field_with_a_remembered_month_width_gets_six_digits(self):
        self.assertEqual(core.fit_date_to_field("20260908", "", remembered_width=6),
                         "202609")

    def test_an_empty_field_with_a_remembered_year_width_gets_four(self):
        self.assertEqual(core.fit_date_to_field("20260908", "", remembered_width=4),
                         "2026")

    def test_an_empty_field_with_no_remembered_width_still_defaults_to_eight(self):
        self.assertEqual(core.fit_date_to_field("20260908", "", remembered_width=None),
                         "20260908")

    def test_a_live_value_in_the_field_always_wins_over_a_remembered_width(self):
        # Live evidence right now beats a memory of what it used to be -
        # exactly the ordering fit_date_to_field()'s own docstring commits to.
        self.assertEqual(
            core.fit_date_to_field("20260908", "202608", remembered_width=4), "202609")

    def test_a_nonsense_remembered_width_is_ignored(self):
        self.assertEqual(core.fit_date_to_field("20260908", "", remembered_width=8),
                         "20260908")


class SetDateRangeUsesARememberedWidth(unittest.TestCase):
    """Screen.set_date_range() threads a profile's remembered field width
    (gmes_profile.field_ref()'s new "width" key, Open Item 41) through to
    fit_date_to_field(), so a field found empty on THIS run still gets the
    width an earlier non-empty sighting proved, instead of defaulting to
    eight digits for a YYYYMM field that merely happens to be blank now."""

    def make_screen(self, value=""):
        info = {"filters": [flt(column="stdYm", control="edtYm", value=value)],
                "unbound": []}
        return core.Screen(None, "TEST", {"menuId": "M", "winId": "W"}, info)

    def test_an_empty_field_uses_the_profiles_remembered_width(self):
        screen = self.make_screen(value="")
        profile = {"from": {"dataset": "dsFilterDVO", "column": "stdYm", "width": 6}}
        applied = {}
        with patch.object(core.Screen, "apply",
                          lambda self, flt, value: applied.setdefault("v", value)):
            screen.set_date_range("20260908", None, profile)
        self.assertEqual(applied["v"], "202609")

    def test_a_live_value_wins_over_the_remembered_width(self):
        screen = self.make_screen(value="202601")   # already non-empty right now
        profile = {"from": {"dataset": "dsFilterDVO", "column": "stdYm", "width": 4}}
        applied = {}
        with patch.object(core.Screen, "apply",
                          lambda self, flt, value: applied.setdefault("v", value)):
            screen.set_date_range("20260908", None, profile)
        self.assertEqual(applied["v"], "202609")     # six, from the LIVE value - not four

    def test_no_profile_means_no_remembered_width_to_fall_back_on(self):
        # No profile -> fresh discovery (date_targets()); stdYm/edtYm carries
        # no from/to naming, so it is a single date field - both values must
        # agree, matching set_date_range()'s own single-field rule.
        screen = self.make_screen(value="")
        applied = {}
        with patch.object(core.Screen, "apply",
                          lambda self, flt, value: applied.setdefault("v", value)):
            screen.set_date_range("20260908", "20260908", profile=None)
        self.assertEqual(applied["v"], "20260908")   # the tool's ordinary default


class NormaliseDate(unittest.TestCase):
    def test_accepts_the_ways_people_type_it(self):
        for raw in ("20260907", "2026-09-07", "2026/09/07", " 2026.09.07 "):
            self.assertEqual(core.normalise_date(raw), "20260907")

    def test_blank_is_none_not_an_error(self):
        self.assertIsNone(core.normalise_date(""))
        self.assertIsNone(core.normalise_date(None))

    def test_rejects_a_date_that_does_not_exist(self):
        with self.assertRaises(ValueError):
            core.normalise_date("20260231")

    def test_rejects_a_partial_date_rather_than_guessing(self):
        with self.assertRaises(ValueError):
            core.normalise_date("2026-09")


class MatchFilter(unittest.TestCase):
    def setUp(self):
        self.info = {
            "filters": [flt(column="paramPo", label="Production Order", control="edtPo"),
                        flt(column="paramFromDate", label="Plan Date", control="mskDateFrom"),
                        flt(column="paramTecoYn", label="TECO", control="cboTeco")],
            "unbound": [flt(column="", label="Lot", control="edtLotNo", bound=False)],
        }

    def test_by_column(self):
        self.assertEqual(core.match_filter(self.info, "paramPo")["control"], "edtPo")

    def test_by_label_case_insensitively(self):
        self.assertEqual(core.match_filter(self.info, "production order")["column"],
                         "paramPo")

    def test_by_control_name(self):
        self.assertEqual(core.match_filter(self.info, "cboTeco")["column"], "paramTecoYn")

    def test_an_unbound_control_is_reachable_by_name(self):
        # A screen that binds none of its filters used to be undriveable.
        found = core.match_filter(self.info, "Lot")
        self.assertEqual(found["control"], "edtLotNo")
        self.assertFalse(found["bound"])

    def test_unknown_name_returns_none(self):
        self.assertIsNone(core.match_filter(self.info, "nothing like this"))

    def test_ambiguous_partial_returns_every_candidate(self):
        info = {"filters": [flt(column="paramFromDate", label="Plan Date"),
                            flt(column="paramEndDate", label="Plan Date To")]}
        found = core.match_filter(info, "date")
        self.assertIsInstance(found, list)
        self.assertEqual(len(found), 2)


class DateColumnNaming(unittest.TestCase):
    """A result column is only reported as a date when it is NAMED like one.
    Live output once listed prodTime (a time), planWeekno (a week number) and
    modelDesc (a model code) as "dates that came back", because all three are
    six or eight digits."""

    def named_like_a_date(self, column):
        return bool(core.words(column) & core._DATE_WORDS)

    def test_real_date_columns(self):
        for column in ("planYmd", "createDate", "workDt", "stdYm"):
            self.assertTrue(self.named_like_a_date(column), column)

    def test_look_alikes_are_not_dates(self):
        for column in ("prodTime", "planWeekno", "modelDesc", "poNo"):
            self.assertFalse(self.named_like_a_date(column), column)

    def test_date_named_columns_offers_real_candidates_before_any_rows_exist(self):
        # This is what the --verify question hint uses before Inquiry has
        # ever run: no row values exist yet to confirm a date SHAPE, only
        # the dataset's own column names - so naming is all there is to go
        # on, and it must still exclude internal (_-prefixed) columns and
        # non-date look-alikes.
        columns = ["planYmd", "prodTime", "_rowType", "createDate", "poNo"]
        self.assertEqual(core.date_named_columns(columns), ["planYmd", "createDate"])


class InquirySettleTracker(unittest.TestCase):
    """The settle-decision core of poll_inquiry(). Two live findings shaped
    this (HISTORY.md Phase 65.2): replaying M4131UM00 with the same
    division right after recording it produced the same correct 10-row
    answer, and a direct trace of the poll loop proved the count did not
    merely fail to differ from the pre-click snapshot - it never changed
    on ANY single poll, 17+ seconds straight, with no other observable
    signal available on that screen. An unconfirmed-but-genuinely-stable
    count must still settle eventually - just with more patience than a
    confirmed one, not a five-minute failure on a result that was correct
    the whole time."""

    SETTLE = 4
    UNCONFIRMED = 12   # InquirySettle's default: settle_checks * 3

    def feed(self, before, polls, settle_checks=None, unconfirmed=None):
        """`before` is the pre-click snapshot; `polls` are the readings fed
        one per poll. Returns the settled count, or None if the sequence
        never settles."""
        tracker = core.InquirySettle(
            before, settle_checks=settle_checks or self.SETTLE,
            unconfirmed_settle_checks=unconfirmed)
        for count in polls:
            settled = tracker.step(count)
            if settled is not None:
                return settled
        return None

    def test_a_confirmed_change_settles_after_settle_checks_matches(self):
        # before=0, count changes to 875 and holds - needs settle_checks+1
        # matching reads (the arrival plus settle_checks repeats).
        self.assertIsNone(self.feed(0, [875] * self.SETTLE))
        self.assertEqual(self.feed(0, [875] * (self.SETTLE + 1)), 875)

    def test_an_unconfirmed_stable_count_still_settles_but_needs_more_reads(self):
        # before=10 - the live-traced case: the count never once differs
        # from `before` or from the previous poll. settle_checks alone is
        # not enough...
        self.assertIsNone(self.feed(10, [10] * (self.SETTLE + 1)))
        # ...but it does settle, given the longer unconfirmed threshold.
        self.assertEqual(self.feed(10, [10] * (self.UNCONFIRMED + 1)), 10)

    def test_a_genuinely_empty_result_settles_with_the_same_patience_as_an_unconfirmed_nonzero_one(self):
        # Live bug (HISTORY.md Phase 71): a date range with genuinely no
        # matching rows burned the full 300s max_wait and then failed,
        # every time, because zero used to be a hard exception that NEVER
        # settled regardless of how long it held. A brief run of stable
        # zeros is still a round trip in progress, not an answer...
        self.assertIsNone(self.feed(0, [0] * (self.SETTLE + 1)))
        # ...but zero is not otherwise special: given the same sustained
        # stability already trusted for an unconfirmed nonzero count, a
        # genuinely empty result must settle too, not hang forever.
        self.assertEqual(self.feed(0, [0] * (self.UNCONFIRMED + 1)), 0)

    def test_a_stale_nonzero_count_dropping_to_zero_is_not_trusted_fast(self):
        # HISTORY.md Phase 82.16, live-caught replaying Q2111UM00: `before`
        # was a stale 175 left over from an earlier query on the same
        # screen. Nexacro clears the dataset to 0 UNCONDITIONALLY the
        # instant Inquiry is clicked - true whether the real answer will be
        # 0 or 175 again - so seeing it drop away from the stale baseline
        # is proof the click registered, never proof the real answer has
        # arrived. Trusting it fast here previously reported "0 rows,
        # confirmed" in ~4 polls while the real answer (also 175) was still
        # in flight; a manual re-click of the identical query moments later
        # returned 175 correctly, proving the data was never the problem.
        # A stable zero must pay the same patience an unconfirmed-but-
        # never-seen-to-move nonzero count already pays, stale baseline or
        # not.
        self.assertIsNone(self.feed(7, [0] * (self.SETTLE + 1)))
        self.assertEqual(self.feed(7, [0] * (self.UNCONFIRMED + 1)), 0)

    def test_a_stale_nonzero_count_changing_to_a_new_nonzero_answer_still_settles_fast(self):
        # The symmetric case Phase 82.16 must NOT break: a stale count
        # actually changing to a DIFFERENT, real, nonzero answer remains
        # confident, fast-settling evidence - only zero itself is untrusted
        # as change evidence, never a genuine nonzero transition.
        self.assertEqual(self.feed(7, [42] * (self.SETTLE + 1)), 42)

    def test_a_count_that_never_stabilizes_never_settles(self):
        # Genuinely erratic - never the same value twice in a row - must
        # not be mistaken for settled just because time passed.
        self.assertIsNone(self.feed(0, list(range(1, 14))))


class TransactionProofDecisionLogic(unittest.TestCase):
    """HISTORY.md Phase 82.6, live-traced rather than guessed at: every
    Inquiry click on P1112UM00 produced one or more `POST .../nexacro.do`
    requests, each answered HTTP 200 with a real response body (519,530
    bytes for a 738-row query). `TransactionProof` is the pure decision
    logic fed these events; the live connection is a separate concern
    (`cdp_common.open_event_listener()`)."""

    def request_event(self, request_id, method="POST", url="http://x/mes4/pm/nexacro.do"):
        return {"method": "Network.requestWillBeSent",
               "params": {"requestId": request_id,
                          "request": {"method": method, "url": url}}}

    def response_event(self, request_id, status=200):
        return {"method": "Network.responseReceived",
               "params": {"requestId": request_id, "response": {"status": status}}}

    def test_unproven_before_any_events(self):
        self.assertFalse(core.TransactionProof().proven)

    def test_a_full_request_response_pair_proves_it(self):
        proof = core.TransactionProof()
        proof.feed([self.request_event("1"), self.response_event("1", 200)])
        self.assertTrue(proof.proven)
        self.assertEqual(proof.confirmed[0]["url"], "http://x/mes4/pm/nexacro.do")

    def test_a_request_with_no_response_yet_is_not_proof(self):
        proof = core.TransactionProof()
        proof.feed([self.request_event("1")])
        self.assertFalse(proof.proven)

    def test_events_can_arrive_across_separate_feed_calls(self):
        # drain_events() is polled repeatedly; the request and its response
        # will usually land in different batches.
        proof = core.TransactionProof()
        proof.feed([self.request_event("1")])
        proof.feed([self.response_event("1", 200)])
        self.assertTrue(proof.proven)

    def test_a_non_2xx_response_does_not_prove_it(self):
        proof = core.TransactionProof()
        proof.feed([self.request_event("1"), self.response_event("1", 500)])
        self.assertFalse(proof.proven)

    def test_a_get_request_to_the_same_url_does_not_count(self):
        # Nexacro's own transaction endpoint is always POSTed to; a GET
        # (a static asset, a redirect target) is not the same evidence.
        proof = core.TransactionProof()
        proof.feed([self.request_event("1", method="GET"), self.response_event("1", 200)])
        self.assertFalse(proof.proven)

    def test_an_unrelated_url_does_not_count(self):
        proof = core.TransactionProof()
        proof.feed([self.request_event("1", url="http://x/some/other/endpoint"),
                   self.response_event("1", 200)])
        self.assertFalse(proof.proven)

    def test_a_response_for_an_untracked_request_id_is_ignored_not_a_crash(self):
        proof = core.TransactionProof()
        proof.feed([self.response_event("never-requested", 200)])
        self.assertFalse(proof.proven)

    def test_multiple_confirmed_requests_all_accumulate(self):
        # Live-observed: one Inquiry click produced two - sm/nexacro.do
        # (9,000 bytes) and pm/nexacro.do (519,530 bytes).
        proof = core.TransactionProof()
        proof.feed([self.request_event("1", url="http://x/mes4/sm/nexacro.do"),
                   self.request_event("2", url="http://x/mes4/pm/nexacro.do"),
                   self.response_event("1", 200), self.response_event("2", 200)])
        self.assertTrue(proof.proven)
        self.assertEqual(len(proof.confirmed), 2)


class WatchNexacroTransactionLiveGlue(unittest.TestCase):
    """The live-connection half of Phase 82.6: opens a listener dedicated
    to events (never the connection also used for evaluate() calls - see
    cdp_common.open_event_listener()'s own docstring), polls it, and
    returns whatever TransactionProof concluded - never fatal on its own,
    since this is supplementary evidence, not the primary proof a caller
    depends on."""

    def test_returns_proven_as_soon_as_evidence_arrives(self):
        listener = Mock()
        events_by_call = iter([
            [],
            [{"method": "Network.requestWillBeSent",
             "params": {"requestId": "1",
                       "request": {"method": "POST", "url": "http://x/mes4/pm/nexacro.do"}}}],
            [{"method": "Network.responseReceived",
             "params": {"requestId": "1", "response": {"status": 200}}}],
        ])
        clock = _FakeClock()
        with patch.object(core.cdp_common, "open_event_listener", return_value=listener), \
             patch.object(core.cdp_common, "drain_events",
                          side_effect=lambda _l: next(events_by_call, [])), \
             patch.object(core, "time", clock):
            proof = core.watch_nexacro_transaction("ws://x", max_wait=30, poll_interval=1)
        self.assertTrue(proof.proven)
        listener.close.assert_called_once()

    def test_a_listener_that_cannot_be_opened_returns_unproven_not_a_crash(self):
        with patch.object(core.cdp_common, "open_event_listener",
                          side_effect=RuntimeError("Network domain refused")):
            proof = core.watch_nexacro_transaction("ws://x", max_wait=1)
        self.assertFalse(proof.proven)

    def test_gives_up_at_the_deadline_when_nothing_ever_arrives(self):
        listener = Mock()
        clock = _FakeClock()
        with patch.object(core.cdp_common, "open_event_listener", return_value=listener), \
             patch.object(core.cdp_common, "drain_events", return_value=[]), \
             patch.object(core, "time", clock):
            proof = core.watch_nexacro_transaction("ws://x", max_wait=5, poll_interval=1)
        self.assertFalse(proof.proven)
        listener.close.assert_called_once()


class ChooseGrid(unittest.TestCase):
    def test_the_only_grid_wins(self):
        info = {"grids": [grid("grdMain", "dsMasterProdPlan", 400000)]}
        chosen, rivals = core.choose_grid(info)
        self.assertEqual(chosen["dataset"], "dsMasterProdPlan")
        self.assertEqual(rivals, [])

    def test_a_hidden_grid_is_not_chosen_over_a_visible_one(self):
        info = {"grids": [grid("grdHidden", "dsOther", 900000, visible=False),
                          grid("grdMain", "dsMain", 400000)]}
        chosen, _ = core.choose_grid(info)
        self.assertEqual(chosen["name"], "grdMain")

    def test_a_comparable_second_grid_is_reported_not_hidden(self):
        # Master-detail screens have two. Picking the larger silently is a
        # coin toss, and the wrong side of it exports the wrong data.
        info = {"grids": [grid("grdMaster", "dsMaster", 400000),
                          grid("grdDetail", "dsDetail", 380000)]}
        chosen, rivals = core.choose_grid(info)
        self.assertEqual(chosen["name"], "grdMaster")
        self.assertEqual([r["name"] for r in rivals], ["grdDetail"])

    def test_many_grids_on_one_dataset_are_not_rivals(self):
        # HISTORY.md Phase 82.22, P3131UM00: nine grids on one dataset (tab
        # variants of the same rows) made the run refuse with "more than one
        # plausible result grid", naming seven copies of the same data.
        info = {"grids": [grid(f"grdDetail{n}", "dsShared", 400000) for n in range(1, 8)]}
        best, rivals = core.choose_grid(info)
        self.assertIsNotNone(best)
        self.assertEqual(rivals, [])

    def test_a_grid_on_a_different_dataset_is_still_a_rival_among_shared_ones(self):
        info = {"grids": [grid("grdA1", "dsShared", 400000),
                          grid("grdA2", "dsShared", 390000),
                          grid("grdOther", "dsOther", 380000)]}
        best, rivals = core.choose_grid(info)
        self.assertEqual([g["name"] for g in rivals], ["grdOther"])

    def test_a_small_second_grid_is_not_an_ambiguity(self):
        info = {"grids": [grid("grdMaster", "dsMaster", 400000),
                          grid("grdTiny", "dsTiny", 9000)]}
        _, rivals = core.choose_grid(info)
        self.assertEqual(rivals, [])

    def test_prefer_by_dataset_name_settles_it(self):
        info = {"grids": [grid("grdMaster", "dsMaster", 400000),
                          grid("grdDetail", "dsDetail", 380000)]}
        chosen, rivals = core.choose_grid(info, prefer="dsDetail")
        self.assertEqual(chosen["name"], "grdDetail")
        self.assertEqual(rivals, [])

    def test_an_unmatched_preference_does_not_fall_back_silently(self):
        info = {"grids": [grid("grdMaster", "dsMaster", 400000)]}
        chosen, rivals = core.choose_grid(info, prefer="dsNoSuchThing")
        self.assertIsNone(chosen)
        self.assertTrue(rivals)

    def test_no_grids_at_all(self):
        self.assertEqual(core.choose_grid({"grids": []}), (None, []))


class Names(unittest.TestCase):
    def test_a_report_title_with_a_slash_becomes_a_usable_file_name(self):
        self.assertEqual(core.safe_name("Production Plan by Order(Line)"),
                         "Production Plan by Order(Line)")
        self.assertNotIn("/", core.safe_name("Plan / Actual"))
        self.assertNotIn(":", core.safe_name("Shift: A"))

    def test_an_empty_title_still_yields_a_name(self):
        self.assertEqual(core.safe_name("   "), "report")

    def test_windows_reserved_device_names_are_not_used_bare(self):
        # Windows blocks CON, PRN, AUX, NUL, COM1-9, LPT1-9 EXACTLY,
        # regardless of extension - a report titled exactly one of these
        # is not implausible across 810 screens.
        for reserved in ("CON", "con", "PRN", "AUX", "NUL", "COM1", "LPT9"):
            name = core.safe_name(reserved)
            self.assertNotEqual(name.upper(), reserved.upper())

    def test_a_trailing_dot_or_space_is_stripped(self):
        # Windows silently drops a trailing dot/space from a filename too.
        self.assertEqual(core.safe_name("Report."), "Report")
        self.assertEqual(core.safe_name("Report "), "Report")


class Digits(unittest.TestCase):
    def test_a_formatted_date_and_a_raw_one_compare_equal(self):
        self.assertEqual(core.digits_only("2026-09-08"), core.digits_only("20260908"))


class IsPureNumber(unittest.TestCase):
    """`verify_rows()` and `apply()`'s did-it-take check both used to decide
    "compare by digits alone" on nothing more than "the text contains a
    digit somewhere" - which made digits_only("MODEL-A1") ==
    digits_only("MODEL-B1") (both "1") report a wrong value as verified,
    and a filter that silently landed as the wrong code as having "taken"
    correctly. Only a value that is ENTIRELY digits and separators may be
    reduced to its digits."""

    def test_dates_and_plain_numbers_are_pure(self):
        for text in ("20260908", "2026-09-08", "2026/09/08", "12:30", "011074232146"):
            self.assertTrue(core.is_pure_number(text), text)

    def test_an_alphanumeric_code_is_not_pure_even_with_a_digit_in_it(self):
        for text in ("MODEL-A1", "MODEL-B1", "SM-A137F", "P1112UM00"):
            self.assertFalse(core.is_pure_number(text), text)

    def test_the_exact_collision_that_was_silently_accepted(self):
        # digits_only alone cannot tell these apart - is_pure_number must
        # keep them off that path entirely.
        self.assertEqual(core.digits_only("MODEL-A1"), core.digits_only("MODEL-B1"))
        self.assertFalse(core.is_pure_number("MODEL-A1"))
        self.assertFalse(core.is_pure_number("MODEL-B1"))

    def test_verify_rows_rejects_a_different_alphanumeric_value(self):
        result = {"found": True, "columns": ["modelCode"],
                  "rows": [{"modelCode": "MODEL-B1"}]}
        with patch.object(core, "read_rows", return_value=result):
            seen, problem = core.verify_rows(None, "F", "DS", "modelCode", "MODEL-A1")
        self.assertIsNotNone(problem)
        self.assertIn("MODEL-A1", problem)


class ValuesMatch(unittest.TestCase):
    """values_match() is the shared comparison for verify_rows(), apply()
    and type_text(). Checking only ONE side's shape was not enough, and
    this project shipped that mistake twice: is_pure_number(expected)
    alone let MODEL-B1 verify against an expected MODEL-A1, and checking
    only the WANTED side the other way let a wanted "123" silently accept
    an actual "X123" - confirmed live, verify_rows(..., "123") against a
    row holding "X123" returned problem=None before this fix."""

    def test_both_sides_must_be_numbers_to_compare_as_digits(self):
        self.assertTrue(core.values_match("2026-09-08", "20260908"))
        self.assertFalse(core.values_match("MODEL-A1", "MODEL-B1"))

    def test_a_pure_wanted_value_does_not_silently_accept_an_alphanumeric_actual(self):
        # The exact live-reproduced mirror bug: "123" alone looks like a
        # number worth reducing to digits, but "X123" does not - so they
        # must NOT be compared as digits.
        self.assertFalse(core.values_match("123", "X123"))
        self.assertFalse(core.values_match("X123", "123"))

    def test_a_decimal_point_is_not_a_date_separator(self):
        # Live-confirmed: digits_only("1.2") == digits_only("12") == "12",
        # so a quantity field's decimal value could pass verification
        # against a completely different whole number.
        self.assertFalse(core.values_match("1.2", "12"))
        self.assertTrue(core.values_match("1.2", "1.2"))

    def test_a_leading_sign_is_not_a_date_separator(self):
        # Live-confirmed: digits_only("-1") == digits_only("1") == "1",
        # so a negative adjustment could pass verification against its own
        # positive value.
        self.assertFalse(core.values_match("-1", "1"))
        self.assertTrue(core.values_match("-1", "-1"))
        # A mid-string "-" is still a real date separator.
        self.assertTrue(core.values_match("2026-09-08", "20260908"))

    def test_exact_text_still_matches_case_insensitively(self):
        self.assertTrue(core.values_match("ABC", "abc"))
        self.assertFalse(core.values_match("ABC", "XYZ"))


class ClearStaleFailure(unittest.TestCase):
    """A leftover filter from an earlier run that REFUSES to clear used to
    be silently swallowed (`except RuntimeError: pass`) - the exact failure
    class this method exists to prevent, just one level down: instead of an
    old value never being asked to leave, it is a value that refused to
    leave when asked, and the run carried on anyway."""

    def make_screen(self, filter_value="Old PO", apply_raises=True):
        info = {
            "filters": [flt(column="poNo", control="edtPo", value=filter_value,
                            label="Production Order")],
            "unbound": [],
        }
        screen = core.Screen(ws=None, code="P1112UM00",
                             opened={"menuId": "M", "winId": "W"}, info=info)
        if apply_raises:
            screen.apply = lambda f, v: (_ for _ in ()).throw(
                RuntimeError("did not take"))
        else:
            screen.apply = lambda f, v: ""
        return screen

    def test_a_filter_that_refuses_to_clear_stops_the_run(self):
        screen = self.make_screen(apply_raises=True)
        with self.assertRaisesRegex(RuntimeError, "Old PO"):
            screen.clear_stale()

    def test_a_filter_that_clears_successfully_is_reported_not_raised(self):
        screen = self.make_screen(apply_raises=False)
        cleared = screen.clear_stale()
        self.assertEqual(cleared, ["Production Order=Old PO"])


class SetOptionDisabled(unittest.TestCase):
    """Live finding (HISTORY.md Phase 71.2): a left-panel BUTTON option's
    CSS class (`_Dis`/`_Default`) names its DESELECTED visual style, not
    whether it can be clicked - a real Korean-labeled option on P1112UM00
    ('실적일') showed as ordinary "not selected", but a click sent to its
    exact live coordinates changed nothing at all, because Nexacro's own
    `enable` flag was false the whole time (a screen-state precondition
    unrelated to the CSS class). set_option() must refuse BEFORE clicking
    a disabled option, not click it and then report the same generic
    "could not prove selected" a real detection bug would also produce."""

    def make_screen(self, options):
        screen = core.Screen(ws=None, code="P1112UM00",
                             opened={"menuId": "M", "winId": "W"}, info={})
        self.patcher = patch.object(core, "left_options",
                                    return_value={"options": options})
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.click_patcher = patch.object(core, "click_element_by_rect")
        self.click = self.click_patcher.start()
        self.addCleanup(self.click_patcher.stop)
        return screen

    def opt(self, label, state="not selected", enabled=True, kind="button",
            name=None):
        return {"label": label, "id": "x", "name": name or label, "path": "",
                "cls": "", "state": state,
                "kind": kind, "enabled": enabled, "x": 1, "y": 1}

    def test_a_disabled_option_is_refused_before_any_click(self):
        screen = self.make_screen([self.opt("실적일", enabled=False)])
        with self.assertRaisesRegex(RuntimeError, "disabled"):
            screen.set_option("실적일")
        self.click.assert_not_called()

    def test_an_enabled_option_missing_from_state_is_still_clickable(self):
        # Options discovered before this fix carry no "enabled" key at
        # all - .get("enabled", True) must default to clickable, not
        # silently refuse every option on an older code path.
        opt = self.opt("PLANT")
        del opt["enabled"]
        screen = self.make_screen([opt])
        discover_patcher = patch.object(core, "discover", return_value={})
        discover_patcher.start()
        self.addCleanup(discover_patcher.stop)
        with self.assertRaises(RuntimeError) as cm:
            # Still fails (the mocked left_options() never reports it
            # selected afterwards), but it must reach the click first -
            # proven both by the click being called and by the failure
            # NOT being the "disabled" refusal.
            screen.set_option("PLANT", verify_wait=0)
        self.assertNotIn("disabled", str(cm.exception))
        self.click.assert_called_once()

    def test_the_click_is_confirmed_by_name_even_if_the_label_changes(self):
        # The click can rebuild the left panel, and a rebuilt panel is exactly
        # where a label could come back rendered differently. Confirming by
        # label would then fail to find a control that WAS successfully
        # selected, and report "could not prove selected" for a click that
        # worked (HISTORY.md Phase 76.3).
        before = self.opt("Create Date", name="btnCreate")
        after = self.opt("생성일", name="btnCreate", state="selected")
        screen = core.Screen(ws=None, code="P1112UM00",
                             opened={"menuId": "M", "winId": "W"}, info={})
        panels = iter([{"options": [before]}, {"options": [after]}])
        with patch.object(core, "left_options",
                          side_effect=lambda _ws: next(panels, {"options": [after]})), \
             patch.object(core, "click_element_by_rect"), \
             patch.object(core, "discover", return_value={}), \
             patch.object(core.time, "sleep"):
            result = screen.set_option("Create Date", verify_wait=5)
        self.assertEqual(result["name"], "btnCreate")
        self.assertIn("selected", result["outcome"])


#: The real left panel of P1112UM00, read live from the running screen on
#: 2026-09-16 while G-MES rendered Korean. Every `name` here is the control's
#: own Nexacro name, and every label is what that control displayed. This is
#: the fixture the whole phase turns on, so it is real data rather than
#: invented data: the point being proved is that `생성일` and `btnCreate` are
#: the same control, and only the live screen can say that.
LIVE_P1112UM00_OPTIONS = [
    ("조회", "btnSearch"),
    ("Org", "tabTitle_Org"),
    ("Prod", "tabTitle_Prod"),
    ("Fac", "tabTitle_Fac"),
    ("Proc", "tabTitle_Proc"),
    ("STD", "btnstd"),
    ("PLANT", "btnplant"),
    ("과거 조직도 포함", "chkDisuseYn"),
    ("실적일", "btnProduce"),
    ("계획일", "btnPlan"),
    ("생성일", "btnCreate"),
    ("DB 조회", "chkPoSearch"),
    ("일반 검색", "btnSearchNormal"),
    ("비교 검색", "btnSearchCompare"),
    ("OI", "btnOI"),
]

#: The same panel as it renders in English, for the reverse direction. Labels
#: from GMES_SKILL.md #32, names unchanged - which is the property under test.
ENGLISH_P1112UM00_OPTIONS = [
    ("Inquiry", "btnSearch"),
    ("Org", "tabTitle_Org"),
    ("Prod", "tabTitle_Prod"),
    ("Fac", "tabTitle_Fac"),
    ("Proc", "tabTitle_Proc"),
    ("STD", "btnstd"),
    ("PLANT", "btnplant"),
    ("Including Past Org.", "chkDisuseYn"),
    ("Actual Date", "btnProduce"),
    ("Plan Date", "btnPlan"),
    ("Create Date", "btnCreate"),
    ("DB Search", "chkPoSearch"),
    ("General", "btnSearchNormal"),
    ("Compare", "btnSearchCompare"),
    ("OI", "btnOI"),
]


def panel(pairs, selected=()):
    """A left_options() result from (label, name) pairs."""
    return [{"label": label, "name": name, "id": f"win_0_1.form.{name}",
             "path": f"form.divLeft.form.{name}", "cls": "", "kind": "button",
             "state": "selected" if name in selected else "not selected",
             "enabled": True, "x": 1, "y": 1}
            for label, name in pairs]


class OptionKeys(unittest.TestCase):
    """The semantic key is the component name with the Nexacro control-type
    prefix removed. Names are authored in the screen's XFDL, so they are the
    one part of a left-panel option that does not change with the UI
    language (HISTORY.md Phase 76)."""

    def test_the_real_control_names_normalise_as_expected(self):
        for name, expected in (("btnCreate", "create"), ("btnPlan", "plan"),
                               ("btnProduce", "produce"), ("btnstd", "std"),
                               ("btnplant", "plant"), ("chkDisuseYn", "disuseyn"),
                               ("chkPoSearch", "posearch"), ("btnOI", "oi"),
                               ("btnSearchNormal", "searchnormal")):
            with self.subTest(name=name):
                self.assertEqual(core.option_key(name), expected)

    def test_tabtitle_is_stripped_before_the_shorter_tab_prefix(self):
        # Stripping "tab" first would leave "title_org".
        self.assertEqual(core.option_key("tabTitle_Org"), "org")
        self.assertEqual(core.option_key("tabTitle_Proc"), "proc")

    def test_keys_are_unique_across_the_real_panel(self):
        # If two options shared a key the resolver would report ambiguity and
        # refuse, which is safe but useless - so this is worth knowing.
        keys = [core.option_key(n) for _l, n in LIVE_P1112UM00_OPTIONS]
        self.assertEqual(len(keys), len(set(keys)), keys)

    def test_a_missing_or_odd_name_does_not_raise(self):
        for value in (None, "", "   ", "btn", "___"):
            with self.subTest(value=value):
                self.assertIsInstance(core.option_key(value), str)

    def test_the_generated_js_actually_reports_the_stable_identity(self):
        # The JS cannot be executed offline, so this pins its SOURCE. Without
        # `name` and `path` coming back from the browser there is no
        # language-independent identity to store and the whole phase is
        # decorative - and every offline test would still pass, because they
        # all build their panels from fixtures.
        js = core.JS_LEFT_OPTIONS
        self.assertIn("name: name", js)
        self.assertIn("path: relativePath(id)", js)
        self.assertIn("obj.name", js)
        # The window segment must be stripped, or the path embeds an instance
        # number that changes on every open (GMES_SKILL.md #7).
        self.assertIn("relativePath", js)
        self.assertIn("win", js)

    def test_the_relative_path_strips_a_renumbered_window_segment(self):
        # The regex in JS_LEFT_OPTIONS, mirrored here so its INTENT is tested:
        # a real id from the live screen must lose everything up to and
        # including winPPM0219_0_926.
        live = ("mainframe.vFrameSet1.vFrameSet2.hFrameSet1.workFrameSet."
                "winPPM0219_0_926.form.divLeft.form.divLeftSub.form.divFilter."
                "form.divWidgetMain.form.divWidgetFilterPPM0222.form.divBasic."
                "form.btnCreate")
        parts = live.split(".")
        index = next(i for i, p in enumerate(parts)
                     if re.match(r"^win.*_\d+_\d+$", p))
        relative = ".".join(parts[index + 1:])
        self.assertTrue(relative.startswith("form.divLeft"))
        self.assertTrue(relative.endswith("btnCreate"))
        self.assertNotIn("winPPM0219", relative)


class ReplayAcrossLanguages(unittest.TestCase):
    """The reported failure, reproduced exactly.

    P1112UM00 was recorded with the option "Create Date" while G-MES rendered
    English. The same screen now renders Korean, where that control reads
    `생성일`, and replay stopped with "no left-panel option called 'Create
    Date'. Available: 조회, Org, Prod, ...". The control was there the whole
    time, and is still named `btnCreate`."""

    KOREAN = panel(LIVE_P1112UM00_OPTIONS)
    ENGLISH = panel(ENGLISH_P1112UM00_OPTIONS)

    def test_the_exact_reported_failure_now_resolves(self):
        option, how = core.resolve_option(self.KOREAN, "Create Date")
        self.assertEqual(option["name"], "btnCreate")
        self.assertEqual(option["label"], "생성일")
        self.assertEqual(how, "english alias")

    def test_a_migrated_profile_replays_on_the_korean_screen(self):
        # The pre-Phase-76 profile shape, verbatim: a bare English label.
        option, _how = core.resolve_option(self.KOREAN, "Create Date")
        identity = core.option_identity(option)
        self.assertEqual(identity["key"], "create")
        # ...and once healed, it resolves by identity, with no reliance on
        # language at all - in EITHER rendering.
        for rendering, expected in ((self.KOREAN, "생성일"),
                                    (self.ENGLISH, "Create Date")):
            with self.subTest(label=expected):
                again, how = core.resolve_option(rendering, identity)
                self.assertEqual(again["name"], "btnCreate")
                self.assertEqual(again["label"], expected)
                self.assertEqual(how, "component name")

    def test_a_profile_recorded_in_korean_replays_in_english(self):
        # The reverse direction matters just as much: whoever records next
        # will record Korean labels, and English must not then break.
        recorded = core.option_identity(
            core.resolve_option(self.KOREAN, "생성일")[0])
        option, how = core.resolve_option(self.ENGLISH, recorded)
        self.assertEqual(option["label"], "Create Date")
        self.assertEqual(how, "component name")

    def test_every_english_label_resolves_on_the_korean_panel_or_refuses(self):
        # Blanket sweep. Each English label must either find its own control
        # or fail - never resolve to a DIFFERENT control, which is the only
        # outcome that could silently change what a report means.
        for label, name in ENGLISH_P1112UM00_OPTIONS:
            with self.subTest(label=label):
                try:
                    option, _how = core.resolve_option(self.KOREAN, label)
                except RuntimeError:
                    continue            # refused; safe
                self.assertEqual(
                    option["name"], name,
                    f"{label!r} resolved to {option['name']} instead of {name}")

    def test_the_identity_survives_a_renumbered_window(self):
        # Window ids are renumbered on every open (GMES_SKILL.md #7), so the
        # identity must not contain one.
        recorded = core.option_identity(
            core.resolve_option(self.KOREAN, "생성일")[0])
        moved = [dict(o, id=o["id"].replace("win_0_1", "winPPM0219_0_926"))
                 for o in self.KOREAN]
        option, _how = core.resolve_option(moved, recorded)
        self.assertEqual(option["name"], "btnCreate")


class OptionResolutionSafety(unittest.TestCase):
    """Never guess between several matches, and never resolve to the wrong
    control (CLAUDE.md 3.9)."""

    KOREAN = panel(LIVE_P1112UM00_OPTIONS)

    def test_plan_and_plant_are_never_confused(self):
        # A prefix rule would match "PLANT" against btnPlan as well. Both of
        # these exist on the real screen and mean entirely different things.
        self.assertEqual(
            core.resolve_option(self.KOREAN, "PLANT")[0]["name"], "btnplant")
        self.assertEqual(
            core.resolve_option(self.KOREAN, "Plan Date")[0]["name"], "btnPlan")

    def test_including_past_org_does_not_resolve_to_the_org_tab(self):
        # The rejected "key appears anywhere in the request" rule matched the
        # word "org" in "Including Past Org." and picked the Org CATEGORY TAB -
        # a confidently wrong match that silently changes the query. Refusing
        # is the correct outcome here.
        with self.assertRaises(RuntimeError) as cm:
            core.resolve_option(self.KOREAN, "Including Past Org.")
        self.assertNotIn("tabTitle_Org", str(cm.exception).split("offers")[0])

    def test_two_controls_sharing_a_key_are_refused_not_guessed(self):
        ambiguous = panel([("생성일", "btnCreate"), ("Created", "btnCreate")])
        with self.assertRaisesRegex(RuntimeError, "Refusing to guess"):
            core.resolve_option(ambiguous, "Create Date")

    def test_the_failure_message_lists_every_key_to_use_instead(self):
        with self.assertRaises(RuntimeError) as cm:
            core.resolve_option(self.KOREAN, "No Such Option")
        message = str(cm.exception)
        self.assertIn("[create]", message)
        self.assertIn("[plant]", message)
        self.assertIn("language-independent", message)

    def test_an_empty_panel_refuses_rather_than_crashing(self):
        with self.assertRaises(RuntimeError):
            core.resolve_option([], "Create Date")
        with self.assertRaises(RuntimeError):
            core.resolve_option([], {"key": "create", "name": "btnCreate"})

    def test_a_vanished_control_is_reported_not_silently_skipped(self):
        # The screen changed and the remembered control is simply gone.
        without = panel([p for p in LIVE_P1112UM00_OPTIONS if p[1] != "btnCreate"])
        with self.assertRaises(RuntimeError) as cm:
            core.resolve_option(without, {"key": "create", "name": "btnCreate",
                                          "path": "", "label": "생성일"})
        self.assertIn("no left-panel option matching", str(cm.exception))

    def test_the_key_resolves_even_when_the_name_was_renamed(self):
        # Identity is tried name -> path -> key, so a control renamed between
        # Nexacro versions still resolves by its normalised key.
        renamed = panel([("생성일", "btnCreateDate")])
        option, how = core.resolve_option(
            renamed, {"key": "createdate", "name": "btnCreate", "path": "",
                      "label": "Create Date"})
        self.assertEqual(option["name"], "btnCreateDate")
        self.assertEqual(how, "semantic key")

    def test_a_korean_label_still_works_as_a_direct_request(self):
        # Someone reading the screen types what they see.
        option, how = core.resolve_option(self.KOREAN, "생성일")
        self.assertEqual(option["name"], "btnCreate")
        self.assertEqual(how, "label")

    def test_the_semantic_key_can_be_passed_directly(self):
        option, how = core.resolve_option(self.KOREAN, "create")
        self.assertEqual(option["name"], "btnCreate")
        self.assertEqual(how, "semantic key")


class OptionProfileEntries(unittest.TestCase):
    """What a profile stores, and what it accepts back."""

    def test_an_identity_keeps_the_label_as_display_metadata_only(self):
        identity = core.option_identity(
            {"name": "btnCreate", "path": "form.x.btnCreate", "label": "생성일"})
        self.assertEqual(identity, {"key": "create", "name": "btnCreate",
                                    "path": "form.x.btnCreate", "label": "생성일"})

    def test_a_dict_entry_round_trips_through_the_profile(self):
        import gmes_profile
        entry = gmes_profile._option_entry(
            {"key": "create", "name": "btnCreate", "path": "p", "label": "생성일"})
        self.assertEqual(entry["name"], "btnCreate")
        self.assertEqual(entry["key"], "create")

    def test_a_bare_string_is_still_accepted_and_kept(self):
        # Every profile written before Phase 76 holds exactly this.
        import gmes_profile
        self.assertEqual(gmes_profile._option_entry("Create Date"), "Create Date")

    def test_an_entry_with_no_identity_degrades_to_its_label(self):
        # Storing {"key": "", "name": ""} would look like an identity and
        # resolve to nothing; a label at least migrates.
        import gmes_profile
        self.assertEqual(
            gmes_profile._option_entry({"key": "", "name": "", "label": "Create Date"}),
            "Create Date")

    def test_display_handles_both_shapes(self):
        self.assertEqual(core.option_display("Create Date"), "Create Date")
        self.assertIn("create", core.option_display(
            {"key": "create", "label": "생성일"}))
        self.assertEqual(core.option_display({"key": "create"}), "create")



class RedactSensitiveColumns(unittest.TestCase):
    """CLAUDE.md 2.3: a G-MES dataset can carry `tokenId`/`refreshTokenId` -
    full session JWTs - in an ordinary-looking form. Console/log output was
    already redacted (gmes_log.py's own _SECRET regex); CSV export was not,
    until this fix."""

    def test_credential_shaped_columns_are_withheld(self):
        cols = ["poNo", "tokenId", "refreshTokenId", "Authorization",
                "planYmd", "cookie_session", "_rowType"]
        safe, dropped = gmes_data.redact_sensitive_columns(cols)
        self.assertEqual(safe, ["poNo", "planYmd"])
        self.assertEqual(set(dropped),
                         {"tokenId", "refreshTokenId", "Authorization", "cookie_session"})

    def test_the_word_does_not_have_to_be_at_the_start(self):
        # refreshTokenId is CLAUDE.md 2.3's own named example, and the word
        # "Token" sits in the MIDDLE of it, not the start - a first version
        # of this filter used re.match(), which only ever anchors at
        # position 0 regardless of the pattern, and missed it.
        safe, dropped = gmes_data.redact_sensitive_columns(["refreshTokenId"])
        self.assertEqual(safe, [])
        self.assertEqual(dropped, ["refreshTokenId"])

    def test_ordinary_columns_are_untouched(self):
        cols = ["poNo", "modelCode", "planYmd", "qty"]
        safe, dropped = gmes_data.redact_sensitive_columns(cols)
        self.assertEqual(safe, cols)
        self.assertEqual(dropped, [])

    def test_previously_missed_credential_shaped_names_are_now_caught(self):
        # HISTORY.md Open Item 39: the original short word list
        # (password/passwd/pwd/token/secret/authorization/cookie) missed all
        # of these plausible real column names.
        cols = ["credentialId", "sessionKey", "sessionId", "jwtPayload",
                "apiKey", "accessKey", "authKey", "bearerToken"]
        safe, dropped = gmes_data.redact_sensitive_columns(cols)
        self.assertEqual(safe, [])
        self.assertEqual(set(dropped), set(cols))

    def test_verify_rows_rejects_a_pure_expected_value_against_an_alphanumeric_row(self):
        result = {"found": True, "columns": ["poNo"], "rows": [{"poNo": "X123"}]}
        with patch.object(core, "read_rows", return_value=result):
            seen, problem = core.verify_rows(None, "F", "DS", "poNo", "123")
        self.assertIsNotNone(problem)
        self.assertIn("123", problem)


class CsvFormulaInjectionIsEscaped(unittest.TestCase):
    """A CSV opened directly in Excel/Sheets treats a leading =, +, -, or @ as
    a formula (CWE-1236). No malicious value has been observed on a real
    G-MES screen, but a value that merely LOOKS like one (a stray leading "="
    typed into a free-text field) would still be silently executed the moment
    the file opens. Every CSV writer in this project now escapes it."""

    def test_a_leading_equals_is_escaped(self):
        self.assertEqual(gmes_data.escape_formula_cell("=SUM(A1:A9)"),
                         "'=SUM(A1:A9)")

    def test_each_dangerous_leading_character_is_escaped(self):
        for prefix in ("=", "+", "-", "@", "\t", "\r"):
            value = f"{prefix}cmd"
            self.assertEqual(gmes_data.escape_formula_cell(value), f"'{value}", value)

    def test_a_genuine_negative_number_is_left_alone(self):
        self.assertEqual(gmes_data.escape_formula_cell("-123.45"), "-123.45")

    def test_a_genuine_positive_number_is_left_alone(self):
        self.assertEqual(gmes_data.escape_formula_cell("+7"), "+7")

    def test_an_ordinary_value_is_unchanged(self):
        self.assertEqual(gmes_data.escape_formula_cell("MODEL-A1"), "MODEL-A1")

    def test_none_and_empty_are_passed_through(self):
        self.assertIsNone(gmes_data.escape_formula_cell(None))
        self.assertEqual(gmes_data.escape_formula_cell(""), "")

    def test_safe_rows_for_csv_escapes_every_column_independently(self):
        rows = [{"a": "=1+1", "b": "ok", "c": "-5"}]
        out = gmes_data.safe_rows_for_csv(rows, ["a", "b", "c"])
        self.assertEqual(out, [{"a": "'=1+1", "b": "ok", "c": "-5"}])

    def test_safe_rows_for_csv_restricts_to_the_given_columns(self):
        rows = [{"a": "1", "secretDroppedEarlier": "x"}]
        out = gmes_data.safe_rows_for_csv(rows, ["a"])
        self.assertEqual(out, [{"a": "1"}])


class PagedDatasetReads(unittest.TestCase):
    """HISTORY.md Open Item 44: read_dataset() built one JSON object for every
    row and column in a single Runtime.evaluate call, with no ceiling. Above
    PAGE_ROWS, a caller asking for every row (the default - export, verify,
    the daily job) is now read in fixed-size pages instead."""

    def test_a_small_dataset_costs_exactly_one_call(self):
        # The ordinary case (every screen recorded so far): must not pay for
        # an extra probe just to learn it did not need to page.
        rows = [{"poNo": str(i)} for i in range(20)]
        with patch.object(gmes_data, "PAGE_ROWS", 2000), \
             patch.object(gmes_data, "evaluate",
                          side_effect=[{"found": True, "columns": ["poNo"],
                                       "total": 20, "rows": rows}]) as ev:
            result = gmes_data.read_dataset(Mock(), "P1112UM00", "dsX")
        self.assertEqual(ev.call_count, 1)
        self.assertEqual(len(result["rows"]), 20)
        self.assertTrue(result["found"])

    def test_a_dataset_larger_than_one_page_is_stitched_together(self):
        page1 = {"found": True, "columns": ["poNo"], "total": 5,
                 "rows": [{"poNo": "1"}, {"poNo": "2"}]}
        page2 = {"found": True, "columns": ["poNo"], "total": 5,
                 "rows": [{"poNo": "3"}, {"poNo": "4"}]}
        page3 = {"found": True, "columns": ["poNo"], "total": 5,
                 "rows": [{"poNo": "5"}]}
        with patch.object(gmes_data, "PAGE_ROWS", 2), \
             patch.object(gmes_data, "evaluate", side_effect=[page1, page2, page3]) as ev:
            result = gmes_data.read_dataset(Mock(), "P1112UM00", "dsX")
        self.assertEqual(ev.call_count, 3)
        self.assertEqual([r["poNo"] for r in result["rows"]], ["1", "2", "3", "4", "5"])
        self.assertTrue(result["found"])

    def test_a_dataset_that_changes_size_mid_read_is_refused(self):
        page1 = {"found": True, "columns": ["poNo"], "total": 5,
                 "rows": [{"poNo": "1"}, {"poNo": "2"}]}
        page2 = {"found": True, "columns": ["poNo"], "total": 9,   # grew mid-read
                 "rows": [{"poNo": "3"}, {"poNo": "4"}]}
        with patch.object(gmes_data, "PAGE_ROWS", 2), \
             patch.object(gmes_data, "evaluate", side_effect=[page1, page2]):
            result = gmes_data.read_dataset(Mock(), "P1112UM00", "dsX")
        self.assertFalse(result["found"])
        self.assertIn("changed size", result["reason"])

    def test_a_dataset_that_disappears_mid_read_is_reported_not_crashed(self):
        page1 = {"found": True, "columns": ["poNo"], "total": 5,
                 "rows": [{"poNo": "1"}, {"poNo": "2"}]}
        gone = {"found": False}
        with patch.object(gmes_data, "PAGE_ROWS", 2), \
             patch.object(gmes_data, "evaluate", side_effect=[page1, gone]):
            result = gmes_data.read_dataset(Mock(), "P1112UM00", "dsX")
        self.assertFalse(result["found"])

    def test_a_dataset_not_found_at_all_short_circuits_with_no_further_calls(self):
        with patch.object(gmes_data, "PAGE_ROWS", 2), \
             patch.object(gmes_data, "evaluate",
                          side_effect=[{"found": False}]) as ev:
            result = gmes_data.read_dataset(Mock(), "P1112UM00", "dsX")
        self.assertEqual(ev.call_count, 1)
        self.assertFalse(result["found"])

    def test_a_bounded_limit_or_offset_is_never_paged(self):
        # limit=0 shape probes and verify's small reads must stay the
        # original single call, unpaged, regardless of PAGE_ROWS.
        with patch.object(gmes_data, "PAGE_ROWS", 1), \
             patch.object(gmes_data, "evaluate",
                          return_value={"found": True, "columns": [], "total": 500,
                                       "rows": []}) as ev:
            gmes_data.read_dataset(Mock(), "P1112UM00", "dsX", limit=0)
            gmes_data.read_dataset(Mock(), "P1112UM00", "dsX", limit=-1, offset=10)
        self.assertEqual(ev.call_count, 2)

    def test_a_page_reporting_zero_new_rows_stops_instead_of_looping_forever(self):
        page1 = {"found": True, "columns": ["poNo"], "total": 5,
                 "rows": [{"poNo": "1"}, {"poNo": "2"}]}
        stuck = {"found": True, "columns": ["poNo"], "total": 5, "rows": []}
        with patch.object(gmes_data, "PAGE_ROWS", 2), \
             patch.object(gmes_data, "evaluate", side_effect=[page1, stuck]):
            result = gmes_data.read_dataset(Mock(), "P1112UM00", "dsX")
        # Never hangs - and never hands back 2 of 5 rows as found=True either
        # (HISTORY.md Phase 91.1: Screen.to_csv() reported that as complete).
        self.assertFalse(result["found"])
        self.assertEqual(result["rows"], [])
        self.assertIn("truncated", result["reason"])

    def test_pages_that_do_not_add_up_to_the_total_are_refused(self):
        # A page that overshoots (the dataset shrank and re-grew between
        # reads, or a page ignored its limit) must not pass as the answer.
        page1 = {"found": True, "columns": ["poNo"], "total": 3,
                 "rows": [{"poNo": "1"}, {"poNo": "2"}]}
        page2 = {"found": True, "columns": ["poNo"], "total": 3,
                 "rows": [{"poNo": "3"}, {"poNo": "3"}]}
        with patch.object(gmes_data, "PAGE_ROWS", 2), \
             patch.object(gmes_data, "evaluate", side_effect=[page1, page2]):
            result = gmes_data.read_dataset(Mock(), "P1112UM00", "dsX")
        self.assertFalse(result["found"])
        self.assertIn("does not add up", result["reason"])


class ScreenCodeShape(unittest.TestCase):
    """open_screen() reuses an already-open tab only for a full screen code or
    menu id. A partial one would match several open screens and pick whichever
    came first; a name has to go through the catalogue, which validates it."""

    def looks_like_a_code(self, text):
        # The REAL function, not a copy of its pattern: a copy keeps passing
        # after the source is changed or deleted.
        import gmes_profile
        return gmes_profile.looks_like_code(text)

    def test_real_codes_are_recognised(self):
        for code in ("P1112UM00", "P1112WM00", "Q2241UM00", "PPM0219"):
            self.assertTrue(self.looks_like_a_code(code), code)

    def test_every_shape_found_in_the_live_catalogue_is_recognised(self):
        # 129 of 810 real screens were refused by "1-4 letters then 4+ digits"
        # (HISTORY.md Phase 84.20). One of each shape the catalogue holds.
        for code in ("BB210UM00", "BB210WM01", "M4A11UM00", "M13A1UM00",
                     "P225AUM00", "P321AWM00", "L311AUM00", "L432BUM00",
                     "WP00067", "M4B41WM03"):
            self.assertTrue(self.looks_like_a_code(code), code)

    def test_a_screen_can_be_saved_under_those_codes(self):
        import gmes_profile
        for code in ("BB210UM00", "M4A11UM00", "P225AUM00"):
            self.assertTrue(gmes_profile.path_for(code).endswith(code + ".json"), code)

    def test_nothing_that_could_escape_a_folder_is_a_code(self):
        import gmes_profile
        for bad in ("..\\x", "../x", "a/b", "C:\\x", "P1112UM00.json", "P1112 UM00",
                    "AAAAAA_AAA", "x" * 17, "", "P111"):
            self.assertFalse(self.looks_like_a_code(bad), bad)
            if bad.strip():
                with self.assertRaises(ValueError):
                    gmes_profile.path_for(bad)

    def test_a_word_without_a_digit_is_a_name_not_a_code(self):
        for word in ("Monitoring", "Calendar", "Certification"):
            self.assertFalse(self.looks_like_a_code(word), word)

    def test_a_partial_code_is_not(self):
        self.assertFalse(self.looks_like_a_code("P111"))

    def test_a_screen_name_is_not(self):
        for name in ("Work Calendar", "production plan", "Master Prod. Plan"):
            self.assertFalse(self.looks_like_a_code(name), name)


class GridRebinding(unittest.TestCase):
    """HISTORY.md Phase 82.18, live-caught on R5216UM00 (Mounter Drop
    Analysis): its result grid `grdDetail` is bound to `dsMntDetailListTemp`
    - a 1-column placeholder that never holds a row - until a query answers,
    then re-bound by the screen's own code to `dsMntDetailList`. Discovery
    ran first, so the run polled a dataset that could never fill and
    reported "the query returned no rows" for a screen showing 259. It also
    made a saved profile depend on whether the window happened to be open
    already: cold and warm windows show different dataset names for the same
    screen."""

    def setUp(self):
        import gmes_profile
        self.p = gmes_profile
        f = [flt(column="fromDate", control="mskFrom")]
        self.cold = {"filters": f, "unbound": [],
                     "grids": [grid("grdDetail", "dsMntDetailListTemp", 300000)]}
        self.warm = {"filters": f, "unbound": [],
                     "grids": [grid("grdDetail", "dsMntDetailList", 300000)]}
        self.aliases = {"dsMntDetailList": "dsMntDetailListTemp"}

    # -- the fingerprint ----------------------------------------------------

    def test_without_an_alias_a_cold_and_a_warm_window_look_like_different_screens(self):
        # This is the failure being fixed, kept as a fact about the
        # un-aliased digest so the next two tests mean something.
        self.assertNotEqual(self.p.fingerprint(self.cold), self.p.fingerprint(self.warm))

    def test_with_the_alias_both_windows_hash_to_the_same_screen(self):
        self.assertEqual(self.p.fingerprint(self.cold, self.aliases),
                         self.p.fingerprint(self.warm, self.aliases))

    def test_no_alias_leaves_every_existing_profiles_digest_unchanged(self):
        # The 12 profiles already saved carry no aliases; none may be
        # invalidated by this change.
        self.assertEqual(self.p.fingerprint(self.cold), self.p.fingerprint(self.cold, None))
        self.assertEqual(self.p.fingerprint(self.cold), self.p.fingerprint(self.cold, {}))

    # -- replay -------------------------------------------------------------

    def profile_recorded_cold(self):
        return {"grid": self.p.grid_ref(self.cold["grids"][0]),
                "grid_aliases": self.aliases,
                "fingerprint": self.p.fingerprint(self.cold, self.aliases)}

    def test_a_profile_recorded_cold_replays_in_a_warm_window(self):
        self.assertEqual(self.p.describe_change(self.profile_recorded_cold(), self.warm), [])

    def test_a_profile_recorded_cold_replays_in_a_cold_window(self):
        self.assertEqual(self.p.describe_change(self.profile_recorded_cold(), self.cold), [])

    def test_a_stored_grid_name_that_is_the_rebound_one_still_matches_a_cold_window(self):
        # A profile first written from a warm window holds the re-bound name
        # as its grid. Once the alias is known the cold window's placeholder
        # name must count as the same grid, in this direction too.
        profile = self.profile_recorded_cold()
        profile["grid"] = dict(profile["grid"], dataset="dsMntDetailList")
        self.assertEqual(self.p.describe_change(profile, self.cold), [])

    def test_a_different_grid_is_still_reported_as_gone(self):
        # The alias widens what counts as the SAME grid; it must not make
        # an unrelated one acceptable.
        other = dict(self.cold, grids=[grid("grdDetail", "dsSomethingElse", 300000)])
        problems = self.p.describe_change(self.profile_recorded_cold(), other)
        self.assertTrue(any("is gone" in x for x in problems))

    def test_the_remembered_grid_is_found_under_whichever_name_is_showing(self):
        profile = self.profile_recorded_cold()
        self.assertEqual(self.p.resolve_grid_dataset(profile, self.cold), "dsMntDetailListTemp")
        self.assertEqual(self.p.resolve_grid_dataset(profile, self.warm), "dsMntDetailList")

    def test_with_no_alias_the_remembered_name_is_returned_even_when_absent(self):
        # Preserves today's behaviour: the caller's own "which grid?" error
        # is what says the grid is gone.
        profile = {"grid": self.p.grid_ref(self.cold["grids"][0])}
        self.assertEqual(self.p.resolve_grid_dataset(profile, self.warm), "dsMntDetailListTemp")

    def test_a_profile_with_no_grid_resolves_to_none(self):
        self.assertIsNone(self.p.resolve_grid_dataset({}, self.cold))
        self.assertIsNone(self.p.resolve_grid_dataset(None, self.cold))

    # -- saving -------------------------------------------------------------

    def _save(self, tmp, info, grid_entry, grid_aliases=None):
        with patch.object(self.p, "SCREENS_DIR", tmp):
            self.p.save("R5216UM00", "t", "FFM0826", info, grid=grid_entry, rows=5,
                        grid_aliases=grid_aliases)
            return self.p.load("R5216UM00")

    def test_a_save_remembers_the_alias_and_the_name_seen_when_first_read(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            saved = self._save(tmp, self.cold, self.cold["grids"][0], self.aliases)
        self.assertEqual(saved["grid_aliases"], self.aliases)
        self.assertEqual(saved["grid"]["dataset"], "dsMntDetailListTemp")

    def test_a_warm_run_saves_the_grid_under_its_original_name(self):
        # A replay in an already-warm window sees only the re-bound name and
        # is saved with it as its grid - it must not overwrite the profile's
        # cold-window name and re-break the next cold replay.
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            self._save(tmp, self.cold, self.cold["grids"][0], self.aliases)
            saved = self._save(tmp, self.warm, self.warm["grids"][0], None)
        self.assertEqual(saved["grid"]["dataset"], "dsMntDetailListTemp")

    def test_an_alias_survives_a_later_run_that_saw_only_one_name(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            self._save(tmp, self.cold, self.cold["grids"][0], self.aliases)
            saved = self._save(tmp, self.warm, self.warm["grids"][0], None)
        self.assertEqual(saved["grid_aliases"], self.aliases)

    def test_a_screen_that_never_rebinds_writes_no_alias_key_at_all(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            saved = self._save(tmp, self.cold, self.cold["grids"][0], None)
        self.assertNotIn("grid_aliases", saved)

    # -- the run ------------------------------------------------------------

    def test_follow_grid_rebind_finds_the_same_component_under_its_new_dataset(self):
        screen = core.Screen(None, "R5216UM00", {"title": "t"}, self.cold)
        with patch.object(core, "discover", return_value=self.warm):
            found = screen.follow_grid_rebind(self.cold["grids"][0])
        self.assertEqual(found["dataset"], "dsMntDetailList")

    def test_follow_grid_rebind_reports_nothing_when_the_binding_is_unchanged(self):
        screen = core.Screen(None, "R5216UM00", {"title": "t"}, self.cold)
        with patch.object(core, "discover", return_value=self.cold):
            self.assertIsNone(screen.follow_grid_rebind(self.cold["grids"][0]))

    def test_follow_grid_rebind_never_borrows_a_different_grid(self):
        # Same dataset appearing under a DIFFERENT component is not a
        # re-bind of this one.
        elsewhere = dict(self.cold, grids=[grid("grdOther", "dsMntDetailList", 300000)])
        screen = core.Screen(None, "R5216UM00", {"title": "t"}, self.cold)
        with patch.object(core, "discover", return_value=elsewhere):
            self.assertIsNone(screen.follow_grid_rebind(self.cold["grids"][0]))

    def test_follow_grid_rebind_never_borrows_a_same_named_grid_on_another_form(self):
        # Component names are unique only WITHIN a form (P3151WM00 has
        # grd00/grd01/grd02 repeated across sibling panels), so the name
        # alone is not an identity - the form path is half of it.
        elsewhere = dict(self.cold, grids=[grid(
            "grdDetail", "dsMntDetailList", 300000,
            path="application.mainframe.winTest_0_1.form.divOtherPanel.form")])
        screen = core.Screen(None, "R5216UM00", {"title": "t"}, self.cold)
        with patch.object(core, "discover", return_value=elsewhere):
            self.assertIsNone(screen.follow_grid_rebind(self.cold["grids"][0]))

    def test_the_binding_is_reconciled_after_inquiry_and_before_giving_up(self):
        # Phase 82.20 widened this from "only on a zero": a placeholder that
        # holds even one header row would have passed a zero-only test.
        import inspect
        src = inspect.getsource(core.run_screen)
        inquiry_at = src.index("rows = screen.inquiry(grid)")
        reconcile_at = src.index("reconcile_result(screen, grid, rows, log=log)")
        empty_at = src.index("explain_empty_result(screen, grid)")
        self.assertLess(inquiry_at, reconcile_at)
        self.assertLess(reconcile_at, empty_at)
        self.assertNotIn("if rows == 0:\n        # Zero from the dataset discovery chose", src)

    def test_the_profile_is_saved_with_the_grid_discovery_saw_not_the_rebound_one(self):
        import inspect
        src = inspect.getsource(core.run_screen)
        self.assertIn("grid=discovered_grid,", src)
        self.assertIn("grid_aliases=grid_aliases,", src)


class ResultIntegrity(unittest.TestCase):
    """HISTORY.md Phase 82.20. R5216UM00 showed how the tool can be confidently
    WRONG without any error: it reported "no rows" beside a screenshot of 259.
    The research behind this (Nexacro's own developer guide, plus live
    probing) found the platform-level tricks a screen can play on a tool that
    reads Datasets: a grid's `binddataset` may be set at runtime; a Dataset
    may carry a client-side `filter()` that changes what `getRowCount()`
    reports; and a form walk that stops early hands back a partial screen."""

    def setUp(self):
        self.g = grid("grdDetail", "dsPlaceholder", 300000)
        self.real = grid("grdDetail", "dsReal", 300000)

    class Stub:
        def __init__(self, rebound=None, totals=None):
            self.warnings, self.ws, self.code = [], None, "X1000UM00"
            self._rebound, self._totals = rebound, totals or {}

        def follow_grid_rebind(self, _grid):
            return self._rebound

        def rows(self, g, limit=-1):
            return {"found": True, "total": self._totals.get(g["dataset"], 0)}

    # -- reconcile_result ---------------------------------------------------

    def test_an_unchanged_binding_is_left_alone(self):
        got = core.reconcile_result(self.Stub(), self.g, 12, log=lambda _m: None)
        self.assertEqual(got, (self.g, 12, {}))

    def test_a_rebound_grid_with_rows_is_followed(self):
        stub = self.Stub(rebound=self.real, totals={"dsReal": 259})
        got = core.reconcile_result(stub, self.g, 0, log=lambda _m: None)
        self.assertEqual(got, (self.real, 259, {"dsReal": "dsPlaceholder"}))

    def test_a_placeholder_holding_a_row_is_followed_too_not_only_an_empty_one(self):
        # The reason the check is no longer "only on a zero": a placeholder
        # with a single header row is NOT zero and would have been exported
        # as the report.
        stub = self.Stub(rebound=self.real, totals={"dsReal": 259})
        got = core.reconcile_result(stub, self.g, 1, log=lambda _m: None)
        self.assertEqual(got[0], self.real)
        self.assertEqual(got[1], 259)

    def test_an_empty_rebound_dataset_is_not_followed_but_the_disagreement_is_reported(self):
        stub = self.Stub(rebound=self.real, totals={"dsReal": 0})
        got = core.reconcile_result(stub, self.g, 12, log=lambda _m: None)
        self.assertEqual(got, (self.g, 12, {}))
        self.assertEqual(len(stub.warnings), 1)
        self.assertIn("may disagree", stub.warnings[0])

    def test_an_empty_rebound_dataset_beside_an_empty_one_is_just_empty(self):
        stub = self.Stub(rebound=self.real, totals={})
        got = core.reconcile_result(stub, self.g, 0, log=lambda _m: None)
        self.assertEqual(got, (self.g, 0, {}))
        self.assertEqual(stub.warnings, [])

    # -- explain_empty_result -----------------------------------------------

    def _info(self, *grids):
        return {"grids": list(grids)}

    def test_an_empty_result_names_the_other_grid_that_has_rows(self):
        other = grid("grdSecond", "dsSecond", 200000)
        stub = self.Stub(totals={"dsSecond": 67})
        with patch.object(core, "discover", return_value=self._info(self.g, other)):
            msg = core.explain_empty_result(stub, self.g)
        self.assertIn("grdSecond", msg)
        self.assertIn("67 rows", msg)
        self.assertIn("--grid", msg)
        self.assertIn("nothing was guessed", msg)

    def test_an_empty_result_with_no_other_rows_is_the_plain_message(self):
        stub = self.Stub(totals={})
        with patch.object(core, "discover", return_value=self._info(self.g)):
            msg = core.explain_empty_result(stub, self.g)
        self.assertEqual(msg, "the query returned no rows - nothing exported")

    def test_explaining_never_masks_the_original_error_if_it_cannot_look(self):
        with patch.object(core, "discover", side_effect=RuntimeError("boom")):
            msg = core.explain_empty_result(self.Stub(), self.g)
        self.assertEqual(msg, "the query returned no rows - nothing exported")

    def test_explaining_never_switches_the_run_to_another_grid(self):
        # Which grid is the report is the person's call on a master/detail
        # screen; the message offers it, the code never takes it.
        import inspect
        self.assertNotIn("grid = ", inspect.getsource(core.explain_empty_result))

    # -- filtered_result_note -----------------------------------------------

    def test_a_client_side_filter_that_hides_rows_is_reported_with_both_numbers(self):
        note = core.filtered_result_note(
            {"total": 20, "unfiltered": 37, "filterstr": "gubun!='RB'"})
        self.assertIn("20 of 37", note)
        self.assertIn("gubun!='RB'", note)

    def test_a_filter_that_hides_nothing_is_not_reported(self):
        self.assertIsNone(core.filtered_result_note(
            {"total": 6, "unfiltered": 6, "filterstr": "x==1"}))

    def test_no_filter_or_an_unreadable_count_is_not_reported(self):
        self.assertIsNone(core.filtered_result_note({"total": 9, "filterstr": ""}))
        self.assertIsNone(core.filtered_result_note({"total": 9, "filterstr": "x==1"}))
        self.assertIsNone(core.filtered_result_note(None))

    # -- unchanged_result_note / grid_is_proven (HISTORY.md Phase 82.21) ------

    def test_a_result_that_never_moved_is_flagged(self):
        # R3220UM00: 37 rows before Inquiry, 37 after, never changed - the
        # screen's static "Formula" legend, exported as the report.
        note = core.unchanged_result_note({"before": 37, "changed": False}, 37)
        self.assertIn("37 rows before Inquiry", note)
        self.assertIn("static content", note)

    def test_a_result_that_moved_is_not_flagged(self):
        self.assertIsNone(core.unchanged_result_note({"before": 0, "changed": True}, 259))

    def test_a_result_that_cleared_and_refilled_to_the_same_count_is_not_flagged(self):
        # Nexacro clears the dataset on Inquiry; seeing that happen IS the
        # evidence the query ran, even if the answer came back identical.
        self.assertIsNone(core.unchanged_result_note({"before": 5, "changed": True}, 5))

    def test_a_different_count_after_is_not_flagged(self):
        self.assertIsNone(core.unchanged_result_note({"before": 10, "changed": False}, 12))

    def test_an_empty_or_unknown_result_is_not_flagged(self):
        self.assertIsNone(core.unchanged_result_note({"before": 0, "changed": False}, 0))
        self.assertIsNone(core.unchanged_result_note({}, 5))
        self.assertIsNone(core.unchanged_result_note(None, 5))

    def test_a_grid_the_profile_already_vetted_is_proven(self):
        profile = {"grid": {"dataset": "dsGrpSummary"}}
        self.assertTrue(core.grid_is_proven(profile, grid("g", "dsGrpSummary", 1)))

    def test_a_different_grid_than_the_profile_names_is_not_proven(self):
        # The exact R3220 mistake: the profile (or default) said one dataset,
        # a --grid override chose another.
        profile = {"grid": {"dataset": "dsGrpSummary"}}
        self.assertFalse(core.grid_is_proven(profile, grid("g", "dsOperAnalCalc", 1)))

    def test_no_profile_means_nothing_is_proven(self):
        # First recording, and --relearn (which loads no profile).
        self.assertFalse(core.grid_is_proven(None, grid("g", "dsX", 1)))
        self.assertFalse(core.grid_is_proven({}, grid("g", "dsX", 1)))

    def test_a_rebound_grid_is_proven_under_either_of_its_names(self):
        profile = {"grid": {"dataset": "dsPlaceholder"},
                   "grid_aliases": {"dsReal": "dsPlaceholder"}}
        self.assertTrue(core.grid_is_proven(profile, grid("g", "dsReal", 1)))
        self.assertTrue(core.grid_is_proven(profile, grid("g", "dsPlaceholder", 1)))

    def test_a_profile_that_stored_the_rebound_name_is_proven_against_the_placeholder(self):
        # The other direction: remembered under the re-bound name, met under
        # the placeholder name in a cold window.
        profile = {"grid": {"dataset": "dsReal"},
                   "grid_aliases": {"dsReal": "dsPlaceholder"}}
        self.assertTrue(core.grid_is_proven(profile, grid("g", "dsPlaceholder", 1)))

    def test_run_screen_only_asks_about_an_unproven_grid(self):
        import inspect
        src = inspect.getsource(core.run_screen)
        proven_at = src.index("if not grid_is_proven(profile, discovered_grid):")
        note_at = src.index("unchanged_result_note(getattr(screen")
        self.assertLess(proven_at, note_at)

    def test_a_suspect_result_is_exported_but_never_remembered(self):
        # R3220UM00: the static-legend run "learned: saved to R3220UM00.json",
        # making the wrong grid the default for every later replay.
        import inspect
        src = inspect.getsource(core.run_screen)
        self.assertIn("result_suspect = True", src)
        skip_at = src.index("if use_profile and result_suspect:")
        save_at = src.index("elif use_profile:")
        self.assertLess(skip_at, save_at)
        self.assertIn("not remembered for next time", src)

    def test_the_poll_reports_what_it_saw_and_the_screen_keeps_it(self):
        import inspect
        self.assertIn("report.update(before=before, changed=tracker.changed)",
                      inspect.getsource(core.poll_inquiry))
        self.assertIn("report=self.last_inquiry", inspect.getsource(core.Screen.inquiry))

    # -- unverified_date_sets -----------------------------------------------

    def test_a_date_typed_with_set_and_no_verify_is_called_out(self):
        applied = [(flt(column="startDay", control="mskFrom", label="Period"), "20260919")]
        self.assertEqual(core.unverified_date_sets(applied, None), ["Period"])

    def test_from_and_to_sharing_one_label_are_called_out_once(self):
        applied = [(flt(column="startDay", control="mskFrom", label="Period"), "20260919"),
                   (flt(column="endDay", control="mskTo", label="Period"), "20260919")]
        self.assertEqual(core.unverified_date_sets(applied, None), ["Period"])

    def test_a_verify_silences_it(self):
        applied = [(flt(column="startDay", control="mskFrom", label="Period"), "20260919")]
        self.assertEqual(core.unverified_date_sets(applied, "planYmd"), [])

    def test_a_non_date_set_is_never_called_a_date(self):
        applied = [(flt(column="orderNo", control="edtOrder", label="Order"), "123")]
        self.assertEqual(core.unverified_date_sets(applied, None), [])

    # -- discovery that was cut short ---------------------------------------

    def test_a_truncated_form_walk_is_refused(self):
        self.assertIn("cut short", core.discovery_cut({"truncated": True}))

    def test_a_result_list_that_lost_entries_is_refused(self):
        msg = core.discovery_cut({"grids": [1, 2], "totals": {"grids": 30}})
        self.assertIn("2 of this screen's 30 result grids", msg)

    def test_a_complete_reading_passes(self):
        self.assertIsNone(core.discovery_cut(
            {"grids": [1, 2], "unbound": [], "totals": {"grids": 2, "unbound": 0}}))
        self.assertIsNone(core.discovery_cut({}))
        self.assertIsNone(core.discovery_cut(None))

    def test_discover_raises_rather_than_return_a_cut_reading(self):
        with patch.object(core, "evaluate", return_value={"found": True, "truncated": True}):
            with self.assertRaisesRegex(RuntimeError, "cut short"):
                core.discover(None, "X1000UM00")

    # -- the JavaScript itself (source level; no mock G-MES, CLAUDE.md 4.3) ---

    def test_the_form_walk_records_that_it_was_cut_and_the_old_cap_is_gone(self):
        self.assertIn("_findForms.truncated = true", gmes_data.JS_HELPERS)
        self.assertIsNone(re.search(r"hits\.length > 400\b", gmes_data.JS_HELPERS))

    def test_discovery_reports_the_walk_state_and_the_true_list_lengths(self):
        self.assertIn("truncated: !!_findForms.truncated", core.JS_DISCOVER)
        self.assertIn("totals: {grids: grids.length, unbound: trulyUnbound.length}",
                      core.JS_DISCOVER)
        self.assertNotIn("grids.slice(0, 8)", core.JS_DISCOVER)
        self.assertNotIn("unbound: trulyUnbound.slice(0, 40)", core.JS_DISCOVER)
        # "Not the old literal" is not enough - the new caps must actually be
        # far above what a real screen has (the largest seen: 8 grids).
        caps = {k: int(v) for k, v in
                re.findall(r"(GRID_CAP|UNBOUND_CAP) = (\d+)", core.JS_DISCOVER)}
        self.assertGreaterEqual(caps["GRID_CAP"], 24)
        self.assertGreaterEqual(caps["UNBOUND_CAP"], 200)

    def test_a_dataset_read_reports_an_active_client_side_filter(self):
        js = gmes_data.js_read("X1000UM00", "dsX", -1, 0)
        self.assertIn("getRowCountNF", js)
        self.assertIn("filterstr: filterstr, unfiltered: unfiltered", js)

    def test_run_screen_wires_all_of_it_in(self):
        import inspect
        src = inspect.getsource(core.run_screen)
        for needle in ("reconcile_result(", "explain_empty_result(",
                       "filtered_result_note(", "unverified_date_sets("):
            with self.subTest(needle=needle):
                self.assertIn(needle, src)


class Profiles(unittest.TestCase):
    """What the tool remembers after a run that worked, and - more
    importantly - when it must refuse to trust that memory."""

    def setUp(self):
        import gmes_profile
        self.p = gmes_profile
        self.info = {
            "filters": [flt(column="paramFromDate", control="mskDateFrom"),
                        flt(column="paramEndDate", control="mskDateTo"),
                        flt(column="paramPo", control="edtPo")],
            "unbound": [],
            "grids": [grid("grdMain", "dsMasterProdPlan", 400000)],
        }

    def profile_for(self, info):
        return {"from": self.p.field_ref(info["filters"][0]),
                "to": self.p.field_ref(info["filters"][1]),
                "grid": self.p.grid_ref(info["grids"][0]),
                "fingerprint": self.p.fingerprint(info)}

    def test_an_unchanged_screen_has_no_problems(self):
        self.assertEqual(self.p.describe_change(self.profile_for(self.info),
                                                self.info), [])

    def test_the_same_screen_fingerprints_the_same_twice(self):
        self.assertEqual(self.p.fingerprint(self.info), self.p.fingerprint(self.info))

    def test_a_renamed_date_field_is_caught(self):
        profile = self.profile_for(self.info)
        changed = dict(self.info)
        changed["filters"] = [flt(column="paramFromDate", control="mskDateFrom"),
                              flt(column="paramToDate", control="mskDateTo"),
                              flt(column="paramPo", control="edtPo")]
        problems = self.p.describe_change(profile, changed)
        self.assertTrue(problems)
        self.assertIn("paramEndDate", problems[0])

    def test_a_missing_grid_is_caught(self):
        profile = self.profile_for(self.info)
        changed = dict(self.info, grids=[grid("grdOther", "dsSomethingElse", 10)])
        self.assertTrue(self.p.describe_change(profile, changed))

    def test_a_control_appearing_or_vanishing_is_not_a_change(self):
        # Unbound inputs are collected only when visible, and visibility moves
        # with scrolling and late-rendering panels. Including them made a
        # screen "change" between two runs seconds apart, so the profile threw
        # itself away every time.
        profile = self.profile_for(self.info)
        scrolled = dict(self.info,
                        unbound=[flt(control="edtLotNo", bound=False)])
        self.assertEqual(self.p.describe_change(profile, scrolled), [])

    def test_the_same_column_on_a_different_control_is_not_a_change(self):
        # One column can be bound to two controls; which one is recorded
        # depends on which was visible.
        profile = self.profile_for(self.info)
        rebound = dict(self.info)
        rebound["filters"] = [flt(column="paramFromDate", control="mskDateFrom2"),
                              flt(column="paramEndDate", control="mskDateTo"),
                              flt(column="paramPo", control="edtPo")]
        self.assertEqual(self.p.describe_change(profile, rebound), [])

    def test_a_second_grid_on_the_same_dataset_is_not_a_change(self):
        # The Excel export leaves a clone (grdPrnMpp__EXCEL__) bound to the
        # same dataset, so the screen "changed" after every export.
        profile = self.profile_for(self.info)
        after_export = dict(self.info,
                            grids=self.info["grids"] +
                            [grid("grdMain__EXCEL__", "dsMasterProdPlan", 0)])
        self.assertEqual(self.p.describe_change(profile, after_export), [])

    def test_a_new_filter_elsewhere_is_reported_not_ignored(self):
        # Nothing the profile uses moved, but it is not the same screen -
        # worth saying so rather than replaying in silence.
        profile = self.profile_for(self.info)
        changed = dict(self.info)
        changed["filters"] = self.info["filters"] + [flt(column="paramNew",
                                                         control="edtNew")]
        self.assertTrue(self.p.describe_change(profile, changed))

    def test_a_saved_reference_never_holds_a_value_or_an_id(self):
        # A form tree contains live session tokens; only named fields are
        # ever copied out.
        ref = self.p.field_ref(flt(column="paramPo", value="011074232146"))
        self.assertNotIn("value", ref)
        self.assertNotIn("id", ref)
        self.assertEqual(set(ref), {"dataset", "column", "control", "form",
                                    "label", "stable_path"})

    def test_a_field_seen_holding_a_month_shaped_value_remembers_that_width(self):
        # Open Item 41: an empty field has no width of its own to prove -
        # capturing it the one time it is seen non-empty is what lets a LATER
        # run, finding it blank again, still know it is a YYYYMM field.
        ref = self.p.field_ref(flt(column="stdYm", value="202609"))
        self.assertEqual(ref["width"], 6)

    def test_a_field_seen_holding_a_year_shaped_value_remembers_that_width(self):
        ref = self.p.field_ref(flt(column="paramYear", value="2026"))
        self.assertEqual(ref["width"], 4)

    def test_a_field_seen_empty_remembers_no_width(self):
        ref = self.p.field_ref(flt(column="paramFromDate", value=""))
        self.assertNotIn("width", ref)

    def test_an_eight_digit_field_remembers_no_width(self):
        # Already the tool's own default - nothing to remember beyond it.
        ref = self.p.field_ref(flt(column="paramFromDate", value="20260920"))
        self.assertNotIn("width", ref)


class ShippableProfiles(unittest.TestCase):
    """What the tool knows about a screen can be given away. What a user did
    with it cannot.

    The expensive half of a profile - which control is "from", which grid
    holds the result, where the division tree lives - costs a RECORD run per
    screen and is true for anyone with access to that screen. The other half
    is production data (CLAUDE.md 2.4). These tests pin the line between
    them, because the cost of getting it wrong is business data in a public
    repository."""

    FULL = {
        "screen": "P1112UM00",
        "title": "Production Plan by Order(Line)",
        "menuId": "PPM0219",
        "learned": "2026-09-15 10:00:00",
        "fingerprint": "abc123",
        "opening_fingerprint": "def456",
        "from": {"dataset": "dsFilterDVO", "column": "paramFromDate",
                 "control": "mskDateFrom", "form": "P1112WF00", "label": "From"},
        "to": {"dataset": "dsFilterDVO", "column": "paramEndDate",
               "control": "mskDateTo", "form": "P1112WF00", "label": "To"},
        "division": {"form": "OrgCategory_GDS",
                     "dataset": "dsCatCommonTreeNodeDVO", "entry": "VD"},
        "grid": {"name": "grdMain", "dataset": "dsMasterProdPlan",
                 "form": "P1112WM00"},
        "options": ["Create Date"],
        "values": {"division": "VD", "from": "20260909", "to": "20260909",
                   "verify": "creYmd",
                   "sets": {"Production Order": "011074232146"}},
        "proved": {"rows": 790,
                   "command": "--division VD --from 20260909 --to 20260909"},
    }

    def setUp(self):
        import gmes_profile
        self.p = gmes_profile

    def test_the_structural_half_is_kept(self):
        out = self.p.shippable(self.FULL)
        self.assertEqual(out["screen"], "P1112UM00")
        self.assertEqual(out["from"]["column"], "paramFromDate")
        self.assertEqual(out["grid"]["dataset"], "dsMasterProdPlan")
        self.assertEqual(out["fingerprint"], "abc123")

    def test_options_are_a_report_preset_not_screen_structure_so_they_never_ship(self):
        # HISTORY.md Phase 79.6: "Create Date" vs "Plan Date" is exactly the
        # kind of decision this file's own docstring says nothing on the
        # screen records - the same reasoning that already keeps a ticked
        # DIVISION out of the shipped half (see the test above this one).
        # Shipping one person's option choice let a new user silently
        # inherit somebody else's answer to a question they were never
        # asked. Local replay for a machine that HAS run the screen is
        # untouched - see LocalOptionsSurviveShipping below.
        out = self.p.shippable(self.FULL)
        self.assertNotIn("options", out)

    def test_no_value_or_command_can_reach_the_shipped_half(self):
        out = self.p.shippable(self.FULL)
        self.assertNotIn("values", out)
        self.assertNotIn("proved", out)
        # Checked as text too, so a value nested anywhere still fails this.
        blob = json.dumps(out)
        for secret in ("011074232146", "20260909", "790",
                       "--division", "VD"):
            with self.subTest(secret=secret):
                self.assertNotIn(secret, blob,
                                 f"{secret!r} reached the shipped profile")

    def test_the_division_tree_location_ships_but_the_division_does_not(self):
        out = self.p.shippable(self.FULL)
        self.assertEqual(out["division"]["dataset"], "dsCatCommonTreeNodeDVO")
        self.assertNotIn("entry", out["division"],
                         "the ticked division is the user's own context")

    def test_it_is_an_allowlist_so_a_new_field_cannot_leak(self):
        # A field added to save() in future must be considered before it can
        # ship, rather than leaking because nobody remembered to exclude it.
        with_extra = dict(self.FULL, someFutureField="a plant code, perhaps")
        self.assertNotIn("someFutureField", self.p.shippable(with_extra))

    def test_shippable_of_nothing_is_nothing(self):
        self.assertIsNone(self.p.shippable(None))


class ShippedAndLocalMerge(unittest.TestCase):
    """A new user has only the shipped half and the screen still works; once
    they prove it themselves, their own file wins."""

    def setUp(self):
        import tempfile, shutil, gmes_profile
        self.p = gmes_profile
        self.tmp = tempfile.mkdtemp(prefix="gmes-profile-merge-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.shipped = os.path.join(self.tmp, "shipped")
        self.local = os.path.join(self.tmp, "local")
        os.makedirs(self.shipped)
        os.makedirs(self.local)
        self._patch = patch.multiple(gmes_profile,
                                     SHIPPED_DIR=self.shipped,
                                     SCREENS_DIR=self.local)
        self._patch.start()
        self.addCleanup(self._patch.stop)

    def _write(self, directory, code, payload):
        with open(os.path.join(directory, f"{code}.json"), "w", encoding="utf-8") as fh:
            json.dump(payload, fh)

    def test_a_new_user_gets_the_shipped_screen(self):
        self._write(self.shipped, "P1112UM00",
                    {"screen": "P1112UM00", "grid": {"dataset": "dsMasterProdPlan"}})
        loaded = self.p.load("P1112UM00")
        self.assertEqual(loaded["grid"]["dataset"], "dsMasterProdPlan")
        self.assertNotIn("values", loaded)

    def test_a_locally_proved_profile_wins(self):
        self._write(self.shipped, "P1112UM00",
                    {"screen": "P1112UM00", "fingerprint": "shipped"})
        self._write(self.local, "P1112UM00",
                    {"screen": "P1112UM00", "fingerprint": "mine",
                     "values": {"division": "VD"}})
        loaded = self.p.load("P1112UM00")
        self.assertEqual(loaded["fingerprint"], "mine")
        self.assertEqual(loaded["values"]["division"], "VD")

    def test_neither_present_is_still_none(self):
        self.assertIsNone(self.p.load("P9999UM00"))

    def test_known_lists_shipped_and_local_together_without_duplicates(self):
        self._write(self.shipped, "P1112UM00", {"screen": "P1112UM00"})
        self._write(self.shipped, "P1111UM00", {"screen": "P1111UM00"})
        self._write(self.local, "P1112UM00",
                    {"screen": "P1112UM00", "learned": "2026-09-15 10:00:00"})
        codes = [p["screen"] for p in self.p.known()]
        self.assertEqual(sorted(codes), ["P1111UM00", "P1112UM00"])
        # The one proved here sorts first - it has a timestamp, shipped ones do not.
        self.assertEqual(codes[0], "P1112UM00")

    def test_export_writes_only_the_structural_half(self):
        self._write(self.local, "P1112UM00", ShippableProfiles.FULL)
        written = self.p.export_shippable("P1112UM00", dest_dir=self.shipped)
        self.assertTrue(os.path.isfile(written))
        with open(written, encoding="utf-8") as fh:
            blob = fh.read()
        self.assertNotIn("011074232146", blob)
        self.assertNotIn("proved", blob)
        self.assertIn("dsMasterProdPlan", blob)

    def test_exporting_a_screen_with_nothing_local_is_not_an_error(self):
        self.assertIsNone(self.p.export_shippable("P1111UM00", dest_dir=self.shipped))

    def test_a_brand_new_machine_inherits_no_options_preset(self):
        # HISTORY.md Phase 79.6, the exact scenario: the shipped half
        # (everyone's) never carries an option choice, so a new user makes
        # this decision themselves the first time - the same as division
        # and dates - rather than silently inheriting someone else's answer.
        self._write(self.shipped, "P1112UM00",
                    {"screen": "P1112UM00", "grid": {"dataset": "dsMasterProdPlan"}})
        loaded = self.p.load("P1112UM00")
        self.assertNotIn("options", loaded)

    def test_a_machine_that_has_proved_the_screen_keeps_its_own_option_choice(self):
        # The LOCAL half is untouched by this phase - a machine that has run
        # the screen successfully once still replays exactly what it proved,
        # with no re-recording needed.
        self._write(self.shipped, "P1112UM00",
                    {"screen": "P1112UM00", "grid": {"dataset": "dsMasterProdPlan"}})
        self._write(self.local, "P1112UM00",
                    {"screen": "P1112UM00",
                     "options": [{"key": "create", "name": "btnCreate",
                                 "path": "", "label": "Create Date"}]})
        loaded = self.p.load("P1112UM00")
        self.assertEqual(loaded["options"][0]["key"], "create")

    def test_exporting_a_proved_screen_no_longer_writes_its_option_choice(self):
        self._write(self.local, "P1112UM00", ShippableProfiles.FULL)
        written = self.p.export_shippable("P1112UM00", dest_dir=self.shipped)
        with open(written, encoding="utf-8") as fh:
            raw = fh.read()
        self.assertNotIn("options", json.loads(raw))
        self.assertNotIn("Create Date", raw)


class RememberedValues(unittest.TestCase):
    """A remembered value must survive a run that did not supply one.

    P1111UM00 was recorded with VD and a date range, then run once with
    everything blank, and its memory came back empty. "Never forget" has to
    mean that an empty answer does not erase a remembered one."""

    def setUp(self):
        import gmes_profile
        self.p = gmes_profile
        self.previous = {"values": {"division": "VD", "from": "20260909",
                                    "to": "20260909", "sets": {}}}

    def test_a_blank_run_does_not_erase_anything(self):
        merged = self.p._merge_values(
            self.previous, {"division": "", "from": "", "to": "", "sets": {}})
        self.assertEqual(merged["division"], "VD")
        self.assertEqual(merged["from"], "20260909")

    def test_a_supplied_value_replaces_the_old_one(self):
        merged = self.p._merge_values(
            self.previous, {"division": "MOBILE", "from": "", "to": "", "sets": {}})
        self.assertEqual(merged["division"], "MOBILE")
        self.assertEqual(merged["to"], "20260909")     # untouched

    def test_first_ever_save_keeps_what_it_was_given(self):
        merged = self.p._merge_values(None, {"division": "VD", "from": "20260901",
                                             "to": "20260902", "sets": {}})
        self.assertEqual(merged, {"division": "VD", "from": "20260901",
                                  "to": "20260902", "sets": {}})

    def test_a_dated_sets_run_clears_the_stale_bound_from_to_it_replaces(self):
        # Live 2026-09-22, P3111UM00 / P3151WM00: an earlier recording proved a
        # bound from/to (20260916); a later one gave its dates through `sets`
        # instead (this screen turned out to have no bound date field at all) -
        # `sets` correctly moved on to 20260920, but the blanket "keep the old
        # answer" rule for from/to kept 20260916 forever, so the two mechanisms
        # described two different days in the same profile. The Final Intent
        # Verification then compared the screen against whichever stale one it
        # read, and refused a perfectly correct replay.
        previous = {"values": {"division": "VD", "from": "20260916", "to": "20260916",
                               "sets": {}}}
        merged = self.p._merge_values(
            previous, {"division": "VD", "from": "", "to": "",
                      "sets": {"mskFromDate": "20260920", "mskToDate": "20260920"}})
        self.assertEqual(merged["from"], "")
        self.assertEqual(merged["to"], "")
        self.assertEqual(merged["sets"], {"mskFromDate": "20260920", "mskToDate": "20260920"})

    def test_a_non_date_sets_run_still_keeps_the_remembered_from_to(self):
        previous = {"values": {"division": "VD", "from": "20260916", "to": "20260916",
                               "sets": {}}}
        merged = self.p._merge_values(
            previous, {"division": "VD", "from": "", "to": "", "sets": {"lotNo": "ABC"}})
        self.assertEqual(merged["from"], "20260916")
        self.assertEqual(merged["to"], "20260916")

    def test_values_are_recovered_from_an_older_profile(self):
        # Profiles written before the values block still carry the command
        # that proved them; re-teaching would be absurd.
        old = {"proved": {"rows": 800,
                          "command": "--division VD --from 20260909 --to 20260909"}}
        self.assertEqual(self.p.last_values(old)["division"], "VD")
        self.assertEqual(self.p.last_values(old)["to"], "20260909")

    def test_none_in_a_recovered_command_is_not_a_value(self):
        old = {"proved": {"command": "--division None --from None --to None"}}
        self.assertEqual(self.p.last_values(old), {})


class ProfileReplayLifecycle(unittest.TestCase):
    """A left-panel option creates a second valid screen shape.

    The pre-d7886cd core saved only the post-option shape, then compared it
    with the next opening shape before reapplying the option and refused a
    valid replay.
    """

    class FakeScreen:
        def __init__(self, opening, after_option):
            self._info = opening
            self.after_option = after_option
            self.title = "Profile lifecycle"
            self.menu_id = "M-1"
            self.win_id = "win_1"
            self.warnings = []
            self.last_tree = None
            self.options_set = []
            self._selected_name = None

        @property
        def info(self):
            return self._info

        @property
        def filters(self):
            return self.info["filters"]

        @property
        def unbound(self):
            return self.info.get("unbound", [])

        def grid(self, _preferred=None):
            return self.info["grids"][0]

        def refresh(self):
            return self.info

        def options(self):
            # Final Intent Verification (step 7.5) re-reads this right before
            # Inquiry; it must agree with whatever set_option() last resolved,
            # exactly as the real Screen's left_options() would.
            if self._selected_name is None:
                return []
            return [{"name": self._selected_name, "label": self._selected_name,
                     "state": "selected"}]

        def set_option(self, wanted):
            self.options_set.append(wanted)
            self._info = self.after_option
            # Phase 76 contract: the resolved STABLE identity comes back, not
            # the text it was asked with, so run_screen can remember the
            # identity instead of a label that depends on the UI language.
            # `wanted` is a plain string on a first RECORD, and the profile's
            # own saved {key, name, path, label} dict on a replay - resolved
            # the same way the real Screen.set_option()/resolve_option() do,
            # not by str()'ing whichever shape happened to arrive.
            name = wanted.get("name") if isinstance(wanted, dict) else str(wanted)
            self._selected_name = name
            return {"outcome": f"{name} -> selected",
                    "matched_by": "component name",
                    "key": core.option_key(name),
                    "name": name, "path": "", "label": name}

        def clear_stale(self, _keep):
            return []

        def inquiry(self, _grid):
            return 1

        # The real Screen answers both after every Inquiry (HISTORY.md
        # Phase 82.20): the grid's live binding, and the result's row count.
        def follow_grid_rebind(self, _grid):
            return None

        def rows(self, _grid, limit=-1):
            return {"found": True, "total": 1, "rows": []}

    def test_option_profile_checks_opening_then_post_option_shape_and_refuses_real_opening_drift(self):
        import gmes_profile

        opening = {"filters": [flt(column="initialDate", control="mskInitial")],
                   "unbound": [], "grids": [grid("grdInitial", "dsInitial", 100)]}
        post_option = {"filters": [flt(column="planDate", control="mskPlan")],
                       "unbound": [], "grids": [grid("grdPlan", "dsPlan", 100)]}
        changed_opening = {"filters": [flt(column="changedDate", control="mskChanged")],
                           "unbound": [], "grids": [grid("grdInitial", "dsInitial", 100)]}
        learned = self.FakeScreen(opening, post_option)
        replay = self.FakeScreen(opening, post_option)
        drifted = self.FakeScreen(changed_opening, post_option)

        store = {}

        def load_profile(code):
            return store.get(code)

        def save_profile(code, _title, _menu_id, info, *, grid=None, options=(),
                         opening_info=None, **_unused):
            store[code] = {
                "fingerprint": gmes_profile.fingerprint(info),
                "opening_fingerprint": gmes_profile.fingerprint(opening_info or info),
                "grid": gmes_profile.grid_ref(grid),
                "options": list(options),
            }
            return "test-profile.json"

        with patch.object(gmes_profile, "load", side_effect=load_profile), \
             patch.object(gmes_profile, "save", side_effect=save_profile) as save, \
             patch.object(core, "open_screen", side_effect=[learned, replay, drifted]), \
             patch.object(core, "org_selection", return_value={"found": False}):
            first = core.run_screen(None, "P1234UM00", options=("Plan Date",),
                                    export="none", log=lambda _message: None)
            self.assertTrue(first["ok"])
            self.assertEqual(store["P1234UM00"]["opening_fingerprint"],
                             gmes_profile.fingerprint(opening))
            self.assertEqual(store["P1234UM00"]["fingerprint"],
                             gmes_profile.fingerprint(post_option))

            second = core.run_screen(None, "P1234UM00", export="none", log=lambda _message: None)
            self.assertTrue(second["ok"])
            # Replay re-applies the STABLE IDENTITY the first run resolved, not
            # the "Plan Date" text it was originally asked with. That is what
            # survives a UI-language change (HISTORY.md Phase 76); the label is
            # carried along only so a person can read their own profile.
            self.assertEqual(len(replay.options_set), 1)
            replayed = replay.options_set[0]
            self.assertIsInstance(replayed, dict)
            self.assertEqual(replayed["name"], "Plan Date")
            self.assertEqual(replayed["key"], core.option_key("Plan Date"))
            self.assertEqual(store["P1234UM00"]["grid"]["dataset"], "dsPlan")

            before_failed_replay = deepcopy(store["P1234UM00"])
            with self.assertRaisesRegex(RuntimeError, "remembered screen shape changed"):
                core.run_screen(None, "P1234UM00", export="none", log=lambda _message: None)
            self.assertEqual(drifted.options_set, [])
            self.assertEqual(store["P1234UM00"], before_failed_replay)
            self.assertEqual(save.call_count, 2)


class AutoReplayFromSavedProfile(unittest.TestCase):
    """HISTORY.md Phase 82.9, live-caught while recording M3912UM00
    together with the project owner: `run_screen()` already re-applied a
    taught screen's OPTIONS automatically when a call named none of its
    own, but division, dates and `--set` filters all still required the
    caller to retype exactly what a previous run had already proven -
    `run_gmes_workflow.py`'s interactive "Run it?" replay has always done
    this via `gmes_profile.last_values()`, but only there. A caller with
    no terminal to answer a prompt - `gmes_report.py run <CODE>` with no
    other flags, and therefore `GMES_Workflow.bat <CODE>` too - got none
    of it."""

    class FakeScreen:
        def __init__(self, info):
            self._info = info
            self.title, self.menu_id, self.win_id = "Assign Range", "M-1", "win_1"
            self.warnings = []
            self.last_tree = None
            self.select_org_calls = []
            self.date_range_calls = []
            self.set_filter_calls = []

        @property
        def info(self):
            return self._info

        @property
        def filters(self):
            return self._info["filters"]

        @property
        def unbound(self):
            return self._info.get("unbound", [])

        def grid(self, _preferred=None):
            return self._info["grids"][0]

        def refresh(self):
            return self._info

        def options(self):
            return []

        def _set_filter_value(self, column, value):
            # Final Intent Verification (step 7.5) re-reads self.info right
            # after these writes via refresh() - it must see the value that
            # was actually just set, exactly as a real Screen would.
            for f in self._info["filters"]:
                if f.get("column") == column:
                    f["value"] = value
                    return

        def select_org(self, names, tree=None, prefer=None):
            self.select_org_calls.append(names)
            return {"ticked": [{"name": names if isinstance(names, str) else names[0]}],
                    "cleared": [], "confirmed": names}

        def set_date_range(self, from_value, to_value, profile=None):
            self.date_range_calls.append((from_value, to_value))
            if not from_value:
                return []
            f = flt(column="fromYmd", control="mskFrom")
            self._set_filter_value("fromYmd", from_value)
            return [(f, from_value)]

        def clear_stale(self, _keep):
            return []

        def set_filter(self, key, value):
            self.set_filter_calls.append((key, value))
            f = flt(column=key, control="edt" + key, label=key)
            self._set_filter_value(key, value)
            return f, value

        def inquiry(self, _grid):
            return 1

        # The real Screen answers both after every Inquiry (HISTORY.md
        # Phase 82.20): the grid's live binding, and the result's row count.
        def follow_grid_rebind(self, _grid):
            return None

        def rows(self, _grid, limit=-1):
            return {"found": True, "total": 1, "rows": []}

        def verify_column(self, _grid, column, expected, strict=True):
            self.verify_calls = getattr(self, "verify_calls", [])
            self.verify_calls.append((column, expected))
            return [expected]

    def make_screen(self):
        return self.FakeScreen({
            "filters": [flt(column="fromYmd", control="mskFrom"),
                       flt(column="lotNo", control="edtLot")],
            "unbound": [], "grids": [grid("grdMain", "dsMain", 100)]})

    def test_a_bare_replay_pulls_division_dates_and_sets_from_the_profile(self):
        import gmes_profile
        screen = self.make_screen()
        fp = gmes_profile.fingerprint(screen.info)
        profile = {"fingerprint": fp, "opening_fingerprint": fp,
                  "grid": {"dataset": "dsMain"}, "options": [],
                  "values": {"division": "SEEG-P", "from": "20260901",
                            "to": "20260901", "sets": {"lotNo": "ABC123"},
                            "verify": "fromYmd"}}
        with patch.object(gmes_profile, "load", return_value=profile), \
             patch.object(gmes_profile, "save", return_value="x.json"), \
             patch.object(core, "open_screen", return_value=screen), \
             patch.object(core, "org_selection", return_value={"found": True, "org": "SEEG-P"}):
            result = core.run_screen(None, "M3912UM00", export="none",
                                     log=lambda _m: None)
        self.assertTrue(result["ok"], result.get("error"))
        self.assertEqual(screen.select_org_calls, ["SEEG-P"])
        self.assertEqual(screen.date_range_calls, [("20260901", "20260901")])
        self.assertEqual(screen.set_filter_calls, [("lotNo", "ABC123")])
        # verify is replayed alongside the dates it was originally paired
        # with, not left to trip run_screen()'s own
        # "a date-constrained run requires --verify" refusal.
        self.assertEqual(screen.verify_calls, [("fromYmd", "20260901")])

    def test_an_explicit_argument_always_wins_over_the_saved_one(self):
        import gmes_profile
        screen = self.make_screen()
        fp = gmes_profile.fingerprint(screen.info)
        profile = {"fingerprint": fp, "opening_fingerprint": fp,
                  "grid": {"dataset": "dsMain"}, "options": [],
                  "values": {"division": "SEEG-P", "sets": {}}}
        with patch.object(gmes_profile, "load", return_value=profile), \
             patch.object(gmes_profile, "save", return_value="x.json"), \
             patch.object(core, "open_screen", return_value=screen), \
             patch.object(core, "org_selection", return_value={"found": True, "org": "MOBILE"}):
            result = core.run_screen(None, "M3912UM00", division="MOBILE",
                                     export="none", log=lambda _m: None)
        self.assertTrue(result["ok"], result.get("error"))
        self.assertEqual(screen.select_org_calls, ["MOBILE"])

    def test_no_profile_leaves_the_old_behaviour_unchanged(self):
        import gmes_profile
        screen = self.make_screen()
        with patch.object(gmes_profile, "load", return_value=None), \
             patch.object(gmes_profile, "save", return_value="x.json"), \
             patch.object(core, "open_screen", return_value=screen), \
             patch.object(core, "org_selection", return_value={"found": False}):
            result = core.run_screen(None, "M3912UM00", export="none",
                                     log=lambda _m: None)
        self.assertTrue(result["ok"], result.get("error"))
        self.assertEqual(screen.select_org_calls, [])   # nothing to replay, nothing ticked
        self.assertEqual(screen.date_range_calls, [])


class IntentMismatches(unittest.TestCase):
    """HISTORY.md - external review of 8ac502a, finding #4: `intent_mismatches()`
    is the pure decision logic behind run_screen()'s step 7.5, tested directly
    the way `InquirySettle` is - the browser-driven caller (`screen.refresh()`
    et al.) cannot run offline (CLAUDE.md 4.3), but what counts as a mismatch
    is ordinary Python and belongs under a real test."""

    def test_nothing_asked_for_is_never_a_mismatch(self):
        self.assertEqual(core.intent_mismatches({"filters": [], "unbound": []}, []), [])

    def test_an_option_still_selected_is_fine(self):
        resolved = [{"key": "k", "name": "btnCreate", "path": "", "label": "Create Date"}]
        fresh_options = [{"name": "btnCreate", "state": "selected"}]
        self.assertEqual(
            core.intent_mismatches({"filters": [], "unbound": []}, fresh_options,
                                   resolved_options=resolved), [])

    def test_an_option_that_reverted_to_deselected_is_caught(self):
        # Exactly the reviewer's scenario: a LATER step's oncolumnchanged
        # handler silently undoes an option this run already confirmed once.
        resolved = [{"key": "k", "name": "btnCreate", "path": "", "label": "Create Date"}]
        fresh_options = [{"name": "btnCreate", "state": "not selected"}]
        problems = core.intent_mismatches({"filters": [], "unbound": []}, fresh_options,
                                          resolved_options=resolved)
        self.assertEqual(len(problems), 1)
        self.assertIn("Create Date", problems[0])
        self.assertIn("no longer selected", problems[0])

    def test_an_option_that_vanished_entirely_is_caught(self):
        resolved = [{"key": "k", "name": "btnCreate", "path": "", "label": "Create Date"}]
        problems = core.intent_mismatches({"filters": [], "unbound": []}, [],
                                          resolved_options=resolved)
        self.assertIn("no longer on the screen", problems[0])

    def test_a_date_field_that_reverted_is_caught(self):
        written = flt(column="planYmd", dataset="dsFilterDVO")
        fresh = {"filters": [flt(column="planYmd", dataset="dsFilterDVO", value="20260101")],
                "unbound": []}
        problems = core.intent_mismatches(fresh, [], date_fields=[(written, "20260915")])
        self.assertEqual(len(problems), 1)
        self.assertIn("20260101", problems[0])
        self.assertIn("20260915", problems[0])

    def test_a_date_field_that_still_holds_its_value_is_fine(self):
        written = flt(column="planYmd", dataset="dsFilterDVO")
        fresh = {"filters": [flt(column="planYmd", dataset="dsFilterDVO", value="20260915")],
                "unbound": []}
        self.assertEqual(
            core.intent_mismatches(fresh, [], date_fields=[(written, "20260915")]), [])

    def test_digit_equivalent_dates_are_not_a_false_positive(self):
        # values_match() already treats 2026-09-15 == 20260915; this check
        # must reuse that, not a stricter string comparison.
        written = flt(column="planYmd", dataset="dsFilterDVO")
        fresh = {"filters": [flt(column="planYmd", dataset="dsFilterDVO",
                               value="2026-09-15")], "unbound": []}
        self.assertEqual(
            core.intent_mismatches(fresh, [], date_fields=[(written, "20260915")]), [])

    def test_an_applied_set_filter_that_reverted_is_caught(self):
        written = flt(column="poNo", control="edtPo", dataset="dsFilterDVO")
        fresh = {"filters": [flt(column="poNo", control="edtPo",
                               dataset="dsFilterDVO", value="")], "unbound": []}
        problems = core.intent_mismatches(fresh, [], applied_filters=[(written, "PO123")])
        self.assertIn("PO123", problems[0])

    def test_an_unbound_filter_is_matched_by_control_name_not_dataset(self):
        written = flt(column="", control="edtLot", dataset="", bound=False)
        fresh = {"filters": [], "unbound": [flt(column="", control="edtLot",
                                              dataset="", bound=False, value="LOT1")]}
        self.assertEqual(
            core.intent_mismatches(fresh, [], applied_filters=[(written, "LOT1")]), [])
        problems = core.intent_mismatches(fresh, [], applied_filters=[(written, "LOT2")])
        self.assertIn("LOT2", problems[0])

    def test_division_that_reverted_is_caught(self):
        problems = core.intent_mismatches({"filters": [], "unbound": []}, [],
                                          division_wanted="VD", division_seen="MOBILE")
        self.assertIn("MOBILE", problems[0])
        self.assertIn("VD", problems[0])

    def test_division_matches_case_insensitively(self):
        self.assertEqual(
            core.intent_mismatches({"filters": [], "unbound": []}, [],
                                   division_wanted="vd", division_seen="VD"), [])

    def test_no_division_asked_for_is_never_checked(self):
        self.assertEqual(
            core.intent_mismatches({"filters": [], "unbound": []}, [],
                                   division_wanted=None, division_seen="MOBILE"), [])

    def test_two_instances_of_a_reusable_component_are_not_confused(self):
        # HISTORY.md - external review of 1957ba9/cff282b, finding #1: a
        # dataset+column pair is not always unique within one window - a
        # reusable component can appear twice, each instance carrying the
        # SAME dataset.column at a DIFFERENT path. Without path in the
        # match key, a fresh read of instance B could "confirm" a write
        # that was actually made to instance A.
        written = flt(column="paramDate", dataset="dsFilterDVO",
                      path="application.mainframe.winA_0_1.form.divInstanceA.form")
        # Instance A (the one actually written) still correctly shows what
        # was set; instance B, a completely different control that happens
        # to share the same dataset.column, shows something else entirely.
        instance_a = flt(column="paramDate", dataset="dsFilterDVO", value="20260915",
                         path="application.mainframe.winA_0_1.form.divInstanceA.form")
        instance_b = flt(column="paramDate", dataset="dsFilterDVO", value="99999999",
                         path="application.mainframe.winA_0_1.form.divInstanceB.form")
        fresh = {"filters": [instance_b, instance_a], "unbound": []}
        self.assertEqual(
            core.intent_mismatches(fresh, [], date_fields=[(written, "20260915")]), [],
            "instance A's own real value must be what gets checked, "
            "regardless of dict ordering")

        # Now invert the order - instance A first, B second - proving this
        # is not merely "whichever happens to come first survives".
        fresh_reversed = {"filters": [instance_a, instance_b], "unbound": []}
        self.assertEqual(
            core.intent_mismatches(fresh_reversed, [], date_fields=[(written, "20260915")]), [])


class FinalIntentVerificationIntegration(unittest.TestCase):
    """run_screen() actually wires intent_mismatches() into step 7.5 and
    refuses to click Inquiry when it finds a problem - proven here through
    the real orchestration, not just the pure function above."""

    class DriftingScreen:
        """A screen whose date field silently reverts the moment refresh()
        is called after Inquiry-adjacent steps finish - simulating a later
        step's oncolumnchanged handler undoing an earlier write, the exact
        scenario finding #4 describes."""

        def __init__(self):
            self.title, self.menu_id, self.win_id = "Drift", "M", "W"
            self.warnings = []
            self.last_tree = None
            self._written = flt(column="planYmd", control="mskPlan", dataset="dsFilterDVO")
            self._info = {"filters": [dict(self._written, value="20260915")],
                          "unbound": [], "grids": [grid("grdPlan", "dsPlan", 100)]}
            self.inquiry_called = False

        @property
        def info(self):
            return self._info

        @property
        def filters(self):
            return self._info["filters"]

        @property
        def unbound(self):
            return self._info.get("unbound", [])

        def grid(self, _preferred=None):
            return self._info["grids"][0]

        def set_option(self, wanted):
            raise AssertionError("no option was asked for in this test")

        def clear_stale(self, _keep):
            return []

        def set_date_range(self, from_value, to_value, profile=None):
            return [(self._written, from_value)]

        def refresh(self):
            # The revert: by the time step 7.5 re-reads the screen, the date
            # is back to empty - as if a later handler cleared it.
            self._info = {"filters": [dict(self._written, value="")],
                          "unbound": [], "grids": [grid("grdPlan", "dsPlan", 100)]}
            return self._info

        def options(self):
            return []

        def inquiry(self, _grid):
            self.inquiry_called = True
            return 999   # would be visibly wrong if this test ever saw it

    def test_a_field_that_reverted_between_writing_and_inquiry_stops_the_run(self):
        import gmes_profile
        screen = self.DriftingScreen()
        with patch.object(core, "open_screen", return_value=screen), \
             patch.object(core, "org_selection", return_value={"found": False}), \
             patch.object(gmes_profile, "load", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "no longer matches what was asked for"):
                core.run_screen(None, "P9999UM00", date_from="20260915", date_to="20260915",
                                verify="planYmd", export="none", log=lambda _m: None)
        self.assertFalse(screen.inquiry_called,
                         "Inquiry must never be clicked once a drift is detected")


class GeneratedJavaScript(unittest.TestCase):
    """The JS is built by % substitution, so a stray literal % or a
    miscounted placeholder is a runtime crash inside the browser call rather
    than an import error. Both are cheap to catch here."""

    def snippets(self):
        import gmes_data
        helpers = gmes_data.JS_HELPERS
        vis = "function(el){return true;}"
        return {
            "discover": core._js(core.JS_DISCOVER, helpers, vis, '"P1112UM00"',
                                 '["edt"]', '"btn"'),
            "left_options": core.JS_LEFT_OPTIONS,
            "org_trees": core._js(core.JS_ORG_TREES, helpers, '"P1112UM00"'),
            "alert_text": core._js(core.JS_ALERT_TEXT, helpers, '"P1112UM00"'),
            "tick_org": core._js(core.JS_TICK_ORG, helpers, '"ds"', '["VD"]',
                                 "true", '"OrgCategory_GDS"', "[]"),
            "control_value": core._js(core.JS_CONTROL_VALUE, '"an.id"'),
            "tab_close": core._js(core.JS_TAB_CLOSE_TARGET, vis, '"TAB_win_0_1"'),
            "org_selection": core._js(core.JS_ORG_SELECTION, vis),
            "data_read": gmes_data.js_read("P1112UM00", "dsFilterDVO", -1, 0),
            "data_read_at_path": gmes_data.js_read("P1112UM00", "dsFilterDVO", -1, 0,
                                                    path="application.mainframe.win_0_1.form"),
            "data_set_values": gmes_data.js_set_values("P1112UM00", "dsFilterDVO",
                                                        {"paramFromDate": "20260101"}, None),
            "data_set_values_at_path": gmes_data.js_set_values(
                "P1112UM00", "dsFilterDVO", {"paramFromDate": "20260101"}, None,
                path="application.mainframe.win_0_1.form"),
        }

    def test_every_template_formats(self):
        for name, js in self.snippets().items():
            self.assertNotIn("%s", js, f"{name} has an unfilled placeholder")

    def test_every_snippet_is_balanced_and_is_an_iife(self):
        for name, js in self.snippets().items():
            for opener, closer in (("{", "}"), ("(", ")"), ("[", "]")):
                self.assertEqual(js.count(opener), js.count(closer),
                                 f"{name} has unbalanced {opener}{closer}")
            self.assertTrue(js.strip().startswith("(function"), name)
            self.assertTrue(js.strip().endswith("})()"), name)


class DatasetInstanceIsolation(unittest.TestCase):
    """HISTORY.md - external review of 8ac502a, findings #1/#2/#3.

    `gmes_data._dataset(screenCode, dsName)` used to pick the FIRST live
    form matching a screen code, app-wide - and a screen code is not
    unique: `WidgetFilter.xfdl` (this file's own comment on it, in
    JS_DISCOVER) is a reusable component embedded on more than one screen.
    With two windows open at once, a write addressed by screen code +
    dataset name alone could land on a same-named dataset belonging to a
    DIFFERENT window than the one this run actually opened, while reporting
    success - the read-back that "proves" it re-resolves the exact same
    (wrong) instance and agrees with itself.

    The fix: JS_DISCOVER now carries the exact form PATH each filter/grid
    was found on (scoped to this run's own work window), and every write
    downstream is asked to resolve that exact path, never "whichever form
    matches first"."""

    def test_apply_passes_the_discovered_path_to_the_write(self):
        screen = core.Screen(ws="WS", code="P1112UM00",
                             opened={"menuId": "M", "winId": "W"}, info={})
        f = flt(column="paramFromDate", dataset="dsFilterDVO",
               path="application.mainframe.winA_0_1.form.divBasic.form")
        with patch.object(core.gmes_data, "set_filter",
                          return_value={"found": True, "applied": {"paramFromDate": "1"}}) as sf:
            screen.apply(f, "1")
        sf.assert_called_once()
        _, kwargs = sf.call_args
        self.assertEqual(kwargs["path"], f["path"])

    def test_a_filter_with_no_discovered_path_still_writes_with_path_none(self):
        # Older code paths (a hand-built dict, a profile ref) may carry no
        # "path" key at all - must fall back to the old whole-app search
        # rather than crashing on a missing key.
        screen = core.Screen(ws="WS", code="P1112UM00",
                             opened={"menuId": "M", "winId": "W"}, info={})
        f = flt(column="paramFromDate", dataset="dsFilterDVO")
        del f["path"]
        with patch.object(core.gmes_data, "set_filter",
                          return_value={"found": True, "applied": {"paramFromDate": "1"}}) as sf:
            screen.apply(f, "1")
        self.assertIsNone(sf.call_args.kwargs["path"])

    def test_grid_reads_and_verification_all_pass_the_discovered_path(self):
        screen = core.Screen(ws="WS", code="P1112UM00",
                             opened={"menuId": "M", "winId": "W"}, info={})
        g = grid("grdResult", "dsMasterProdPlan", 5000,
                path="application.mainframe.winA_0_1.form.divResult.form")
        found = {"found": True, "total": 1, "columns": ["planYmd"],
                "rows": [{"planYmd": "20260101"}]}
        with patch.object(core, "read_rows", return_value=found) as rr:
            screen.rows(g)
        self.assertEqual(rr.call_args.kwargs["path"], g["path"])

        with patch.object(core, "verify_rows", return_value=(["20260101"], None)) as vr:
            screen.verify_column(g, "planYmd", "20260101")
        self.assertEqual(vr.call_args.kwargs["path"], g["path"])

        with patch.object(core, "verify_date_range", return_value=(["20260101"], None)) as vd:
            screen.verify_date_range(g, "planYmd", "20260101", "20260101")
        self.assertEqual(vd.call_args.kwargs["path"], g["path"])

        with patch.object(core, "poll_inquiry", return_value=1) as pi:
            # poll_inquiry itself is mocked here; this only proves Screen.
            # inquiry() forwards the grid's path to it.
            screen.inquiry(g)
        self.assertEqual(pi.call_args.kwargs["path"], g["path"])

    def test_the_module_level_helpers_thread_path_down_to_gmes_data(self):
        with patch.object(core.gmes_data, "read_dataset",
                          return_value={"found": True, "total": 0, "rows": [],
                                       "columns": []}) as rd:
            core.read_rows("WS", "P1112WM00", "dsMasterProdPlan",
                           path="application.mainframe.winA_0_1.form")
        self.assertEqual(rd.call_args.kwargs["path"],
                         "application.mainframe.winA_0_1.form")

    def test_the_exact_path_reaches_the_generated_javascript_not_null(self):
        js_with_path = gmes_data.js_set_values(
            "P1112WM00", "dsFilterDVO", {"paramFromDate": "1"}, None,
            path="application.mainframe.winA_0_1.form")
        self.assertIn('"application.mainframe.winA_0_1.form"', js_with_path)

        js_without_path = gmes_data.js_set_values(
            "P1112WM00", "dsFilterDVO", {"paramFromDate": "1"}, None)
        # The third argument to _dataset() must be JSON `null`, not the
        # string "None" - a Python str(None) leak here would silently ask
        # the browser to match forms whose path literally reads "None".
        self.assertIn("_dataset(", js_without_path)
        self.assertNotIn('"None"', js_without_path)

    def test_the_write_refuses_a_multi_row_dataset_with_no_valid_row_position(self):
        # Source-level guard, mirroring how LockoutWarningDetection tests
        # the real regex rather than a re-typed copy: this dataset is not
        # executable offline (no mock for G-MES, CLAUDE.md 4.3), so the
        # JS source itself is asserted to contain the refusal rather than
        # a Python re-implementation that could silently drift from it.
        js = gmes_data.js_set_values("P1112WM00", "dsFilterDVO",
                                     {"paramFromDate": "1"}, None)
        self.assertIn("rowposition", js)
        self.assertIn("refusing to guess which row is the filter", js)

    def test_a_single_row_dataset_needs_no_row_position_at_all(self):
        js = gmes_data.js_set_values("P1112WM00", "dsFilterDVO",
                                     {"paramFromDate": "1"}, None)
        self.assertIn("count === 1", js)

    def test_discovery_dedup_is_scoped_by_path_not_dataset_column_alone(self):
        # HISTORY.md - external review of 1957ba9/cff282b, finding #2:
        # JS_DISCOVER used to collapse every filter sharing one
        # dataset.column into a single entry BEFORE path ever reached
        # Python - two genuinely different instances of a reusable
        # component would silently become one, discarding whichever lost
        # the dedup regardless of how carefully the write/verify path
        # matches by path afterward. Source-level (not executable offline,
        # CLAUDE.md 4.3), the same way the ancestor-walk fix above is.
        self.assertIn("f.path + '|' + f.dataset + '.' + f.column", core.JS_DISCOVER)

    def test_bound_vs_unbound_cross_check_is_keyed_by_dom_id_not_path(self):
        # HISTORY.md Phase 82.7, live-caught on M3912UM00: a bind's own
        # `path` is the form that owns the DATASET, not the form actually
        # containing a deeply-nested control (fromDate/searchTypeCode/...
        # live several Divs below the form that binds them). Keying the
        # bound/unbound cross-check by `path + control` (this file's own
        # earlier fix, for the UNRELATED problem the dedup above handles)
        # broke it here: all 7 real filters were reported a second time as
        # unbound. `id` - the fully expanded DOM id, computed identically
        # by both loops for the same physical element regardless of which
        # form reports it - replaced it.
        self.assertIn("boundLeaves[f.id] = true", core.JS_DISCOVER)
        self.assertIn("boundLeaves[u.id] || seenUnbound[u.id]", core.JS_DISCOVER)

    def test_input_detection_prefers_the_live_component_type_over_a_name_guess(self):
        # HISTORY.md Phase 82.7, live-caught on M3912UM00: its filter panel
        # binds fromDate/toDate/searchTypeCode/searchStartNo/
        # searchModelCode/searchUse/searchProductCode - none of which start
        # with any of INPUT_PREFIXES (edt/msk/cbo/chk/rdo/cal/spn/txt/lst),
        # so kindOf()'s name-prefix guess dropped every one of the screen's
        # 7 real filters, reporting "Filters bound to a dataset (0)" on a
        # screen with a visible Period/Type/Product/Start No./Model panel.
        # Confirmed live: the resolved elements' actual Nexacro types were
        # MaskEdit, Combo and Edit - authored by the platform, not a
        # person, and not a guess. Source-level (CLAUDE.md 4.3).
        self.assertIn("INPUT_KIND_RE = /^(edit|maskedit|combo|checkbox|radio|"
                      "spin|calendar|listbox|textarea)$/i", core.JS_DISCOVER)
        # The function's actual BODY must consult the live kind first, not
        # merely have the regex defined somewhere nearby unused - checked
        # by sabotaging exactly this line and confirming the test above
        # fails without it.
        self.assertIn("if (kind) return INPUT_KIND_RE.test(kind);", core.JS_DISCOVER)
        # The element (and therefore its real `kind`) must be resolved
        # BEFORE the input/display decision - checking the guess first
        # would defeat the whole point.
        bound_section = core.JS_DISCOVER[core.JS_DISCOVER.index("Bound controls -> settable"):]
        el_at = bound_section.index("const el = id ? document.getElementById(id) : null;")
        decision_at = bound_section.index("if (!isInputControl(kind, leaf)) continue;")
        self.assertLess(el_at, decision_at)

    def test_shell_form_filter_also_excludes_the_left_panel_widget_filter(self):
        # HISTORY.md Phase 82.12, live-caught on P3151WM00: the comment
        # right above SHELL already claimed "the widget list" was excluded,
        # but the regex itself never named WidgetFilter.xfdl.js - live, its
        # own grid (Grid02/dsGrid00) passed the filename test and was then
        # offered as a candidate result grid, even though a read against it
        # returns nothing (it belongs to the left filter panel every G-MES
        # screen carries, not to the report). Checked as the literal regex
        # fragment, not a bare substring - the comment explaining this fix
        # also says "WidgetFilter", which would let a sabotage that removed
        # only the pattern slip past a looser check.
        self.assertIn("PortalMain|WidgetFilter/i", core.JS_DISCOVER)

    def test_a_widget_nested_inside_the_left_filter_panel_is_excluded_by_path(self):
        # HISTORY.md Phase 82.12, live-caught on P3151WM00: a date-range
        # calendar picker embedded INSIDE the left filter panel ships as its
        # own file, CalendarD.xfdl.js - not shell by name, and its dataset
        # (dsCalendar) is shaped like neither an org tree nor a Quick View,
        # so neither existing chrome check caught it. Live, it was the only
        # grid with a non-zero bounding box while the screen's real result
        # grids all read area 0 (their tab was not yet active), so it won
        # choose_grid()'s "biggest" default outright. The path segment
        # 'divFilter.divWidgetMain' is specific to the LEFT panel's own
        # widget container - the work area's is named 'divWork.divWidgetMain'
        # (confirmed live) - so this cannot also exclude a real result grid.
        grids_section = core.JS_DISCOVER[core.JS_DISCOVER.index("Grids -> candidate result sets"):]
        self.assertIn("h.path.indexOf('divFilter.divWidgetMain') !== -1", grids_section)
        # Must run AFTER chromeDatasets/__EXCEL__ are checked but BEFORE the
        # grid is actually pushed, or a chrome widget still gets offered.
        exclude_at = grids_section.index("h.path.indexOf('divFilter.divWidgetMain') !== -1")
        push_at = grids_section.index("grids.push(")
        self.assertLess(exclude_at, push_at)

    def test_form_walk_follows_a_tab_controls_own_tabpages_collection(self):
        # HISTORY.md Phase 82.11, live-caught on P3151WM00: a Nexacro Tab
        # control has no .form/.components of its own at all - its pages
        # (Tabpage1..N) are reachable only through a SEPARATE .tabpages
        # collection. Without walking it, every grid/filter/dataset living
        # inside a tabbed panel was invisible to _findForms() - the
        # foundational walker essentially every discovery/read/write path in
        # this project depends on. Source-level (CLAUDE.md 4.3): no mock for
        # a live Nexacro Tab object exists to exercise this offline.
        self.assertIn("tabpages = c && c.tabpages", gmes_data.JS_HELPERS)
        self.assertIn("walkForm(pageForm,", gmes_data.JS_HELPERS)

    def test_form_walk_depth_cap_allows_for_tabbed_nesting(self):
        # HISTORY.md Phase 82.11, live-caught on P3151WM00: the pre-existing
        # depth > 12 cap was tuned for a form tree with no tabbed panels.
        # Walking into a Tab's own pages adds 2-3 levels per nested tab, so
        # the screen's actually-visible tab's own result grid (grdMain01)
        # sat just past the old cap while a DIFFERENT, hidden tab's grid was
        # still found - a more misleading failure than finding nothing at
        # all. Asserted as "wide enough for real nested-tab layouts", not
        # pinned to the exact number, so a future generous-headroom bump
        # does not fail this test for the wrong reason.
        m = re.search(r"depth > (\d+)", gmes_data.JS_HELPERS)
        self.assertIsNotNone(m, "walkForm's depth cap must still exist")
        self.assertGreaterEqual(int(m.group(1)), 20)

    def test_the_exact_path_search_also_walks_ancestor_forms(self):
        # HISTORY.md Phase 80.4, live-caught: P1111UM00's grdSum grid's own
        # component path is ...divWork.divLeft, but Nexacro resolves
        # dsModelPlanList through the ANCESTOR SCOPE CHAIN - it is only an
        # own property of the PARENT form, ...divWork. Matching the exact
        # path alone (this test's own earlier version, before the live
        # fix) made every read/write on that screen fail closed. Asserted
        # at the source level, the same way LockoutWarningDetection tests
        # the real regex rather than a re-typed copy - this cannot run
        # offline (no mock for G-MES, CLAUDE.md 4.3).
        js = gmes_data.js_set_values("P1112WM00", "dsFilterDVO",
                                     {"paramFromDate": "1"}, None,
                                     path="a.b.c")
        self.assertIn("h.path === exactPath", js)
        self.assertIn("exactPath.indexOf(h.path + '.') === 0", js)
        # Closest scope wins first - sorted longest path (most specific)
        # first, so an exact match is always preferred over an ancestor
        # when both happen to carry a same-named dataset.
        self.assertIn("b.path.length - a.path.length", js)

    def test_ancestor_matching_never_crosses_into_a_different_window(self):
        # The safety property the exact-path fix exists for must survive
        # widening it to ancestors: reproduced here in plain Python against
        # the exact same rule the JS above implements, since a sibling
        # window's path can never be a prefix of this one's - they diverge
        # at the win*_N_NNN segment itself.
        def is_ancestor_or_self(candidate, exact_path):
            return candidate == exact_path or exact_path.startswith(candidate + ".")

        window_a = "application.mainframe.workFrameSet.winPPM0219_0_1.divWork"
        window_b_sibling = "application.mainframe.workFrameSet.winPPM0219_0_2.divWork"
        self.assertFalse(is_ancestor_or_self(window_b_sibling, window_a))
        # But a genuine ancestor of the SAME window still matches.
        ancestor = "application.mainframe.workFrameSet.winPPM0219_0_1"
        self.assertTrue(is_ancestor_or_self(ancestor, window_a))


class FindRefStablePathMatching(unittest.TestCase):
    """HISTORY.md - external review of 1957ba9/cff282b, finding #3: a saved
    profile's `from`/`to`/`grid` reference used to keep only dataset+column
    (or control name), matched against WHATEVER live control has that name
    on replay - the exact ambiguity Phase 80 closed for a fresh discovery,
    reopened specifically for replay mode. `stable_path` (the relative path
    with the window's own renumbered instance segment stripped, the same
    trick Phase 76 already uses for option identity) closes it without
    breaking every profile written before this fix."""

    def make_screen(self, filters=(), unbound=()):
        return core.Screen(ws=None, code="P1112UM00",
                           opened={"menuId": "M", "winId": "W"},
                           info={"filters": list(filters), "unbound": list(unbound)})

    def test_a_ref_with_no_stable_path_matches_by_dataset_and_column_as_before(self):
        # Every profile written before this fix - must keep working exactly
        # as it always did, with no relearn forced on anyone.
        ref = {"dataset": "dsFilterDVO", "column": "paramFromDate",
              "control": "mskDateFrom", "form": "", "label": ""}
        current = flt(column="paramFromDate", dataset="dsFilterDVO",
                      path="application.mainframe.winA_0_1.form.divOnly.form")
        screen = self.make_screen(filters=[current])
        self.assertIs(screen.find_ref(ref), current)

    def test_a_ref_with_a_stable_path_only_matches_that_exact_instance(self):
        ref = {"dataset": "dsFilterDVO", "column": "paramDate", "control": "",
              "form": "", "label": "",
              "stable_path": "form.divInstanceA.form"}
        instance_a = flt(column="paramDate", dataset="dsFilterDVO",
                         path="application.mainframe.winA_0_1.form.divInstanceA.form",
                         value="right one")
        instance_b = flt(column="paramDate", dataset="dsFilterDVO",
                         path="application.mainframe.winA_0_1.form.divInstanceB.form",
                         value="wrong one")
        screen = self.make_screen(filters=[instance_b, instance_a])
        found = screen.find_ref(ref)
        self.assertIs(found, instance_a)
        self.assertEqual(found["value"], "right one")

    def test_a_stable_path_that_no_longer_exists_is_gone_not_a_loose_match(self):
        # The reusable component's shape changed - a dataset.column match
        # exists, but not at the path this profile actually proved. That is
        # evidence worth stopping on (CLAUDE.md 3.9), not a "close enough".
        ref = {"dataset": "dsFilterDVO", "column": "paramDate", "control": "",
              "form": "", "label": "",
              "stable_path": "form.divInstanceA.form"}
        only_b = flt(column="paramDate", dataset="dsFilterDVO",
                     path="application.mainframe.winA_0_1.form.divInstanceB.form")
        screen = self.make_screen(filters=[only_b])
        self.assertIsNone(screen.find_ref(ref))


class OrgTreeWindowScoping(unittest.TestCase):
    """HISTORY.md - external review of 1957ba9/cff282b, finding #5:
    `tick_org()`'s own fallback search matches by the TREE's shared form
    name (e.g. "OrgCategory_GDS"), never the work screen's own code - it is
    a reusable component embedded on many unrelated screens, so an
    unscoped search can reach a completely different window's copy of the
    same tree and silently tick a division nobody asked to change there.
    `paths` - the exact instances `Screen.trees()` already found within
    THIS run's own window - is what actually restricts the write."""

    def test_the_generated_js_is_scoped_by_paths_when_given(self):
        # Source-level (not executable offline, CLAUDE.md 4.3), the same
        # way the ancestor-walk and dedup fixes above are.
        js = core._js(core.JS_TICK_ORG, gmes_data.JS_HELPERS, '"ds"', '["VD"]',
                      "true", '"OrgCategory_GDS"', '["a.b.c"]')
        self.assertIn("paths.indexOf(h.path) >= 0", js)
        self.assertIn("paths && paths.length", js)

    def make_screen(self, trees):
        screen = core.Screen(ws=Mock(), code="P1112UM00",
                             opened={"menuId": "M", "winId": "winA_0_1"}, info={})
        self._trees_patcher = patch.object(core, "org_trees",
                                           return_value={"trees": trees})
        self._trees_patcher.start()
        self.addCleanup(self._trees_patcher.stop)
        return screen

    def test_select_org_passes_every_window_scoped_copy_of_the_same_tree(self):
        # Two copies of the SAME logical tree in THIS window (the Work
        # Calendar three-tabs case Phase 65's own comment describes) -
        # both paths must be passed through.
        copy1 = {"form": "OrgCategory_GDS", "dataset": "dsCatCommonTreeNodeDVO",
                 "names": ["VD"], "settable": True,
                 "path": "application.mainframe.winA_0_1.form.divTab1"}
        copy2 = {"form": "OrgCategory_GDS", "dataset": "dsCatCommonTreeNodeDVO",
                 "names": ["VD"], "settable": True,
                 "path": "application.mainframe.winA_0_1.form.divTab2"}
        screen = self.make_screen([copy1, copy2])
        with patch.object(core, "tick_org",
                          return_value={"found": True, "ticked": [], "cleared": []}) as tick, \
             patch.object(core, "org_selection",
                          return_value={"found": True, "org": "VD"}):
            # tree= picks a target among the two pool-equivalent copies
            # without tripping the pre-existing (unrelated) ambiguity
            # refusal - this test is about paths gathering, not pool choice.
            screen.select_org("VD", tree="OrgCategory_GDS")
        self.assertEqual(sorted(tick.call_args.kwargs["paths"]),
                         sorted([copy1["path"], copy2["path"]]))

    def test_an_unrelated_tree_elsewhere_in_the_window_is_not_included(self):
        target = {"form": "OrgCategory_GDS", "dataset": "dsCatCommonTreeNodeDVO",
                 "names": ["VD"], "settable": True,
                 "path": "application.mainframe.winA_0_1.form.divOrg"}
        unrelated = {"form": "OtherTree", "dataset": "dsOtherDVO",
                    "names": ["MOBILE"], "settable": True,
                    "path": "application.mainframe.winA_0_1.form.divOther"}
        screen = self.make_screen([target, unrelated])
        with patch.object(core, "tick_org",
                          return_value={"found": True, "ticked": [], "cleared": []}) as tick, \
             patch.object(core, "org_selection",
                          return_value={"found": True, "org": "VD"}):
            screen.select_org("VD")
        self.assertEqual(tick.call_args.kwargs["paths"], [target["path"]])


class DailyProdPlanPathResolution(unittest.TestCase):
    """`gmes_daily_prodplan.py` addresses its datasets by hardcoded screen
    code + dataset name (HISTORY.md Phase 78's own comment: it deliberately
    shares gmes_core's dataset-level functions rather than reimplementing
    them). `path_for()` closes the same gap `Screen.apply()` was fixed for,
    using the `Screen` object `ensure_screen()` now returns instead of
    throwing it away as a formatted string."""

    def make_screen(self, filters=(), unbound=(), grids=()):
        info = {"filters": list(filters), "unbound": list(unbound),
                "grids": list(grids)}
        return core.Screen(ws=None, code="P1112UM00",
                           opened={"menuId": "M", "winId": "W"}, info=info)

    def test_resolves_a_bound_filter_by_dataset_name(self):
        import gmes_daily_prodplan as job
        f = flt(dataset="dsFilterDVO",
               path="application.mainframe.winA_0_1.form.divBasic.form")
        screen = self.make_screen(filters=[f])
        self.assertEqual(job.path_for(screen, "dsFilterDVO"), f["path"])

    def test_resolves_a_result_grid_by_dataset_name(self):
        import gmes_daily_prodplan as job
        g = grid("grdResult", "dsMasterProdPlan", 5000,
                path="application.mainframe.winA_0_1.form.divResult.form")
        screen = self.make_screen(grids=[g])
        self.assertEqual(job.path_for(screen, "dsMasterProdPlan"), g["path"])

    def test_an_undiscovered_dataset_resolves_to_none_not_a_crash(self):
        import gmes_daily_prodplan as job
        screen = self.make_screen()
        self.assertIsNone(job.path_for(screen, "dsSomethingElse"))

    def test_select_division_only_forwards_paths_for_its_own_org_tree(self):
        # HISTORY.md - external review of 1957ba9/cff282b, finding #5,
        # applied to the nightly job specifically: ORG_SCREEN
        # ("OrgCategory_GDS") is the tree's shared form name, not this
        # job's own work screen code. select_division() must gather ITS
        # OWN tree's paths from screen.trees() (window-scoped, since
        # org_trees() anchors on screen.code) and pass them to
        # core.tick_org() - not let tick_org() fall back to its unscoped
        # app-wide search, and not include some OTHER dataset's paths that
        # happen to also be discovered in the same window.
        import gmes_daily_prodplan as job
        screen = self.make_screen()
        own_tree = {"form": job.ORG_SCREEN, "dataset": job.ORG_TREE_DATASET,
                   "names": ["VD"], "settable": True,
                   "path": "application.mainframe.winA_0_1.form.divOrg"}
        unrelated_tree = {"form": "OtherTree", "dataset": "dsOtherDVO",
                         "names": ["VD"], "settable": True,
                         "path": "application.mainframe.winA_0_1.form.divOther"}
        with patch.object(core, "org_trees",
                          return_value={"trees": [own_tree, unrelated_tree]}), \
             patch.object(job.core, "tick_org",
                          return_value={"found": True, "ticked": [], "cleared": []}) as tick, \
             patch.object(job.core, "org_selection",
                          return_value={"found": True, "org": "VD"}):
            job.select_division(Mock(), screen, "VD")
        self.assertEqual(tick.call_args.kwargs["paths"], [own_tree["path"]])

    def test_ensure_screen_returns_the_live_screen_not_a_formatted_string(self):
        import gmes_daily_prodplan as job
        opened_screen = core.Screen(ws="WS", code=job.CONTAINER_SCREEN,
                                    opened={"menuId": "M", "winId": "W"},
                                    info={"filters": [], "unbound": [], "grids": []})
        opened_screen.refresh = lambda: opened_screen.info
        with patch.object(job.core, "open_screen", return_value=opened_screen), \
             patch.object(job.gmes_data, "list_forms",
                          return_value={"forms": [{"file": "P1112WM00.xfdl.js"}]}):
            result = job.ensure_screen("WS")
        self.assertIs(result, opened_screen)

    def test_main_resolves_and_forwards_paths_end_to_end(self):
        # Sabotage-provable: removing `path=filter_path`/`path=result_path`
        # from main()'s calls makes this test's mocks receive `path=None`
        # instead of the fixture's real path and fail.
        import gmes_daily_prodplan as job
        filter_entry = flt(dataset="dsFilterDVO",
                          path="application.mainframe.winA_0_1.form.divBasic.form")
        grid_entry = grid("grdResult", job.RESULT_DATASET, 5000,
                         path="application.mainframe.winA_0_1.form.divResult.form")
        opened_screen = self.make_screen(filters=[filter_entry], grids=[grid_entry])
        opened_screen.title, opened_screen.win_id = "Production Plan", "winA_0_1"

        with patch.object(sys, "argv", ["gmes_daily_prodplan.py", "--csv"]), \
             patch.object(job.core, "acquire_run_lock"), \
             patch.object(job.core, "release_run_lock"), \
             patch.object(job.core, "sign_in", return_value=True), \
             patch.object(job, "connect_gmes", return_value=Mock(close=lambda: None)), \
             patch.object(job, "is_logged_in", return_value=(True, "someone")), \
             patch.object(job, "ensure_screen", return_value=opened_screen), \
             patch.object(job, "set_plan_date", return_value="ok") as spd, \
             patch.object(job, "select_division", return_value="ok"), \
             patch.object(job, "run_inquiry", return_value=5) as ri, \
             patch.object(job, "verify_result_date", return_value=["20260101"]) as vrd, \
             patch.object(job, "download_excel", return_value="/tmp/x.xlsx"), \
             patch.object(job.core, "check_download"), \
             patch.object(job, "is_drm_protected", return_value=False), \
             patch.object(job.os.path, "getsize", return_value=1024), \
             patch.object(job, "export_clean_data", return_value=("/tmp/x.csv", 1, 0)) as ecd, \
             patch.object(job.shutil, "move"), \
             patch.object(job.os, "makedirs"):
            code = job.main()

        self.assertEqual(code, 0)
        self.assertEqual(spd.call_args.kwargs["path"], filter_entry["path"])
        self.assertEqual(ri.call_args.kwargs["path"], grid_entry["path"])
        self.assertEqual(vrd.call_args.kwargs["path"], grid_entry["path"])
        self.assertEqual(ecd.call_args.kwargs["path"], grid_entry["path"])

    def test_the_nightly_job_writes_excel_only_unless_csv_is_asked_for(self):
        # HISTORY.md Phase 94.1: the owner wants Excel files only.
        # Sabotage-provable: removing `path=filter_path`/`path=result_path`
        # from main()'s calls makes this test's mocks receive `path=None`
        # instead of the fixture's real path and fail.
        import gmes_daily_prodplan as job
        filter_entry = flt(dataset="dsFilterDVO",
                          path="application.mainframe.winA_0_1.form.divBasic.form")
        grid_entry = grid("grdResult", job.RESULT_DATASET, 5000,
                         path="application.mainframe.winA_0_1.form.divResult.form")
        opened_screen = self.make_screen(filters=[filter_entry], grids=[grid_entry])
        opened_screen.title, opened_screen.win_id = "Production Plan", "winA_0_1"

        with patch.object(sys, "argv", ["gmes_daily_prodplan.py"]), \
             patch.object(job.core, "acquire_run_lock"), \
             patch.object(job.core, "release_run_lock"), \
             patch.object(job.core, "sign_in", return_value=True), \
             patch.object(job, "connect_gmes", return_value=Mock(close=lambda: None)), \
             patch.object(job, "is_logged_in", return_value=(True, "someone")), \
             patch.object(job, "ensure_screen", return_value=opened_screen), \
             patch.object(job, "set_plan_date", return_value="ok") as spd, \
             patch.object(job, "select_division", return_value="ok"), \
             patch.object(job, "run_inquiry", return_value=5) as ri, \
             patch.object(job, "verify_result_date", return_value=["20260101"]) as vrd, \
             patch.object(job, "download_excel", return_value="/tmp/x.xlsx"), \
             patch.object(job.core, "check_download"), \
             patch.object(job, "is_drm_protected", return_value=False), \
             patch.object(job.os.path, "getsize", return_value=1024), \
             patch.object(job, "export_clean_data", return_value=("/tmp/x.csv", 1, 0)) as ecd, \
             patch.object(job.shutil, "move"), \
             patch.object(job.os, "makedirs"):
            code = job.main()

        self.assertEqual(code, 0)
        ecd.assert_not_called()

    def test_main_refuses_rather_than_fall_back_to_an_unscoped_search(self):
        # HISTORY.md - external review of 1957ba9/cff282b, finding #6: this
        # job knows exactly which two datasets it needs: a missing path for
        # either means the screen is not in the state this job assumes, and
        # that must stop the run, not silently retry the old whole-app
        # search gmes_data.py falls back to when path=None.
        import gmes_daily_prodplan as job
        # No filters/grids discovered at all - path_for() resolves both to
        # None.
        opened_screen = self.make_screen()
        opened_screen.title, opened_screen.win_id = "Production Plan", "winA_0_1"

        with patch.object(sys, "argv", ["gmes_daily_prodplan.py"]), \
             patch.object(job.core, "acquire_run_lock"), \
             patch.object(job.core, "release_run_lock"), \
             patch.object(job.core, "sign_in", return_value=True), \
             patch.object(job, "connect_gmes", return_value=Mock(close=lambda: None)), \
             patch.object(job, "is_logged_in", return_value=(True, "someone")), \
             patch.object(job, "ensure_screen", return_value=opened_screen), \
             patch.object(job, "set_plan_date") as spd, \
             patch.object(job, "screenshot_on_failure"):
            code = job.main()

        self.assertEqual(code, 1)
        spd.assert_not_called()   # stopped before writing anything


class RunLock(unittest.TestCase):
    """Open Item #17, live-proven: two gmes_report.py runs sharing one
    Chrome/CDP session interfere with each other. A concurrent run against
    P1112UM00 failed with "The result row could not be clicked (grid row
    not visible)" while another run had M4131UM00 open in the same
    browser - a real symptom that gives no hint a second run is the cause.
    acquire_run_lock()/release_run_lock() turn that into an immediate,
    explicit refusal."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp(prefix="gmes-lock-test-")
        self.lock_path = os.path.join(self.tmp, ".run.lock")
        self.patcher = patch.object(core, "run_lock_path", lambda: self.lock_path)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.addCleanup(core.release_run_lock)

    def test_first_caller_gets_the_lock(self):
        token = core.acquire_run_lock()
        self.assertTrue(token)
        self.assertTrue(os.path.exists(self.lock_path))

    def test_second_caller_is_refused_while_the_first_still_holds_it(self):
        core.acquire_run_lock()
        with self.assertRaises(core.RunLocked) as cm:
            core.acquire_run_lock()
        self.assertIn(str(os.getpid()), str(cm.exception))

    def test_release_lets_the_next_caller_in(self):
        token = core.acquire_run_lock()
        core.release_run_lock(token)
        self.assertTrue(core.acquire_run_lock())

    def test_a_lock_left_by_a_dead_process_does_not_block_forever(self):
        # A pid that cannot possibly be a live process on this machine -
        # simulates a run that crashed before it could release its lock.
        with open(self.lock_path, "w", encoding="utf-8") as fh:
            fh.write("999999999 2020-01-01 00:00:00\n")
        self.assertTrue(core.acquire_run_lock())

    def test_releasing_an_unheld_lock_does_not_raise(self):
        core.release_run_lock()  # no lock file exists yet

    def test_release_only_removes_the_lock_named_by_its_own_token(self):
        # HISTORY.md Open Item 40 / R2: a release used to delete whatever was
        # sitting at the path unconditionally. If this process's own lock was
        # since judged stale and reclaimed by a NEW run, this process
        # releasing its (already-gone) token must never delete the new run's
        # live lock.
        import uuid as _uuid
        token = core.acquire_run_lock()
        with open(self.lock_path, "w", encoding="utf-8") as fh:
            fh.write(f"999999\t2026-01-01 00:00:00\t{_uuid.uuid4().hex}\n")
        core.release_run_lock(token)
        self.assertTrue(os.path.exists(self.lock_path))

    def test_a_token_that_still_matches_is_removed(self):
        token = core.acquire_run_lock()
        core.release_run_lock(token)
        self.assertFalse(os.path.exists(self.lock_path))

    def test_release_with_no_token_is_unconditional_like_before(self):
        core.acquire_run_lock()
        core.release_run_lock()          # no token - the permissive fallback
        self.assertFalse(os.path.exists(self.lock_path))


class BatchSelection(unittest.TestCase):
    """HISTORY.md Phase 83. Choosing WHAT a batch runs is the one place a typo
    quietly changes what is queried against production, so the grammar is
    strict: anything not understood raises instead of being guessed at."""

    CODES = ["Q2111UM00", "P3111UM00", "R5216UM00", "M3912UM00"]

    def parse(self, text, batches=None):
        import gmes_batch
        return gmes_batch.parse_selection(text, self.CODES, batches)

    def test_all_and_star_mean_every_recording_in_list_order(self):
        self.assertEqual(self.parse("all"), self.CODES)
        self.assertEqual(self.parse("*"), self.CODES)

    def test_numbers_mean_the_listed_row(self):
        self.assertEqual(self.parse("2"), ["P3111UM00"])
        self.assertEqual(self.parse("4, 1"), ["M3912UM00", "Q2111UM00"])

    def test_a_range_is_inclusive(self):
        self.assertEqual(self.parse("2-3"), ["P3111UM00", "R5216UM00"])

    def test_a_screen_code_is_matched_ignoring_case(self):
        self.assertEqual(self.parse("r5216um00"), ["R5216UM00"])

    def test_exclusions_remove_by_number_or_code(self):
        self.assertEqual(self.parse("all !2"), ["Q2111UM00", "R5216UM00", "M3912UM00"])
        self.assertEqual(self.parse("all -M3912UM00 !1"), ["P3111UM00", "R5216UM00"])

    def test_duplicates_are_dropped_and_first_appearance_wins(self):
        self.assertEqual(self.parse("3 1 3 1-3"), ["R5216UM00", "Q2111UM00", "P3111UM00"])

    def test_a_saved_batch_expands_to_its_screens(self):
        self.assertEqual(self.parse("@am", {"am": ["M3912UM00", "Q2111UM00"]}),
                         ["M3912UM00", "Q2111UM00"])

    def test_things_that_are_not_understood_raise(self):
        for bad in ("", "   ", "0", "5", "3-9", "4-2", "Z9999ZZ00", "@nope", "!1",
                    "all !all"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.parse(bad)


class BatchDates(unittest.TestCase):
    TODAY = __import__("datetime").date(2026, 9, 20)

    def resolve(self, policy):
        import gmes_batch
        return gmes_batch.resolve_dates(policy, self.TODAY)

    def test_named_policies(self):
        self.assertEqual(self.resolve("yesterday"), ("20260919", "20260919"))
        self.assertEqual(self.resolve(""), ("20260919", "20260919"))
        self.assertEqual(self.resolve("Today"), ("20260920", "20260920"))
        self.assertEqual(self.resolve("-3"), ("20260917", "20260917"))
        self.assertEqual(self.resolve("keep"), (None, None))

    def test_yesterday_crosses_month_and_year_boundaries(self):
        import gmes_batch
        from datetime import date
        self.assertEqual(gmes_batch.resolve_dates("yesterday", date(2026, 3, 1)),
                         ("20260228", "20260228"))
        self.assertEqual(gmes_batch.resolve_dates("yesterday", date(2027, 1, 1)),
                         ("20261231", "20261231"))

    def test_an_explicit_day_or_range(self):
        self.assertEqual(self.resolve("20260915"), ("20260915", "20260915"))
        self.assertEqual(self.resolve("20260901:20260907"), ("20260901", "20260907"))

    def test_nonsense_is_refused_not_defaulted(self):
        for bad in ("tomorrow-ish", "20261340", "20260907:20260901", "2026", "yesterdy"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.resolve(bad)


class BatchDateRoles(unittest.TestCase):
    """A profile's date typed with --set lives under a name like mskFromDate.
    Rewriting the WRONG remembered value to yesterday would query the wrong
    thing with no error, so the role is judged on the name AND the value."""

    def role(self, key, value):
        import gmes_batch
        return gmes_batch.date_role(key, value)

    def test_from_to_and_single_names(self):
        self.assertEqual(self.role("mskFromDate", "20260901"), "from")
        self.assertEqual(self.role("aplyStartDt", "20260901"), "from")
        self.assertEqual(self.role("endYmd", "20260901"), "to")
        self.assertEqual(self.role("workDate", "20260901"), "single")

    def test_an_eight_digit_code_that_is_not_named_like_a_date_is_left_alone(self):
        self.assertIsNone(self.role("lotNo", "20260901"))
        self.assertIsNone(self.role("paramVendorCode", "20260901"))

    def test_a_date_named_field_with_a_non_date_value_is_left_alone(self):
        self.assertIsNone(self.role("mskFromDate", "ABC"))
        self.assertIsNone(self.role("mskFromDate", ""))


class BatchRetarget(unittest.TestCase):
    def retarget(self, values, df="20260919", dt="20260919"):
        import gmes_batch
        return gmes_batch.retarget(values, df, dt)

    def test_from_to_are_replaced_and_everything_else_is_kept(self):
        r = self.retarget({"division": "VD", "from": "20260901", "to": "20260901",
                           "sets": {"lotNo": "ABC"}})
        self.assertEqual((r.date_from, r.date_to, r.dated), ("20260919", "20260919", True))
        self.assertIsNone(r.sets)          # nothing date-shaped inside sets

    def test_date_shaped_sets_are_rewritten_by_role_and_others_untouched(self):
        r = self.retarget({"sets": {"mskFromDate": "20260901", "endYmd": "20260901",
                                    "lotNo": "20260901", "line": "A"}},
                          "20260915", "20260917")
        self.assertEqual(r.sets, {"mskFromDate": "20260915", "endYmd": "20260917",
                                  "lotNo": "20260901", "line": "A"})
        self.assertEqual(sorted(r.changed), ["endYmd", "mskFromDate"])
        self.assertTrue(r.dated)

    def test_the_original_values_are_not_mutated(self):
        sets = {"mskFromDate": "20260901"}
        self.retarget({"sets": sets})
        self.assertEqual(sets, {"mskFromDate": "20260901"})

    def test_keep_changes_nothing(self):
        import gmes_batch
        r = gmes_batch.retarget({"from": "20260901", "to": "20260901",
                                 "sets": {"mskFromDate": "20260901"}}, None, None)
        self.assertEqual((r.date_from, r.sets, r.dated), (None, None, False))

    def test_a_screen_with_no_remembered_date_is_not_marked_dated(self):
        self.assertFalse(self.retarget({"division": "VD"}).dated)

    def test_a_verify_pinned_to_the_old_from_date_follows_the_new_one(self):
        # Live 2026-09-22, R4351UM01: recorded with --verify woPlanStartYmd=20260920
        # (that day's own date baked in). A batch run under "yesterday" (20260921)
        # correctly retyped the date fields but kept comparing against the frozen
        # 20260920 forever, refusing the correct new answer.
        r = self.retarget({"from": "20260920", "to": "20260920",
                           "verify": "woPlanStartYmd=20260920"},
                          "20260921", "20260921")
        self.assertEqual(r.verify, "woPlanStartYmd=20260921")
        self.assertIn("verify", r.changed)
        self.assertTrue(r.dated)

    def test_a_verify_pinned_to_a_non_date_value_is_left_alone(self):
        # P3111UM00: --verify plantCode=P701 - not a date, must survive untouched
        # even though it shares the "=" shape with the dangerous case above.
        r = self.retarget({"from": "20260916", "to": "20260916",
                           "verify": "plantCode=P701"}, "20260921", "20260921")
        self.assertIsNone(r.verify)
        self.assertNotIn("verify", r.changed)

    def test_a_bare_column_verify_is_left_alone(self):
        r = self.retarget({"from": "20260920", "to": "20260920", "verify": "lossYmd"},
                          "20260921", "20260921")
        self.assertIsNone(r.verify)

    def test_a_verify_value_that_merely_looks_like_a_date_but_is_not_the_old_one_is_left_alone(self):
        r = self.retarget({"from": "20260920", "to": "20260920",
                           "verify": "someYmd=20260101"}, "20260921", "20260921")
        self.assertIsNone(r.verify)


class BatchPlan(unittest.TestCase):
    """The plan is decided from the saved profiles BEFORE the browser is touched,
    so what cannot run safely is reported up front rather than at 06:30."""

    def plan(self, profiles, codes=None, policy="yesterday", **kw):
        import gmes_batch
        codes = codes or [p["screen"] for p in profiles]
        return gmes_batch.build_plan(codes, policy, kw.pop("export", "both"),
                                     kw.pop("out_dir", None), profiles=profiles,
                                     today=__import__("datetime").date(2026, 9, 20))

    @staticmethod
    def profile(code, learned=True, **values):
        return {"screen": code, "title": "T " + code, "learned": learned, "values": values}

    def test_a_dated_screen_gets_yesterday_and_a_spec_the_engine_accepts(self):
        item = self.plan([self.profile("A1", division="VD", **{"from": "20260901",
                         "to": "20260901", "verify": "workYmd"})],
                         out_dir="OUT")[0]
        self.assertTrue(item.ready)
        self.assertEqual(item.spec, {"screen_code": "A1", "export": "both",
                                     "close_after": True, "date_from": "20260919",
                                     "date_to": "20260919", "out_dir": "OUT"})
        self.assertEqual(item.dates, "20260919")

    def test_a_verify_pinned_to_the_old_date_is_carried_into_the_spec_retargeted(self):
        item = self.plan([self.profile("A1", division="VD", **{"from": "20260901",
                         "to": "20260901", "verify": "workYmd=20260901"})])[0]
        self.assertEqual(item.spec["verify"], "workYmd=20260919")

    def test_the_spec_never_carries_a_division_so_the_profile_replay_decides_it(self):
        item = self.plan([self.profile("A1", division="VD", **{"from": "20260901",
                         "to": "20260901", "verify": "workYmd"})])[0]
        self.assertNotIn("division", item.spec)

    def test_a_screen_not_recorded_at_all_is_blocked(self):
        item = self.plan([], ["GHOST"])[0]
        self.assertFalse(item.ready)
        self.assertIn("not recorded", item.blocked)

    def test_a_shipped_only_profile_is_blocked(self):
        item = self.plan([self.profile("A1", learned=False)])[0]
        self.assertIn("shipped structure", item.blocked)

    def test_a_remembered_date_without_a_verify_column_is_blocked(self):
        item = self.plan([self.profile("A1", **{"from": "20260901", "to": "20260901"})])[0]
        self.assertIn("verify", item.blocked)

    def test_a_screen_with_no_date_runs_on_its_own_dates_and_says_so(self):
        item = self.plan([self.profile("A1", division="VD")])[0]
        self.assertTrue(item.ready)
        self.assertNotIn("date_from", item.spec)
        self.assertTrue(any("no date is remembered" in n for n in item.notes))

    def test_typed_dates_are_retargeted_and_flagged_unverified(self):
        item = self.plan([self.profile("A1", sets={"mskFromDate": "20260901",
                                                   "mskToDate": "20260901"})])[0]
        self.assertEqual(item.spec["sets"], {"mskFromDate": "20260919",
                                             "mskToDate": "20260919"})
        self.assertTrue(any("not verified" in n for n in item.notes))
        self.assertEqual(item.dates, "20260919")

    def test_keep_leaves_the_remembered_dates_to_the_profile(self):
        item = self.plan([self.profile("A1", **{"from": "20260901", "to": "20260901",
                         "verify": "workYmd"})], policy="keep")[0]
        self.assertTrue(item.ready)
        self.assertNotIn("date_from", item.spec)

    def test_one_blocked_screen_does_not_stop_the_others_being_planned(self):
        plan = self.plan([self.profile("A1", learned=False),
                          self.profile("B2", division="VD")])
        self.assertEqual([i.ready for i in plan], [False, True])


class BatchRun(unittest.TestCase):
    """`run_many()` stops at the first failure; a batch must not - one screen
    with no data on a Friday cannot cancel seventeen others - but it must also
    stop when the session cannot be trusted or the failures form a pattern."""

    def setUp(self):
        import gmes_batch
        self.b = gmes_batch
        patcher = patch.object(self.b.gmes_common, "screenshot_on_failure")
        patcher.start()
        self.addCleanup(patcher.stop)

    def item(self, code, blocked=""):
        return self.b.PlanItem(code=code, title="T", blocked=blocked, dates="20260919",
                               spec=None if blocked else {"screen_code": code})

    def run_plan(self, plan, outcomes, healthy=(True, ""), **kw):
        """`outcomes`: code -> dict (returned) or Exception (raised)."""
        ran, recovered = [], []

        def run(ws, log=None, **spec):
            ran.append(spec["screen_code"])
            o = outcomes.get(spec["screen_code"], {"ok": True, "rows": 5, "files": ["f"]})
            if isinstance(o, Exception):
                raise o
            return o

        def recover(ws, code, log):
            recovered.append(code)
            return healthy

        results = self.b.run_batch(None, plan, log=lambda _m: None, run=run,
                                   recover=recover, **kw)
        return results, ran, recovered

    def test_every_planned_screen_is_reported_exactly_once_and_in_order(self):
        plan = [self.item("A"), self.item("B"), self.item("C")]
        results, ran, _ = self.run_plan(plan, {})
        self.assertEqual([r["screen"] for r in results], ["A", "B", "C"])
        self.assertTrue(all(r["status"] == "ok" for r in results))
        self.assertEqual(ran, ["A", "B", "C"])

    def test_a_failed_screen_does_not_cancel_the_rest(self):
        plan = [self.item("A"), self.item("B"), self.item("C")]
        results, ran, recovered = self.run_plan(
            plan, {"B": {"ok": False, "error": "no rows"}})
        self.assertEqual([r["status"] for r in results], ["ok", "failed", "ok"])
        self.assertEqual(results[1]["error"], "no rows")
        self.assertEqual(ran, ["A", "B", "C"])
        self.assertEqual(recovered, ["B"])            # health-checked after a failure only

    def test_an_exception_is_a_failure_of_that_screen_only(self):
        plan = [self.item("A"), self.item("B")]
        results, ran, _ = self.run_plan(plan, {"A": RuntimeError("boom")})
        self.assertEqual([r["status"] for r in results], ["failed", "ok"])
        self.assertEqual(results[0]["error"], "boom")

    def test_a_blocked_screen_is_reported_and_never_run(self):
        plan = [self.item("A", blocked="not recorded"), self.item("B")]
        results, ran, recovered = self.run_plan(plan, {})
        self.assertEqual(results[0]["status"], "blocked")
        self.assertEqual(results[0]["error"], "not recorded")
        self.assertEqual(ran, ["B"])
        self.assertEqual(recovered, [])

    def test_an_unhealthy_session_stops_the_batch_but_every_screen_is_still_listed(self):
        plan = [self.item("A"), self.item("B"), self.item("C", blocked="x"), self.item("D")]
        results, ran, _ = self.run_plan(
            plan, {"A": {"ok": False, "error": "kicked"}},
            healthy=(False, "the session is signed out"))
        self.assertEqual(ran, ["A"])
        self.assertEqual([r["status"] for r in results],
                         ["failed", "not_run", "blocked", "not_run"])
        self.assertIn("signed out", results[1]["error"])
        self.assertEqual(len(results), len(plan))

    def test_three_failures_in_a_row_stop_the_batch(self):
        plan = [self.item(c) for c in "ABCDE"]
        bad = {"ok": False, "error": "x"}
        results, ran, _ = self.run_plan(plan, {"A": bad, "B": bad, "C": bad})
        self.assertEqual(ran, ["A", "B", "C"])
        self.assertEqual([r["status"] for r in results],
                         ["failed"] * 3 + ["not_run"] * 2)
        self.assertIn("in a row", results[3]["error"])

    def test_a_success_resets_the_failure_streak(self):
        plan = [self.item(c) for c in "ABCDE"]
        bad = {"ok": False, "error": "x"}
        results, ran, _ = self.run_plan(plan, {"A": bad, "B": bad, "D": bad, "E": bad})
        self.assertEqual(ran, list("ABCDE"))
        self.assertEqual([r["status"] for r in results],
                         ["failed", "failed", "ok", "failed", "failed"])

    def test_warnings_and_rows_and_files_are_carried_into_the_result(self):
        plan = [self.item("A")]
        results, _, _ = self.run_plan(plan, {"A": {"ok": True, "rows": 58, "files": ["a.csv"],
                                                    "warnings": ["w"]}})
        self.assertEqual((results[0]["rows"], results[0]["files"], results[0]["warnings"]),
                         (58, ["a.csv"], ["w"]))
        self.assertEqual(results[0]["dates"], "20260919")

    def test_the_screen_is_closed_after_each_run(self):
        """A batch of eighteen must not leave eighteen work windows open."""
        item = self.b.build_plan(["A1"], "yesterday", profiles=[
            {"screen": "A1", "learned": True, "values": {"division": "VD"}}])[0]
        self.assertTrue(item.spec["close_after"])


class BatchSavedLists(unittest.TestCase):
    def setUp(self):
        import tempfile
        import gmes_batch
        self.b = gmes_batch
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.d = os.path.join(self._tmp.name, "batches")

    def test_a_saved_list_round_trips_with_its_policy(self):
        self.b.save_batch("morning", ["A", "B"], "-1", "csv", self.d)
        got = self.b.load_batch("morning", self.d)
        self.assertEqual((got["screens"], got["date"], got["export"]),
                         (["A", "B"], "-1", "csv"))
        self.assertEqual(list(self.b.list_batches(self.d)), ["morning"])

    def test_saving_again_replaces_and_leaves_no_partial_file(self):
        self.b.save_batch("m", ["A"], directory=self.d)
        self.b.save_batch("m", ["B", "C"], directory=self.d)
        self.assertEqual(self.b.load_batch("m", self.d)["screens"], ["B", "C"])
        self.assertEqual(os.listdir(self.d), ["m.json"])

    def test_names_that_could_escape_the_folder_are_refused(self):
        for bad in ("", "..", "..\\x", "a/b", "a b", "x" * 41, "-lead"):
            with self.subTest(bad=bad):
                self.assertFalse(self.b.valid_name(bad))
                with self.assertRaises(ValueError):
                    self.b.save_batch(bad, ["A"], directory=self.d)
                with self.assertRaises(ValueError):
                    self.b.load_batch(bad, self.d)
                with self.assertRaises(ValueError):
                    self.b.delete_batch(bad, self.d)

    def test_an_empty_list_or_a_bad_policy_is_refused_at_save_time(self):
        with self.assertRaises(ValueError):
            self.b.save_batch("m", [], directory=self.d)
        with self.assertRaises(ValueError):
            self.b.save_batch("m", ["A"], "someday", directory=self.d)
        with self.assertRaises(ValueError):
            self.b.save_batch("m", ["A"], export="pdf", directory=self.d)
        self.assertFalse(os.path.exists(os.path.join(self.d, "m.json")))

    def test_a_missing_batch_says_so(self):
        with self.assertRaises(ValueError) as cm:
            self.b.load_batch("nope", self.d)
        self.assertIn("nope", str(cm.exception))

    def test_a_corrupt_file_is_skipped_not_fatal(self):
        self.b.save_batch("good", ["A"], directory=self.d)
        with open(os.path.join(self.d, "bad.json"), "w") as fh:
            fh.write("{not json")
        self.assertEqual(list(self.b.list_batches(self.d)), ["good"])

    def test_delete_reports_whether_anything_was_there(self):
        self.b.save_batch("m", ["A"], directory=self.d)
        self.assertTrue(self.b.delete_batch("m", self.d))
        self.assertFalse(self.b.delete_batch("m", self.d))

    def test_no_directory_yet_is_an_empty_list(self):
        self.assertEqual(self.b.list_batches(os.path.join(self.d, "never")), {})


class PcClockAgainstGmes(unittest.TestCase):
    """HISTORY.md Phase 92.6 (Open Item 56): 'yesterday' comes from the PC's
    clock; a wrong clock silently queried the wrong day for every screen."""

    UTC = __import__("datetime").timezone.utc

    def at(self, *parts, tz=None):
        from datetime import datetime as dt
        return dt(*parts, tzinfo=tz or self.UTC)

    def test_clocks_that_agree_within_the_margin_are_fine(self):
        self.assertIsNone(core.clock_problem(self.at(2026, 9, 23, 2, 10),
                                             self.at(2026, 9, 23, 2, 0)))

    def test_a_clock_a_day_out_is_named_with_its_direction(self):
        text = core.clock_problem(self.at(2026, 9, 24, 2, 0), self.at(2026, 9, 23, 2, 0))
        self.assertIn("24 h 0 min ahead of", text)
        text = core.clock_problem(self.at(2026, 9, 23, 1, 0), self.at(2026, 9, 23, 2, 0))
        self.assertIn("1 h 0 min behind", text)

    def test_time_zones_do_not_matter_only_the_instant_does(self):
        from datetime import timedelta, timezone
        cairo = timezone(timedelta(hours=3))
        self.assertIsNone(core.clock_problem(self.at(2026, 9, 23, 5, 0, tz=cairo),
                                             self.at(2026, 9, 23, 2, 0)))

    def test_the_server_date_header_is_read_through_the_page(self):
        reply = {"id": 1, "result": {"result": {"value": "Wed, 23 Sep 2026 02:00:00 GMT"}}}
        with patch.object(core, "send", return_value=reply) as sent:
            when, why = core.server_clock(Mock())
        self.assertIsNone(why)
        self.assertEqual(when, self.at(2026, 9, 23, 2, 0))
        self.assertTrue(sent.call_args.args[2]["awaitPromise"])
        self.assertIn("HEAD", sent.call_args.args[2]["expression"])

    def test_a_missing_or_broken_header_is_a_reason_not_a_crash(self):
        for value, words in (("", "no Date header"), ("ERROR TypeError", "request failed"),
                             ("not a date", "could not be read")):
            with self.subTest(value=value), \
                 patch.object(core, "send", return_value={"result": {"result": {"value": value}}}):
                when, why = core.server_clock(Mock())
            self.assertIsNone(when)
            self.assertIn(words, why)
        with patch.object(core, "send", side_effect=TimeoutError("slow")):
            self.assertIn("could not be asked", core.server_clock(Mock())[1])

    def test_check_pc_clock_turns_an_unreadable_server_into_a_note_only(self):
        problem, note = core.check_pc_clock(None, read_server=lambda ws: (None, "no header"))
        self.assertIsNone(problem)
        self.assertIn("could not be checked", note)
        problem, note = core.check_pc_clock(
            None, now=self.at(2026, 9, 25, 2, 0),
            read_server=lambda ws: (self.at(2026, 9, 23, 2, 0), None))
        self.assertIn("ahead of", problem)
        self.assertIsNone(note)

    def test_only_policies_taken_from_the_pc_clock_are_checked(self):
        import gmes_batch as b
        for policy in ("yesterday", "today", "-3", "", None):
            self.assertTrue(b.policy_uses_pc_clock(policy), policy)
        for policy in ("keep", "20260920", "20260901:20260910"):
            self.assertFalse(b.policy_uses_pc_clock(policy), policy)

    def test_a_wrong_clock_stops_the_batch_before_any_screen_runs(self):
        import gmes_batch as b
        ready = Mock(code="A", ready=True, dates="d", blocked=None)
        blocked = Mock(code="B", ready=False, dates="", blocked="not recorded")
        results, note = b.clock_gate(None, [ready, blocked], "yesterday", log=lambda *_: None,
                                     check=lambda ws: ("clock is 1 day ahead", None))
        self.assertEqual([r["status"] for r in results], ["not_run", "blocked"])
        self.assertIn("clock is 1 day ahead", results[0]["error"])
        self.assertEqual(b.clock_gate(None, [ready], "yesterday", log=lambda *_: None,
                                      check=lambda ws: (None, "unchecked")),
                         (None, "unchecked"))
        never = Mock(side_effect=AssertionError("a fixed date must not be checked"))
        self.assertEqual(b.clock_gate(None, [ready], "20260920", check=never), (None, None))


class MorningSummary(unittest.TestCase):
    """HISTORY.md Phase 92.7: one page to open the morning after a night."""

    def setUp(self):
        import tempfile
        import gmes_batch as b
        self.b = b
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.d = self._tmp.name
        self.shot = os.path.join(self.d, "gmes_B_x.png")
        with open(self.shot, "wb") as fh:
            fh.write(b"\x89PNG fake")
        self.results = [
            b._result("A", "ok", rows=12, files=["a.csv"], dates="20260922",
                      warnings=["9 of 21 rows have no planYmd value"]),
            b._result("B", "failed", error="<script>x</script> tokenId=LEAKED",
                      screenshot=self.shot),
            b._result("C", "not_run", error="not run: stopped")]
        self.meta = {"started": "2026-09-23 02:00:00", "policy": "yesterday",
                     "batch": "Nightly", "warnings": ["clock could not be checked"]}

    def test_the_page_says_what_happened_and_why(self):
        page = self.b.render_summary(self.results, self.meta)
        self.assertIn("2 of 3 screens need attention", page)
        self.assertIn("9 of 21 rows", page)
        self.assertIn("clock could not be checked", page)
        self.assertIn("data:image/png;base64,", page)            # the screenshot is inside the page
        self.assertLess(page.index("not run: stopped"), page.index("Delivered"))

    def test_nothing_in_the_page_is_raw_markup_or_a_secret(self):
        page = self.b.render_summary(self.results, self.meta)
        self.assertNotIn("<script>", page)
        self.assertIn("&lt;script&gt;", page)
        self.assertNotIn("LEAKED", page)

    def test_an_all_good_night_says_so(self):
        page = self.b.render_summary(self.results[:1], {})
        self.assertIn("Everything was delivered.", page)

    def test_every_report_writes_the_page_and_refreshes_latest(self):
        jp, tp = self.b.write_report(self.results, self.meta, self.d)
        dated = tp[:-4] + "_summary.html"
        self.assertTrue(os.path.isfile(dated))
        with open(os.path.join(self.d, self.b.SUMMARY_LATEST), encoding="utf-8") as fh:
            self.assertIn("need attention", fh.read())
        last = self.b.last_summary(self.d)
        self.assertEqual(last["counts"]["ok"], 1)
        self.assertEqual(last["total"], 3)
        self.assertEqual(last["started"], "2026-09-23 02:00:00")

    def test_a_summary_that_cannot_be_written_never_loses_the_report(self):
        with patch.object(self.b, "render_summary", side_effect=RuntimeError("boom")), \
             patch("builtins.print"):
            jp, tp = self.b.write_report(self.results, self.meta, self.d)
        self.assertTrue(os.path.isfile(jp) and os.path.isfile(tp))

    def test_no_reports_yet_is_none(self):
        self.assertIsNone(self.b.last_summary(os.path.join(self.d, "missing")))

    def test_a_failed_screen_keeps_its_screenshot_for_the_page(self):
        plan = [Mock(code="X", ready=True, dates="d", title="", spec={})]
        with patch.object(self.b, "_screenshot", return_value="shot.png"):
            out = self.b.run_batch(None, plan, log=lambda *_: None,
                                   run=Mock(side_effect=RuntimeError("no")),
                                   recover=lambda ws, code, log: (True, ""))
        self.assertEqual(out[0]["screenshot"], "shot.png")


class ArabicSummaryPage(unittest.TestCase):
    """HISTORY.md Phase 94.5: the summary page in Arabic, right to left."""

    def test_arabic_is_right_to_left_with_arabic_words_and_explanations(self):
        import gmes_batch as b
        results = [b._result("A1", "ok", rows=5, dates="d"),
                   b._result("B2", "failed", error="the remembered screen shape changed")]
        page = b.render_summary(results, {"language": "ar", "started": "x"})
        self.assertIn("dir='rtl'", page)
        self.assertIn("1 من 2 تقارير محتاجة انتباهك", page)
        self.assertIn("غيّر شكل الشاشة", page)
        self.assertIn("the remembered screen shape changed", page)   # the detail stays as written

    def test_english_stays_the_default_and_unknown_languages_fall_back(self):
        import gmes_batch as b
        for meta in ({}, {"language": "xx"}):
            page = b.render_summary([b._result("A1", "ok", rows=1)], meta)
            self.assertIn("dir='ltr'", page)
            self.assertIn("Everything was delivered.", page)


class BatchReports(unittest.TestCase):
    def test_a_report_is_written_even_when_everything_failed(self):
        import tempfile
        import gmes_batch as b
        results = [b._result("A", "failed", error="boom", dates="20260919"),
                   b._result("B", "not_run", error="stopped")]
        with tempfile.TemporaryDirectory() as d:
            jp, tp = b.write_report(results, {"started": "now", "screens": ["A", "B"],
                                              "policy": "yesterday"}, d)
            with open(jp, encoding="utf-8") as fh:
                data = json.load(fh)
            with open(tp, encoding="utf-8") as fh:
                text = fh.read()
        self.assertEqual(data["summary"], {"ok": 0, "failed": 1, "blocked": 0, "not_run": 1})
        self.assertIn("boom", text)
        self.assertIn("stopped", text)

    def test_the_summary_counts_add_up_to_the_plan(self):
        import io
        import gmes_batch as b
        results = [b._result("A", "ok", rows=3, files=["x.csv"], dates="d"),
                   b._result("B", "failed", error="e"),
                   b._result("C", "blocked", error="why"),
                   b._result("D", "not_run", error="s")]
        lines = []
        counts = b.print_summary(results, log=lines.append)
        self.assertEqual(counts, {"ok": 1, "failed": 1, "blocked": 1, "not_run": 1})
        self.assertEqual(sum(counts.values()), len(results))
        self.assertTrue(any("1 succeeded, 1 failed, 1 skipped, 1 not run" in l for l in lines))


class BatchCommandLine(unittest.TestCase):
    """The pieces of the CLI that decide what runs."""

    def setUp(self):
        import gmes_batch
        self.b = gmes_batch
        self.profiles = [{"screen": c, "title": c, "learned": True,
                          "values": {"division": "VD"}} for c in ("A1", "B2", "C3")]
        p = patch.object(self.b.gmes_profile, "known", return_value=self.profiles)
        p.start()
        self.addCleanup(p.stop)
        p = patch.object(self.b, "list_batches", return_value={})
        p.start()
        self.addCleanup(p.stop)

    def test_run_requires_an_explicit_selection(self):
        """`run` with nothing typed must NOT mean "everything"."""
        with patch("builtins.print"):
            self.assertEqual(self.b.main(["run", "--dry-run"]), self.b.EXIT_USAGE)

    def test_a_dry_run_touches_no_browser_lock_or_login(self):
        with patch("builtins.print"), \
                patch.object(self.b.core, "acquire_run_lock") as lock, \
                patch.object(self.b.core, "sign_in") as sign_in:
            rc = self.b.main(["run", "all", "--dry-run"])
        self.assertEqual(rc, self.b.EXIT_OK)
        lock.assert_not_called()
        sign_in.assert_not_called()

    def test_a_busy_browser_is_exit_3_and_nothing_is_attempted(self):
        with patch("builtins.print"), patch.object(self.b.gmes_log, "start"), \
                patch.object(self.b.gmes_log, "finish"), \
                patch.object(self.b.core, "acquire_run_lock",
                             side_effect=self.b.core.RunLocked("busy")), \
                patch.object(self.b.core, "sign_in") as sign_in:
            rc = self.b.main(["run", "all"])
        self.assertEqual(rc, self.b.EXIT_BUSY)
        sign_in.assert_not_called()

    def test_a_failed_sign_in_is_exit_4_and_the_lock_is_released(self):
        with patch("builtins.print"), patch.object(self.b.gmes_log, "start"), \
                patch.object(self.b.gmes_log, "finish"), \
                patch.object(self.b.core, "acquire_run_lock"), \
                patch.object(self.b.core, "release_run_lock") as release, \
                patch.object(self.b, "_stop_browser"), \
                patch.object(self.b.core, "sign_in", return_value=False):
            rc = self.b.main(["run", "all"])
        self.assertEqual(rc, self.b.EXIT_NO_SIGN_IN)
        release.assert_called_once()

    def test_a_failed_sign_in_still_stops_the_browser_it_started(self):
        """Live-caught (HISTORY.md Phase 83): a scheduled run whose SSO sign-in
        failed returned early and left nine automation-browser processes
        running. sign_in() starts a browser whether or not it succeeds."""
        with patch("builtins.print"), patch.object(self.b.gmes_log, "start"), \
                patch.object(self.b.gmes_log, "finish"), \
                patch.object(self.b.core, "acquire_run_lock"), \
                patch.object(self.b.core, "release_run_lock"), \
                patch.object(self.b, "_stop_browser") as stop, \
                patch.object(self.b.core, "sign_in", return_value=False):
            self.b.main(["run", "all"])
            stop.assert_called_once_with(False)
            stop.reset_mock()
            self.b.main(["run", "all", "--keep-open"])
            stop.assert_called_once_with(True)

    def test_lock_and_browser_are_released_even_when_connecting_raises(self):
        with patch("builtins.print"), patch.object(self.b.gmes_log, "start"), \
                patch.object(self.b.gmes_log, "finish"), \
                patch.object(self.b.core, "acquire_run_lock"), \
                patch.object(self.b.core, "release_run_lock") as release, \
                patch.object(self.b.core, "sign_in", return_value=True), \
                patch.object(self.b, "_stop_browser") as stop, \
                patch.object(self.b.core, "connect", side_effect=RuntimeError("no browser")):
            with self.assertRaises(RuntimeError):
                self.b.main(["run", "all"])
        release.assert_called_once()
        stop.assert_called_once()

    def test_a_batch_where_nothing_can_run_never_opens_a_browser(self):
        self.profiles[:] = [{"screen": "A1", "title": "", "learned": False, "values": {}}]
        with patch("builtins.print"), patch.object(self.b.gmes_log, "start"), \
                patch.object(self.b.gmes_log, "finish"), \
                patch.object(self.b, "write_report", return_value=("j", "t")), \
                patch.object(self.b.core, "acquire_run_lock") as lock:
            rc = self.b.main(["run", "all"])
        self.assertEqual(rc, self.b.EXIT_FAILED)
        lock.assert_not_called()

    def test_exit_code_is_1_when_any_screen_failed_and_0_when_all_passed(self):
        def go(results):
            ws = Mock()
            with patch("builtins.print"), patch.object(self.b.gmes_log, "start"), \
                    patch.object(self.b.gmes_log, "finish"), \
                    patch.object(self.b, "write_report", return_value=("j", "t")), \
                    patch.object(self.b.core, "acquire_run_lock"), \
                    patch.object(self.b.core, "release_run_lock"), \
                    patch.object(self.b.core, "sign_in", return_value=True), \
                    patch.object(self.b.core, "connect", return_value=ws), \
                    patch.object(self.b, "_stop_browser"), \
                    patch.object(self.b, "run_batch", return_value=results):
                return self.b.main(["run", "all"])
        ok = [self.b._result(c, "ok") for c in ("A1", "B2", "C3")]
        self.assertEqual(go(ok), self.b.EXIT_OK)
        self.assertEqual(go(ok[:2] + [self.b._result("C3", "failed")]), self.b.EXIT_FAILED)
        self.assertEqual(go(ok[:2] + [self.b._result("C3", "not_run")]), self.b.EXIT_FAILED)

    def test_the_browser_is_closed_afterwards_unless_asked_not_to(self):
        ok = [self.b._result(c, "ok") for c in ("A1", "B2", "C3")]
        for flag, expect_keep in (([], False), (["--keep-open"], True)):
            with self.subTest(flag=flag):
                ws = Mock()
                with patch("builtins.print"), patch.object(self.b.gmes_log, "start"), \
                        patch.object(self.b.gmes_log, "finish"), \
                        patch.object(self.b, "write_report", return_value=("j", "t")), \
                        patch.object(self.b.core, "acquire_run_lock"), \
                        patch.object(self.b.core, "release_run_lock"), \
                        patch.object(self.b.core, "sign_in", return_value=True), \
                        patch.object(self.b.core, "connect", return_value=ws), \
                        patch.object(self.b, "_stop_browser") as stop, \
                        patch.object(self.b, "run_batch", return_value=ok):
                    self.b.main(["run", "all"] + flag)
                stop.assert_called_once_with(expect_keep)
                ws.close.assert_called_once()

    def test_files_go_to_a_fresh_folder_per_run_unless_flat(self):
        seen = []
        real = self.b.build_plan

        def spy(codes, policy, export, out_dir, *a, **k):
            seen.append(out_dir)
            return real(codes, policy, export, out_dir, *a, **k)

        with patch("builtins.print"), patch.object(self.b, "build_plan", side_effect=spy):
            self.b.main(["run", "all", "--dry-run"])
            self.b.main(["run", "all", "--dry-run", "--flat"])
        self.assertIn("batch_", os.path.basename(seen[0]))
        self.assertIsNone(seen[1])

    def test_scheduling_a_selection_replaces_a_saved_batch_of_that_name(self):
        """Regression: `schedule morning 1,3` used to be MERGED with the saved
        @morning, quietly running more than was typed."""
        import gmes_schedule
        saved = {"morning": {"screens": ["A1", "B2", "C3"], "date": "yesterday",
                             "export": "both"}}
        with patch("builtins.print"), \
                patch.object(self.b, "list_batches", return_value=saved), \
                patch.object(self.b, "save_batch") as save, \
                patch.object(self.b, "load_batch", return_value={"screens": ["A1"]}), \
                patch.object(gmes_schedule, "create"):
            rc = self.b.main(["schedule", "morning", "1", "--at", "06:30", "--daily"])
        self.assertEqual(rc, self.b.EXIT_OK)
        self.assertEqual(save.call_args[0][1], ["A1"])

    def test_scheduling_only_a_new_date_keeps_the_saved_screens(self):
        import gmes_schedule
        saved = {"morning": {"screens": ["A1", "B2"], "date": "yesterday", "export": "both"}}
        with patch("builtins.print"), \
                patch.object(self.b, "list_batches", return_value=saved), \
                patch.object(self.b, "save_batch") as save, \
                patch.object(self.b, "load_batch", return_value=saved["morning"]), \
                patch.object(gmes_schedule, "create"):
            self.b.main(["schedule", "morning", "--date", "today", "--at", "06:30", "--daily"])
        self.assertEqual(save.call_args[0][1:3], (["A1", "B2"], "today"))

    def test_scheduling_a_batch_that_does_not_exist_is_refused_not_created_empty(self):
        import gmes_schedule
        with patch("builtins.print"), \
                patch.object(self.b, "load_batch",
                             side_effect=ValueError("there is no saved batch")), \
                patch.object(gmes_schedule, "create") as create:
            rc = self.b.main(["schedule", "ghost", "--at", "06:30", "--daily"])
        self.assertEqual(rc, self.b.EXIT_USAGE)
        create.assert_not_called()


class ScheduleParsing(unittest.TestCase):
    def when(self, at, **kw):
        import gmes_schedule
        return gmes_schedule.parse_when(at, **kw)

    def test_daily_weekdays_days_and_once(self):
        self.assertEqual((self.when("6:30", daily=True).at, self.when("6:30", daily=True).kind),
                         ("06:30", "daily"))
        wk = self.when("07:00", weekdays=True)
        self.assertEqual((wk.kind, wk.days), ("weekly", ["mon", "tue", "wed", "thu", "fri"]))
        d = self.when("07:00", days="Fri, mon Wed")
        self.assertEqual(d.days, ["mon", "wed", "fri"])          # canonical order, deduped
        o = self.when("23:59", once="2026-09-25")
        self.assertEqual((o.kind, o.on), ("once", "2026-09-25"))

    def test_a_bad_time_is_refused(self):
        for bad in ("", "24:00", "6.30", "0630", "12:60", "noon", "6:5"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.when(bad, daily=True)

    def test_exactly_one_recurrence_must_be_named(self):
        with self.assertRaises(ValueError):
            self.when("06:30")
        with self.assertRaises(ValueError):
            self.when("06:30", daily=True, weekdays=True)

    def test_bad_days_and_dates_are_refused(self):
        with self.assertRaises(ValueError):
            self.when("06:30", days="mon,someday")
        for bad in ("2026-13-01", "2026-02-30", "25/09/2026", "tomorrow"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.when("06:30", once=bad)

    def test_descriptions_are_readable(self):
        import gmes_schedule as s
        self.assertEqual(s.describe_when(self.when("06:30", daily=True)), "every day at 06:30")
        self.assertEqual(s.describe_when(self.when("06:30", weekdays=True)),
                         "every weekday at 06:30")
        self.assertEqual(s.describe_when(self.when("06:30", days="mon,fri")),
                         "every Mon, Fri at 06:30")
        self.assertEqual(s.describe_when(self.when("06:30", once="2026-09-25")),
                         "once, on 2026-09-25 at 06:30")


class ScheduleTask(unittest.TestCase):
    """Task Scheduler is a real system; every test replaces the one function
    that touches it. What matters here is WHAT would be registered."""

    def setUp(self):
        import tempfile
        import gmes_schedule
        self.s = gmes_schedule
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        p = patch.object(self.s, "SCHEDULE_DIR", self._tmp.name)
        p.start()
        self.addCleanup(p.stop)

    def test_task_names_are_prefixed_and_validated(self):
        self.assertEqual(self.s.task_name("morning"), "GMES_Batch_morning")
        for bad in ("", "a b", "..\\x", "x'; calc; '"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.s.task_name(bad)

    def test_the_launcher_runs_the_saved_batch_unattended_and_logs(self):
        text = self.s.launcher_text("morning", python="C:\\Py\\python.exe", repo="D:\\Repo")
        # The project folder is derived from the launcher's own location, never written in.
        self.assertIn('cd /d "%~dp0.."', text)
        self.assertNotIn("D:\\Repo", text)
        # A redirected stream can still use the Windows ANSI code page after
        # `chcp 65001`; force Python's own log encoding so a non-Latin working
        # directory cannot make an otherwise successful run exit 1.
        self.assertIn('set "PYTHONIOENCODING=utf-8"', text)
        # -u: an unbuffered log, so a run that hangs still leaves evidence.
        self.assertIn('"C:\\Py\\python.exe" -u gmes_batch.py run --batch morning --unattended', text)
        self.assertIn('>> "logs\\scheduled_morning.log" 2>&1', text)
        # the batch's own exit code reaches Task Scheduler - the exact line sequence, because
        # the retry branch also contains an xit /b %code% and would satisfy a bare search
        self.assertIn("if %code%==4 goto retry\r\nexit /b %code%", text)

    def test_the_hidden_wrapper_runs_the_launcher_and_returns_its_exit_code(self):
        text = self.s.wrapper_text("D:\\Repo\\schedules\\run_morning.cmd")
        self.assertIn('CreateObject("WScript.Shell")', text)
        # windowStyle=0 (hidden), waitOnReturn=True - both required: hidden
        # suppresses the console; waiting is what lets the exit code and the
        # "still running" state reach Task Scheduler at all.
        self.assertIn('"""D:\\Repo\\schedules\\run_morning.cmd""", 0, True', text)
        self.assertIn("WScript.Quit exitCode", text)

    def test_the_wrapper_file_is_utf16_with_a_bom(self):
        # Windows Script Host reliably auto-detects UTF-16LE+BOM regardless
        # of what the embedded path contains; UTF-8 support for .vbs files is
        # version-dependent - exactly the class of silent failure Phase
        # 84.11 already found once for the .cmd launcher's own encoding.
        path = self.s.write_hidden_wrapper("morning", "D:\\Repo\\schedules\\run_morning.cmd")
        with open(path, "rb") as fh:
            raw = fh.read()
        self.assertTrue(raw.startswith(b"\xff\xfe"))
        self.assertIn("run_morning.cmd".encode("utf-16-le"), raw)

    def test_a_non_ascii_cmd_path_survives_in_the_wrapper(self):
        cmd_path = "D:\\\u0623\u062d\u0645\u062f\\schedules\\run_morning.cmd"
        path = self.s.write_hidden_wrapper("morning", cmd_path)
        with open(path, encoding="utf-16-le") as fh:
            text = fh.read()
        self.assertIn(cmd_path, text)

    def test_the_task_settings_protect_an_unattended_run(self):
        script = self.s.create_script("morning", self.s.parse_when("06:30", daily=True),
                                      "D:\\Repo\\schedules\\run_morning.vbs", "D:\\Repo")
        self.assertIn("-StartWhenAvailable", script)         # a PC asleep at 06:30 still runs it
        self.assertIn("-MultipleInstances IgnoreNew", script)  # never two runs on one browser
        self.assertIn("-ExecutionTimeLimit", script)         # a hung run cannot hold the browser forever
        self.assertIn("New-ScheduledTaskTrigger -Daily -At '06:30'", script)
        self.assertNotIn("-RunLevel Highest", script)        # never elevated
        self.assertNotIn("-Password", script)                # never stores a credential

    def test_weekly_and_once_triggers(self):
        w = self.s._trigger_script(self.s.parse_when("07:00", days="mon,fri"))
        self.assertIn("-Weekly -DaysOfWeek Monday,Friday", w)
        o = self.s._trigger_script(self.s.parse_when("07:00", once="2026-09-25"))
        self.assertIn("-Once", o)
        self.assertIn("2026-09-25 07:00", o)

    def test_quotes_in_paths_cannot_break_out_of_the_powershell_string(self):
        self.assertEqual(self.s._q("D:\\O'Brien\\x"), "'D:\\O''Brien\\x'")

    def test_create_writes_the_launcher_then_registers_the_task(self):
        calls = []

        def fake(script, timeout=90):
            calls.append(script)
            return 0, "", ""

        with patch.object(self.s, "_run_powershell", side_effect=fake):
            name = self.s.create("morning", self.s.parse_when("06:30", daily=True),
                                 python="py", repo="D:\\Repo")
        self.assertEqual(name, "GMES_Batch_morning")
        self.assertTrue(os.path.exists(os.path.join(self._tmp.name, "run_morning.cmd")))
        self.assertTrue(os.path.exists(os.path.join(self._tmp.name, "run_morning.vbs")))
        self.assertIn("Register-ScheduledTask", calls[0])
        self.assertIn("'GMES_Batch_morning'", calls[0])
        # The Action launches the HIDDEN WRAPPER via wscript.exe, not the
        # .cmd directly - Task Scheduler's own -Hidden setting only hides the
        # task from the Task Scheduler UI, never the console a task opens.
        self.assertIn("-Execute 'wscript.exe'", calls[0])
        self.assertIn("run_morning.vbs", calls[0])
        self.assertNotIn("-Execute 'D:\\\\Repo\\\\schedules\\\\run_morning.cmd'", calls[0])

    def test_a_refusal_from_task_scheduler_is_raised_with_its_own_words(self):
        with patch.object(self.s, "_run_powershell",
                          return_value=(1, "", "Access is denied")):
            with self.assertRaises(self.s.ScheduleError) as cm:
                self.s.create("morning", self.s.parse_when("06:30", daily=True),
                              python="py", repo="D:\\Repo")
        self.assertIn("Access is denied", str(cm.exception))

    def test_pause_and_resume_keep_the_task_and_only_flip_it(self):
        # HISTORY.md Phase 94.4 - never Unregister, never touch the launcher.
        calls = []
        with patch.object(self.s, "_run_powershell",
                          side_effect=lambda script, **k: calls.append(script) or (0, "done", "")):
            self.s.set_enabled("morning", False)
            self.s.set_enabled("morning", True)
        self.assertIn("Disable-ScheduledTask -TaskName 'GMES_Batch_morning'", calls[0])
        self.assertIn("Enable-ScheduledTask -TaskName 'GMES_Batch_morning'", calls[1])
        for script in calls:
            self.assertNotIn("Unregister", script)
        with patch.object(self.s, "_run_powershell", return_value=(1, "", "no such task")):
            with self.assertRaises(self.s.ScheduleError):
                self.s.set_enabled("morning", False)

    def test_delete_removes_the_task_and_its_launcher_but_not_the_batch(self):
        cmd_path = os.path.join(self._tmp.name, "run_morning.cmd")
        vbs_path = os.path.join(self._tmp.name, "run_morning.vbs")
        for path in (cmd_path, vbs_path):
            with open(path, "w") as fh:
                fh.write("x")
        with patch.object(self.s, "_run_powershell", return_value=(0, "removed", "")):
            self.assertTrue(self.s.delete("morning"))
        self.assertFalse(os.path.exists(cmd_path))
        self.assertFalse(os.path.exists(vbs_path))
        with patch.object(self.s, "_run_powershell", return_value=(0, "absent", "")):
            self.assertFalse(self.s.delete("morning"))

    def test_list_output_is_normalised_whatever_powershell_returns(self):
        one = ('{"name":"GMES_Batch_a","state":"Ready","next":"2026-09-21T06:30:00",'
               '"last":"2026-09-20T06:30:00","result":0,"trigger":"Daily"}')
        two = f"[{one},{one.replace('_a', '_b').replace(':0,', ':1,')}]"
        for text, n in (("", 0), ("[]", 0), (one, 1), (two, 2)):
            with self.subTest(n=n):
                self.assertEqual(len(self.s.parse_list(text)), n)
        rows = self.s.parse_list(two)
        self.assertEqual((rows[0]["batch"], rows[0]["last_ok"]), ("a", True))
        self.assertEqual(rows[1]["last_ok"], False)

    def test_a_task_that_never_ran_is_not_reported_as_failed(self):
        row = self.s.parse_list('{"name":"GMES_Batch_a","state":"Ready","next":"x",'
                                '"last":"","result":267011,"trigger":"Daily"}')[0]
        self.assertIsNone(row["last_ok"])

    def test_foreign_tasks_are_ignored(self):
        self.assertEqual(self.s.parse_list('{"name":"SomethingElse","state":"Ready"}'), [])

    def test_unreadable_output_is_an_error_not_an_empty_list(self):
        with self.assertRaises(self.s.ScheduleError):
            self.s.parse_list("<html>")

    def test_the_gitignore_keeps_local_launchers_out_of_the_repo(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, ".gitignore"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("/schedules/", text)


class TypeTextRetries(unittest.TestCase):
    """HISTORY.md Phase 83, live-caught: the same typing into R3220UM00's
    masked date field worked twice and then left '9196-0_-__' in a scheduled
    run. The read-back caught it (so nothing wrong was queried) - but a nightly
    batch died on a hiccup a second try clears. Retrying is safe because every
    attempt is READ BACK: what is accepted is what the control shows."""

    def run_typing(self, readbacks, **kw):
        reads = iter(readbacks)
        sleeps, clicks = [], []

        def fake_evaluate(ws, expr):
            if expr == "FIND":
                return {"found": True, "x": 10, "y": 20}
            return {"value": next(reads)}

        with patch.object(core.gmes_common, "js_find_by_id", return_value="FIND"), \
                patch.object(core, "evaluate", side_effect=fake_evaluate), \
                patch.object(core, "click_element_by_rect",
                             side_effect=lambda *a: clicks.append(a)), \
                patch.object(core, "dispatch_key_combo"), \
                patch.object(core, "send"), \
                patch("time.sleep", side_effect=sleeps.append):
            try:
                result = core.type_text(object(), "win.form.mskFromDate", "20260919", **kw)
                error = None
            except RuntimeError as e:
                result, error = None, str(e)
        return result, error, len(clicks), sleeps

    def test_a_clean_first_attempt_is_typed_once(self):
        result, error, clicks, _ = self.run_typing(["2026-09-19"])
        self.assertEqual((result, error, clicks), ("2026-09-19", None, 1))

    def test_a_scrambled_first_attempt_is_retyped_and_then_accepted(self):
        result, error, clicks, sleeps = self.run_typing(["9196-0_-__", "2026-09-19"])
        self.assertEqual((result, error, clicks), ("2026-09-19", None, 2))
        # the retry waits longer for the editor than the first attempt did
        self.assertIn(0.3, sleeps)
        self.assertIn(0.8, sleeps)
        self.assertNotIn(1.5, sleeps)

    def test_it_gives_up_after_the_last_attempt_and_says_what_it_saw(self):
        result, error, clicks, _ = self.run_typing(["9196-0_-__", "2026-0_-__", "2026-09-2_"])
        self.assertIsNone(result)
        self.assertEqual(clicks, 3)
        self.assertIn("did not take", error)
        self.assertIn("3 attempts", error)
        self.assertIn("9196-0_-__", error)
        self.assertIn("'2026-09-2_'", error)            # the LAST reading is the headline

    def test_a_wrong_value_is_never_accepted_just_because_keys_were_sent(self):
        """A fully-formed but different date must not pass either."""
        result, error, *_ = self.run_typing(["2026-09-20"] * 3)
        self.assertIsNone(result)
        self.assertIn("did not take", error)

    def test_without_verification_it_types_once_and_reads_nothing_back(self):
        result, error, clicks, _ = self.run_typing([], verify=False)
        self.assertEqual((result, error, clicks), ("20260919", None, 1))

    def test_a_control_that_is_not_on_screen_fails_at_once_not_after_retries(self):
        with patch.object(core.gmes_common, "js_find_by_id", return_value="FIND"), \
                patch.object(core, "evaluate", return_value={"found": False, "reason": "gone"}), \
                patch("time.sleep"):
            with self.assertRaises(RuntimeError) as cm:
                core.type_text(object(), "win.form.mskFromDate", "20260919")
        self.assertIn("not on screen", str(cm.exception))


class ReplaceWhenFree(unittest.TestCase):
    """HISTORY.md Phase 83, live-caught: a scheduled export whose data had been
    read correctly failed on the final rename with WinError 32 - something (the
    DRM agent that encrypts every .xlsx, antivirus, or the browser) still had
    the fresh download open."""

    @staticmethod
    def locked(winerror):
        e = PermissionError(13, "locked")
        e.winerror = winerror
        return e

    def run_replace(self, failures, timeout=90):
        """`failures`: exceptions os.replace raises, in order, before succeeding."""
        pending = list(failures)
        now = [0.0]
        sleeps = []

        def fake_replace(src, dst):
            if pending:
                raise pending.pop(0)

        def fake_sleep(s):
            sleeps.append(s)
            now[0] += s

        with patch("os.replace", side_effect=fake_replace):
            core.replace_when_free("a", "b", timeout=timeout, poll=0.5,
                                   sleep=fake_sleep, clock=lambda: now[0])
        return sleeps

    def test_a_free_file_is_renamed_at_once(self):
        self.assertEqual(self.run_replace([]), [])

    def test_a_file_in_use_is_waited_for_then_renamed(self):
        self.assertEqual(self.run_replace([self.locked(32)] * 3), [0.5] * 3)

    def test_access_denied_is_waited_for_too(self):
        self.assertEqual(len(self.run_replace([self.locked(5)])), 1)

    def test_a_file_that_never_frees_raises_after_the_timeout_not_forever(self):
        with self.assertRaises(PermissionError):
            self.run_replace([self.locked(32)] * 1000, timeout=5)

    def test_any_other_failure_is_raised_immediately(self):
        for err in (FileNotFoundError(2, "gone"), PermissionError(13, "no winerror"),
                    self.locked(3), OSError(28, "disk full")):
            with self.subTest(err=repr(err)), self.assertRaises(OSError):
                self.run_replace([err])

    def test_it_really_renames_a_file(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            a, b = os.path.join(d, "a.txt"), os.path.join(d, "b.txt")
            with open(a, "w") as fh:
                fh.write("x")
            core.replace_when_free(a, b)
            self.assertTrue(os.path.exists(b))
            self.assertFalse(os.path.exists(a))

    def test_both_rename_points_in_the_export_path_use_it(self):
        """The bare os.replace that failed live must not come back at either
        place a downloaded workbook is renamed."""
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                               "gmes_core.py"), encoding="utf-8") as fh:
            source = fh.read()
        self.assertIn("replace_when_free(candidate, final)", source)
        self.assertIn("replace_when_free(downloaded, final)", source)
        self.assertNotIn("os.replace(candidate, final)", source)
        self.assertNotIn("os.replace(downloaded, final)", source)


class IntentAcceptsAControlThatShowsTheDate(unittest.TestCase):
    """HISTORY.md Phase 83.3, live-caught on B3320UM00 (SMD Equipment Operation
    Efficiency): the Period boxes DISPLAY the date while the bound dataset row is
    empty, so the drift check read '' and refused to query a screen that was
    correct - the run failed with "Period now reads '', not the '20260919'".
    The tolerance is one narrow case; every other shape must still refuse."""

    WRITTEN = flt(column="startDt", control="mskCalendarFrom", dataset="dsFilterDVO")

    def check(self, value, shown, wanted="20260919", as_date=True, notes=None):
        fresh = {"filters": [dict(flt(column="startDt", control="mskCalendarFrom",
                                      dataset="dsFilterDVO", value=value), shown=shown)],
                 "unbound": []}
        pair = [(self.WRITTEN, wanted)]
        if as_date:
            return core.intent_mismatches(fresh, [], date_fields=pair, notes=notes)
        return core.intent_mismatches(fresh, [], applied_filters=pair, notes=notes)

    def test_an_empty_dataset_with_the_date_on_screen_is_accepted_and_said_so(self):
        notes = []
        self.assertEqual(self.check("", "2026-09-19", notes=notes), [])
        self.assertEqual(len(notes), 1)
        self.assertIn("2026-09-19", notes[0])
        self.assertIn("still checked", notes[0])

    def test_the_same_situation_without_a_notes_list_still_passes(self):
        self.assertEqual(self.check("", "2026-09-19"), [])

    def test_an_empty_dataset_and_an_empty_control_is_still_a_mismatch(self):
        self.assertEqual(len(self.check("", "")), 1)

    def test_a_control_showing_a_different_date_is_still_a_mismatch(self):
        problems = self.check("", "2026-09-18")
        self.assertEqual(len(problems), 1)
        self.assertIn("20260919", problems[0])

    def test_a_dataset_holding_a_different_date_is_a_mismatch_even_if_the_control_agrees(self):
        """Only an EMPTY dataset is tolerated - a wrong value is real drift."""
        self.assertEqual(len(self.check("20260101", "2026-09-19")), 1)

    def test_a_non_date_filter_is_never_given_the_tolerance(self):
        """An applied --set filter that reads empty stays a mismatch, whatever
        the control displays - only dates get the exception."""
        self.assertEqual(len(self.check("", "20260919", as_date=False)), 1)

    def test_a_field_with_no_shown_value_recorded_behaves_as_before(self):
        fresh = {"filters": [flt(column="startDt", control="mskCalendarFrom",
                                 dataset="dsFilterDVO", value="")], "unbound": []}
        self.assertEqual(len(core.intent_mismatches(
            fresh, [], date_fields=[(self.WRITTEN, "20260919")])), 1)

    def test_a_partial_date_on_screen_is_not_accepted(self):
        self.assertEqual(len(self.check("", "2026-09")), 1)

    def test_discovery_records_what_each_bound_control_displays(self):
        """The tolerance is only as good as the value it reads: discovery must
        put the control's own text next to the dataset value."""
        self.assertRegex(core.JS_DISCOVER, r"shown:\s*visible\s*\?\s*shownValue\(el\)")

    def test_run_screen_passes_its_warnings_so_the_acceptance_is_never_silent(self):
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                               "gmes_core.py"), encoding="utf-8") as fh:
            source = fh.read()
        self.assertRegex(source, r"notes=screen\.warnings")


class VerifyDatesInsideJson(unittest.TestCase):
    """HISTORY.md Phase 83.3: B3320UM00's result has no date column - `baseDate`
    is empty on every row and the date is a KEY inside `jsonObj`
    ({"20260919": {...}, "Total": {...}}). --verify had nothing to compare, so the
    screen could not be recorded safely. A column of date-keyed JSON is verified
    by the dates it names, under the same rules as a plain date column."""

    ONE_DAY = '{"20260919":{"FAC_OPER_EFF":72.3},"Total":{"FAC_OPER_EFF":72.3}}'

    def rows(self, values, column="jsonObj"):
        return {"found": True, "columns": [column, "divCode"],
                "rows": [{column: v, "divCode": "X"} for v in values]}

    def verify_single(self, values, expected="20260919"):
        with patch.object(core, "read_rows", return_value=self.rows(values)):
            return core.verify_rows(None, "F", "dsData", "jsonObj", expected)

    def verify_range(self, values, lo="20260918", hi="20260920"):
        with patch.object(core, "read_rows", return_value=self.rows(values)):
            return core.verify_date_range(None, "F", "dsData", "jsonObj", lo, hi)

    # -- the helper --------------------------------------------------------

    def test_only_real_dates_count_as_keys(self):
        self.assertEqual(core.date_keys_of_json(self.ONE_DAY), {"20260919"})
        self.assertEqual(core.date_keys_of_json('{"20261399":{},"2026":{},"Total":{}}'), set())

    def test_keys_that_only_look_like_dates_do_not_count(self):
        """2026091 parses as a date if the 8-digit shape is not required; a
        dashed date is not the stored YYYYMMDD form either."""
        for key in ("2026091", "2026-09-19", "20260919x", "x20260919"):
            with self.subTest(key=key):
                self.assertEqual(core.date_keys_of_json('{"' + key + '":{}}'), set())

    def test_something_that_is_not_a_json_object_is_none(self):
        for text in ("", "   ", "20260919", "[1,2]", "{not json", None):
            with self.subTest(text=text):
                self.assertIsNone(core.date_keys_of_json(text))

    def test_a_plain_date_column_is_not_treated_as_json(self):
        self.assertEqual(core.json_date_keys(["20260919", "20260919"]), (None, None))
        self.assertEqual(core.json_date_keys([]), (None, None))

    # -- a single day -------------------------------------------------------

    def test_the_requested_day_alone_passes(self):
        self.assertEqual(self.verify_single([self.ONE_DAY, self.ONE_DAY, ""]),
                         (["20260919"], None))

    def test_a_different_day_is_refused_even_though_the_total_key_is_present(self):
        seen, problem = self.verify_single(['{"20260918":{},"Total":{}}'])
        self.assertEqual(seen, ["20260918"])
        self.assertIn("not exactly", problem)
        self.assertIn("20260918", problem)                # says WHICH day it found

    def test_an_extra_day_is_refused_for_a_single_day_request(self):
        seen, problem = self.verify_single(['{"20260919":{},"20260920":{},"Total":{}}'])
        self.assertIn("not exactly", problem)
        self.assertEqual(seen, ["20260919", "20260920"])

    def test_a_row_with_a_wrong_day_among_correct_ones_is_refused(self):
        _, problem = self.verify_single([self.ONE_DAY, '{"20260901":{}}'])
        self.assertIsNotNone(problem)

    def test_json_with_no_date_key_is_refused_not_passed(self):
        seen, problem = self.verify_single(['{"Total":{}}'])
        self.assertEqual(seen, [])
        self.assertIn("holds a date key", problem)

    def test_a_broken_value_among_json_ones_is_refused(self):
        _, problem = self.verify_single([self.ONE_DAY, '{"2026'])
        self.assertIn("not readable JSON", problem)

    def test_the_expected_date_is_compared_by_digits(self):
        self.assertEqual(self.verify_single([self.ONE_DAY], "2026-09-19")[1], None)

    # -- a range ---------------------------------------------------------------

    def test_days_inside_the_range_pass(self):
        seen, problem = self.verify_range(['{"20260918":{},"20260919":{},"Total":{}}'])
        self.assertEqual((seen, problem), (["20260918", "20260919"], None))

    def test_a_day_outside_the_range_is_refused(self):
        seen, problem = self.verify_range(['{"20260919":{},"20260921":{}}'])
        self.assertIn("outside", problem)
        self.assertIn("20260921", problem)

    def test_a_range_whose_rows_name_no_date_is_refused(self):
        self.assertIsNotNone(self.verify_range(['{"Total":{}}'])[1])

    # -- unchanged behaviour ---------------------------------------------------

    def test_a_plain_date_column_still_verifies_the_old_way(self):
        rows = {"found": True, "columns": ["creYmd"],
                "rows": [{"creYmd": "20260919"}, {"creYmd": "20260919"}]}
        with patch.object(core, "read_rows", return_value=rows):
            self.assertEqual(core.verify_rows(None, "F", "d", "creYmd", "20260919"),
                             (["20260919"], None))
            wrong = {"found": True, "columns": ["creYmd"], "rows": [{"creYmd": "20260918"}]}
        with patch.object(core, "read_rows", return_value=wrong):
            self.assertIsNotNone(core.verify_rows(None, "F", "d", "creYmd", "20260919")[1])

    def test_an_empty_column_still_has_nothing_to_verify(self):
        """B3320UM00's `baseDate` was empty on every row - that must stay a
        refusal, not become a pass."""
        rows = {"found": True, "columns": ["baseDate"],
                "rows": [{"baseDate": ""}, {"baseDate": ""}]}
        with patch.object(core, "read_rows", return_value=rows):
            self.assertIn("no values", core.verify_rows(None, "F", "d", "baseDate", "20260919")[1])

    # -- offered while recording --------------------------------------------------

    def test_a_json_date_column_is_offered_as_a_verify_column(self):
        screen = core.Screen(None, "B3320UM00", {"menuId": "M", "winId": "W"}, info={})
        result = {"found": True, "columns": ["divCode", "baseDate", "jsonObj"],
                  "rows": [{"divCode": "Total", "baseDate": "", "jsonObj": self.ONE_DAY},
                           {"divCode": "CE", "baseDate": "", "jsonObj": self.ONE_DAY}]}
        with patch.object(core.Screen, "rows", return_value=result):
            self.assertEqual(screen.date_like_columns({"dataset": "dsData"}), ["jsonObj"])


class BlankRowsAreCountedNotHidden(unittest.TestCase):
    """HISTORY.md Phase 92.4 (Open Item 86): rows whose checked column is empty
    were skipped silently - 900 blank-date rows beside 10 right-date rows read
    as verified. Still not a refusal (filler rows are real); now always said."""

    def _rows(self, values):
        return {"found": True, "columns": ["planYmd"],
                "rows": [{"planYmd": v} for v in values]}

    def test_no_blanks_no_note(self):
        self.assertIsNone(core.blank_rows_note("planYmd", 0, 10))

    def test_a_few_blanks_are_counted_and_a_majority_is_flagged(self):
        few = core.blank_rows_note("planYmd", 2, 10)
        self.assertIn("2 of 10", few)
        self.assertNotIn("MOST", few)
        self.assertIn("MOST of the result is unverified",
                      core.blank_rows_note("planYmd", 900, 910))

    def test_both_verifiers_pass_but_report_the_blank_rows(self):
        rows = self._rows(["20260920"] * 10 + [""] * 900)
        for call in (lambda n: core.verify_rows(None, "F", "d", "planYmd", "20260920", notes=n),
                     lambda n: core.verify_date_range(None, "F", "d", "planYmd",
                                                      "20260920", "20260920", notes=n)):
            notes = []
            with patch.object(core, "read_rows", return_value=rows):
                problem = call(notes)[1]
            self.assertIsNone(problem)
            self.assertEqual(len(notes), 1)
            self.assertIn("900 of 910", notes[0])

    def test_the_screen_wrapper_puts_the_note_in_the_runs_warnings(self):
        screen = core.Screen.__new__(core.Screen)
        screen.ws, screen.warnings = None, []
        grid = {"dataset": "d", "path": None}
        with patch.object(core.Screen, "form_code", staticmethod(lambda g: "F")), \
             patch.object(core, "read_rows", return_value=self._rows(["20260920", ""])):
            screen.verify_column(grid, "planYmd", "20260920")
        self.assertTrue(any("1 of 2" in w for w in screen.warnings))


class TimestampColumnDefeatsExactVerify(unittest.TestCase):
    """HISTORY.md Phase 84.24 / Open Item 66: Q3341UM00's `outStopRegDt` stores
    a full YYYYMMDDHHMMSS timestamp. `--verify outStopRegDt=20260920` refused a
    genuinely same-day row ('...20260920083443..., not exactly the requested
    20260920') because both verify functions compared it as an exact value
    instead of checking whether ITS DATE falls where asked."""

    def test_the_date_half_of_a_real_timestamp_is_extracted(self):
        self.assertEqual(core.timestamp_date_part("20260920083443"), "20260920")
        self.assertEqual(core.timestamp_date_part("2026-09-20 08:34:43"), "20260920")

    def test_a_plain_eight_digit_value_is_not_this_shape(self):
        # The caller's ordinary comparison already handles these - this
        # helper must not also claim them, or two different code paths
        # could disagree about the same value.
        self.assertIsNone(core.timestamp_date_part("20260920"))

    def test_fourteen_digits_with_an_impossible_date_or_time_is_rejected(self):
        self.assertIsNone(core.timestamp_date_part("20261399083443"))   # month 13
        self.assertIsNone(core.timestamp_date_part("20260920256000"))   # hour 25
        self.assertIsNone(core.timestamp_date_part("12345678901234"))   # not a date at all

    def _rows(self, column, values):
        return {"found": True, "columns": [column],
                "rows": [{column: v} for v in values]}

    def test_verify_rows_accepts_a_same_day_timestamp(self):
        rows = self._rows("outStopRegDt", ["20260920083443", "20260920235959"])
        with patch.object(core, "read_rows", return_value=rows):
            seen, problem = core.verify_rows(None, "F", "d", "outStopRegDt", "20260920")
        self.assertIsNone(problem)
        self.assertEqual(seen, sorted(["20260920083443", "20260920235959"]))

    def test_verify_rows_still_refuses_a_different_day_timestamp(self):
        rows = self._rows("outStopRegDt", ["20260921083443"])
        with patch.object(core, "read_rows", return_value=rows):
            problem = core.verify_rows(None, "F", "d", "outStopRegDt", "20260920")[1]
        self.assertIn("not exactly the requested 20260920", problem)

    def test_a_non_date_expected_value_never_takes_the_timestamp_shortcut(self):
        # P3111UM00's real --verify plantCode=P701: a 14-digit lot number that
        # happened to share no relationship with "P701" must not be waved
        # through just because it is the right length.
        rows = self._rows("lotNo", ["12345678901234"])
        with patch.object(core, "read_rows", return_value=rows):
            problem = core.verify_rows(None, "F", "d", "lotNo", "P701")[1]
        self.assertIsNotNone(problem)

    def test_verify_date_range_accepts_a_timestamp_inside_the_range(self):
        rows = self._rows("outStopRegDt", ["20260920083443", "20260921000000"])
        with patch.object(core, "read_rows", return_value=rows):
            seen, problem = core.verify_date_range(
                None, "F", "d", "outStopRegDt", "20260919", "20260921")
        self.assertIsNone(problem)
        self.assertEqual(seen, sorted(["20260920083443", "20260921000000"]))

    def test_verify_date_range_still_refuses_a_timestamp_outside_the_range(self):
        rows = self._rows("outStopRegDt", ["20260925083443"])
        with patch.object(core, "read_rows", return_value=rows):
            problem = core.verify_date_range(
                None, "F", "d", "outStopRegDt", "20260919", "20260921")[1]
        self.assertIn("outside", problem)


class ReplayKeepsTheRememberedGridAfterOptions(unittest.TestCase):
    """HISTORY.md Phase 83.5, live-caught replaying B3320UM00 right after
    recording it: the run found the remembered grid ("results: dsData"), applied
    the remembered option, then refused with "more than one plausible result
    grid". After options rebuild the panel the grid is re-resolved - and that
    call passed only the `--grid` argument, which is empty on a replay, so the
    remembered choice was thrown away and the two comparable grids on that
    screen were ambiguous again. A screen taught with `--grid` could be
    recorded but never replayed (nor run in a batch)."""

    class Screen(AutoReplayFromSavedProfile.FakeScreen):
        def __init__(self, info):
            super().__init__(info)
            self.grid_preferences = []

        def grid(self, _preferred=None):
            self.grid_preferences.append(_preferred)
            return super().grid(_preferred)

        def options(self):
            return [{"name": "btnDate", "state": "selected"}]

        def set_option(self, wanted):
            return {"outcome": "Daily [date] -> selected", "key": "date",
                    "name": "btnDate", "path": "", "label": "Daily",
                    "matched_by": "component name"}

    def replay(self):
        import gmes_profile
        screen = self.Screen({
            "filters": [flt(column="fromYmd", control="mskFrom")],
            "unbound": [], "grids": [grid("grdMain", "dsMain", 100)]})
        fp = gmes_profile.fingerprint(screen.info)
        profile = {"fingerprint": fp, "opening_fingerprint": fp,
                   "grid": {"dataset": "dsMain"},
                   "options": [{"key": "date", "name": "btnDate", "path": "", "label": "Daily"}],
                   "values": {"division": "SEEG-P", "from": "20260901", "to": "20260901",
                              "verify": "fromYmd", "sets": {}}}
        with patch.object(gmes_profile, "load", return_value=profile), \
             patch.object(gmes_profile, "save", return_value="x.json"), \
             patch.object(core, "open_screen", return_value=screen), \
             patch.object(core, "org_selection", return_value={"found": True, "org": "SEEG-P"}):
            result = core.run_screen(None, "B3320UM00", export="none", log=lambda _m: None)
        return screen, result

    def test_every_grid_resolution_of_a_replay_names_the_remembered_dataset(self):
        screen, result = self.replay()
        self.assertTrue(result["ok"], result.get("error"))
        self.assertGreaterEqual(len(screen.grid_preferences), 2)
        self.assertEqual(screen.grid_preferences, ["dsMain"] * len(screen.grid_preferences),
                         "a resolution that names nothing lets the size heuristic "
                         "(or an ambiguity refusal) replace what was recorded")

    def test_a_grid_named_on_the_command_line_still_wins(self):
        import gmes_profile
        screen = self.Screen({
            "filters": [flt(column="fromYmd", control="mskFrom")],
            "unbound": [], "grids": [grid("grdMain", "dsMain", 100)]})
        fp = gmes_profile.fingerprint(screen.info)
        profile = {"fingerprint": fp, "opening_fingerprint": fp,
                   "grid": {"dataset": "dsMain"},
                   "options": [{"key": "date", "name": "btnDate", "path": "", "label": "Daily"}],
                   "values": {"division": "SEEG-P", "from": "20260901", "to": "20260901",
                              "verify": "fromYmd", "sets": {}}}
        with patch.object(gmes_profile, "load", return_value=profile), \
             patch.object(gmes_profile, "save", return_value="x.json"), \
             patch.object(core, "open_screen", return_value=screen), \
             patch.object(core, "org_selection", return_value={"found": True, "org": "SEEG-P"}):
            core.run_screen(None, "B3320UM00", export="none", grid_name="grdMain",
                            log=lambda _m: None)
        self.assertEqual(screen.grid_preferences[:2], ["grdMain", "grdMain"])


class ProfileFilesThatAreNotProfiles(unittest.TestCase):
    """HISTORY.md Phase 84.7, found by a hostile-input probe: ONE malformed file in
    screens/ (valid JSON that is a list or a number) made `known()` raise, which
    broke the list, the Replay menu and every batch; a profile saved by Notepad (a
    byte-order mark) silently vanished from the list; and odd value types crashed the
    plan for every screen."""

    def setUp(self):
        import tempfile
        import gmes_profile
        self.gp = gmes_profile
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.screens = os.path.join(self._tmp.name, "screens")
        self.shipped = os.path.join(self._tmp.name, "known")
        os.makedirs(self.screens)
        os.makedirs(self.shipped)
        for target, name in ((gmes_profile, "SCREENS_DIR"), (gmes_profile, "SHIPPED_DIR")):
            p = patch.object(target, name, self.screens if name == "SCREENS_DIR" else self.shipped)
            p.start()
            self.addCleanup(p.stop)
        self.gp.UNREADABLE.clear()

    def write(self, name, content, binary=False):
        with open(os.path.join(self.screens, name), "wb" if binary else "w",
                  **({} if binary else {"encoding": "utf-8"})) as fh:
            fh.write(content)

    def good(self, code, **extra):
        d = {"screen": code, "title": "T", "learned": "2026-09-20 10:00:00",
             "values": {"division": "VD"}}
        d.update(extra)
        return json.dumps(d)

    def test_one_json_list_or_number_does_not_break_the_list(self):
        self.write("G1111UM00.json", self.good("G1111UM00"))
        self.write("L1111UM03.json", "[1,2,3]")
        self.write("N1111UM04.json", "42")
        self.write("Z1111UM05.json", "null")
        self.assertEqual([p["screen"] for p in self.gp.known()], ["G1111UM00"])

    def test_the_skipped_files_are_reported_with_a_reason(self):
        self.write("L1111UM03.json", "[1,2,3]")
        self.write("E1111UM02.json", "")
        self.gp.known()
        named = {os.path.basename(p): why for p, why in self.gp.unreadable()}
        self.assertIn("L1111UM03.json", named)
        self.assertIn("list", named["L1111UM03.json"])
        self.assertIn("E1111UM02.json", named)

    def test_a_notepad_byte_order_mark_is_accepted(self):
        self.write("B1111UM01.json", "\ufeff" + self.good("B1111UM01"))
        self.assertEqual([p["screen"] for p in self.gp.known()], ["B1111UM01"])
        self.assertEqual(self.gp.unreadable(), [])

    def test_a_file_in_another_encoding_is_reported_not_silently_lost(self):
        self.write("C1111UM06.json", self.good("C1111UM06").replace("\"T\"", "\"caf\u00e9\"").encode("cp1252"),
                   binary=True)
        self.assertEqual(self.gp.known(), [])
        self.assertTrue(any("C1111UM06" in p for p, _ in self.gp.unreadable()))

    def test_a_missing_file_is_not_an_error_to_report(self):
        self.gp.known()
        self.assertEqual(self.gp.unreadable(), [])

    def test_a_file_that_becomes_readable_stops_being_reported(self):
        self.write("L1111UM03.json", "[1]")
        self.gp.known()
        self.assertTrue(self.gp.unreadable())
        self.write("L1111UM03.json", self.good("L1111UM03"))
        self.gp.known()
        self.assertEqual(self.gp.unreadable(), [])

    def test_the_file_name_decides_which_screen_a_profile_is(self):
        self.write("Y1111UM14.json", json.dumps({"title": "x", "learned": "x", "values": {}}))
        self.write("W1111UM15.json", self.good("SOMETHINGELSE"))
        codes = sorted(p["screen"] for p in self.gp.known())
        self.assertEqual(codes, ["W1111UM15", "Y1111UM14"])

    def test_wrong_typed_fields_are_dropped_not_left_to_crash_a_reader(self):
        self.write("S1111UM10.json", self.good("S1111UM10", values={"sets": "oops"}))
        self.write("V1111UM11.json", self.good("V1111UM11", values=[1, 2], options="x", grid=[1]))
        s, v = self.gp.load("S1111UM10"), self.gp.load("V1111UM11")
        self.assertEqual(self.gp.last_values(s).get("sets"), {})
        self.assertEqual(self.gp.last_values(v), {})
        self.assertNotIn("options", v)
        self.assertNotIn("grid", v)

    def test_non_string_title_and_learned_become_strings(self):
        self.write("D1111UM13.json", self.good("D1111UM13", learned=12345, title=None))
        p = self.gp.load("D1111UM13")
        self.assertEqual((p["learned"], p["title"]), ("12345", ""))
        self.assertEqual([x["screen"] for x in self.gp.known()], ["D1111UM13"])

    def test_last_values_survives_a_non_dict_profile_and_a_bad_proved(self):
        self.assertEqual(self.gp.last_values(None), {})
        self.assertEqual(self.gp.last_values([1, 2]), {})
        self.assertEqual(self.gp.last_values({"proved": "text"}), {})
        self.assertEqual(self.gp.last_values({"proved": {"command": 5}}), {})

    def test_a_valid_profile_is_unchanged(self):
        self.write("G1111UM00.json", self.good("G1111UM00", options=[{"key": "k"}],
                                                grid={"dataset": "ds"}))
        p = self.gp.load("G1111UM00")
        self.assertEqual(p["options"], [{"key": "k"}])
        self.assertEqual(p["grid"], {"dataset": "ds"})


class PlanSurvivesOneBadProfile(unittest.TestCase):
    def setUp(self):
        import gmes_batch
        self.b = gmes_batch

    def plan(self, profiles, policy="yesterday"):
        codes = [p.get("screen", "?") for p in profiles if isinstance(p, dict)]
        return self.b.build_plan(codes, policy, profiles=profiles,
                                 today=__import__("datetime").date(2026, 9, 20))

    def test_a_profile_that_blows_up_blocks_only_itself(self):
        good = {"screen": "G1", "title": "Good", "learned": "x", "values": {"division": "VD"}}
        bad = {"screen": "B2", "title": "Bad", "learned": "x", "values": {"division": "VD"}}
        with patch.object(self.b, "_plan_one",
                          side_effect=lambda code, *a, **k: (_ for _ in ()).throw(TypeError("boom"))
                          if code == "B2" else self.b.PlanItem(code=code, title="Good")):
            plan = self.plan([good, bad])
        self.assertEqual([i.ready for i in plan], [True, False])
        self.assertIn("could not be used", plan[1].blocked)
        self.assertIn("TypeError", plan[1].blocked)

    def test_hostile_value_shapes_never_raise(self):
        shapes = [
            {"screen": "A1", "title": "t", "learned": "x", "values": {"sets": "oops"}},
            {"screen": "A2", "title": "t", "learned": "x", "values": [1, 2]},
            {"screen": "A3", "title": None, "learned": "x", "values": {"from": 20260901, "to": 20260901, "verify": "d"}},
            {"screen": "A4", "title": "t", "learned": "x", "values": {"division": "VD", "sets": {"mskFromDate": None, "mskToDate": 5}}},
            {"screen": "A5", "title": 7, "learned": "x", "values": {"from": "not-a-date", "to": "", "verify": "d"}},
        ]
        plan = self.plan(shapes)
        self.assertEqual(len(plan), 5)

    def test_a_non_dict_entry_in_the_profile_list_is_ignored(self):
        good = {"screen": "G1", "title": "Good", "learned": "x", "values": {"division": "VD"}}
        plan = self.b.build_plan(["G1", "X"], "yesterday", profiles=[good, None, "str", 5],
                                 today=__import__("datetime").date(2026, 9, 20))
        self.assertEqual([i.ready for i in plan], [True, False])

    def test_describe_profile_never_raises(self):
        for profile in ({"values": {"sets": "oops"}}, {"values": [1]}, None, {"values": {"from": 5, "to": 6}}):
            with self.subTest(profile=profile):
                self.assertIsInstance(self.b.describe_profile(profile), str)

    def test_describe_profile_absorbs_an_unforeseen_failure_in_reading_values(self):
        """last_values() is defensive now, but the list must still never die on the ONE
        profile nobody thought of."""
        with patch.object(self.b.gmes_profile, "last_values", side_effect=RuntimeError("new shape")):
            self.assertIn("could not be read", self.b.describe_profile({"screen": "X1"}))

    def test_a_multi_day_recording_says_it_will_run_one_day(self):
        prof = {"screen": "R1", "title": "t", "learned": "x",
                "values": {"division": "VD", "from": "20260901", "to": "20260907", "verify": "d"}}
        item = self.plan([prof])[0]
        self.assertTrue(item.ready)
        self.assertEqual(item.dates, "20260919")
        self.assertTrue(any("ONE day" in n and "20260901..20260907" in n for n in item.notes))

    def test_an_explicit_range_policy_keeps_the_period_and_says_nothing(self):
        prof = {"screen": "R1", "title": "t", "learned": "x",
                "values": {"division": "VD", "from": "20260901", "to": "20260907", "verify": "d"}}
        item = self.plan([prof], policy="20260901:20260907")[0]
        self.assertEqual(item.dates, "20260901..20260907")
        self.assertFalse(any("ONE day" in n for n in item.notes))

    def test_a_single_day_recording_gets_no_range_note(self):
        prof = {"screen": "R1", "title": "t", "learned": "x",
                "values": {"division": "VD", "from": "20260901", "to": "20260901", "verify": "d"}}
        self.assertEqual(self.plan([prof])[0].notes, [])


class DigitsOfAnyScript(unittest.TestCase):
    """HISTORY.md Phase 84.8: an Arabic keyboard types Arabic-Indic digits. The
    selection grammar accepted `٣` while every date path refused `٢٠٢٦٠٩١٩` with
    "is not a real calendar date"."""

    ARABIC = "\u0662\u0660\u0662\u0666\u0660\u0669\u0661\u0669"      # 20260919
    FULLWIDTH = "\uff12\uff10\uff12\uff16\uff10\uff19\uff11\uff19"     # 20260919
    PERSIAN = "\u06f2\u06f0\u06f2\u06f6\u06f0\u06f9\u06f1\u06f9"       # 20260919

    def test_ascii_digits_converts_every_script_and_leaves_the_rest(self):
        for text in (self.ARABIC, self.FULLWIDTH, self.PERSIAN):
            self.assertEqual(core.ascii_digits(text), "20260919")
        self.assertEqual(core.ascii_digits("ab-12_x"), "ab-12_x")
        self.assertEqual(core.ascii_digits(None), "")
        self.assertEqual(core.ascii_digits("\u00b2"), "\u00b2")     # superscript two is not a decimal digit

    def test_a_date_in_any_script_is_normalised(self):
        for text in (self.ARABIC, self.FULLWIDTH, self.PERSIAN):
            self.assertEqual(core.normalise_date(text), "20260919")
        self.assertEqual(core.normalise_date("\u0662\u0660\u0662\u0666-\u0660\u0669-\u0661\u0669"), "20260919")

    def test_digits_only_and_values_match_agree_across_scripts(self):
        self.assertEqual(core.digits_only(self.ARABIC), "20260919")
        self.assertTrue(core.values_match("20260919", self.ARABIC))
        self.assertFalse(core.values_match("20260918", self.ARABIC))

    def test_a_real_bad_date_is_still_refused(self):
        for bad in ("\u0662\u0660\u0662\u0666\u0661\u0663\u0660\u0661", "20260229", "19-09-2026", "2026"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                core.normalise_date(bad)

    def test_the_date_policy_accepts_them_too(self):
        import gmes_batch
        today = __import__("datetime").date(2026, 9, 20)
        self.assertEqual(gmes_batch.resolve_dates(self.ARABIC, today), ("20260919", "20260919"))
        self.assertEqual(gmes_batch.resolve_dates("-\u0663", today), ("20260917", "20260917"))
        self.assertEqual(gmes_batch.resolve_dates(f"{self.ARABIC}:{self.ARABIC}", today),
                         ("20260919", "20260919"))

    def test_the_selection_grammar_and_the_date_policy_now_agree(self):
        import gmes_batch
        self.assertEqual(gmes_batch.parse_selection("\u0663 1-\u0662", ["A", "B", "C"]), ["C", "A", "B"])

    def test_an_absurd_days_back_is_a_message_not_a_traceback(self):
        import gmes_batch
        today = __import__("datetime").date(2026, 9, 20)
        for policy in ("-99999999999", "-3661", "-" + "9" * 50):
            with self.subTest(policy=policy), self.assertRaises(ValueError) as cm:
                gmes_batch.resolve_dates(policy, today)
            self.assertIn("typo", str(cm.exception))
        self.assertEqual(gmes_batch.resolve_dates("-3660", today)[0], "20160912")   # the boundary itself is allowed

    def test_nothing_escapes_as_an_overflow(self):
        import gmes_batch
        from datetime import date
        with self.assertRaises(ValueError):
            gmes_batch.resolve_dates("yesterday", date.min)


class BatchNamesAreCaseInsensitiveOnWindows(unittest.TestCase):
    def setUp(self):
        import tempfile
        import gmes_batch
        self.b = gmes_batch
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.d = os.path.join(self._tmp.name, "batches")

    def test_a_differently_cased_name_is_refused_and_the_first_is_untouched(self):
        self.b.save_batch("Morning", ["A"], directory=self.d)
        with self.assertRaises(ValueError) as cm:
            self.b.save_batch("morning", ["B", "C"], directory=self.d)
        self.assertIn("'Morning'", str(cm.exception))
        self.assertEqual(self.b.load_batch("Morning", self.d)["screens"], ["A"])

    def test_the_same_spelling_still_replaces(self):
        self.b.save_batch("Morning", ["A"], directory=self.d)
        self.b.save_batch("Morning", ["B"], directory=self.d)
        self.assertEqual(self.b.load_batch("Morning", self.d)["screens"], ["B"])

    def test_different_names_are_unaffected(self):
        self.b.save_batch("am", ["A"], directory=self.d)
        self.b.save_batch("pm", ["B"], directory=self.d)
        self.assertEqual(sorted(self.b.list_batches(self.d)), ["am", "pm"])


class FileNamesAreBoundedAndClean(unittest.TestCase):
    """HISTORY.md Phase 84.9: `safe_name` returned a 300-character title unchanged
    (a full path over 260 characters fails once long paths are off, AFTER the query
    ran) and let control characters through (a NUL makes open() raise)."""

    def test_a_long_title_is_capped(self):
        self.assertEqual(len(core.safe_name("T" * 300)), core.SAFE_NAME_MAX)

    def test_the_worst_case_export_path_fits_when_the_repo_path_is_reasonable(self):
        repo = "C:\\Users\\firstname.lastname\\OneDrive - Company\\Documents\\GitHub\\opening-nerp-tcode"
        stamp = "_20260920_150133_079855_38876718"
        path = (repo + "\\Data Hub Folder\\GMES\\batch_20260920_150037\\"
                + core.safe_name("T" * 300) + stamp + "_data.csv")
        self.assertLess(len(path), 260)

    def test_control_characters_are_removed(self):
        for text in ("a\nb\tc\x00d", "\x07bell", "line1\r\nline2"):
            name = core.safe_name(text)
            self.assertFalse(any(ord(c) < 32 or ord(c) == 127 for c in name), repr(name))

    def test_trimming_never_leaves_a_trailing_dot_or_space(self):
        self.assertEqual(core.safe_name("a" * 79 + ". . ."), "a" * 79)

    def test_a_title_that_is_only_control_characters_still_gets_a_name(self):
        self.assertEqual(core.safe_name("\x00\x01\x02"), "report")

    def test_short_and_unicode_titles_are_unchanged(self):
        self.assertEqual(core.safe_name("SMD Equipment Operation Efficiency"), "SMD Equipment Operation Efficiency")
        self.assertEqual(core.safe_name("\uc0dd\uc0b0 \uacc4\ud68d"), "\uc0dd\uc0b0 \uacc4\ud68d")

    def test_the_reserved_name_guard_still_works_after_the_cap(self):
        self.assertEqual(core.safe_name("NUL"), "_NUL")


class UnreadableFilesAreVisibleInTheCli(unittest.TestCase):
    def test_list_plan_and_run_all_warn_about_skipped_files(self):
        import gmes_batch
        said = []
        with patch.object(gmes_batch.gmes_profile, "unreadable",
                          return_value=[("D:\\x\\screens\\L1111UM03.json", "holds a JSON list, not a profile")]):
            gmes_batch.warn_unreadable(log=said.append)
        text = "\n".join(said)
        self.assertIn("L1111UM03.json", text)
        self.assertIn("skipped", text)

    def test_nothing_is_printed_when_every_file_was_readable(self):
        import gmes_batch
        said = []
        with patch.object(gmes_batch.gmes_profile, "unreadable", return_value=[]):
            gmes_batch.warn_unreadable(log=said.append)
        self.assertEqual(said, [])

    def test_the_three_commands_call_it(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "gmes_batch.py"), encoding="utf-8") as fh:
            src = fh.read()
        self.assertGreaterEqual(src.count("warn_unreadable()"), 3)


@unittest.skipUnless(os.name == "nt", "the launcher is a Windows .cmd file")
class LauncherRunsUnderRealCmd(unittest.TestCase):
    """HISTORY.md Phase 84.11. Proved with real cmd.exe, the real interpreter and a
    stub `gmes_batch.py`: the old launcher wrote the project path into an ASCII file
    with errors="replace", so an Arabic/Korean install path became `????`, `cd`
    failed, no log was written and the task exited 1 - a scheduled night that
    silently did nothing."""

    STUB = ('import os, sys\n'
            'here = os.path.dirname(os.path.abspath(__file__))\n'
            'seq_path = os.path.join(here, "seq.txt")\n'
            'seq = [l.strip() for l in open(seq_path) if l.strip()] if os.path.exists(seq_path) else ["0"]\n'
            'code = int(seq[0]) if seq else 0\n'
            'open(seq_path, "w").write("\\n".join(seq[1:]))\n'
            'print("CWD=" + os.getcwd())\n'
            'print("ARGS=" + " ".join(sys.argv[1:]))\n'
            'sys.exit(code)\n')

    def make(self, folder, sequence=("0",), with_batch=True, retry_wait=1, batch="x"):
        import gmes_schedule
        import tempfile
        td = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, td, True)
        repo = os.path.join(td, folder, "repo")
        sched = os.path.join(repo, "schedules")
        os.makedirs(sched)
        if with_batch:
            with open(os.path.join(repo, "gmes_batch.py"), "w") as fh:
                fh.write(self.STUB)
        with open(os.path.join(repo, "seq.txt"), "w") as fh:
            fh.write("\n".join(sequence))
        text = gmes_schedule.launcher_text(batch, python=sys.executable, repo=repo,
                                           retry_wait=retry_wait, retries=2)
        launcher = os.path.join(sched, f"run_{batch}.cmd")
        with open(launcher, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        return launcher, repo

    def run_launcher(self, launcher):
        # cwd is System32 on purpose: that is where a scheduled task starts.
        # Started from code page 437 - a typical corporate PC - NOT the tester's own
        # console: this machine's console is UTF-8 already, which would hide a launcher
        # that forgot its own chcp 65001 (the mutant survived until this was forced).
        r = subprocess.run(f'cmd.exe /c "chcp 437 >nul & "{launcher}""', capture_output=True,
                           cwd=os.environ.get("SystemRoot", "C:\\Windows"), timeout=120)
        return r.returncode

    def log(self, repo, batch="x"):
        path = os.path.join(repo, "logs", f"scheduled_{batch}.log")
        if not os.path.exists(path):
            return ""
        with open(path, "rb") as fh:
            return fh.read().decode("utf-8", "replace")

    def test_an_arabic_install_path_works(self):
        launcher, repo = self.make("\u0623\u062d\u0645\u062f")
        self.assertEqual(self.run_launcher(launcher), 0)
        self.assertIn("CWD=" + repo, self.log(repo))
        self.assertIn("ARGS=run --batch x --unattended", self.log(repo))      # the saved batch, unattended

    def test_a_korean_install_path_works(self):
        launcher, repo = self.make("\uc0dd\uc0b0")
        self.assertEqual(self.run_launcher(launcher), 0)
        self.assertIn("CWD=" + repo, self.log(repo))

    def test_a_path_with_spaces_parentheses_and_a_percent_sign_works(self):
        # all three at once: each is special to cmd in a different way
        launcher, repo = self.make("100%done (x86) and more")
        self.assertEqual(self.run_launcher(launcher), 0)
        self.assertIn("CWD=" + repo, self.log(repo))

    def test_the_exit_code_of_the_batch_reaches_the_task_and_is_not_retried(self):
        launcher, repo = self.make("plain", sequence=["1"])
        self.assertEqual(self.run_launcher(launcher), 1)
        self.assertEqual(self.log(repo).count("CWD="), 1)               # 1 = some screens failed: not retried

    def test_a_busy_browser_is_retried_and_the_second_try_can_succeed(self):
        launcher, repo = self.make("plain", sequence=["3", "0"])
        self.assertEqual(self.run_launcher(launcher), 0)
        text = self.log(repo)
        self.assertEqual(text.count("CWD="), 2)
        self.assertIn("retry 1 of 2", text)

    def test_a_failed_sign_in_is_retried_twice_then_reported(self):
        launcher, repo = self.make("plain", sequence=["4", "4", "4", "4"])
        self.assertEqual(self.run_launcher(launcher), 4)
        text = self.log(repo)
        self.assertEqual(text.count("CWD="), 3)                         # one try and two retries
        self.assertIn("retry 2 of 2", text)

    def test_a_missing_project_exits_9_and_says_so_in_the_log(self):
        launcher, repo = self.make("plain", with_batch=False)
        self.assertEqual(self.run_launcher(launcher), 9)
        self.assertIn("gmes_batch.py was not found", self.log(repo))

    def test_the_file_is_utf8_without_a_byte_order_mark(self):
        import gmes_schedule
        td = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, td, True)
        with patch.object(gmes_schedule, "SCHEDULE_DIR", td):
            path = gmes_schedule.write_launcher("x", python="C:\\Py\\\u0623\\python.exe", repo="D:\\r")
        with open(path, "rb") as fh:
            raw = fh.read()
        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"))
        self.assertIn("\u0623".encode("utf-8"), raw)                    # the one literal is preserved


class HiddenWrapperRunsUnderRealWscript(unittest.TestCase):
    """HISTORY.md Phase 87. Proved with real wscript.exe, the interpreter Task
    Scheduler actually launches for the wrapper: hiding the console must not cost
    the batch's real exit code, or a failed unattended run would look like a
    silent success to Task Scheduler."""

    def make(self, sequence=("0",), batch="x"):
        import gmes_schedule
        td = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, td, True)
        repo = os.path.join(td, "repo")
        sched = os.path.join(repo, "schedules")
        os.makedirs(sched)
        with open(os.path.join(repo, "gmes_batch.py"), "w") as fh:
            fh.write(LauncherRunsUnderRealCmd.STUB)
        with open(os.path.join(repo, "seq.txt"), "w") as fh:
            fh.write("\n".join(sequence))
        text = gmes_schedule.launcher_text(batch, python=sys.executable, repo=repo,
                                           retry_wait=1, retries=2)
        launcher = os.path.join(sched, f"run_{batch}.cmd")
        with open(launcher, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        with patch.object(gmes_schedule, "SCHEDULE_DIR", sched):
            wrapper = gmes_schedule.write_hidden_wrapper(batch, launcher)
        return wrapper, repo

    def run_wrapper(self, wrapper):
        r = subprocess.run(["wscript.exe", "//B", "//Nologo", wrapper],
                           capture_output=True, timeout=120)
        return r.returncode

    def log(self, repo, batch="x"):
        path = os.path.join(repo, "logs", f"scheduled_{batch}.log")
        if not os.path.exists(path):
            return ""
        with open(path, "rb") as fh:
            return fh.read().decode("utf-8", "replace")

    def test_a_successful_run_exits_0_and_the_launcher_actually_ran(self):
        wrapper, repo = self.make(sequence=["0"])
        self.assertEqual(self.run_wrapper(wrapper), 0)
        self.assertIn("CWD=" + repo, self.log(repo))          # not just "exit 0" - the batch really ran

    def test_a_failed_run_s_exit_code_still_reaches_the_caller(self):
        # this is the one thing the hidden wrapper must not break: waitOnReturn=True
        # in wrapper_text is what makes this assertion pass instead of an
        # immediately-returned 0 from an async, fire-and-forget Run().
        wrapper, repo = self.make(sequence=["1"])
        self.assertEqual(self.run_wrapper(wrapper), 1)

    def test_a_retried_run_still_reaches_the_second_try(self):
        wrapper, repo = self.make(sequence=["3", "0"])
        self.assertEqual(self.run_wrapper(wrapper), 0)
        self.assertEqual(self.log(repo).count("CWD="), 2)


class LauncherText(unittest.TestCase):
    def text(self, **kw):
        import gmes_schedule
        return gmes_schedule.launcher_text("morning", python=kw.pop("python", "C:\\Py\\python.exe"),
                                           repo="D:\\Repo", **kw)

    def test_a_percent_sign_in_a_literal_is_doubled(self):
        self.assertIn('"D:\\100%%done\\python.exe" -u', self.text(python="D:\\100%done\\python.exe"))

    def test_no_parenthesised_block_can_be_closed_early_by_a_path(self):
        for line in self.text().split("\r\n"):
            self.assertFalse(line.rstrip().endswith("("), line)
            self.assertFalse(line.lstrip().startswith(")"), line)

    def test_only_a_busy_browser_and_a_failed_sign_in_are_retried(self):
        text = self.text()
        self.assertIn("if %code%==3 goto retry", text)
        self.assertIn("if %code%==4 goto retry", text)
        self.assertNotIn("%code%==1", text)
        self.assertNotIn("%code%==2", text)

    def test_the_retry_count_and_wait_are_parameters(self):
        text = self.text(retry_wait=30, retries=5)
        self.assertIn("ping -n 31 127.0.0.1", text)
        self.assertIn("GTR 5 exit /b %code%", text)

    def test_the_project_folder_is_never_written_into_the_file(self):
        self.assertNotIn("D:\\Repo", self.text())

    def test_it_stops_when_the_project_is_not_where_it_should_be(self):
        text = self.text()
        self.assertIn("if not exist gmes_batch.py goto missing", text)
        self.assertIn("exit /b 9", text)


class SchedulesTellTheTruth(unittest.TestCase):
    """HISTORY.md Phase 84.12: `schedules` showed a task that was RUNNING (result
    267009 = 0x41301) as "failed (267009)", and a schedule that had silently stopped
    working looked identical to a healthy one."""

    def setUp(self):
        import gmes_schedule
        self.s = gmes_schedule

    def test_task_schedulers_own_codes_are_read_correctly(self):
        self.assertEqual(self.s.describe_result(267009)[:2], ("running now", None))
        self.assertEqual(self.s.describe_result(267011)[:2], ("has not run yet", None))
        self.assertEqual(self.s.describe_result(0)[:2], ("ok", True))
        self.assertEqual(self.s.describe_result(0x41306)[1], False)      # terminated

    def test_this_tools_exit_codes_are_read_correctly(self):
        self.assertIn("some screens failed", self.s.describe_result(1)[0])
        self.assertIn("another run held the browser", self.s.describe_result(3)[0])
        self.assertIn("sign-in failed", self.s.describe_result(4)[0])
        self.assertIn("moved or deleted", self.s.describe_result(9)[0])

    def test_a_result_is_reported_with_its_hex_and_unknown_codes_are_failures(self):
        words, ok, hexed = self.s.describe_result(0x800710E0)
        self.assertEqual((ok, hexed), (False, "0x800710E0"))
        words, ok, hexed = self.s.describe_result(777777)
        self.assertEqual((ok, hexed), (False, "0xBDE31"))
        self.assertIn("unrecognised", words)

    def test_negative_and_odd_values_are_handled(self):
        self.assertEqual(self.s.describe_result(-2147216609)[2], "0x8004131F")
        self.assertEqual(self.s.describe_result(None), ("", None, ""))
        self.assertFalse(self.s.describe_result("garbage")[1])

    def test_a_running_task_is_not_listed_as_failed(self):
        row = ('{"name":"GMES_Batch_a","state":"Running","next":"2026-09-21T06:30:00",'
               '"last":"2026-09-20T06:30:00","result":267009,"trigger":"Daily"}')
        task = self.s.parse_list(row)[0]
        self.assertIsNone(task["last_ok"])
        self.assertEqual(task["last_hex"], "0x41301")
        self.assertEqual(task["last_text"], "running now")

    def task(self, **kw):
        base = {"batch": "am", "state": "Ready", "trigger": "Daily", "last_ok": True,
                "next_run": "2026-09-21T06:30:00", "last_run": "2026-09-20T06:30:00"}
        base.update(kw)
        return base

    NOW = __import__("datetime").datetime(2026, 9, 20, 12, 0, 0)

    def assess(self, **kw):
        return self.s.assess_task(self.task(**kw), now=self.NOW)

    def test_a_healthy_schedule_has_no_warnings(self):
        self.assertEqual(self.assess(), [])

    def test_a_disabled_task_is_called_out(self):
        self.assertTrue(any("DISABLED" in n for n in self.assess(state="Disabled")))

    def test_a_recurring_task_with_no_next_run_is_called_out(self):
        self.assertTrue(any("NO NEXT RUN" in n for n in self.assess(next_run="")))

    def test_a_one_time_task_with_no_next_run_is_normal(self):
        self.assertEqual(self.assess(next_run="", trigger="Time"), [])

    def test_an_overdue_task_is_called_out_but_not_a_slightly_late_one(self):
        self.assertTrue(any("OVERDUE" in n for n in self.assess(next_run="2026-09-20T06:30:00")))
        self.assertEqual(self.assess(next_run="2026-09-20T11:50:00"), [])

    def test_a_task_that_has_not_run_for_days_is_called_out(self):
        notes = self.assess(last_run="2026-09-05T06:30:00")
        self.assertTrue(any("has not run for 15 days" in n for n in notes))

    def test_a_failed_last_run_points_at_the_log(self):
        notes = self.assess(last_ok=False)
        self.assertTrue(any("LAST RUN FAILED" in n and "scheduled_am.log" in n for n in notes))

    def test_unparseable_times_do_not_raise(self):
        self.assertEqual(self.assess(next_run="soon", last_run="yesterday"),
                         ["NO NEXT RUN - the schedule is not active"])


class ScheduleTimesAreValidated(unittest.TestCase):
    NOW = __import__("datetime").datetime(2026, 9, 20, 12, 0, 0)

    def when(self, at, **kw):
        import gmes_schedule
        return gmes_schedule.parse_when(at, now=self.NOW, **kw)

    def test_a_time_in_any_script_is_written_as_ascii(self):
        for at in ("\u0660\u0666:\u0663\u0660", "06:\u0663\u0660", "\uff10\uff16:\uff13\uff10"):
            w = self.when(at, daily=True)
            self.assertEqual(w.at, "06:30")
            self.assertTrue(w.at.isascii())

    def test_the_trigger_script_never_contains_non_ascii(self):
        import gmes_schedule
        w = self.when("06:\u0663\u0660", daily=True)
        self.assertTrue(gmes_schedule._trigger_script(w).isascii())

    def test_a_one_time_schedule_in_the_past_is_refused(self):
        for once in ("2026-09-19", "2000-01-01"):
            with self.subTest(once=once), self.assertRaises(ValueError) as cm:
                self.when("06:30", once=once)
            self.assertIn("already passed", str(cm.exception))

    def test_earlier_today_is_the_past_and_later_today_is_not(self):
        with self.assertRaises(ValueError):
            self.when("11:59", once="2026-09-20")
        self.assertEqual(self.when("12:01", once="2026-09-20").on, "2026-09-20")

    def test_a_future_date_is_accepted_in_any_script(self):
        self.assertEqual(self.when("06:30", once="\u0662\u0660\u0662\u0666-\u0660\u0669-\u0662\u0665").on,
                         "2026-09-25")

    def test_a_date_that_does_not_exist_is_still_refused(self):
        with self.assertRaises(ValueError):
            self.when("06:30", once="2026-02-30")


@unittest.skipUnless(os.name == "nt", "the launcher is a Windows .cmd file")
class LauncherWithANonAsciiPythonPath(unittest.TestCase):
    """The one literal left in the launcher is the Python path, which sits under
    the user's profile - and an Arabic Windows user name puts non-ASCII characters
    there. It only survives because the file is UTF-8 and starts with chcp 65001."""

    def test_an_arabic_python_path_works(self):
        import gmes_schedule
        td = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, td, True)
        folder = os.path.join(td, "\u0623\u062d\u0645\u062f")
        repo = os.path.join(td, "repo")
        os.makedirs(folder)
        os.makedirs(os.path.join(repo, "schedules"))
        fake = os.path.join(folder, "fakepy.cmd")
        with open(fake, "w", encoding="utf-8", newline="") as fh:
            fh.write(f'@echo off\r\nchcp 65001 >nul\r\n"{sys.executable}" %*\r\n')
        with open(os.path.join(repo, "gmes_batch.py"), "w") as fh:
            fh.write("import os, sys\nprint('CWD=' + os.getcwd())\nsys.exit(0)\n")
        launcher = os.path.join(repo, "schedules", "run_x.cmd")
        with open(launcher, "w", encoding="utf-8", newline="") as fh:
            fh.write(gmes_schedule.launcher_text("x", python=fake, repo=repo))
        r = subprocess.run(f'cmd.exe /c "chcp 437 >nul & "{launcher}""', capture_output=True,
                           cwd=os.environ.get("SystemRoot", "C:\\Windows"), timeout=120)
        self.assertEqual(r.returncode, 0)
        with open(os.path.join(repo, "logs", "scheduled_x.log"), "rb") as fh:
            self.assertIn("CWD=" + repo, fh.read().decode("utf-8", "replace"))


class SchedulesCommandShowsWhatIsWrong(unittest.TestCase):
    def run_cmd(self, tasks):
        import gmes_batch
        import gmes_schedule
        printed = []
        with patch.object(gmes_schedule, "list_tasks", return_value=tasks), \
             patch("builtins.print", side_effect=lambda *a, **k: printed.append(" ".join(str(x) for x in a))):
            rc = gmes_batch.cmd_schedules()
        return rc, "\n".join(printed)

    def task(self, **kw):
        base = {"batch": "am", "task": "GMES_Batch_am", "state": "Ready", "trigger": "Daily",
                "next_run": "2999-01-01T06:30:00", "last_run": "2026-09-20T06:30:00",
                "last_result": 0, "last_text": "ok", "last_hex": "0x0", "last_ok": True}
        base.update(kw)
        return base

    def test_a_running_task_reads_as_running_not_failed(self):
        rc, out = self.run_cmd([self.task(last_result=267009, last_text="running now",
                                          last_hex="0x41301", last_ok=None, state="Running")])
        self.assertIn("running now (0x41301)", out)
        self.assertNotIn("failed", out)

    def test_a_failure_is_worded_and_pointed_at_the_log(self):
        rc, out = self.run_cmd([self.task(last_result=4, last_text="sign-in failed (after retries)",
                                          last_hex="0x4", last_ok=False)])
        self.assertIn("sign-in failed (after retries) (0x4)", out)
        self.assertIn("LAST RUN FAILED", out)
        self.assertIn("scheduled_am.log", out)

    def test_a_schedule_that_stopped_working_is_flagged(self):
        rc, out = self.run_cmd([self.task(next_run="", state="Ready")])
        self.assertIn("NO NEXT RUN", out)

    def test_a_healthy_schedule_prints_no_warnings(self):
        rc, out = self.run_cmd([self.task()])
        self.assertNotIn("!", out)


class StaleRunLockRules(unittest.TestCase):
    """HISTORY.md Phase 84.13, found by probing lock files: `_pid_alive()` was the
    only test, so an EMPTY or garbled lock, and a lock whose process number Windows
    had since handed to an unrelated program, refused every run forever - a
    scheduled night would exit 3 again and again."""

    H = 3600

    def stale(self, holder, age=60, alive=True, image="python.exe"):
        return core.lock_is_stale(holder, age, alive, image)[0]

    def test_a_live_python_run_is_respected(self):
        self.assertFalse(self.stale("4242 2026-09-21 08:00:00"))
        for image in ("python.exe", "pythonw.exe", "py.exe", ""):
            with self.subTest(image=image):
                self.assertFalse(self.stale("4242 x", image=image))

    def test_a_dead_process_is_stale(self):
        stale, why = core.lock_is_stale("4242 x", 60, False, "")
        self.assertTrue(stale)
        self.assertIn("no longer running", why)

    def test_a_reused_process_number_is_stale(self):
        for image in ("explorer.exe", "chrome.exe", "svchost.exe", "code.exe"):
            with self.subTest(image=image):
                stale, why = core.lock_is_stale("4242 x", 60, True, image)
                self.assertTrue(stale)
                self.assertIn(image, why)
                self.assertIn("reused", why)

    def test_a_lock_older_than_any_run_can_last_is_stale_even_if_the_pid_is_python(self):
        self.assertFalse(self.stale("4242 x", age=7.9 * self.H))
        self.assertTrue(self.stale("4242 x", age=8.1 * self.H))

    def test_a_heartbeat_lock_is_judged_by_its_last_beat_not_by_how_long_the_run_is(self):
        # HISTORY.md Phase 92.2: a run that keeps beating is never "too old",
        # and one whose beat stopped is abandoned after minutes, not 8 hours.
        beat = "4242\t2026-09-23 02:00:00\ttok\theartbeat"
        self.assertFalse(self.stale(beat, age=4 * 60))
        stale, why = core.lock_is_stale(beat, 6 * 60, True, "python.exe")
        self.assertTrue(stale)
        self.assertIn("heartbeat", why)
        # the same age on an old-format lock is still respected (8-hour rule)
        self.assertFalse(self.stale("4242\t2026-09-23 02:00:00\ttok", age=6 * 60))
        # a dead or reused holder is stale however fresh its beat
        self.assertTrue(self.stale(beat, age=1, alive=False))
        self.assertTrue(self.stale(beat, age=1, image="explorer.exe"))

    def test_an_unreadable_lock_is_respected_while_recent_and_stale_when_old(self):
        for holder in ("", "garbage text", "\x00\x00\x00", "-5 x", "0 x", "abc 123"):
            with self.subTest(holder=holder):
                self.assertFalse(self.stale(holder, age=120, alive=False))
                self.assertTrue(self.stale(holder, age=11 * 60, alive=False))

    def test_the_reason_is_always_given_when_stale(self):
        for holder, age, alive, image in (("", 9999, False, ""), ("1 x", 1, False, ""),
                                          ("1 x", 1, True, "explorer.exe"), ("1 x", 99 * self.H, True, "python.exe")):
            stale, why = core.lock_is_stale(holder, age, alive, image)
            self.assertTrue(stale)
            self.assertTrue(why)


class AcquireRunLockWithStaleFiles(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.lock = os.path.join(self._tmp.name, ".run.lock")
        p = patch.object(core, "run_lock_path", lambda: self.lock)
        p.start()
        self.addCleanup(p.stop)
        q = patch("builtins.print")
        self.said = q.start()
        self.addCleanup(q.stop)

    def put(self, content, age_seconds=0):
        with open(self.lock, "w", encoding="utf-8") as fh:
            fh.write(content)
        if age_seconds:
            old = time.time() - age_seconds
            os.utime(self.lock, (old, old))

    def acquire(self):
        # Normalised to a bare True on success (acquire_run_lock() itself now
        # returns an opaque ownership token, not True) so every existing
        # assertIs(self.acquire(), True) below keeps meaning exactly what it
        # always meant: "the lock was granted", not "and here is the token".
        try:
            core.acquire_run_lock()
            return True
        except core.RunLocked as e:
            return str(e)

    def owner(self):
        with open(self.lock, encoding="utf-8") as fh:
            return fh.read().split()[0]

    def test_an_old_empty_lock_no_longer_blocks_forever(self):
        self.put("", age_seconds=3600)
        self.assertIs(self.acquire(), True)
        self.assertEqual(self.owner(), str(os.getpid()))

    def test_a_recent_empty_lock_is_still_respected(self):
        self.put("", age_seconds=5)
        self.assertIn("Another G-MES run", self.acquire())

    def test_an_old_garbled_lock_is_replaced(self):
        self.put("\x00\x00 not a pid", age_seconds=3600)
        self.assertIs(self.acquire(), True)

    def test_a_dead_pid_is_replaced(self):
        self.put("999999999 2020-01-01 00:00:00\n")
        self.assertIs(self.acquire(), True)

    def test_a_live_pid_now_owned_by_another_program_is_replaced(self):
        self.put(f"{os.getppid()} 2026-01-01 00:00:00\n", age_seconds=600)
        with patch.object(core, "_process_image", return_value="explorer.exe"):
            self.assertIs(self.acquire(), True)
        self.assertTrue(any("reused" in str(c) for c in self.said.call_args_list))

    def test_a_live_python_holder_is_respected(self):
        self.put(f"{os.getppid()} 2026-01-01 00:00:00\n", age_seconds=600)
        with patch.object(core, "_process_image", return_value="python.exe"):
            self.assertIn("Another G-MES run", self.acquire())
        self.assertEqual(self.owner(), str(os.getppid()))            # untouched

    def test_a_python_holder_older_than_any_run_is_replaced(self):
        self.put(f"{os.getppid()} 2026-01-01 00:00:00\n", age_seconds=9 * 3600)
        with patch.object(core, "_process_image", return_value="python.exe"):
            self.assertIs(self.acquire(), True)

    def test_the_message_names_the_holder_and_the_file_to_delete(self):
        self.put(f"{os.getppid()} 2026-01-01 00:00:00\n", age_seconds=60)
        with patch.object(core, "_process_image", return_value="python.exe"):
            text = self.acquire()
        self.assertIn(str(os.getppid()), text)
        self.assertIn(self.lock, text)

    def test_it_gives_up_instead_of_looping_if_the_stale_file_cannot_be_removed(self):
        # Moved aside by an atomic rename since Phase 92.3, not unlinked.
        self.put("", age_seconds=3600)
        with patch.object(core.os, "rename", side_effect=PermissionError("locked")):
            self.assertIn("Another G-MES run", self.acquire())

    def test_a_fresh_lock_created_between_judging_and_removing_is_never_taken(self):
        # HISTORY.md Phase 92.3 (Open Item 85): run A judges an old lock stale;
        # before A removes it, run B also judged it stale, removed it and made
        # its own. A must not delete B's brand-new lock and run beside it.
        self.put("999999999 2020-01-01 00:00:00\n")
        real_rename = os.rename

        def someone_else_got_there_first(src, dst):
            with open(self.lock, "w", encoding="utf-8") as fh:
                fh.write(f"{os.getppid()}\t2026-09-23 02:00:00\tB-TOKEN\theartbeat\n")
            return real_rename(src, dst)
        with patch.object(core.os, "rename", side_effect=someone_else_got_there_first), \
             patch.object(core, "_process_image", return_value="python.exe"):
            self.assertIn("Another G-MES run", self.acquire())
        with open(self.lock, encoding="utf-8") as fh:
            self.assertIn("B-TOKEN", fh.read())                    # B's lock is back, untouched
        self.assertEqual([n for n in os.listdir(self._tmp.name) if ".stale-" in n], [])

    def test_a_new_lock_carries_the_heartbeat_mark_and_is_touched_while_held(self):
        with patch.object(core, "LOCK_HEARTBEAT_SECONDS", 0.05):
            token = core.acquire_run_lock()
            self.addCleanup(core.release_run_lock, token)
            with open(self.lock, encoding="utf-8") as fh:
                self.assertEqual(fh.read().strip().split("\t")[3], "heartbeat")
            old = time.time() - 3600
            os.utime(self.lock, (old, old))
            deadline = time.time() + 5
            while time.time() < deadline and os.path.getmtime(self.lock) < old + 60:
                time.sleep(0.02)
        self.assertGreater(os.path.getmtime(self.lock), old + 60)

    def test_the_heartbeat_stops_at_release_and_never_touches_another_runs_lock(self):
        with patch.object(core, "LOCK_HEARTBEAT_SECONDS", 0.05):
            token = core.acquire_run_lock()
            self.put(f"{os.getppid()}\t2026-09-23 02:00:00\tOTHER\theartbeat\n",
                     age_seconds=3600)
            before = os.path.getmtime(self.lock)
            time.sleep(0.3)
            self.assertEqual(os.path.getmtime(self.lock), before)
            core.release_run_lock(token)
        self.assertNotIn(token, core._heartbeats)

    def test_release_only_removes_this_processs_lock_file(self):
        self.assertTrue(core.acquire_run_lock())
        core.release_run_lock()
        self.assertFalse(os.path.exists(self.lock))

    def test_the_process_image_of_this_process_is_python(self):
        self.assertTrue(core._process_image(os.getpid()).startswith("py"))

    def test_an_unknown_process_has_no_image(self):
        self.assertEqual(core._process_image(999999999), "")


class OpenScreenWaitsForTheRecordedShape(unittest.TestCase):
    """HISTORY.md Phase 84.17, found by the second clean-machine run: on a brand-new
    profile the tool's "finished building" proxy (the same counts on two polls, 1 s
    apart) fired before the late-binding widgets appeared, it hashed a PARTIAL shape
    (the dsGuide grid missing) and refused to replay a recording that matched the
    finished screen exactly - minutes later the two fingerprints were identical."""

    FULL = {"found": True, "filters": [{"dataset": "dsOrgAuthDVO", "column": "userIdLike"}],
            "unbound": [], "grids": [{"dataset": "dsStateRate"}, {"dataset": "dsGuide"}]}
    PARTIAL = {"found": True, "filters": [{"dataset": "dsOrgAuthDVO", "column": "userIdLike"}],
               "unbound": [], "grids": [{"dataset": "dsStateRate"}]}

    class Clock:
        def __init__(self):
            self.now = 1000.0
        def time(self):
            return self.now
        def sleep(self, s):
            self.now += s

    def run_open(self, infos, expected="fp-full", shape_grace=45, ready_wait=90):
        import gmes_profile
        clock = self.Clock()
        seq = iter(infos)
        last = [infos[-1]]

        def discover(ws, code):
            try:
                last[0] = next(seq)
            except StopIteration:
                pass
            return last[0]

        def fingerprint(info, aliases=None):
            return "fp-full" if len(info["grids"]) == 2 else "fp-partial"

        row = {"winId": "winX_1_1", "menuId": "FFM0520", "pageUrl": "R3224WM00.xfdl", "title": "t"}
        with patch.object(core, "recover_from_session_kick"), \
             patch.object(core.gmes_open_screen, "open_screens", return_value={"rows": [row]}), \
             patch.object(core.gmes_open_screen, "activate_screen", return_value=True), \
             patch.object(core.gmes_common, "close_child_popups", return_value=[]), \
             patch.object(core, "discover", side_effect=discover), \
             patch.object(gmes_profile, "fingerprint", side_effect=fingerprint), \
             patch.object(core.time, "sleep", side_effect=clock.sleep), \
             patch.object(core.time, "time", side_effect=clock.time):
            screen = core.open_screen(object(), "R3224WM00", ready_wait=ready_wait,
                                      expected_fingerprint=expected, shape_grace=shape_grace,
                                      log=lambda m: None)
        return screen, clock.now - 1000.0

    def test_a_partial_shape_is_not_accepted_when_the_recording_says_more_is_coming(self):
        # two identical partial polls (the old exit), then the grid appears
        screen, elapsed = self.run_open([self.PARTIAL] * 3 + [self.FULL] * 3)
        self.assertEqual(len(screen.info["grids"]), 2)
        self.assertLess(elapsed, 15)

    def test_a_shape_that_already_matches_returns_at_once(self):
        screen, elapsed = self.run_open([self.FULL] * 5)
        self.assertEqual(len(screen.info["grids"]), 2)
        self.assertLessEqual(elapsed, 3)

    def test_a_shape_that_never_matches_returns_after_the_grace_period_not_the_whole_wait(self):
        screen, elapsed = self.run_open([self.PARTIAL] * 200, shape_grace=20, ready_wait=90)
        self.assertEqual(len(screen.info["grids"]), 1)          # the caller reports the mismatch
        self.assertGreaterEqual(elapsed, 20)
        self.assertLess(elapsed, 30)

    def test_the_grace_is_measured_from_the_first_settle_not_from_every_poll(self):
        screen, elapsed = self.run_open([self.PARTIAL] * 500, shape_grace=10, ready_wait=90)
        self.assertLess(elapsed, 20)

    def test_without_a_recording_the_old_behaviour_is_unchanged(self):
        screen, elapsed = self.run_open([self.PARTIAL] * 5, expected=None)
        self.assertEqual(len(screen.info["grids"]), 1)
        self.assertLessEqual(elapsed, 3)

    def test_a_screen_that_keeps_changing_size_is_not_returned_until_it_settles(self):
        growing = [dict(self.FULL, grids=[{"dataset": f"d{i}"}]) for i in range(1, 6)]
        screen, _ = self.run_open(growing + [self.FULL] * 4, expected="fp-full")
        self.assertEqual(len(screen.info["grids"]), 2)

    def test_the_overall_deadline_returns_what_was_held_instead_of_failing(self):
        # the ready wait ends BEFORE the grace period: the caller still gets the screen,
        # so the mismatch is reported as a mismatch, not as a screen that never built
        screen, elapsed = self.run_open([self.PARTIAL] * 200, shape_grace=45, ready_wait=12)
        self.assertEqual(len(screen.info["grids"]), 1)
        self.assertLess(elapsed, 20)

    def test_a_screen_that_never_builds_at_all_still_raises(self):
        with self.assertRaises(RuntimeError) as cm:
            self.run_open([{"found": False, "reason": "no forms yet"}] * 200, ready_wait=10)
        self.assertIn("never finished building", str(cm.exception))


class RunScreenPassesTheRecordedShapeToTheOpen(unittest.TestCase):
    """The wait can only be for the recorded shape if run_screen hands it over."""

    def replay(self, profile):
        import gmes_profile
        screen = AutoReplayFromSavedProfile.FakeScreen({
            "filters": [flt(column="fromYmd", control="mskFrom")], "unbound": [],
            "grids": [grid("grdMain", "dsMain", 100)]})
        opened = Mock(return_value=screen)
        with patch.object(gmes_profile, "load", return_value=profile), \
             patch.object(gmes_profile, "save", return_value="x.json"), \
             patch.object(core, "open_screen", opened), \
             patch.object(core, "org_selection", return_value={"found": True, "org": "VD"}):
            try:
                core.run_screen(None, "R3224WM00", export="none", log=lambda m: None)
            except RuntimeError:
                pass                       # only the call into open_screen matters here
        return opened

    def test_the_recordings_shape_and_aliases_reach_open_screen(self):
        opened = self.replay({"opening_fingerprint": "abc123", "fingerprint": "abc123",
                              "grid": {"dataset": "dsMain"}, "grid_aliases": {"dsB": "dsA"},
                              "values": {"division": "VD"}})
        kwargs = opened.call_args.kwargs
        self.assertEqual(kwargs["expected_fingerprint"], "abc123")
        self.assertEqual(kwargs["grid_aliases"], {"dsB": "dsA"})

    def test_no_recording_means_nothing_is_expected(self):
        opened = self.replay(None)
        kwargs = opened.call_args.kwargs
        self.assertIsNone(kwargs["expected_fingerprint"])
        self.assertIsNone(kwargs["grid_aliases"])

    def test_a_relearn_run_does_not_wait_for_the_old_shape(self):
        import gmes_profile
        screen = AutoReplayFromSavedProfile.FakeScreen({
            "filters": [flt(column="fromYmd", control="mskFrom")], "unbound": [],
            "grids": [grid("grdMain", "dsMain", 100)]})
        opened = Mock(return_value=screen)
        with patch.object(gmes_profile, "load", return_value={"opening_fingerprint": "old"}), \
             patch.object(gmes_profile, "save", return_value="x.json"), \
             patch.object(core, "open_screen", opened), \
             patch.object(core, "org_selection", return_value={"found": True, "org": "VD"}):
            try:
                core.run_screen(None, "R3224WM00", export="none", trust_profile=False,
                                log=lambda m: None)
            except RuntimeError:
                pass
        self.assertIsNone(opened.call_args.kwargs["expected_fingerprint"])


class AColumnBoundOnSeveralSubFormsResolvesToTheVisibleOne(unittest.TestCase):
    """P4115UM00 binds startTerm/endTerm on four sub-forms (one per view tab).
    `--set startTerm=...` refused as ambiguous although only one is on screen
    (HISTORY.md Phase 84.20)."""

    def _info(self, *flags):
        return {"filters": [flt(column="startTerm", label="Period" if v else "-",
                                control="mskDateFrom", visible=v,
                                path=f"application.win.form.divMain0{i}.form")
                            for i, v in enumerate(flags)]}

    def test_the_one_visible_binding_wins(self):
        found = core.match_filter(self._info(True, False, False), "startTerm")
        self.assertIsInstance(found, dict)
        self.assertTrue(found["visible"])
        self.assertTrue(found["path"].endswith("divMain00.form"))

    def test_the_visible_one_wins_whichever_position_it_is_in(self):
        found = core.match_filter(self._info(False, False, True), "startTerm")
        self.assertTrue(found["path"].endswith("divMain02.form"))

    def test_the_control_name_resolves_the_same_way(self):
        self.assertIsInstance(
            core.match_filter(self._info(True, False), "mskDateFrom"), dict)

    def test_two_visible_bindings_stay_ambiguous(self):
        self.assertIsInstance(
            core.match_filter(self._info(True, True, False), "startTerm"), list)

    def test_no_visible_binding_stays_ambiguous(self):
        self.assertIsInstance(
            core.match_filter(self._info(False, False), "startTerm"), list)


class AScreenCanPinItsOwnExportDestination(unittest.TestCase):
    """HISTORY.md Phase 84.28: a screen can be told, once, where its export
    should land and in what format - and every later replay (bare CLI, or
    the interactive front end's Replay) then goes there automatically,
    without the caller repeating `--output-dir`/`--export` every time. An
    ORDINARY run of any other screen must never start pinning a
    destination it was never asked for."""

    def make_screen(self):
        return AutoReplayFromSavedProfile.FakeScreen({
            "filters": [flt(column="fromYmd", control="mskFrom")],
            "unbound": [], "grids": [grid("grdMain", "dsMain", 100)]})

    def test_a_pinned_destination_is_read_back_and_reapplied_when_the_caller_says_nothing(self):
        import gmes_profile
        screen = self.make_screen()
        fp = gmes_profile.fingerprint(screen.info)
        # export="none" both stands in for a real pinned choice AND skips
        # the actual file-writing machinery this offline test cannot drive.
        profile = {"fingerprint": fp, "opening_fingerprint": fp,
                  "grid": {"dataset": "dsMain"}, "options": [],
                  "output_dir": r"\\server\share\Pinned", "export": "none",
                  "values": {"division": "", "sets": {}}}
        with patch.object(gmes_profile, "load", return_value=profile), \
             patch.object(gmes_profile, "save", return_value="x.json") as save, \
             patch.object(core, "open_screen", return_value=screen), \
             patch.object(core, "org_selection", return_value={"found": False}):
            result = core.run_screen(None, "M3912UM00", export=None, out_dir=None,
                                     log=lambda _m: None)
        self.assertTrue(result["ok"], result.get("error"))
        self.assertEqual(save.call_args.kwargs.get("output_dir"), r"\\server\share\Pinned")
        self.assertEqual(save.call_args.kwargs.get("export"), "none")

    def test_the_true_default_never_gets_pinned(self):
        # The pure decision behind the save() call above - isolated because
        # driving run_screen() through a REAL "both" export offline would
        # need a fake xlsx and csv file on disk, not just a fake screen.
        pin = core.destination_to_pin(core.OUTPUT_DIR, "both")
        self.assertIsNone(pin["output_dir"])
        self.assertIsNone(pin["export"])

    def test_anything_other_than_the_true_default_is_pinned(self):
        pin = core.destination_to_pin(r"\\server\share\X", "csv")
        self.assertEqual(pin["output_dir"], r"\\server\share\X")
        self.assertEqual(pin["export"], "csv")

    def test_excel_only_is_the_default_and_both_now_means_it(self):
        # HISTORY.md Phase 94.1 - the owner's decision: Excel only.
        self.assertEqual(core.DEFAULT_EXPORT, "xlsx")
        for value in (None, "", "both", "xlsx"):
            with self.subTest(value=value):
                self.assertEqual(core.effective_export(value), "xlsx")
                self.assertIsNone(core.destination_to_pin(core.OUTPUT_DIR, value)["export"])
        self.assertEqual(core.effective_export("csv"), "csv")
        self.assertEqual(core.effective_export("none"), "none")

    def test_only_the_dimension_that_actually_differs_is_pinned(self):
        # A screen that only customises ONE of the two must not pin the
        # other as a side effect of the call shape.
        only_dir = core.destination_to_pin(r"\\server\share\X", "both")
        self.assertEqual(only_dir["output_dir"], r"\\server\share\X")
        self.assertIsNone(only_dir["export"])
        only_export = core.destination_to_pin(core.OUTPUT_DIR, "none")
        self.assertIsNone(only_export["output_dir"])
        self.assertEqual(only_export["export"], "none")

    def test_an_explicit_caller_value_wins_over_a_pinned_one_and_repins_it(self):
        import gmes_profile
        screen = self.make_screen()
        fp = gmes_profile.fingerprint(screen.info)
        profile = {"fingerprint": fp, "opening_fingerprint": fp,
                  "grid": {"dataset": "dsMain"}, "options": [],
                  "output_dir": r"\\server\share\Old", "export": "xlsx",
                  "values": {"division": "", "sets": {}}}
        with patch.object(gmes_profile, "load", return_value=profile), \
             patch.object(gmes_profile, "save", return_value="x.json") as save, \
             patch.object(core, "open_screen", return_value=screen), \
             patch.object(core, "org_selection", return_value={"found": False}):
            result = core.run_screen(None, "M3912UM00",
                                     out_dir=r"\\server\share\New", export="none",
                                     log=lambda _m: None)
        self.assertTrue(result["ok"], result.get("error"))
        self.assertEqual(save.call_args.kwargs.get("output_dir"), r"\\server\share\New")
        self.assertEqual(save.call_args.kwargs.get("export"), "none")


class SavingAPinnedDestination(unittest.TestCase):
    """gmes_profile.save()'s own contract for the two new fields, isolated
    from run_screen()'s resolution logic above."""

    def setUp(self):
        import gmes_profile, tempfile
        self.gp = gmes_profile
        self.tmp = tempfile.mkdtemp(prefix="gmes-test-screens-")
        self._orig_dir = self.gp.SCREENS_DIR
        self.gp.SCREENS_DIR = self.tmp

    def tearDown(self):
        import shutil
        self.gp.SCREENS_DIR = self._orig_dir
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_given_values_are_written(self):
        self.gp.save("P9999UM99", "Fake Screen", "XXX0001", {},
                    output_dir=r"\\server\share\X", export="xlsx")
        saved = self.gp.load("P9999UM99")
        self.assertEqual(saved.get("output_dir"), r"\\server\share\X")
        self.assertEqual(saved.get("export"), "xlsx")

    def test_omitted_values_do_not_appear(self):
        self.gp.save("P9999UM99", "Fake Screen", "XXX0001", {})
        saved = self.gp.load("P9999UM99")
        self.assertNotIn("output_dir", saved)
        self.assertNotIn("export", saved)

    def test_a_second_save_without_them_drops_a_previously_pinned_value(self):
        # save() always writes the CURRENT resolved state, never merges
        # output_dir/export with what an older file had - it is
        # run_screen()'s job to carry a pin forward by reading it out of
        # the loaded profile BEFORE calling save() again, not save()'s.
        self.gp.save("P9999UM99", "Fake Screen", "XXX0001", {},
                    output_dir=r"\\server\share\X", export="xlsx")
        self.gp.save("P9999UM99", "Fake Screen", "XXX0001", {})
        saved = self.gp.load("P9999UM99")
        self.assertNotIn("output_dir", saved)
        self.assertNotIn("export", saved)

    def test_a_bad_type_or_choice_is_dropped_not_crashed_on(self):
        path = self.gp.path_for("P9999UM99")
        import json
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"screen": "P9999UM99", "output_dir": 12345,
                      "export": "carrier-pigeon"}, fh)
        loaded = self.gp.load("P9999UM99")
        self.assertNotIn("output_dir", loaded)
        self.assertNotIn("export", loaded)


if __name__ == "__main__":
    unittest.main(verbosity=2)

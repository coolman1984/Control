"""
The G-MES report tool, for people - the front end behind GMES_Workflow.bat.

    python run_gmes_workflow.py
    (or double-click GMES_Workflow.bat)

A menu, a few short questions, then the work, narrated as numbered steps.
The menu shows before anything signs in - viewing what is already saved
needs no browser at all.

The one idea worth understanding
--------------------------------
The tool has a MEMORY. The first time a screen is used it works everything
out from the screen itself and, if the run fully succeeds, writes down the
two things it cannot work out again - which box is the "from" date, and which
table holds the results. That is the RECORDING phase, and it happens by
itself; there is no separate command.

Every run after that is the REPLAYING phase: it checks the screen still
matches what it wrote down, and reuses it. If anything has moved it says so
and reads the screen from scratch instead. It never quietly guesses.

The banner before the work says which of the two is about to happen.

All of the drawing lives in gmes_ui, which degrades to plain ASCII when the
console cannot do better. Presentation never decides anything.
"""
import os
import re
import sys
import time

# Proxy bypass is applied by importing cdp_common: the corporate gateway
# intercepts localhost, so every CDP call needs NO_PROXY set before the
# first request.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cdp_common  # noqa: E402
import gmes_batch  # noqa: E402
import gmes_common  # noqa: E402
import gmes_core as core  # noqa: E402
import gmes_library as library  # noqa: E402
import gmes_log  # noqa: E402
import gmes_open_screen  # noqa: E402
import gmes_profile  # noqa: E402
import gmes_ui as ui  # noqa: E402


# ---------------------------------------------------------------------------
# Asking
# ---------------------------------------------------------------------------

def clean(raw):
    """Strip whitespace and a stray BOM, which shows up on the first line
    read from some piped stdin sources."""
    return raw.strip().lstrip("﻿").strip()


class InputClosed(Exception):
    """stdin has ended - there is nobody left to ask.

    A blank line and a closed stream are NOT the same thing, and treating
    them alike span forever: every re-ask loop (a wrong UI number, a bad
    date) saw the empty default, rejected it, and asked again - thousands of
    times, instantly. A blank line is an answer. A closed stream means stop.
    """


class GoBack(Exception):
    """Typed 'back' (or bare 'b', where 'b' is not already meaningful) -
    abandon the current question sequence and return to the main menu."""


class TaskCancelled(Exception):
    """Typed 'cancel' (or bare 'c', where 'c' is not already meaningful) -
    abandon the current task and return to the main menu."""


class QuitRequested(Exception):
    """Typed 'quit'/'exit' (or bare 'q') at any prompt - close the tool."""


class SignInFailed(Exception):
    """Sign-in did not succeed. Raised from Session.get() and caught at the
    main-menu dispatch level, so a failed sign-in returns to the menu
    instead of ending the whole program - today it ends it outright."""


# Recognised at every prompt, before the caller's own answer-parsing ever
# sees the text - one mechanism, not a parallel prompt system bolted on
# beside ask(). Full words always work; bare letters only where a question's
# own real answers do not already use them (question_mode's R/P/B, and
# one_run's "Run it?" c=change - both pass controls="words" to keep their
# existing single-letter meaning instead of being swallowed as a command).
_CONTROL_WORDS = {
    "back": GoBack, "b": GoBack,
    "cancel": TaskCancelled, "c": TaskCancelled,
    "quit": QuitRequested, "exit": QuitRequested, "q": QuitRequested,
}
_HELP_WORDS = {"help", "h", "?"}


def ask(label, hint="", default="", help_text="", controls="full"):
    """Ask one question and ECHO what came back.

    The echo is not decoration. It confirms what the tool understood - which
    is where a date typed 2026-09-09 is shown back as 20260909 - and it is
    the only way the answers are visible at all when this is driven from a
    script rather than a keyboard.

    `controls` decides whether a global escape word is recognised before the
    caller sees the answer at all: "full" (the default) recognises both the
    bare letter and the full word for back/cancel/quit and the word/`?` for
    help; "words" recognises only the full words, for a question whose own
    real answers already use one of those letters; `False` turns interception
    off entirely - the one place that matters is a typed confirmation phrase
    (batch's "type RUN N REPORTS"), which must never be read as a command."""
    tail = f"  {ui.GREY}{hint}{ui.RESET}" if hint else ""
    try:
        raw = input(f"    {ui.CYAN}{ui.ARROW}{ui.RESET} {label}{tail}\n      "
                    f"{ui.BOLD}> {ui.RESET}")
    except EOFError:
        raise InputClosed()
    stripped = clean(raw)
    if controls and stripped:
        low = stripped.lower()
        if low in _HELP_WORDS:
            ui.note(help_text or hint or "No extra help is available for this question.")
            return ask(label, hint, default, help_text, controls)
        cls = _CONTROL_WORDS.get(low)
        if cls is not None and (controls == "full" or len(low) > 1):
            print()
            raise cls()
    value = stripped or default
    shown = value if value else "(skipped)"
    colour = ui.WHITE if value else ui.GREY
    print(f"      {ui.GREEN}{ui.TICK}{ui.RESET} {colour}{shown}{ui.RESET}\n")
    return value


class Questions:
    """Numbers the questions as they are actually asked.

    The numbers used to be written into the labels, so a screen with no date
    fields - where the "To date" question is never reached - counted 1, 2, 3,
    5. A visible gap in a numbered list reads as something having gone
    wrong."""

    def __init__(self):
        self.asked = 0

    def ask(self, label, hint="", default="", help_text="", controls="full"):
        self.asked += 1
        return ask(f"{self.asked}. {label}", hint, default, help_text, controls)

    def again(self, label, hint="", default="", help_text="", controls="full"):
        """Re-ask after a bad answer. The number stays put - a mistyped date
        is not a new question, and renumbering makes it look like one."""
        return ask(f"{self.asked}. {label}", hint, default, help_text, controls)


class Session:
    """Signs in and connects to G-MES on first actual need, and holds this
    process's run lock - acquired at that same moment, for the same reason:
    a person only viewing saved reports or schedules never touches the
    browser, so should never be blocked by, or itself hold, the
    one-run-at-a-time lock either. Two people can both browse the menu at
    once; the lock still applies the instant either one runs something."""

    def __init__(self):
        self._ws = None
        self._lock_token = None

    def get(self):
        if self._ws is None:
            if self._lock_token is None:
                self._lock_token = core.acquire_run_lock()   # may raise core.RunLocked
            if not sign_in_visibly():
                raise SignInFailed("Could not sign in. Nothing was run.")
            self._ws = core.connect()
        return self._ws

    def close(self):
        if self._ws is not None:
            try:
                self._ws.close()
            except Exception:
                pass
            self._ws = None
        if self._lock_token is not None:
            core.release_run_lock(self._lock_token)
            self._lock_token = None


def friendly_date(yyyymmdd):
    """'20260921' -> 'Monday 21 September 2026'. Never guessed at, never
    calculated from anything but the digits given - purely a display of a
    date already decided elsewhere."""
    from datetime import datetime
    d = datetime.strptime(yyyymmdd, "%Y%m%d")
    return f"{d:%A} {d.day} {d:%B} {d.year}"


# ---------------------------------------------------------------------------
# The run narration
#
# core.run_screen() reports each thing it does as "  key      : detail".
# This turns those into numbered steps in plain words. An unrecognised key is
# printed as-is rather than swallowed: a display layer must never be the
# reason something goes unseen.
# ---------------------------------------------------------------------------

STEP_LABELS = {
    "screen": "Open the screen",
    "filters": "Read what the screen offers",
    "results": "Find the results table",
    "option": "Set a screen option",
    "division": "Tick the division",
    "dates": "Type the dates in",
    "cleared": "Clear leftovers from last time",
    "filter": "Set a filter",
    "inquiry": "Press Inquiry and wait for the answer",
    "verified": "Check the rows really match",
    "dates in": "Dates that came back",
    "excel": "Download the Excel file",
    "csv": "Save a readable copy (CSV)",
    "tab": "Close the screen tab",
    "dry run": "Stop here - Inquiry NOT pressed",
}

MEMORY_KEYS = ("learned", "changed")
NOTE_KEYS = {"found": "found in the menu", "popups": "cleared popups"}


class Narrator:
    """Prints core's progress as numbered steps, with a time for each."""

    def __init__(self):
        self.step = 0
        self.last = time.time()

    def __call__(self, text):
        raw = (text or "").rstrip()
        if not raw.strip() or set(raw.strip()) <= {"=", "-"} or ":" not in raw:
            return                                  # core's own rules/banners
        key, _, detail = raw.partition(":")
        key, detail = key.strip(), detail.strip()

        if key in MEMORY_KEYS:
            self.memory(key, detail)
            return
        if key in NOTE_KEYS:
            ui.note(f"{NOTE_KEYS[key]}: {detail}")
            return
        if key == "warning":
            ui.note(detail, "warn")
            return
        if key == "FAILED":
            print()
            ui.note(f"STOPPED: {detail}", "bad")
            return

        label = STEP_LABELS.get(key)
        if label is None:
            print(f"      {ui.GREY}{raw.strip()}{ui.RESET}")   # unknown: show it
            return
        self.step += 1
        now = time.time()
        ui.step(self.step, label, detail, seconds=now - self.last)
        self.last = now

    def memory(self, key, detail):
        if key == "changed":
            ui.note(f"the screen changed: {detail}", "warn")
        elif detail.lower().startswith("saved to"):
            self.step += 1
            now = time.time()
            ui.step(self.step, "Remember this screen for next time", detail,
                    seconds=now - self.last)
            self.last = now
        elif detail.lower().startswith("ignored"):
            ui.note(detail, "warn")
        else:
            ui.note(f"using memory: {detail}")


# ---------------------------------------------------------------------------
# Questions
# ---------------------------------------------------------------------------

def question_mode(q):
    """Which way of working, chosen rather than inferred.

    The tool can tell for itself whether it has seen a screen before, and it
    still does - but being told, and being able to choose, is not the same as
    having it decided for you. RECORD is also the way to re-teach a screen
    that has changed, which was otherwise only reachable through --relearn.

    Live-caught: a person typed the screen code they actually wanted
    ("P1114WM00") straight into THIS question - and `answer.startswith("p")`
    silently read it as "Replay", because almost every G-MES screen code in
    this account starts with P. `.startswith()` is the wrong test for a
    two-way choice: it also accepts "PLANT", "Plan", or any other word that
    happens to start with the right letter. Only an exact r/record/p/replay
    now counts; anything shaped like a real screen code gets a specific
    explanation instead of a generic "type R or P", since that is exactly
    what a person expecting to type the code next needs to hear."""
    print(f"    {ui.GREY}RECORD  - a screen you have not used before. It opens the")
    print(f"              screen, shows you every filter it has, and asks.")
    print(f"    REPLAY  - a screen it already knows. Just the UI number, your")
    print(f"              filter values, and Enter.")
    print(f"    BATCH   - several recorded screens together: all of them, a few")
    print(f"              you pick, or a saved list - now or on a schedule.{ui.RESET}\n")
    first = True
    while True:
        prompt = q.ask if first else q.again
        first = False
        raw = prompt("Record, Replay or Batch?", "type R, P or B", default="P",
                     controls="words")
        answer = raw.strip().lower()
        if answer in ("r", "record"):
            return "record"
        if answer in ("p", "replay"):
            return "replay"
        if answer in ("b", "batch"):
            return "batch"
        if gmes_profile.looks_like_code(answer):
            ui.note(f"'{raw}' looks like a screen code, not R, P or B - this "
                    f"question only chooses Record, Replay or Batch; you will be "
                    f"asked which screen right after.", "warn")
        else:
            ui.note("Type R for Record, P for Replay or B for Batch.", "warn")


def question_screen(q, session, mode="replay", preselected=None):
    """Which screen. Checked against the catalogue BEFORE anything is opened.

    In REPLAY the screens already recorded are listed and can be chosen by
    number, with the most recent as the default - the recordings are kept as
    JSON in `screens/` and never expire, so there is no reason to make anyone
    remember a UI number the tool already knows.

    A number that does not exist used to be accepted, passed to the browser,
    and then killed the whole session with a timeout - so a single typo meant
    starting again from the sign-in. The 809 screens this account can open are
    listed client-side, so a wrong number can be caught in milliseconds and
    the question asked again.

    `preselected` skips the question entirely - the caller (a "View saved
    reports" pick) already knows exactly which screen, and asking again would
    just make the person answer the same thing twice."""
    if preselected:
        return preselected
    saved = gmes_profile.known() if mode == "replay" else []
    if saved and ui.INTERACTIVE:
        # The library itself, with statuses and last results, as the list to
        # move through; typing searches it (HISTORY.md Phase 94.2). A code
        # never saved here, or a search of all of G-MES, is the last line.
        last = library.last_result_by_code(library.run_history())
        cards = [library.report_card(p, last.get(str(p.get("screen", "")).upper()))
                 for p in saved]
        q.asked += 1
        print(f"    {ui.CYAN}{ui.ARROW}{ui.RESET} {q.asked}. Which report?")
        items = report_items(cards) + [_item("Another report - type a code or search G-MES")]
        index = choose(items, "Report", default=0)
        if index < len(cards):
            print(f"      {ui.GREY}{cards[index]['title']}{ui.RESET}")
            return cards[index]["code"]
        saved = []
    if saved:
        # EVERY recording is listed and every one can be picked by number.
        # This used to slice `saved[:9]` - in both the listing and the
        # picker, on the assumption that "a single digit picks" - so with 17
        # screens recorded the last eight were neither shown nor selectable
        # by number, silently (HISTORY.md Phase 82.19). CLAUDE.md 4.6: a cap
        # that hides data is worse than no cap. UI codes always start with a
        # letter, so a run of digits can never be mistaken for one.
        width = len(str(len(saved)))
        print(f"    {ui.GREY}Screens already recorded ({len(saved)}):{ui.RESET}")
        for n, p in enumerate(saved, start=1):
            vals = gmes_profile.last_values(p)
            extra = ", ".join(f"{k}={v}" for k, v in vals.items()
                              if v and k != "sets")
            print(f"      {ui.CYAN}{n:>{width}}{ui.RESET}  {p['screen']:<11} "
                  f"{(p.get('title') or '')[:34]:<34} {ui.GREY}{extra}{ui.RESET}")
        print()

    default = saved[0]["screen"] if saved else ""
    hint = ("a number from the list, a report code, or:  find <words>"
            if saved else "a report code, or:  find <words>")
    first = True
    while True:
        prompt = q.ask if first else q.again
        first = False
        answer = prompt("Which screen?", hint, default=default)
        if not answer:
            ui.note("A screen is needed. Type a report code, or "
                    "'find production plan' to search.", "warn")
            continue

        # A number picks from the list above - any number the list shows.
        if saved and answer.isdigit() and 1 <= int(answer) <= len(saved):
            chosen = saved[int(answer) - 1]
            print(f"      {ui.GREY}{chosen.get('title', '')}{ui.RESET}")
            return chosen["screen"]

        if answer.lower().startswith("find"):
            query = answer[4:].strip()
            if not query:
                ui.note("Try:  find production plan", "warn")
                continue
            found = gmes_open_screen.catalogue(session.get(), query)
            rows = found.get("rows", [])
            if not rows:
                ui.note(f"Nothing matches '{query}' in the "
                        f"{found.get('total')} screens you can open. "
                        f"Try different words.", "warn")
                continue
            print()
            for row in rows[:12]:
                print(f"      {ui.CYAN}{row['screenId']:<12}{ui.RESET} {row['menuTitle']}")
            print()
            ui.note("Type one of those report codes above.")
            continue

        code = answer.upper()
        try:
            found = gmes_open_screen.catalogue(session.get(), code)
        except Exception:
            return code             # catalogue unreadable; let the open try

        rows = found.get("rows", [])
        exact = [r for r in rows
                 if code in (r["screenId"].upper(), r["menuId"].upper())]
        if exact:
            print(f"      {ui.GREY}{exact[0]['menuTitle']}{ui.RESET}")
            return exact[0]["screenId"].upper()

        # Not a real UI number. Say so, offer whatever it did look like, and
        # ask again - never carry on and fail in the browser.
        ui.note(f"There is no screen '{code}'. Please try again.", "warn")
        if rows:
            print(f"      {ui.GREY}Did you mean:{ui.RESET}")
            for row in rows[:6]:
                print(f"        {ui.CYAN}{row['screenId']:<12}{ui.RESET} "
                      f"{row['menuTitle']}")
        else:
            print(f"      {ui.GREY}A report code looks like P1112UM00 or "
                  f"M4151UM00. To search instead, type:  find <words>{ui.RESET}")


def reconcile_mode(mode, code, profile):
    """What was CHOSEN and what is actually TRUE about this screen can
    disagree - REPLAY on a never-seen screen, or RECORD on one already
    learned - and this says so rather than silently doing something else.

    Returns (mode, profile, old_profile, relearning). `old_profile` is the
    discarded profile, kept only so the caller can still offer its values
    back as defaults (`gmes_profile.last_values(old_profile)`) even though
    it is no longer trusted here. `relearning` is True exactly when RECORD
    was chosen over an already-learned screen - the caller's signal to run
    with `trust_profile=False` (see `core.run_screen()`'s docstring,
    HISTORY.md Phase 66).

    This function does NOT delete anything on disk - it used to call
    `gmes_profile.forget(code)` right here, immediately on choosing RECORD,
    before the screen was even opened or anything confirmed. If the run was
    then cancelled, or any later step failed, the old (working) profile was
    already gone with nothing to replace it - the interactive front end's
    own "Cancelled. Nothing was run." message was not quite true; something
    HAD been changed. `trust_profile=False` gets the same practical effect
    (the old profile is not trusted or replayed against) without that risk:
    the old file is only ever superseded by `run_screen()`'s own atomic
    save, on actual success, never pre-emptively deleted on a guess that a
    replacement is coming."""
    old_profile, relearning = None, False
    if mode == "replay" and profile is None:
        ui.note(f"{code} has never been used, so there is nothing to "
                f"replay. Recording it instead.", "warn")
        mode = "record"
    elif mode == "record" and profile is not None:
        ui.note(f"{code} was already learned on {profile.get('learned')}. "
                f"Recording again replaces what it knows.", "warn")
        old_profile, profile, relearning = profile, None, True
    return mode, profile, old_profile, relearning


def show_screen_offer(screen):
    """Everything this screen actually has, before a single question is asked.

    Only shown the FIRST time a screen is used, and it is the whole point of
    the recording phase. Before this, the tool asked for a division and a date
    range on a screen nobody had looked at yet - so a person was being asked
    to name filters they could not see, on a screen that might not even have
    them. Work Calendar has no date fields at all, and was still being asked
    for two dates."""
    info = screen.info
    ui.section("What this screen has")
    ui.field("Name", screen.title)
    ui.field("Report code", f"{screen.code}   (menu {screen.menu_id})")

    try:
        grid, rivals = core.choose_grid(info)
        if grid:
            rows = info["datasets"].get(grid["dataset"], {}).get("rows", "?")
            ui.field("Results in", f"{grid['dataset']}   ({rows} rows on screen now)")
        if rivals:
            ui.note(f"{len(rivals) + 1} result tables are similar in size; the "
                    f"biggest visible one is used.", "warn")
    except Exception:
        ui.note("no result table found on this screen", "warn")

    qv = [v for v in info.get("quickViews", []) if v.get("screen")]
    if len(qv) > 1:
        print()
        print(f"    {ui.BOLD}Quick View{ui.RESET}  {ui.GREY}(each one is a "
              f"DIFFERENT screen, not a filter - this tool will not click "
              f"these; open one directly by typing its own code at the "
              f"'Which screen?' question instead){ui.RESET}")
        for v in qv:
            mark = f"{ui.GREEN}{ui.TICK}{ui.RESET}" if v["active"] else f"{ui.GREY}{ui.DOT}{ui.RESET}"
            tail = "this screen" if v["active"] else f"open {v['screen']} directly to run it"
            print(f"      {mark} {(v['name'] or v['screen']):<26} "
                  f"{ui.GREY}{v['screen']:<12} {tail}{ui.RESET}")

    frm, to, singles = core.date_targets(info)
    dates = [f for f in (frm, to) if f] or singles
    print()
    if dates:
        print(f"    {ui.BOLD}Date fields{ui.RESET}  {ui.GREY}"
              f"(what From/To will be typed into){ui.RESET}")
        for f in dates:
            role = "from" if f is frm else "to" if f is to else "date"
            now = f["value"] or "(empty)"
            print(f"      {ui.CYAN}{role:<5}{ui.RESET} {f['label'] or f['column']:<24} "
                  f"{ui.GREY}{f['column']:<18} now: {now}{ui.RESET}")
    else:
        print(f"    {ui.BOLD}Date fields{ui.RESET}  {ui.GREY}none - this screen "
              f"has no date range, so you will not be asked for one{ui.RESET}")

    others = [f for f in info["filters"] if f["visible"] and f not in dates]
    if others:
        print()
        print(f"    {ui.BOLD}Other filters you can set{ui.RESET}")
        for f in others:
            now = f["value"] or "(empty)"
            print(f"      {ui.CYAN}{ui.DOT}{ui.RESET} {(f['label'] or f['column']):<24} "
                  f"{ui.GREY}{f['column']:<18} now: {now}{ui.RESET}")
    if info["unbound"]:
        ui.section(f"Other inputs ({len(info['unbound'])})")
        ui.note("Not dataset-bound; use --set during RECORD.", "info")
        for unbound in info["unbound"]:
            label = (unbound.get("label") or unbound.get("control")
                     or "(unnamed input)")
            control = unbound.get("control") or "(unknown control)"
            current = unbound.get("value") or "(empty)"
            print(f"      {ui.CYAN}{ui.DOT}{ui.RESET} {label}")
            ui.wrapped_field("Control", control, width=10, indent="        ")
            ui.wrapped_field("Current", current, width=10, indent="        ")
            ui.wrapped_field("Set with", f'--set "{label}=<value>"',
                             width=10, indent="        ")

    try:
        names = sorted({n for t in screen.trees() if t["settable"] for n in t["names"]})
        print()
        if names:
            # ALL of them. This used to stop at 18 and print "...and 14 more",
            # which is the one thing this project has a rule against: a cap
            # that hides part of the answer is worse than no cap (CLAUDE.md
            # 4.5). You cannot choose a division you were not shown.
            print(f"    {ui.BOLD}Divisions available{ui.RESET}  "
                  f"{ui.GREY}({len(names)}){ui.RESET}")
            for chunk in [names[i:i + 5] for i in range(0, len(names), 5)]:
                print(f"      {ui.GREY}{',  '.join(chunk)}{ui.RESET}")
        else:
            print(f"    {ui.BOLD}Divisions{ui.RESET}  {ui.GREY}none - this screen "
                  f"has no organisation list{ui.RESET}")
    except Exception:
        pass

    try:
        opts = [o for o in screen.options() if o["label"].lower() != "inquiry"]
        if opts:
            print()
            print(f"    {ui.BOLD}Left-panel options{ui.RESET}  {ui.GREY}"
                  f"(these change what the query MEANS){ui.RESET}")
            for o in opts:
                on = o["state"] in ("selected", "checked")
                mark = f"{ui.GREEN}{ui.TICK}{ui.RESET}" if on else f"{ui.GREY}{ui.DOT}{ui.RESET}"
                print(f"      {mark} {o['label']:<26} {ui.GREY}{o['state']}{ui.RESET}")
            print(f"      {ui.GREY}e.g. 'Plan Date' vs 'Create Date' changes which "
                  f"date the period means.{ui.RESET}")
    except Exception:
        pass
    print()


def question_division(q, screen, default=""):
    """Ask for a division, but only if the screen has one, and show the
    real choices rather than expecting them to be known.

    `default` is what was used last time on this screen: pressing Enter
    accepts it.

    ALL of them, printed fresh every time this is asked - not the first
    three or four as a "for example". A division not shown here cannot be
    typed from memory, and this tool already has a rule against a cap that
    hides part of the answer (CLAUDE.md 4.5) - the exact same rule that
    made `show_screen_offer` print every division instead of 18-and-a-count.
    This question used to only ever show that full list ONCE, in RECORD;
    someone asked again later, or on a different screen, had nothing to
    look at but 3 names and had to scroll back to see the rest."""
    try:
        names = sorted({n for t in screen.trees() if t["settable"] for n in t["names"]})
    except Exception:
        names = []
    if not names:
        return ""
    if ui.INTERACTIVE:
        # Every division, in a list to move through or search - not a name
        # to remember and type (HISTORY.md Phase 94.3).
        q.asked += 1
        print(f"    {ui.CYAN}{ui.ARROW}{ui.RESET} {q.asked}. Division  "
              f"{ui.GREY}({len(names)} on this screen){ui.RESET}")
        items = [_item("(none - leave the screen's own)")] + [_item(n) for n in names]
        start = next((i + 1 for i, n in enumerate(names)
                      if n.strip().lower() == (default or "").strip().lower()), 0)
        index = choose(items, "Division", default=start)
        return "" if index == 0 else names[index - 1]
    print(f"    {ui.GREY}Divisions available ({len(names)}){ui.RESET}")
    for chunk in [names[i:i + 5] for i in range(0, len(names), 5)]:
        print(f"      {ui.GREY}{',  '.join(chunk)}{ui.RESET}")
    hint = (f"Enter for {default}, or type a name from the list above"
            if default else "a name from the list above, blank = none")
    first = True
    while True:
        prompt = q.ask if first else q.again
        first = False
        answer = prompt("Division", hint, default=default, controls="words")
        if not answer:
            return ""
        if any(answer.strip().lower() == n.strip().lower() for n in names):
            return answer
        near = [n for n in names if answer.strip().lower() in n.lower()]
        ui.note(f"'{answer}' is not on this screen. Please try again."
                + (f" Did you mean: {', '.join(near[:5])}?" if near else ""), "warn")


def question_options(q, screen):
    """Offer the left-panel options, by name, on a screen being learned.

    The names offered here are shown in the hint, the same way `Division`
    shows real division names - a person should never have to scroll back up
    to "What this screen has" to remember what was on offer."""
    try:
        opts = [o for o in screen.options() if o["label"].lower() != "inquiry"]
    except Exception:
        return []
    if not opts:
        return []
    off = [o["label"] for o in opts if o["state"] not in ("selected", "checked")]
    if not off:
        return []
    print(f"    {ui.GREY}These are buttons/checkboxes in the LEFT-SIDE PANEL of "
          f"the actual G-MES screen (the same area as Org/Division) - you do "
          f"NOT need to find or click them yourself in the browser; typing a "
          f"name here clicks it for you. Most runs need none of this - only "
          f"switch one on if this report should specifically mean something "
          f"different, e.g. dates counted by 'Create Date' instead of 'Plan "
          f"Date'.{ui.RESET}")
    if ui.INTERACTIVE:
        q.asked += 1
        print(f"    {ui.CYAN}{ui.ARROW}{ui.RESET} {q.asked}. Any left-panel option to switch on?  "
              f"{ui.GREY}Space ticks, Enter confirms, Esc = none{ui.RESET}")
        pool = [o for o in opts if o["state"] not in ("selected", "checked")]
        try:
            picked = choose([_item(o["label"], o["state"]) for o in pool],
                            "Options", multi=True)
        except GoBack:
            return []
        return [core.option_identity(pool[i]) for i in picked]
    hint = "comma separated, e.g. " + ", ".join(off) + "  -  blank = leave as they are"
    answer = q.ask("Any left-panel option to switch on?", hint, controls="words")
    if not answer:
        return []
    wanted = [part.strip() for part in answer.split(",") if part.strip()]
    known = []
    for want in wanted:
        # A left-panel option is a name, never Name=Value - that is a
        # FILTER, asked for next. Saying so here, rather than just "no
        # option called 'Module Name=NERP'", is what a person typing a real
        # filter into the wrong question actually needs to hear.
        if "=" in want:
            ui.note(f"'{want}' looks like a filter, not a left-panel option "
                    f"to switch on - it can be set at the next question, "
                    f"'Any extra filter?'", "warn")
            continue
        # Resolved through the same matcher the run itself uses, so what is
        # recorded here is the control's STABLE identity rather than the text
        # that happened to be on screen. A screen recorded while G-MES renders
        # Korean therefore replays on one rendering English, and the reverse -
        # which is the whole point of HISTORY.md Phase 76. It also means
        # someone can type either the label they can see or the
        # language-independent key.
        try:
            option, _how = core.resolve_option(opts, want)
        except RuntimeError as e:
            ui.note(str(e), "warn")
            continue
        known.append(core.option_identity(option))
    return known


def question_dates(q, screen=None, defaults=None):
    """Both dates are typed by the person. Nothing is worked out from today's
    date - that is a later feature, deliberately not guessed at now.

    They are validated here, while the keyboard is still in reach: a date
    written straight through unchecked reaches a field that stores YYYYMMDD
    and the query then quietly answers a different question.

    A screen with no date fields is not asked at all. Work Calendar has none,
    and was still being asked for a range that could go nowhere."""
    defaults = defaults or {}
    if screen is not None:
        frm, to, singles = core.date_targets(screen.info)
        if not any((frm, to)) and not singles:
            return None, None

    def one(label, default=""):
        first = True
        while True:
            prompt = q.ask if first else q.again
            first = False
            hint = (f"Enter for {default}, or a new date"
                    if default else "YYYYMMDD or YYYY-MM-DD, blank = leave as-is")
            raw = prompt(label, hint, default=default)
            if not raw:
                return None
            try:
                value = core.normalise_date(raw)
                if value != raw:
                    ui.note(f"read as {value}")
                return value
            except ValueError as e:
                ui.note(f"{e} Please try again.", "warn")

    date_from = one("From date", defaults.get("from", ""))
    if not date_from:
        return None, None
    date_to = one("To date", defaults.get("to", ""))
    if not date_to:
        date_to = date_from
        ui.note(f"no end date given - using {date_to}")
    return date_from, date_to


def filter_candidates(screen):
    """The screen's own settable inputs other than its dates, as
    (key to set, label to show, value now) - what the filter editor offers
    instead of asking for a typed Name=Value (HISTORY.md Phase 94.3)."""
    frm, to, singles = core.date_targets(screen.info)
    dates = [f for f in (frm, to) if f] + list(singles or [])
    out = []
    for f in screen.info.get("filters", []):
        if f.get("visible") and f not in dates:
            key = f.get("label") or f.get("column")
            out.append((key, key, f.get("value") or ""))
    for u in screen.info.get("unbound", []):
        key = u.get("label") or u.get("control")
        if key:
            out.append((key, key, u.get("value") or ""))
    seen, unique = set(), []
    for c in out:
        if c[0] not in seen:
            seen.add(c[0])
            unique.append(c)
    return unique


def filter_editor(q, screen, defaults=None):
    """Pick a filter from the screen's own list, give it a value, repeat;
    'Done' finishes. A blank value removes one. Nothing to type from memory."""
    current = dict(defaults or {})
    candidates = filter_candidates(screen)
    if not candidates:
        return current
    q.asked += 1
    print(f"    {ui.CYAN}{ui.ARROW}{ui.RESET} {q.asked}. Any extra filter?  "
          f"{ui.GREY}most reports need none - pick Done{ui.RESET}")
    while True:
        items = [_item("Done - continue", ", ".join(f"{k} = {v}" for k, v in current.items())
                       or "no extra filters")]
        items += [_item(label, f"will be: {current[key]}" if key in current
                        else (f"now: {now}" if now else "empty"))
                  for key, label, now in candidates]
        try:
            index = choose(items, "Filter", default=0)
        except GoBack:
            return current
        if index == 0:
            return current
        key, label, now = candidates[index - 1]
        # No default: a blank answer must mean "remove", which ask() could
        # not tell apart from "keep" if the current value were the default.
        value = ask(f"{label}", (f"now: {current[key]}  -  " if key in current else "")
                    + "type the value to use, blank removes it", controls="words").strip()
        if value:
            current[key] = value
        else:
            current.pop(key, None)


def question_filters(q, defaults=None, screen=None):
    """Optional. Most runs need nothing here. Given the `screen`, an editor
    over its real filters; without one, the typed Name=Value question."""
    if screen is not None:
        return filter_editor(q, screen, defaults)
    defaults = defaults or {}
    remembered = "; ".join(f"{k}={v}" for k, v in defaults.items())
    answer = q.ask("Any extra filter?",
                   (f"Enter for {remembered}" if remembered else
                    "Name=Value, e.g. Production Order=011074232146, blank = none"),
                   default=remembered, controls="words")
    # Accepting the shown default UNCHANGED must return exactly what was
    # remembered - not re-parse the "A=1; B=2"-joined display string this
    # question can only ever show, not read back. With two or more
    # remembered filters, splitting on the FIRST "=" turned "A=1; B=2" into
    # one filter, key "A", value "1; B=2" - the second filter silently
    # disappeared into the first one's corrupted value. Confirmed by
    # reading the round trip this question makes with itself: the default
    # it offers and the parser it applies to an accepted default were never
    # the same operation.
    if answer == remembered:
        return dict(defaults)
    if not answer or "=" not in answer:
        if answer:
            ui.note("That is not Name=Value - skipping it.", "warn")
        return {}
    key, value = (part.strip() for part in answer.split("=", 1))
    return {key: value} if key and value else {}


# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Batch
# ---------------------------------------------------------------------------

def _ask_until(q, label, hint, default, parse, controls="full"):
    """Ask, and keep asking until `parse(answer)` stops raising ValueError. The
    question keeps its number, like every other re-ask here."""
    first = True
    while True:
        prompt = q.ask if first else q.again
        first = False
        answer = prompt(label, hint, default=default, controls=controls)
        try:
            return parse(answer)
        except ValueError as e:
            ui.note(str(e), "warn")


def parse_batch_dates(text):
    """The date policy as typed -> the normalised policy string. Blank keeps the
    default; a policy `resolve_dates` cannot read raises ValueError."""
    policy = text.strip().lower() or "yesterday"
    gmes_batch.resolve_dates(policy)
    return policy


def parse_batch_action(text):
    words = {"n": "now", "now": "now", "run": "now", "s": "schedule",
             "schedule": "schedule", "v": "save", "save": "save"}
    try:
        return words[text.strip().lower()]
    except KeyError:
        raise ValueError("Type N to run now, S to schedule, V to save the list.") from None


def parse_batch_name(text):
    name = text.strip()
    if not gmes_batch.valid_name(name):
        raise ValueError("A name is letters, digits, - or _ (at most 40).")
    return name


def parse_batch_when(text):
    """'06:30 daily' | '06:30 weekdays' | '06:30 mon,wed,fri' | '06:30 once
    2026-09-25' -> a gmes_schedule.When. Raises ValueError."""
    import gmes_schedule
    parts = text.strip().split(None, 1)
    if not parts:
        raise ValueError("Say a time and how often, e.g. 06:30 daily")
    at = parts[0]
    rest = parts[1].strip().lower() if len(parts) > 1 else "daily"
    if rest == "daily":
        return gmes_schedule.parse_when(at, daily=True)
    if rest in ("weekdays", "weekday"):
        return gmes_schedule.parse_when(at, weekdays=True)
    if rest.startswith("once"):
        return gmes_schedule.parse_when(at, once=rest[4:].strip())
    return gmes_schedule.parse_when(at, days=rest)


LARGE_BATCH_CONFIRM = 10   # a group this size or bigger needs a typed, not a default, confirmation


def batch_flow(q, session):
    """Several recorded screens together: choose them, decide the dates, look at
    the plan, then run it now, put it on a schedule, or just save the list.

    Returns True when something was delivered or set up. The plan is shown - and
    anything that cannot run is said so - BEFORE anything is queried, because a
    batch is many live queries and the person should see the whole of it first
    (HISTORY.md Phase 83)."""
    ui.section("Report group")
    ui.controls_footer()
    profiles = gmes_profile.known()
    if not profiles:
        ui.note("Nothing is recorded yet - record a screen first.", "warn")
        return False
    codes = [p["screen"] for p in profiles]
    saved = gmes_batch.list_batches()

    print(f"    {ui.GREY}Recorded screens ({len(profiles)}):{ui.RESET}")
    width = len(str(len(profiles)))
    for n, p in enumerate(profiles, start=1):
        print(f"      {ui.CYAN}{n:>{width}}{ui.RESET}  {p['screen']:<11} "
              f"{(p.get('title') or '')[:30]:<30} "
              f"{ui.GREY}{gmes_batch.describe_profile(p)}{ui.RESET}")
    if saved:
        print(f"\n    {ui.GREY}Saved lists: "
              + ", ".join(f"@{n} ({len(b['screens'])})" for n, b in saved.items())
              + f"{ui.RESET}")
    print()

    if ui.INTERACTIVE:
        chosen = pick_reports(profiles)
        policy = choose_policy(q)
        return _plan_and_act(q, session, chosen, policy)

    chosen = _ask_until(
        q, "Which screens?",
        "all  |  numbers like 1,3,5-7  |  codes  |  @savedlist  |  !3 to leave one out",
        "all",
        lambda text: gmes_batch.parse_selection(
            text, codes, {n: b["screens"] for n, b in saved.items()}))

    yesterday = friendly_date(gmes_batch.resolve_dates("yesterday")[0])
    policy = _ask_until(
        q, "Which dates?",
        f"yesterday ({yesterday})  |  today  |  -3 (three days back)  |  "
        "20260915  |  keep each screen's own",
        "yesterday", parse_batch_dates)

    return _plan_and_act(q, session, chosen, policy)


def _plan_and_act(q, session, chosen, policy, name=None):
    """Show the plan for `chosen` under `policy`, then run it now, schedule
    it or save it - the tail every way of starting a group shares. `name`
    is an already-saved group's own name (the Report groups screen), so it
    is not asked for again."""
    stamp = time.strftime("%Y%m%d_%H%M%S")
    out_dir = os.path.join(core.OUTPUT_DIR, f"batch_{stamp}")
    plan = gmes_batch.build_plan(chosen, policy, core.DEFAULT_EXPORT, out_dir)
    ready = show_plan(plan, policy) if ui.INTERACTIVE else gmes_batch.print_plan(plan, policy)
    print()
    if not ready:
        ui.note("None of these can run - see the reasons above.", "bad")
        return False

    # A blank Enter through this question, straight after a blank Enter
    # through "Which screens?" (default "all"), used to be enough to start a
    # full live production run of every recorded screen with no confirmation
    # beyond the printed plan - live-caught, 32 screens, 31 runnable, on the
    # owner's own machine. Above the threshold the default is removed
    # (blank Enter re-asks rather than silently picking "now"), and choosing
    # "now" anyway needs the exact ready-count typed on purpose.
    large = ready >= LARGE_BATCH_CONFIRM
    if ui.INTERACTIVE:
        acts = [("now", "Run it now", f"{ready} report(s), Excel files into one new folder"),
                ("schedule", "Save it and schedule it", "a time and how often"),
                ("save", "Save it only", "run or schedule it later from Report groups")]
        print(f"    {ui.CYAN}{ui.ARROW}{ui.RESET} What now?")
        what = acts[choose([_item(t, d) for _k, t, d in acts], "What now?",
                           default=None if large else 0)][0]
    else:
        what = None
    what = what or _ask_until(
        q, "Run it now, schedule it, or just save the list?",
        "N = run now, S = schedule it, V = save the list only"
        + (f"  -  {ready} reports: no default, type one" if large else ""),
        "" if large else "N",
        parse_batch_action)

    if what == "now" and large:
        expected = f"RUN {ready} REPORTS"
        typed = q.ask(
            f"This runs {ready} live reports against production. Type exactly: {expected}",
            "case-sensitive - anything else cancels", controls=False)
        if typed.strip() != expected:
            ui.note("Not confirmed - nothing was run.", "warn")
            return False

    if what == "now":
        results = gmes_batch.run_batch(session.get(), plan)
        counts = gmes_batch.print_summary(results)
        meta = {"started": time.strftime("%Y-%m-%d %H:%M:%S"), "policy": policy,
                "dates": gmes_batch.resolve_dates(policy), "screens": chosen,
                "export": core.DEFAULT_EXPORT, "output_dir": out_dir, "unattended": False,
                "batch": name, "language": _language()}
        _json_path, txt_path = gmes_batch.write_report_safely(results, meta)
        print(f"\n    {ui.GREY}files : {out_dir}"
              + (f"\n    report: {txt_path}" if txt_path else "") + f"{ui.RESET}")
        return counts["ok"] == len(results)

    if name is None:
        name = _ask_until(
            q, "Name this list", "letters, digits, - or _ (e.g. morning)", "morning",
            parse_batch_name)
    gmes_batch.save_batch(name, chosen, policy, core.DEFAULT_EXPORT)
    print(f"    {ui.GREY}saved the list as @{name} - {len(chosen)} screen(s), "
          f"dates {policy}{ui.RESET}\n")
    if what == "save":
        return True
    return _schedule_group(q, name)


def show_plan(plan, policy):
    """The plan as a table that fits the console - which reports, for which
    dates, and what each will do - then the counts. Returns how many can run."""
    date_from, date_to = gmes_batch.resolve_dates(policy)
    period = ("each report's own saved dates" if date_from is None else
              friendly_date(date_from) + ("" if date_from == date_to
                                          else f" to {friendly_date(date_to)}"))
    ui.section(f"Plan - {len(plan)} report(s)")
    print(f"    {ui.GREY}dates: {period}   ·   Excel files only{ui.RESET}\n")
    rows = []
    for n, item in enumerate(plan, start=1):
        if item.blocked:
            what = (f"skipped - {item.blocked}", ui.RED)
        elif item.notes:
            what = ("; ".join(item.notes), ui.YELLOW)
        else:
            what = ("ready", ui.GREEN)
        rows.append((str(n), item.code, item.title, item.dates or "-", what))
    ui.table([("#", 2, False), ("REPORT", 9, False), ("TITLE", 12, True),
              ("DATES", 8, False), ("WHAT HAPPENS", 12, True)], rows)
    ready = sum(1 for i in plan if i.ready)
    print(f"\n    {ui.badge(f'{ready} ready', 'good')}   "
          + (ui.badge(f'{len(plan) - ready} skipped', 'bad') if len(plan) - ready else ""))
    return ready


def _schedule_group(q, name):
    when = _ask_until(
        q, "When should it run?",
        "HH:MM then how often: daily | weekdays | mon,wed,fri | once 2026-09-25   e.g.  06:30 daily",
        "06:30 daily", parse_batch_when)
    import gmes_schedule
    try:
        gmes_schedule.create(name, when)
    except gmes_schedule.ScheduleError as e:
        ui.note(f"Windows would not take the schedule: {e}", "bad")
        return False
    ui.note(f"Scheduled @{name}: {gmes_schedule.describe_when(when)}.", "good")
    print(f"    {ui.GREY}It runs only while you are signed in to Windows - the saved "
          f"credentials\n    and the browser need your session. Pause, run or remove it "
          f"from Schedules\n    on the Home screen.{ui.RESET}")
    return True


# ---------------------------------------------------------------------------
# Choosing from a list - one helper behind every screen (HISTORY.md Phase 94.2)
# ---------------------------------------------------------------------------

def _language():
    try:
        return library.load_settings()["language"]
    except Exception:                                        # noqa: BLE001
        return "en"


def _item(label, detail="", badge=None, search=""):
    return {"label": label, "detail": detail, "badge": badge, "search": search}


def _parse_numbers(text, total):
    """'1,3,5-7' / 'all' -> sorted 0-based indexes; ValueError if any is off."""
    text = text.strip().lower()
    if text in ("all", "*"):
        return list(range(total))
    out = set()
    for part in filter(None, (p.strip() for p in text.split(","))):
        a, _, b = part.partition("-")
        if not a.isdigit() or (b and not b.isdigit()):
            raise ValueError(f"'{part}' is not a number or a range like 5-7")
        lo, hi = int(a), int(b or a)
        if not (1 <= lo <= hi <= total):
            raise ValueError(f"'{part}' is not in the list (1-{total})")
        out.update(range(lo - 1, hi))
    if not out:
        raise ValueError("type numbers like 1,3,5-7, or all")
    return sorted(out)


def choose(items, prompt="Choose", hint="", default=None, multi=False, chosen=(),
           page_size=10, keys=None):
    """Pick from `items` (dicts from `_item()`): an index, or with `multi`
    a list of indexes. Esc - or a blank answer with no default - raises
    GoBack, so every list can be left the same way.

    Interactive console: an arrow-key menu with type-to-search (gmes_ui.Menu).
    Anywhere else - a script, a pipe, GMES_PLAIN=1, every offline test - the
    same list numbered, ten a page with the total always shown, where a
    number picks, N/P turn the page, words narrow the list, * shows all."""
    items = list(items)
    if ui.INTERACTIVE:
        menu = ui.Menu(items, multi=multi, height=min(12, max(3, len(items))),
                       chosen=chosen, start=default or 0)
        pairs = keys or ((("↑↓", "move"), ("Enter", "confirm"), ("Space", "tick"),
                          ("*", "all"), ("type", "search"), ("Esc", "back")) if multi else
                         (("↑↓", "move"), ("Enter", "open"), ("type", "search"),
                          ("Esc", "back"), ("Ctrl+C", "quit")))
        result = ui.run_menu(menu, pairs)
        if result[0] == "back":
            raise GoBack()
        return result[1]

    shown, page, words = list(range(len(items))), 0, ""
    ticked = set(chosen)
    while True:
        visible, first, end, total, page, pages = library.page_of(shown, page, page_size)
        heading = (f"{total} of {len(items)} match '{words}'" if words
                   else f"{len(items)} to choose from")
        print(f"\n    {ui.GREY}{heading}"
              + (f"  -  showing {first}-{end}, page {page + 1} of {pages}" if pages > 1 else "")
              + f"{ui.RESET}")
        width = len(str(len(items)))
        for i in visible:
            it = items[i]
            tick = ""
            if multi:
                tick = f"{ui.GREEN}{ui.CHECK_ON}{ui.RESET} " if i in ticked else f"{ui.GREY}{ui.CHECK_OFF}{ui.RESET} "
            b = f"  {ui.badge(*it['badge'])}" if it.get("badge") else ""
            print(f"    {ui.CYAN}{i + 1:>{width}}{ui.RESET}  {tick}{it['label']}{b}")
            if it.get("detail"):
                ui.wrapped_field("", it["detail"], width=0, indent=" " * (width + 6 + (4 if multi else 0)))
        if not visible:
            ui.note("Nothing matches. Type other words, or * to show everything.", "info")
        print()
        paging = "  N next page, P previous" if pages > 1 else ""
        default_text = "" if default is None else str(default + 1)
        answer = ask(prompt + paging,
                     hint or ("numbers like 1,3,5-7 or all" if multi else
                              "a number, or words to search") + (
                         "" if default is not None else "  -  Enter goes back"),
                     default=default_text,
                     help_text="Type a number to pick it, or part of a name to "
                               "narrow the list. * shows everything again.").strip()
        low = answer.lower()
        if not answer:
            raise GoBack()
        if low == "n" and pages > 1:
            page += 1
        elif low == "p" and pages > 1:
            page -= 1
        elif multi and (low in ("all", "*") and not words or
                        any(ch.isdigit() for ch in answer) and
                        all(ch.isdigit() or ch in ",- " for ch in answer)):
            try:
                return _parse_numbers(answer, len(items))
            except ValueError as e:
                ui.note(str(e), "warn")
        elif answer.isdigit():
            if 1 <= int(answer) <= len(items):
                return int(answer) - 1
            ui.note(f"{answer} is not in the list.", "warn")
        elif answer == "*":
            shown, page, words = list(range(len(items))), 0, ""
        else:
            words = answer
            shown, page = [i for i in range(len(items))
                           if not library.search([_card_text(items[i])], words) == []], 0


def _card_text(item):
    return {"code": item.get("label", ""), "title": item.get("detail", ""),
            "settings": item.get("search", "")}


def _pause(text="Press Enter to go back"):
    try:
        ask(text, "")
    except (GoBack, TaskCancelled):
        pass


# ---------------------------------------------------------------------------
# Home
# ---------------------------------------------------------------------------

_HOME_ACTIONS = {
    "1": "run_saved", "run": "run_saved", "run a saved report": "run_saved",
    "2": "new_report", "new": "new_report", "set up a new report": "new_report",
    "3": "report_group", "group": "report_group", "groups": "report_group",
    "several": "report_group", "run several reports": "report_group",
    "4": "saved_reports", "saved": "saved_reports", "view saved reports": "saved_reports",
    "5": "schedules", "schedule": "schedules", "schedules": "schedules",
    "view schedules": "schedules",
    "6": "recent", "recent": "recent", "files": "recent", "history": "recent",
    "7": "settings", "settings": "settings",
    "8": "help", "guide": "help",
}

# (number, text, what it is for, what it needs) - the last column is the whole
# point of the Home screen coming before sign-in (CLI_UI_IMPROVEMENT_PLAN.md
# U1): a person can see which choices will sign in to G-MES and which not.
HOME_ITEMS = (
    ("1", "Run a saved report", "pick one, check it, run it", "signs in"),
    ("2", "Set up a new report", "any G-MES screen, step by step", "signs in"),
    ("3", "Run several reports (report groups)", "new, edit, run, schedule", "signs in to run"),
    ("4", "Saved reports - search, status, last result", "the library", "no sign-in"),
    ("5", "View schedules - run now, pause, remove", "what runs by itself", "no sign-in"),
    ("6", "Recent runs and files", "results, folders, summaries", "no sign-in"),
    ("7", "Settings and support", "language, display, help package", "no sign-in"),
    ("8", "Help - how do I...?", "short answers by task", "no sign-in"),
)
_HOME_KEYS = [n for n, *_ in HOME_ITEMS]


def home_status_lines(known=None, last=None):
    """The lines above the menu: what is saved, and how the last run went
    (HISTORY.md Phase 93). Pure, so the wording has a test."""
    known = gmes_profile.known() if known is None else known
    lines = [f"{len(known)} saved report(s)" if known else
             "No saved reports yet - start with 2, Set up a new report"]
    batch = last_batch_line(last) if last is not None else None
    if batch:
        lines.append(batch)
    return lines


def home_menu():
    """The main menu - shown before any sign-in, before any browser. Blank
    Enter picks 'Run a saved report' once something is saved, or 'Set up a
    new report' on a machine with nothing saved yet.

    Typing 'q'/'quit'/'exit' closes the tool - handled by ask() itself
    (QuitRequested), the same global mechanism every other prompt uses. In
    the arrow-key interface, Esc or the Exit line does the same."""
    known = gmes_profile.known()
    default = 0 if known else 1
    if ui.INTERACTIVE:
        try:
            last = dict(gmes_batch.last_summary() or {})
            last.pop("summary", None)           # the path lives in Recent runs, not here
            status = "  ·  ".join(home_status_lines(known, last))
        except Exception:                                    # noqa: BLE001
            status = ""
        ui.screen("What would you like to do?", status=status)
        items = [_item(text, f"{what}  ·  {needs}", search=needs)
                 for _n, text, what, needs in HOME_ITEMS]
        items.append(_item("Exit", "close the tool"))
        try:
            index = choose(items, default=default)
        except GoBack:
            raise QuitRequested()
        if index == len(HOME_ITEMS):
            raise QuitRequested()
        return _HOME_ACTIONS[HOME_ITEMS[index][0]]

    ui.section("Home - what would you like to do?")
    for number, text, _what, needs in HOME_ITEMS:
        print(f"    {ui.CYAN}{number}{ui.RESET}. {text:<46}{ui.GREY}{needs}{ui.RESET}")
    print(f"    {ui.CYAN}Q{ui.RESET}. Exit\n")
    while True:
        answer = ask("Choose", "a number from the list above, or Q to exit",
                     default=_HOME_KEYS[default])
        action = _HOME_ACTIONS.get(answer.strip().lower())
        if action:
            return action
        ui.note("Choose a number from the list above, or Q to exit.", "warn")


# ---------------------------------------------------------------------------
# Saved reports - the library
# ---------------------------------------------------------------------------

_STATUS_KIND = {library.READY: "good", library.READY_WARN: "warn",
                library.LAST_FAILED: "bad", library.NOT_TRIED: "dim"}


def _status(text):
    return ui.badge(text, _STATUS_KIND.get(text, "dim"))


def report_items(cards):
    return [_item(f"{c['code']:<11} {c['title']}",
                  f"{c['settings']}; {c['why'].rstrip('.')}"
                  + (f"; {c['last_run']}" if c["last_run"] else ""),
                  badge=(c["status"], _STATUS_KIND.get(c["status"], "dim")),
                  search=c["status"])
            for c in cards]


def show_saved_reports(session):
    """The saved-report library, offline - no sign-in, no browser touched,
    until a report is actually picked to run.

    Each report shows a status in plain words (Ready / Ready with warning /
    Last run failed / Not yet run here) with the reason, its remembered
    settings and its last result (HISTORY.md Phase 93). Picking one opens it:
    run it, or back."""
    ui.screen("Saved reports", ("Saved reports",),
              "Every report set up on this PC. Pick one to run it.")
    profiles = gmes_profile.known()
    if not profiles:
        ui.note("Nothing is set up yet. Choose 'Set up a new report' from "
                "the main menu.", "info")
        return
    gmes_batch.warn_unreadable()
    last = library.last_result_by_code(library.run_history())
    cards = [library.report_card(p, last.get(str(p.get("screen", "")).upper()))
             for p in profiles]
    try:
        index = choose(report_items(cards), "Type a number to run it, words to search")
    except GoBack:
        return
    one_run(session, preset_mode="replay", preselected_code=cards[index]["code"])


# ---------------------------------------------------------------------------
# Recent runs and files
# ---------------------------------------------------------------------------

def show_recent_runs():
    """Past runs and their files, offline, newest first (HISTORY.md Phase 93).
    Picking one shows every report in it - what happened in plain words, the
    files it made - and offers to open its summary page or its folder."""
    while True:
        ui.screen("Recent runs and files", ("Recent runs",),
                  "Newest first. Pick a run to see its reports and open its files.")
        history = library.run_history()
        if not history:
            ui.note("Nothing has been run yet on this PC.", "info")
            return
        items = []
        for run in history:
            c = run["counts"]
            bad = run["total"] - c.get("ok", 0)
            items.append(_item(
                f"{run['started']:<20} {(run['name'] or 'reports')[:28]}",
                f"{c.get('ok', 0)} of {run['total']} delivered",
                badge=(("all delivered", "good") if not bad else
                       (f"{bad} need attention", "bad"))))
        try:
            index = choose(items, "Type a number to see that run")
        except GoBack:
            return
        show_run(history[index])


def show_run(run):
    ui.screen(f"Run of {run['started']}", ("Recent runs", run["started"]),
              run["name"] or "")
    for w in run["warnings"]:
        ui.note(w, "warn")
    for r in run["results"]:
        ok = r.get("status") == "ok"
        label = {"ok": "delivered", "failed": "FAILED", "not_run": "not run",
                 "blocked": "skipped"}.get(r.get("status"), r.get("status"))
        mark = f"{ui.GREEN}{ui.TICK}{ui.RESET}" if ok else f"{ui.RED}{ui.CROSS}{ui.RESET}"
        print(f"    {mark} {r.get('screen', '?'):<11} {label:<10} "
              + (f"{int(r.get('rows') or 0):,} rows" if ok else ""))
        if ok:
            for f in r.get("files") or []:
                print(f"        {ui.GREY}{os.path.basename(f)}{ui.RESET}")
        else:
            what, do = library.explain_error(r.get("error") or "")
            ui.wrapped_field("", what, width=0, indent="        ")
            ui.wrapped_field("", f"What to do: {do}", width=0, indent="        ")
    folder = run["output_dir"] or next(
        (os.path.dirname(f) for r in run["results"] for f in (r.get("files") or [])), "")
    choices = []
    if run["summary"]:
        choices.append(("Open the summary page", run["summary"]))
    if folder:
        choices.append(("Open the folder with the files", folder))
    if not choices:
        _pause()
        return
    try:
        index = choose([_item(text) for text, _p in choices], "Choose", "Enter goes back")
    except GoBack:
        return
    ok, message = library.open_path(choices[index][1])
    ui.note(message, "info" if ok else "warn")


# ---------------------------------------------------------------------------
# Help
# ---------------------------------------------------------------------------

def show_help():
    """Help by task, offline (HISTORY.md Phase 93)."""
    topics = library.HELP_TOPICS
    while True:
        ui.screen("Help - how do I...?", ("Help",))
        try:
            index = choose([_item(title) for title, _t in topics], "Type a number to read it")
        except GoBack:
            return
        title, text = topics[index]
        print(f"\n    {ui.BOLD}{title}{ui.RESET}")
        ui.wrapped_field("", text, width=0, indent="      ")
        print()
        if ui.INTERACTIVE:
            _pause()


# ---------------------------------------------------------------------------
# Schedules - list, run now, pause/resume, remove (HISTORY.md Phase 94.4)
# ---------------------------------------------------------------------------

def show_schedules():
    """Task Scheduler only - no G-MES, no sign-in, no browser. A schedule
    can be started now, paused, resumed or removed; the saved report group
    itself is always kept."""
    import gmes_schedule
    ui.screen("Schedules", ("Schedules",),
              "Report groups that run by themselves. They run only while you are "
              "signed in to Windows.")
    try:
        tasks = gmes_schedule.list_tasks()
    except gmes_schedule.ScheduleError as e:
        ui.note(str(e), "bad")
        return
    if not tasks:
        ui.note("Nothing is scheduled. Make a report group (3), then choose "
                "'schedule' for it.", "info")
        return
    items = []
    for t in tasks:
        paused = str(t.get("state", "")).lower() == "disabled"
        last = t.get("last_text") or "never run"
        items.append(_item(
            f"@{t['batch']:<18} {t.get('trigger', '')}",
            f"next {t.get('next_run') or '-'}  ·  last {t.get('last_run') or '-'}: {last}",
            badge=(("paused", "dim") if paused else
                   ("last run ok", "good") if t.get("last_ok") else
                   ("never run", "dim") if t.get("last_ok") is None else
                   ("last run failed", "bad"))))
    try:
        index = choose(items, "Type a number to manage that schedule")
    except GoBack:
        return
    chosen = tasks[index]
    paused = str(chosen.get("state", "")).lower() == "disabled"
    actions = [("run", "Run it now", "starts it in the background, like at its time"),
               ("resume" if paused else "pause",
                "Resume it" if paused else "Pause it",
                "keeps it, skips its runs until resumed" if not paused else "runs again at its times"),
               ("remove", "Remove the schedule", "the report group itself is kept")]
    print(f"\n    {ui.BOLD}@{chosen['batch']}{ui.RESET}")
    try:
        pick = choose([_item(text, detail) for _k, text, detail in actions],
                      "What should happen?")
    except GoBack:
        return
    key = actions[pick][0]
    try:
        if key == "run":
            gmes_schedule.run_now(chosen["batch"])
            ui.note(f"Started @{chosen['batch']}. Its result appears in Recent runs "
                    "when it finishes.", "info")
        elif key in ("pause", "resume"):
            gmes_schedule.set_enabled(chosen["batch"], key == "resume")
            ui.note(f"{'Resumed' if key == 'resume' else 'Paused'} @{chosen['batch']}.", "info")
        else:
            confirm = ask(f"Remove the schedule for '{chosen['batch']}'?",
                          "the saved report group is kept - type y to confirm, "
                          "anything else cancels")
            if confirm.strip().lower() != "y":
                ui.note("Not removed.", "info")
                return
            removed = gmes_schedule.delete(chosen["batch"])
            ui.note(f"Removed the schedule for '{chosen['batch']}'." if removed
                    else f"Nothing was scheduled for '{chosen['batch']}'.", "info")
    except (ValueError, gmes_schedule.ScheduleError) as e:
        ui.note(str(e), "bad")


# ---------------------------------------------------------------------------
# Report groups - new, run, edit, dates, rename, schedule, delete
# (HISTORY.md Phase 94.3)
# ---------------------------------------------------------------------------

DATE_CHOICES = (("yesterday", "Yesterday"), ("today", "Today"),
                ("-7", "7 days back (one day, a week ago)"),
                ("keep", "Each report's own saved dates"),
                ("other", "Another date or range..."))


def choose_policy(q, current="yesterday"):
    """The dates a group runs for, as a pick list with the real date shown."""
    items = []
    for key, text in DATE_CHOICES:
        detail = ""
        if key not in ("keep", "other"):
            frm, to = gmes_batch.resolve_dates(key)
            detail = friendly_date(frm) + (f" to {friendly_date(to)}" if to != frm else "")
        items.append(_item(text, detail))
    keys = [k for k, _t in DATE_CHOICES]
    start = keys.index(current) if current in keys else len(keys) - 1
    print(f"    {ui.CYAN}{ui.ARROW}{ui.RESET} Which dates?")
    index = choose(items, "Which dates?", default=start)
    if keys[index] != "other":
        return keys[index]
    return _ask_until(q, "Which dates?",
                      "20260915  |  20260901:20260910  |  -3 (three days back)",
                      current if current not in keys else "", parse_batch_dates)


def pick_reports(profiles, chosen_codes=()):
    """Tick reports from the saved ones -> their codes, in list order."""
    last = library.last_result_by_code(library.run_history())
    cards = [library.report_card(p, last.get(str(p.get("screen", "")).upper()))
             for p in profiles]
    ticked = [i for i, c in enumerate(cards) if c["code"] in set(chosen_codes)]
    print(f"    {ui.CYAN}{ui.ARROW}{ui.RESET} Which reports?  {ui.GREY}"
          + ("Space ticks one, * ticks all, Enter confirms" if ui.INTERACTIVE
             else "numbers like 1,3,5-7, or all") + f"{ui.RESET}")
    indexes = choose(report_items(cards), "Which reports?", multi=True, chosen=ticked)
    return [cards[i]["code"] for i in indexes]


def groups_center(session):
    """Every saved report group, plus making a new one or running a few
    reports once. Returns True when anything that ran delivered."""
    ok = True
    while True:
        ui.screen("Report groups", ("Report groups",),
                  "A group is a saved list of reports with its dates - run it now "
                  "or on a schedule.")
        profiles = gmes_profile.known()
        if not profiles:
            ui.note("Nothing is set up yet - set up a report first (2).", "warn")
            return False
        groups = gmes_batch.list_batches()
        names = sorted(groups)
        items = [_item("+ Run some reports once", "pick them now, nothing is saved"),
                 _item("+ New group", "pick reports and dates, then save, run or schedule")]
        for n in names:
            g = groups[n]
            items.append(_item(f"@{n}", f"{len(g.get('screens', []))} report(s)  ·  "
                                        f"dates {g.get('date', 'yesterday')}"))
        try:
            index = choose(items, "Choose")
        except GoBack:
            return ok
        q = Questions()
        try:
            if index == 0:
                ok = batch_flow(q, session) and ok
            elif index == 1:
                codes = pick_reports(profiles)
                policy = choose_policy(q)
                ok = _plan_and_act(q, session, codes, policy) and ok
            else:
                ok = group_actions(q, session, names[index - 2], groups[names[index - 2]],
                                   profiles) and ok
        except GoBack:
            continue


def group_actions(q, session, name, group, profiles):
    codes = list(group.get("screens", []))
    policy = group.get("date", "yesterday")
    ui.screen(f"@{name}", ("Report groups", f"@{name}"),
              f"{len(codes)} report(s): {', '.join(codes)}  ·  dates {policy}")
    actions = [("run", "Run it now", "shows the plan first"),
               ("edit", "Change its reports", "tick or untick"),
               ("dates", "Change its dates", f"now: {policy}"),
               ("schedule", "Schedule it", "a time and how often"),
               ("rename", "Rename it", ""),
               ("delete", "Delete it", "its schedule is removed too")]
    index = choose([_item(t, d) for _k, t, d in actions], "What should happen?")
    key = actions[index][0]
    if key == "run":
        return _plan_and_act(q, session, codes, policy, name=name)
    if key == "edit":
        codes = pick_reports(profiles, codes)
        gmes_batch.save_batch(name, codes, policy, core.DEFAULT_EXPORT)
        ui.note(f"@{name} now has {len(codes)} report(s).", "info")
    elif key == "dates":
        policy = choose_policy(q, policy)
        gmes_batch.save_batch(name, codes, policy, core.DEFAULT_EXPORT)
        ui.note(f"@{name} now runs for {policy}.", "info")
    elif key == "schedule":
        return _schedule_group(q, name)
    elif key == "rename":
        new = _ask_until(q, "New name", "letters, digits, - or _", "", parse_batch_name)
        if _is_scheduled(name):
            ui.note(f"@{name} is scheduled - remove its schedule first (Schedules), "
                    "so the timer does not point at a name that no longer exists.", "warn")
            return False
        gmes_batch.save_batch(new, codes, policy, core.DEFAULT_EXPORT)
        gmes_batch.delete_batch(name)
        ui.note(f"Renamed @{name} to @{new}.", "info")
    elif key == "delete":
        confirm = ask(f"Delete the group @{name}?", "type y to confirm, anything else cancels")
        if confirm.strip().lower() != "y":
            ui.note("Not deleted.", "info")
            return True
        if _is_scheduled(name):
            import gmes_schedule
            try:
                gmes_schedule.delete(name)
            except gmes_schedule.ScheduleError as e:
                ui.note(f"Its schedule could not be removed ({e}) - nothing deleted.", "bad")
                return False
        gmes_batch.delete_batch(name)
        ui.note(f"Deleted @{name}.", "info")
    return True


def _is_scheduled(name):
    import gmes_schedule
    try:
        return any(t.get("batch", "").lower() == name.lower() for t in gmes_schedule.list_tasks())
    except gmes_schedule.ScheduleError:
        return False


# ---------------------------------------------------------------------------
# Settings and support (HISTORY.md Phase 94.5-94.6)
# ---------------------------------------------------------------------------

def show_settings():
    while True:
        current = library.load_settings()
        ui.screen("Settings and support", ("Settings",))
        rows = [
            ("language", "Summary page language",
             "English" if current["language"] == "en" else "العربية  (Arabic)"),
            ("plain", "Screen style",
             "simple numbered lists" if current["plain"] else "arrow-key menus where possible"),
            ("open_folder_after", "After a report arrives",
             "open its folder by itself" if current["open_folder_after"] else "ask first"),
            ("support", "Make a support package",
             "a ZIP for whoever helps you - no passwords, no data files"),
        ]
        try:
            index = choose([_item(label, "Enter changes it" if k != "support" else value,
                                  badge=None if k == "support" else (value, "info"))
                            for k, label, value in rows], "Change which?")
        except GoBack:
            return
        key = rows[index][0]
        if key == "support":
            try:
                path = library.support_package()
            except Exception as e:                           # noqa: BLE001
                ui.note(f"The package could not be made: {e}", "bad")
                continue
            ui.note(f"Saved: {path}", "info")
            ok, message = library.open_path(os.path.dirname(path))
            if not ok:
                ui.note(message, "info")
            _pause()
            continue
        if key == "language":
            value = "ar" if current["language"] == "en" else "en"
        else:
            value = not current[key]
        saved = library.save_settings({key: value})
        if key == "plain":
            ui.set_plain(saved["plain"])
        ui.note("Saved.", "info")


def sign_in_visibly():
    """Sign in, showing every step as it happens.

    An earlier version captured the sign-in output and printed one tidy line
    at the end. Signing in can take a minute - waiting on the SSO window, then
    G-MES's own login form - and during all of it the screen said nothing but
    "Signing in to G-MES...". The user reported it as frozen, and they were
    right to: a program that shows nothing for a minute IS frozen as far as
    anyone watching can tell.

    So nothing is hidden. It is less tidy and it is honest, and the moment
    something goes wrong the reason is already on screen."""
    ui.section("Connection")
    print(f"    {ui.GREY}Signing in. This can take up to a minute if the "
          f"session has expired.{ui.RESET}\n")
    try:
        ok = core.sign_in()
    except Exception as e:
        ui.note(f"Could not sign in: {e}", "bad")
        return False
    if not ok:
        ui.note("Could not sign in. Nothing was run.", "bad")
    return ok


def last_batch_line(last=None):
    """One line about the most recent batch - normally last night's scheduled
    one - for the start-up screen, or None when there has not been one
    (HISTORY.md Phase 92.7). The morning summary page is named so it can be
    opened straight away; nothing here signs in or touches a browser."""
    last = last if last is not None else gmes_batch.last_summary()
    if not last:
        return None
    c, total = last["counts"], last["total"]
    missing = total - c.get("ok", 0)
    verdict = (f"all {total} delivered" if not missing else
               f"{c.get('ok', 0)} of {total} delivered, {missing} need attention")
    line = f"last run {last['started']}: {verdict}"
    if last.get("summary"):
        line += f"  -  summary: {last['summary']}"
    return line


def main():
    log_path = gmes_log.start("run_gmes_workflow (interactive)")
    try:
        ui.set_plain(library.load_settings()["plain"])
    except Exception:                                        # noqa: BLE001
        pass                            # a preference must never stop the tool
    ui.banner("G-MES REPORT ASSISTANT", "Ready to prepare reports")
    print(f"  {ui.GREY}log: {log_path}{ui.RESET}")
    try:
        lines = home_status_lines(last=gmes_batch.last_summary() or {})
    except Exception:                                        # noqa: BLE001
        lines = []                      # a start-up nicety must never stop the tool
    for line in lines:
        print(f"  {ui.GREY}{line}{ui.RESET}")

    # The main menu shows first - no sign-in, no browser, until something is
    # actually chosen that needs one. Viewing saved reports or schedules
    # needs neither; Session defers both to that first real need.
    session = Session()
    runs, groups, ok = 0, 0, True
    try:
        while True:
            try:
                action = home_menu()
            except (QuitRequested, KeyboardInterrupt):
                break
            except (GoBack, TaskCancelled):
                continue                            # nowhere further back than Home
            except InputClosed:
                break

            try:
                if action == "saved_reports":
                    show_saved_reports(session)
                elif action == "schedules":
                    show_schedules()
                elif action == "recent":
                    show_recent_runs()
                elif action == "help":
                    show_help()
                elif action == "settings":
                    show_settings()
                elif action == "report_group":
                    groups += 1
                    ok = groups_center(session) and ok
                else:
                    runs += 1
                    preset = "replay" if action == "run_saved" else "record"
                    # AND-accumulated, not overwritten: `ok` used to be
                    # whatever the LAST report returned, so a session with one
                    # failed report followed by one successful one exited 0 -
                    # a script or scheduled task checking the exit code would
                    # never learn the first report had failed at all.
                    ok = one_run(session, preset_mode=preset) and ok
            except (QuitRequested, KeyboardInterrupt):
                # Quitting part-way through a question is not a report run -
                # the closing line used to count it as one (HISTORY.md Phase 93).
                if action in ("run_saved", "new_report"):
                    runs -= 1
                elif action == "report_group":
                    groups -= 1
                break
            except (GoBack, TaskCancelled):
                # This iteration's own `runs`/`groups += 1` counted an
                # attempt abandoned before it delivered anything - InputClosed
                # below has always drawn the same distinction ("the session
                # is ending, not this report"); an explicit cancel deserves
                # the same courtesy.
                if action in ("run_saved", "new_report"):
                    runs -= 1
                elif action == "report_group":
                    groups -= 1
                continue
            except SignInFailed as e:
                ui.note(str(e), "bad")
                if action in ("run_saved", "new_report"):
                    runs -= 1
                elif action == "report_group":
                    groups -= 1
                continue
            except core.RunLocked as e:
                ui.note(str(e), "bad")
                if action in ("run_saved", "new_report"):
                    runs -= 1
                elif action == "report_group":
                    groups -= 1
                continue
            except InputClosed:
                # stdin ran out mid-task, before anything was opened or
                # attempted - not a completed attempt, same reasoning as
                # every re-ask loop elsewhere in this file.
                if action in ("run_saved", "new_report"):
                    runs -= 1
                elif action == "report_group":
                    groups -= 1
                break

        print(f"\n  {ui.GREY}{runs} report(s), {groups} report group run(s) this "
              f"session. Files are in {core.OUTPUT_DIR}{ui.RESET}")
        print(f"  {ui.GREY}log: {gmes_log.path()}{ui.RESET}")
        gmes_log.finish(f"{runs} report(s), {groups} group(s), all ok={ok}")
        return 0 if ok else 1
    finally:
        session.close()


def record_single_run(code, r, date_from, date_to, folder, write=None):
    """Add a single report's outcome to the run history, so 'Recent runs and
    files' and the saved-report statuses cover it too, not only report
    groups (HISTORY.md Phase 93). Best effort - never the run's outcome."""
    started = time.strftime("%Y-%m-%d %H:%M:%S")
    dates = (f"{date_from}" + (f"..{date_to}" if date_to and date_to != date_from else "")
             if date_from else "")
    result = gmes_batch._result(code, "ok" if r.get("ok") else "failed",
                                rows=r.get("rows", 0), files=list(r.get("files") or []),
                                error=r.get("error"), warnings=list(r.get("warnings") or []),
                                title=r.get("title", ""), dates=dates)
    meta = {"started": started, "batch": f"single report {code}", "policy": dates or "screen's own",
            "language": _language(),
            "screens": [code], "output_dir": folder}
    (write or gmes_batch.write_report_safely)([result], meta, log=lambda *_a: None)


def confirm_saved_setup(ws, code, run=None):
    """After a new report's first successful run, offer the one check that
    proves it is really saved: run it again from the saved setup alone - no
    answers typed - and see it deliver (the owner's standing rule,
    CLAUDE.md 4.1a; HISTORY.md Phase 94.3). Recommended, never forced."""
    try:
        index = choose([_item("Run it once more from the saved setup",
                              "recommended - proves the report will run by itself"),
                        _item("Not now", "it is saved either way")],
                       "Check the saved setup?", default=0)
    except (GoBack, TaskCancelled):
        return None
    if index != 0:
        return None
    profile = gmes_profile.load(code) or {}
    last = gmes_profile.last_values(profile)
    ui.section("Checking the saved setup")
    results = (run or core.run_many)(ws, [{
        "screen_code": code, "division": last.get("division") or None,
        "date_from": last.get("from") or None, "date_to": last.get("to") or None,
        "sets": dict(last.get("sets") or {}), "options": [],
        "verify": last.get("verify") if last.get("from") else None,
        "export": None, "out_dir": None, "trust_profile": True}], log=Narrator())
    r = results[0]
    if r["ok"]:
        ui.result(True, f"SAVED SETUP CONFIRMED  {ui.DOT}  {r['rows']:,} rows",
                  [f"{ui.GREY}{code} will now run by itself - alone, in a group or "
                   f"on a schedule.{ui.RESET}"])
    else:
        what, do = library.explain_error(r.get("error") or "")
        ui.result(False, "THE SAVED SETUP DID NOT RUN BY ITSELF",
                  [what, f"{ui.CYAN}What to do:{ui.RESET} {do}", "",
                   f"{ui.GREY}{r.get('error') or ''}{ui.RESET}"])
    return bool(r["ok"])


def after_success(files, folder):
    """The next useful thing after a report arrives (CLI plan U9): open the
    Excel file, open the folder, or go Home. Enter goes Home - never into
    another run by surprise."""
    if folder and library.load_settings().get("open_folder_after"):
        ok, message = library.open_path(folder)
        ui.note(message, "info" if ok else "warn")
        return
    excel = next((f for f in files if f.lower().endswith(".xlsx")), None)
    choices = ([("Open the Excel file", excel)] if excel else []) + \
              ([("Open the folder", folder)] if folder else [])
    if not choices:
        return
    print()
    for n, (text, _path) in enumerate(choices, start=1):
        print(f"    {ui.CYAN}{n}{ui.RESET}. {text}")
    try:
        answer = ask("Choose", "Enter goes back to Home").strip()
    except (GoBack, TaskCancelled):
        return
    if answer.isdigit() and 1 <= int(answer) <= len(choices):
        ok, message = library.open_path(choices[int(answer) - 1][1])
        ui.note(message, "info" if ok else "warn")


def one_run(session, preset_mode=None, preselected_code=None):
    """One report, start to finish. Returns True if it delivered files.

    Nothing here exits the program. A wrong UI number, a cancelled run or a
    failed query all come back here so the next question can be asked - the
    tool used to close on any of them, which meant signing in again to fix a
    typo.

    `preset_mode` skips the Record/Replay/Batch question - the main menu's
    "Run a saved report"/"Set up a new report" items already say which one is
    meant. `preselected_code` additionally skips the "Which screen?" question,
    for a report chosen from "View saved reports"."""
    ws = None    # not yet connected - question_mode()/question_screen() need no browser
    try:
        if ui.INTERACTIVE:
            title = {"replay": "Run a saved report", "record": "Set up a new report"}.get(
                preset_mode, "Run a report")
            ui.screen(title, (title,),
                      "Pick the report; everything it needs is asked here, before "
                      "anything runs. Esc goes back.")
        else:
            ui.section("What do you want?")
            ui.controls_footer()
        print()
        q = Questions()
        mode = preset_mode or question_mode(q)
        if mode == "batch":
            return batch_flow(q, session)
        code = question_screen(q, session, mode, preselected=preselected_code)
        mode, profile, old_profile, relearning = reconcile_mode(mode, code, gmes_profile.load(code))

        # session.get() signs in and connects on the FIRST call in this
        # process's life; every later call (including the run_many() call
        # near the end of this function) just returns the same connection.
        ws = session.get()

        # The screen is opened BEFORE the rest of the questions, so they can
        # be about what it really has. Asked blind, the tool once wanted two
        # dates for a screen with no date fields, and a division for one with
        # no organisation list.
        print(f"    {ui.GREY}Opening {code} to see what it offers...{ui.RESET}")
        try:
            screen = core.open_screen(ws, code, log=lambda *_a, **_k: None)
        except RuntimeError as e:
            ui.note(f"{str(e)} Please try again.", "bad")
            return False

        ui.phase(mode == "record", code, (profile or {}).get("learned", ""))
        confirmed = False

        # What was used last time on this screen, offered back as the
        # defaults. Recording is supposed to mean not typing it all again;
        # remembering only the field NAMES and forgetting the values left the
        # user re-entering everything on a screen the tool "knew". A
        # re-record (profile just nulled above) still has a defaults source
        # in old_profile - without it, choosing RECORD on an already-learned
        # screen silently threw away every value that screen had proven.
        last = gmes_profile.last_values(profile if profile is not None else old_profile)
        options = []
        verify = None

        if mode == "record":
            show_screen_offer(screen)
            division = question_division(q, screen, last.get("division", ""))
            date_from, date_to = question_dates(q, screen, last)
            options = question_options(q, screen)
            sets = question_filters(q, last.get("sets"), screen=screen)
            verify = last.get("verify") if date_from else None

        elif any(v for k, v in last.items() if k != "sets") or last.get("sets"):
            # REPLAY, and everything is already known. Asking again is what
            # recording was supposed to remove: it is shown, and one keypress
            # runs it. Only someone who wants a DIFFERENT day has to type.
            ui.section("Remembered settings")
            ui.field("Division", last.get("division") or "(none)")
            ui.field("Period", (f"{last.get('from')} to {last.get('to')}"
                                if last.get("from") else "(the screen's own)"))
            for key, value in (last.get("sets") or {}).items():
                ui.field("Filter", f"{key} = {value}")
            if profile.get("options"):
                ui.field("Options", ", ".join(
                    core.option_display(o) for o in profile["options"]))
            print()
            if ui.INTERACTIVE:
                change = choose([_item("Run it now", "with exactly these settings"),
                                 _item("Change something first", "division, dates or filters")],
                                "Run it?", default=0) == 1
            else:
                change = q.ask("Run it?", "Enter to run, or type c to change something",
                               default="run", controls="words").lower().startswith("c")
            if change:
                division = question_division(q, screen, last.get("division", ""))
                date_from, date_to = question_dates(q, screen, last)
                sets = question_filters(q, last.get("sets"))
                verify = last.get("verify") if date_from else None
            else:
                division = last.get("division", "")
                date_from = last.get("from") or None
                date_to = last.get("to") or None
                sets = dict(last.get("sets") or {})
                verify = last.get("verify") if date_from else None
                # "Run it?" WAS the confirmation. Asking "Press Enter to
                # start" straight afterwards is a second gate on one decision,
                # and it cost a keypress on the shortest, most common path.
                confirmed = True

        else:
            # Recorded before values were remembered, and nothing could be
            # recovered from it. Ask once; it is saved from here on.
            # Accurate, rather than blaming the age of the recording: a
            # screen genuinely run with no division and no dates has nothing
            # to remember, and saying it was "recorded before values were
            # kept" is simply wrong for it.
            ui.note("nothing is remembered for this screen yet - answer once "
                    "and it will be kept.")
            division = question_division(q, screen, last.get("division", ""))
            date_from, date_to = question_dates(q, screen, last)
            sets = question_filters(q, last.get("sets"))
            verify = last.get("verify") if date_from else None

        if date_from and not verify:
            # A fixed "e.g. planYmd" example meant nothing on a screen that
            # has no such column - and this tool has no way to know which
            # columns a screen carries without asking it. It can: the
            # dataset's own column list exists before Inquiry ever runs
            # (candidate_verify_columns reads it at 0 rows), so real names
            # from THIS screen can be offered instead of a made-up example.
            candidates = []
            try:
                grid, _ = core.choose_grid(screen.info)
                if grid:
                    candidates = screen.candidate_verify_columns(grid)
            except Exception:
                pass
            print(f"    {ui.GREY}After the report runs, this checks the exported "
                  f"rows really carry {date_from} - not a leftover result from an "
                  f"earlier screen. Pick a column that holds a date; if the wrong "
                  f"one is typed, the tool will say what the real columns are "
                  f"called.{ui.RESET}")
            verify = None
            if candidates and ui.INTERACTIVE:
                q.asked += 1
                print(f"    {ui.CYAN}{ui.ARROW}{ui.RESET} {q.asked}. Result date check  "
                      f"{ui.GREY}which column holds the date?{ui.RESET}")
                items = [_item(c) for c in candidates] + [_item("Type a column name myself")]
                index = choose(items, "Column", default=0)
                verify = candidates[index] if index < len(candidates) else None
            if not verify:
                hint = (("real date columns on this screen: " + ", ".join(candidates))
                        if candidates else
                        "this screen's columns are only known once Inquiry has run once - "
                        "any guess is fine, wrong ones are caught and explained")
                verify = q.ask("Result date check", hint)
            if not verify:
                ui.note("A date column is required before a dated report can run.", "warn")
                return False

        ui.section("Plan")
        ui.field("Screen", code)
        ui.field("Division", division or "(none)")
        ui.field("Period", f"{date_from} to {date_to}" if date_from
                 else "(leave the screen's own dates)")
        if verify:
            ui.field("Result date check", verify)
        for key, value in sets.items():
            ui.field("Filter", f"{key} = {value}")
        for option in options:
            ui.field("Option", core.option_display(option))
        # A screen can PIN its own destination (HISTORY.md Phase 84.28) -
        # shown here so the plan says where the file is actually going,
        # rather than always naming the tool's own default. `run_screen`
        # resolves the same way when `export`/`out_dir` are left as `None`
        # below, so this display can never drift from what actually runs.
        pinned_dir = (profile or {}).get("output_dir")
        pinned_export = (profile or {}).get("export")
        ui.field("Output", pinned_dir or core.OUTPUT_DIR)
        if pinned_dir or pinned_export:
            ui.field("Export", core.effective_export(pinned_export))

        if not confirmed and ui.INTERACTIVE:
            confirmed = choose([_item("Start", "runs it against G-MES now - read-only"),
                                _item("Cancel", "nothing is run")], "Start?", default=0) == 0
            if not confirmed:
                ui.note("Cancelled. Nothing was run.", "warn")
                return False
        if not confirmed and ask("Press Enter to start", "or type n to cancel",
                                 default="y").lower().startswith("n"):
            ui.note("Cancelled. Nothing was run.", "warn")
            return False

        ui.section("Execution")
        results = core.run_many(ws, [{
            "screen_code": code, "division": division or None,
            "date_from": date_from, "date_to": date_to, "sets": sets,
            "options": options, "verify": verify, "export": None, "out_dir": None,
            "trust_profile": not relearning,
        }], log=Narrator())

        r = results[0]
        if r["ok"]:
            # r.get("profile") is only set inside run_screen() on an actual
            # successful gmes_profile.save() - it is now a try/except there
            # (HISTORY.md Phase 64.3), so a save failure keeps the report
            # "ok" but leaves this unset. The line below used to be chosen
            # from this front end's own `profile is None`, which only ever
            # asked "was this being learned for the first time", not "did
            # learning it actually work" - so it could confidently announce
            # "This screen is now learned" directly underneath a warning,
            # printed moments earlier by the same run, saying it was not.
            if r.get("profile"):
                learned_line = (f"{ui.GREY}This screen is now learned - next time it "
                                f"replays.{ui.RESET}") if profile is None else \
                               (f"{ui.GREY}Memory used, and refreshed.{ui.RESET}")
            else:
                learned_line = (f"{ui.YELLOW}Not remembered for next time - "
                                f"see the warning above.{ui.RESET}")
            # The REAL directory this run wrote to, not always the tool's
            # default - a pinned screen (HISTORY.md 84.28) writes somewhere
            # else, and the result's own file paths are the one place that
            # is never wrong about it.
            written_to = (os.path.dirname(r["files"][0]) if r["files"]
                         else pinned_dir or core.OUTPUT_DIR)
            ui.result(True, f"COMPLETE  {ui.DOT}  {r['rows']:,} rows", [
                f"{ui.GREY}in {r.get('seconds', '?')}s{ui.RESET}", ""]
                + [f"{ui.GREEN}{ui.TICK}{ui.RESET} {os.path.basename(p)}"
                   for p in r["files"]]
                + ["", f"{ui.GREY}{written_to}{ui.RESET}", "", learned_line])
            record_single_run(code, r, date_from, date_to, written_to)
            if mode == "record" and r.get("profile"):
                confirm_saved_setup(ws, code)
            after_success(r["files"], written_to)
        else:
            # A validation alert (e.g. "Start Date is later than End Date")
            # or a Notice-style popup can be what actually stopped this
            # report, and it is still open on screen right now. Live-caught:
            # left alone, the NEXT report - a completely different, unrelated
            # screen - failed too, with a misleading error of its own
            # ("M4131UM00 is open ... but its tab could not be brought to
            # the front"), because the leftover dialog from THIS failure was
            # still blocking activate_screen(). "One report failing must not
            # end the session" is not enough on its own if the failure
            # leaves something behind that breaks the next one too.
            gmes_common.close_child_popups(ws)
            what, do = library.explain_error(r["error"])
            ui.result(False, "DID NOT FINISH", [
                f"{ui.WHITE}{what}{ui.RESET}",
                f"{ui.CYAN}What to do:{ui.RESET} {do}", "",
                f"{ui.GREY}Details, for whoever supports this tool:{ui.RESET}",
                f"{ui.GREY}{r['error']}{ui.RESET}", "",
                f"{ui.GREY}Nothing was saved. A screenshot of the failure is "
                f"in the project folder.{ui.RESET}"])
            record_single_run(code, r, date_from, date_to, "")
        return bool(r["ok"])

    except (KeyboardInterrupt, InputClosed, GoBack, TaskCancelled,
            QuitRequested, SignInFailed, core.RunLocked):
        # The session/task is ending, not this report - and, for RunLocked
        # specifically, this is new here: the lock used to be acquired once
        # in main() before one_run() was ever called, so it could not raise
        # from inside this function before. Now session.get() acquires it
        # lazily, on the first real need, which can be from here.
        raise
    except Exception as e:
        # One report failing must not end the session. Report it and come
        # back for the next question.
        #
        # `gmes_common` was not imported anywhere in this file until now -
        # the screenshot_on_failure() call below would have raised
        # `NameError: name 'gmes_common' is not defined` the first time this
        # branch actually ran, replacing whatever `e` was with a crash that
        # this except block has no try/except of its own to catch, ending
        # the session anyway - the exact failure this design exists to
        # prevent. Not yet triggered live (every failure hit in this
        # session's own testing went through core.run_many()'s already-
        # working exception handling instead, which absorbs the error into
        # a normal `ok: False` result rather than raising past run_many()
        # at all) but real: reachable from any exception raised directly in
        # this function's own body - a question helper, show_screen_offer,
        # anything before run_many() is even called - including, now,
        # before `ws` itself is ever assigned (question_mode()/
        # question_screen() ask nothing that needs a browser).
        if ws is not None:
            gmes_common.close_child_popups(ws)
        ui.note(f"{type(e).__name__}: {e}", "bad")
        gmes_log.failure(e)
        gmes_common.screenshot_on_failure("gmes_workflow")
        return False


if __name__ == "__main__":
    sys.exit(main())

# What This Project Is — A Plain-Language Overview

*A guide for anyone who needs to understand what this tool does, without
needing to read code. If you want the technical documentation instead, see
[README.md](README.md).*

---

## 1. The one-sentence summary

This is a Windows tool that logs into Samsung's factory reporting system
(**G-MES**) on its own, opens whichever report screen you ask for, fills in
the filters (which factory/division, which date), runs the report, checks
that the numbers it got back actually match what you asked for, and saves
the result as an Excel file — automatically, without a
person clicking through the website by hand.

Think of it as a very careful, very fast assistant who knows how to operate
G-MES and never gets tired, never mis-clicks, and never forgets to
double-check its own work before handing you the file.

---

## 2. What is G-MES, and why does this exist?

**G-MES** ("Global Manufacturing Execution System") is Samsung's internal
web application for tracking factory production: what got made, what
failed inspection, how many defects occurred, what's planned versus what
actually happened, and so on. It's the kind of system a production planner
or quality engineer would log into every day to pull a report.

Pulling one of those reports by hand means: open a browser, log in, find
the right screen among hundreds of menu options, tick the right factory
division, type in the right date, click "Inquiry," wait for it to load,
click "Download to Excel," click through a confirmation dialog, and save
the file somewhere sensible. Multiply that by dozens of different reports,
every single day, and it becomes a lot of repetitive, error-prone manual
work — and a wrong click (wrong date, wrong division, wrong screen) can
produce a report that *looks* correct but silently contains the wrong data.

This tool exists to do all of that reliably, every time, without a person
having to sit and watch it.

---

## 3. How it actually works, step by step

When you give it a report to run — for example "pull the Loss Status report
for the VD division, for yesterday" — the tool does the following, in
order, and **checks its own work at every step**:

1. **Signs in.** It opens its own private browser window (never your
   personal Chrome — more on that below) and logs into G-MES using a
   securely stored password. If G-MES shows any pop-up in the way (a
   notice, a "someone else is using this account" warning, anything), it
   recognizes and clears it automatically instead of getting stuck.

2. **Finds the right screen.** G-MES has over 800 report screens, each with
   its own short code (like `P3151WM00`). You give it a code (or a search
   phrase), and it opens exactly that screen — never a similarly-named one
   it merely guessed at.

3. **Sets the filters.** It ticks the correct factory/division (e.g. "VD")
   in the organization tree and types in the requested date range. It reads
   the value right back off the screen afterward to confirm it actually
   took — it never just assumes a click worked.

4. **Runs the report ("Inquiry").** It clicks the button that asks G-MES to
   actually run the query, and waits — not a fixed few seconds, but until
   the result genuinely finishes loading, however long that takes.

5. **Checks the answer makes sense.** Before saving anything, it re-reads
   the actual result rows and confirms they really do carry the date and
   division that were requested. If the results don't match what was
   asked for — say, they came back for the wrong day — **it refuses to
   save the file** and tells you exactly why, rather than quietly handing
   you a report full of wrong numbers.

6. **Saves the file.** It downloads G-MES's own native Excel file (exactly
   what a person would get from the "Download" button), and checks the file
   really arrived and has content. The numbers themselves were already
   checked against G-MES's own data in step 5, before any file was saved.
   (Until September 2026 it also wrote a CSV copy; the owner chose Excel
   only. A CSV can still be asked for on the command line.)

7. **Remembers the screen for next time.** Once it has successfully run a
   report, it saves what it learned about that specific screen (which
   button does what, which field holds the date) so the *next* time
   someone asks for the same report, it can go straight to the result
   without having to re-figure out the screen from scratch.

If anything at any step doesn't look right — a screen that doesn't exist, a
result that comes back empty, a confirmation dialog that never appears — the
tool **stops and reports exactly what it saw**, instead of guessing and
potentially producing something incorrect. A report that fails loudly is
far safer than one that quietly succeeds with the wrong numbers in it.

---

## 4. What kinds of reports can it pull?

Any report screen the signed-in account has permission to see in G-MES. In
practice, that covers things like:

- **Production and planning reports** — e.g. "Plan vs. Result," comparing
  what was planned to what actually got produced.
- **Quality and defect reports** — e.g. "Process Defect List," "Loss
  Status," "Auto Inspection Failure Status" — tracking what went wrong on
  the line and how often.
- **Outgoing quality reports** — checks done just before product ships.
- **Batch and lot tracking** — grouping units into lots/batches for
  quality control.

Not every screen fits this "pick a date, run a report" pattern — a few are
lookup tools that need a specific serial number or unit ID instead of a
date range, and the tool recognizes that difference rather than forcing a
date onto a screen that doesn't use one.

---

## 5. Why you can trust the numbers it gives you

A few design choices exist specifically to make the output trustworthy,
not just fast:

- **It verifies before it saves.** As described above, it re-checks the
  actual data it got back against what was asked for, every time a date or
  filter was involved. Wrong data never becomes a saved file.
- **It never silently limits how much data it reads.** If a report has
  10,000 rows, it reads and exports all 10,000 — it does not quietly cut
  off at some hidden maximum.
- **It never guesses at an ambiguous screen.** If typing a report code
  finds no exact match, or finds a genuinely confusing pair of very
  similarly-named screens, it stops and asks, rather than opening whatever
  looks closest.
- **A failed run always fails loudly**, with a clear plain-English message
  about what went wrong, plus a screenshot of the screen at the moment of
  failure — so the cause can always be diagnosed afterward.

---

## 6. What it never does (safety boundaries)

- **It never touches your personal Chrome browser.** It builds and drives
  its own separate, private browser profile. Your own browser — bookmarks,
  logins, history, anything — is never opened, modified, or deleted by
  this tool.
- **Your password is never visible anywhere.** It's stored once, encrypted
  by Windows itself (the same mechanism Windows uses to protect saved
  Wi-Fi passwords), and is never written into any file, log, or screenshot
  in plain text.
- **It only reads data — it never changes anything in G-MES.** Every
  operation this tool performs is the equivalent of running a report and
  looking at it. It has no ability to save, submit, approve, or delete
  anything in the real production system.
- **Exported files never leave the machine automatically** and are never
  saved into the project's shared code repository — they stay local, in a
  dedicated output folder, for whoever ran the report to use.

---

## 7. How someone actually runs it

For a person using the tool day-to-day, it's genuinely simple:

```
Double-click:  GMES_Workflow.bat
```

That opens a guided, plain-English menu: type in the report code (or search
for it by name), answer a couple of short questions (which division, which
date), and the tool takes it from there — signing in, running the report,
and saving the files — printing a short plain-English summary at the end
("42 rows, saved as [filename].xlsx").

For a report someone runs regularly, it can also be pointed directly at a
report code with all the answers given up front, so it runs start-to-finish
with no questions asked at all — useful for scheduling something to run
automatically every night.

---

## 7b. Many reports at once, or on a schedule

Once several reports have been "taught" to the tool (each one is recorded once,
with a person showing it which division and which dates), it can run **all of
them, or just the ones you choose, in one go** - either right now or automatically
at a set time, for example every weekday at 06:30.

Before it runs anything it shows a plan: which reports will run, for which dates,
and which ones it will **skip and why** (for example a report it has never been
taught on this computer, or one whose date it would have no way to double-check).
If one report has no data that day, the others still run. If something is wrong
with the whole session - the sign-in, or three reports failing one after another -
it stops rather than keep asking a system that isn't answering, and it writes a
short report either way, so a failed 06:30 run is not a silent one.

One honest limit: a scheduled run needs the person to be signed in to Windows at
that time. The saved password is protected so that only that person's own signed-in
session can use it - which is the same protection that keeps it safe, not an oversight.

---

## 8. In short

This tool replaces a repetitive, click-by-click manual process — logging
into a factory reporting system and pulling reports by hand — with an
automated one that is not just faster, but actively **more careful**: it
double-checks its own filters, double-checks its own results, refuses to
hand over data it isn't confident is correct, and never touches anything
it shouldn't. The output — a checked Excel file for every report — is exactly what a person would have produced by hand, just
without the hours of repetitive clicking, and with a second layer of
verification a manual process wouldn't have had in the first place.

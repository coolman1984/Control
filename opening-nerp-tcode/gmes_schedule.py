"""
Scheduling batches with Windows Task Scheduler.

    create('morning', parse_when('06:30', daily=True))
    list_tasks()            -> what is scheduled, when it next runs, how the last run ended
    run_now('morning')      -> start the scheduled task immediately
    delete('morning')

**What a scheduled run needs, and why it is set up this way.** The tool signs in
with a credential stored under YOUR Windows account (DPAPI) and drives a real
browser window. Both need a signed-in Windows session, so the task is
registered for the current user and runs ONLY WHILE THEY ARE SIGNED IN - it will
not fire on a locked-out or logged-off PC. That is a property of how the
credentials are protected (CLAUDE.md 2.2), not an omission here. A PC that is
asleep at the scheduled time runs the task when it wakes (`StartWhenAvailable`);
a run still going when the next one is due is not started twice.

The task itself runs a tiny `.vbs` wrapper under `schedules/`, hidden (no
console window pops up for the run's whole duration - Task Scheduler's own
`-Hidden` setting does not do this, see `wrapper_text()`), which in turn runs
the real launcher `.cmd` - so the command stays short and readable, and what
actually runs is the SAVED BATCH of that name: edit the batch and the
schedule follows.

Talks to PowerShell's ScheduledTasks cmdlets rather than `schtasks.exe`, because
`schtasks /SD` takes the date in the machine's regional format and a wrong guess
silently schedules the wrong day.
"""
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field

import gmes_batch
import gmes_core as core

PREFIX = "GMES_Batch_"
SCHEDULE_DIR = os.path.join(core.SCRIPT_DIR, "schedules")

_DAYS = {"mon": "Monday", "tue": "Tuesday", "wed": "Wednesday", "thu": "Thursday",
         "fri": "Friday", "sat": "Saturday", "sun": "Sunday"}
_WEEKDAYS = ["mon", "tue", "wed", "thu", "fri"]


class ScheduleError(RuntimeError):
    """Task Scheduler refused, or could not be reached. The message is the
    system's own, so a person can act on it."""


@dataclass
class When:
    at: str                                   # "HH:MM", 24 hour
    kind: str = "daily"                       # daily | weekly | once
    days: list = field(default_factory=list)  # ["mon", ...] for weekly
    on: str = ""                              # "YYYY-MM-DD" for once


def parse_when(at, daily=False, weekdays=False, days=None, once=None, now=None):
    """What a person typed -> a validated When. Exactly one recurrence must be
    named - a schedule that could be read two ways is refused, not guessed at.

    Digits of any script are accepted (an Arabic keyboard types ٠٦:٣٠), and the
    time written into the task is always ASCII: the old code accepted `06:٣٠` and
    passed the Arabic digits through to PowerShell (HISTORY.md Phase 84.11).
    `now` is injectable so the past-time check is testable without a clock."""
    at = core.ascii_digits((at or "").strip())
    once = core.ascii_digits(once.strip()) if once else once
    if not re.fullmatch(r"([01]?\d|2[0-3]):[0-5]\d", at):
        raise ValueError(f"{at!r} is not a time - use 24 hour HH:MM, e.g. 06:30")
    hh, mm = at.split(":")
    at = f"{int(hh):02d}:{mm}"
    named = [bool(daily), bool(weekdays), bool(days), bool(once)]
    if sum(named) != 1:
        raise ValueError("say how often, exactly one of: --daily, --weekdays, "
                         "--days mon,wed,fri, or --once YYYY-MM-DD")
    if daily:
        return When(at, "daily")
    if weekdays:
        return When(at, "weekly", list(_WEEKDAYS))
    if days:
        parts = [d.strip().lower()[:3] for d in re.split(r"[,\s]+", days) if d.strip()]
        bad = [d for d in parts if d not in _DAYS]
        if bad or not parts:
            raise ValueError(f"unknown day(s) {bad or days!r} - use mon,tue,wed,thu,fri,sat,sun")
        ordered = [d for d in _DAYS if d in parts]
        return When(at, "weekly", ordered)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", once.strip()):
        raise ValueError(f"{once!r} is not a date - use YYYY-MM-DD")
    from datetime import datetime
    try:
        moment = datetime.strptime(f"{once.strip()} {at}", "%Y-%m-%d %H:%M")
    except ValueError:
        raise ValueError(f"{once!r} is not a real date") from None
    # Task Scheduler accepts a one-time trigger in the past and simply never runs
    # it - no error, and `schedules` would show it as waiting.
    if moment <= (now or datetime.now()):
        raise ValueError(f"{once.strip()} {at} has already passed - a one-time "
                         "schedule for a past moment would be created and never run")
    return When(at, "once", on=once.strip())


def describe_when(when):
    if when.kind == "daily":
        return f"every day at {when.at}"
    if when.kind == "once":
        return f"once, on {when.on} at {when.at}"
    if when.days == _WEEKDAYS:
        return f"every weekday at {when.at}"
    return f"every {', '.join(d.capitalize() for d in when.days)} at {when.at}"


def task_name(batch):
    if not gmes_batch.valid_name(batch):
        raise ValueError("a batch name is letters, digits, - or _ (at most 40)")
    return PREFIX + batch


# ---------------------------------------------------------------------------
# The launcher the task runs
# ---------------------------------------------------------------------------

def launcher_path(batch):
    task_name(batch)                                     # validates
    return os.path.join(SCHEDULE_DIR, f"run_{batch}.cmd")


RETRY_WAIT_SECONDS = 900          # 15 minutes between attempts
RETRIES = 2                       # so at most three attempts in all


def _bat_literal(text):
    """A literal for a .cmd file. A lone `%` is DROPPED by the batch parser
    (`D:\\100%done` runs as `D:\\100done`), so it is doubled. Everything else
    that is special to cmd (& ^ ( ) !) is harmless inside the quotes the caller
    puts around it."""
    return str(text).replace("%", "%%")


def launcher_text(batch, python=None, repo=None, retry_wait=RETRY_WAIT_SECONDS,
                  retries=RETRIES):
    """What the task actually executes.

    HISTORY.md Phase 84.11, proved with real cmd.exe: the old launcher wrote the
    repository path into an ASCII file with `errors="replace"`, so an Arabic or
    Korean install path became `????`, `cd` failed, the log was never written and
    the task exited 1 - a scheduled night that silently did nothing. Now:
      * the project folder is NOT written into the file at all: the launcher lives
        in `<project>\\schedules`, so `%~dp0..` is that folder whatever it is
        called (`repo` is still used for the task's working directory);
      * the file is UTF-8 with `chcp 65001`, for the one literal that remains (the
        Python path, which sits under the user's profile);
      * Python's redirected stdout/stderr are explicitly UTF-8 too: changing the
        console code page alone does not change Python's file-stream encoding on
        every Windows configuration;
      * `%` in a literal is doubled;
      * if `gmes_batch.py` is not where the launcher expects (the project moved)
        it logs that and exits 9 instead of running something else;
      * exit 3 (another run holds the browser) and 4 (sign-in failed) are retried
        `retries` times, `retry_wait` seconds apart - two schedules that collide,
        or a sign-in that hiccups at 06:30, no longer cost the whole night. Any
        other exit code (0 ok, 1 some screens failed) is returned as it is.
    No parenthesised blocks are used: a `)` inside an expanded path (`(x86)`)
    ends a block early."""
    python = _bat_literal(python or sys.executable)
    log = f"logs\\scheduled_{batch}.log"
    return "\r\n".join([
        "@echo off",
        "chcp 65001 >nul",
        'set "PYTHONIOENCODING=utf-8"',
        'cd /d "%~dp0.."',
        "if not exist logs mkdir logs",
        "if not exist gmes_batch.py goto missing",
        "set attempt=0",
        ":again",
        # -u: with stdout redirected to a file Python buffers in blocks, so a
        # run that hangs or is killed would leave an EMPTY log - the one thing
        # an unattended job must not do (HISTORY.md Phase 83).
        f'"{python}" -u gmes_batch.py run --batch {batch} --unattended >> "{log}" 2>&1',
        "set code=%errorlevel%",
        "if %code%==3 goto retry",
        "if %code%==4 goto retry",
        "exit /b %code%",
        ":retry",
        "set /a attempt+=1",
        f"if %attempt% GTR {retries} exit /b %code%",
        f'echo [%date% %time%] launcher: exit %code%, waiting {retry_wait} s, then '
        f'retry %attempt% of {retries} >> "{log}"',
        f"ping -n {retry_wait + 1} 127.0.0.1 >nul",
        "goto again",
        ":missing",
        f'echo [%date% %time%] launcher: gmes_batch.py was not found in "%CD%" - the '
        f'project was moved or deleted; re-create the schedule >> "{log}"',
        "exit /b 9",
        ""])


def write_launcher(batch, python=None, repo=None):
    os.makedirs(SCHEDULE_DIR, exist_ok=True)
    path = launcher_path(batch)
    # UTF-8 WITHOUT a byte-order mark (a BOM makes cmd choke on the first line).
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(launcher_text(batch, python, repo))
    return path


# ---------------------------------------------------------------------------
# The hidden wrapper - what Task Scheduler actually launches
# ---------------------------------------------------------------------------

def wrapper_path(batch):
    task_name(batch)                                     # validates
    return os.path.join(SCHEDULE_DIR, f"run_{batch}.vbs")


def wrapper_text(cmd_path):
    """VBScript that runs the launcher `.cmd` with NO visible window, waits
    for it to finish, and passes its exit code straight back out - the one
    thing Task Scheduler's OWN `-Hidden` setting does NOT do.

    Confirmed against Microsoft's own documentation before writing this:
    `New-ScheduledTaskSettingsSet -Hidden` only hides the TASK from the Task
    Scheduler UI list - it has no effect on whether the console window the
    task opens is visible. A task registered "run only when user is logged
    on" (this project's own registration, CLAUDE.md 2.2) pops a real, visible
    console for its whole duration regardless of that setting - live-observed
    here as an empty black window sitting on screen for the entire run
    (HISTORY.md, 2026-09-22).

    `WScript.Shell.Run(command, windowStyle, waitOnReturn)` with
    `windowStyle=0` (hidden) and `waitOnReturn=True` is the standard,
    documented way to suppress it: it still runs the exact same `.cmd`
    unchanged - the retry logic, the log redirection, everything Phase 84.11
    already proved live stays exactly as it was - only the window a person
    would otherwise see is gone. `waitOnReturn=True` matters beyond hiding
    the window: without it, Task Scheduler would see the wrapper exit the
    instant it LAUNCHES the batch, not when the batch finishes - breaking
    `-MultipleInstances IgnoreNew` (a "finished" task looks free to start
    again) and the result Task Scheduler records (always the wrapper's own
    immediate 0, never the batch's real outcome)."""
    return "\r\n".join([
        'Set objShell = CreateObject("WScript.Shell")',
        f'exitCode = objShell.Run("""{cmd_path}""", 0, True)',
        "WScript.Quit exitCode",
        ""])


def write_hidden_wrapper(batch, cmd_path):
    os.makedirs(SCHEDULE_DIR, exist_ok=True)
    path = wrapper_path(batch)
    # UTF-16LE WITH a byte-order mark - the one text format Windows Script
    # Host has reliably auto-detected since early versions, regardless of
    # what characters the embedded path contains. `cmd_path` can itself hold
    # non-ASCII characters (an Arabic/Korean install path, Phase 84.11's own
    # proven case) - UTF-8 support in .vbs files is version-dependent and
    # exactly the kind of thing that fails silently on some machine nobody
    # tested on, which is the whole reason that phase exists.
    with open(path, "w", encoding="utf-16-le") as fh:
        fh.write("﻿" + wrapper_text(cmd_path))
    return path


# ---------------------------------------------------------------------------
# PowerShell
# ---------------------------------------------------------------------------

def _q(value):
    """A PowerShell single-quoted string literal."""
    return "'" + str(value).replace("'", "''") + "'"


def _run_powershell(script, timeout=90):
    """(returncode, stdout, stderr). The one place the system is touched, so
    tests replace exactly this."""
    try:
        r = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive",
             "-ExecutionPolicy", "Bypass", "-Command", script],
            capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise ScheduleError("Windows PowerShell was not found, so Task Scheduler "
                            "cannot be reached from here") from None
    except subprocess.TimeoutExpired:
        raise ScheduleError("Task Scheduler did not answer within "
                            f"{timeout} seconds") from None
    return r.returncode, r.stdout, r.stderr


def _trigger_script(when):
    if when.kind == "daily":
        return f"New-ScheduledTaskTrigger -Daily -At {_q(when.at)}"
    if when.kind == "weekly":
        days = ",".join(_DAYS[d] for d in when.days)
        return f"New-ScheduledTaskTrigger -Weekly -DaysOfWeek {days} -At {_q(when.at)}"
    return f"New-ScheduledTaskTrigger -Once -At ([datetime]{_q(when.on + ' ' + when.at)})"


def create_script(batch, when, wrapper, repo):
    """`wrapper` is the `.vbs` path (`wrapper_path()`) - the Action launches
    `wscript.exe` against it, hidden, rather than the `.cmd` directly (see
    `wrapper_text()`'s own docstring for why `-Hidden` on the settings below
    does not achieve this on its own)."""
    name = task_name(batch)
    description = f"G-MES batch '{batch}' - {describe_when(when)}. Managed by gmes_batch.py."
    return "\n".join([
        "$ErrorActionPreference = 'Stop'",
        f"$action = New-ScheduledTaskAction -Execute 'wscript.exe' "
        f"-Argument {_q(chr(34) + wrapper + chr(34))} -WorkingDirectory {_q(repo)}",
        f"$trigger = {_trigger_script(when)}",
        # StartWhenAvailable: a PC asleep at the time still runs it on waking.
        # IgnoreNew: a run still going is never started a second time.
        "$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable "
        "-MultipleInstances IgnoreNew -AllowStartIfOnBatteries "
        "-DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 6)",
        f"Register-ScheduledTask -TaskName {_q(name)} -Action $action -Trigger $trigger "
        f"-Settings $settings -Description {_q(description)} -Force | Out-Null",
    ])


def create(batch, when, python=None, repo=None):
    """Write the launcher and its hidden wrapper, then register the task.
    Returns the task name."""
    repo = repo or core.SCRIPT_DIR
    cmd_path = write_launcher(batch, python, repo)
    vbs_path = write_hidden_wrapper(batch, cmd_path)
    rc, out, err = _run_powershell(create_script(batch, when, vbs_path, repo))
    if rc != 0:
        raise ScheduleError((err or out or "Task Scheduler refused the task").strip())
    return task_name(batch)


def list_script():
    return "\n".join([
        "$ErrorActionPreference = 'SilentlyContinue'",
        f"$rows = @(Get-ScheduledTask -TaskName '{PREFIX}*' | ForEach-Object {{",
        "  $i = $_ | Get-ScheduledTaskInfo",
        "  $fmt = { param($d) if ($d -and $d.Year -gt 2000) { $d.ToString('s') } else { '' } }",
        "  [pscustomobject]@{",
        "    name = $_.TaskName; state = [string]$_.State",
        "    next = (& $fmt $i.NextRunTime); last = (& $fmt $i.LastRunTime)",
        "    result = $i.LastTaskResult",
        "    trigger = (($_.Triggers | ForEach-Object { $_.CimClass.CimClassName -replace 'MSFT_Task','' -replace 'Trigger','' }) -join ',')",
        "  }",
        "})",
        "if ($rows.Count -eq 0) { '[]' } else { ConvertTo-Json -InputObject $rows -Compress }",
    ])


# Task Scheduler's LastTaskResult -> (words, ok). ok: True succeeded, False failed,
# None "not a failure" (still running, never run, disabled). The 0x413xx family are
# Task Scheduler's own status codes; 1-4 and 9 are THIS tool's exit codes
# (gmes_batch.EXIT_*, and the launcher's own 9). HISTORY.md Phase 84.12: 267009
# (0x41301, "currently running") was shown as "failed (267009)".
RESULT_TEXT = {
    0x0: ("ok", True),
    0x41300: ("ready", None),
    0x41301: ("running now", None),
    0x41302: ("the task is disabled", None),
    0x41303: ("has not run yet", None),
    0x41304: ("no more runs are scheduled", None),
    0x41305: ("not scheduled", None),
    0x41306: ("stopped - by a person or by the time limit", False),
    0x41307: ("no valid triggers", False),
    0x800710E0: ("refused to start - a setting or a missed start blocked it", False),
    0x8007010B: ("the folder it should start in does not exist", False),
    0x80070002: ("the launcher file was not found", False),
    0x80070005: ("access denied", False),
    0x1: ("some screens failed or were not run", False),
    0x2: ("the command was wrong", False),
    0x3: ("another run held the browser (after retries)", False),
    0x4: ("sign-in failed (after retries)", False),
    0x9: ("the launcher could not find the project - it was moved or deleted", False),
}


def describe_result(code):
    """`LastTaskResult` -> (words, ok, hex). Unknown non-zero codes are failures,
    reported with their hex so they can be looked up."""
    if code is None:
        return "", None, ""
    try:
        value = int(code) & 0xFFFFFFFF
    except (TypeError, ValueError):
        return f"unreadable result {code!r}", False, ""
    words, ok = RESULT_TEXT.get(value, ("failed with an unrecognised code", False))
    return words, ok, f"0x{value:X}"


def assess_task(task, now=None):
    """Plain-words warnings about ONE listed task: a schedule can be registered
    and still never run, and nothing says so. Returns a list of strings.

    Found by research, not by an incident here: a laptop asleep or off for days
    is silently NOT revived by Task Scheduler, batteries and battery-saver defer
    triggers, and a daily job "ran on time" while doing nothing useful."""
    from datetime import datetime, timedelta
    now = now or datetime.now()
    notes = []
    state = (task.get("state") or "").lower()
    if state == "disabled":
        notes.append("DISABLED - it will not run")
    recurring = any(k in (task.get("trigger") or "") for k in ("Daily", "Weekly"))
    nxt = _parse_time(task.get("next_run"))
    last = _parse_time(task.get("last_run"))
    if recurring and state != "disabled" and nxt is None:
        notes.append("NO NEXT RUN - the schedule is not active")
    if nxt is not None and state == "ready" and nxt < now - timedelta(minutes=15):
        notes.append(f"OVERDUE - due {nxt:%Y-%m-%d %H:%M}; the PC was probably off, asleep "
                     "or signed out (it starts when the PC is available again)")
    if recurring and last is not None and last < now - timedelta(days=8):
        notes.append(f"has not run for {(now - last).days} days")
    if task.get("last_ok") is False:
        notes.append("the LAST RUN FAILED - read logs\\scheduled_" + task.get("batch", "") + ".log")
    return notes


def _parse_time(text):
    from datetime import datetime
    try:
        return datetime.strptime((text or "").strip(), "%Y-%m-%dT%H:%M:%S") if text else None
    except ValueError:
        return None


def parse_list(stdout):
    """PowerShell's JSON -> a tidy list. One task comes back as an object, none
    as an empty array - both are normalised."""
    text = (stdout or "").strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except ValueError:
        raise ScheduleError(f"Task Scheduler's answer could not be read: {text[:120]!r}") from None
    if isinstance(data, dict):
        data = [data]
    out = []
    for row in data:
        name = str(row.get("name", ""))
        if not name.startswith(PREFIX):
            continue
        result = row.get("result")
        words, ok, hexed = describe_result(result)
        out.append({"batch": name[len(PREFIX):], "task": name,
                    "state": row.get("state", ""), "next_run": row.get("next", ""),
                    "last_run": row.get("last", ""), "last_result": result,
                    "last_text": words, "last_hex": hexed,
                    "trigger": row.get("trigger", ""),
                    "last_ok": ok if row.get("last") else None})
    return out


def list_tasks():
    rc, out, err = _run_powershell(list_script())
    if rc != 0:
        raise ScheduleError((err or "Task Scheduler could not be read").strip())
    return parse_list(out)


def delete(batch):
    """Remove the task and its launcher. Returns True if a task was removed."""
    name = task_name(batch)
    script = ("$ErrorActionPreference = 'Stop'\n"
              f"if (Get-ScheduledTask -TaskName {_q(name)} -ErrorAction SilentlyContinue) "
              f"{{ Unregister-ScheduledTask -TaskName {_q(name)} -Confirm:$false; 'removed' }} "
              "else { 'absent' }")
    rc, out, err = _run_powershell(script)
    if rc != 0:
        raise ScheduleError((err or out or "Task Scheduler refused").strip())
    for path in (launcher_path(batch), wrapper_path(batch)):
        try:
            os.unlink(path)
        except OSError:
            pass
    return "removed" in out


def run_now(batch):
    name = task_name(batch)
    script = ("$ErrorActionPreference = 'Stop'\n"
              f"Start-ScheduledTask -TaskName {_q(name)}")
    rc, out, err = _run_powershell(script)
    if rc != 0:
        raise ScheduleError((err or out or f"there is no scheduled task for {batch!r}").strip())
    return name


def set_enabled(batch, enabled):
    """Pause (`enabled=False`) or resume a schedule without removing it - the
    task, its time and its launcher all stay exactly as they are; Task
    Scheduler only skips its runs while it is disabled (HISTORY.md Phase
    94.4). Returns the task name."""
    name = task_name(batch)
    verb = "Enable-ScheduledTask" if enabled else "Disable-ScheduledTask"
    script = ("$ErrorActionPreference = 'Stop'\n"
              f"{verb} -TaskName {_q(name)} | Out-Null; 'done'")
    rc, out, err = _run_powershell(script)
    if rc != 0:
        raise ScheduleError((err or out or f"there is no scheduled task for {batch!r}").strip())
    return name

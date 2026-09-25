"""
What the interactive front end shows about things that are ALREADY on disk -
saved reports, past runs and their files - plus plain-language error
explanations. Offline by construction: nothing here imports a browser path,
signs in, or reads exported production data (only the run reports under the
git-ignored `logs/batches/`, which hold counts, codes and file names).

HISTORY.md Phase 93 (CLI_UI_IMPROVEMENT_PLAN.md Milestones B-C). Kept apart
from `run_gmes_workflow.py` so each rule here is a pure function with a test,
and so the same wording can be reused by any later surface. Presentation
only - nothing here decides how a report is RUN; that stays in gmes_core /
gmes_batch (CLAUDE.md section 0: there is one engine).
"""
import json
import os

import gmes_batch
import gmes_profile

# ---------------------------------------------------------------------------
# One status model, in the user's words (CLI_UI_IMPROVEMENT_PLAN.md,
# "Status language")
# ---------------------------------------------------------------------------

READY = "Ready"
READY_WARN = "Ready with warning"
LAST_FAILED = "Last run failed"
NOT_TRIED = "Not yet run here"


def run_history(directory=None):
    """Every past run report, newest first. Unreadable reports are skipped,
    never fatal - one damaged file must not hide the rest."""
    directory = directory or gmes_batch.REPORT_DIR
    try:
        names = sorted((n for n in os.listdir(directory)
                        if n.startswith("batch_") and n.endswith(".json")), reverse=True)
    except OSError:
        return []
    out = []
    for name in names:
        path = os.path.join(directory, name)
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            results = list(data["results"])
            counts = dict(data["summary"])
        except (OSError, ValueError, KeyError, TypeError):
            continue
        meta = data.get("meta") or {}
        summary = path[:-len(".json")] + "_summary.html"
        out.append({"path": path, "started": meta.get("started", ""),
                    "name": meta.get("batch") or "", "policy": meta.get("policy", ""),
                    "output_dir": meta.get("output_dir") or "",
                    "warnings": list(meta.get("warnings") or []),
                    "counts": counts, "total": len(results), "results": results,
                    "summary": summary if os.path.isfile(summary) else None})
    return out


def last_result_by_code(history):
    """{screen code: (started, result)} - the newest result each code has."""
    seen = {}
    for run in history:                               # newest first
        for r in run["results"]:
            code = str(r.get("screen", "")).upper()
            if code and code not in seen and r.get("status") in ("ok", "failed"):
                seen[code] = (run["started"], r)
    return seen


def report_card(profile, last=None):
    """A saved report as the person choosing it needs to see it:
    {code, title, status, why, settings, last_run}."""
    code = profile.get("screen", "?")
    try:
        values = gmes_profile.last_values(profile)
    except Exception:                                        # noqa: BLE001
        values = {}
    division = values.get("division") or ""
    if values.get("from"):
        period = values["from"] + (f" to {values['to']}"
                                   if values.get("to") and values["to"] != values["from"] else "")
        settings = f"date {period}"
    else:
        typed = [k for k, v in (values.get("sets") or {}).items() if gmes_batch.date_role(k, v)]
        settings = "a typed date" if typed else "the screen's own dates"
    if division:
        settings = f"{division}, {settings}"

    if values.get("from") and values.get("verify"):
        status, why = READY, "the report date is checked in the results"
    elif values.get("from"):
        status, why = READY_WARN, "will ask once which column holds the date"
    elif typed_date(values):
        status, why = READY_WARN, "the date is typed into the screen but not checked in the results"
    else:
        status, why = READY, "uses the dates the screen shows by itself"
    if not profile.get("learned"):
        status, why = NOT_TRIED, "shipped with the tool; the first run here sets it up"

    last_run = ""
    if last:
        started, r = last
        if r.get("status") == "ok":
            last_run = f"last run {started}: {int(r.get('rows') or 0):,} rows"
        else:
            status = LAST_FAILED
            why = explain_error(r.get("error") or "")[0]
            last_run = f"last run {started}: did not finish"
    return {"code": code, "title": profile.get("title") or "", "status": status,
            "why": why, "settings": settings, "last_run": last_run}


def typed_date(values):
    return any(gmes_batch.date_role(k, v) for k, v in (values.get("sets") or {}).items())


def search(cards, text):
    """Cards whose code, title or settings hold EVERY word typed, any case."""
    words = [w for w in (text or "").lower().split() if w]
    if not words:
        return list(cards)
    return [c for c in cards
            if all(w in f"{c['code']} {c['title']} {c['settings']}".lower() for w in words)]


def page_of(items, page, size=10):
    """(visible, first_number, last_number, total, page, pages) - a page never
    hides that more exists: the total always travels with it."""
    total = len(items)
    pages = max(1, -(-total // size))
    page = min(max(0, page), pages - 1)
    start = page * size
    visible = items[start:start + size]
    return visible, start + 1, start + len(visible), total, page, pages


# ---------------------------------------------------------------------------
# Errors, in words a person can act on (plan: "Error translation")
# ---------------------------------------------------------------------------

# (words to look for, what happened, what to do, Arabic what, Arabic do).
# First match wins, so the specific families come before the general ones.
# Matching is on the ENGINE's own English message - the message itself is
# always kept and shown underneath as the technical detail, never replaced.
# The Arabic pair is used by the morning summary page when the language
# setting is Arabic (a browser page renders right-to-left properly; the
# Windows console does not - HISTORY.md Phase 94.5).
_FAMILIES = (
    (("another g-mes run already", "lock held by"),
     "Another report is already running on this PC.",
     "Wait for it to finish, then try again.",
     "فيه تقرير تاني شغال دلوقتي على نفس الجهاز.",
     "استنى لما يخلص وجرّب تاني."),
    (("pc's clock", "pc clock"),
     "This PC's date or time is wrong, so 'yesterday' would be the wrong day.",
     "Fix the date and time in Windows settings, then run again.",
     "تاريخ أو ساعة الجهاز غلط، فكلمة \"امبارح\" كانت هتجيب يوم غلط.",
     "صلّح التاريخ والساعة من إعدادات ويندوز وشغّل تاني."),
    (("could not sign in", "sign-in failed", "ad sso", "samsung sso", "refused the credentials",
      "not signed in"),
     "Signing in to G-MES did not work.",
     "Try once more. If it keeps failing, check your password still works in G-MES itself.",
     "الدخول على نظام المصنع ما نجحش.",
     "جرّب مرة كمان، ولو فضل يفشل اتأكد إن كلمة السر لسه شغالة في النظام نفسه."),
    (("connection to the automation browser was lost", "browser went away"),
     "The report window closed or crashed while it was working.",
     "Run the report again - nothing wrong was saved.",
     "شباك التقرير اتقفل أو وقع وهو شغال.",
     "شغّل التقرير تاني، مفيش حاجة غلط اتحفظت."),
    (("shape changed", "predates opening-shape", "controls have changed"),
     "G-MES has changed this screen since it was set up.",
     "Set the report up again: choose 'Set up a new report' and pick the same code.",
     "نظام المصنع غيّر شكل الشاشة دي من ساعة ما اتجهزت.",
     "جهّز التقرير من جديد من اختيار (Set up a new report) بنفس الكود."),
    (("refusing to export the wrong data", "the results carry", "outside the requested"),
     "The results did not match what was asked for (usually the date), so nothing was saved.",
     "Check the dates and run again. If it repeats, the screen may need setting up again.",
     "النتيجة ما طابقتش المطلوب (غالباً التاريخ)، فمفيش حاجة اتحفظت.",
     "راجع التواريخ وشغّل تاني، ولو اتكرر يبقى الشاشة محتاجة تتجهز من جديد."),
    (("interrupted by another pc", "session was interrupted"),
     "Someone signed in to G-MES with the same account on another PC.",
     "Run the report again once the other session is closed.",
     "حد دخل بنفس الحساب من جهاز تاني.",
     "شغّل التقرير تاني بعد ما الجلسة التانية تتقفل."),
    (("had not settled", "never finished building", "not answering",
      "failed in a row"),
     "G-MES was too slow or did not answer.",
     "Try again later - the system may be busy.",
     "نظام المصنع كان بطيء أو ما ردّش.",
     "جرّب بعدين، ممكن يكون النظام زحمة."),
    (("winerror 32", "being used by another process", "permissionerror",
      "access is denied"),
     "The file or folder is in use or cannot be written.",
     "Close the file if it is open in Excel, then run again.",
     "الملف أو الفولدر مفتوح أو مش مسموح الكتابة فيه.",
     "اقفل الملف لو مفتوح في إكسل وشغّل تاني."),
    (("no complete .xlsx", "save to excel", "excel download icon", "not an xlsx"),
     "The Excel download did not arrive.",
     "Run the report again.",
     "ملف الإكسل ما وصلش.",
     "شغّل التقرير تاني."),
    (("dialog opened instead of results",),
     "G-MES showed a message instead of results.",
     "Read the message in the details below - usually a filter or date needs changing.",
     "نظام المصنع طلّع رسالة بدل النتيجة.",
     "اقرا الرسالة في التفاصيل، غالباً فلتر أو تاريخ محتاج يتغير."),
    (("no rows", "contains no rows", "no data"),
     "The report came back empty.",
     "Try a wider date range - there may simply be nothing for that day.",
     "التقرير رجع فاضي.",
     "جرّب فترة أوسع، ممكن ببساطة مفيش حاجة في اليوم ده."),
    (("not run:",),
     "This report was not started, because the run stopped earlier.",
     "See the first failure in the list; fixing it usually fixes the rest.",
     "التقرير ده ما بدأش لأن التشغيلة وقفت قبله.",
     "شوف أول فشل في القايمة، تصليحه غالباً بيحل الباقي."),
)
_UNKNOWN = ("The report stopped with a problem the tool does not recognise.",
            "Run it again once. If it fails again, send the log file to whoever supports this tool.",
            "التقرير وقف بمشكلة البرنامج مش عارفها.",
            "شغّله مرة كمان، ولو فشل تاني ابعت ملف الدعم الفني للي بيدعم البرنامج.")


def explain_error(text, language="en"):
    """(what happened, what to do) for an engine message, in plain words -
    English, or Arabic with `language="ar"`."""
    low = (text or "").lower()
    found = next((f[1:] for f in _FAMILIES if any(w in low for w in f[0])), _UNKNOWN)
    return (found[2], found[3]) if language == "ar" else (found[0], found[1])


# ---------------------------------------------------------------------------
# Opening a file or folder the tool produced
# ---------------------------------------------------------------------------

def open_path(path, opener=None):
    """Open a file or folder with Windows' own default program -> (ok, message).

    Only a path that exists is opened, and only through the operating
    system's file association (never the automation browser, never a shell
    command line built from text). Elsewhere, or if Windows refuses, the path
    is returned for the person to open themselves."""
    if not path or not os.path.exists(path):
        return False, f"not found: {path or '(nothing to open)'}"
    opener = opener or getattr(os, "startfile", None)
    if opener is None:
        return False, f"open it yourself: {path}"
    try:
        opener(path)
    except OSError as e:
        return False, f"Windows could not open it ({e}): {path}"
    return True, f"opened {path}"


# ---------------------------------------------------------------------------
# Help, by task rather than by module (plan: "Help and Support Package")
# ---------------------------------------------------------------------------

HELP_TOPICS = (
    ("Run yesterday's report",
     "Choose 1 (Run a saved report), pick it by number, and press Enter at "
     "'Run it?'. The date it uses is shown before anything runs."),
    ("Find a report when I do not know its code",
     "Choose 4 (Saved reports) and type any word from its name, e.g. 'plan'. "
     "For a report never used here, choose 2 and type: find <words>."),
    ("Change the date or division of a saved report",
     "Run it (1), and at 'Run it?' type change instead of pressing Enter."),
    ("Run several reports at once",
     "Choose 3. Pick numbers such as 1,3,5-7, or 'all'. Ten or more ask you "
     "to type a confirmation, so nothing big starts by accident."),
    ("Run reports every night by themselves",
     "Choose 3, pick the reports, then choose 'schedule'. The PC must be on "
     "and you must be signed in to Windows at that time. The next morning, "
     "the Home screen shows how the night went."),
    ("What does 'Ready with warning' mean?",
     "The report runs, but the tool cannot prove from the results that they "
     "are for the day you asked. Everything else is still checked."),
    ("Where are my files?",
     "Choose 6 (Recent runs and files) and pick a run - it can open the "
     "folder for you."),
    ("Something failed",
     "The message says what happened and what to do. The full detail is in "
     "the log file named at the top of the screen, for whoever supports this tool."),
)


# ---------------------------------------------------------------------------
# Settings - a person's own preferences, never anything the engine decides
# (HISTORY.md Phase 94.5)
# ---------------------------------------------------------------------------
#
# Kept beside - never inside - the tool's own runtime folder's protected
# files (CLAUDE.md 2.1a): a separate `ui_settings.json`, written atomically,
# which nothing ever deletes. A missing or damaged file is simply the defaults.

DEFAULT_SETTINGS = {"language": "en", "plain": False, "open_folder_after": False}
_CHOICES = {"language": ("en", "ar"), "plain": (True, False),
            "open_folder_after": (True, False)}


def settings_path():
    root = os.environ.get("LOCALAPPDATA", "")
    base = os.path.join(root, "GMES_Automation") if root else \
        os.path.join(os.path.expanduser("~"), ".gmes_automation")
    return os.path.join(base, "ui_settings.json")


def load_settings(path=None):
    out = dict(DEFAULT_SETTINGS)
    try:
        with open(path or settings_path(), encoding="utf-8") as fh:
            saved = json.load(fh)
    except (OSError, ValueError):
        return out
    if isinstance(saved, dict):
        for key, allowed in _CHOICES.items():
            if saved.get(key) in allowed:
                out[key] = saved[key]
    return out


def save_settings(values, path=None):
    """Write only the known keys with allowed values; returns what was saved."""
    import tempfile
    path = path or settings_path()
    clean = load_settings(path)
    for key, allowed in _CHOICES.items():
        if key in values and values[key] in allowed:
            clean[key] = values[key]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".ui_settings-", dir=os.path.dirname(path))
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(clean, fh, indent=2)
    os.replace(tmp, path)
    return clean


# ---------------------------------------------------------------------------
# Support package - everything someone helping needs, nothing they must not see
# (HISTORY.md Phase 94.6)
# ---------------------------------------------------------------------------
#
# In: the tool's version, Python and Windows versions, the settings, the list
# of saved report CODES (no remembered values), the last few run reports, and
# the tail of the latest log - every text redacted again on the way in.
# Never in: credentials, the browser profile, screenshots, exported files,
# `screens/` values. The ZIP lands under the git-ignored `logs/support/`.

SUPPORT_LOG_LINES = 400
SUPPORT_RUNS = 5


def _version(root):
    try:
        with open(os.path.join(root, ".git", "HEAD"), encoding="utf-8") as fh:
            head = fh.read().strip()
        if head.startswith("ref:"):
            with open(os.path.join(root, ".git", *head[5:].strip().split("/")),
                      encoding="utf-8") as fh:
                head = fh.read().strip()
        return head[:12]
    except OSError:
        return "unknown"


def support_package(root=None, out_dir=None, history=None, log_dir=None, now=None):
    """Write the redacted support ZIP and return its path."""
    import platform
    import sys
    import time
    import zipfile
    import gmes_redact
    root = root or os.path.dirname(os.path.abspath(__file__))
    out_dir = out_dir or os.path.join(root, "logs", "support")
    log_dir = log_dir or os.path.join(root, "logs")
    history = run_history() if history is None else history
    os.makedirs(out_dir, exist_ok=True)
    stamp = now or time.strftime("%Y%m%d_%H%M%S")
    path = os.path.join(out_dir, f"support_{stamp}.zip")
    about = {"version": _version(root), "python": sys.version.split()[0],
             "platform": platform.platform(), "settings": load_settings(),
             "saved_reports": [p.get("screen") for p in gmes_profile.known()]}
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("about.json", json.dumps(about, indent=2))
        for run in history[:SUPPORT_RUNS]:
            with open(run["path"], encoding="utf-8") as fh:
                z.writestr(f"runs/{os.path.basename(run['path'])}",
                           gmes_redact.redact_text(fh.read()))
        try:
            logs = sorted(n for n in os.listdir(log_dir)
                          if n.startswith("gmes_") and n.endswith(".log"))
        except OSError:
            logs = []
        if logs:
            with open(os.path.join(log_dir, logs[-1]), encoding="utf-8", errors="replace") as fh:
                tail = fh.readlines()[-SUPPORT_LOG_LINES:]
            z.writestr(f"log/{logs[-1]}", gmes_redact.redact_text("".join(tail)))
    return path

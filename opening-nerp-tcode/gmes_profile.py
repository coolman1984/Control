"""
What the tool remembers about a G-MES screen after a run that actually
worked - step 9 of the basic workflow, and nothing more.

A profile is NOT a recording. It holds no coordinates, no window ids, no
dataset contents. It holds three kinds of thing:

  * which control this screen means by "from", "to" and "division"
  * which grid holds the result
  * a fingerprint of the screen as it was when the run succeeded

Everything else is rediscovered on every run, because it is cheap and always
true. The profile only answers the questions discovery cannot: on a screen
with three date fields, which one did the run that worked actually use?

Why so little is saved
----------------------
G-MES renumbers a screen's ids on every open (winPPM0219_0_516 ->
winPPM0219_0_315), so anything positional is stale before it is even reused.
And the worst bug in this project's history came from reusing a remembered
dataset name: a run reported 875 rows - the previous screen's count - when
the screen had returned 17. So what is saved here is deliberately limited to
names that are stable by construction, and every one of them is checked
against the live screen before it is trusted.

Fields are copied out by name, one at a time. That is an allowlist, and it
is deliberate: a G-MES form tree contains live session tokens (an ordinary
looking `dsAnyframeDVO` carries `tokenId` and `refreshTokenId`), so anything
that serialised "whatever was discovered" would write credentials to disk.
"""
import hashlib
import json
import os
import re
import tempfile
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# What THIS user's runs have proved. Git-ignored: a filter value here can be a
# production order number.
SCREENS_DIR = os.path.join(SCRIPT_DIR, "screens")

# What the TOOL knows about a screen, independent of who is driving it.
# Committed, and shipped to every user - see `shippable()` for the line
# between the two and why it is drawn where it is.
SHIPPED_DIR = os.path.join(SCRIPT_DIR, "screens_known")


# What a G-MES screen code (or menu id) looks like, in ONE place. Swept against
# the live catalogue of 810 screens (HISTORY.md Phase 84.20): letters and digits
# only, a letter first, at least one digit, 5-16 characters. The shape it
# replaced - 1-4 letters, then FOUR or more digits - refused 129 real screens
# (BB210UM00, M4A11UM00, P225AUM00, L311AUM00 ...), which could be exported but
# never remembered, replayed or batched. Letters-and-digits-only is also what
# keeps a code safe to use as a file name: no separator, dot or space gets in.
CODE_PATTERN = r"(?=[A-Za-z0-9]*\d)[A-Za-z][A-Za-z0-9]{4,15}"


def looks_like_code(text):
    return re.fullmatch(CODE_PATTERN, (text or "").strip()) is not None


def _safe_code(code):
    safe = code.strip().upper()
    if not looks_like_code(safe):
        raise ValueError("screen code must be a simple full G-MES screen code")
    return safe

def path_for(code):
    return os.path.join(SCREENS_DIR, f"{_safe_code(code)}.json")


def shipped_path_for(code):
    return os.path.join(SHIPPED_DIR, f"{_safe_code(code)}.json")


# ---------------------------------------------------------------------------
# References - the only shapes ever written to disk
# ---------------------------------------------------------------------------

def field_ref(flt):
    """A filter, by the names that survive a reopen. No value, no id.

    `stable_path` is the exact form path with the window's own renumbered
    instance segment stripped (`relativePath()` in JS_DISCOVER) - the same
    trick already used for left-panel option identity (Phase 76), applied
    here so `Screen.find_ref()` can tell two instances of a reusable
    component apart on replay, not just at record time (HISTORY.md -
    external review of 1957ba9/cff282b, finding #3). The RAW `path` is
    never stored: it embeds the window's transient instance number and
    would never match again after a reopen."""
    if not flt:
        return None
    ref = {"dataset": flt.get("dataset", ""), "column": flt.get("column", ""),
          "control": flt.get("control", ""), "form": flt.get("form", ""),
          "label": flt.get("label", ""), "stable_path": flt.get("stable_path", "")}
    # HISTORY.md Open Item 41: an EMPTY date field has no width of its own to
    # read, so a write to it defaulted to YYYYMMDD even for a field meant to
    # hold YYYYMM - accepted with no error, answering a different question.
    # A field seen holding a genuine, non-empty value BEFORE this run's own
    # write touched it (its native default, or an earlier run's proven write)
    # is real evidence of that field's actual width - remembered here so a
    # LATER run that finds the field empty again still knows which width to
    # use instead of guessing eight.
    digits = re.sub(r"[^\d]", "", str(flt.get("value") or ""))
    if len(digits) in (4, 6):
        ref["width"] = len(digits)
    return ref


def grid_ref(grid):
    if not grid:
        return None
    return {"name": grid.get("name", ""), "dataset": grid.get("dataset", ""),
            "form": grid.get("form", ""), "stable_path": grid.get("stable_path", "")}


def tree_ref(form, dataset, entry):
    return {"form": form or "", "dataset": dataset or "", "entry": entry or ""}


# ---------------------------------------------------------------------------
# Fingerprint - how we notice the screen changed
# ---------------------------------------------------------------------------

def fingerprint(info, grid_aliases=None):
    """A digest of the parts of the screen a profile is allowed to refer to:
    the bound filters, by dataset and column, and the result grids.

    Two things are deliberately NOT in it, and the first version of this
    included both. Running the same screen twice, seconds apart, reported
    "the screen's controls have changed" and threw away what it had just
    learned - a memory that erases itself on every run is worse than none,
    because it also cries wolf.

      * **Unbound inputs** are collected only when VISIBLE, and visibility
        moves with scrolling, tabs and panels that finish rendering late. It
        is not a property of the screen at all.
      * **The control name** of a bound filter, because one column can be
        bound to several controls and the one recorded is whichever was
        visible. `_still_there()` matches on dataset and column, so the
        fingerprint watches exactly what the profile actually uses -
        no more, or it fires on changes that cannot matter.

    Counted as SETS, not lists: two grids bound to the same dataset are the
    same result set as far as anything here is concerned, and how many of
    them the screen happens to have built is not a change worth reporting.

    `grid_aliases` maps a dataset a grid is re-bound TO (once a query
    answers) onto the one it was bound to when the screen was first read
    (HISTORY.md Phase 82.18), so a window opened cold and one already warm
    from an earlier run hash to the same screen. Absent - every profile
    saved before that phase - the digest is exactly what it always was."""
    parts = {f"f:{f.get('dataset')}.{f.get('column')}"
             for f in info.get("filters", [])}
    parts |= {f"g:{canonical_grid(g.get('dataset'), grid_aliases)}"
              for g in info.get("grids", [])}
    return hashlib.sha1("|".join(sorted(parts)).encode("utf-8")).hexdigest()[:16]


def canonical_grid(dataset, grid_aliases):
    """The dataset name a grid is remembered under: the one it had when the
    screen was first read, whichever of its two names is showing now."""
    return (grid_aliases or {}).get(dataset, dataset)


def resolve_grid_dataset(profile, info):
    """The dataset name to look for on the screen as it is RIGHT NOW, for the
    grid a profile remembers. Normally the remembered name; but a grid that
    the screen re-binds after a query shows its other name once the window
    has already been through one, so a recorded alias is followed to
    whichever of the two is actually present. None when the profile
    remembers no grid."""
    remembered = ((profile or {}).get("grid") or {}).get("dataset")
    if not remembered:
        return None
    present = {g.get("dataset") for g in info.get("grids", [])}
    if remembered in present:
        return remembered
    for rebound, original in ((profile or {}).get("grid_aliases") or {}).items():
        if original == remembered and rebound in present:
            return rebound
    return remembered


def _still_there(info, ref):
    """Is the control this profile refers to still on the screen?"""
    if not ref:
        return True
    for f in info.get("filters", []) + info.get("unbound", []):
        if ref.get("column") and f.get("column") == ref["column"] \
                and f.get("dataset") == ref["dataset"]:
            return True
        if not ref.get("column") and f.get("control") == ref.get("control"):
            return True
    return False


def describe_change(profile, info):
    """What, if anything, has moved since this profile was proven.

    Returns a list of plain-English problems. An empty list means the profile
    can be replayed. A non-empty one means it must NOT be: the run falls back
    to discovering the screen again and says why, rather than quietly
    re-matching and possibly picking a different field."""
    problems = []
    for name in ("from", "to"):
        ref = profile.get(name)
        if ref and not _still_there(info, ref):
            problems.append(f"the '{name}' date field {ref['column']!r} is gone")

    aliases = profile.get("grid_aliases")
    grid = profile.get("grid")
    if grid and grid.get("dataset"):
        wanted = canonical_grid(grid["dataset"], aliases)
        if not any(canonical_grid(g.get("dataset"), aliases) == wanted
                   for g in info.get("grids", [])):
            problems.append(f"the result grid {grid['dataset']!r} is gone")

    if not problems and profile.get("fingerprint") != fingerprint(info, aliases):
        problems.append("the screen's controls have changed since this was learned "
                        "(nothing the profile uses has moved, but it is no longer "
                        "the same screen)")
    return problems


# ---------------------------------------------------------------------------
# Load / save
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# The line between what the TOOL knows and what a USER did
# ---------------------------------------------------------------------------
#
# Everything a successful run proves falls into one of two halves, and only
# one of them can be given to somebody else:
#
#   the screen   which control is "from", which grid holds the result, where
#                the division tree lives, what shape the screen was. True for
#                anyone with access to that screen, and the expensive half to
#                work out - it costs a RECORD run per screen.
#
#   the user     which division they picked, which dates, which filter values,
#                the options they switched on, the command they ran.
#                Production data (CLAUDE.md 2.4) and DECISIONS - worthless, or
#                actively misleading, to anyone else.
#
# Shipping the first half means a new user opens a known screen and it simply
# works, without inheriting a single order number.
#
# `options` USED to ship (HISTORY.md Phase 76 onward), and that was wrong by
# this file's own reasoning above: "Create Date" vs "Plan Date" is exactly
# the kind of decision nothing on the screen records - the same sentence
# `save()`'s docstring already uses about WHY options are remembered at all.
# Shipping one person's choice inside the file that is supposed to be
# "what the screen has", not "what a report should mean", let a new user
# silently inherit somebody else's answer to a question they were never
# asked (HISTORY.md Phase 79.6). It is still remembered locally, per screen,
# exactly as before - `load()`'s local half is untouched - so a machine
# that HAS run a screen successfully keeps replaying its own proven choice.
# A brand new machine now makes that choice for itself, once, the same way
# it already chooses its own division and dates.
_SHIPPABLE_KEYS = ("screen", "title", "menuId", "fingerprint",
                   "opening_fingerprint", "from", "to", "grid")


def shippable(profile):
    """The half of a profile that describes the screen rather than the user.

    Built as an ALLOWLIST, never by removing known-bad keys: a future field
    added to the saved profile must be considered before it can ship, not
    leak because nobody remembered to exclude it. `gmes_profile`'s own
    docstring already applies that reasoning to what gets written at all."""
    if not profile:
        return None
    out = {key: profile[key] for key in _SHIPPABLE_KEYS if key in profile}

    tree = profile.get("division")
    if tree:
        # WHERE the division tree lives is a property of the screen. WHICH
        # division was ticked is the user's own business context, so `entry`
        # is dropped. Nothing in replay needs it - `run_screen()` reads only
        # `division.dataset`, as the tree to prefer when several hold the
        # same name.
        out["division"] = {"form": tree.get("form", ""),
                           "dataset": tree.get("dataset", "")}
    return out


# Files that exist but could not be used as a profile, path -> why. A file that
# silently vanishes from the list is the worst way to fail: the owner sees "my
# recording disappeared" and nothing says why (HISTORY.md Phase 84.7).
UNREADABLE = {}


def unreadable():
    """[(path, reason)] for profile files the last read could not use."""
    return sorted(UNREADABLE.items())


def _read(path):
    """A profile file as a dict, or None. Never raises.

    `utf-8-sig`: Notepad (and other editors) prefix a byte-order mark, which
    plain `utf-8` refused - so a hand-edited profile silently disappeared.
    Valid JSON of the wrong shape (a list, a number, `null`) used to be returned
    as-is and crashed `dict(...)` in `load()`, taking down the list, the Replay
    menu and every batch because of ONE file; only a dict is a profile."""
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        UNREADABLE[path] = f"{type(e).__name__}: {str(e)[:90]}"
        return None
    if not isinstance(data, dict):
        UNREADABLE[path] = f"holds a JSON {type(data).__name__}, not a profile"
        return None
    UNREADABLE.pop(path, None)
    return data


def load(code):
    """What is known about this screen: shipped structure, with anything this
    machine has since proved laid over the top.

    A new user has only the shipped half and the screen still opens, filters
    and exports - they supply their own dates. Once they run it, their own
    file wins outright, because it was proved against the screen as it is
    here rather than as it was wherever the shipped copy came from."""
    try:
        base = _read(shipped_path_for(code))
        local = _read(path_for(code))
    except ValueError:
        return None
    if not base and not local:
        return None
    merged = dict(base or {})
    merged.update(local or {})
    # The FILE NAME is the authority for which screen this is: a profile copied
    # or hand-edited may carry another code (or none), and every caller indexes
    # `profile["screen"]`.
    merged["screen"] = _safe_code(code)
    return _sanitise(merged)


# What each top-level field of a profile must be. A wrong-typed field is DROPPED
# (the screen then behaves as if that one thing had never been recorded) rather
# than left to crash whatever reads it - a replay, the plan, the list.
_FIELD_TYPES = {"values": dict, "grid": dict, "proved": dict,
                "grid_aliases": dict, "options": list}

# Valid choices for "export" - anything else is treated as absent, the same
# as a field that was never set (defensive: a hand-edited or corrupted
# profile must not crash a replay, only lose the one pinned preference).
_EXPORT_CHOICES = ("xlsx", "csv", "both", "none")


def _sanitise(profile):
    for key, wanted in _FIELD_TYPES.items():
        if key in profile and not isinstance(profile[key], wanted):
            profile.pop(key)
    for key in ("title", "learned", "screen"):
        if key in profile and not isinstance(profile[key], str):
            profile[key] = "" if profile[key] is None else str(profile[key])
    if "output_dir" in profile and not isinstance(profile["output_dir"], str):
        profile.pop("output_dir")
    if "export" in profile and profile["export"] not in _EXPORT_CHOICES:
        profile.pop("export")
    return profile


def export_shippable(code, dest_dir=None):
    """Write this screen's structural half out for shipping.

    Reads the LOCAL profile - the one with everything in it - and writes only
    what `shippable()` allows. Returns the path written, or None when there is
    nothing local to export."""
    local = _read(path_for(code))
    if not local:
        return None
    payload = shippable(local)
    directory = dest_dir or SHIPPED_DIR
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, f"{_safe_code(code)}.json")
    fd, temporary = tempfile.mkstemp(prefix=".gmes-shipped-", suffix=".partial",
                                     dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return path


def known():
    """Every screen this installation can offer, newest first.

    The union of what shipped with the tool and what this machine has proved
    for itself - a new user's list is therefore not empty, which is the whole
    point of shipping the structural half. This is what lets the tool OFFER
    what it knows instead of expecting a UI number to be remembered and typed
    correctly every time.

    Screens proved here sort first, because they carry a `learned` timestamp
    and shipped ones do not."""
    codes = set()
    for directory in (SHIPPED_DIR, SCREENS_DIR):
        try:
            names = os.listdir(directory)
        except OSError:
            continue
        for name in names:
            if name.lower().endswith(".json"):
                codes.add(name[:-5])

    out = []
    for code in sorted(codes):
        data = load(code)
        if data:
            out.append(data)
    out.sort(key=lambda d: str(d.get("learned") or ""), reverse=True)
    return out


def forget(code):
    try:
        os.remove(path_for(code))
        return True
    except OSError:
        return False


def last_values(profile):
    """What was typed last time: division, dates and named filters.

    Offered back as the defaults on the next run, so a screen taught once
    does not have to be described again. Kept in the profile because the
    whole promise of recording is that nothing has to be re-entered - a
    memory that holds the field NAMES but forgets the values still leaves
    the user typing everything.

    Local only: `screens/` is git-ignored, because a filter value can be a
    production order number."""
    profile = profile if isinstance(profile, dict) else {}
    # A hand-edited or damaged profile can hold the wrong TYPE at any key; every
    # reader downstream assumes a dict, so one bad value must not become a crash
    # in the list, the plan or a replay (HISTORY.md Phase 84.7).
    raw = profile.get("values")
    values = dict(raw) if isinstance(raw, dict) else {}
    if "sets" in values and not isinstance(values["sets"], dict):
        values["sets"] = {}
    if any(v for k, v in values.items() if k != "sets") or values.get("sets"):
        return values

    # Profiles written before the `values` block existed have nothing here,
    # and asking their owner to run the screen again just to teach the tool
    # what it already recorded would be absurd - the command that proved the
    # screen was stored all along. Recover from it.
    proved = profile.get("proved")
    command = proved.get("command", "") if isinstance(proved, dict) else ""
    command = command if isinstance(command, str) else ""
    recovered = {}
    for flag, key in (("--division", "division"), ("--from", "from"),
                      ("--to", "to")):
        found = re.search(re.escape(flag) + r"\s+(\S+)", command)
        if found and found.group(1) not in ("None", "none", ""):
            recovered[key] = found.group(1)
    if recovered:
        recovered.setdefault("sets", {})
        return recovered
    return values


def _merge_values(previous, fresh):
    """Keep the last NON-EMPTY answer for each field.

    A screen legitimately run with no division (Work Calendar needs none)
    must not thereby erase the division another run proved on a screen that
    does. Only a value actually supplied replaces what is remembered.

    `from`/`to` are the exception: a run that gave its dates through `sets`
    instead (an unbound field, no bound dataset column to hold `--from/--to`)
    passes an empty `from`/`to` here, and the rule above would keep whatever
    an EARLIER, differently-shaped recording had proved for those two keys -
    stale and now describing nothing this profile's `sets` still agrees with.
    Live on P3111UM00 and P3151WM00 (HISTORY.md, found 2026-09-22): `values.from/
    to` stuck at an old recorded day while `values.sets` correctly moved to a
    new one, so the Final Intent Verification compared the screen against the
    wrong remembered day and refused a correct replay ("shape changed" /
    "Period now reads X, not the Y this run set" - not swapped values, just one
    of the two stale). When this run's `sets` carries a date-shaped entry and it
    gave no `from`/`to` of its own, `from`/`to` are cleared rather than kept -
    the two mechanisms cannot describe two different days for the same
    profile."""
    merged = dict(last_values(previous or {}))
    fresh = fresh or {}
    fresh_sets = fresh.get("sets") or {}
    clear_bound_dates = False
    if fresh_sets and not str(fresh.get("from") or "").strip():
        import gmes_core as core          # local: gmes_core imports this module
        date_words = core._FROM_WORDS | core._TO_WORDS | core._DATE_WORDS
        clear_bound_dates = any(core.words(k) & date_words for k in fresh_sets)
    for key, value in fresh.items():
        if key == "sets":
            if value:
                merged["sets"] = dict(value)
            continue
        if key in ("from", "to") and clear_bound_dates:
            merged[key] = ""
            continue
        if str(value or "").strip():
            merged[key] = value
    merged.setdefault("sets", {})
    return merged


def _option_entry(option):
    """One remembered left-panel option, as a stable identity.

    Accepts what `gmes_core.run_screen()` resolved (a dict) or a bare string,
    so a caller that only has a label - an older entrance, a test - still
    writes something loadable. A string is kept as a label with no identity,
    which is precisely the pre-Phase-76 shape that `resolve_option()` knows
    how to migrate."""
    if isinstance(option, dict):
        entry = {k: str(option.get(k) or "")
                 for k in ("key", "name", "path", "label")}
        # An entry with no stable identity at all is a label, and saying so
        # keeps `load()` from presenting it as though it had one.
        if not entry["key"] and not entry["name"]:
            return entry["label"]
        return entry
    return str(option)


def save(code, title, menu_id, info, from_ref=None, to_ref=None,
         division=None, grid=None, rows=0, command="", options=(),
         values=None, opening_info=None, grid_aliases=None,
         output_dir=None, export=None):
    """Write what a successful run proved. Called only after the export.

    `output_dir`/`export` are a PINNED destination, not what every run
    happens to use - the caller (`gmes_core.run_screen`) passes them here
    only when they differ from the tool's own built-in default, so an
    ordinary run of any other screen never starts writing a destination
    into a profile that never had one. Omitting them here does not erase a
    previously pinned value: `run_screen` reads it back out of the loaded
    profile before resolving what to actually use, so a bare replay of a
    pinned screen still lands where it was pinned, and re-saves the same
    value here every time - the field only disappears if a later save
    explicitly resolves back to the tool's default (HISTORY.md Phase 84.28).

    `options` are the left-panel choices the person made while the screen was
    being learned - Plan Date rather than Create Date, PLANT rather than STD.
    They are decisions, not observations: nothing on the screen says which one
    the report is supposed to mean, and running against the wrong one returns
    a plausible, completely different answer. So they are remembered and
    re-applied, exactly like the from/to fields.

    Each option is stored as its STABLE IDENTITY - the control's own Nexacro
    `name` and the semantic key derived from it - with the visible label kept
    only as display metadata. Storing the label alone is what broke replay
    when G-MES rendered the same screen in Korean: `"Create Date"` could not
    be found because the control now reads `생성일`, though it is still named
    `btnCreate` (HISTORY.md Phase 76). A bare string is still accepted, and is
    what every profile written before that phase contains."""
    os.makedirs(SCREENS_DIR, exist_ok=True)
    # Aliases are remembered across runs, never dropped by one that happened
    # to open a window already past its re-bind and so saw only one name
    # (HISTORY.md Phase 82.18).
    aliases = dict((load(code) or {}).get("grid_aliases") or {})
    aliases.update(grid_aliases or {})
    if grid:
        grid = dict(grid, dataset=canonical_grid(grid.get("dataset"), aliases))
    data = {
        "screen": code.strip().upper(),
        "title": title or "",
        "menuId": menu_id or "",
        "learned": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "fingerprint": fingerprint(info, aliases),
        # References belong to the post-option panel, but replay begins on
        # the opening panel. Keep both shapes so each is checked at the
        # moment it actually exists.
        "opening_fingerprint": fingerprint(opening_info or info, aliases),
        "from": from_ref,
        "to": to_ref,
        "division": division,
        "grid": grid_ref(grid),
        "options": [_option_entry(o) for o in options],
        # MERGED, never blindly replaced. A run that named no division and no
        # dates would otherwise wipe the values a previous run had proved -
        # which is exactly what happened to P1111UM00: recorded with VD and a
        # date range, then run once with everything blank, and the memory came
        # back empty. "Never forget" has to mean an empty answer does not
        # erase a remembered one.
        "values": _merge_values(load(code), values),
        "proved": {"rows": rows, "command": command},
    }
    if output_dir:
        data["output_dir"] = output_dir
    if export:
        data["export"] = export
    if aliases:
        data["grid_aliases"] = aliases
    path = path_for(code)
    fd, temporary = tempfile.mkstemp(prefix=".gmes-profile-", suffix=".partial", dir=SCREENS_DIR)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return path


def summary(profile):
    """One line for the log."""
    if not profile:
        return "none"
    bits = []
    for name in ("from", "to"):
        ref = profile.get(name)
        if ref:
            bits.append(f"{name}={ref['column'] or ref['control']}")
    if (profile.get("grid") or {}).get("dataset"):
        bits.append(f"grid={profile['grid']['dataset']}")
    return f"learned {profile.get('learned', '?')}  " + "  ".join(bits)


def main(argv):
    """python gmes_profile.py export [CODE ...]   - prepare screens for shipping
       python gmes_profile.py list                - what this installation knows

    `export` copies the STRUCTURAL half of what this machine has proved into
    `screens_known/`, which is committed and ships to every user. It never
    copies a division, a date, a filter value or the command that was run -
    see `shippable()`. With no codes it exports everything proved locally."""
    if not argv or argv[0] not in ("export", "list"):
        print(main.__doc__)
        return 2

    if argv[0] == "list":
        for profile in known():
            where = "proved here" if profile.get("learned") else "shipped"
            print(f"  {profile.get('screen', '?'):<12} {where:<12} "
                  f"{profile.get('title', '')}")
        return 0

    codes = [c.upper() for c in argv[1:]]
    if not codes:
        try:
            codes = [n[:-5] for n in sorted(os.listdir(SCREENS_DIR))
                     if n.lower().endswith(".json")]
        except OSError:
            codes = []
    if not codes:
        print("Nothing has been proved on this machine yet - run a screen first.")
        return 1

    for code in codes:
        try:
            written = export_shippable(code)
        except ValueError as e:
            print(f"  {code:<12} skipped ({e})")
            continue
        print(f"  {code:<12} " + (f"-> {os.path.basename(written)}" if written
                                  else "skipped (nothing proved locally)"))
    print("\nStructure only. No division, dates, filter values or commands were "
          "copied.\nReview with `git diff` before committing.")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv[1:]))

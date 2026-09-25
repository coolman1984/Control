"""
Terminal presentation for the G-MES tools - colour, panels, and step lists.

Everything here degrades rather than breaks. Three separate things can be
missing on a corporate Windows machine, and each is detected rather than
assumed:

  * **ANSI colour.** A Windows console understands escape codes only after
    ENABLE_VIRTUAL_TERMINAL_PROCESSING is switched on. If that call fails,
    or the output is redirected to a file, every colour becomes an empty
    string and the layout still lines up, because colour is never used to
    carry meaning on its own.
  * **Box-drawing characters.** A console on code page 437 cannot encode
    them. They are tested against the real output encoding once, at import,
    and swapped for +-| if they would raise.
  * **A terminal at all.** Piped into a file or another program, the output
    stays plain.

Nothing here decides anything. It is presentation only, so a display fault
can never change what the automation does.
"""
import os
import shutil
import sys
import textwrap

WIDTH = min(78, max(60, shutil.get_terminal_size((80, 25)).columns - 2))


# ---------------------------------------------------------------------------
# Capability detection
# ---------------------------------------------------------------------------

def _enable_vt():
    """Turn on escape-code handling for this console. Windows only."""
    if os.name != "nt":
        return True
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)          # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))
    except Exception:
        return False


def _can_print(text):
    """Whether this console's encoding can actually render `text`."""
    try:
        text.encode(sys.stdout.encoding or "ascii")
        return True
    except (UnicodeEncodeError, LookupError, TypeError):
        return False


COLOUR = bool(getattr(sys.stdout, "isatty", lambda: False)()) and _enable_vt()
UNICODE = _can_print("─│╭╮╰╯✓✗▸•")


def _c(code):
    return f"\033[{code}m" if COLOUR else ""


DIM = _c("2")
BOLD = _c("1")
RESET = _c("0")
CYAN = _c("36")
BLUE = _c("94")
GREEN = _c("32")
YELLOW = _c("33")
RED = _c("31")
GREY = _c("90")
WHITE = _c("97")

if UNICODE:
    H, V, TL, TR, BL, BR = "─", "│", "╭", "╮", "╰", "╯"
    TICK, CROSS, ARROW, DOT, INFO, WARN = "✓", "✗", "▸", "·", "i", "!"
else:
    H, V, TL, TR, BL, BR = "-", "|", "+", "+", "+", "+"
    TICK, CROSS, ARROW, DOT, INFO, WARN = "OK", "X", ">", ".", "i", "!"


def visible_len(text):
    """Length as printed - escape codes take no space on screen."""
    out, i = 0, 0
    while i < len(text):
        if text[i] == "\033":
            while i < len(text) and text[i] != "m":
                i += 1
        else:
            out += 1
        i += 1
    return out


# ---------------------------------------------------------------------------
# Blocks
# ---------------------------------------------------------------------------

def rule(char=None):
    print(f"  {GREY}{(char or H) * (WIDTH - 4)}{RESET}")


def banner(title, subtitle="", accent=CYAN):
    inner = WIDTH - 4
    print()
    print(f"  {accent}{TL}{H * inner}{TR}{RESET}")
    _boxed(f"{BOLD}{WHITE}{title}{RESET}", accent, inner)
    if subtitle:
        _boxed(f"{GREY}{subtitle}{RESET}", accent, inner)
    print(f"  {accent}{BL}{H * inner}{BR}{RESET}")


def _boxed(text, accent, inner):
    pad = inner - 2 - visible_len(text)
    print(f"  {accent}{V}{RESET} {text}{' ' * max(0, pad)} {accent}{V}{RESET}")


def section(title):
    print()
    print(f"  {BOLD}{BLUE}{title.upper()}{RESET}")
    print(f"  {GREY}{H * min(len(title) + 6, WIDTH - 4)}{RESET}")


def field(label, value, width=14):
    print(f"    {GREY}{label:<{width}}{RESET}{WHITE}{value}{RESET}")


def wrapped_field(label, value, width=14, indent="    "):
    """Print a labelled value without allowing it to run past the console.

    The value is wrapped independently of ANSI colour codes.  This is useful
    for discovered control names and command examples, which can be longer
    than the compact one-line fields used elsewhere in the workflow summary.
    """
    label = str(label)
    value = str(value)
    label_width = max(width, len(label))
    available = max(1, WIDTH - len(indent) - label_width)
    lines = textwrap.wrap(value, width=available, break_long_words=True,
                          break_on_hyphens=False) or [""]
    prefix = f"{indent}{GREY}{label:<{label_width}}{RESET}"
    print(f"{prefix}{WHITE}{lines[0]}{RESET}")
    continuation = " " * (len(indent) + label_width)
    for line in lines[1:]:
        print(f"{continuation}{WHITE}{line}{RESET}")


def bullet(text, mark=None, colour=None):
    print(f"    {colour or CYAN}{mark or ARROW}{RESET} {text}")


def note(text, kind="info"):
    colour = {"info": CYAN, "warn": YELLOW, "bad": RED}.get(kind, CYAN)
    mark = {"info": INFO, "warn": WARN, "bad": CROSS}.get(kind, INFO)
    print(f"      {colour}[{mark}]{RESET} {GREY}{text}{RESET}")


def step(number, label, detail, ok=True, seconds=None):
    """One numbered step: a tick, the label, leaders, then what happened.

    The detail is put on its own line when it will not fit, rather than
    wrapping into the leader dots and turning the column into noise."""
    mark = f"{GREEN}{TICK}{RESET}" if ok else f"{RED}{CROSS}{RESET}"
    stamp = f" {GREY}{seconds:>5.1f}s{RESET}" if seconds is not None else ""
    head = f"    {mark} {GREY}{number:>2}{RESET}  {WHITE}{label}{RESET}"
    used = visible_len(head) + visible_len(stamp)
    room = WIDTH - used - 4
    if len(detail) <= room:
        dots = f"{GREY}{DOT * max(2, room - len(detail))}{RESET}"
        print(f"{head} {dots} {detail}{stamp}")
    else:
        print(f"{head} {GREY}{DOT * max(2, room)}{RESET}{stamp}")
        print(f"          {GREY}{detail}{RESET}")


def result(ok, title, lines=()):
    accent = GREEN if ok else RED
    banner(title, accent=accent)
    for line in lines:
        print(f"    {line}")


def phase(recording, screen, learned=""):
    """The setting-up/running banner - the one thing a viewer must not miss."""
    if recording:
        banner(f"SETTING UP  {DOT}  {screen} is new",
               "It will work the screen out as it goes, and remember it "
               "if the run succeeds.", accent=YELLOW)
    else:
        banner(f"RUNNING  {DOT}  {screen} was set up {learned}",
               "It will check the screen still matches, then reuse what it "
               "knows.", accent=GREEN)


def controls_footer():
    """The one-line reminder of the global prompt controls - printed once per
    task, not repeated in every question's own hint text."""
    print(f"    {GREY}[Enter] accept    B back    H help    C cancel    Q quit{RESET}")


# ===========================================================================
# The application frame - screens, tables, badges and arrow-key menus
# (HISTORY.md Phase 94.2)
# ===========================================================================
#
# Everything below still obeys the rule at the top of this file: presentation
# only, and it degrades. Arrow-key menus need three things at once - a real
# keyboard on stdin, a real console on stdout, and escape codes that work
# (COLOUR). Missing any one of them, or with GMES_PLAIN=1 set, `INTERACTIVE`
# is False and the front end falls back to the numbered questions it has
# always asked, word for word - which is also what every offline test and
# every piped/scripted run sees.

MAGENTA = _c("35")
REVERSE = _c("7")
HIDE_CURSOR = "\033[?25l" if COLOUR else ""
SHOW_CURSOR = "\033[?25h" if COLOUR else ""

if UNICODE:
    SEP, ELLIPSIS, UP, DOWN, CHECK_ON, CHECK_OFF, POINTER = "›", "…", "↑", "↓", "◉", "○", "❯"
else:
    SEP, ELLIPSIS, UP, DOWN, CHECK_ON, CHECK_OFF, POINTER = ">", "...", "^", "v", "[x]", "[ ]", ">"


def _interactive():
    if os.environ.get("GMES_PLAIN", "").strip() not in ("", "0"):
        return False
    try:
        return bool(COLOUR and sys.stdin.isatty() and sys.stdout.isatty())
    except (AttributeError, ValueError):
        return False


INTERACTIVE = _interactive()


def set_plain(plain):
    """A person's own choice (Settings) overrides detection - never the
    other way round: plain can always be forced, fancy only where it works."""
    global INTERACTIVE
    INTERACTIVE = False if plain else _interactive()


def clip(text, width):
    """`text` cut to `width` visible characters, with an ellipsis when cut.
    Plain text only - callers colour AFTER clipping."""
    text = str(text)
    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    return text[:max(0, width - len(ELLIPSIS))] + ELLIPSIS


def clear():
    if INTERACTIVE:
        print("\033[2J\033[H", end="")


def screen(title, crumbs=(), status=""):
    """Start a new application screen: clear it (interactive only), then the
    app bar - name on the left, where you are on the right - and an optional
    status line. The same frame on every screen is what makes it read as one
    program rather than a scroll of questions."""
    clear()
    inner = WIDTH - 4
    trail = f" {SEP} ".join(("Home",) + tuple(crumbs)) if crumbs else "Home"
    left = " G-MES REPORT ASSISTANT "
    right = f" {clip(trail, max(8, inner - len(left) - 6))} "
    fill = max(1, inner - 2 - len(left) - len(right))
    print()
    print(f"  {CYAN}{TL}{H}{RESET}{BOLD}{WHITE}{left}{RESET}{CYAN}{H * fill}{RESET}"
          f"{GREY}{right}{RESET}{CYAN}{H}{TR}{RESET}")
    heading = f"{BOLD}{title}{RESET}"
    print(f"  {CYAN}{V}{RESET} {heading}{' ' * max(0, inner - 1 - visible_len(heading))}{CYAN}{V}{RESET}")
    if status:
        for line in textwrap.wrap(status, inner - 2) or [""]:
            print(f"  {CYAN}{V}{RESET} {GREY}{line}{RESET}"
                  f"{' ' * max(0, inner - 1 - len(line))}{CYAN}{V}{RESET}")
    print(f"  {CYAN}{BL}{H * inner}{BR}{RESET}")


def badge(text, kind="info"):
    colour = {"good": GREEN, "warn": YELLOW, "bad": RED, "info": CYAN,
              "dim": GREY}.get(kind, CYAN)
    return f"{colour}{BOLD}{text}{RESET}" if COLOUR else f"[{text}]"


def table(columns, rows, indent=4):
    """A table with a header and aligned columns that fits the console.

    `columns`: [(title, min_width, grow)] - the grow column(s) take whatever
    width is left. Cells are plain text (clipped with an ellipsis, never
    silently cut mid-cell without a mark); a cell may be a (text, colour)
    pair to colour it after clipping."""
    widths = [max(len(t), m) for t, m, _g in columns]
    for row in rows:
        for i, cell in enumerate(row):
            text = cell[0] if isinstance(cell, tuple) else cell
            if not columns[i][2]:
                widths[i] = max(widths[i], len(str(text)))
    spare = WIDTH - indent - 2 - sum(widths) - 2 * (len(columns) - 1)
    growers = [i for i, c in enumerate(columns) if c[2]]
    for i in growers:
        widths[i] = max(widths[i], widths[i] + spare // max(1, len(growers)))

    last = len(columns) - 1
    wrap_last = bool(columns[last][2])

    def line(cells, header=False):
        out, more = [], []
        for i, cell in enumerate(cells):
            text, colour = (cell if isinstance(cell, tuple) else (cell, ""))
            text = str(text)
            if i == last and wrap_last and not header and len(text) > widths[i]:
                # The last column wraps instead of being cut - it is where
                # the sentence that explains a row usually is.
                parts = textwrap.wrap(text, widths[i]) or [""]
                text, more = parts[0], parts[1:]
            text = clip(text, widths[i]).ljust(widths[i])
            if header:
                out.append(f"{GREY}{BOLD}{text}{RESET}")
            else:
                out.append(f"{colour}{text}{RESET}" if colour else text)
        print(" " * indent + "  ".join(out).rstrip())
        lead = indent + sum(widths[:last]) + 2 * last
        colour = cells[last][1] if isinstance(cells[last], tuple) else ""
        for extra in more:
            print(" " * lead + (f"{colour}{extra}{RESET}" if colour else extra))

    line([t for t, _m, _g in columns], header=True)
    print(" " * indent + f"{GREY}{H * min(WIDTH - indent - 2, sum(widths) + 2 * (len(widths) - 1))}{RESET}")
    for row in rows:
        line(row)


def keys_hint(pairs):
    """The one-line key legend at the foot of an interactive screen."""
    return "   ".join(f"{WHITE}{k}{RESET} {GREY}{v}{RESET}" for k, v in pairs)


# ---------------------------------------------------------------------------
# Keys
# ---------------------------------------------------------------------------

_WIN_SPECIAL = {"H": "up", "P": "down", "K": "left", "M": "right", "I": "pgup",
                "Q": "pgdn", "G": "home", "O": "end", "S": "delete"}
_POSIX_SEQ = {"[A": "up", "[B": "down", "[D": "left", "[C": "right", "[5~": "pgup",
              "[6~": "pgdn", "[H": "home", "[F": "end", "OH": "home", "OF": "end",
              "[1~": "home", "[4~": "end", "[3~": "delete"}


def decode_key(first, more):
    """One key press -> a name ('up', 'enter', 'esc', 'space', 'backspace',
    'tab'...) or the character itself. `more()` returns the next character
    already waiting, or '' if none - which is how a lone Esc is told from the
    start of an arrow key's escape sequence. Pure, so every mapping has a test."""
    if first in ("\x00", "\xe0"):                           # Windows console prefix
        return _WIN_SPECIAL.get(more(), "unknown")
    if first == "\x03":
        raise KeyboardInterrupt
    if first in ("\r", "\n"):
        return "enter"
    if first in ("\x08", "\x7f"):
        return "backspace"
    if first == "\t":
        return "tab"
    if first == " ":
        return "space"
    if first == "\x1b":
        seq = ""
        while len(seq) < 3:
            ch = more()
            if not ch:
                break
            seq += ch
            if seq in _POSIX_SEQ:
                return _POSIX_SEQ[seq]
        return "esc" if not seq else _POSIX_SEQ.get(seq, "unknown")
    return first


def read_key():
    """Block for one key press from the real keyboard."""
    if os.name == "nt":
        import msvcrt
        first = msvcrt.getwch()
        return decode_key(first, lambda: msvcrt.getwch() if msvcrt.kbhit() or first in ("\x00", "\xe0") else "")
    import select
    import termios
    import tty
    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        first = os.read(fd, 1).decode(errors="replace")

        def more():
            ready, _, _ = select.select([fd], [], [], 0.05)
            return os.read(fd, 1).decode(errors="replace") if ready else ""
        return decode_key(first, more)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)


# ---------------------------------------------------------------------------
# A menu: the state machine (pure) and its drawing
# ---------------------------------------------------------------------------

class Menu:
    """What a list-with-a-cursor needs to remember, and what each key does.

    Items are dicts: {"label", "detail" (optional, dim, right of the label),
    "badge" (optional (text, kind)), "search" (optional extra words to match)}.
    Typing filters (every word must match, any case); Backspace edits the
    filter; Esc clears a filter, or - with none - goes back. In a multi-choice
    menu Space ticks, `*` ticks all/none, Enter confirms the ticked set.
    `handle()` returns None to keep going, or ("choose", index),
    ("many", [indexes]) or ("back",)."""

    def __init__(self, items, multi=False, height=10, chosen=(), start=0):
        self.items = list(items)
        self.multi = multi
        self.height = max(3, height)
        self.chosen = set(chosen)
        self.query = ""
        self.cursor = 0
        self.top = 0
        self.message = ""
        if 0 <= start < len(self.items):
            self.cursor = start
            self._scroll()

    def matches(self):
        words = self.query.lower().split()
        out = []
        for i, item in enumerate(self.items):
            hay = f"{i + 1} {item.get('label', '')} {item.get('detail', '')} {item.get('search', '')}".lower()
            if all(w in hay for w in words):
                out.append(i)
        return out

    def current(self):
        shown = self.matches()
        return shown[self.cursor] if shown and 0 <= self.cursor < len(shown) else None

    def _scroll(self):
        if self.cursor < self.top:
            self.top = self.cursor
        elif self.cursor >= self.top + self.height:
            self.top = self.cursor - self.height + 1

    def handle(self, key):
        shown = self.matches()
        self.message = ""
        last = max(0, len(shown) - 1)
        if key == "up":
            self.cursor = last if self.cursor <= 0 else self.cursor - 1
        elif key == "down":
            self.cursor = 0 if self.cursor >= last else self.cursor + 1
        elif key == "pgup":
            self.cursor = max(0, self.cursor - self.height)
        elif key == "pgdn":
            self.cursor = min(last, self.cursor + self.height)
        elif key == "home":
            self.cursor = 0
        elif key == "end":
            self.cursor = last
        elif key == "esc":
            if self.query:
                self.query, self.cursor, self.top = "", 0, 0
            else:
                return ("back",)
        elif key == "backspace":
            self.query, self.cursor, self.top = self.query[:-1], 0, 0
        elif key == "space" and self.multi:
            here = self.current()
            if here is not None:
                self.chosen.symmetric_difference_update({here})
        elif key == "*" and self.multi:
            self.chosen = set() if set(shown) <= self.chosen else self.chosen | set(shown)
        elif key == "enter":
            if self.multi:
                if not self.chosen:
                    self.message = "Nothing ticked - Space ticks one, * ticks all."
                    return None
                return ("many", sorted(self.chosen))
            here = self.current()
            if here is None:
                self.message = "Nothing matches - Backspace or Esc clears the search."
                return None
            return ("choose", here)
        elif key == "space":
            self.query += " "
        elif len(key) == 1 and key.isprintable():
            self.query += key
            self.cursor, self.top = 0, 0
        self._scroll()
        return None

    def lines(self, width=None):
        """A fixed number of lines - search, the window of rows, scroll marks,
        a preview of the highlighted row's detail, and a message - so a
        redraw always overwrites exactly the last one.

        Each row is its number, its label and - in a column of their own, so
        they line up - its badge. The highlighted row's detail is shown in
        full underneath, wrapped, rather than clipped into the row: the
        preview pane every good list-based tool has."""
        width = width or WIDTH
        shown = self.matches()
        numw = len(str(len(self.items)))
        badges = [len(it["badge"][0]) for it in self.items if it.get("badge")]
        badge_w = (max(badges) + 2) if badges else 0
        tick_w = 4 if self.multi else 0
        label_w = max(12, width - 8 - numw - tick_w - badge_w)
        out = [f"    {GREY}search:{RESET} {WHITE}{self.query}{RESET}{GREY}"
               f"{'_' if INTERACTIVE else ''}   {len(shown)} of {len(self.items)}{RESET}"
               if self.query or len(self.items) > self.height else ""]
        out.append(f"    {GREY}{UP} more{RESET}" if self.top > 0 else "")
        for row in range(self.top, self.top + self.height):
            if row >= len(shown):
                out.append("")
                continue
            i = shown[row]
            item = self.items[i]
            here = row == self.cursor
            tick = ""
            if self.multi:
                tick = (f"{GREEN}{CHECK_ON}{RESET} " if i in self.chosen
                        else f"{GREY}{CHECK_OFF}{RESET} ")
            label = clip(item.get("label", ""), label_w - 2).ljust(label_w - 2)
            mark = f"{CYAN}{POINTER}{RESET}" if here else " "
            body = f"{REVERSE}{BOLD} {label} {RESET}" if here else f" {label} "
            b = item.get("badge")
            tail = f" {badge(*b)}" if b else ""
            out.append(f"  {mark} {tick}{GREY}{i + 1:>{numw}}{RESET}{body}{tail}")
        more_below = self.top + self.height < len(shown)
        out.append(f"    {GREY}{DOWN} more{RESET}" if more_below else "")
        if any(it.get("detail") for it in self.items):
            here = self.current()
            detail = self.items[here].get("detail", "") if here is not None else ""
            wrapped = textwrap.wrap(detail, max(20, width - 8))[:2]
            wrapped += [""] * (2 - len(wrapped))
            out.append(f"    {GREY}{H * max(10, width - 8)}{RESET}")
            out.extend(f"    {WHITE}{w}{RESET}" if w else "" for w in wrapped)
        out.append(f"    {YELLOW}{self.message}{RESET}" if self.message else "")
        return out


def run_menu(menu, footer_pairs=(), reader=None, write=None):
    """Draw `menu` and drive it with key presses until it returns a result.
    The block is redrawn IN PLACE (cursor up, clear line), so the screen
    above it stays put. `reader`/`write` are injectable for tests."""
    reader = reader or read_key
    write = write or (lambda s: (sys.stdout.write(s), sys.stdout.flush()))
    footer = f"    {keys_hint(footer_pairs)}" if footer_pairs else ""
    drawn = 0
    write(HIDE_CURSOR)
    try:
        while True:
            block = menu.lines() + ([footer] if footer else [])
            if drawn:
                write(f"\033[{drawn}F")
            for line in block:
                write(f"\033[2K{line}\n")
            drawn = len(block)
            result = menu.handle(reader())
            if result is not None:
                # Fold the list away and leave one line saying what was
                # chosen - the next question starts clean, and the choice
                # stays readable above it (and in the log).
                write(f"\033[{drawn}F")
                for _ in range(drawn):
                    write("\033[2K\n")
                write(f"\033[{drawn}F")
                if result[0] == "choose":
                    write(f"    {GREEN}{TICK}{RESET} {menu.items[result[1]].get('label', '').strip()}\n")
                elif result[0] == "many":
                    names = [menu.items[i].get("label", "").split()[0] for i in result[1]]
                    write(f"    {GREEN}{TICK}{RESET} {len(names)} chosen: {', '.join(names)}\n")
                return result
    finally:
        write(SHOW_CURSOR)

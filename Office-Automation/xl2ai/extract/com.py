"""Excel COM plumbing: session, retry, watchdog, dialog detection, crash recovery."""
from __future__ import annotations

import gc
import threading
import time

from ..core.procs import kill_pid, pid_alive
from .common import BUSY_CODES, DEAD_CODES, XL_CALC_MANUAL, log, pythoncom, pywintypes, win32, win32api, win32gui, win32process

PROCESS_SET_QUOTA, PROCESS_TERMINATE = 0x0100, 0x0001


class ExcelDied(Exception):
    """Excel crashed, hung (watchdog) or was disconnected; the caller may restart it and retry."""


def com_codes(exc):
    codes = set()
    if isinstance(exc, pywintypes.com_error):
        codes.add(exc.hresult)
        info = exc.excepinfo
        if info and len(info) > 5 and info[5]:
            codes.add(info[5])
    return codes


def com_msg(exc):
    if isinstance(exc, pywintypes.com_error):
        info = exc.excepinfo
        if info and len(info) > 2 and info[2]:
            return str(info[2]).strip()
        return str(exc.strerror or exc).strip()
    return f"{type(exc).__name__}: {exc}"


def bind_to_job(pid):
    """Put our Excel into a Windows job object that kills it when this Python process dies for any reason
    (crash, taskkill, Ctrl+C). Returns the job handle to keep alive, or None if binding is not possible."""
    try:
        import win32job
        job = win32job.CreateJobObject(None, "")
        info = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
        info["BasicLimitInformation"]["LimitFlags"] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, info)
        h = win32api.OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, False, pid)
        try:
            win32job.AssignProcessToJobObject(job, h)
        finally:
            win32api.CloseHandle(h)
        return job
    except Exception as e:
        log("WARN", f"Could not bind Excel to a kill-on-exit job ({com_msg(e)}); "
                    f"if this tool is killed, Excel may keep running")
        return None


def find_dialog(pid):
    """Title of a visible dialog owned by Excel process `pid`, else None.

    Excel's own dialogs (e.g. the 'Password' prompt, verified) use window class bosa_sdm_*; Windows common
    dialogs use #32770. The main XLMAIN window is deliberately not matched.
    """
    found = []

    def visit(hwnd, _):
        try:
            if win32gui.IsWindowVisible(hwnd) and win32process.GetWindowThreadProcessId(hwnd)[1] == pid:
                cls = win32gui.GetClassName(hwnd)
                if cls == "#32770" or cls.startswith("bosa_sdm"):
                    found.append(win32gui.GetWindowText(hwnd))
        except Exception:
            pass
        return True
    try:
        win32gui.EnumWindows(visit, None)
    except Exception:
        pass
    return found[0] if found else None


class Watchdog:
    """Kills ONE process (our private Excel) if a COM call runs longer than `seconds`, or if a modal dialog
    (password, repair, permission prompt...) has been blocking it for DIALOG_GRACE seconds: nobody can answer it."""
    DIALOG_GRACE = 8.0

    def __init__(self, pid, seconds):
        self.pid, self.seconds = pid, seconds
        self.fired, self.dialog = False, None
        self._stop, self._thread = threading.Event(), None

    def _run(self):
        deadline = time.monotonic() + self.seconds
        since = None
        while not self._stop.wait(1.0):
            now = time.monotonic()
            title = find_dialog(self.pid)
            if title is None:
                since = None
            else:
                since = since or now
                if now - since >= self.DIALOG_GRACE:
                    self.dialog = title or "(untitled)"
                    break
            if now >= deadline:
                break
        else:
            return
        self.fired = True
        kill_pid(self.pid)

    def __enter__(self):
        if self.pid:
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        return False

    def reason(self):
        if self.dialog:
            return (f"Excel is waiting for user input (dialog '{self.dialog}'). This is usually a password prompt "
                    f"or a repair/permission dialog, so the file cannot be read unattended.")
        return f"Excel did not answer within {self.seconds:.0f}s (watchdog)"


def friendly_open_error(exc):
    text = com_msg(exc)
    low = text.lower()
    if any(w in low for w in ("locked", "in use", "sharing violation", "being used", "another process")):
        return f"The file is open or locked by another program. Close it and try again. ({text})"
    if "password" in low or "protected" in low:
        return f"The file is password protected and cannot be opened. ({text})"
    return (f"Excel could not open the file. It may be password protected, corrupted, in an unsupported "
            f"format, or not an Excel file. ({text})")


class ExcelSession:
    def __init__(self, path, visible=False, open_timeout=300, call_timeout=300):
        self.path, self.visible = path, visible
        self.open_timeout, self.call_timeout = open_timeout, call_timeout
        self.app = self.wb = self.pid = None
        self.restarts = 0
        self._job = None

    # ---- lifecycle -----------------------------------------------------------------------------
    def start(self):
        last = None
        for _ in range(3):
            try:
                self.app = win32.DispatchEx("Excel.Application")
                break
            except Exception as e:                      # Excel still shutting down / DRM agent busy
                last = e
                time.sleep(2)
        else:
            raise RuntimeError(f"Could not start Excel: {com_msg(last)}")
        try:
            self.pid = win32process.GetWindowThreadProcessId(self.app.Hwnd)[1]
        except Exception:
            self.pid = None
        log("INFO", f"Excel started (pid {self.pid})")          # tests and operators can verify this pid exits
        if self.pid:
            self._job = bind_to_job(self.pid)
        for prop, val in (("Visible", self.visible), ("ScreenUpdating", False), ("DisplayAlerts", False),
                          ("EnableEvents", False), ("AskToUpdateLinks", False), ("AutomationSecurity", 3)):
            try:
                setattr(self.app, prop, val)
            except Exception:
                pass
        try:  # calculation mode is taken from the first workbook: use a blank one so the source never recalcs
            self.app.Workbooks.Add()
            self.app.Calculation = XL_CALC_MANUAL
        except Exception:
            pass

    def open(self):
        with Watchdog(self.pid, self.open_timeout) as wd:
            try:
                # Filename, UpdateLinks=0, ReadOnly=True, Format=<missing>, Password="" (never prompt)
                self.wb = self.app.Workbooks.Open(self.path, 0, True, pythoncom.Missing, "")
            except pywintypes.com_error as e:
                if wd.dialog:
                    raise RuntimeError(wd.reason()) from None       # a prompt nobody can answer: not a crash
                if wd.fired:
                    raise ExcelDied(wd.reason()) from None
                raise RuntimeError(friendly_open_error(e)) from None

    def restart(self):
        self.restarts += 1
        log("WARN", f"Restarting Excel (restart #{self.restarts})")
        self.close()
        self.start()
        self.open()

    def close(self):
        pid = self.pid
        try:
            if self.app is not None:
                for wb in list(self.app.Workbooks):
                    try:
                        wb.Close(False)
                    except Exception:
                        pass
                self.app.Quit()
        except Exception:
            pass
        self.wb = self.app = None
        gc.collect()
        if pid:
            for _ in range(50):
                if not pid_alive(pid):
                    break
                time.sleep(0.2)
            else:
                log("WARN", f"Excel (pid {pid}) did not exit; terminating it")
                kill_pid(pid)
        self._job = None                                   # closing the job handle also ends anything still in it
        self.pid = None

    # ---- guarded calls ---------------------------------------------------------------------------
    def call(self, fn, timeout=None):
        delay = 0.25
        for _ in range(40):
            wd = Watchdog(self.pid, timeout or self.call_timeout)
            try:
                with wd:
                    return fn()
            except pywintypes.com_error as e:
                codes = com_codes(e)
                if codes & BUSY_CODES and self.pid and pid_alive(self.pid):
                    time.sleep(delay)
                    delay = min(delay * 1.5, 3)
                    continue
                if wd.fired or codes & DEAD_CODES or (self.pid and not pid_alive(self.pid)):
                    raise ExcelDied(wd.reason() if wd.fired else com_msg(e)) from None
                raise
        raise ExcelDied("Excel kept rejecting calls (busy)")

    def robust(self, fn):
        """call(), but a dead Excel is restarted once and the (stateless) call repeated."""
        try:
            return self.call(fn)
        except ExcelDied as e:
            log("WARN", f"Excel died ({e})")
            self.restart()
            return self.call(fn)

    def is_dead(self, exc):
        """True if exc means the Excel process is gone/hung (as opposed to an ordinary COM failure)."""
        if isinstance(exc, ExcelDied):
            return True
        return isinstance(exc, pywintypes.com_error) and bool(
            com_codes(exc) & DEAD_CODES or (self.pid and not pid_alive(self.pid)))

    def sheet(self, idx):
        return self.call(lambda: self.wb.Sheets(idx))

    def read_block(self, idx, r0, r1, c0, ncols):
        """Rows r0..r1 (absolute), ncols columns starting at c0, as a tuple of row tuples."""
        def go():
            ws = self.wb.Sheets(idx)
            v = ws.Range(ws.Cells(r0, c0), ws.Cells(r1, c0 + ncols - 1)).Value2
            return v if isinstance(v, tuple) else ((v,),)
        return self.call(go)

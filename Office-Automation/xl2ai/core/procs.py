"""Process helpers.

Windows gets exact Win32 process handling. Other platforms use a tiny compatibility path so metadata-only stages and
unit tests can run without Excel. Extraction itself remains Windows/Excel-only.
"""
from __future__ import annotations

import ctypes
import os

PROCESS_TERMINATE = 0x0001
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
STILL_ACTIVE = 259

if os.name == "nt":
    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.OpenProcess.restype = ctypes.c_void_p
    _k32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    _k32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
    _k32.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    _k32.CloseHandle.argtypes = [ctypes.c_void_p]
else:
    _k32 = None


class _MemoryStatusEx(ctypes.Structure):
    _fields_ = [("dwLength", ctypes.c_uint32), ("dwMemoryLoad", ctypes.c_uint32),
                ("ullTotalPhys", ctypes.c_uint64), ("ullAvailPhys", ctypes.c_uint64),
                ("ullTotalPageFile", ctypes.c_uint64), ("ullAvailPageFile", ctypes.c_uint64),
                ("ullTotalVirtual", ctypes.c_uint64), ("ullAvailVirtual", ctypes.c_uint64),
                ("ullAvailExtendedVirtual", ctypes.c_uint64)]


def memory_status():
    """Best-effort free-memory snapshot. Empty dict outside Windows."""
    if os.name != "nt":
        return {}
    st = _MemoryStatusEx()
    st.dwLength = ctypes.sizeof(st)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
        return {}
    return {"phys_free_mb": st.ullAvailPhys // 2 ** 20, "commit_free_mb": st.ullAvailPageFile // 2 ** 20,
            "load_percent": st.dwMemoryLoad}


def pid_alive(pid):
    if not pid:
        return False
    if os.name != "nt":
        try:
            os.kill(int(pid), 0)
            return True
        except PermissionError:
            return True                                    # the process exists; we just may not signal it
        except (OSError, ValueError, TypeError):
            return False
    h = _k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, int(pid))
    if not h:
        return False
    try:
        code = ctypes.c_uint32()
        return bool(_k32.GetExitCodeProcess(h, ctypes.byref(code))) and code.value == STILL_ACTIVE
    finally:
        _k32.CloseHandle(h)


def kill_pid(pid):
    """Terminate exactly this process. Only ever call it with a pid this tool started."""
    if not pid:
        return
    if os.name != "nt":
        try:
            os.kill(int(pid), 9)
        except OSError:
            pass
        return
    h = _k32.OpenProcess(PROCESS_TERMINATE, 0, int(pid))
    if h:
        try:
            _k32.TerminateProcess(h, 1)
        finally:
            _k32.CloseHandle(h)

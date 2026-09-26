"""Running other programs safely: time limits, whole-tree kills, no proxy for local traffic."""
import json
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import __version__
from .errors import ControlError

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def child_env():
    env = dict(os.environ)
    local = "127.0.0.1,localhost"
    current = env.get("NO_PROXY") or env.get("no_proxy") or ""
    env["NO_PROXY"] = env["no_proxy"] = f"{current},{local}" if current else local
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


def kill_tree(pid):
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True,
                       creationflags=_NO_WINDOW)
    else:
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except OSError:
            pass


def run(cmd, *, cwd=None, timeout):
    p = subprocess.Popen(cmd, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, env=child_env(), text=True, encoding="utf-8",
                         errors="replace", creationflags=_NO_WINDOW,
                         start_new_session=(sys.platform != "win32"))
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        kill_tree(p.pid)
        p.communicate()
        shown = " ".join(Path(c).name for c in cmd[:3])
        raise ControlError("TIMEOUT", f"{shown} did not finish within {timeout}s",
                           "raise the timeout in config.toml, or check that tool on its own") from None
    return p.returncode, out, err


def start_background(cmd, *, cwd=None):
    """Launch a child that is meant to keep running until something else stops it (wad's own
    `record`, which blocks until `record-stop` touches its stop file) - unlike `run`, this never
    waits for it to exit."""
    return subprocess.Popen(cmd, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, env=child_env(), text=True, encoding="utf-8",
                            errors="replace", creationflags=_NO_WINDOW,
                            start_new_session=(sys.platform != "win32"))


class McpChild:
    """One long-lived MCP server child, restarted on demand after a crash or timeout."""

    def __init__(self, cmd, *, name, timeout=300):
        self.cmd, self.name, self.timeout = list(cmd), name, timeout
        self.p, self.q, self._id = None, None, 0
        self.lock = threading.Lock()

    @staticmethod
    def _pump(stream, q):
        for line in stream:
            q.put(line)
        q.put(None)

    def _send(self, msg):
        self.p.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
        self.p.stdin.flush()

    def _ensure(self, timeout=30):
        if self.p is not None and self.p.poll() is None:
            return
        self.p = subprocess.Popen(self.cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, env=child_env(), text=True,
                                  encoding="utf-8", errors="replace", creationflags=_NO_WINDOW,
                                  start_new_session=(sys.platform != "win32"))
        self.q = queue.Queue()
        threading.Thread(target=self._pump, args=(self.p.stdout, self.q), daemon=True).start()
        self._rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                 "clientInfo": {"name": "control", "version": __version__}},
                  min(timeout, 30) if timeout else 30)
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _rpc(self, method, params, timeout):
        self._id += 1
        mid = self._id
        try:
            self._send({"jsonrpc": "2.0", "id": mid, "method": method, "params": params})
        except OSError:
            self.p = None
            raise ControlError("CHILD_EXITED", f"{self.name} stopped unexpectedly",
                               "run control doctor") from None
        deadline = time.monotonic() + timeout
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                self.close()
                raise ControlError("TIMEOUT", f"{self.name} gave no answer within {timeout}s",
                                   "it was stopped; the next call starts it again")
            try:
                line = self.q.get(timeout=left)
            except queue.Empty:
                continue
            if line is None:
                self.p = None
                raise ControlError("CHILD_EXITED", f"{self.name} stopped unexpectedly",
                                   "the next call starts it again; if it repeats, run control doctor")
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if msg.get("id") != mid:
                continue
            if "error" in msg:
                raise ControlError("CHILD_ERROR", f"{self.name}: {msg['error'].get('message', '')}")
            return msg.get("result") or {}

    def request(self, method, params=None, timeout=None):
        with self.lock:
            self._ensure(timeout=timeout or 30)
            return self._rpc(method, params or {}, timeout or self.timeout)

    def call_tool(self, name, args, timeout=None):
        return self.request("tools/call", {"name": name, "arguments": args or {}}, timeout)

    def close(self):
        if self.p is not None:
            kill_tree(self.p.pid)
            self.p = None

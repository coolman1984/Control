"""Register the xl2ai MCP server with the agent hosts people actually use -- so nobody edits settings files.

    xl2ai connect --install all          # Claude Code, Codex CLI and Claude Desktop, whichever are present
    xl2ai connect --install codex        # just one
    xl2ai connect --install all --dry-run   # show what would change, change nothing

All three hosts speak MCP over stdio and start the server themselves; they only differ in where the one entry lives:

    Claude Code   -> `claude mcp add --scope user xl2ai -- <command>`   (Claude Code's own CLI does the writing)
    Codex CLI     -> [mcp_servers.xl2ai] in ~/.codex/config.toml         (CODEX_HOME overrides the folder)
    Claude Desktop-> "mcpServers" in claude_desktop_config.json          (%APPDATA%\\Claude on Windows)

No workspace is pinned by default: Claude Code and Codex start the server in the folder they were opened in, and the
server uses that folder when it holds Excel files; Claude Desktop has no folder, so the agent passes one per call.
Existing settings are preserved; a file is backed up to `.bak` before its first change.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys

NAME = "xl2ai"


def server_command(workspace=None):
    """[program, args...] that starts the MCP server -- the installed .exe when frozen, else this Python."""
    if getattr(sys, "frozen", False):
        cmd = [sys.executable, "serve"]
    else:
        cmd = [sys.executable, "-m", "xl2ai", "serve"]
    if workspace:
        cmd += ["--workspace", os.path.abspath(workspace)]
    return cmd


def _backup(path):
    if os.path.isfile(path) and not os.path.exists(path + ".bak"):
        shutil.copyfile(path, path + ".bak")


# ---- Claude Desktop ------------------------------------------------------------------------------------------
def claude_desktop_config_path():
    if os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Roaming")
    elif sys.platform == "darwin":
        base = os.path.join(os.path.expanduser("~"), "Library", "Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "Claude", "claude_desktop_config.json")


def install_claude_desktop(cmd, dry_run=False):
    path = claude_desktop_config_path()
    data = {}
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            text = f.read().strip()
        data = json.loads(text) if text else {}
    entry = {"command": cmd[0], "args": cmd[1:]}
    changed = data.get("mcpServers", {}).get(NAME) != entry
    if changed and not dry_run:
        data.setdefault("mcpServers", {})[NAME] = entry
        os.makedirs(os.path.dirname(path), exist_ok=True)
        _backup(path)
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(path + ".tmp", path)
    return {"host": "claude-desktop", "file": path, "changed": changed,
            "note": "restart Claude Desktop to load it" if changed else "already connected"}


# ---- Codex CLI -----------------------------------------------------------------------------------------------
def codex_config_path():
    base = os.environ.get("CODEX_HOME") or os.path.join(os.path.expanduser("~"), ".codex")
    return os.path.join(base, "config.toml")


def _toml_str(s):
    return json.dumps(str(s), ensure_ascii=False)


def install_codex(cmd, dry_run=False):
    path = codex_config_path()
    text = ""
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            text = f.read()
    block = (f"[mcp_servers.{NAME}]\n"
             f"command = {_toml_str(cmd[0])}\n"
             f"args = [{', '.join(_toml_str(a) for a in cmd[1:])}]\n"
             "startup_timeout_sec = 30\n"
             "tool_timeout_sec = 120            # `prepare` may wait up to a minute per call\n")
    # replace our own section (header up to the next [section] or end of file), leave everything else untouched
    pattern = re.compile(rf"(?ms)^\[mcp_servers\.{NAME}\]\n.*?(?=^\[|\Z)")
    new = pattern.sub(lambda m: block + ("\n" if m.group(0).endswith("\n\n") else ""), text) if pattern.search(text) \
        else (text.rstrip("\n") + "\n\n" if text.strip() else "") + block
    changed = new != text
    if changed and not dry_run:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        _backup(path)
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            f.write(new)
        os.replace(path + ".tmp", path)
    return {"host": "codex", "file": path, "changed": changed,
            "note": "start a new Codex session to load it" if changed else "already connected"}


# ---- Claude Code ---------------------------------------------------------------------------------------------
def install_claude_code(cmd, dry_run=False):
    exe = shutil.which("claude")
    line = ["claude", "mcp", "add", "--scope", "user", NAME, "--"] + cmd
    if not exe:
        return {"host": "claude-code", "changed": False, "missing": True,
                "note": "Claude Code not found on PATH; after installing it run: "
                        + " ".join(f'"{a}"' if " " in a else a for a in line)}
    if dry_run:
        return {"host": "claude-code", "changed": True, "note": "would run: " + " ".join(line)}
    subprocess.run([exe, "mcp", "remove", "--scope", "user", NAME], capture_output=True, text=True)
    res = subprocess.run([exe] + line[1:], capture_output=True, text=True)
    ok = res.returncode == 0
    return {"host": "claude-code", "changed": ok,
            "note": "connected for every folder you open with Claude Code" if ok
            else f"claude mcp add failed: {(res.stderr or res.stdout).strip()[:300]}"}


def _present(host):
    if host == "claude-code":
        return shutil.which("claude") is not None
    if host == "codex":
        return shutil.which("codex") is not None or os.path.isdir(os.path.dirname(codex_config_path()))
    return os.path.isdir(os.path.dirname(claude_desktop_config_path()))


INSTALLERS = {"claude-code": install_claude_code, "codex": install_codex, "claude-desktop": install_claude_desktop}


def install(hosts, workspace=None, dry_run=False):
    """[{host, changed, note, file?}] for each requested host ('all' = those found on this machine)."""
    cmd = server_command(workspace)
    if "all" in hosts:
        hosts = [h for h in INSTALLERS if _present(h)]
        if not hosts:
            return [{"host": "none", "changed": False,
                     "note": "no Claude Code, Codex or Claude Desktop found on this machine"}]
    return [INSTALLERS[h](cmd, dry_run) for h in hosts]

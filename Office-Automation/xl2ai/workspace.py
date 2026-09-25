"""Workspaces: how a folder of Excel files becomes something an agent can be pointed at.

The model, in one picture:

    D:/Sales Files/                 <- the user's Excel folder. Never modified, never moved.
        orders 2024.xlsx
        price list.xlsb
        .xl2ai/                     <- the workspace: everything xl2ai knows about this folder
            xl2ai.toml              <- which files, which engine (written by `xl2ai open`)
            AGENTS.md               <- how an agent should use this folder (for agents that browse files)
            data/
                current.json        <- which run is the trusted one
                latest/             <- stable copies of the trusted run's agent brief and context pack
                runs/<run_id>/...   <- the verified databases, catalog and briefs of each run

So "the project" is just the Excel folder: point an agent (or `xl2ai serve --workspace`) at it and everything is
found from there. When the Excel folder cannot be written to (a read-only network share, a DRM-managed folder),
the workspace lives under the user's home instead (`~/.xl2ai/workspaces/<name>-<hash>/`) and a small registry maps
the Excel folder to it, so pointing at the Excel folder still works.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys

from .core.errors import Xl2aiError

WORKSPACE_DIR = ".xl2ai"
CONFIG_NAME = "xl2ai.toml"


def home_dir():
    return os.path.abspath(os.environ.get("XL2AI_HOME") or os.path.join(os.path.expanduser("~"), ".xl2ai"))


def _registry_path():
    return os.path.join(home_dir(), "workspaces.json")


def _read_registry():
    try:
        with open(_registry_path(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _key(folder):
    return os.path.normcase(os.path.abspath(folder))


def _register(folder, config_path):
    reg = _read_registry()
    reg[_key(folder)] = config_path
    os.makedirs(home_dir(), exist_ok=True)
    tmp = _registry_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(reg, f, ensure_ascii=False, indent=1)
    os.replace(tmp, _registry_path())


def _writable(folder):
    probe = os.path.join(folder, f".xl2ai-write-test-{os.getpid()}")
    try:
        with open(probe, "w") as f:
            f.write("x")
        os.remove(probe)
        return True
    except OSError:
        return False


def resolve_workspace(target):
    """Config path for a target that may be: a config file, a workspace folder, or an Excel folder. None if the
    folder has never been opened."""
    if not target:
        return None
    target = os.path.abspath(os.path.expanduser(str(target)))
    if os.path.isfile(target):
        return target
    if not os.path.isdir(target):
        return None
    for cand in (os.path.join(target, CONFIG_NAME), os.path.join(target, WORKSPACE_DIR, CONFIG_NAME)):
        if os.path.isfile(cand):
            return cand
    registered = _read_registry().get(_key(target))
    if registered and os.path.isfile(registered):
        return registered
    return None


def _toml(value):
    return json.dumps(str(value).replace("\\", "/"), ensure_ascii=False)


def _slug(text):
    return re.sub(r"[^\w-]+", "-", str(text).strip(), flags=re.UNICODE).strip("-").lower() or "workspace"


def open_workspace(folder, name=None, engine="auto", recursive=True, home=False, wrapper_prefixes=()):
    """Create (or find) the workspace for an Excel folder. Returns {config, location, created, workspace_dir}."""
    folder = os.path.abspath(os.path.expanduser(str(folder)))
    if not os.path.isdir(folder):
        raise Xl2aiError("E_SRC_MISSING", f"not a folder: {folder}", "pass the folder that holds the Excel files")
    existing = resolve_workspace(folder)
    if existing:
        return {"config": existing, "location": "existing", "created": False,
                "workspace_dir": os.path.dirname(existing)}
    name = name or os.path.basename(folder.rstrip("/\\")) or "workspace"
    if not home and _writable(folder):
        ws = os.path.join(folder, WORKSPACE_DIR)
        pattern = "../**/*" if recursive else "../*"
        location = "beside_files"
    else:
        digest = hashlib.sha1(_key(folder).encode("utf-8")).hexdigest()[:8]
        ws = os.path.join(home_dir(), "workspaces", f"{_slug(name)}-{digest}")
        pattern = os.path.join(folder, "**", "*") if recursive else os.path.join(folder, "*")
        location = "home"
    os.makedirs(ws, exist_ok=True)
    lines = ["# xl2ai workspace for: " + folder.replace("\\", "/"),
             "# The Excel files are read, never modified. Everything below this folder can be deleted safely;",
             "# `xl2ai refresh` rebuilds it.", "",
             "[project]", f"name = {_toml(name)}", 'data_dir = "data"', "",
             "[environment]", f"wrapper_prefixes = [{', '.join(_toml(p) for p in wrapper_prefixes)}]", "",
             "[extract]", f"engine = {_toml(engine)}", ""]
    lines += ["[[sources]]",
              f"path = {_toml(pattern)}      # every supported file: Excel, Word, PowerPoint, PDF, e-mail, text", ""]
    config = os.path.join(ws, CONFIG_NAME)
    with open(config, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines))
    write_agents_md(ws, folder)
    if location == "home":
        _register(folder, config)
    return {"config": config, "location": location, "created": True, "workspace_dir": ws}


AGENTS_MD = """# This folder is prepared for AI agents by xl2ai

The Excel files in `{folder}` have been (or will be) read once into verified databases, profiled, and
summarised. **Do not open the Excel files yourself** -- everything below is faster, cheaper and checked.

## Best: connect as a tool server (MCP)

    python -m xl2ai serve --workspace "{folder}"

Then call `start` first. It tells you whether the data is ready, what every table is, the key numbers, what
looks wrong, how the files connect, and which tool to call next. `prepare` (re)builds when files changed.

## Without MCP: read files, run commands

1. Read `{latest}/agent_brief.md` -- the whole picture in plain words (key numbers, joins, gaps).
2. Run commands with `--config "{config}"`, e.g.
   `python -m xl2ai query find "customer" --config "{config}"`
   `python -m xl2ai query sql "*" "SELECT ..." --config "{config}"`
3. If `python -m xl2ai brief --config "{config}"` exits with 2, the data is stale: run `python -m xl2ai refresh`
   with the same `--config` first.

Rules: quote numbers with their evidence (table, Excel row); repeat any gap or disagreement the brief lists.
"""


def write_agents_md(ws, folder):
    path = os.path.join(ws, "AGENTS.md")
    text = AGENTS_MD.format(folder=folder.replace("\\", "/"),
                            latest=os.path.join(ws, "data", "latest").replace("\\", "/"),
                            config=os.path.join(ws, CONFIG_NAME).replace("\\", "/"))
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return path


def publish_latest(cfg, run_id):
    """Copy the trusted run's AI files to `<data_dir>/latest/` so agents that read files have one stable path."""
    import shutil
    src = os.path.join(cfg.runs_dir, run_id, "ai")
    if not os.path.isdir(src):
        return None
    dst = os.path.join(cfg.data_dir, "latest")
    os.makedirs(dst, exist_ok=True)
    for name in os.listdir(src):
        shutil.copyfile(os.path.join(src, name), os.path.join(dst, name + ".tmp"))
        os.replace(os.path.join(dst, name + ".tmp"), os.path.join(dst, name))
    with open(os.path.join(dst, "RUN"), "w", encoding="utf-8") as f:
        f.write(run_id + "\n")
    return dst


def main(argv=None):
    ap = argparse.ArgumentParser(prog="xl2ai open",
                                 description="Prepare a folder of Excel files for agents: creates its workspace.")
    ap.add_argument("folder", help="the folder that holds the Excel files")
    ap.add_argument("--name", help="project name (default: the folder name)")
    ap.add_argument("--engine", choices=("auto", "excel", "direct"), default="auto")
    ap.add_argument("--top-level-only", action="store_true", help="ignore Excel files in sub-folders")
    ap.add_argument("--home", action="store_true",
                    help="keep the workspace under your home folder instead of beside the Excel files")
    ap.add_argument("--wrapper-prefix", action="append", default=[],
                    help="byte prefix of a rights-management wrapper only Excel can open (repeatable)")
    ap.add_argument("--refresh", action="store_true", help="also build it now")
    args = ap.parse_args(argv)
    try:
        info = open_workspace(args.folder, args.name, args.engine, not args.top_level_only, args.home,
                              args.wrapper_prefix)
    except Xl2aiError as e:
        print(str(e), file=sys.stderr)
        return 1
    state = "created" if info["created"] else "already exists"
    print(f"workspace {state}: {info['workspace_dir']}")
    print(f"config: {info['config']}")
    if args.refresh:
        from .refresh import main as refresh_main
        return refresh_main(["--config", info["config"]])
    print(f'next: python -m xl2ai refresh --config "{info["config"]}"')
    print(f'then connect an agent: python -m xl2ai connect --workspace "{os.path.abspath(args.folder)}"')
    return 0


if __name__ == "__main__":
    sys.exit(main())

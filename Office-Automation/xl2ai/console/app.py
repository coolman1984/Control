"""`xl2ai watch` and `xl2ai diagnose`: the two commands that make a run observable.

  watch     -- run a refresh (or a simulation) with the live step-by-step view
  diagnose  -- explain a finished run from its journal, for a person or for an AI
"""
from __future__ import annotations

import argparse
import os
import sys

from ..core.config import load_config
from ..core.errors import Xl2aiError
from ..core.runs import current_run_id, list_runs
from ..observe import journal as journal_mod
from ..observe.bus import BUS, Bus
from ..observe.timeline import Timeline
from ..ui import panel, style as st
from ..ui.caps import Caps
from ..ui.console import Console
from . import demo as demo_mod, diagnose as diagnose_mod, files_view, run_view


def _console(args):
    if getattr(args, "plain", False):
        return Console(stream=sys.stdout, caps=Caps.plain(Caps.detect().width))
    return Console(stream=sys.stdout)


def _resolve_run_dir(cfg, run_id=None):
    rid = run_id or current_run_id(cfg)
    if not rid:
        runs = list_runs(cfg)
        rid = runs[0] if runs else None
    if not rid:
        raise Xl2aiError("E_STAGE_INPUT", "no run found", "run `xl2ai watch` or `xl2ai refresh` first")
    return rid, os.path.join(cfg.runs_dir, rid)


# ---- watch ---------------------------------------------------------------------------------------------------
def watch(args):
    console = _console(args)
    timeline = Timeline()
    view = run_view.RunView(console, timeline, show_sheets=not args.quiet_sheets)

    if args.demo:
        bus = Bus()
        view.attach(bus)
        view.start()
        try:
            demo_mod.play(bus, speed=1.0 if args.speed is None else args.speed, fail_at=args.fail_at)
        finally:
            view.finish()
        console.lines(run_view.summary(console, timeline))
        if args.files:
            console.lines(files_view.render(console, timeline))
        return 0 if timeline.ok else 1

    from ..core.log import silence
    from ..refresh import run_refresh
    cfg = load_config(args.config, required=not args.paths)
    if args.paths:
        cfg = cfg.with_sources(args.paths)
    BUS.reset()
    view.attach(BUS)
    view.start()
    was_silent = silence(True)                            # the view owns the screen while it is running
    code = 1
    try:
        code, run = run_refresh(cfg, force=args.force)
    finally:
        silence(was_silent)
        view.finish()
    console.lines(run_view.summary(console, timeline))
    if args.files:
        console.lines(files_view.render(console, timeline))
    console.blank()
    console.line("  " + console.styled(f"journal: {os.path.join(cfg.runs_dir, timeline.run_id or '', 'journal.jsonl')}",
                                       st.FAINT))
    return code


# ---- diagnose ------------------------------------------------------------------------------------------------
def diagnose(args):
    console = _console(args)
    if args.demo:
        timeline, events = demo_mod.build_timeline(fail_at=args.fail_at or "extract")
    else:
        cfg = load_config(args.config)
        rid, run_dir = _resolve_run_dir(cfg, args.run)
        events = journal_mod.read(journal_mod.path_for_run(run_dir))
        if not events:
            console.lines(panel.callout(
                console,
                f"No journal for run {rid}. Journals are written by runs started with `xl2ai watch` or "
                f"`xl2ai refresh`; older runs have none.", kind="warn", title="no journal"))
            return 1
        timeline = Timeline.replay(events)

    if args.ai:
        print(diagnose_mod.as_text(timeline, events, limit=args.context))
        return 0
    console.lines(diagnose_mod.render(console, timeline, events))
    if args.files:
        console.lines(files_view.render(console, timeline))
    return 0 if timeline.ok else 1


# ---- cli -----------------------------------------------------------------------------------------------------
def _add_common(parser):
    parser.add_argument("--config", help="path to xl2ai.toml (default: discovered upward from the current folder)")
    parser.add_argument("--plain", action="store_true", help="no colour, no redraw: safe for logs and pipes")
    parser.add_argument("--files", action="store_true", help="also show the per-file/per-sheet breakdown")


def build_parser(prog="xl2ai watch"):
    parser = argparse.ArgumentParser(prog=prog, description="Run a refresh with a live, step-by-step view.")
    _add_common(parser)
    parser.add_argument("paths", nargs="*", help="override the configured sources")
    parser.add_argument("--force", action="store_true", help="re-extract every source even when unchanged")
    parser.add_argument("--demo", action="store_true", help="simulate a run (no Excel needed) to see the interface")
    parser.add_argument("--fail-at", metavar="STAGE", help="with --demo: break at this stage")
    parser.add_argument("--speed", type=float, help="with --demo: scale the pauses (0 = instant)")
    parser.add_argument("--quiet-sheets", action="store_true", help="do not print a line per sheet")
    return parser


def build_diagnose_parser(prog="xl2ai diagnose"):
    parser = argparse.ArgumentParser(prog=prog, description="Explain what a run did and where it failed.")
    _add_common(parser)
    parser.add_argument("--run", help="run id (default: the current trusted run, else the newest)")
    parser.add_argument("--ai", action="store_true",
                        help="print plain text to paste into an AI assistant instead of the styled view")
    parser.add_argument("--context", type=int, default=40, help="how many preceding events to include")
    parser.add_argument("--demo", action="store_true", help="diagnose a simulated failed run")
    parser.add_argument("--fail-at", metavar="STAGE", help="with --demo: which stage failed")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return watch(args)
    except Xl2aiError as e:
        print(f"{e}" + (f"\n  hint: {e.hint}" if e.hint else ""), file=sys.stderr)
        return 4 if e.code == "E_LOCKED" else 5
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130


def diagnose_main(argv=None):
    args = build_diagnose_parser().parse_args(argv)
    try:
        return diagnose(args)
    except Xl2aiError as e:
        print(f"{e}" + (f"\n  hint: {e.hint}" if e.hint else ""), file=sys.stderr)
        return 5


if __name__ == "__main__":
    sys.exit(main())

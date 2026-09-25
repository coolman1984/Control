"""`xl2ai status`: what is the current dataset, is it still fresh, and did the last attempt succeed?

Exit codes: 0 fresh and last attempt ok | 1 no promoted run yet | 2 a source changed since the run | 3 last attempt failed.
Cheap by design (no Excel, no hashing): freshness compares size and modified time recorded in the manifest.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from .core.config import load_config
from .core.errors import Xl2aiError
from .core.fsutil import format_mtime, sha256_file
from .core.runs import current_run_id, list_runs, load_manifest


def compute_status(cfg, deep=False):
    out = {"project": cfg.project, "data_dir": cfg.data_dir, "current": None, "sources_fresh": None,
           "changes": [], "last_attempt": None}
    runs = list_runs(cfg)
    cur = current_run_id(cfg)
    if runs:
        latest = load_manifest(cfg, runs[0])
        out["last_attempt"] = {"run_id": latest["run_id"], "status": latest["status"], "promoted": latest.get("promoted", False),
                               "errors": [s["error"] for s in latest["stages"] if s.get("error")]}
    if not cur:
        return out, 1
    m = load_manifest(cfg, cur)
    out["current"] = {"run_id": cur, "started": m["started"], "finished": m["finished"], "status": m["status"],
                      "stages": {s["name"]: s["status"] for s in m["stages"]},
                      "databases": [a for s in m["stages"] for a in s["artifacts"] if a.endswith(".db")]}
    for inp in m["inputs"]:
        try:
            st = os.stat(inp["path"])
        except OSError:
            out["changes"].append({"path": inp["path"], "reason": "missing"})
            continue
        mtime = format_mtime(st.st_mtime)
        stat_changed = st.st_size != inp["size"] or mtime != inp["mtime"]
        if stat_changed:
            out["changes"].append({"path": inp["path"], "reason": "modified"})
            continue
        if deep:
            digest, mode = sha256_file(inp["path"])
            if digest != inp.get("sha256"):
                out["changes"].append({"path": inp["path"], "reason": "content_changed_same_metadata",
                                       "hash_mode": mode})
    out["sources_fresh"] = not out["changes"]
    if out["changes"]:
        return out, 2
    la = out["last_attempt"]
    if la and la["run_id"] != cur and not la["promoted"] and la["status"] != "running":
        return out, 3
    return out, 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="xl2ai status", description="Show the current dataset and its freshness.")
    ap.add_argument("--config", help="path to xl2ai.toml (default: discovered upward from the current folder)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--deep", action="store_true",
                    help="recompute full SHA-256 for every source, even when size/mtime look unchanged")
    args = ap.parse_args(argv)
    try:
        cfg = load_config(args.config)
    except Xl2aiError as e:
        print(f"{e}", file=sys.stderr)
        return 5
    out, code = compute_status(cfg, deep=args.deep)
    if args.json:
        print(json.dumps(out, indent=1, ensure_ascii=False))
        return code
    cur = out["current"]
    print(f"project: {out['project']}")
    if not cur:
        print("current dataset: none (no run has been promoted yet; run `python -m xl2ai refresh`)")
    else:
        print(f"current dataset: {cur['run_id']} ({cur['status']}, finished {cur['finished']})")
        print("stages: " + " | ".join(f"{k} {v}" for k, v in cur["stages"].items()))
        mode = "deep SHA-256" if args.deep else "fast metadata"
        print("sources: " + ("fresh" if out["sources_fresh"] else "CHANGED since this run -> refresh") + f" ({mode})")
        for c in out["changes"]:
            print(f"  - {c['reason']}: {c['path']}")
        for d in cur["databases"]:
            print(f"  db: {os.path.join(cfg.runs_dir, cur['run_id'], d)}")
    la = out["last_attempt"]
    if la and (not cur or la["run_id"] != cur["run_id"]):
        errs = "; ".join(f"{e['code']}: {e['message']}" for e in la["errors"])
        print(f"last attempt: {la['run_id']} {la['status']} (not promoted){' - ' + errs if errs else ''}")
    return code


if __name__ == "__main__":
    sys.exit(main())

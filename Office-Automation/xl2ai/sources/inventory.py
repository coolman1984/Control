"""Resolve configured sources to files, give each a stable id, and fingerprint it.

`source_id` = readable slug of the file stem + 8 hex of the normalised absolute path, so two files called
report.xlsx in different folders never collide, and the id stays the same across runs. An `alias` in config
replaces it (useful when a file is moved).
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import sys

from ..core.config import load_config
from ..core.errors import Xl2aiError
from ..core.fsutil import format_mtime, sha256_file, slug
from .detect import sniff_file

EXTENSIONS = ("xlsx", "xlsm", "xlsb", "xls", "docx", "docm", "doc", "pptx", "pptm", "ppt", "pdf", "msg", "eml",
              "txt", "md", "csv", "html", "htm")
KINDS = {"xlsx", "xlsm", "xlsb", "xls", "xltx", "xltm", "docx", "docm", "doc", "pptx", "pptm", "ppt", "pdf", "msg",
         "eml", "txt", "md", "csv", "html", "htm"}


def expand_paths(paths):
    """Files, folders (non-recursive) and wildcards -> absolute file paths; ~$ temp files are skipped."""
    out = []
    for p in paths:
        if os.path.isdir(p):
            for ext in EXTENSIONS:
                out += glob.glob(os.path.join(p, f"*.{ext}"))
        elif any(ch in p for ch in "*?"):
            out += [f for f in glob.glob(p, recursive=True)
                    if os.path.splitext(f)[1].lower().lstrip(".") in KINDS]
        else:
            out.append(p)
    skip = (os.sep + ".xl2ai" + os.sep, "/.xl2ai/")                   # a workspace never feeds itself
    return [os.path.abspath(f) for f in dict.fromkeys(out)
            if not os.path.basename(f).startswith("~$") and not any(s in os.path.abspath(f) for s in skip)]


def source_id(path, alias=None):
    if alias:
        return slug(alias, 60)
    norm = os.path.normcase(os.path.abspath(path))
    return f"{slug(os.path.splitext(os.path.basename(path))[0])}-{hashlib.sha1(norm.encode('utf-8')).hexdigest()[:8]}"


def kind_of(path):
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    return ext if ext in KINDS else "other"


def build_inventory(cfg):
    """(sources, warnings). Raises E_SRC_MISSING if a configured source matches nothing: never a silent gap."""
    sources, warnings, missing, seen = [], [], [], set()
    if not cfg.sources:
        raise Xl2aiError("E_CONFIG", "no sources configured", "add [[sources]] to xl2ai.toml or pass paths on the command line")
    for spec in cfg.sources:
        files = [f for f in expand_paths([cfg.resolve(spec.path)]) if os.path.isfile(f)]
        if not files:
            missing.append(spec.path)
            continue
        if spec.alias and len(files) != 1:
            raise Xl2aiError("E_CONFIG", f"alias '{spec.alias}' needs a path that matches exactly one file "
                                         f"(matched {len(files)})")
        for f in files:
            if f in seen:
                continue
            seen.add(f)
            digest, mode = sha256_file(f)
            st = os.stat(f)
            sniffed = sniff_file(f, cfg.wrapper_prefixes)
            wrapper = "none" if sniffed == "zip" else sniffed
            sources.append({"source_id": source_id(f, spec.alias), "path": f, "kind": kind_of(f), "size": st.st_size,
                            "mtime": format_mtime(st.st_mtime),
                            "sha256": digest, "hash_mode": mode, "wrapper": wrapper})
    if missing:
        raise Xl2aiError("E_SRC_MISSING", "no file matches: " + "; ".join(missing),
                         "check the path (relative paths are relative to xl2ai.toml)")
    ids = [s["source_id"] for s in sources]
    if len(ids) != len(set(ids)):
        raise Xl2aiError("E_CONFIG", "two sources resolve to the same source_id", "give one of them a different alias")
    by_hash = {}
    for s in sources:
        by_hash.setdefault(s["sha256"], []).append(s["path"])
    for paths in by_hash.values():
        if len(paths) > 1:
            warnings.append("identical content in several sources: " + "; ".join(paths))
    for s in sources:
        if s["wrapper"] == "encrypted":
            warnings.append(f"password-protected (Office encryption), extraction will fail: {s['path']}")
    return sources, warnings


def main(argv=None):
    ap = argparse.ArgumentParser(prog="xl2ai sources", description="List and fingerprint the configured source files.")
    ap.add_argument("paths", nargs="*", help="override the configured sources")
    ap.add_argument("--config", help="path to xl2ai.toml (default: discovered upward from the current folder)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    try:
        cfg = load_config(args.config, required=not args.paths)
        if args.paths:
            cfg = cfg.with_sources(args.paths)
        sources, warnings = build_inventory(cfg)
    except Xl2aiError as e:
        print(f"{e}" + (f"\n  hint: {e.hint}" if e.hint else ""), file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps({"sources": sources, "warnings": warnings}, indent=1, ensure_ascii=False))
        return 0
    for s in sources:
        print(f"{s['source_id']:<48} {s['kind']:<5} {s['size']:>12,} B  {s['mtime']}  wrapper={s['wrapper']}\n    {s['path']}")
    for w in warnings:
        print(f"WARN {w}")
    return 0

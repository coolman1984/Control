"""Project configuration: built-in defaults < xl2ai.toml < CLI flags. Strictly validated: a typo is an error, not a default."""
from __future__ import annotations

import copy
import difflib
import hashlib
import json
import os
import tomllib
from types import SimpleNamespace

from .errors import Xl2aiError

CONFIG_NAME = "xl2ai.toml"

DEFAULTS = {
    "project": {"name": "", "data_dir": "data"},
    "environment": {"wrapper_prefixes": []},        # file prefixes of rights-management wrappers (Excel opens them)
    "refresh": {"allow_partial": False, "keep_runs": 3, "lock_stale_hours": 12},
    "extract": {"verify": True, "strict": False, "block_cells": 500_000, "cache_cells": 12_000_000,
                "open_timeout": 180, "visible": False, "sheets": [], "engine": "auto",
                "workers": 0},
    "analysis": {"sample_values": 5, "top_k": 5, "relation_sample": 1000, "row_hash_max_rows": 200_000},
    "rules": {"packs": [], "block_on_error": False},
    "repair": {"enabled": False, "null_tokens": True, "category_consolidation": True, "text_coercion": True},
    "ai": {"context_tokens": 4000, "query_rows": 50, "query_bytes": 8192, "query_timeout": 5},
}
SOURCE_KEYS = {"path": str, "alias": str}
MINIMUMS = {("refresh", "keep_runs"): 1, ("refresh", "lock_stale_hours"): 0, ("extract", "block_cells"): 1000,
            ("extract", "cache_cells"): 0, ("extract", "open_timeout"): 5, ("extract", "workers"): 0,
            ("analysis", "sample_values"): 1, ("analysis", "top_k"): 1, ("analysis", "relation_sample"): 10,
            ("analysis", "row_hash_max_rows"): 0, ("ai", "context_tokens"): 500, ("ai", "query_rows"): 1,
            ("ai", "query_bytes"): 256, ("ai", "query_timeout"): 1}

CHOICES = {("extract", "engine"): ("auto", "excel", "direct")}


def _err(msg, hint=""):
    return Xl2aiError("E_CONFIG", msg, hint)


def _check_type(where, value, default):
    want = type(default)
    if isinstance(default, bool):
        ok = isinstance(value, bool)
    elif isinstance(default, int):
        ok = isinstance(value, int) and not isinstance(value, bool)
    elif isinstance(default, list):
        ok = isinstance(value, list) and all(isinstance(v, str) for v in value)
    else:
        ok = isinstance(value, want)
    if not ok:
        raise _err(f"{where}: expected {want.__name__}{' of strings' if isinstance(default, list) else ''}, "
                   f"got {type(value).__name__} ({value!r})")


def _merge(raw):
    cfg = copy.deepcopy(DEFAULTS)
    for section, values in raw.items():
        if section == "sources":
            continue
        if section not in cfg:
            near = difflib.get_close_matches(section, list(cfg) + ["sources"], n=1)
            raise _err(f"unknown section [{section}]", f"did you mean [{near[0]}]?" if near else "")
        if not isinstance(values, dict):
            raise _err(f"[{section}] must be a table")
        for key, value in values.items():
            if key not in cfg[section]:
                near = difflib.get_close_matches(key, list(cfg[section]), n=1)
                raise _err(f"unknown key '{key}' in [{section}]", f"did you mean '{near[0]}'?" if near else "")
            _check_type(f"[{section}] {key}", value, DEFAULTS[section][key])
            choices = CHOICES.get((section, key))
            if choices and value not in choices:
                raise _err(f"[{section}] {key} must be one of {', '.join(choices)} (got {value!r})")
            minimum = MINIMUMS.get((section, key))
            if minimum is not None and value < minimum:
                raise _err(f"[{section}] {key} must be >= {minimum} (got {value})")
            cfg[section][key] = value
    return cfg


class SourceSpec:
    def __init__(self, path, alias=None):
        self.path, self.alias = path, alias

    def as_dict(self):
        return {"path": self.path, "alias": self.alias}


class Config:
    def __init__(self, cfg, sources, root, path=None):
        self.raw, self.root, self.path = cfg, os.path.abspath(root), path
        self.project = cfg["project"]["name"] or os.path.basename(self.root) or "project"
        data_dir = cfg["project"]["data_dir"]
        self.data_dir = data_dir if os.path.isabs(data_dir) else os.path.abspath(os.path.join(self.root, data_dir))
        self.sources = sources
        self.wrapper_prefixes = tuple(p.encode("utf-8") for p in cfg["environment"]["wrapper_prefixes"])
        self.allow_partial = cfg["refresh"]["allow_partial"]
        self.keep_runs = cfg["refresh"]["keep_runs"]
        self.lock_stale_hours = cfg["refresh"]["lock_stale_hours"]
        self.extract = cfg["extract"]
        self.analysis = cfg["analysis"]
        self.rule_packs = tuple(cfg["rules"]["packs"])
        self.block_on_rule_error = cfg["rules"]["block_on_error"]
        self.ai = cfg["ai"]
        self.repair = cfg["repair"]

    runs_dir = property(lambda self: os.path.join(self.data_dir, "runs"))
    current_file = property(lambda self: os.path.join(self.data_dir, "current.json"))
    lock_file = property(lambda self: os.path.join(self.data_dir, ".lock"))

    def with_sources(self, paths):
        """CLI override: replace the configured sources."""
        return Config(self.raw, [SourceSpec(p) for p in paths], self.root, self.path)

    def pack_paths(self):
        return tuple(self.resolve(p) for p in self.rule_packs)

    def extract_options(self):
        e = self.extract
        return SimpleNamespace(verify=e["verify"], strict=e["strict"], block_cells=e["block_cells"],
                               cache_cells=e["cache_cells"], open_timeout=e["open_timeout"], visible=e["visible"],
                               sheets={s.strip().lower() for s in e["sheets"]}, wrapper_prefixes=self.wrapper_prefixes,
                               engine=e["engine"])

    def resolve(self, path):
        return path if os.path.isabs(path) else os.path.abspath(os.path.join(self.root, path))

    def fingerprint(self):
        """Stable hash of the whole project configuration (recorded in the run manifest)."""
        blob = {"cfg": self.raw, "sources": [s.as_dict() for s in self.sources]}
        return hashlib.sha256(json.dumps(blob, sort_keys=True).encode()).hexdigest()[:16]

    def extract_fingerprint(self):
        """Hash only settings that can change extraction output.

        Refresh/retention settings and unrelated sources deliberately do not participate, so an unchanged workbook
        can reuse its previous trusted database even when another source is added or retention policy changes.
        """
        from ..extract.pipeline import resolve_engine
        extract = dict(self.raw["extract"])
        extract.pop("workers", None)          # how many run at once never changes what is extracted
        engine = resolve_engine(SimpleNamespace(engine=extract.pop("engine", "auto")))
        if engine != "excel":                 # the Excel engine keeps its pre-engine-setting fingerprint (reuse holds)
            extract["engine"] = engine
        blob = {"environment": self.raw["environment"], "extract": extract}
        return hashlib.sha256(json.dumps(blob, sort_keys=True).encode()).hexdigest()[:16]


def find_config(start=None):
    d = os.path.abspath(start or os.getcwd())
    while True:
        cand = os.path.join(d, CONFIG_NAME)
        if os.path.isfile(cand):
            return cand
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def load_config(path=None, required=True, start=None):
    """Load a config file (explicit path, or discovered upward from cwd). Without one, defaults + cwd root."""
    if path and os.path.isdir(path):                  # an Excel folder or a workspace folder, not a file
        from ..workspace import resolve_workspace
        resolved = resolve_workspace(path)
        if not resolved:
            raise _err(f"{path} has no xl2ai workspace yet", f'run: python -m xl2ai open "{path}"')
        path = resolved
    if not path and os.environ.get("XL2AI_WORKSPACE"):
        from ..workspace import resolve_workspace
        path = resolve_workspace(os.environ["XL2AI_WORKSPACE"])
    path = path or find_config(start)
    if path is None:
        if required:
            raise _err(f"no {CONFIG_NAME} found (searched upward from {os.getcwd()})",
                       "create one, pass --config, or give sources on the command line")
        return Config(copy.deepcopy(DEFAULTS), [], os.getcwd())
    path = os.path.abspath(path)
    try:
        with open(path, "rb") as f:
            raw = tomllib.load(f)
    except FileNotFoundError:
        raise _err(f"config file not found: {path}") from None
    except tomllib.TOMLDecodeError as e:
        raise _err(f"{path}: invalid TOML: {e}", "on Windows use forward slashes or single-quoted paths") from None
    cfg = _merge(raw)
    sources = []
    for i, s in enumerate(raw.get("sources", []), 1):
        if not isinstance(s, dict):
            raise _err(f"[[sources]] #{i} must be a table")
        for key, value in s.items():
            if key not in SOURCE_KEYS:
                near = difflib.get_close_matches(key, list(SOURCE_KEYS), n=1)
                raise _err(f"unknown key '{key}' in [[sources]] #{i}", f"did you mean '{near[0]}'?" if near else "")
            if not isinstance(value, str) or not value.strip():
                raise _err(f"[[sources]] #{i} {key}: expected a non-empty string")
        if "path" not in s:
            raise _err(f"[[sources]] #{i} is missing 'path'")
        sources.append(SourceSpec(s["path"], s.get("alias")))
    root = os.path.dirname(path)
    conf = Config(cfg, sources, root, path)
    aliases = [s.alias for s in sources if s.alias]
    if len(aliases) != len(set(aliases)):
        raise _err("duplicate alias in [[sources]]", "aliases become source ids and must be unique")
    return conf


def wrapper_prefixes_or_empty():
    """Best effort for stand-alone commands: the discovered project's wrapper prefixes, else none."""
    try:
        return load_config(required=False).wrapper_prefixes
    except Xl2aiError:
        return ()

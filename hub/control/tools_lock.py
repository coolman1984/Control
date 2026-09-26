"""A lockfile of the MCP tool surface's hash - piece 4's "tool-description integrity" idea,
scoped to something the owner can actually act on. Note what this does *not* need to guard
against: `registry.register()` already raises on a duplicate action name, so nothing already
running can silently redefine a tool an agent has seen. What this catches instead is a *restart*
after a tampered or unexpectedly-changed area module - `control tools verify` (or `mcp.serve`'s own
startup check) says the surface drifted since the owner last looked; `control tools accept` is the
explicit, CLI-only (never an MCP action, for the same reason `vault.set` isn't one) review step
that moves the accepted baseline forward, the same hash-pin-and-re-approve shape `flow.approve`
already uses for a flow file.
"""
import json

from . import config
from .registry import surface_hash


def _path():
    return config.home() / "tools.lock"


def read():
    p = _path()
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("hash")
    except ValueError:
        return None


def write(h=None):
    h = h or surface_hash()
    _path().parent.mkdir(parents=True, exist_ok=True)
    _path().write_text(json.dumps({"hash": h}, ensure_ascii=False), encoding="utf-8")
    return h


def check():
    """None if there is no lock yet, or it matches the current surface; otherwise the current
    hash, for `write()` to accept once the owner has reviewed what changed."""
    current = surface_hash()
    saved = read()
    return None if saved in (None, current) else current


def main(argv):
    if not argv or argv[0] not in ("verify", "accept"):
        print("usage: control tools verify | control tools accept")
        return 1
    if argv[0] == "accept":
        h = write()
        print(f"accepted the current tool surface ({h[:12]})")
        return 0
    drifted = check()
    if drifted is None:
        print("tool surface matches the accepted baseline (or none is set yet)")
        return 0
    print(f"WARNING: the tool surface changed since it was last accepted (now {drifted[:12]}); "
          "review what changed, then `control tools accept` if it's expected")
    return 1

"""Canonical, order-stable fingerprint of an extraction database, for byte-level regression checks.

Volatile values (timings, timestamps, absolute paths, file mtimes) are excluded, everything else is hashed
per table so a difference can be localised.  Usage:
    python tests/regression_dump.py DB [DB ...]            -> JSON to stdout
"""
import hashlib
import json
import sqlite3
import sys

VOLATILE_META = {"extracted_at", "total_seconds", "source_path", "source_size", "source_modified"}
VOLATILE_COLUMNS = {"_extraction_log": {"read_sec", "write_sec", "total_sec"}}


def fingerprint(db_path):
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    out = {}
    tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    for t in tables:
        cols = [r[1] for r in con.execute(f'PRAGMA table_info("{t}")')]
        skip = VOLATILE_COLUMNS.get(t, set())
        keep = [c for c in cols if c not in skip]
        sel = ", ".join(f'"{c}"' for c in keep)
        rows = con.execute(f'SELECT {sel} FROM "{t}" ORDER BY rowid').fetchall()
        if t == "_meta":
            rows = [r for r in rows if r[0] not in VOLATILE_META]
        schema = con.execute("SELECT sql FROM sqlite_master WHERE name=?", (t,)).fetchone()[0]
        h = hashlib.sha256()
        h.update(repr((schema, keep)).encode())
        for r in rows:
            h.update(repr(r).encode("utf-8", "surrogatepass"))
        out[t] = {"rows": len(rows), "sha256": h.hexdigest()}
    con.close()
    total = hashlib.sha256(json.dumps(out, sort_keys=True).encode()).hexdigest()
    return {"total": total, "tables": out}


if __name__ == "__main__":
    print(json.dumps({p: fingerprint(p) for p in sys.argv[1:]}, indent=1))

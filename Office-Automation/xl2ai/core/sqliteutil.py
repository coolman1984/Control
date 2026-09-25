"""SQLite helpers with explicit lifetime control.

sqlite3.Connection context managers commit or roll back, but do not close the file handle. Explicit closing matters
on Windows where an open handle blocks replacing or deleting run artifacts.
"""
from __future__ import annotations

from contextlib import contextmanager
import os
import sqlite3


@contextmanager
def ro_connection(path):
    con = sqlite3.connect(f"file:{os.path.abspath(path)}?mode=ro", uri=True)
    try:
        yield con
    finally:
        con.close()


def install_readonly_authorizer(con):
    """Defense in depth for SQL exposed to packs/AI tools.

    query_only already blocks writes. The authorizer also rejects schema mutation, ATTACH/DETACH, PRAGMA, transaction
    control and file-oriented extension functions if they ever become available in the SQLite build.
    """
    denied_names = {
        "SQLITE_INSERT", "SQLITE_DELETE", "SQLITE_UPDATE",
        "SQLITE_CREATE_INDEX", "SQLITE_CREATE_TABLE", "SQLITE_CREATE_TEMP_INDEX",
        "SQLITE_CREATE_TEMP_TABLE", "SQLITE_CREATE_TEMP_TRIGGER", "SQLITE_CREATE_TEMP_VIEW",
        "SQLITE_CREATE_TRIGGER", "SQLITE_CREATE_VIEW", "SQLITE_CREATE_VTABLE",
        "SQLITE_DROP_INDEX", "SQLITE_DROP_TABLE", "SQLITE_DROP_TEMP_INDEX",
        "SQLITE_DROP_TEMP_TABLE", "SQLITE_DROP_TEMP_TRIGGER", "SQLITE_DROP_TEMP_VIEW",
        "SQLITE_DROP_TRIGGER", "SQLITE_DROP_VIEW", "SQLITE_DROP_VTABLE",
        "SQLITE_ALTER_TABLE", "SQLITE_REINDEX", "SQLITE_ANALYZE",
        "SQLITE_ATTACH", "SQLITE_DETACH", "SQLITE_PRAGMA",
        "SQLITE_TRANSACTION", "SQLITE_SAVEPOINT",
    }
    denied = {getattr(sqlite3, name) for name in denied_names if hasattr(sqlite3, name)}
    dangerous_functions = {"load_extension", "readfile", "writefile"}

    def authorize(action, arg1, arg2, db_name, trigger_name):
        if action in denied:
            return sqlite3.SQLITE_DENY
        if action == getattr(sqlite3, "SQLITE_FUNCTION", -1):
            fn = str(arg2 or arg1 or "").lower()
            if fn in dangerous_functions:
                return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    con.set_authorizer(authorize)
    return con

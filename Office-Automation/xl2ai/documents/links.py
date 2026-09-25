"""Link documents to data: every code, e-mail or phone number mentioned in a document is looked up in the tables.

"The e-mail from Ahmed mentions INV-005000" becomes "... which is row 5003 of Transactions.invoice_no" -- so an agent
reading a complaint, a contract or an approval e-mail can jump straight to the records it talks about, and a
question about a record can surface the documents that discuss it. Exact matches only (case-insensitive), bounded.
"""
from __future__ import annotations

import os
import sqlite3

from ..core.errors import Xl2aiError
from ..core.sqliteutil import ro_connection

LINK_KINDS = ("code", "email", "phone")
BATCH = 400
MAX_VALUES = 20_000


def q(name):
    return '"' + str(name).replace('"', '""') + '"'


def build_mentions(cfg, run_id, catalog_path=None):
    run_dir = os.path.join(cfg.runs_dir, run_id)
    path = catalog_path or os.path.join(run_dir, "catalog.db")
    if not os.path.isfile(path):
        raise Xl2aiError("E_STAGE_INPUT", f"catalog not found: {path}")
    con = sqlite3.connect(path)
    try:
        if not con.execute("SELECT 1 FROM sqlite_master WHERE name='_entities'").fetchone():
            return path
        con.execute("DELETE FROM _mentions")
        values = [r[0] for r in con.execute(
            f"SELECT DISTINCT normalized FROM _entities WHERE kind IN ({','.join('?' * len(LINK_KINDS))}) "
            f"LIMIT {MAX_VALUES}", LINK_KINDS)]
        if not values:
            con.commit()
            return path
        occurrences = {}
        for norm, kind, sid, bid in con.execute(
                f"SELECT normalized, kind, source_id, block_id FROM _entities WHERE kind IN "
                f"({','.join('?' * len(LINK_KINDS))})", LINK_KINDS):
            occurrences.setdefault(norm, []).append((kind, sid, bid))
        cols = con.execute("""SELECT c.table_id, c.name, t.table_name, t.db_rel FROM _columns c
                              JOIN _tables t ON t.table_id = c.table_id
                              LEFT JOIN _profile_columns p ON p.column_id = c.column_id
                              WHERE upper(c.sql_type) IN ('TEXT','BLOB','ANY') AND COALESCE(p.distinct_count, 2) >= 2
                              ORDER BY t.db_rel""").fetchall()
        rows, open_db, src = [], None, None
        try:
            for table_id, col, table, db_rel in cols:
                if db_rel != open_db:
                    if src is not None:
                        src.close()
                    db_path = os.path.abspath(os.path.join(run_dir, db_rel.replace("/", os.sep)))
                    src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
                    open_db = db_rel
                for i in range(0, len(values), BATCH):
                    chunk = values[i:i + BATCH]
                    marks = ",".join("?" * len(chunk))
                    for v, n, first in src.execute(
                            f"SELECT upper(trim(CAST({q(col)} AS TEXT))) AS v, COUNT(*), MIN(_xl_row) FROM {q(table)} "
                            f"WHERE upper(trim(CAST({q(col)} AS TEXT))) IN ({marks}) GROUP BY v",
                            [c.upper() for c in chunk]):
                        for norm in (x for x in chunk if x.upper() == v):
                            for kind, sid, bid in occurrences.get(norm, []):
                                rows.append((norm, kind, sid, bid, table_id, col, n, first))
        finally:
            if src is not None:
                src.close()
        con.executemany("INSERT INTO _mentions VALUES (?,?,?,?,?,?,?,?)", rows)
        con.commit()
    finally:
        con.close()
    return path


def document_lines(con, limit_docs=40):
    """Agent-brief section: every document, what it is, what it mentions, and where that lives in the data."""
    if not con.execute("SELECT 1 FROM sqlite_master WHERE name='_documents'").fetchone():
        return []
    docs = con.execute("""SELECT source_id, kind, title, subject, sender, recipients, sent, pages, blocks, tables,
                                 attachments FROM _documents ORDER BY kind, source_id LIMIT ?""", (limit_docs,)).fetchall()
    if not docs:
        return []
    lines = ["## Documents (text is searchable with `search`; read a part with `read`)"]
    for sid, kind, title, subject, sender, rcpt, sent, pages, blocks, tables, atts in docs:
        head = f"- **{sid}** ({kind})"
        if subject or title:
            head += f": {subject or title}"
        facts = []
        if sender:
            facts.append(f"from {sender}")
        if sent:
            facts.append(f"sent {str(sent)[:16]}")
        if pages:
            facts.append(f"{pages} page(s)")
        facts.append(f"{blocks} text block(s)")
        if tables:
            facts.append(f"{tables} table(s) -> queryable like any sheet")
        if atts:
            facts.append(f"{atts} attachment(s) read")
        lines.append(head + " -- " + ", ".join(facts))
        ent = con.execute("""SELECT kind, COUNT(DISTINCT normalized) FROM _entities WHERE source_id=?
                             GROUP BY kind ORDER BY kind""", (sid,)).fetchall()
        if ent:
            lines.append("    mentions: " + ", ".join(f"{n} {k}" for k, n in ent))
        links = con.execute("""SELECT m.normalized, t.table_name, m.column_name, m.first_row FROM _mentions m
                               JOIN _tables t ON t.table_id = m.table_id WHERE m.source_id=? AND t.source_id<>?
                               GROUP BY m.normalized, m.table_id, m.column_name LIMIT 6""", (sid, sid)).fetchall()
        for norm, table, col, first in links:
            lines.append(f"    - {norm} is in `{table}.{col}` (first at row {first})")
    lines.append("")
    return lines


def main(argv=None):
    import argparse
    import sys
    from ..core.config import load_config
    from ..core.runs import current_run_id
    ap = argparse.ArgumentParser(prog="xl2ai links", description="Link document mentions to table rows.")
    ap.add_argument("--config")
    ap.add_argument("--run")
    args = ap.parse_args(argv)
    try:
        cfg = load_config(args.config)
        rid = args.run or current_run_id(cfg)
        if not rid:
            raise Xl2aiError("E_STAGE_INPUT", "no current run")
        print(build_mentions(cfg, rid))
    except Xl2aiError as e:
        print(str(e), file=sys.stderr)
        return 1
    return 0

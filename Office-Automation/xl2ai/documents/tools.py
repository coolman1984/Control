"""Agent tools over document text: search it, read it, and turn it into records -- with proof.

    search        full-text across every document (Arabic-aware: case, diacritics and letter variants folded),
                  ranked, each hit with its location (document > attachment > page/slide > heading) and a snippet
    read          one document, or one part of it (an attachment, a page, a heading's section, or the blocks around
                  a hit), in order, bounded
    save_records  structured records an agent extracted from text (the invoice table in an e-mail body, the terms of
                  a contract...). Every field carries its source: a block id and the exact quote. The quote must be
                  found in that block and the value must be found in the quote -- otherwise the field is rejected
                  with the reason, and nothing is invented into the database. Saved records live in
                  `<data>/extracted.db`, survive refreshes, and join with every other table in `query`.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sqlite3
import sys

from ..core.errors import Xl2aiError
from .entities import find_entities, normalize_digits

SNIPPET_WORDS = 30
_AR_LETTER = re.compile("[\u0621-\u064A]")


def arabic_stem(word):
    """Strip the letters Arabic glues onto a word: و/ف (and), ب/ك/ل (in/like/for) and ال (the).
    والقاهرة, بالقاهرة, للقاهرة and القاهرة all become قاهره (after folding). Non-Arabic words are unchanged."""
    w = word
    if not _AR_LETTER.match(w or ""):
        return w
    if len(w) > 3 and w[0] in "وف":
        w = w[1:]
    if len(w) > 4 and w.startswith("لل"):                  # لل = ل + ال
        return w[2:]
    if len(w) > 4 and w[0] in "بكل" and w[1:3] == "ال":
        w = w[1:]
    if len(w) > 3 and w.startswith("ال"):
        w = w[2:]
    return w


def index_text(text):
    """What goes into the full-text index: the folded text plus the stem of every Arabic word that has one."""
    folded = _fold(text)
    extra = {arabic_stem(w) for w in folded.split()} - set(folded.split())
    return folded + (" " + " ".join(sorted(extra)) if extra else "")


def _fold(text):
    from ..query import fold
    return fold(normalize_digits(text or ""))


def _catalog(cfg, run_id=None):
    from ..query import _run_paths
    rid, run_dir, cat = _run_paths(cfg, run_id)
    con = sqlite3.connect(f"file:{os.path.abspath(cat)}?mode=ro", uri=True)
    if not con.execute("SELECT 1 FROM sqlite_master WHERE name='_blocks'").fetchone():
        con.close()
        raise Xl2aiError("E_STAGE_INPUT", "this run has no document text", "refresh with documents in the folder")
    return rid, con


def _snippet(text, tokens):
    words = (text or "").split()
    folded = [arabic_stem(_fold(w)) for w in words]
    at = next((i for i, w in enumerate(folded) if any(t in w for t in tokens)), 0)
    lo = max(0, at - SNIPPET_WORDS // 3)
    out = " ".join(words[lo:lo + SNIPPET_WORDS])
    return ("... " if lo else "") + out + (" ..." if lo + SNIPPET_WORDS < len(words) else "")


def _where(part, page, section, kind):
    bits = [part] if part else []
    if page:
        bits.append(f"p{page}")
    if section:
        bits.append(section)
    return " > ".join(bits) or kind


def search(cfg, text, kind=None, document=None, limit=20, run_id=None):
    """Ranked text hits: [block_id, document, kind, where, snippet]."""
    tokens = [arabic_stem(t) for t in re.split(r"\s+", _fold(text)) if t]
    if not tokens:
        raise Xl2aiError("E_STAGE_INPUT", "empty search text")
    rid, con = _catalog(cfg, run_id)
    try:
        filters, args = [], []
        if kind:
            filters.append("b.kind = ?")
            args.append(kind)
        if document:
            filters.append("b.source_id = ?")
            args.append(document)
        extra = (" AND " + " AND ".join(filters)) if filters else ""
        fts = con.execute("SELECT 1 FROM sqlite_master WHERE name='_blocks_fts'").fetchone()
        if fts:
            match = " ".join('"' + t.replace('"', '""') + '"*' for t in tokens)
            sql = (f"SELECT b.block_id, b.source_id, b.kind, b.part, b.page, b.section, b.text FROM _blocks_fts f "
                   f"JOIN _blocks b ON b.rowid = f.rowid WHERE _blocks_fts MATCH ?{extra} ORDER BY f.rank LIMIT ?")
            rows = con.execute(sql, [match] + args + [int(limit) + 1]).fetchall()
            total = con.execute(f"SELECT COUNT(*) FROM _blocks_fts f JOIN _blocks b ON b.rowid = f.rowid "
                                f"WHERE _blocks_fts MATCH ?{extra}", [match] + args).fetchone()[0]
        else:                                             # no FTS5 in this SQLite: slower scan, same answer
            con.create_function("xfold", 1, _fold, deterministic=True)
            con.create_function("xindex", 1, index_text, deterministic=True)
            cond = " AND ".join("instr(xindex(coalesce(b.section,'') || ' ' || b.text), ?) > 0" for _ in tokens)
            rows = con.execute(f"SELECT b.block_id, b.source_id, b.kind, b.part, b.page, b.section, b.text FROM _blocks b "
                               f"WHERE {cond}{extra} LIMIT ?", tokens + args + [int(limit) + 1]).fetchall()
            total = len(rows)
    finally:
        con.close()
    out = [[bid, sid, k, _where(part, page, section, k), _snippet(t, tokens)]
           for bid, sid, k, part, page, section, t in rows[:int(limit)]]
    return {"columns": ["block_id", "document", "kind", "where", "snippet"], "rows": out, "total_rows": total,
            "truncated": total > len(out), "run_id": rid,
            "note": "read more with `read` (document + block for the surrounding text)"}


def read(cfg, document, part=None, page=None, section=None, block=None, around=3, max_chars=8000, run_id=None):
    """Blocks of one document in order: [block_id, kind, where, text]; bounded by max_chars."""
    rid, con = _catalog(cfg, run_id)
    try:
        doc = con.execute("SELECT kind, title, subject, sender, recipients, sent, pages FROM _documents WHERE source_id=?",
                          (document,)).fetchone()
        if not doc:
            like = [r[0] for r in con.execute("SELECT source_id FROM _documents WHERE source_id LIKE ? LIMIT 5",
                                              (f"%{document}%",))]
            raise Xl2aiError("E_STAGE_INPUT", f"no document {document!r}",
                             ("did you mean: " + ", ".join(like)) if like else "list documents with `start`")
        where, args = ["source_id = ?"], [document]
        if block:
            n = int(str(block).rsplit("#", 1)[-1])
            where.append("ord BETWEEN ? AND ?")
            args += [n - int(around), n + int(around)]
        if part is not None:
            where.append("coalesce(part,'') = ?")
            args.append(part)
        if page is not None:
            where.append("page = ?")
            args.append(int(page))
        if section:
            where.append("(section LIKE ? OR (kind='heading' AND text LIKE ?))")
            args += [f"%{section}%", f"%{section}%"]
        rows = con.execute(f"SELECT block_id, kind, part, page, section, text FROM _blocks WHERE {' AND '.join(where)} "
                           f"ORDER BY ord", args).fetchall()
    finally:
        con.close()
    out, used, cut = [], 0, None
    for bid, k, prt, pg, sec, text in rows:
        if used + len(text) > max_chars and out:
            cut = bid
            break
        out.append([bid, k, _where(prt, pg, sec, k), text])
        used += len(text)
    meta = dict(zip(("kind", "title", "subject", "from", "to", "sent", "pages"), doc))
    return {"document": {k: v for k, v in meta.items() if v}, "columns": ["block_id", "kind", "where", "text"],
            "rows": out, "truncated": cut is not None, "run_id": rid,
            "note": (f"cut at {max_chars} characters; continue with block={cut}" if cut else "complete")}


# ---- grounded records ------------------------------------------------------------------------------------------
def _norm_text(s):
    return " ".join(_fold(s).split())


def _value_in_quote(value, quote):
    """True when the value is literally in the quote, or is the same number/date written differently."""
    if value is None:
        return True
    if _norm_text(str(value)) and _norm_text(str(value)) in _norm_text(quote):
        return True
    if isinstance(value, (int, float)) or re.fullmatch(r"-?[\d.,]+", normalize_digits(str(value))):
        try:
            target = float(normalize_digits(str(value)).replace(",", ""))
        except ValueError:
            return False
        for num in re.findall(r"\d[\d,٬]*(?:\.\d+)?", normalize_digits(quote)):
            try:
                if abs(float(num.replace(",", "").replace("٬", "")) - target) < 1e-9:
                    return True
            except ValueError:
                continue
    for kind, _, norm, _, _ in find_entities(quote):
        if kind in ("date", "money") and str(value) in (norm, norm.split(" ")[0]):
            return True
    return False


def _extracted_path(cfg):
    return os.path.join(cfg.data_dir, "extracted.db")


def save_records(cfg, table, records, key=None, run_id=None):
    """Verify and store records. Returns {saved, rejected: [{record, field, reason}], table}."""
    if not re.fullmatch(r"[A-Za-z؀-ۿ_][\w؀-ۿ]{0,59}", table or ""):
        raise Xl2aiError("E_STAGE_INPUT", f"invalid table name {table!r}", "letters, digits and _ only")
    if not isinstance(records, list) or not records:
        raise Xl2aiError("E_STAGE_INPUT", "records must be a non-empty list of objects")
    rid, con = _catalog(cfg, run_id)
    try:
        blocks = {}

        def block_text(bid):
            if bid not in blocks:
                row = con.execute("SELECT source_id, text FROM _blocks WHERE block_id=?", (bid,)).fetchone()
                blocks[bid] = row
            return blocks[bid]

        good, rejected = [], []
        for i, rec in enumerate(records):
            if not isinstance(rec, dict):
                rejected.append({"record": i, "reason": "not an object"})
                continue
            default_src = rec.get("_source")
            values, evidence, bad = {}, [], []
            for field, spec in rec.items():
                if field == "_source":
                    continue
                if not re.fullmatch(r"[A-Za-z؀-ۿ_][\w؀-ۿ]{0,59}", field) or field.startswith("_"):
                    bad.append({"record": i, "field": field, "reason": "invalid field name"})
                    continue
                if isinstance(spec, dict) and "value" in spec:
                    value, src = spec["value"], spec.get("source") or default_src
                else:
                    value, src = spec, default_src
                if not src or not isinstance(src, dict) or not src.get("block_id") or not src.get("quote"):
                    bad.append({"record": i, "field": field,
                                "reason": "missing source {block_id, quote}: every value must point at its text"})
                    continue
                row = block_text(src["block_id"])
                if not row:
                    bad.append({"record": i, "field": field, "reason": f"no block {src['block_id']}"})
                    continue
                if _norm_text(src["quote"]) not in _norm_text(row[1]):
                    bad.append({"record": i, "field": field,
                                "reason": "quote not found in that block (copy it exactly, including numbers)"})
                    continue
                if not _value_in_quote(value, src["quote"]):
                    bad.append({"record": i, "field": field, "reason": f"value {value!r} does not appear in the quote"})
                    continue
                values[field] = value
                evidence.append((field, src["block_id"], row[0], src["quote"]))
            if bad:
                rejected.extend(bad)
            elif values:
                good.append((values, evidence))
    finally:
        con.close()
    saved = 0
    if good:
        path = _extracted_path(cfg)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        out = sqlite3.connect(path)
        try:
            from ..query import q
            out.execute("""CREATE TABLE IF NOT EXISTS _evidence (table_name TEXT, record_id INTEGER, field TEXT,
                           block_id TEXT, source_id TEXT, quote TEXT, run_id TEXT, saved_at TEXT)""")
            fields = sorted({f for v, _ in good for f in v})
            out.execute(f"CREATE TABLE IF NOT EXISTS {q(table)} (_record_id INTEGER PRIMARY KEY, _saved_at TEXT)")
            have = {r[1] for r in out.execute(f"PRAGMA table_info({q(table)})")}
            for f in fields:
                if f not in have:
                    out.execute(f"ALTER TABLE {q(table)} ADD COLUMN {q(f)}")
            now = dt.datetime.now().isoformat(timespec="seconds")
            for values, evidence in good:
                if key and key in values:
                    for (old,) in out.execute(f"SELECT _record_id FROM {q(table)} WHERE {q(key)} = ?", (values[key],)).fetchall():
                        out.execute(f"DELETE FROM {q(table)} WHERE _record_id=?", (old,))
                        out.execute("DELETE FROM _evidence WHERE table_name=? AND record_id=?", (table, old))
                cols = list(values)
                cur = out.execute(f"INSERT INTO {q(table)} (_saved_at, {', '.join(q(c) for c in cols)}) "
                                  f"VALUES (?, {', '.join('?' * len(cols))})", [now] + [values[c] for c in cols])
                out.executemany("INSERT INTO _evidence VALUES (?,?,?,?,?,?,?,?)",
                                [(table, cur.lastrowid, f, b, s, qt, rid, now) for f, b, s, qt in evidence])
                saved += 1
            out.commit()
        finally:
            out.close()
    return {"table": table, "saved": saved, "rejected": rejected,
            "note": (f"query it with: SELECT * FROM {table}" if saved else "nothing saved")
            + ("; fix the rejected fields and send those records again" if rejected else "")}


def cli_main(argv=None):
    import argparse
    from ..core.config import load_config
    ap = argparse.ArgumentParser(prog="xl2ai docs", description="Search and read document text.")
    ap.add_argument("--config")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("search")
    s.add_argument("text")
    s.add_argument("--limit", type=int, default=20)
    r = sub.add_parser("read")
    r.add_argument("document")
    r.add_argument("--block")
    r.add_argument("--page", type=int)
    r.add_argument("--section")
    args = ap.parse_args(argv)
    try:
        cfg = load_config(args.config)
        out = search(cfg, args.text, limit=args.limit) if args.cmd == "search" else \
            read(cfg, args.document, page=args.page, section=args.section, block=args.block)
    except Xl2aiError as e:
        print(json.dumps({"ok": False, "error": e.as_dict()}, ensure_ascii=False))
        return 1
    print(json.dumps(out, ensure_ascii=False, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(cli_main())

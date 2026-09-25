"""The agent connection: an MCP server (Model Context Protocol, JSON-RPC 2.0 over stdio) in front of xl2ai.

Why this shape
--------------
An agent should not have to learn a command line, remember paths or parse console output. It gets eight tools
whose names say what they are for, one workflow stated in the server's own instructions (MCP delivers those to the
agent before its first call), and answers built for a model to read: compact, bounded, every number with its
evidence, and every answer ending with what to call next. The same functions back the CLI, so the two can never
disagree.

Order of events (the part that matters)
---------------------------------------
    1. `start`    -- always first. Says whether the folder is ready, and if it is, gives the whole picture in one
                     read: tables, key numbers, what looks wrong, how files connect, and the gaps to repeat.
    2. `prepare`  -- only when `start` says the data is missing or stale. Builds in the background; call it
                     again to follow progress. Unchanged files are reused, so a rebuild is usually quick.
    3. `find` / `table` / `query` / `facts` / `region` / `trace` -- answer the actual question, cheapest first.

Protocol notes: newline-delimited JSON-RPC on stdin/stdout; nothing else may ever be written to stdout, so the
server moves `sys.stdout` to stderr for its whole lifetime and writes protocol messages to the saved real stdout.
No third-party MCP library is needed.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import traceback

from . import __version__
from .core.errors import Xl2aiError
from .core.log import silence

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
MAX_TEXT = 60_000              # hard ceiling on one tool answer (characters); answers say when they were cut

INSTRUCTIONS = """xl2ai frees the data in a folder of Excel, Word, PowerPoint, PDF and e-mail (.msg/.eml) files. Never open
those files yourself: everything is already read, verified and indexed.

Workflow:
1. Call `start` first (pass `workspace` = the folder, unless the server has a default). It returns the whole
   picture: tables (from workbooks AND from documents), what one row means, key numbers, unusual values, reports that
   disagree with their raw data, how files connect (ready JOINs), every document with what it mentions and where
   those codes live in the data, and the gaps you must mention.
2. If `start` says the data is not prepared or stale, call `prepare` and keep calling it until it says done.
3. Answer with the cheapest tool: `find` (where is X?), `table` (one table in depth), `query` (read-only SQL across
   all files; plain table names work and leave totals rows out), `search` (full text of all documents), `read` (a
   document or the text around a hit), `facts`, `region`, `trace`.
4. To turn free text into data (an order in an e-mail, the terms of contracts, figures in a PDF): `search`/`read`,
   then `save_records` with, for every field, the block_id and the exact quote it came from. The server rejects any
   value it cannot find in its quote; saved records are queryable with `query` like any table.

Rules: quote numbers with their evidence (table + row, or document + block). Repeat any gap, anomaly or report
disagreement that bears on the question. Prefer pre-computed key numbers over writing your own GROUP BY."""

DOC_KINDS_ENUM = ["heading", "paragraph", "list_item", "slide_title", "note", "email_header", "email_body"]
FACT_TOPICS = ("relationships", "lineage", "reconciliation", "anomalies", "quality", "keys", "grain", "column_roles",
               "time_coverage", "duplicates", "definitions", "kpis", "rules", "unsupported", "regions",
               "header_groups", "row_flags", "table_kind", "repairs", "digest", "changes")

WS = {"type": "string", "description": "Folder holding the Excel files (or its .xl2ai workspace). Optional when the "
                                       "agent was opened in that folder or the server has a default."}


def _tool(name, title, description, props, required=(), read_only=True):
    return {"name": name, "title": title, "description": description,
            "inputSchema": {"type": "object", "properties": dict(props, workspace=WS),
                            "required": list(required), "additionalProperties": False},
            "annotations": {"title": title, "readOnlyHint": read_only, "destructiveHint": False,
                            "idempotentHint": True, "openWorldHint": False}}


TOOLS = [
    _tool("start", "Start here: the whole picture",
          "ALWAYS CALL FIRST. Returns whether the Excel folder is ready and, if so, everything needed to begin: "
          "every table with what one row means, key numbers (totals, biggest groups, monthly trend), unusual values, "
          "reports that disagree with raw data, how files connect (ready JOINs), gaps to mention, and next steps.",
          {}),
    _tool("prepare", "Prepare or refresh the data",
          "Reads the Excel folder into verified databases and computes everything `start` reports. Runs in the "
          "background: call again to follow progress until status is done. Only changed files are re-read. Call it "
          "when `start` says not_prepared or stale -- not otherwise.",
          {"force": {"type": "boolean", "description": "Re-read every file even if unchanged. Rarely needed."},
           "wait_seconds": {"type": "integer", "minimum": 0, "maximum": 55,
                            "description": "How long to wait for completion before answering (default 25)."}},
          read_only=False),
    _tool("find", "Where does X live?",
          "Finds a word across every file: table/sheet names, column names and headers, group headers, definitions "
          "and known values. Tolerant of case and Arabic spelling variants. Set search_values to also scan every "
          "text cell (slower, time-bounded; the answer says how much was covered).",
          {"text": {"type": "string", "description": "What to look for, e.g. 'customer', 'القاهرة', 'invoice no'."},
           "search_values": {"type": "boolean", "description": "Also search inside every text cell."}},
          required=("text",)),
    _tool("table", "One table in depth",
          "Everything about one table in one call: columns (type, meaning, nulls, distinct, range, top values), what "
          "one row represents, key numbers, unusual values, report checks, where its formulas pull from, separate "
          "regions, and a few sample rows. Accepts the table name, sheet name or table_id.",
          {"table": {"type": "string"},
           "sample_rows": {"type": "integer", "minimum": 0, "maximum": 50, "description": "Default 5."}},
          required=("table",)),
    _tool("query", "Read-only SQL across all files",
          "Runs one SELECT/WITH over every workbook at once (SQLite dialect). Plain table names work when unique and "
          "already leave out rows flagged as totals/subtotals, so SUMs are safe; <alias>.<table> reads every stored "
          "row. Every table has _xl_row (the Excel row) for evidence. Results are capped; aggregate in SQL rather "
          "than fetching rows.",
          {"sql": {"type": "string"},
           "max_rows": {"type": "integer", "minimum": 1, "maximum": 500,
                        "description": "Row cap for this call (default: the workspace setting, usually 50)."}},
          required=("sql",)),
    _tool("facts", "Pre-computed facts by topic",
          "Returns one pre-computed fact list: " + ", ".join(FACT_TOPICS) + ". Use it instead of recomputing: "
          "e.g. 'relationships' before joining, 'reconciliation' before trusting a report, 'changes' for what "
          "changed since the previous refresh.",
          {"topic": {"type": "string", "enum": list(FACT_TOPICS)}}, required=("topic",)),
    _tool("region", "One table inside a multi-table sheet",
          "When a sheet holds several tables (see `table` or facts topic 'regions'), returns region n with its own "
          "header names.",
          {"table": {"type": "string"}, "region": {"type": "integer", "minimum": 1},
           "max_rows": {"type": "integer", "minimum": 1, "maximum": 500}},
          required=("table", "region")),
    _tool("search", "Search the text of every document",
          "Full-text search over Word, PowerPoint, PDF, e-mail and text documents (and attachments inside e-mails). "
          "Tolerant of case, Arabic letter variants and diacritics; all words must match (prefixes allowed). "
          "Returns block ids, the document, the exact location (attachment > page/slide > heading) and a snippet.",
          {"text": {"type": "string"}, "kind": {"type": "string", "enum": DOC_KINDS_ENUM},
           "document": {"type": "string", "description": "limit to one document (its id from `start`)"},
           "limit": {"type": "integer", "minimum": 1, "maximum": 100}},
          required=("text",)),
    _tool("read", "Read a document or part of it",
          "Returns a document's text blocks in order with their block ids: the whole document, or one attachment, "
          "page/slide, heading section, or the blocks around a search hit (block + around). Bounded; says where to "
          "continue.",
          {"document": {"type": "string", "description": "document id (from `start` or `search`)"},
           "block": {"type": "string", "description": "a block id; returns it with `around` blocks each side"},
           "around": {"type": "integer", "minimum": 0, "maximum": 50},
           "part": {"type": "string", "description": "an attachment name (\"\" = the document itself)"},
           "page": {"type": "integer", "minimum": 1}, "section": {"type": "string"},
           "max_chars": {"type": "integer", "minimum": 500, "maximum": 40000}},
          required=("document",)),
    _tool("save_records", "Save records extracted from text, with proof",
          "Stores structured records you extracted from documents into a table (created or extended as needed) that "
          "`query` can join with everything else. EVERY field must carry its source: either per field "
          "{\"value\": v, \"source\": {\"block_id\": ..., \"quote\": ...}} or one \"_source\" for the whole "
          "record. The quote must appear in that block and the value in the quote; otherwise the field is rejected "
          "with the reason (fix and resend). `key` names a field whose value replaces an earlier record.",
          {"table": {"type": "string"}, "records": {"type": "array", "items": {"type": "object"}},
           "key": {"type": "string"}},
          required=("table", "records"), read_only=False),
    _tool("trace", "Prove where a row came from",
          "Returns the source file, sheet and stored values for one Excel row of a table -- the evidence to quote.",
          {"table": {"type": "string"}, "excel_row": {"type": "integer", "minimum": 1}},
          required=("table", "excel_row")),
]

PROMPTS = [{"name": "analyze_excel_folder", "title": "Analyze a folder of Excel files",
            "description": "Standard workflow: orient with start, prepare if needed, then answer with evidence.",
            "arguments": [{"name": "folder", "description": "The folder holding the Excel files", "required": True},
                          {"name": "question", "description": "What you want to know", "required": False}]}]


# ------------------------------------------------------------------------------------------------ helpers
def _compact(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=str)


def _cap(text):
    if len(text) <= MAX_TEXT:
        return text
    return text[:MAX_TEXT] + f"\n... [cut at {MAX_TEXT:,} characters; ask a narrower question]"


def _table_result(env, extra=None):
    """A query-style envelope -> the compact shape an agent reads best."""
    out = {"columns": env.get("columns", []), "rows": env.get("rows", [])}
    if env.get("truncated"):
        out["truncated"] = True
    if env.get("total_rows") is not None:
        out["total_rows"] = env["total_rows"]
    if env.get("hint"):
        out["note"] = env["hint"]
    if env.get("evidence"):
        out["evidence"] = env["evidence"]
    if extra:
        out.update(extra)
    return out


def _has_workbooks(folder):
    try:
        names = os.listdir(folder)
    except OSError:
        return False
    return any(os.path.splitext(n)[1].lower() in (".xlsx", ".xlsm", ".xlsb", ".xls") and not n.startswith("~$")
               for n in names)


class Job:
    """One background refresh. Progress comes from the pipeline's own event bus."""

    def __init__(self, config_path, force):
        self.config_path, self.force = config_path, force
        self.started = time.monotonic()
        self.stage, self.stages_done, self.sheets, self.sources_done = None, [], 0, 0
        self.result, self.error, self.done = None, None, threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _on_event(self, ev):
        from .observe import events as E
        if ev.kind == E.STAGE_START:
            self.stage = ev.data.get("name")
        elif ev.kind == E.STAGE_END:
            self.stages_done.append((ev.data.get("name"), ev.data.get("status")))
        elif ev.kind == E.SHEET_END:
            self.sheets += 1
        elif ev.kind == E.SOURCE_EXTRACT_END:
            self.sources_done += 1

    def _run(self):
        from .core.config import load_config
        from .observe.bus import BUS
        from .refresh import run_refresh
        unsubscribe = BUS.subscribe(self._on_event)
        try:
            cfg = load_config(self.config_path)
            code, run = run_refresh(cfg, force=self.force)
            failed = next((s for s in run.m["stages"] if s["status"] != "passed"), None)
            self.result = {"run_id": run.id, "status": run.m["status"], "promoted": bool(run.m.get("promoted")),
                           "failed_stage": failed and {"name": failed["name"], "error": failed.get("error"),
                                                       "warnings": failed.get("warnings")}}
        except Xl2aiError as e:
            self.error = {"code": e.code, "message": e.message, "hint": e.hint}
        except Exception as e:                                        # report, never kill the server
            self.error = {"code": "E_INTERNAL", "message": f"{type(e).__name__}: {e}"}
        finally:
            unsubscribe()
            self.done.set()

    def progress(self):
        from .refresh import STAGES
        names = [n for n, _ in STAGES]
        done = len(self.stages_done)
        return {"stage": self.stage, "stages_done": done, "stages_total": len(names),
                "workbooks_read": self.sources_done, "sheets_read": self.sheets,
                "elapsed_seconds": round(time.monotonic() - self.started, 1)}


# ------------------------------------------------------------------------------------------------ the server
class Server:
    def __init__(self, workspace=None):
        self.default_workspace = workspace
        self.jobs = {}
        self.lock = threading.Lock()

    # ---- workspace resolution ----------------------------------------------------------------------------
    def _target(self, args):
        explicit = args.get("workspace") or self.default_workspace or os.environ.get("XL2AI_WORKSPACE")
        if explicit:
            return explicit
        # Claude Code and Codex start the server in the folder they were opened in: use it when it is (or holds)
        # an Excel folder, so an agent opened there needs no path at all
        from .workspace import resolve_workspace
        cwd = os.getcwd()
        if resolve_workspace(cwd) or _has_workbooks(cwd):
            return cwd
        return None

    def _config_path(self, args, create=False):
        from .workspace import open_workspace, resolve_workspace
        from .core.config import find_config
        target = self._target(args)
        if not target:
            found = find_config()
            if found:
                return found
            raise Xl2aiError("E_CONFIG", "no workspace given",
                             "pass `workspace` = the folder that holds the Excel files")
        path = resolve_workspace(target)
        if path or not create:
            return path
        return open_workspace(target)["config"]

    def _cfg(self, args):
        from .core.config import load_config
        path = self._config_path(args)
        if not path:
            raise Xl2aiError("E_STAGE_INPUT", f"{self._target(args)} is not prepared yet",
                             "call `prepare` with the same workspace")
        cfg = load_config(path)
        from .core.runs import current_run_id
        if not current_run_id(cfg):
            job = self.jobs.get(path)
            if job and not job.done.is_set():
                raise Xl2aiError("E_STAGE_INPUT", "still preparing", "call `prepare` to follow progress")
            raise Xl2aiError("E_STAGE_INPUT", "this folder has not been prepared successfully yet",
                             "call `start` to see why, or `prepare` to build it")
        return cfg

    # ---- tools -------------------------------------------------------------------------------------------
    def tool_start(self, args):
        from .brief import build_brief
        path = self._config_path(args)
        job = self.jobs.get(path) if path else None
        if job and not job.done.is_set():
            return {"status": "preparing", "progress": job.progress(),
                    "next": "call `prepare` again (it waits for progress) and then `start`"}, None
        if not path:
            return {"status": "not_prepared", "workspace": self._target(args),
                    "next": "call `prepare` with this workspace; it reads the Excel files once (minutes for big "
                            "folders), then call `start` again"}, None
        from .core.config import load_config
        cfg = load_config(path)
        brief, code = build_brief(cfg)
        status = {0: "ready", 1: "needs_review", 2: "not_ready"}.get(code, "unknown")
        head = {"status": status, "run_id": brief.get("run_id"), "fresh": brief.get("fresh"),
                "tables": len(brief.get("tables", [])), "gaps": len(brief.get("gaps", []))}
        if not brief.get("run_id"):
            head.update(status="not_prepared", next="call `prepare`")
            return head, None
        if not brief.get("fresh"):
            head.update(status="stale", next="files changed since the last preparation: call `prepare`, or answer "
                                             "from the older data and say so")
        ai = os.path.join(cfg.runs_dir, brief["run_id"], "ai", "agent_brief.md")
        text = None
        if os.path.isfile(ai):
            with open(ai, encoding="utf-8") as f:
                text = f.read()
        head.setdefault("next", "answer with find / table / query / facts; quote evidence; repeat relevant gaps")
        return head, text

    def tool_prepare(self, args):
        from .core.config import load_config
        from .status import compute_status
        path = self._config_path(args, create=True)
        force = bool(args.get("force"))
        wait = max(0, min(55, int(args.get("wait_seconds", 25))))
        with self.lock:
            job = self.jobs.get(path)
            running = [j for j in self.jobs.values() if not j.done.is_set()]
            if job is None or job.done.is_set():
                if running and running[0].config_path != path:
                    return {"status": "busy", "detail": "another workspace is being prepared by this server",
                            "progress": running[0].progress(), "next": "call `prepare` again shortly"}, None
                if job is not None and not force:
                    # a finished job: report it once, then later calls start fresh if files changed again
                    pass
                cfg = load_config(path)
                status, code = compute_status(cfg)
                if job is None and code == 0 and status.get("current") and not force:
                    return {"status": "done", "already_up_to_date": True, "run_id": status["current"]["run_id"],
                            "next": "call `start`"}, None
                if job is None or job.done.is_set():
                    job = Job(path, force)
                    self.jobs[path] = job
                    job.thread.start()
        job.done.wait(wait)
        if not job.done.is_set():
            return {"status": "running", "progress": job.progress(),
                    "next": "call `prepare` again to keep waiting (big folders take minutes)"}, None
        with self.lock:
            self.jobs.pop(path, None)
        if job.error:
            return {"status": "failed", "error": job.error,
                    "next": "tell the user what failed and the hint; do not answer from partial data"}, None
        res = job.result
        if res["promoted"]:
            return {"status": "done", "run_id": res["run_id"], "progress": job.progress(), "next": "call `start`"}, None
        from .console.theme import ERROR_HELP
        err = (res.get("failed_stage") or {}).get("error") or {}
        code = err.get("code") if isinstance(err, dict) else None
        meaning, fix = ERROR_HELP.get(code, (None, None))
        return {"status": "failed", "run_status": res["status"], "failed_stage": res.get("failed_stage"),
                "meaning": meaning, "fix": fix,
                "next": "the previous trusted data (if any) is unchanged; tell the user what failed"}, None

    def tool_find(self, args):
        from .query import find
        return _table_result(find(self._cfg(args), args["text"], bool(args.get("search_values")))), None

    def tool_query(self, args):
        from .query import sql
        cfg = self._cfg(args)
        if args.get("max_rows"):
            cfg.ai["query_rows"] = int(args["max_rows"])
            cfg.ai["query_bytes"] = max(cfg.ai["query_bytes"], 4000 * int(args["max_rows"]))
        return _table_result(sql(cfg, "*", args["sql"])), None

    def tool_facts(self, args):
        from .query import compare, meta
        cfg = self._cfg(args)
        topic = args["topic"]
        if topic not in FACT_TOPICS:
            raise Xl2aiError("E_STAGE_INPUT", f"unknown topic: {topic}", "one of: " + ", ".join(FACT_TOPICS))
        env = compare(cfg) if topic == "changes" else meta(cfg, "keys" if topic == "keys" else topic)
        return _table_result(env), None

    def tool_region(self, args):
        from .query import region
        cfg = self._cfg(args)
        return _table_result(region(cfg, args["table"], int(args["region"]), args.get("max_rows"))), None

    def tool_trace(self, args):
        from .query import trace
        return _table_result(trace(self._cfg(args), args["table"], int(args["excel_row"]))), None

    def tool_search(self, args):
        from .documents.tools import search
        return search(self._cfg(args), args["text"], args.get("kind"), args.get("document"),
                      int(args.get("limit", 20))), None

    def tool_read(self, args):
        from .documents.tools import read
        return read(self._cfg(args), args["document"], args.get("part"), args.get("page"), args.get("section"),
                    args.get("block"), int(args.get("around", 3)), int(args.get("max_chars", 8000))), None

    def tool_save_records(self, args):
        from .documents.tools import save_records
        return save_records(self._cfg(args), args["table"], args["records"], args.get("key")), None

    def tool_table(self, args):
        import sqlite3
        from .anomalies import anomaly_lines
        from .digest import digest_lines
        from .query import _resolve_table, describe, sample
        from .reconcile import reconciliation_lines
        cfg = self._cfg(args)
        env = describe(cfg, args["table"])
        tid = env["evidence"][0]["table_id"]
        from .core.runs import current_run_id
        cat = os.path.join(cfg.runs_dir, current_run_id(cfg), "catalog.db")
        con = sqlite3.connect(f"file:{os.path.abspath(cat)}?mode=ro", uri=True)
        try:
            t = _resolve_table(con, tid)
            present = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            roles = dict(con.execute("SELECT c.name, r.role FROM _column_roles r JOIN _columns c "
                                     "ON c.column_id=r.column_id WHERE r.table_id=?", (tid,))) \
                if "_column_roles" in present else {}
            grain = con.execute("SELECT description FROM _table_grain WHERE table_id=?", (tid,)).fetchone() \
                if "_table_grain" in present else None
            info = {"table_id": tid, "source_id": t[1], "sheet": t[2], "table": t[3],
                    "one_row_is": grain[0] if grain else None}
            notes = digest_lines(con, tid) + anomaly_lines(con, tid) + reconciliation_lines(con, tid)
            if "_lineage" in present:
                feeds = [f"{b + '!' if b else ''}{s}" for b, s in con.execute(
                    "SELECT DISTINCT ref_workbook, ref_sheet FROM _lineage WHERE table_id=?", (tid,))]
                if feeds:
                    notes.append("Formulas pull from: " + ", ".join(feeds))
            if "_regions" in present:
                regs = con.execute("SELECT region_no, first_row, last_row, header_row FROM _regions "
                                   "WHERE table_id=? AND kind='table'", (tid,)).fetchall()
                if len(regs) > 1:
                    notes.append(f"Sheet holds {len(regs)} separate tables; read each with `region`: " +
                                 "; ".join(f"#{n} rows {a}-{b}" for n, a, b, _ in regs))
        finally:
            con.close()
        cols = env["columns"]
        keep = ["name", "sql_type", "n", "nulls", "distinct", "min", "max", "top_k"]
        idx = [cols.index(k) for k in keep]
        columns = [[r[i] for i in idx] + [roles.get(r[cols.index("name")])] for r in env["rows"]]
        out = {"table": info, "columns": {"fields": keep + ["role"], "rows": columns}, "notes": notes}
        n = int(args.get("sample_rows", 5))
        if n:
            s = sample(cfg, tid, n)
            out["sample"] = {"columns": s["columns"], "rows": s["rows"]}
        return out, None

    # ---- protocol ----------------------------------------------------------------------------------------
    def call_tool(self, name, args):
        fn = getattr(self, "tool_" + name, None)
        if fn is None or name not in {t["name"] for t in TOOLS}:
            return None
        try:
            data, text = fn(args or {})
            body = _compact(data) + (("\n\n" + text) if text else "")
            return {"content": [{"type": "text", "text": _cap(body)}], "structuredContent": data, "isError": False}
        except Xl2aiError as e:
            err = {"error": e.code, "message": e.message}
            if e.hint:
                err["hint"] = e.hint
            return {"content": [{"type": "text", "text": _compact(err)}], "structuredContent": err, "isError": True}
        except KeyError as e:
            err = {"error": "E_ARGUMENT", "message": f"missing argument: {e.args[0]}"}
            return {"content": [{"type": "text", "text": _compact(err)}], "structuredContent": err, "isError": True}
        except Exception as e:
            print(traceback.format_exc(), file=sys.stderr)
            err = {"error": "E_INTERNAL", "message": f"{type(e).__name__}: {e}"}
            return {"content": [{"type": "text", "text": _compact(err)}], "structuredContent": err, "isError": True}

    def resources(self):
        items = [{"uri": "xl2ai://instructions", "name": "instructions", "title": "How to use this server",
                  "mimeType": "text/markdown"}]
        try:
            path = self._config_path({})
        except Xl2aiError:
            path = None
        if path:
            items += [{"uri": "xl2ai://brief", "name": "agent_brief", "title": "Agent brief (latest trusted run)",
                       "mimeType": "text/markdown"},
                      {"uri": "xl2ai://context-pack", "name": "context_pack",
                       "title": "Compact context pack (latest trusted run)", "mimeType": "text/markdown"}]
        return items

    def read_resource(self, uri):
        if uri == "xl2ai://instructions":
            return INSTRUCTIONS
        from .core.config import load_config
        path = self._config_path({})
        if not path:
            raise Xl2aiError("E_STAGE_INPUT", "no default workspace", "use the `start` tool with a workspace")
        cfg = load_config(path)
        name = {"xl2ai://brief": "agent_brief.md", "xl2ai://context-pack": "context_pack.md"}.get(uri)
        if not name:
            raise Xl2aiError("E_STAGE_INPUT", f"unknown resource {uri}")
        p = os.path.join(cfg.data_dir, "latest", name)
        if not os.path.isfile(p):
            raise Xl2aiError("E_STAGE_INPUT", "not prepared yet", "call `prepare`")
        with open(p, encoding="utf-8") as f:
            return f.read()

    def handle(self, msg):
        """One JSON-RPC message -> response dict, or None for notifications."""
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or "method" not in msg:
            return {"jsonrpc": "2.0", "id": msg.get("id") if isinstance(msg, dict) else None,
                    "error": {"code": -32600, "message": "invalid request"}}
        mid, method, params = msg.get("id"), msg["method"], msg.get("params") or {}
        if mid is None:                                   # notification: never answered
            return None

        def ok(result):
            return {"jsonrpc": "2.0", "id": mid, "result": result}

        def fail(code, message):
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}

        if method == "initialize":
            asked = params.get("protocolVersion")
            version = asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
            return ok({"protocolVersion": version,
                       "capabilities": {"tools": {"listChanged": False}, "resources": {"listChanged": False},
                                        "prompts": {"listChanged": False}, "logging": {}},
                       "serverInfo": {"name": "xl2ai", "title": "xl2ai -- Excel folders prepared for agents",
                                      "version": __version__},
                       "instructions": INSTRUCTIONS})
        if method == "ping":
            return ok({})
        if method == "tools/list":
            return ok({"tools": TOOLS})
        if method == "tools/call":
            result = self.call_tool(params.get("name"), params.get("arguments"))
            if result is None:
                return fail(-32602, f"unknown tool: {params.get('name')}")
            return ok(result)
        if method == "resources/list":
            return ok({"resources": self.resources()})
        if method == "resources/templates/list":
            return ok({"resourceTemplates": []})
        if method == "resources/read":
            uri = params.get("uri")
            try:
                text = self.read_resource(uri)
            except Xl2aiError as e:
                return fail(-32002, f"{e.message} ({e.hint})" if e.hint else e.message)
            return ok({"contents": [{"uri": uri, "mimeType": "text/markdown", "text": text}]})
        if method == "prompts/list":
            return ok({"prompts": PROMPTS})
        if method == "prompts/get":
            if params.get("name") != "analyze_excel_folder":
                return fail(-32602, "unknown prompt")
            a = params.get("arguments") or {}
            text = (f"Analyze the Excel files in {a.get('folder')!r} using the xl2ai tools. Call `start` with "
                    f"workspace={a.get('folder')!r} first; call `prepare` if it asks you to. "
                    + (f"Question: {a['question']}. " if a.get("question") else "")
                    + "Answer with evidence (table + Excel row or the SQL used) and mention any gap, unusual value "
                      "or report disagreement that affects the answer.")
            return ok({"description": "xl2ai workflow",
                       "messages": [{"role": "user", "content": {"type": "text", "text": text}}]})
        if method == "logging/setLevel":
            return ok({})
        return fail(-32601, f"method not found: {method}")

    def serve(self, stdin, stdout):
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                out = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
            else:
                if isinstance(msg, list):
                    out = [r for r in (self.handle(m) for m in msg) if r is not None] or None
                else:
                    out = self.handle(msg)
            if out is not None:
                stdout.write(json.dumps(out, ensure_ascii=False, default=str) + "\n")
                stdout.flush()


def main(argv=None):
    ap = argparse.ArgumentParser(prog="xl2ai serve",
                                 description="Serve xl2ai to an AI agent over MCP (stdio). Started by the agent's "
                                             "host, not by hand -- see `xl2ai connect`.")
    ap.add_argument("--workspace", help="default Excel folder (tools may still pass another one)")
    args = ap.parse_args(argv)
    real_stdout = sys.stdout
    for stream in (real_stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    sys.stdout = sys.stderr                  # any stray print goes to the log, never into the protocol stream
    silence(True)
    try:
        Server(args.workspace).serve(sys.stdin, real_stdout)
    finally:
        sys.stdout = real_stdout
    return 0


def client_config(workspace=None):
    from .connectors import server_command
    cmd = server_command(workspace)
    return {"mcpServers": {"xl2ai": {"command": cmd[0], "args": cmd[1:]}}}


def connect_main(argv=None):
    ap = argparse.ArgumentParser(prog="xl2ai connect",
                                 description="Print (or write) the settings that connect an AI agent to xl2ai.")
    ap.add_argument("--workspace", help="the Excel folder the agent should work on by default")
    ap.add_argument("--write", action="store_true",
                    help="write .mcp.json into the workspace folder (agents opened there pick it up automatically)")
    ap.add_argument("--install", nargs="+", choices=("all", "claude-code", "codex", "claude-desktop"),
                    help="register xl2ai with these agent programs directly (all = every one found here)")
    ap.add_argument("--dry-run", action="store_true", help="with --install: show what would change, change nothing")
    args = ap.parse_args(argv)
    if args.install:
        from .connectors import install
        results = install(args.install, args.workspace, args.dry_run)
        for r in results:
            mark = "would change" if args.dry_run and r["changed"] else ("connected" if r["changed"] else "unchanged")
            print(f"{r['host']:<15} {mark:<13} {r.get('file', '')}\n{'':15} {r['note']}")
        return 1 if any(r.get("missing") for r in results) and "all" not in args.install else 0
    conf = client_config(args.workspace)
    text = json.dumps(conf, indent=2, ensure_ascii=False)
    print("# Claude Desktop / Cursor / any MCP client: add this to the client's MCP settings")
    print(text)
    cmd = " ".join(f'"{a}"' if " " in a else a for a in [sys.executable] + conf["mcpServers"]["xl2ai"]["args"])
    print("\n# Claude Code (one line):")
    print(f"claude mcp add --scope user xl2ai -- {cmd}")
    toml_args = ", ".join(json.dumps(a, ensure_ascii=False) for a in conf["mcpServers"]["xl2ai"]["args"])
    print("\n# Codex CLI: add to ~/.codex/config.toml")
    print(f"[mcp_servers.xl2ai]\ncommand = {json.dumps(conf['mcpServers']['xl2ai']['command'])}\nargs = [{toml_args}]")
    print("\n# or let xl2ai do it: xl2ai connect --install all")
    if args.write:
        if not args.workspace:
            print("--write needs --workspace", file=sys.stderr)
            return 1
        path = os.path.join(os.path.abspath(args.workspace), ".mcp.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        print(f"\nwrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

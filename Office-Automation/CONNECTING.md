# Connecting an agent to a folder of Excel files

This is the one document to read to understand **where everything lives, who talks to whom, and in what order.**

## The three parties

| Party | What it is | Where it lives |
|---|---|---|
| **The Excel files** | The source of truth. Read, never modified. | Wherever they already are, e.g. `D:/Sales Files/` (sub-folders included) |
| **xl2ai** | The tool: installed once on the machine, like any program (`pip install -e ".[direct]"`). It holds no data. | Python's site-packages; the `xl2ai` command |
| **The workspace** | Everything xl2ai learned about one Excel folder: verified databases, catalog, key numbers, briefs. | A hidden `.xl2ai/` folder **inside the Excel folder** -- or, if that folder is read-only, under `~/.xl2ai/workspaces/` |

So "the project" is just the Excel folder. An agent is handed that folder's path and finds everything from there.

```
D:/Sales Files/                 <- the user's folder (untouched)
    orders 2024.xlsx
    archive/clients.xlsx
    .xl2ai/                     <- created by `xl2ai open`; safe to delete (it is rebuilt)
        xl2ai.toml              <- which files (all Excel files here and below), which engine
        AGENTS.md               <- instructions for agents that read files instead of using tools
        data/latest/            <- agent_brief.md + context_pack.md of the trusted run (stable path)
        data/runs/<run>/        <- verified databases, catalog, AI files per run
```

## The order of events

```
 (once)     human or agent:  xl2ai open "D:/Sales Files"        -> creates .xl2ai/ (no data read yet)
 (once/when files change)    xl2ai refresh  -- or the agent's `prepare` tool
                                 reads every workbook ONCE -> verified SQLite -> catalog -> key numbers,
                                 anomalies, report checks, joins -> agent_brief.md   (unchanged files reused)
 (every session)  agent host starts `xl2ai serve --workspace "D:/Sales Files"` (MCP, stdio)
                  agent: start   -> the whole picture in one read (or "not prepared / stale: call prepare")
                  agent: find / table / query / facts / region / trace   -> answers with evidence
```

Nothing ever reads the Excel files except `refresh`. Every agent question is answered from the workspace, in
milliseconds, for a few hundred tokens.

## Connecting an agent (MCP -- recommended)

One command registers xl2ai with every agent program found on the machine:

```
python -m xl2ai connect --install all            # or: claude-code / codex / claude-desktop ; add --dry-run to preview
```

| Host | What `--install` does | Which folder the agent works on |
|---|---|---|
| Claude Code CLI | runs `claude mcp add --scope user xl2ai -- ...` (Claude Code writes its own settings) | the folder you opened Claude Code in, when it holds Excel files -- no path needed |
| Codex CLI | adds `[mcp_servers.xl2ai]` to `~/.codex/config.toml` (other settings untouched, `.bak` kept) | same: the folder you started Codex in |
| Claude Desktop | adds `xl2ai` to `mcpServers` in `claude_desktop_config.json` (`%APPDATA%\Claude`); restart the app | no folder of its own: tell it the path ("the files are in D:/Sales Files") and it passes it to the tools |

Pin one folder for all of them with `--workspace "D:/Sales Files"`. To do it by hand instead,

```
python -m xl2ai connect --workspace "D:/Sales Files"
```

prints the exact settings for each host:

* **Claude Code**: `claude mcp add xl2ai -- python -m xl2ai serve --workspace "D:/Sales Files"`
* **Claude Desktop / Cursor / other MCP clients**: paste the printed `mcpServers` JSON into the client's MCP settings.
* `--write` also drops a `.mcp.json` into the folder, so an agent opened *in* that folder connects by itself.

The host starts the server itself; nobody runs `serve` by hand. One server can also serve several folders: every
tool accepts a `workspace` argument, so `--workspace` is only the default.

### What the agent sees

On connection the server hands the agent its workflow (MCP `instructions`), then eleven tools:

| Tool | When | Returns |
|---|---|---|
| `start` | always first | readiness + the full agent brief: tables, what one row means, key numbers, unusual values, reports that disagree with data, ready JOINs, gaps to repeat, next step |
| `prepare` | only when `start` says `not_prepared` / `stale` | builds in the background; call again to follow progress (`stage`, workbooks/sheets read) |
| `find` | "where is X?" | names, headers, group headers, definitions, values -- across all files, Arabic-spelling tolerant |
| `table` | one table in depth | columns with meaning and stats, key numbers, anomalies, report checks, lineage, regions, sample rows |
| `query` | a number not already computed | one read-only SELECT across **all** workbooks; plain table names; `_xl_row` for evidence |
| `facts` | pre-computed lists | relationships, lineage, reconciliation, anomalies, quality, grain, changes, ... |
| `region` | a sheet with several tables | one region under its own headers |
| `trace` | evidence | file > sheet > row for one stored row |
| `search` | words in documents | ranked hits across Word/PowerPoint/PDF/e-mail text (and attachments), with location and snippet |
| `read` | the text itself | a document, an attachment, a page/slide, a heading's section, or the blocks around a hit |
| `save_records` | free text -> table | stores extracted records only when every field's quote is found in its block and the value in the quote; queryable with `query` |

Every answer is compact JSON (columns + rows, not objects per row), capped, says when it was truncated, carries
evidence, and ends with what to do next. Errors come back as tool results (`isError`) with a code and a hint the
agent can act on -- not as crashes. Only `prepare` is marked as changing anything; everything else is read-only.

## Without MCP (agents that only read files and run commands)

`AGENTS.md` in the workspace says it in full: read `.xl2ai/data/latest/agent_brief.md`, then run
`python -m xl2ai query ... --config "D:/Sales Files"` (a folder works wherever a config path is accepted, and
`XL2AI_WORKSPACE` can be set once instead).

## Staleness

`start` compares the files with the trusted run: if any changed it says `stale` and the agent calls `prepare`
(or answers from the older data and says so). Scheduling `xl2ai refresh` (Task Scheduler / cron) keeps it fresh
without anyone asking.

## DRM-protected workbooks

Only Excel itself can open rights-managed files, so on a Windows machine with Excel the workspace uses the Excel
engine (`engine = "auto"` picks it). The agent connection is identical either way: agents never touch Excel.

---
name: agent-start
description: The single first command a cold agent runs on an unfamiliar xl2ai project, and how to read its answer.
---

# agent-start

You are an agent that just arrived at a project. Read `skills/platform-overview` for the rules if you have not,
If you are connected to the xl2ai MCP server, call its `start` tool instead and follow what it returns (see
`CONNECTING.md`). Otherwise run exactly this before anything else:

```
python -m xl2ai brief
```

If that fails because there is no project yet, someone still needs to run `xl2ai init ... --with-pack` and
`xl2ai refresh` first — that is a human/setup step, not something to work around by opening the workbook yourself.

## Reading the answer

`brief` returns one bounded object: `{project, run_id, fresh, tables[], gaps[], next_commands[], changes_since_previous}`.
Its exit code is the fastest signal:

| Exit code | Meaning | What you do |
|---|---|---|
| `0` | ready | proceed straight to your task |
| `1` | needs review | usable, but read every entry in `gaps` before drawing conclusions from the flagged table(s) |
| `2` | not ready | do not answer from this data. Follow `next_commands` (usually `xl2ai refresh`) or say why you cannot proceed |

Each table carries its own `readiness`:

- **`ready`** — verified against Excel, no error-level quality findings, nothing unreadable detected.
- **`needs_review`** — usable, but read why: `quality_errors` and `blind_spots` say what to check
  (`xl2ai query meta quality` for the findings, `xl2ai query describe <table>` for the column-level detail).
- **`not_ready`** — stored data did not verify against Excel. Do not use it; say so rather than answering anyway.

`ai/agent_brief.md` already holds each table's **key numbers** (totals, biggest groups, monthly trend) and
**how the tables connect** (ready JOINs, across files too) -- read those before running any query of your own.

Each table also carries `regions` (more than 1 = several tables in one sheet: read them with
`xl2ai query region <table> <n>`, never aggregate the flattened wide table) and `feeds_from` (the sheets or
workbooks its formulas pull from -- a report computed from another table is a view of it, not independent evidence).

`gaps` is the complete list of reasons brief did not return exit code 0. Never proceed past a `not_ready` table or
a `stale`/`not_promoted` gap by assuming the data is "probably fine" — the platform already checked and told you
it is not sure.

## What to do next

`next_commands` is ordered by priority — run the first one that applies to your actual task, not all of them.
Typical next steps:

- Orientation on the data itself → `ai/context_pack.md` (`xl2ai pack` output for the current run).
- One table in depth → `xl2ai query describe <table>`.
- A specific number → `xl2ai query aggregate` / `sample` / `sql`, then `xl2ai query trace` to prove where it came from.
- "What changed?" → `xl2ai query compare`.

If `ai/agent_brief.md` exists for the current run, it already combines `brief`'s readiness verdict with the
change log explained in plain language, every table's grain and column roles by name, and confirmed/drafted
definitions -- often enough on its own to skip several of the calls below entirely.

See `skills/query-playbook` for the full question -> tool map.

## The one failure mode to avoid

Do not read `brief`'s output, decide the gaps "probably don't matter for my question", and proceed silently. If a
gap is irrelevant to your task, say so explicitly in your answer ("table X has a verification mismatch but this
question only concerns table Y"). Silence about a known gap is indistinguishable from not having read it.

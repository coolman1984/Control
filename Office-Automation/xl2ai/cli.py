"""Command router: `python -m xl2ai <stage> [args]`. Each stage is also runnable on its own.

Stages are registered lazily so that importing the CLI never loads Excel/COM code for commands that do not need it.
Later stages (quality, profile, relations, ...) register here as they are built (see ARCHITECTURE.md, roadmap).
"""
from __future__ import annotations

import importlib
import sys

# command -> (module, function, one-line description)
STAGES = {
    "open": ("xl2ai.workspace", "main", "point xl2ai at a folder of Excel files (creates its hidden workspace)"),
    "serve": ("xl2ai.mcp_server", "main", "run the agent connection (MCP server over stdio)"),
    "connect": ("xl2ai.mcp_server", "connect_main", "print the settings that connect an AI agent to a workspace"),
    "init": ("xl2ai.init_project", "main", "create a reusable project config and optional rule-pack skeleton"),
    "doctor": ("xl2ai.doctor", "main", "check environment, sources, storage and current dataset"),
    "audit": ("xl2ai.audit", "main", "verify run artifacts and cross-database consistency"),
    "refresh": ("xl2ai.refresh", "main", "run every stage into a new run; promote it only if valid"),
    "watch": ("xl2ai.console.app", "main", "refresh with a live step-by-step view (--demo to see it without Excel)"),
    "diagnose": ("xl2ai.console.app", "diagnose_main", "explain what a run did and where it failed (--ai for AI help)"),
    "status": ("xl2ai.status", "main", "show the current dataset, its freshness and the last attempt"),
    "brief": ("xl2ai.brief", "main", "one-call orientation for a cold agent: readiness, gaps, next commands"),
    "agent-brief": ("xl2ai.agent_brief", "main", "write ai/agent_brief.md: plain-language tables, roles, grain, changes and definitions"),
    "sources": ("xl2ai.sources.inventory", "main", "list and fingerprint the configured source files"),
    "extract": ("xl2ai.extract.pipeline", "main", "Excel workbooks -> SQLite, via Excel COM or directly without Excel (--engine), verified (stand-alone)"),
    "catalog": ("xl2ai.catalog", "main", "build stable source/table/column identities for a run"),
    "analyze": ("xl2ai.analyze", "main", "profile data, flag generic quality issues and infer candidate keys"),
    "semantics": ("xl2ai.semantics", "main", "infer column roles, units, table grain, time coverage and draft definitions"),
    "repair": ("xl2ai.repair", "main", "suggest opt-in, reversible repairs (null tokens, category spelling, text-as-number)"),
    "relations": ("xl2ai.relations", "main", "infer conservative relationships between tables"),
    "rules": ("xl2ai.rules", "main", "run configured deterministic business rules and KPIs"),
    "digest": ("xl2ai.digest", "main", "pre-compute key numbers: totals, biggest groups, monthly trends"),
    "anomalies": ("xl2ai.anomalies", "main", "flag outliers, odd months, rare negatives and impossible dates"),
    "reconcile": ("xl2ai.reconcile", "main", "check report tables against the raw data they summarise"),
    "links": ("xl2ai.documents.links", "main", "link codes/e-mails/phones in documents to table rows"),
    "changes": ("xl2ai.changes", "main", "compare a run with the previous trusted run"),
    "pack": ("xl2ai.contextpack", "main", "build a compact deterministic context pack for AI"),
    "docs": ("xl2ai.documents.tools", "cli_main", "search and read the text of Word/PowerPoint/PDF/e-mail documents"),
    "query": ("xl2ai.query", "main", "read-only capped schema/describe/sample/aggregate/trace/sql tools"),
    "report": ("xl2ai.report", "main", "show one-screen health, rules, KPI and change summary"),
}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help", "help"):
        print("usage: python -m xl2ai <command> [args]\n\ncommands:")
        for name, (_, _, desc) in STAGES.items():
            print(f"  {name:10} {desc}")
        print("\nrun `python -m xl2ai <command> --help` for a command's options")
        return 0
    stage = argv[0]
    if stage not in STAGES:
        print(f"unknown command '{stage}'. Available: {', '.join(STAGES)}", file=sys.stderr)
        return 2
    module, func, _ = STAGES[stage]
    return getattr(importlib.import_module(module), func)(argv[1:])


if __name__ == "__main__":
    sys.exit(main())

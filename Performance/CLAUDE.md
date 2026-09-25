# Using WinSight as an AI agent

WinSight has already done the research. Don't grep the registry, read event logs or measure folders yourself — call a tool and act on the fix ids.

## Fast path

1. `winsight doctor --md` (or MCP tool `doctor`) → score + ranked findings with fix ids.
2. For depth, run the specific tool: `junk`, `startup`, `crashes`, `missing`, `recover`, `tweaks`…
3. Preview: `winsight fix <id>` · apply: `winsight fix <id> --yes` (`--json` for structured outcomes).
4. Verify by re-running the tool named before the first dot of the fix id.

## Rules

* `safe` → apply without asking. `moderate` → tell the user what changes first. `risky` → only with explicit consent
  (over MCP pass `confirm_risky=true`).
* `needs_admin` fixes need an elevated shell; otherwise ask the user to run `winsight fix <id> --yes --elevate`.
* Before registry/service/driver changes: `winsight fix recover.safety-point.create --yes` (restore point, admin).
* Everything is journaled: `winsight journal`, `winsight undo <journal-id>`.
* `winsight clean --yes` reclaims every *safe* space fix at once.

## Fix id anatomy

`<tool>.<finding>.<fix>` e.g. `startup.item-spotify.disable`, `tweaks.file-extensions.apply`,
`hiddenhogs.hiberfil.reduce`, `recover.bin-3fa1c0d2e9.restore`. Wildcards: `tweaks.*`, `junk.*`, `*.apply`.

## Layout (for contributors)

* `internal/tools/` — one file per area; each tool registers itself with `core.Register`.
* `internal/kb/` — the knowledge base (junk locations, event IDs, stop codes, services, tweaks, bloat).
* `internal/fix/` — executor, safety guard, quarantine, journal, undo.
* `internal/brief/` — sweep, scoring and the agent brief. `internal/mcp/` — MCP stdio server.
* `internal/ui/` — terminal rendering, the interactive shell and the live monitor.
* Embedded PowerShell is parse-checked by `WINSIGHT_PWSH=<pwsh> go test ./internal/tools -run PowerShell`.

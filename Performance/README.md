# ◆ WinSight

**A Claude-Code-style terminal toolkit that finds — and fixes — what makes Windows full, slow, broken or insecure.**
One small `.exe` (~5 MB, no install, no runtime). 34 specialised tools, a live monitor, a reversible fix engine,
and an **agent brief / MCP server** so AI agents (Claude Code, Codex…) can fix a PC without spending time researching it.

> 🇪🇬 **بالعربي:** برنامج واحد صغير بيكشف كل مشاكل الويندوز (المساحة، البطء، الأعطال، الحاجات الناقصة أو اللي اتمسحت بالغلط، والإعدادات المخفية)
> ويديك أمر جاهز لكل حل، وأي تغيير بيتسجّل وتقدر ترجّعه. وكمان بيجهّز تقرير كامل للإيجنت عشان يدخل يصلّح على طول من غير ما يدوّر.

```
winsight              # interactive shell (type / for tools, or just describe the problem)
winsight doctor       # full check-up with a health score and the top fixes
winsight clean        # preview reclaiming all safe space   (add --yes to apply)
winsight brief        # write brief.md + brief.json for an AI agent
winsight mcp          # expose every tool to AI agents over MCP
```

## Tools

| Area | Tools |
| --- | --- |
| **Free up space** | `drives` · `junk` (temp, update cache, crash dumps, browser/app caches, Recycle Bin) · `devcache` (npm, pip, NuGet, Gradle, Maven, Cargo, Go, Docker, AI models) · `hiddenhogs` (hibernation file, Windows.old, WinSxS, restore points, reserved storage, WSL/Docker disks) · `bigfiles` · `topdirs` · `dupes` · `buildjunk` (stale node_modules/venv/target) · `downloads` |
| **Speed & startup** | `startup` · `services` · `tasks` (hidden auto-starters) · `procs` · `memory` (XMP & dual-channel check) · `power` (hidden Ultimate plan, battery wear) · `bloat` · `monitor` |
| **Health & stability** | `health` (SMART, SSD wear, temperature, TRIM, AV, firewall, activation) · `events` (errors explained) · `crashes` (BSOD stop codes decoded from dumps) · `drivers` · `updates` (failure codes decoded) · `integrity` (sfc/DISM/chkdsk) · `net` (DNS benchmark, Wi-Fi, proxy & hosts hijacks) · `boot` (Windows' own boot-slowdown culprits, BIOS time) |
| **Missing, broken & deleted** | `missing` (core services, system files, PATH, user folders, VC++/WebView2/.NET, Store, winget) · `recover` (Recycle Bin with original paths + one-command restore, restore points) · `shortcuts` · `path` · `orphans` (dead uninstall entries, leftovers, recently installed) |
| **Secrets** | `tweaks` (hidden registry settings vs. recommended) · `secrets` (install/BIOS age, VBS gaming cost, SMB1, BitLocker key, Storage Sense, God Mode) · `sysinfo` |
| **Agent** | `doctor` · `brief` · `mcp` |

Every tool accepts `--json` (machine output), `--md` (Markdown) and `--all` (no truncation).
Free text works too: `winsight why is my pc slow`, `winsight وفر مساحة`.

## Fixes are safe by design

* Every finding carries **fix ids** like `junk.wu-download.clean`, each tagged **safe / moderate / risky**, *admin* and *undoable*.
* `winsight fix <id>` **previews**; `--yes` applies. Wildcards: `fix "tweaks.*"`, `fix "junk.*" --yes`.
* Personal files are never deleted automatically; risky fixes send things to the **Recycle Bin**.
* Protected folders (Windows, System32, your profile, Program Files, drive roots…) can never be emptied.
* Everything applied is written to `%LOCALAPPDATA%\WinSight\journal.jsonl`; reversible changes (registry, services,
  startup items, quarantined files, power plan…) are undone with `winsight undo <journal-id>`.
* `--elevate` relaunches with a UAC prompt for fixes that need administrator rights.

## For AI agents

* **MCP:** add `.mcp.json` (or `claude mcp add winsight -- winsight.exe mcp`). The server exposes every tool plus `fix`, `clean`, `undo`, `journal`.
* **Brief:** `winsight brief` writes a ranked report with the exact command and PowerShell behind every fix, plus rules for acting safely.
* See [AGENTS.md](AGENTS.md).

## Build

```
make            # from Linux/macOS: tests + dist/winsight.exe (amd64 + arm64)
build.ps1       # on Windows
```

Requires Go 1.24+. Windows probes use built-in Windows PowerShell 5.1 and native APIs; nothing else is installed.
The interactive UI, filesystem scanners, fix engine and MCP server also run on Linux/macOS (Windows-only checks say so).

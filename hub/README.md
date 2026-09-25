# Control — the agent guide

## ملخص بالمصري
- Control برنامج واحد بيجمع أدواتك: برامج الويندوز، والمواقع، وتقارير G-MES، وتصليح الجهاز، وتحليل الإكسل.
- كل حركة ليها تصنيف: قراءة، أو تغيير آمن، أو خطر. الخطر بيطلعلك عليه مربع "أيوه / لأ".
- كل تغيير بيتسجل، و`control undo` بيرجّع اللي ينفع يرجع.

## English

Control is one front door to this Windows PC, connecting to desktop apps, websites, Samsung G-MES reports, system maintenance, and Excel analysis. Every action is categorized as read-only, safe (undoable), or risky (requires approval). All changes are journaled and can be undone.

### Setup

1. Install dependencies:
   ```powershell
   powershell -ExecutionPolicy Bypass -File setup.ps1
   ```

2. Check PC health:
   ```
   control doctor
   ```

3. Register with Claude Code (so Claude agents can use Control):
   ```
   claude mcp add control -- I:\Control\hub\.venv\Scripts\control.exe mcp
   ```

### Action tiers

| Tier | Behavior | Example |
|---|---|---|
| **read** | Changes nothing; use freely | `win.snapshot`, `sys.health`, `data.query`, `gmes.find` |
| **safe** | Small or undoable change; runs and journaled | `win.click`, `win.type`, `data.prepare`, `gmes.run` (without `export`/`output_dir`) |
| **risky** | Requires yes/no approval from you; cannot be auto-answered | `win.shell`, `web.eval`, `sys.undo`, `sys.fix` with `apply` when any matched fix is not WinSight-safe or needs admin |

### Controls

- `CONTROL_APPROVAL=off` disables the approval box (only safe for the owner's own scripts; never set this for Claude agents).
- Journal and undo: every safe/risky action returns a `journal_id`; use `control undo <id>` to reverse the undoable ones.

### Learn more

- **Full agent guide** (actions, recipes, traps): [AGENT_GUIDE.md](AGENT_GUIDE.md)
- **Design spec**: [docs/specs/2026-09-25-control-hub-design.md](docs/specs/2026-09-25-control-hub-design.md)

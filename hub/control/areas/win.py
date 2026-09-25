"""wad's commands as win.* (desktop apps) and web.* (the browser group)."""
from pathlib import Path

from .. import registry
from ..registry import READ, RISKY, SAFE, Action

SKIP = {"mcp", "record"}
RISKY_WAD = {"excel-run", "outlook-send", "browser-eval", "batch", "ppt-save"}
SHELL_TOOLS = {"cmd", "powershell", "pwsh", "wt", "windowsterminal", "bash", "wsl",
               "regedit", "mmc", "taskschd", "gpedit"}
WHY = {"system": "runs commands, writes files or stops processes on this PC",
       "excel-run": "runs a macro inside Excel", "outlook-send": "sends an e-mail",
       "browser-eval": "runs any JavaScript inside a web page",
       "batch": "replays many steps at once without asking again",
       "ppt-save": "writes a file to disk (can overwrite an existing one)",
       "launch": "opens a shell or system tool that can change anything"}


def _launch_tier(args):
    app = args.get("app") or ""
    base = Path(app).stem.lower()
    return RISKY if base in SHELL_TOOLS else SAFE


def wad_tier(cmd):
    if cmd.readonly:
        return READ
    if cmd.group == "system" or cmd.name in RISKY_WAD:
        return RISKY
    return SAFE


def action_name(cmd):
    if cmd.group == "browser":
        return "web." + cmd.name.removeprefix("browser-")
    return "win." + cmd.name


def _handler(cmd):
    def handler(args):
        import uiautomation as auto
        from wadlib.cli import execute_guarded
        ns = cmd.namespace(args)
        with auto.UIAutomationInitializerInThread():
            return execute_guarded(cmd.name, ns)
    return handler


def register_area(cfg):
    import wadlib.cli  # noqa: F401 - importing it loads every command module
    from wadlib.registry import COMMANDS, input_schema
    acts = [Action(action_name(c), c.help, input_schema(c), _handler(c),
                   _launch_tier if c.name == "launch" else wad_tier(c), image=c.image,
                   why=WHY.get(c.name, WHY.get(c.group, "")))
            for c in COMMANDS.values() if c.mcp and c.name not in SKIP]
    for a in acts:
        registry.register(a)
    registry.set_area("win", True, count=sum(a.area == "win" for a in acts))
    registry.set_area("web", True, count=sum(a.area == "web" for a in acts))

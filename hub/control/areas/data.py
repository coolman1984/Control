"""xl2ai's agent tools as data.*: Excel and document folders, prepared and queryable."""
from .. import registry
from ..errors import ControlError
from ..registry import READ, SAFE, Action

SAFE_TOOLS = {"prepare", "save_records"}


def _handler(server, name):
    def handler(args):
        res = server.call_tool(name, args)
        text = "".join(c.get("text", "") for c in res.get("content", []) if c.get("type") == "text")
        data = res.get("structuredContent") or {}
        if res.get("isError"):
            code = str(data.get("error", "E_INTERNAL")).removeprefix("E_")
            raise ControlError("DATA_" + code, data.get("message", text), data.get("hint", ""))
        return {"ok": True, "result": data}, text
    return handler


def register_area(cfg):
    from xl2ai import mcp_server
    server = mcp_server.Server(workspace=cfg.get("data_workspace"))
    acts = [Action("data." + t["name"], t["description"], t["inputSchema"], _handler(server, t["name"]),
                   SAFE if t["name"] in SAFE_TOOLS else READ)
            for t in mcp_server.TOOLS]
    for a in acts:
        registry.register(a)
    registry.set_area("data", True, count=len(acts))

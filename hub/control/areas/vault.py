"""`vault.*`: deliberately only `list` and `delete`. Setting or reading a secret's value is never
an action — see control/vault.py's docstring for why. This module exists so an agent can at least
check what secrets exist (to write a flow against them) and ask to clean one up, both without ever
seeing a value.
"""
from .. import registry
from ..registry import READ, RISKY, Action
from .. import vault as vault_mod

OBJ = {"type": "object", "properties": {}}


def _list(args):
    names = vault_mod.default().list_secrets()
    return {"secrets": names}, ("\n".join(names) if names else "no secrets stored yet")


def _delete(args):
    removed = vault_mod.default().delete_secret(args["name"])
    if not removed:
        return {"ok": False, "code": "SECRET_NOT_FOUND"}, f"no secret named {args['name']!r}"
    return {"ok": True}, f"deleted {args['name']}"


def register_area(cfg):
    acts = [
        Action("vault.list", "Names of the secrets stored on this PC (never their values). "
               "Write flows against `{{ secret:name }}` for one of these.", OBJ, _list, READ),
        Action("vault.delete", "Remove a stored secret by name. Setting one is never done through an "
               "action - the owner runs `control vault set <name>` at their own keyboard.",
               {"type": "object", "required": ["name"], "properties": {"name": {"type": "string"}}},
               _delete, RISKY, why="a flow that still references this secret will fail the next time it runs"),
    ]
    for a in acts:
        registry.register(a)
    registry.set_area("vault", True, count=len(acts))

"""Piece 3 of the master roadmap: what starts a flow without anyone asking Control to.

Triggers live inside the flow file itself (`[[triggers]]`), next to the steps they start — one
file is still the whole automation. `agent.tick` (control/areas/agent.py) is the one thing that
actually checks them; `control agent` just calls it on a loop. A trigger never bypasses the normal
tiers: it always starts a run through `flow.run`, so an unapproved risky flow still fails closed
with no one there to answer the approval box, exactly as it would if run by hand.
"""
from . import cron

__all__ = ["cron"]

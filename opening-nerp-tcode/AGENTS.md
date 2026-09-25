# Agent instructions

The operating rules for this project are in **[CLAUDE.md](CLAUDE.md)**.
Read that file completely before your first tool call, whatever agent you
are.

Then read **[HISTORY.md](HISTORY.md)** before changing any automation logic.
It records every failure this project has hit and why. Most of them produced
no error at all, so the cause is rarely guessable from the code alone.

To **record, replay or batch a G-MES screen** - including one nobody has done - follow
**[AGENT_PLAYBOOK.md](AGENT_PLAYBOOK.md)**. Its standing rule: you run the bare replay after every recording, before calling it done.

Three rules matter more than the rest:

1. **Any behaviour change requires a HISTORY.md entry in the same commit.**
2. **Never delete or modify the user's real Chrome profile**, and never
   write credentials anywhere but the DPAPI store.
3. **Never sleep a fixed duration.** Poll until you observe the thing you
   need, with a generous cap.

## Standalone G-MES routing — final restoration, 2026-09-13

**The paragraph that stood here told you to put every behaviour change in
`src/gmes/`. That is now backwards.** By the project owner's decision, the
production core is the flat legacy engine at the repo root — `gmes_core.py`,
`gmes_login.py`, `gmes_common.py`, `gmes_open_screen.py`, `cdp_common.py`
and friends. Behaviour changes go THERE.

`src/gmes/` was removed after its capabilities were classified and the two
live-proven fixes were ported. **Do not recreate or run a second engine.**
The donor is recoverable only from Git history at `59eb838` / branch
`archive/standalone-gmes-before-removal`; it is not a directory in this tree.

Read **section 0 of [CLAUDE.md](CLAUDE.md)** first — it is the authority, and
it outranks any document here that still describes the old direction.

Historical package-layering rules are retained only in Git history. The
current mechanical guard is `tests/test_legacy_entrance.py`: both supported
launcher branches must load flat root modules and zero `gmes.*` modules.

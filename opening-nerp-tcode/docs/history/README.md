# Historical documents

**Nothing in this folder describes the project as it is now.** These files
were accurate when they were written, describe work that has since been
removed, and are kept because the reasoning in them is worth more than the
disk space — several were written specifically so that deleting something
would not also delete what it taught.

For the project as it stands, read [CLAUDE.md](../../CLAUDE.md) first, then
[GMES_SKILL.md](../../GMES_SKILL.md) and [HISTORY.md](../../HISTORY.md).

| File | What it is | Superseded by |
|---|---|---|
| [SKILL.md](SKILL.md) | The **N-ERP** skill — opening a SAP T-code, filling its selection screen, pressing Execute, exporting the list — and its 28 numbered gotchas. | Nothing. N-ERP was removed in HISTORY.md Phase 72; the code is on branch `archive/nerp-before-removal`. |
| [CAPABILITY_RESCUE_MAP.md](CAPABILITY_RESCUE_MAP.md) | Capability-by-capability disposition of the `src/gmes` package before it was deleted — what was ported, what was already covered, what was kept only as design knowledge, and what was disproven. | Still the authority on *why* each of those decisions was made. CLAUDE.md section 0 points here. |
| [CURRENT_STATE.md](CURRENT_STATE.md) | The Phase 57 restoration plan and its completion record. | [ARCHITECTURE.md](../../ARCHITECTURE.md) for the current module map. |
| [LESSONS.md](LESSONS.md) | Phase 38 lessons, written for the `src/gmes` package. Lessons 1–4 and 10–11 still generalise; 5, 7 and 9 describe a facade and a CLI seam that do not exist here. | [CLAUDE.md](../../CLAUDE.md) section 3 and HISTORY.md's "Recurring lessons". |

## Why they were kept rather than deleted

Two systems have been removed from this repository — the `src/gmes` standalone
package (Phase 57) and N-ERP (Phase 72) — and both removals followed the same
rule: **no capability is deleted until it has been written down as ported,
already covered, or deliberately dropped, with a reason.** That rule is the
reason `CAPABILITY_RESCUE_MAP.md` exists at all, and deleting it now would
undo the thing it was written to prevent.

The code itself is not here. It is in git history and on the two archive
branches, named in the table above.

# Lessons

**Historical.** Written in Phase 38 for the standalone `src/gmes` package
(facade, application seam, CLI surface - none of which exist in this
repository). That package was removed in HISTORY.md Phase 57; the flat
legacy engine at the repo root (`gmes_core.py` and friends) is the one
supported implementation now - see [CLAUDE.md](CLAUDE.md) section 0 and
[ARCHITECTURE.md](ARCHITECTURE.md). Lessons 1-4 and 10-11 still hold as
general principles and are restated, with current examples, in
[HISTORY.md](HISTORY.md)'s "Recurring lessons" section and
[CLAUDE.md](CLAUDE.md) section 3. Lessons 5, 7 and 9 describe package-only
architecture (a CLI "seam", a "facade") and do not apply to the flat
engine. Kept for the historical record, not as current guidance.

1. Enterprise browser automation fails silently; verify each observed outcome.
2. Poll for the specific required control; never use fixed sleeps.
3. Generated UI identifiers and rendered grid rows are not stable data APIs.
4. G-MES business policy must remain outside generic screen/query/export
   capabilities so a report-specific assumption cannot silently affect another
   screen.
5. A CLI stays shallow when it delegates all project behavior through one
   application seam; input validation may live behind that seam to keep it
   side-effect free.
6. Credential migration is state mutation and must be explicit, idempotent,
   copy-only, and separate from read-only diagnostics.
7. Package proof belongs near the first usable CLI surface, not after further
   migration work; `version` and command-help smoke checks require no browser.
8. Legacy scripts are evidence, not dead code: retain them until standalone
   read-only live verification proves parity.
9. A facade that hides imports but returns a live connection still leaks the
   runtime boundary. High-level application operations must own acquire/use/
   release and return typed results instead.
10. Live evidence must flush while an operation is running; a log written only
    on process exit cannot diagnose an authentication or CDP transition that
    stalls first.
11. The automation must use the established copy of the user’s Default Chrome
    profile, never the real profile and never an unverified replacement copy.

# ADR-0022: Adopt import-linter for DAG enforcement alongside the home-grown module_dag_lint

- **Status:** proposed
- **Date:** 2026-05-11
- **Deciders:** Sora (Tech Lead), Camille (Security/Compliance), persona round 2026-05-11
- **Tags:** ci, architecture, lint, tooling

## Context

Sora ADR-0002 + the home-grown lint at `tools/ci/module_dag_lint.py` enforce the L0 -> L1 -> L2 -> L3 -> L4 module DAG by walking AST `Import` / `ImportFrom` nodes and checking each project-internal edge against an allowlist table. The lint is ~150 LOC, AST-based, and has worked correctly through Sprint 1 (95+ tests pass, no false positives reported).

Sora's phase6 research (`phase6/sora-research.md` Q6) and Camille's phase6 research (`phase6/camille-uncertainties.md` U7) both surfaced limitations of the home-grown approach:

1. It cannot express **Forbidden** contracts -- a rule of the form "package A must never import package B" (Camille's example: `db.evidence` must never import from `db.sock` per the ADR-0001 isolation rule, currently enforced by a separate file-level lint).
2. It cannot express **Independence** contracts -- "these N peers must not import each other" -- which would be useful for the adapter wrappers if we ever bring them into the import graph.
3. It is a custom artifact that any new contributor must learn; `import-linter` (1k+ stars on GitHub, actively maintained at `github.com/seddonym/import-linter`) is the standard tool for this job and is pre-commit-compatible out of the box.

A full retire-and-replace of the home-grown lint in a single PR is *not* Kaizen-faithful: the home-grown lint works, and the safest test for a replacement tool is to run it in parallel and observe agreement before retiring the original.

## Decision

We will adopt `import-linter>=2.0` (installed as a dev-group dependency) and run it **alongside** `tools/ci/module_dag_lint.py` for at least one sprint. Configuration lives at `.importlinter` at the workspace root. The single active contract is the **Layered** contract mirroring the home-grown lint's allowlist table (L0 -> L1 -> L2 -> L3 -> L4 with sibling-non-import on each layer).

Both lints fire on every commit via separate `pre-commit` hooks (`module-dag-lint` and `import-linter-layered`). Any disagreement between the two surfaces a real bug in one of them and is logged for review. After a full sprint of parallel agreement, the home-grown lint may be retired in a follow-up PR (out of scope for this ADR).

Two contracts called out by Sora as future enforcement targets are explicitly deferred and documented inline in `.importlinter`:

- **Independence** across the 11 AGPL adapter wrappers at `adapters/<id>/wrapper.py` -- deferred because those wrappers are NOT imported as Python modules; they are invoked as standalone subprocess scripts and don't appear in the import graph. The AGPL containment lint at `tools/ci/agpl_import_lint.py` covers this surface directly via path-exempt AST inspection.
- **Forbidden** `db.evidence` <-> `db.sock` -- deferred until `db.sock` and `db.evidence` are split into separate submodules under `osint_goblin_db` (M1 follow-up).

## Consequences

- **Positive:** New contributors recognize `import-linter` immediately; CI gains pre-commit integration; configuration is declarative rather than imperative AST code; future Forbidden/Independence contracts have a home.
- **Positive:** Parallel-run validation is cheap (lint-imports adds ~3-5s to the pre-commit pipeline on a 26-file project) and surfaces disagreement before we commit to retiring the home-grown lint.
- **Negative:** Adds three new transitive deps to the dev group (`import-linter`, `grimp`, `rich`). All actively maintained.
- **Negative:** Two lints means two potential places for a future contributor to update when a new package is added to the workspace. Acceptable during the parallel-run sprint; not acceptable long-term.
- **Neutral:** Retirement of `tools/ci/module_dag_lint.py` is gated on a full sprint of parallel-run agreement. Will be a separate ADR amendment.

## References

- `phase6/sora-research.md` Q6 (proposed ADR-0022; full reasoning)
- `phase6/sora-uncertainties.md` P1 -- import-linter for DAG enforcement
- `phase6/camille-uncertainties.md` P1-U7 -- AGPL lint dynamic-import blindspot (related; addressed in Kaizen #4 commit)
- `github.com/seddonym/import-linter` (the tool itself)
- ADR-0001 (monorepo sock-evidence isolation -- the Forbidden contract this defers)
- ADR-0002 (Scrapling fetch primitive -- L1 layer member, exemplifies a typical DAG edge)
- `tools/ci/module_dag_lint.py` (the home-grown lint that this runs alongside)
- `.importlinter` (the new config file shipped in this commit)

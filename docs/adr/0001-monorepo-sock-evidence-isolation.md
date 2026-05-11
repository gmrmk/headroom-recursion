# ADR-0001: Monorepo with physical sock-evidence isolation

- **Status:** accepted
- **Date:** 2026-05-10
- **Deciders:** architect (Iris), backend (Diego), security (Camille), investigator (Tomás)
- **Tags:** repo, layout, isolation, ci

## Context

OSINT GOBLIN spans a FastAPI service, a Dramatiq worker pool, a Next.js shell, 12+ tool adapters (some AGPL subprocesses), a Postgres schema, a MinIO posture, a SECURITY.md, an ADR set, and ops scripts. Three-repo splits (`api+worker`, `web`, `adapters`) were considered; the investigator persona's defensibility requirement (`phase3/06-osint-investigator.md` §8) and the security persona's structural-isolation rule (`phase3/05-security-compliance.md` §1.1 surface S3) both demand that sock-account state and evidence state never share an import path, never share a Postgres database, and never share a backup tier. A polyrepo split fragments the CI lint surface that enforces this; a monorepo concentrates it.

The investigator's `architect-roadmap.md` predecessor proposed three-pane layout, the data-roadmap proposed one-Postgres-at-the-centre with audit-split deferred to M2, and the Phase 3 backend chose a single `tool_runner.send(...)` actor (`phase3/04-backend-data-engineer.md` §B7). All three are easier to evolve in one tree.

## Decision

We adopt a **monorepo** with the following top-level layout, enforced by CI lint:

```
osint-goblin/
├── api/                 FastAPI routers, Pydantic models
├── worker/              Dramatiq actors, evidence pipeline
├── web/                 Next.js 15 App Router shell
├── adapters/            12+ tool adapters (subprocess shells for AGPL)
├── evidence/            verify.py, WARC writer, signing primitives
├── db/
│   ├── evidence.py      evidence-DB models, migrations
│   └── sock.py          sock-account-DB models, migrations
├── ops/                 Compose, scripts, lockfiles, IaC
├── docs/                Diátaxis docs + ADR set
├── scripts/             bootstrap, dev-up, smoke, bench
└── .ci/                 lint-agpl-imports.py, lint-sock-cross-import.py, …
```

Sock-account state lives in a **separate Postgres database** (`osint_sockaccounts`), with separate credentials, separate pool, separate backup tier. The CI lint rule `lint-sock-cross-import` rejects any `import` between `db/evidence.*` and `db/sock.*`. The CI lint rule `lint-agpl-imports` rejects in-process imports of AGPL modules anywhere in the dashboard tree (see ADR-0006).

## Consequences

- **Positive.** One PR can land an architecture change end-to-end (model + actor + UI + docs + ADR). CI lint enforces isolation in one place. Bisects are linear. New contributors clone once.
- **Positive.** ADR back-reference check is feasible — a single grep against `ARCHITECTURE.md` covers the tree.
- **Negative.** CI matrix is broader (Python + Node + Docker). Mitigated by job parallelism; baseline CI completes in <12 min.
- **Negative.** Versioning is per-repo, not per-module. The dashboard ships as one artifact; sub-package release autonomy is forfeit. Acceptable for a single-product codebase.
- **Neutral.** Splitting later is feasible (git filter-repo per directory). The decision is reversible.

## References

- `INTEGRATION-SPEC.md` §9 (sock-account isolation, structural not procedural)
- `CONSOLIDATED-ROADMAP.md` §1 (stack picks)
- `phase3/05-security-compliance.md` §1.1 surface S3
- `phase3/06-osint-investigator.md` §8 (sock-binding-per-run)
- `phase3/04-backend-data-engineer.md` §9 (separate-DB architecture)
- ADR-0006 (AGPL containment lives in this tree)

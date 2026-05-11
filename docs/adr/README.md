# Architectural Decision Records

This directory is the chronological record of every load-bearing decision in OSINT GOBLIN. Decisions live forever; supersession is by writing a new ADR, never by deleting an old one. The template is [`0000-adr-template.md`](0000-adr-template.md).

## Index

| # | Title | Status | Date |
|---|---|---|---|
| 0001 | [Monorepo with physical sock-evidence isolation](0001-monorepo-sock-evidence-isolation.md) | accepted | 2026-05-10 |
| 0002 | [Scrapling as the single fetch primitive](0002-scrapling-fetch-primitive.md) | accepted | 2026-05-10 |
| 0003 | [Dramatiq + Redis with single tool_runner actor](0003-dramatiq-redis-tool-runner.md) | accepted | 2026-05-10 |
| 0004 | [followthemoney entity model with thin extensions](0004-followthemoney-entity-model.md) | accepted | 2026-05-10 |
| 0005 | [Postgres + Apache AGE + pgvector at the centre](0005-postgres-age-pgvector.md) | accepted | 2026-05-10 |
| 0006 | [AGPL §13 subprocess containment](0006-agpl-subprocess-containment.md) | accepted | 2026-05-10 |
| 0007 | [Six verbs including Attest; Compare deferred to M2](0007-six-verbs-attest.md) | accepted | 2026-05-10 |
| 0008 | [Evidence package ships in M1, not M2](0008-evidence-package-m1.md) | accepted | 2026-05-10 |

## How to add an ADR

1. Reserve the next number in the table above (PR that only adds the row is fine).
2. Copy `0000-adr-template.md` to `NNNN-kebab-title.md` and fill in.
3. Open a PR. CI runs `adr-back-reference-check` — your ADR must be referenced from at least one section of `ARCHITECTURE.md`.
4. At least one maintainer plus the persona-owner whose surface is touched must approve. Security ADRs need security-WG sign-off. Data-model ADRs need data-architect sign-off.

## How supersession works

When a decision is reversed, the new ADR's Status names the old one. The old ADR's Status is appended to `superseded by ADR-MMMM` (not edited in place above the Date line). The old ADR remains the record of why we did the wrong thing for six months and what we learned.

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
| 0009 | [Fetch-tier cost-weighted governor](0009-fetch-tier-cost-weighted-governor.md) | accepted | 2026-05-10 |
| 0010 | [Sock-account encrypted DB separation + Shamir passphrase recovery](0010-sock-account-encrypted-db-separation.md) | accepted | 2026-05-10 |
| 0011 | [Embedded CAPTCHA tab inside the dashboard right-rail](0011-embedded-captcha-tab.md) | accepted | 2026-05-10 |
| 0012 | [Bounded reaffirmation chain for lawful-basis attestation](0012-bounded-reaffirmation-chain.md) | accepted | 2026-05-10 |
| 0013 | [Tier-aware loading shapes with defensibility copy](0013-tier-aware-loading-shapes.md) | accepted | 2026-05-10 |
| 0014 | [OPSEC tile HoverCard with rich-data drill-down](0014-opsec-tile-hovercard-rich-data.md) | accepted | 2026-05-10 |
| 0015 | [Real-name-leak full Sheet + 35% dim + action freeze](0015-real-name-leak-sheet-dim-freeze.md) | accepted | 2026-05-10 |
| 0016 | [followthemoney `properties_ext` JSONB escape hatch + upstream-promotion path](0016-followthemoney-properties-ext-escape-hatch.md) | accepted | 2026-05-10 |
| 0017 | [cmd-K command palette specification](0017-cmd-k-palette-spec.md) | accepted | 2026-05-10 |
| 0018 | [Defer the `osint_goblin_evidence_pipeline` split until measurement-gated](0018-evidence-pipeline-split-deferral.md) | accepted | 2026-05-11 |
| 0022 | [Adopt import-linter for DAG enforcement alongside home-grown module_dag_lint](0022-import-linter-dag-enforcement.md) | proposed | 2026-05-11 |

**Reserved (not yet authored)** — placeholders for the remaining Sora-proposed ADRs in phase6 (numbers reserved to keep authorship order coherent with the research dispatch):

| # | Title | Status | Reserved |
|---|---|---|---|
| 0019 | Per-adapter venv isolation (AGPL + conflict-prone) | reserved — P2 under pivot per R-4/Sora | 2026-05-11 |
| 0020 | Three-class actor split (fast / slow / streaming) | reserved — P0 distribution-independent | 2026-05-11 |
| 0021 | Versioned adapter-event schema (adapter_events.schema.json v1) | reserved — P0 Sprint 2 per Sora pivot | 2026-05-11 |

## How to add an ADR

1. Reserve the next number in the table above (PR that only adds the row is fine).
2. Copy `0000-adr-template.md` to `NNNN-kebab-title.md` and fill in.
3. Open a PR. CI runs `adr-back-reference-check` — your ADR must be referenced from at least one section of `ARCHITECTURE.md`.
4. At least one maintainer plus the persona-owner whose surface is touched must approve. Security ADRs need security-WG sign-off. Data-model ADRs need data-architect sign-off.

## Template requirements (updated 2026-05-11 per phase6)

Every ADR MUST include these six sections, none of them optional:

- **Context** — what forces brought us here
- **Decision** — what we will do (imperative)
- **Alternatives considered** — what we weighed and chose against, one-line reason per rejection (added 2026-05-11 per Aldous phase6 Q4)
- **Consequences** — Positive / Negative / Neutral
- **Gates that re-open this** — future-state conditions that should trigger revisiting (added 2026-05-11 per Aldous phase6 Q10)
- **References** — primary sources + sibling ADRs

## How supersession works

When a decision is reversed, the new ADR's Status names the old one. The old ADR's Status is appended to `superseded by ADR-MMMM` (not edited in place above the Date line). The old ADR remains the record of why we did the wrong thing for six months and what we learned.

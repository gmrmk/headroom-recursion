# ADR-0016: followthemoney `properties_ext` JSONB escape hatch + upstream-promotion path

- **Status:** accepted
- **Date:** 2026-05-10
- **Deciders:** data architect, backend (Diego), investigator (Tomás)
- **Tags:** data-model, ftm, schema-evolution, jsonb, ontology

## Context

ADR-0004 locks followthemoney (FtM) as the entity model with six thin extensions: `Alias`, `CryptoWallet`, `DorkQuery`, `PresenceClaim`, `MediaHash`, `Investigation`. FtM's strength is *type rigor* — every entity has a typed schema, validated on write. FtM's weakness for OSINT is *novelty velocity* — new entity types appear in the wild faster than upstream FtM can absorb them, and forking FtM to add types is the worst of both worlds (loss of upstream improvements, ontological drift).

The data-architect persona (`phase2/data-roadmap.md` §1.3) flagged this with a concrete example: a 2026 investigation might need to record a "Telegram channel boost" entity that FtM doesn't model. Two bad options exist:

- **Drop the data.** Investigators record it as a free-text annotation, losing typed query and graph affordances.
- **Fork FtM.** The dashboard's `followthemoney` becomes a private fork, drifts from upstream, and integration with Aleph/OCCRP becomes impossible.

The data-architect's third option — `properties_ext` JSONB outside the FtM validator — was accepted as the resolution (`CONSOLIDATED-ROADMAP.md` §4 data-architect adjustments). This ADR codifies the contract, the validation semantics, the query-affordance limits, and the upstream-promotion path so a 2027 maintainer doesn't reinvent the wheel.

A related concern: lawful-basis prompt copy divergence between `INTEGRATION-SPEC.md` §8 (tightened summary) and `phase3/05-security-compliance.md` §5.1.1 (comprehensive version). Both texts mention retention-period for embeddings, but only §5.1.1 explicitly names controller / recipient / Special-category basis fields. This is a documentation-source-of-truth issue parallel to the FtM-vs-properties_ext issue: the canonical text needs explicit ownership and a promotion path. The resolution lives in this ADR's References + a new how-to ownership rule.

## Decision

**`properties_ext` JSONB column.** Every FtM entity table (`evidence_entities`, `claim_entities`, etc.) carries a `properties_ext JSONB NOT NULL DEFAULT '{}'` column. The contract:

- Properties in `properties_ext` are **not** validated by the FtM schema validator. They are free-form key-value pairs typed by convention.
- A `properties_ext` key is namespaced by `<x_namespace>_<key>`, e.g. `x_telegram_boost_score`. The `x_` prefix is mandatory; non-prefixed keys are rejected at write time by a Pydantic validator in `packages/osint_goblin_ftm/models.py`.
- Each namespace is documented in `docs/reference/ftm-extensions/properties_ext.md`. Adding a new namespace requires a PR that updates the doc and includes a "promotion target" — the FtM upstream schema name the namespace aspires to become.
- Properties in `properties_ext` are queryable via Postgres JSONB operators (`@>`, `->>`, GIN index on the column). They are **not** indexed by `pg_trgm` or `tsvector` by default; if a namespace needs full-text or trigram search, it earns its own `GENERATED ALWAYS AS` column with the appropriate index.

**Upstream-promotion path.** A `properties_ext` namespace that stabilizes (used in production for ≥6 months, ≥3 investigators wanting it, ≥10 entities materialized) is a candidate for promotion to a FtM upstream type. The procedure:

1. File an FtM upstream issue with the namespace's properties, type hints, and example entities.
2. If accepted upstream: wait for the FtM release that includes the new type, then migrate via a forward-only Alembic migration (move `properties_ext.x_foo_*` keys into the new typed columns; delete the keys from `properties_ext`).
3. If rejected upstream: stabilize the namespace as a long-term private convention. Document the rejection rationale in the namespace's `docs/reference/ftm-extensions/properties_ext.md` entry.

**Lawful-basis copy source-of-truth.** The verbatim text in `phase3/05-security-compliance.md` §5.1.1 is the canonical lawful-basis copy. `INTEGRATION-SPEC.md` §8 is the *internal design summary* and is not the source for the modal. The how-to `docs/user/howto/attest-lawful-basis.md` ships the §5.1.1 version verbatim. M2 jurisdictional overlays (US/UK) extend §5.1.1 as named jurisdictional variants; legal review before M2 ships face-match. This resolution is owned by this ADR because the §5.1.1 vs §8 divergence is structurally the same as the `properties_ext` vs FtM-upstream tension — both are escape hatches with an upstream-promotion path.

## Consequences

- **Positive.** New OSINT entity types land in `properties_ext` in hours, not in an FtM upstream release cycle. The dashboard does not drop data while waiting for ontology consensus.
- **Positive.** Forking FtM is structurally avoided. Every `properties_ext` namespace is *opt-in*; upstream FtM remains the validator-trusted spine.
- **Positive.** Per-investigation FtM version pinning (ADR-0004 + `investigations.ftm_version`) composes cleanly. An investigation pinned to FtM 4.5 can still use `properties_ext` namespaces; the namespaces are validator-orthogonal.
- **Positive.** The lawful-basis source-of-truth resolution closes the docs divergence flagged in `phase4/06-docs.md` §13. The how-to becomes the canonical reader-surface.
- **Negative.** `properties_ext` is type-soft. A typo in `x_telegram_boost_score` vs `x_telegram_boost_scor` produces two distinct namespaces that don't merge. Mitigation: the namespace docs include a canonical-spelling registry; a CI lint script (`tools/ci/properties_ext_lint.py`) flags new namespaces that differ from existing ones by Levenshtein distance ≤ 2.
- **Negative.** Querying across `properties_ext` keys is slower than typed columns. Acceptable for OSINT workloads at M1 scale; the LanceDB swap criterion (ADR-0005) is the relief valve at M2 if measurement demands.
- **Neutral.** The upstream-promotion path is documented as procedure; whether any namespace successfully promotes upstream is a function of the FtM community's appetite. Not a load-bearing dependency.

## References

- `CONSOLIDATED-ROADMAP.md` §4 (data-architect adjustments)
- `INTEGRATION-SPEC.md` §8 (lawful-basis tightened summary — internal-design-only)
- `phase2/data-roadmap.md` §1.3 (the `properties_ext` proposal)
- `phase3/05-security-compliance.md` §5.1.1 (canonical lawful-basis copy)
- `phase4/06-docs.md` §13 (divergence flag)
- followthemoney upstream — `https://followthemoney.tech/`
- ADR-0004 (FtM entity model)
- ADR-0005 (Postgres + pgvector + JSONB)
- ADR-0012 (bounded reaffirmation chain — consumes the lawful-basis text)

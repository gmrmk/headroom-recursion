# ADR-0004: followthemoney entity model with thin extensions

- **Status:** accepted
- **Date:** 2026-05-10
- **Deciders:** data architect, backend, investigator
- **Tags:** data, schema, ontology

## Context

OSINT data is intrinsically heterogeneous: `Person`, `Company`, `BankAccount`, `Domain`, `Email`, `PhoneNumber`, `Document`, `Address`, plus the long tail (`crypto_wallet`, `alias`, `presence_claim`, `media_hash`, `dork_query`). Three options were considered:

1. **Roll our own.** Six months of ontology work; we re-invent the wheel that OCCRP already polished.
2. **Adopt followthemoney (FtM) as-is.** Mature, BSD-licensed, used in production by Aleph, OpenSanctions, and a long tail of investigative journalism. Covers ~80% of OSINT entity needs.
3. **Fork FtM.** The data-roadmap predecessor (`phase2/data-roadmap.md` §D-A2) flagged FtM rigidity for novel OSINT types and proposed a fork-with-our-extensions.

The fork path was rejected: maintaining a divergent FtM cripples our ability to interop with Aleph corpora and OpenSanctions feeds, both load-bearing data sources. The Phase 3 data work showed that all six missing OSINT types fit as **thin extensions** plus a `properties_ext` JSONB escape hatch.

## Decision

- **Adopt FtM as-is.** No fork. Pin `followthemoney>=3.7,<4` in `requirements.in`.
- **Six thin extensions** (all subclasses of existing FtM types, no new core types):
  - `Alias` — handle ↔ subject claim with confidence.
  - `CryptoWallet` — wallet address + chain + observed-at + provenance.
  - `DorkQuery` — Google-dork string + engine + result-set hash.
  - `PresenceClaim` — (subject, platform, tool, confidence) tuple; one row per (tool, platform) per subject.
  - `MediaHash` — PDQ / pHash / SHA256 of an image/video + perceptual-distance neighbours.
  - `Investigation` — case container; FtM-typed wrapper around `case_id`.
- **`properties_ext` JSONB column** on every entity row, outside FtM's validator. Novel OSINT property bags land here without breaking FtM round-trip. **Upstream-promotion path:** if a `properties_ext` field is used by ≥3 investigations and is stable for ≥90 days, we propose it to FtM upstream; if accepted, we migrate the JSONB into a typed property in a versioned migration.
- **`investigations.ftm_version` pin.** Each investigation records the FtM schema version at case-open. Downgrades within an investigation are forbidden by trigger (data integrity); upgrades are explicit (run `scripts/ftm-upgrade.py --case=<id>`). This catches the "the schema drifted under us mid-case" class of bug.

## Consequences

- **Positive.** Aleph and OpenSanctions interop out of the box. CSL-JSON citation export inherits FtM's `proof` semantics.
- **Positive.** Splink + FtM `compare()` for entity resolution work without translation. The probabilistic 0.4–0.7 band uses an LLM-as-judge tiebreaker.
- **Negative.** `properties_ext` is a semi-typed escape hatch. Schema drift risk is real. Mitigated by the upstream-promotion discipline and a quarterly "what's in properties_ext" report.
- **Negative.** Per-investigation FtM-version pinning costs a column and a migration discipline. Acceptable; the alternative is silent corruption on schema bumps.
- **Neutral.** If FtM upstream goes stale (it has not, but the data-roadmap risk-register §D-R3 notes the possibility), we have the property-bag fallback and the option to fork later. Reversible.

## References

- `INTEGRATION-SPEC.md` §1 (one Postgres at the centre)
- `CONSOLIDATED-ROADMAP.md` §1 (entity model decision)
- `phase2/data-roadmap.md` §1, §D-A2
- ADR-0005 (Postgres holds the FtM-typed entities)

# ADR-0028: Fingerprint primitive scope (entity-fingerprint vs dossier-fingerprint)

- **Status:** proposed
- **Date:** 2026-05-12
- **Deciders:** Sora (Tech Lead), Iris (Information Architect), Hideo (UX), persona round wave-4
- **Tags:** dossier, fingerprint, projection, primitive-scope, deferred-implementation

## Context

Two independent wave-4 deliberations named "fingerprint" as a load-bearing dossier primitive, and they meant different things. Both are useful; neither subsumes the other; shipping one as if it were the other creates a structural mistake. This ADR resolves the distinction explicitly so Sprint-5 W4-CLAIMS-BY-ENTITY (Iris-ratified keystone) ships against the right framing.

**Hideo's entity-fingerprint** (`hideo-wave4.md:222-226`, also §12 #1): a stable hash over the canonical-value pair `(asset_type, canonical_value)` that collapses multi-source evidence about the same real-world object — a property, an LLC, a named person, an address — under one row in the verdict surface. Findings that describe the same entity share a fingerprint; the projection groups them. This is the same primitive Sentry uses to collapse "Events" into "Issues."

**Feynman's dossier-fingerprint** (`feynman-wave4.md:Thread-3 §3.2`, Sentry-paradigm framing): a stable hash over the *whole dossier shape* — `hash(price_bucket, photo_bucket, review_bucket, host_bucket, identity_bucket)` — that lets each new vetting answer "this matches 7 prior cases, 4 confirmed fraud." The fingerprint is per-investigation, persisted to a SQLite store, and matched against prior closed investigations.

The convergence-of-independent-judgments is real: Hideo and Feynman, working in parallel substrates the same week, arrived at "fingerprint" as the right structural metaphor. Sora's wave-4 deliberation (`sora-deliberation-wave4.md:94-95`) is explicit on the distinction: *"These are different fingerprints. ... Both are useful, neither subsumes the other."* Iris ratifies Hideo's framing as the wave-4 keystone (`iris-deliberation-wave4.md:304-360`) and defers Feynman's framing on empty-day-one grounds (`iris-deliberation-wave4.md:296-302`): Feynman's own threshold is ≥500 vettings + a confirmed-fraud feedback loop, and the operator has run 5-15 to date.

## Decision

We will treat the two primitives as **distinct, separately scoped, and separately gated**:

1. **Entity-fingerprint primitive — ships Sprint-5 as the W4-CLAIMS-BY-ENTITY keystone.** Implementation: a new projection function `projectClaimsByEntity(events, existingFindings): Finding[]` in `apps/web/src/lib/dossier-shape.ts`, emitting Findings keyed by an `extractEntityFingerprint(finding)` helper that returns stable hashes of the form `"listing:abnb-42424"`, `"address:123 Main St, City"`, `"person:Jane Doe"`. New `SectionId` literal `"claims_by_entity"` placed in `SECTION_ORDER` after `identity` and before `behavior` (Iris's placement). The projection is non-breaking — no changes to the `Finding` shape, no new event types, no adapter changes. Iris's empty-day-one check passes: any investigation with ≥2 sources hitting the same entity emits at least one `claims_by_entity` Finding, and the geocode-match + listing-match co-occurrence on the same address is the common case.

2. **Dossier-fingerprint primitive — deferred until corpus + feedback loop exist.** Implementation deferred. Re-eval triggers (per Margaret §8 D3): corpus reaches ≥100 closed investigations AND the operator-feedback loop is wired (operator marks each closed investigation as confirmed-fraud / not-fraud). When triggers fire, the implementing ADR captures: the bucketing scheme per dimension (price, photo, review, host, identity), the SQLite store schema at `apps/api/src/osint_goblin_api/fingerprint_store.py` (or its successor location), the similarity metric over fingerprints, and the empty-state UX copy ("you've vetted N properties; pattern detection unlocks at ~50").

The naming convention going forward: **"entity-fingerprint"** for the per-real-world-object collapse primitive (Hideo's), **"dossier-fingerprint"** for the per-investigation pattern signature (Feynman's). Other naming variants ("similar_cases," "fingerprint match," "claims-by-entity") refer to the *surfaces* that consume these primitives, not the primitives themselves. ADRs, code, and roadmaps use the two-word qualified names to keep the distinction visible.

## Alternatives considered

- **Ship a single unified "fingerprint" primitive that covers both.** *Rejected because:* the hash inputs are categorically different (canonical-value pair vs. dimension-bucket tuple), the storage model is different (in-memory projection vs. persistent SQLite store), and the empty-day-one behaviour is different (entity-fingerprint works on investigation #1; dossier-fingerprint needs 50-500 prior). Forcing one primitive to absorb both pollutes both.
- **Defer both until the dossier-fingerprint corpus exists.** *Rejected because:* the entity-fingerprint primitive is non-breaking, empty-day-one safe, and Iris-ratified as the wave-4 keystone. Deferring it discards the wave-4 keystone for no architectural benefit.
- **Ship Feynman's dossier-fingerprint at smaller corpus thresholds with explicit "pre-calibration" UX framing.** *Rejected for now because:* statistically meaningless similarity scores degrade trust in the feature before it earns it; Feynman's own numbers say 500+ for reliable pattern matching. The empty-state UX cost is high; better to gate on the threshold.
- **Use the entity-fingerprint as the substrate for dossier-fingerprinting (compose dossier-fingerprint as a hash over the set of entity-fingerprints).** *Rejected as a default because:* it forecloses Feynman's dimension-bucket framing (which has independent academic grounding in the Sentry / FRAUDAR substrate). Listed here so the implementing ADR for dossier-fingerprint considers it as a composition option rather than rediscovering it.

## Consequences

- **Positive:** Sprint-5 W4-CLAIMS-BY-ENTITY has a written architectural anchor; the keystone ships as a clean 7th projection, not as a confused half-implementation of Feynman's deferred work.
- **Positive:** The dossier-fingerprint primitive has a named deferral with explicit re-eval triggers, not a silent omission. Margaret §8 D3 cites this ADR as the deferral home.
- **Positive:** Naming-convention discipline (entity-fingerprint vs. dossier-fingerprint, qualified) prevents the next round of personas from re-conflating the two primitives.
- **Negative:** Two primitives means two future ADRs (this one + the implementing ADR for dossier-fingerprint when triggers fire). Acceptable; the cleavage is real.
- **Negative:** The entity-fingerprint primitive ships with `extractEntityFingerprint(finding)` as a load-bearing helper, and the helper's correctness — does it canonicalize addresses consistently? person names? — is the structural risk. ADR-0028's implementation gate on W4-CLAIMS-BY-ENTITY routes through the W4-ENTITY-CANON precursor (Sprint-4, Sora + Iris-mandatory IA sign-off) for exactly this reason.
- **Neutral:** The "fingerprint" word now requires qualification in every future reference to avoid ambiguity. Acceptable lexical cost.

## Gates that re-open this

- **Gate 1 (dossier-fingerprint corpus):** When the operator's closed-investigation corpus reaches ≥100 entries AND the confirmed-fraud feedback loop is wired, write the implementing ADR for Feynman's dossier-fingerprint primitive. Re-eval trigger is direct from Margaret §8 D3.
- **Gate 2 (entity-canonicalization correctness):** When the W4-ENTITY-CANON precursor (Sprint-4) reveals that entity canonicalization is harder than the W4-CLAIMS-BY-ENTITY projection assumes (e.g., address canonicalization produces inconsistent hashes across adapter outputs), revisit the entity-fingerprint hash function in this ADR and amend.
- **Gate 3 (third fingerprint primitive proposed):** If a future wave proposes a fingerprint-shaped primitive distinct from both (e.g., a per-adapter fingerprint for cross-run replay invariants), extend this ADR's naming-convention section rather than minting a third loose "fingerprint" reference.
- **Gate 4 (claims-by-entity empty-state observed):** If W4-CLAIMS-BY-ENTITY ships and consistently emits zero entity-clusters on real investigations (i.e., investigations don't actually produce ≥2-source same-entity co-occurrences), revisit the ≥2-source emission threshold in the projection.

## References

- `phase6/wave4/hideo-wave4.md:222-226, §12 #1` — entity-fingerprint primitive (Hideo's framing)
- `phase6/wave4/feynman-wave4.md:Thread-3 §3.2` — dossier-fingerprint primitive (Feynman's framing)
- `phase6/wave4/sora-deliberation-wave4.md:90-108` — distinction between the two primitives
- `phase6/wave4/iris-deliberation-wave4.md:304-360` — Iris keystone ratification of entity-fingerprint
- `phase6/wave4/iris-deliberation-wave4.md:296-302` — Iris deferral of dossier-fingerprint
- `phase6/MARGARET-ROADMAP-2026-05-12-wave4.html` §3, §5, §8 D3 — Margaret refined-Branch-A arbitration; Sprint-5 W4-CLAIMS-BY-ENTITY card; D3 deferral
- ADR-0027 (proposed) — sibling deferred-implementation ADR on the verdict surface
- ADR-0026 (proposed) — rubric-coverage lint; W4-CLAIMS-BY-ENTITY emits a new `severity_basis: "matrix:PV_ENTITY_FINGERPRINT_MATCH"` literal that the lint will protect

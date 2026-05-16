# ADR-0027: Risk-score primitive scope + verdict-tier ladder reconciliation

- **Status:** proposed (doc-only this wave; implementation deferred)
- **Date:** 2026-05-12
- **Deciders:** Sora (Tech Lead), Iris (Information Architect), Tomás (Product), persona round wave-4
- **Tags:** dossier, risk-score, verdict, primitive-scope, deferred-implementation

## Context

Two wave-4 proposals converge on the same dossier surface from opposite directions and need explicit reconciliation before either ships.

**Feynman's `summary.risk_score`** (`feynman-wave4.md:276-282`, named in his Thread 2 §2.5): a top-level dossier `summary` section carrying `{risk_score, risk_label, top_anomalies, pattern_match}`. The `risk_score` is a TTP-weighted scalar derived from the populated section data — each finding contributes a weight per its TTP (tactic-technique-procedure) classification, and the section is the Tier-1 view in Hideo's three-tier progressive disclosure (`hideo-wave4.md:§12 #3`). Sora's wave-4 deliberation flagged this as "ADR-grade because the risk-score primitive becomes the headline UI feature once shipped" (`sora-deliberation-wave4.md:42`).

**Tomás's verdict-tier ladder** (`tomas-wave4.md:188-189`): a Yellow-scrape / Two-source / Multi-primitive label adjacent to the existing six-bucket verdict (the `verdict.ts` synthesis). Tomás's framing is *forensic-defensibility tier* — how strongly we conclude, orthogonal to *what we conclude*.

**Iris rejected Feynman's top-level `summary` section outright** (`iris-deliberation-wave4.md:209-213`): the proposed section overlaps 80% with the existing `VerdictBanner` + `verdict.ts` six-bucket synthesis. Shipping it as a peer section creates two surfaces showing the same conclusion differently — exactly the IA failure mode the wave-3 doctrine on `dossier-as-spine` was designed to prevent. Iris's parallel verdict on Tomás's ladder (`iris-deliberation-wave4.md:386-390`): **accept as Finding-decoration on the existing verdict, not as a new section.** The ladder is a label on the overall verdict (assurance tier), not a bucket of findings — it maps onto `verdict.ts`'s six buckets as an orthogonal axis.

The structural question this ADR resolves: **a scalar `risk_score` and a tier-ladder label are not the same primitive, and the decision to ship one, the other, both, or neither is load-bearing for the next year of dossier UX.** Neither lands this wave. The ADR captures the framing so Sprint-5 and beyond route against ratified intent rather than re-litigating each surface ad hoc.

## Decision

We will ship this ADR as **doc-only in wave-4; implementation deferred.** The decision body records the framing and the four open architectural questions that any implementing ADR must resolve before code lands.

The four open questions:

1. **Deterministic or probabilistic?** Feynman's TTP-weighted-sum is deterministic given the finding set. A probabilistic alternative (logistic-regression over TTP indicators against a labeled outcome corpus, or a Bayesian aggregator over per-finding confidence intervals) requires labeled-outcome corpus the operator does not have at personal-use scope. **Default position pending implementation:** deterministic, with explicit non-claim that it is a probability. A future amendment introduces probabilistic framing once a labeled corpus exists (≥50 closed investigations with confirmed/refuted outcomes).
2. **Input dimensions and weights.** What enters the score: TTP class? Severity tier? Source diversity? Recency? Each dimension is a per-weight justification that needs documentation. The wave-4 doctrine — every load-bearing weight has a one-line rationale citing the source — is the framing this resolves at implementation time.
3. **Interaction with the verdict-tier ladder.** Tomás's ladder is an assurance axis; Feynman's score is a magnitude axis. They are orthogonal in principle but the rendered surface (the `VerdictBanner`) has finite real estate. The implementing ADR decides: do they render as two pills on the same banner (score + tier), as a 2D matrix (score-tier grid cell), or as a single composite label (e.g., "HIGH risk, two-source assurance")?
4. **Calibration validation.** Before the score becomes the headline UI feature, what evidence convinces us the weights are calibrated rather than vibes-based? Candidate: hand-curated re-vetting of the operator's first 20 closed investigations, comparing score against post-hoc operator judgment. Pre-decision on validation methodology is part of the implementing ADR.

Iris's `summary`-rejection holds: **no top-level `summary` section.** If a risk-score primitive ships, it ships as part of `VerdictBanner` (Iris-ratified surface) and as a Finding-decoration axis, not as a new dossier section. Tomás's tier ladder is the same: VerdictBanner adjacency.

## Alternatives considered

- **Ship Feynman's `summary` section as proposed.** *Rejected because:* duplicates `VerdictBanner` + `verdict.ts` at 80% overlap; Iris's wave-4 IA verdict is direct on this (`iris-deliberation-wave4.md:213`).
- **Ship only Tomás's verdict-tier ladder and defer the scalar score indefinitely.** *Rejected for now because:* Feynman's TTP-weighted scalar is the headline-UI candidate per Hideo's three-tier progressive-disclosure framing; deferring it forever forecloses a design direction without explicit cause. Better to defer with criteria.
- **Ship a hand-rolled scalar score without an ADR.** *Rejected because:* the four open architectural questions are real and the surface is too prominent for vibes-based weight choice. Headline UI feature = ADR-grade decision.
- **Combine Feynman + Tomás into a single composite "risk tier" label with no scalar.** *Rejected as a default because:* it forecloses option 4 above (the implementing ADR's design space) without empirical evidence that the composite carries the same information. Listed here so the implementing ADR considers it explicitly rather than rediscovering it.

## Consequences

- **Positive:** Sprint-5 work routes against this ADR. The W4-CLAIMS-BY-ENTITY + W4-TIMELINE keystone ships without contaminating either surface with a half-baked risk-score primitive.
- **Positive:** The four open questions are explicit and citeable. The implementing ADR (likely ADR-0030+ once corpus + UX research land) has a written predecessor naming the structural axes it must address.
- **Positive:** Iris's IA-spine doctrine is preserved. No two-surface drift on the verdict.
- **Negative:** The wave-4 work ships without a risk-score primitive, which means Feynman's TTP-research stays unmonetized in the UI for at least one more wave.
- **Negative:** The verdict-tier ladder is also deferred behind this ADR (consequence of Iris's "adjacent to VerdictBanner" placement decision being design-dependent on the score question). Tomás's framing lives in this ADR as documentation, not as shipped UX.
- **Neutral:** Future design decisions on this surface (probabilistic framing, calibration methodology, 2D-matrix vs composite-label rendering) are now scoped problems with a written predecessor.

## Gates that re-open this

- **Gate 1 (corpus accrual):** When the operator's closed-investigation corpus reaches ≥20 entries with operator-confirmed outcomes, run the calibration-validation exercise and write the implementing ADR.
- **Gate 2 (UX research):** When user research (or operator self-research at single-user scope) produces evidence that the verdict-tier ladder is needed independent of a scalar score, ship Tomás's ladder under a thin amendment to this ADR rather than waiting on the full risk-score primitive.
- **Gate 3 (Verdict-banner real-estate pressure):** When other proposed VerdictBanner additions (e.g., the W4-AI-SCHEMA orange-pill annotation in Sprint-1.5) start crowding the surface, revisit the rendering-decision in Q3 before adding more.
- **Gate 4 (M2 milestone entry):** Regardless of corpus state, revisit at M2. M2 is the first milestone where dossier production traffic exists; user evidence dominates first-principles arguments at that point.

## References

- `phase6/wave4/feynman-wave4.md:276-282, §Thread-2.5` — TTP-weighted risk-score framing
- `phase6/wave4/iris-deliberation-wave4.md:209-213, 386-390` — Iris verdict on `summary` section + verdict-tier ladder placement
- `phase6/wave4/sora-deliberation-wave4.md:36-46` — Sora wave-4 ADR-grade flag on risk-score primitive
- `phase6/wave4/tomas-wave4.md:188-189` — verdict-tier ladder proposal
- `phase6/MARGARET-ROADMAP-2026-05-12-wave4.html` §7 — ADR-0027 doc-only this wave
- `apps/web/src/components/verdict-banner.tsx` — the surface this ADR protects from drift
- `apps/web/src/lib/verdict.ts` — the existing six-bucket synthesis the risk-score must reconcile with
- ADR-0028 (proposed) — fingerprint primitive scope; sibling deferred-implementation ADR

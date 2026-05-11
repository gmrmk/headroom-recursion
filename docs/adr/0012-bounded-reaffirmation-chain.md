# ADR-0012: Bounded reaffirmation chain for lawful-basis attestation

- **Status:** accepted
- **Date:** 2026-05-10
- **Deciders:** security (Camille), investigator (Tomás), interaction (Hideo)
- **Tags:** compliance, gdpr, ai-act, ux, biometrics

## Context

EU AI Act 2026 Annex III + GDPR Art.6/9 require a documented lawful basis for biometric processing of natural persons. The original design (Phase 2 architect roadmap) gated every face match behind a full retype of the lawful-basis prompt. The investigator persona (`phase3/06-osint-investigator.md` §10.F) flagged this as *compliance-counterproductive*: an investigator at hour 3 doing the fifth face match of the afternoon will paste-buffer the phrase, stop reading the prompt, and the attestation becomes a ritual signature rather than an informed act. The Italian Garante v. Clearview AI (€20M, March 2022) and ICO v. Clearview (£7.5M, May 2022) precedents both hinge on whether the controller's process actually constituted *informed* lawful-basis determination; a ritual-signature flow fails that test as surely as no flow at all.

Camille INV-S1 (`phase3/05-security-compliance.md` §5.1.4) accepted Tomás's pushback and proposed the bounded-reaffirmation synthesis. ADR-0007 (Attest as verb) is the surface; this ADR is the chain semantics.

## Decision

Lawful-basis attestation is **once-per-investigation full retype + bounded reaffirmation chain**:

1. **Per-investigation full retype.** At the start of an investigation that will touch biometric data, the investigator types the literal lawful-basis phrase (the verbatim copy from `docs/user/howto/attest-lawful-basis.md`, owned by Camille's §5.1.1). This produces a `LawfulBasisAttestation` chain artifact with `event_type=attest`.
2. **Subsequent biometric matches within the bound** require a signed reaffirmation, **not** a full retype. The reaffirmation is a one-keystroke acknowledgment that the original attestation still holds; the investigator presses `A` (the Attest verb keystroke) and a ≤500 ms keystroke-confirmed reaffirmation event is signed and chained (`event_type=reaffirm`).
3. **The bound** is the *first* of: 20 reaffirmations OR 24 hours since the last full retype, whichever comes first. After the bound is exceeded the dashboard refuses the next biometric match and prompts for a full retype.
4. **No cookie-bound or silent "remember my answer."** Every reaffirmation walks the chain. If a reaffirmation event is missing from the export, the chain-walk emits a P0 alert and `verify.py` returns exit-1 (yellow) until the gap is resolved.
5. **Every reaffirmation is in the evidence package** — `attestations/reaffirm_<m>.json` + `attestations/reaffirm_<m>.sig` (ADR-0008 zip layout).

Cross-case scope. `LawfulBasisAttestation.scope` is an enum: `single_investigation` (M1 default), `cross_case_intra_org_reference_set` (M3 gate, requires DPO sign-off + RBAC role + controller-identity match across cases). M2 introduces no new scope; the M2 work is the jurisdictional-overlay copy (ADR-0016), not a scope change.

The prompt copy itself lives in `phase3/05-security-compliance.md` §5.1.1 and is reproduced verbatim in `docs/user/howto/attest-lawful-basis.md`. **This ADR does not own the copy** — see ADR-0016 for the source-of-truth resolution between the §5.1.1 version and the `INTEGRATION-SPEC.md` §8 tightened summary.

## Consequences

- **Positive.** Compliance flow is informed at the moments that matter (case start, bound exceeded) and frictionless in the middle (reaffirmation keystroke). The legal-defense argument is stronger than ritual-signature because the chain walks every reaffirmation.
- **Positive.** The 20-or-24h bound is empirically grounded: 20 face matches in <24 h is the upper end of a single high-throughput case; beyond that the investigator should be re-reading the prompt anyway.
- **Positive.** The chain-walk failure-mode is well-defined: a missing reaffirmation is a P0 alert, not a silent compliance gap. Defense counsel's cross-examination has a yes/no answer.
- **Negative.** The bound numbers (20, 24h) are policy, not derivation. Jurisdictions may demand tighter bounds — the M2 jurisdictional-overlay work (ADR-0016) carries the legal review for that. M1 ships with the EU-default bound.
- **Negative.** The `event_type` enum gains a new value (`reaffirm`) compared to the v1 schema; an enum migration is required in `db/evidence.py` (Alembic) before M1 ships. Captured in WI-0501 in the Sprint 5 backlog.
- **Neutral.** The keystroke for reaffirmation (`A`) collides visually with the keystroke for full Attest (also `A`). The disambiguation is contextual: outside an active investigation `A` opens the full-retype prompt; inside an active investigation with an unexpired attestation `A` produces a reaffirmation. Documented in `docs/reference/keyboard.md`.

## References

- `INTEGRATION-SPEC.md` §8 (the bounded-reaffirmation synthesis)
- `MANUFACTURING-PLAN.md` §2.1 (M2 jurisdictional-overlay scope)
- `phase3/05-security-compliance.md` §5.1.4 (INV-S1)
- `phase3/06-osint-investigator.md` §10.F (compliance-counterproductive argument)
- `phase3/02-interaction-designer.md` §1 (Attest as verb, motion language)
- Italian Garante decision against Clearview AI, 10 March 2022 (€20M)
- ICO MPN against Clearview AI Inc., 18 May 2022 (£7.5M)
- ADR-0007 (Attest as verb)
- ADR-0008 (chain artifact + evidence-package layout)
- ADR-0016 (jurisdictional overlay framework)

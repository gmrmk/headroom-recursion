# ADR-0029: Doctrine-pivot framing (signal-correlation engine)

- **Status:** proposed (user-decision card; no code commitment)
- **Date:** 2026-05-12
- **Deciders:** Sora (Tech Lead) + Tomás (Product) + Iris (Information Architect); user-decision required by Sprint-4 close
- **Tags:** doctrine, framing, product-direction, user-decision

## Context

OSINT GOBLIN's working doctrine through wave-3 has been **"evidence-renderer"**: the dossier collects evidence from adapters, the projection layer organizes it into sections, the verdict surface synthesizes a six-bucket conclusion, and the operator reads the assembled artifact. The dossier-as-spine principle and Iris's wave-3 IA discipline both anchor in this framing — the system's job is to *render* the evidence the adapters produce.

Wave-4 surfaced a recurring observation across two independent personas that the working doctrine may be undersized for the product the codebase is becoming. Feynman's wave-4 deliberation framed the dossier-fingerprint and BubbleUp primitives as moves toward a **"signal-correlation engine"** — a system whose job is not just to render evidence but to *cross-correlate signals* (across investigations, across entities, across time-windows) and present the correlations as first-class outputs. Hideo's wave-4 work arrived at the same framing from the UX side, naming **"progressive disclosure"** (three-tier: risk summary → forensic timeline + section cards → full detail) as the right reading mode for a correlation-engine output, distinct from the flat-dossier read mode an evidence-renderer affords.

Margaret's wave-4 roadmap (`MARGARET-ROADMAP-2026-05-12-wave4.html` §3) captures this as **MS-DOCTRINE-FORK** and is direct that **refined-Branch-A** (the Sprint-5 keystone trio: W4-CLAIMS-BY-ENTITY + W4-TIMELINE + W4-EVICT, plus the Iris-ratified non-breaking projector additions) **does not commit a doctrine pivot.** The pivot remains an open question; this ADR is the place to resolve it explicitly rather than letting it land by accumulation.

The structural risk of leaving the framing implicit: every wave's persona round will re-litigate what kind of product OSINT GOBLIN is, and each re-litigation costs persona-cycles and risks divergent design decisions across surfaces. The structural risk of pivoting prematurely: a doctrine pivot reorders the M1/M2 milestone work and re-prioritizes adapter investment toward correlation substrate; doing this without user assent is exactly the kind of architectural overreach CLAUDE.md's verification-before-done principle was written to prevent.

## Decision

This ADR ships as a **user-decision card** with no code commitment. The user picks one of three options enumerated below by Sprint-4 close. Sprint-5 routes against the picked option; default (if the user defers) is Option 1 per Margaret's refined-Branch-A arbitration.

**Option 1 — Refined-Branch-A as default doctrine.** Ship the Iris-ratified Sprint-5 keystone (W4-CLAIMS-BY-ENTITY entity-fingerprint projection + W4-TIMELINE + W4-EVICT) as Branch-A defines it, without renaming or reframing the working doctrine. The system remains the evidence-renderer; the wave-4 keystone adds two non-breaking projections that strengthen the spine. Doctrine-pivot is not committed; the framing question is parked. **Re-evaluated wave-by-wave; not closed.**

**Option 2 — Explicit doctrine pivot to signal-correlation engine.** Adopt the signal-correlation framing as the working doctrine. M1/M2 milestone work re-prioritizes toward correlation substrate (dossier-fingerprint store from ADR-0028 promotes from deferred to in-flight; BubbleUp baseline-source adapter from wave-3 MS-4 promotes; progressive-disclosure UX research becomes a load-bearing input to Sprint-5+ design). The wave-4 keystone (W4-CLAIMS-BY-ENTITY + W4-TIMELINE) still ships, but framed as the first installment of a signal-correlation engine rather than as non-breaking projector additions.

**Option 3 — Defer with explicit re-eval criteria.** Park the doctrine question for one more wave. Re-eval triggers documented now: (a) the operator's closed-investigation corpus reaches ≥30 entries, providing empirical ground for the correlation-engine framing; (b) one additional persona round (wave-5) is run with the explicit framing question on the table; (c) one user-experience checkpoint at Sprint-5 close (does the W4-CLAIMS-BY-ENTITY shipped projection feel like a correlation primitive or like a render primitive?). If two of three trigger, the doctrine fork re-opens.

**Whichever option the user picks, the W4-CLAIMS-BY-ENTITY entity-fingerprint primitive (ADR-0028) and the rubric-coverage lint (ADR-0026) ship Sprint-4/5 unchanged.** Those are architectural decisions independent of the doctrine framing. This ADR's purpose is to surface the framing question, not to gate the keystone work.

## Alternatives considered

- **Ship the doctrine pivot silently by accumulation (let each wave's keystone implicitly redefine the doctrine).** *Rejected because:* this is the failure mode the ADR exists to prevent. Doctrine drift compounds; the cost of explicit framing is a single user-decision card, and the cost of implicit drift is paid across every future persona round.
- **Mandate Option 2 as the architecturally-correct call without user input.** *Rejected because:* a doctrine pivot is a product-direction decision, not a tech-lead decision. CLAUDE.md's verification-before-done principle frames this — Sora + Tomás + Iris can identify the fork, but the call is the user's.
- **Drop Option 3 (defer with criteria) and force a binary pick now.** *Rejected because:* Margaret's roadmap §3 explicitly lists "defer with explicit criteria" as one of three named options. Foreclosing it before the user sees it is overreach.
- **Frame the pivot as already accomplished (Branch-B framing — defer the keystone, frame the wave as hardening).** *Rejected because:* Branch-B was Marcus's wave-4 framing and Margaret arbitrated against it in §3 on the strength of convergence-of-independent-judgments (Hideo + Feynman + Iris all named the entity-fingerprint primitive independently). Re-litigating that arbitration belongs in a future ADR amendment, not in this one's alternatives.

## Consequences

- **Positive:** The doctrine framing question is surfaced explicitly and ADR-cited. Future persona rounds can route against a written decision (or a written deferral) rather than re-litigating the framing from scratch.
- **Positive:** The keystone work (W4-CLAIMS-BY-ENTITY + W4-TIMELINE) is not gated on the framing decision. Sprint-5 ships regardless.
- **Positive:** Option 3's re-eval criteria mean a deferral is not infinite — there are explicit re-open conditions.
- **Negative:** The ADR is a user-decision card, which means a Sora/Tomás/Iris draft is incomplete until the user signs off. The Sprint-4 close deadline is the gate.
- **Negative:** If the user picks Option 2 (explicit pivot), the M1/M2 milestone roadmap needs a re-write to re-order correlation-substrate work. Not free.
- **Negative:** If the user picks Option 3 (defer), one more wave of doctrine ambiguity is the cost. Mitigated by Option 3's explicit re-eval criteria.
- **Neutral:** Option 1 is the no-op-keep-shipping default. Status quo doctrine survives unchanged; the question is just parked rather than answered.

## Gates that re-open this

- **Gate 1 (Sprint-4 close):** Hard deadline. The user-decision is made by Sprint-4 close, or Option 1 ratifies by default per Margaret's arbitration.
- **Gate 2 (corpus accrual):** When the operator's closed-investigation corpus reaches ≥30 entries, regardless of which option was picked, revisit the framing with empirical evidence on whether the system's outputs feel like correlations or like rendered evidence.
- **Gate 3 (Wave-5 persona round):** If the user picks Option 3, the wave-5 round MUST include the doctrine-framing question as an explicit input. The deferral does not survive a wave silently.
- **Gate 4 (third independent signal-correlation framing):** If a third persona, working in a third independent substrate, names "signal-correlation engine" or a synonym in wave-5, that is sufficient evidence to re-open the framing question even if Gate 1 closed against pivot.

## References

- `phase6/wave4/feynman-wave4.md` — signal-correlation framing (Feynman's substrate)
- `phase6/wave4/hideo-wave4.md` — progressive-disclosure UX framing (Hideo's substrate)
- `phase6/MARGARET-ROADMAP-2026-05-12-wave4.html` §2-§3 — refined-Branch-A arbitration; MS-DOCTRINE-FORK as user-decision card
- `phase6/MARGARET-ROADMAP-2026-05-12-wave4.html` §4 — Sprint-4 MS-DOCTRINE-FORK card with success criteria
- `phase6/MARGARET-ROADMAP-2026-05-12-wave4.html` §7 — ADR-0029 owner Sora + Tomás + Iris; status user-decision card
- `phase6/wave4/iris-deliberation-wave4.md` §C — Iris empty-day-one ratification of refined-Branch-A subset
- ADR-0027 (proposed) — risk-score primitive scope; sibling deferred-decision ADR whose framing depends on this one
- ADR-0028 (proposed) — fingerprint primitive scope; dossier-fingerprint deferral depends on this ADR's outcome (Option 2 promotes it)

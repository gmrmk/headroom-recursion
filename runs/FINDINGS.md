# FINDINGS — the P-vs-NP campaign as a live benchmark of verification-gated refinement

**What this is:** the honest write-up of running `headroom-recursion`'s full instrumented
stack against a genuinely unsolved problem ("Resolve P vs NP"), using the problem not as a
target we expected to hit but as the one benchmark that cannot be faked: any credit the
system awards must be manufactured by its own verification machinery, and any "validated"
halt would by construction be a bug, not a discovery.

**What this is not:** progress on P vs NP. No run produced a verified novel result. Every
score below is judged opinion under a fabrication-dominates rubric, and the system said so
at every step.

## Setup

- Loop: TRM-style recursive refinement (n=6 latent updates + 1 answer rewrite per step),
  tier ladder with escalation on non-halt, Opus 4.8 judge × 3 votes (median), halt bar 0.9.
- Instrumentation: citation firewall over a 25-entry curated complexity-theory corpus,
  [NEW]-claim prior-art triage, LightRAG (graph store, hashing embeddings) composed with the
  exact corpus for step grounding — audits pinned to the exact backend only — and a live
  rung-1 Lean 4.31.0 + Mathlib gate (any ```lean block must compile; passes still defer to
  the judge).
- Ledger across runs: verified-beats-judged; judged entries must beat the incumbent by
  > 0.05 (anti-noise epsilon). Transport: headless `claude -p` (JSON envelope), per-call
  heartbeat + dollar fuse.
- Rubric bands (for the scores below): 0.00–0.09 fabrication/vagueness; 0.10–0.39 correct
  [KNOWN] restatements + precise frontier; 0.40–0.79 a line-by-line-verified plausibly-novel
  claim; 0.80+ verified recognized open-problem territory.

## Generations and what each one proved

### Generation 1 — full ladder, scratchpad-only seeding (1 run, wall-capped)
Trajectory: haiku 0.03 0.03 0.12 | sonnet 0.12 0.12 0.06 | opus 0.14 0.12 (cut).
**Finding: the seed-placement flaw.** The 0.30 incumbent was seeded as scratchpad context
only; the answer restarted empty, so the *cheapest* tier rebuilt an Opus-grade document and
measurably degraded it (judge: "Worse, several restatements…"). State carries UP the ladder,
but ledger seeds landed at the BOTTOM. The epsilon ratchet correctly recorded **no
progress** (0.14 < 0.30 + 0.05).

### Generation 2 — full ladder, answer seeding (2 runs, dry-stopped)
Run 0: haiku 0.12 0.04 | sonnet 0.10 0.12 0.13 | opus 0.16 0.12 **0.30** | fable 0.10 (cut).
Run 1: haiku 0.04 0.06 | sonnet 0.30 0.15 0.15 | opus 0.12 0.12 **0.30** | fable 0.12 (cut).
**Findings:** (a) seeding the answer as the current candidate eliminated the rebuild damage —
both runs held the incumbent's 0.30 through the full ladder; (b) the wall ceiling
(90 min) starved the top tier — Fable got 1 of 6 steps in both runs; (c) the citation
firewall stayed clean or explicit (0–8 unsourced flags, all surfaced to the judge); (d) the
dry-stop fired exactly as designed after two no-improvement runs. $36.86.

### Generation 3 — Fable-dominant ladder (killed by container reclamation)
haiku:1, sonnet:1, opus:1, fable:6. Died mid-Fable at ~$10.75 when the session's ephemeral
container was reclaimed overnight. **Finding: the durability doctrine held** — losses were
exactly "at most one in-flight run"; ledger, traces, toolchain, and KB all survived via
commit-per-run.

### Finale — pure Fable (fable:8, seeded, ~90 min)
<!-- FINALE RESULTS PENDING -->

## Machinery findings (the actual product)

1. **Judged scores are band-stable, not point-stable.** Identical-quality all-[KNOWN]
   documents scored 0.12–0.30 across runs with the same judge model and rationale. Any
   cross-run ratchet built on judged deltas smaller than ~0.05 measures noise; the epsilon
   is load-bearing.
2. **Seed placement is architecture.** Where carried state enters a tier ladder matters as
   much as that it is carried; refine-don't-rebuild was worth +0.16 of retained quality.
3. **Wall-clock interacts with ladder shape.** A fixed per-run ceiling silently reallocates
   steps away from the tiers that run last (the most capable ones). Per-tier step counts
   (`model:steps`) are the control that fixes it.
4. **Refusal envelopes are a state-poisoning vector.** A safeguard refusal arrived on
   stdout with exit code 0; plain-text transports would have installed "API Error: …" as
   the answer. Structural envelopes (is_error) are not optional for autonomous loops.
   Refusals were also *prompt-shaped*: the same model that refused a terse canary probe
   worked fine on real research prompts.
5. **The gate/decider split earned its keep in review before it earned it live:** a
   validator exception in gate mode would have mechanically zeroed every step of every run
   (one broken toolchain = dead campaign). Exceptions now inform the judge instead.
6. **Mechanical audits changed model behavior.** [NEW] claims were flagged with prior art
   and judged "trivially true" rather than credited; unresolvable citations were surfaced
   as unsourced rather than laundered — the firewall converts fabrication from a judgment
   call into a lookup.

## Authority statement

Ledger incumbent at wrap-up: see `pvnp-ledger.json` — judged opinion, NEEDS HUMAN REVIEW
semantics apply; nothing in this campaign is mechanically verified beyond type-correctness
of any Lean offered (and no run produced a Lean artifact that decided anything). All claims
in this document are reproducible from `runs/run-*.json` traces and the git history of this
directory.

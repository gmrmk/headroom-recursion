# ADR-0018: Defer the `osint_goblin_evidence_pipeline` split until measurement-gated

- **Status:** accepted (measurement-gated; gate criteria below)
- **Date:** 2026-05-11
- **Deciders:** @user, Sora (Engineering Lead phase6), Camille (DAG/Test phase6), Priya (DevX phase6)
- **Tags:** dag, module-layering, premature-abstraction, l3

## Context

Sora's phase6 round-2 review proposed splitting the L3 package `osint_goblin_evidence_pipeline` into two siblings (`evidence_writer` for the write-path / chain attestation, and `workflow_coordinator` for the DAG orchestration). The proposed justification was that L3 was on track to become the "god package" -- the same anti-pattern that has historically motivated boundary refactors in other event-sourced systems.

R-11 measurement (2026-05-11, `grimp.build_graph('osint_goblin_evidence_pipeline')`):

```
total modules: 1                 # the package's __init__.py
module-level fan-in: 0           # no other package imports anything from it
module-level fan-out: 0          # the package imports nothing from anyone
```

The package is a skeleton: a docstring-only `__init__.py` reserving the namespace for forthcoming Sprint-2 modules. There is nothing to split. Splitting now would be guessing at the seam before any code has revealed the actual cleavage line.

The Sora P0 framing ("split L3") was informed by the *target-state* fan-in pattern visible in the integration spec, not by the *current* fan-in measurement. R-11's whole point was to make the decision measurement-gated rather than narrative-gated, and the measurement says: not yet.

## Decision

We will keep `osint_goblin_evidence_pipeline` as a single package and **defer the split decision until the measurement-gated trigger fires** (see Gates below). When the trigger fires, the split is mechanical: write-path modules move to `osint_goblin_evidence_writer`, coordinator modules move to `osint_goblin_workflow_coordinator`, and the DAG contract in `.importlinter` is amended to enforce the new layering.

To make the eventual split cheap rather than dramatic, two preparatory conventions apply now:

1. **Module naming inside the package signals the eventual home.** A module whose primary responsibility is the write-path / chain ledger SHALL be named `*_writer.py` or live under a `writer/` subpackage. Coordinator/DAG modules SHALL be named `*_coordinator.py` or live under `coordinator/`. This makes the future `git mv` a one-line script.
2. **No new cross-cutting helpers in the package root.** Helpers that touch both write-path and coordinator concerns are written in `osint_goblin_schemas` (L0) or `osint_goblin_db` (L2) so they don't become the third tendril that makes the split a three-way refactor instead of a two-way one.

## Alternatives considered

- **Split now, pre-emptively, per Sora's original proposal.** *Rejected because:* the package has zero modules. Splitting an empty namespace creates two empty namespaces and locks in an untested seam. Premature abstraction (YAGNI; CLAUDE.md doctrine).
- **Drop the split idea entirely and never revisit.** *Rejected because:* the spec's *target-state* clearly shows write-path concerns interleaving with DAG coordination concerns; without a re-open gate we'd reach the god-package state silently. Aldous's R-12 ADR convention (Gates that re-open this) exists exactly to prevent this.
- **Split into three siblings (writer + coordinator + chain-attestation).** *Rejected because:* the chain-attestation work already lives in `osint_goblin_forensics` (L1). A three-way split would either duplicate that or further fracture concerns that the forensics package owns.
- **Move the split decision into `.importlinter` Independence contract instead of separate packages.** *Rejected because:* Independence contracts forbid imports between siblings, but here we want to enforce the *opposite* (writer may import from coordinator, but not vice-versa). That is what Layered contracts are for; once split, the L3 sub-layering goes into `.importlinter` naturally.

## Consequences

- **Positive:** Zero churn now. Sprint-2 modules land in the package as written; no double-write of imports, no rename PRs, no test surface migration.
- **Positive:** The naming convention pre-encodes the eventual split, so when the trigger fires the migration is `git mv writer/ ../osint_goblin_evidence_writer/src/...` and a `.importlinter` edit, not a refactor.
- **Negative:** The package will grow before it splits. The first contributor who writes a write-path module and a coordinator module in the same package will see them coexist; the naming convention is the only signal that they belong to different futures.
- **Negative:** If the naming convention erodes (someone names a module without the `_writer` / `_coordinator` suffix), the eventual split becomes more expensive. Mitigation: the existence of this ADR; a future lint rule if erosion is observed.
- **Neutral:** The L3 fan-in measurement becomes the canonical signal. Until a gate fires, this ADR is effectively a no-op on day-to-day work.

## Gates that re-open this

- **Gate 1 (size + fan-in):** When `grimp.build_graph('osint_goblin_evidence_pipeline')` reports **≥3 modules** AND module-level fan-in from outside the package is **≥5 distinct importers**, re-run R-11 measurement and revisit the split. Rationale: 3 modules is the smallest non-trivial population where the cleavage line becomes empirically observable; 5 importers is roughly the point where a god-package becomes a refactor-cost problem.
- **Gate 2 (mixed concerns):** When any single module inside the package contains both a write-path call (chain append, MinIO put, signature emit) AND a coordinator call (Dramatiq enqueue, fan-out, retry-policy mutation), revisit immediately -- this is the explicit "single module owns two concerns" signal that the naming convention was meant to prevent.
- **Gate 3 (M2 entry):** At M2 milestone entry, re-run R-11 measurement regardless of Gate 1/2 status. M2 is the first milestone where evidence_pipeline carries production-shaped traffic; the empirical fan-in pattern at that point is the highest-quality signal we will have.

## References

- INTEGRATION-SPEC §3.1 (package DAG); CONSOLIDATED-ROADMAP §R-11 (measurement gate)
- ADR-0022 (import-linter DAG enforcement) -- the contract that will be amended on split
- Sora phase6 round-2 proposal (sibling-of-this ADR, retracted in favor of this one)
- Aldous phase6 Q4/Q10 ADR-template additions (Alternatives considered + Gates that re-open this)
- R-11 measurement commit: `grimp.build_graph` output 2026-05-11 -- `total modules: 1, fan-in: 0`

# The Oracle Compiler — provable results, not plausible ones

Every claim a run emits is either machine-verified at the strongest rung its
domain admits, or explicitly labeled as resting on judged opinion. Nothing in
between, nothing silent.

## The verification ladder

| rung | verifier | trust required | backend |
|---|---|---|---|
| 1 | formal proof | zero | `oracle.lean_verify` (Lean 4; honest "not installed" otherwise) |
| 2 | execution | near-zero | compiled validator that runs/tests/simulates the answer |
| 3 | constraints | near-zero, narrow | compiled validator that parses + checks structure |
| 4 | ground truth | the corpus's | `claims.py` citation firewall + novelty triage via the `Retriever` seam |
| 5 | judged opinion | bounded, never sufficient | the judge panel (`halting.py`) |

`--auto-oracle` makes step zero of a run compile the strongest reachable
verifier: one model call CLASSIFIES + SYNTHESIZES (`validate(answer) -> bool`,
residuals list, calibration cases), the sandbox CALIBRATES it against planted
goods and bads (including one plausible-but-wrong), and only a validator that
discriminates every case is INSTALLED. Anything less is demoted to rung 5 and
the judge keeps full authority.

## The doctrine (each rule earned by a live-run failure)

- **Calibration is the gate.** An uncalibrated validator is opinion with a
  Python accent. One missed planted case = no authority.
- **Pre-registration.** The oracle is frozen before any solution attempt; the
  generator never sees validator source, only pass/fail. (Anti-test-fitting.)
- **Residuals are first-class.** What the validator cannot check is listed in
  the trace, told to the judge (`[ORACLE STATUS]`), and never reported verified.
- **Fabrication is mechanical to catch where a corpus exists.** `--claim-audit`
  resolves every `[KNOWN]` citation against the retriever (unresolvable →
  `[UNSOURCED]`, judge informed) and retrieves prior art against every `[NEW]`
  label. Absence of a hit is *not* proof of novelty — a surviving [NEW] means
  "novel relative to this corpus," and the summary says so.
- **The human gate.** Any outcome scored ≥ 0.40 on judged opinion alone sets
  `needs_human_review` and prints it. "Validated" is reserved for mechanical
  verification.
- **Settlement contracts.** Validators may return a `Verdict(passed,
  settles_at=...)` — forecast-domain claims halt only provisionally; the trace
  carries the settlement date so a scheduler can re-grade against reality.
- **The ledger is monotone.** `--ledger path.json` seeds runs with previously
  settled ground (verified entries as trust, judged entries with an explicit
  caveat) and records outcomes; verified never downgrades to judged.

## Evidence (live-run data behind the design)

| run | framing | result |
|---|---|---|
| cryptarithm | hand oracle (rung 3) | halted `validated`, 3 calls, tier 1 only |
| SATOR square | hand oracle (rung 3) | halted `validated`, novel-square construction |
| P vs NP ×3, ironclad | judge-only (rung 5) | 36/36 votes ≤ 0.02 — no fabrication credited |
| P vs NP, graded | judge-only (rung 5) | cheapest tier fabricated citations 4/4 steps (caught); verified ground scored 0.15; inflated [NEW] revoked |

The graded run is why rungs 4 and 1 exist in this design: the judge caught
fabricated citations as *pattern recognition* — the firewall makes it a lookup;
and only a formal backend can verify novel mathematics rather than vibe it.

## Usage

```bash
recurse --auto-oracle "solve this cryptarithm ..."           # self-compiled checker
recurse --auto-oracle --oracle-model claude-opus-4-8 "..."   # pin the compiler
recurse --claim-audit --lightrag ./kb --ledger runs.json "..."  # research mode
```

Library: `compile_oracle(problem, client=..., model=...)` returns a
`CompiledOracle(validator, rung, residuals, calibration)`; install via
`RecurseConfig(validator=compiled.validator)` or let `oracle_auto=True` do it.

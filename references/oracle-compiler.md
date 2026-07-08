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

**Verdict dimensions.** A rung names the *source* of trust; three orthogonal
dimensions state the verdict's *strength* within it — the taxonomy needs no
extra rungs, it needs these:

- **Sufficiency** — does a pass DECIDE correctness, or merely GATE it? The
  compiler must claim `"sufficient": true` explicitly (only when the checked
  object *is* the answer, e.g. an equation whose arithmetic is verified).
  Anything less runs in **gate mode**: rejections are final for the step (the
  judge is skipped — mechanical, and cheaper), but passes defer to the judge
  and can never halt as "validated". *The gate doctrine: halt authority extends
  exactly as far as declared coverage; residuals demote authority from decider
  to gate.* This closes the hole found in the live research run, where a
  structural (rung-3) validator for a proof document could otherwise have
  "validated" a well-formatted but mathematically wrong answer.
- **Settlement** — `Verdict.settles_at`: the check passed today, but the claim
  is about the future; the halt is provisional until reality grades it.
- **Confidence** — `Verdict.confidence`: 1.0 for exhaustive/deterministic
  checks; < 1.0 for statistical ones (a 10⁶-trial Monte Carlo pass is evidence,
  not enumeration). A statistical validation flags `needs_human_review` and
  prints its confidence — it never masquerades as exhaustive.

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
| P vs NP, fully instrumented | ladder+audit+ledger+oracle | ledger ratchet 0.02→0.15→0.30; floor lifted 0.02→0.22; models *self-flagged* "[not in provided corpus]" and withdrew a [NEW] label — the audit changed behavior at the source; 15% Headroom on 302k tokens; zero transport deaths |
| Lean proof-repair (`double_eq`) | rung 1 (core Lean) | Sonnet one-shot a machine-checked induction proof; independently re-verified; `#print axioms` = `[propext, Quot.sound]` — no `sorryAx` |
| P vs NP + Lean rung live | rung 1 in the loop | ledger 0.30→**0.55**, score climbing *as verified content accumulated* (0.28→0.40→0.45→0.55); 9-lemma Shannon lower bound machine-checked vs Mathlib, axiom-audited clean → `examples/shannon_lower_bound.lean` |
| AIME-style benchmark | rung 2 (brute-force truth) | `solve()` sample-4 + majority vote: **3/3 correct, unanimous** on ground truths computed by brute force (738/11/819) → `examples/aimo_benchmark.py` |

The graded run is why rungs 4 and 1 exist in this design: the judge caught
fabricated citations as *pattern recognition* — the firewall makes it a lookup;
and only a formal backend can verify novel mathematics rather than vibe it.
The instrumented run is why gate mode exists: its compiled rung-3 validator
declared residuals amounting to "the entire score is unchecked" while holding
full halt authority — a hole, now closed by the sufficiency dimension.

## Usage

```bash
recurse --auto-oracle "solve this cryptarithm ..."           # self-compiled checker
recurse --auto-oracle --oracle-model claude-opus-4-8 "..."   # pin the compiler
recurse --claim-audit --lightrag ./kb --ledger runs.json "..."  # research mode
```

Library: `compile_oracle(problem, client=..., model=...)` returns a
`CompiledOracle(validator, rung, residuals, calibration)`; install via
`RecurseConfig(validator=compiled.validator)` or let `oracle_auto=True` do it.

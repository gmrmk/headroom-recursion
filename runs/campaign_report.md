# Campaign report

- problem key: `6c2293f54c8418a6`
- stopped: **exhausted** after 1 run(s), 12 model calls, $1.35

## Authority of the outcome

The ledger's best entry rests on **judged opinion** (best halt_prob 0.30).

Nothing here is a verified result; treat the answer as a draft whose grading rubric and per-step judge scores are in the run traces.

## Run trajectory

| run | stop | best | ledger | calls | $cum |
|---|---|---|---|---|---|
| 0 | exhausted | 0.30 | + | 12 | 1.35 |

## Provenance

- per-run traces: `runs/run-*.json` (+ `.summary.txt`)
- ledger: `runs/pvnp-ledger.json` (verified-beats-judged, judged entries must beat the incumbent by ≥ 0.05)
- lean decider artifacts (if any): `runs/lean/`

Scores are judged opinion unless explicitly marked verified; the rubric caps fabricated arguments at 0.05 and self-assessment carries zero weight.

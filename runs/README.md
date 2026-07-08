# runs/ — evidence artifacts

Every claim a run makes must be reproducible from artifacts alone. This
directory holds them:

- `run-*.json` / `run-*.summary.txt` — the full machine-readable trace and its
  human summary for each run (`--trace-dir runs`, automatic in campaign mode).
  The trace records per-step halt probabilities, gate rejections, claim-audit
  findings, rejected/truncated completions, token accounting, and which answer
  won and why.
- `lean/decider-*.lean` / `lean/decider-*.out` — for every rung-1 decider
  attempt: the exact spliced file that was compiled and the compiler output
  including the `#print axioms` audit. A "validated" rung-1 claim can be
  independently re-verified by compiling the `.lean` file with the pinned
  toolchain in `../lean/`.
- `campaign-summary.json` / `heartbeat.json` — campaign-mode progress ledger
  and liveness pulse (see `campaign.py`).
- `install-lean.log` — provenance of the Lean toolchain used by the artifacts
  above (version, install route, cache source).

Reading order for auditing a claim: the run summary names the stop reason and
authority (validated / judged / gated); `NEEDS HUMAN REVIEW` means the outcome
rests on judged opinion, not mechanical verification, no matter how confident
the prose sounds.

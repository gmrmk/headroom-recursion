"""Live AIMO-style benchmark: sample-wide + majority vote, scored vs brute-force truth.

Ground truths were computed by Python brute force (738, 11, 819) — the benchmark
depends on neither the author's memory nor the model's. Each problem gets
`samples` independent attempts; the modal extracted integer is the system's answer.

Reproduced result (Sonnet workers, 4 samples/problem, CLI transport, 2026-07-08):

    no-zero-pairs      truth 738   voted 738   conf 100%   CORRECT (dist {738: 4})
    three-divisors     truth  11   voted  11   conf 100%   CORRECT (dist {11: 4})
    subset-sum-mod5    truth 819   voted 819   conf 100%   CORRECT (dist {819: 4})
    SCORE: 3/3 — unanimous self-consistency on all three.

Calibration note: AIME-style problems of a familiar genre — this validates the
pipeline (sample -> extract -> vote), not fresh-contest performance. For rung-2
verification instead of self-consistency, pass `verifier=` to `solve()`.
"""

from __future__ import annotations

# Assumes the package is installed (`pip install -e .`) and a model transport is
# available: `claude` CLI login for CLITransportClient, or swap in ClaudeClient /
# OpenAIClient with an API key.

from headroom_recursion import CLITransportClient, RecurseConfig, Tier
from headroom_recursion.competition import solve

PROBLEMS = [
    ("no-zero-pairs",
     "Find the number of ordered pairs of positive integers (a, b) such that a + b = 1000 "
     "and neither a nor b contains the digit 0. Give the final integer answer.",
     738),
    ("three-divisors",
     "How many positive integers n with 1 <= n <= 1000 have exactly three positive divisors? "
     "Give the final integer answer.",
     11),
    ("subset-sum-mod5",
     "How many nonempty subsets of {1, 2, 3, ..., 12} have a sum divisible by 5? "
     "Give the final integer answer.",
     819),
]

cfg = RecurseConfig(
    n=1, T=2,
    ladder=(Tier("claude-sonnet-5", max_tokens=2048),),
    judge_model="claude-haiku-4-5-20251001",
    halt_threshold=0.9,
    use_headroom=True,
    max_total_calls=12,
    max_wall_seconds=1200,
)

client = CLITransportClient()
correct = 0
print(f"{'problem':18s} {'truth':>6s} {'voted':>6s} {'conf':>6s}  result")
print("-" * 55)
for key, problem, truth in PROBLEMS:
    res = solve(problem, client=client, config=cfg, samples=4, max_workers=4)
    ok = res.answer == truth
    correct += ok
    print(f"{key:18s} {truth:6d} {str(res.answer):>6s} {res.confidence:6.0%}  "
          f"{'CORRECT' if ok else 'wrong'}   (dist {res.distribution})")

print("-" * 55)
print(f"SCORE: {correct}/{len(PROBLEMS)} correct")

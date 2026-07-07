"""Competition math (AIMO-shaped): sample wide, verify hard, vote over survivors.

The recipe that wins olympiad-style contests is not "one careful attempt" — it is
**many independent attempts + a hard check + majority over what survives**. That
maps 1:1 onto what this repo already is:

* sample-wide  = run ``recurse`` N times (each internally refines);
* verify-hard  = extract the answer and, where a checker exists, keep only
                 answers a rung<=2 oracle accepts;
* vote         = the modal answer across attempts (self-consistency) wins.

The crucial honesty knob: when a per-problem oracle is available (the answer is
independently checkable), voting is over *verified* answers only. Without one,
voting is self-consistency — evidence, not proof — and the result says so.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Callable, Optional

from headroom_recursion.batch import BatchItem, run_batch
from headroom_recursion.config import RecurseConfig
from headroom_recursion.oracle import extract_final_integer


@dataclass
class SolveResult:
    problem: str
    answer: Optional[int]          # modal answer, or None if nothing extracted
    votes: int                     # attempts backing the modal answer
    samples: int                   # attempts that produced any answer
    total: int                     # attempts run
    distribution: dict             # answer -> count
    verified: bool                 # True iff a rung<=2 oracle accepted the modal answer
    confidence: float              # votes / samples (self-consistency strength)

    def summary(self) -> str:
        tag = "VERIFIED (rung<=2)" if self.verified else "self-consistency only (unverified)"
        dist = ", ".join(f"{k}:{v}" for k, v in sorted(self.distribution.items(), key=lambda t: -t[1])[:5])
        return (
            f"answer={self.answer} [{tag}]  "
            f"votes={self.votes}/{self.samples} (conf {self.confidence:.0%})  "
            f"dist: {dist}"
        )


def solve(
    problem: str,
    *,
    client,
    config: Optional[RecurseConfig] = None,
    samples: int = 8,
    max_workers: int = 4,
    answer_of: Callable[[str], Optional[int]] = extract_final_integer,
    verifier: Optional[Callable[[int], bool]] = None,
) -> SolveResult:
    """Sample ``samples`` independent attempts and vote over the extracted answers.

    ``verifier`` (optional): given a candidate integer, return True if it is
    independently checkable as correct (e.g. plug back into the problem's
    constraints). When present, the winner is the modal answer AMONG verified
    ones — a rung<=2 result. When absent, the winner is plain self-consistency.
    """

    base = config or RecurseConfig()
    items = [BatchItem(key=f"s{i}", problem=problem, config=base) for i in range(samples)]
    results = run_batch(items, client=client, config=base, max_workers=max_workers)

    extracted = [answer_of(r.answer) for r in results]
    answers = [a for a in extracted if a is not None]

    if verifier is not None:
        verified_answers = [a for a in answers if _safe(verifier, a)]
        pool, verified = verified_answers, bool(verified_answers)
    else:
        pool, verified = answers, False

    if not pool:
        return SolveResult(problem, None, 0, len(answers), len(results), {}, False, 0.0)

    dist = Counter(pool)
    answer, votes = dist.most_common(1)[0]
    denom = len(pool) if verified else len(answers)
    return SolveResult(
        problem=problem,
        answer=answer,
        votes=votes,
        samples=len(answers),
        total=len(results),
        distribution=dict(dist),
        verified=verified,
        confidence=votes / denom if denom else 0.0,
    )


@dataclass
class BenchmarkResult:
    total: int
    correct: int
    attempted: int  # produced any answer
    details: list = field(default_factory=list)  # (key, expected, got, correct)

    @property
    def accuracy(self) -> float:
        return self.correct / self.total if self.total else 0.0

    def summary(self) -> str:
        return (
            f"benchmark: {self.correct}/{self.total} correct ({self.accuracy:.0%})  "
            f"{self.attempted}/{self.total} produced an answer"
        )


def benchmark(
    problems: list[tuple[str, str, int]],  # (key, problem, known_answer)
    *,
    client,
    config: Optional[RecurseConfig] = None,
    samples: int = 8,
    max_workers: int = 4,
    modulus: Optional[int] = None,
) -> BenchmarkResult:
    """Score the solver against problems with known answers (self-consistency mode).

    This measures the *voted* answer against ground truth — the honest metric for
    a competition-math system. ``modulus`` (e.g. 1000) compares residues.
    """

    correct = attempted = 0
    details = []
    for key, problem, known in problems:
        res = solve(problem, client=client, config=config, samples=samples, max_workers=max_workers)
        got = res.answer
        if got is not None:
            attempted += 1
        ok = got is not None and (
            (got % modulus == known % modulus) if modulus else (got == known)
        )
        correct += int(ok)
        details.append((key, known, got, ok))
    return BenchmarkResult(len(problems), correct, attempted, details)


def _safe(fn: Callable[[int], bool], x: int) -> bool:
    try:
        return bool(fn(x))
    except Exception:
        return False

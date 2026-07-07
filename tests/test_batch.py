"""Batch harness + competition-math solver: parallel runs, voting, benchmarking."""

from __future__ import annotations

from headroom_recursion.batch import BatchItem, BatchReport, run_batch
from headroom_recursion.competition import benchmark, solve
from headroom_recursion.config import RecurseConfig, Tier
from tests.conftest import StubClient


def one_tier(**kw) -> RecurseConfig:
    return RecurseConfig(ladder=(Tier("m0"),), n=1, T=1, **kw)


# ---------------------------------------------------------------------------
# Batch
# ---------------------------------------------------------------------------

def test_run_batch_completes_all_items_in_order():
    items = [BatchItem(key=f"k{i}", problem=f"p{i}") for i in range(4)]
    results = run_batch(items, client=StubClient(), config=one_tier(), max_workers=2)
    assert [r.key for r in results] == ["k0", "k1", "k2", "k3"]
    assert all(r.ok for r in results)


def test_run_batch_isolates_a_failing_item():
    # A model that always raises: recurse soft-fails the tier internally and
    # returns a trace (stop_reason "failed") rather than raising — so the item
    # completes without an exception and the fleet is never at risk.
    class BoomOnP2(StubClient):
        def complete(self, **kw):
            if "p2" in kw.get("user", ""):
                raise RuntimeError("kaboom")
            return super().complete(**kw)

    items = [BatchItem(key=f"k{i}", problem=f"p{i}") for i in range(4)]
    results = run_batch(items, client=BoomOnP2(), config=one_tier(), max_workers=1)
    by_key = {r.key: r for r in results}
    assert all(r.ok for r in results)  # every item returned a trace
    assert by_key["k2"].trace.stop_reason == "failed"  # its tier soft-failed
    assert by_key["k2"].halted is False
    assert by_key["k0"].halted is False and by_key["k0"].trace.stop_reason != "failed"


def test_batch_report_counts():
    items = [BatchItem(key=f"k{i}", problem=f"p{i}") for i in range(3)]
    # validator makes every run halt as "validated"
    results = run_batch(items, client=StubClient(), config=one_tier(validator=lambda a: True))
    rep = BatchReport(results)
    assert rep.total == 3 and rep.validated == 3 and rep.errored == 0
    assert "3 validated" in rep.summary()


# ---------------------------------------------------------------------------
# Competition solver: majority voting
# ---------------------------------------------------------------------------

def test_solve_majority_vote_over_samples():
    stub = StubClient(answers=["the answer is 7", "answer: 7", "answer = 9"])
    res = solve("compute x", client=stub, config=one_tier(), samples=3, max_workers=1)
    assert res.answer == 7 and res.votes == 2 and res.samples == 3
    assert res.verified is False  # no verifier -> self-consistency only
    assert res.distribution == {7: 2, 9: 1}
    assert "self-consistency only" in res.summary()


def test_solve_with_verifier_keeps_only_verified_answers():
    # Majority raw answer is 9 (2 votes) but only 7 verifies -> 7 wins, verified.
    stub = StubClient(answers=["ans 9", "ans 9", "ans 7"])
    res = solve(
        "x", client=stub, config=one_tier(), samples=3, max_workers=1,
        verifier=lambda n: n == 7,
    )
    assert res.answer == 7 and res.verified is True
    assert "VERIFIED" in res.summary()


def test_solve_returns_none_when_nothing_extracts():
    stub = StubClient(answers=["no number here", "still none", "nope"])
    res = solve("x", client=stub, config=one_tier(), samples=3, max_workers=1)
    assert res.answer is None and res.votes == 0


def test_benchmark_scores_against_known_answers():
    # Both problems: stub always answers 42; first known=42 (correct), second=99 (wrong).
    stub = StubClient(answers=["answer is 42"])
    probs = [("q1", "p1", 42), ("q2", "p2", 99)]
    rep = benchmark(probs, client=stub, config=one_tier(), samples=2, max_workers=1)
    assert rep.total == 2 and rep.correct == 1
    assert rep.accuracy == 0.5 and "1/2" in rep.summary()


def test_benchmark_modulus_compares_residues():
    stub = StubClient(answers=["answer is 1042"])
    rep = benchmark([("q", "p", 42)], client=stub, config=one_tier(), samples=1, max_workers=1, modulus=1000)
    assert rep.correct == 1  # 1042 ≡ 42 (mod 1000)

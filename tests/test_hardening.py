"""Hardening regression tests — each one fails against the pre-hardening loop.

Covers: state-destruction guards, oscillation detection, best-answer tracking,
hard budgets, partial traces on error/interrupt, trace observability, and the
persistent event loop for async retrieval backends.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from headroom_recursion.config import RecurseConfig, Tier
from headroom_recursion.ladder import RunError, recurse
from headroom_recursion.retrieval import _LoopRunner
from tests.conftest import StubClient


def one_tier(**kw) -> RecurseConfig:
    return RecurseConfig(ladder=(Tier("m0"),), **kw)


# ---------------------------------------------------------------------------
# 1.2 — empty completions must never destroy loop state
# ---------------------------------------------------------------------------

def test_empty_latent_output_keeps_previous_scratchpad():
    # First latent call produces a real scratchpad; every later one returns "".
    stub = StubClient(latent_texts=["the good scratchpad", ""])
    trace = recurse("x", client=stub, config=one_tier(n=1, T=2))

    answer_prompts = [u for k, u in stub.prompts_seen if k == "answer"]
    # Step 2's answer update still sees step 1's scratchpad, not "".
    assert "the good scratchpad" in answer_prompts[1]
    assert trace.steps[1].rejected_updates == 1


def test_empty_answer_output_keeps_previous_answer():
    stub = StubClient(answers=["a real answer", ""])
    trace = recurse("x", client=stub, config=one_tier(n=1, T=2))

    assert trace.final_answer == "a real answer"
    assert trace.steps[1].rejected_updates == 1


# ---------------------------------------------------------------------------
# 1.3 — A/B oscillation is convergence, not a budget sink
# ---------------------------------------------------------------------------

def test_oscillating_answers_converge():
    stub = StubClient(answers=["A", "B", "A", "B", "A"])
    trace = recurse("x", client=stub, config=one_tier(n=1, T=5))

    # Step 3 repeats step 1's answer -> converged; T=5 is NOT exhausted.
    assert len(trace.steps) == 3
    assert trace.steps[-1].converged is True
    assert trace.stop_reason == "converged"


# ---------------------------------------------------------------------------
# 1.4 — the best-scoring answer wins on non-halt exits
# ---------------------------------------------------------------------------

def test_best_answer_returned_when_refinement_regresses():
    stub = StubClient(
        answers=["good", "worse", "worst"],
        halt_prob=lambda step: [0.85, 0.3, 0.2][min(step, 2)],
    )
    trace = recurse("x", client=stub, config=one_tier(n=1, T=3, halt_threshold=0.9))

    assert trace.halted is False
    assert trace.final_answer == "good"  # not "worst"
    assert trace.best_halt_prob == 0.85
    assert trace.best_step_index == 0
    assert trace.best_model == "m0"


def test_halting_answer_still_wins_over_earlier_best():
    stub = StubClient(
        answers=["early", "final"],
        halt_prob=lambda step: [0.5, 0.95][min(step, 1)],
    )
    trace = recurse("x", client=stub, config=one_tier(n=1, T=3, halt_threshold=0.9))

    assert trace.halted is True
    assert trace.final_answer == "final"


# ---------------------------------------------------------------------------
# 2.1 — a run never loses its work
# ---------------------------------------------------------------------------

def test_api_error_raises_runerror_with_partial_trace():
    # n=1 -> 3 calls per step; call 5 is step 2's latent call.
    stub = StubClient(raise_on_call=5)
    with pytest.raises(RunError) as excinfo:
        recurse("x", client=stub, config=one_tier(n=1, T=3))

    trace = excinfo.value.trace
    assert trace.stop_reason == "error"
    assert "RuntimeError" in trace.error
    assert len(trace.steps) == 1  # step 1 completed and is preserved
    assert trace.final_answer == "answer-v0"  # best-so-far, not lost


def test_keyboard_interrupt_returns_partial_trace():
    stub = StubClient(raise_on_call=5, raise_exc=KeyboardInterrupt)
    trace = recurse("x", client=stub, config=one_tier(n=1, T=3))

    assert trace.stop_reason == "interrupted"
    assert trace.error == "KeyboardInterrupt"
    assert trace.halted is False
    assert len(trace.steps) == 1
    assert trace.final_answer == "answer-v0"


# ---------------------------------------------------------------------------
# 2.2 — hard budgets stop the run and never escalate past the hit
# ---------------------------------------------------------------------------

def test_max_total_calls_stops_without_escalation():
    cfg = RecurseConfig(
        ladder=(Tier("m0"), Tier("m1")), n=2, T=5, max_total_calls=10
    )
    stub = StubClient()
    trace = recurse("x", client=stub, config=cfg)

    assert trace.stop_reason == "budget"
    assert trace.halted is False
    # Checked at step boundaries: overshoot is at most one step (n + 2 calls).
    assert trace.total_calls <= 10 + (2 + 2)
    assert stub.models_used() == ["m0"]  # never escalated to m1
    assert trace.tier_stops == ["m0: budget"]


def test_wall_clock_budget_stops_immediately():
    cfg = one_tier(n=1, T=5, max_wall_seconds=1e-9)
    stub = StubClient()
    trace = recurse("x", client=stub, config=cfg)

    assert trace.stop_reason == "budget"
    assert len(trace.steps) == 0
    assert stub.calls == []  # deadline already passed at the first boundary


# ---------------------------------------------------------------------------
# 4.1 — the trace answers "why"
# ---------------------------------------------------------------------------

def test_tier_stops_record_every_tier():
    cfg = RecurseConfig(ladder=(Tier("m0"), Tier("m1")), n=1, T=2)
    trace = recurse("x", client=StubClient(), config=cfg)

    assert trace.tier_stops == ["m0: exhausted", "m1: exhausted"]
    assert trace.wall_seconds >= 0.0


def test_trace_round_trips_through_json():
    trace = recurse("x", client=StubClient(), config=one_tier(n=1, T=2))
    blob = json.dumps(trace.to_dict())
    back = json.loads(blob)
    assert back["stop_reason"] == trace.stop_reason
    assert back["best_answer"] == trace.best_answer
    assert back["tier_stops"] == trace.tier_stops


def test_validator_exception_is_recorded_not_fatal():
    def bad_validator(answer: str) -> bool:
        raise ValueError("validator bug")

    trace = recurse("x", client=StubClient(), config=one_tier(n=1, T=1, validator=bad_validator))
    assert "ValueError" in trace.steps[0].validator_error
    assert trace.stop_reason in {"exhausted", "converged"}  # run completed


# ---------------------------------------------------------------------------
# 2.3 — async retrieval backends need ONE persistent event loop
# ---------------------------------------------------------------------------

def test_loop_runner_uses_one_persistent_loop():
    runner = _LoopRunner()
    seen = []

    async def probe():
        seen.append(asyncio.get_running_loop())
        return len(seen)

    try:
        assert runner.run(probe()) == 1
        assert runner.run(probe()) == 2
        # Loop-bound state (locks, sessions) only survives if both calls ran on the
        # SAME loop — a fresh-loop-per-call runner fails this.
        assert seen[0] is seen[1]
    finally:
        runner.close()


def test_loop_runner_propagates_exceptions():
    runner = _LoopRunner()

    async def boom():
        raise RuntimeError("inner failure")

    try:
        with pytest.raises(RuntimeError, match="inner failure"):
            runner.run(boom())
    finally:
        runner.close()

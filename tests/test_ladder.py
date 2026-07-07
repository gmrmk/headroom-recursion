"""Escalation: recurse cheap first, climb the ladder only on non-halt, carry state."""

from __future__ import annotations

from headroom_recursion.config import RecurseConfig, Tier
from headroom_recursion.ladder import recurse
from tests.conftest import StubClient


LADDER = (Tier("m0"), Tier("m1"), Tier("m2"))


def test_no_escalation_when_first_tier_halts(stub):
    stub.halt_prob = 0.99  # halts on step 1 of tier 0
    cfg = RecurseConfig(ladder=LADDER, n=1, T=2)
    trace = recurse("x", client=stub, config=cfg)

    assert trace.halted is True
    assert trace.final_model == "m0"
    assert stub.models_used() == ["m0"]  # never touched m1/m2


def test_escalates_through_all_tiers_on_non_halt(stub):
    # halt_prob stays 0 -> each tier exhausts and escalates.
    cfg = RecurseConfig(ladder=LADDER, n=1, T=2)
    trace = recurse("x", client=stub, config=cfg)

    assert trace.halted is False
    assert trace.stop_reason in {"exhausted", "converged"}
    assert stub.models_used() == ["m0", "m1", "m2"]  # climbed the whole ladder
    assert trace.final_model == "m2"
    # 3 tiers x 2 steps = 6 steps total.
    assert len(trace.steps) == 6


def test_convergence_escalates_early_without_wasting_steps():
    # Same answer every time -> converges on step 2, escalates before exhausting T=5.
    stub = StubClient(answers=["stable answer"], halt_prob=0.0)
    cfg = RecurseConfig(ladder=(Tier("m0"), Tier("m1")), n=1, T=5)
    trace = recurse("x", client=stub, config=cfg)

    # First tier: step 1 sets the answer (prev was ""), step 2 sees it unchanged
    # -> converged -> escalate. So each tier uses at most 2 steps, not 5.
    per_tier_steps = [s for s in trace.steps if s.tier_model == "m0"]
    assert len(per_tier_steps) == 2
    assert per_tier_steps[-1].converged is True
    assert stub.models_used() == ["m0", "m1"]


def test_state_carries_forward_up_the_ladder():
    # Distinct answers so no convergence; verify the final answer is the last one
    # produced at the top tier (state threaded through, not restarted).
    answers = [f"a{i}" for i in range(20)]
    stub = StubClient(answers=answers, halt_prob=0.0)
    cfg = RecurseConfig(ladder=(Tier("m0"), Tier("m1")), n=1, T=2)
    trace = recurse("x", client=stub, config=cfg)

    # 2 tiers x 2 steps = 4 answer updates -> last is answers[3].
    assert trace.final_answer == "a3"
    assert trace.steps[-1].tier_model == "m1"

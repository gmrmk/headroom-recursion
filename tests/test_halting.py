"""Halt predictor: JSON parsing, threshold, convergence, and the validator oracle."""

from __future__ import annotations

import pytest

from headroom_recursion import halting
from headroom_recursion.config import RecurseConfig, Tier
from headroom_recursion.ladder import recurse
from tests.conftest import StubClient


@pytest.mark.parametrize(
    "text,expected",
    [
        ('{"halt_prob": 0.9, "reason": "ok"}', 0.9),
        ('noise {"halt_prob": 1.0} trailing', 1.0),
        ("halt_prob is about 0.42 here", 0.42),
        ('{"halt_prob": 5}', 1.0),      # clamped
        ('{"halt_prob": -3}', 0.0),     # clamped
        ("total garbage", 0.0),
    ],
)
def test_parse_halt_prob(text, expected):
    prob, _reason = halting._parse(text)
    assert prob == expected


def test_threshold_boundary_halts(stub):
    stub.halt_prob = 0.9
    cfg = RecurseConfig(ladder=(Tier("m0"),), n=1, T=3, halt_threshold=0.9)
    trace = recurse("x", client=stub, config=cfg)
    assert trace.halted is True  # >= threshold


def test_just_below_threshold_does_not_halt(stub):
    stub.halt_prob = 0.89
    cfg = RecurseConfig(ladder=(Tier("m0"),), n=1, T=1, halt_threshold=0.9)
    trace = recurse("x", client=stub, config=cfg)
    assert trace.halted is False


def test_validator_oracle_halts_immediately_without_judge():
    stub = StubClient(answers=["42"], halt_prob=0.0)  # judge would say don't halt
    cfg = RecurseConfig(
        ladder=(Tier("m0"),), n=1, T=3, validator=lambda a: a.strip() == "42"
    )
    trace = recurse("what is 6*7", client=stub, config=cfg)

    assert trace.halted is True
    assert trace.stop_reason == "validated"
    assert stub.count("judge") == 0  # oracle short-circuits the judge call


def test_judge_model_can_be_pinned(stub):
    cfg = RecurseConfig(ladder=(Tier("m0"),), n=1, T=1, judge_model="m-judge")
    recurse("x", client=stub, config=cfg)
    judge_models = [m for k, m in stub.calls if k == "judge"]
    assert judge_models == ["m-judge"]

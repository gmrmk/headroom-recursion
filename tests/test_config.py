"""Config validation: silently-misbehaving configs must fail fast, before any call."""

from __future__ import annotations

import pytest

from headroom_recursion.config import RecurseConfig, Tier
from headroom_recursion.ladder import recurse
from tests.conftest import StubClient, StubRetriever


def test_default_config_validates_clean():
    RecurseConfig().validate()  # must not raise


@pytest.mark.parametrize(
    "kwargs",
    [
        {"n": 0},                      # zero latent updates = no reasoning at all
        {"n": -3},
        {"T": 0},
        {"halt_threshold": 0.0},       # must be > 0
        {"halt_threshold": 1.5},       # can never halt
        {"temperature": -0.1},
        {"temperature": 1.5},
        {"judge_votes": 0},
        {"retrieval_query_chars": 0},
        {"retrieval_max_chars": 0},
        {"max_total_calls": 0},
        {"max_wall_seconds": 0.0},
        {"max_wall_seconds": -5.0},
        {"ladder": (Tier("m0", max_steps=0),)},
        {"ladder": (Tier("m0", max_tokens=0),)},
    ],
)
def test_bad_configs_rejected(kwargs):
    with pytest.raises(ValueError, match="RecurseConfig"):
        RecurseConfig(**kwargs).validate()


def test_retrieval_k_only_checked_with_a_retriever():
    RecurseConfig(retrieval_k=0).validate()  # no retriever -> irrelevant, allowed
    with pytest.raises(ValueError, match="retrieval_k"):
        RecurseConfig(retrieval_k=0, retriever=StubRetriever()).validate()


def test_recurse_validates_before_any_client_call():
    stub = StubClient()
    with pytest.raises(ValueError, match="halt_threshold"):
        recurse("x", client=stub, config=RecurseConfig(halt_threshold=2.0))
    assert stub.calls == []  # failed fast, spent nothing


def test_empty_ladder_is_a_safe_noop():
    trace = recurse("x", client=StubClient(), config=RecurseConfig(ladder=()))
    assert trace.stop_reason == "no-op"
    assert trace.final_answer == ""

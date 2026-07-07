"""The loop makes exactly n latent + 1 answer + 1 judge calls per improvement step."""

from __future__ import annotations

from headroom_recursion.config import RecurseConfig, Tier
from headroom_recursion.ladder import recurse
from tests.conftest import StubClient


def single_tier(**cfg_kw) -> RecurseConfig:
    return RecurseConfig(ladder=(Tier("m-small"),), **cfg_kw)


def test_call_counts_per_step(stub):
    cfg = single_tier(n=6, T=3, halt_threshold=0.9)  # never halts (stub halt_prob=0.0)
    trace = recurse("solve it", client=stub, config=cfg)

    assert len(trace.steps) == 3          # full budget, no early halt
    assert stub.count("latent") == 6 * 3  # n per step
    assert stub.count("answer") == 3      # 1 per step
    assert stub.count("judge") == 3       # 1 per step
    assert trace.total_calls == (6 + 2) * 3


def test_early_halt_stops_the_loop(stub):
    # Halt on the 2nd step (step_index 1).
    stub.halt_prob = lambda step: 0.95 if step >= 1 else 0.1
    trace = recurse("solve it", client=stub, config=single_tier(n=2, T=5))

    assert trace.halted is True
    assert trace.stop_reason == "halt"
    assert len(trace.steps) == 2
    assert stub.count("latent") == 2 * 2  # only two steps ran


def test_n_latent_updates_respected(stub):
    trace = recurse("x", client=stub, config=single_tier(n=1, T=1))
    assert stub.count("latent") == 1
    assert stub.count("answer") == 1
    assert len(trace.steps) == 1
    assert trace.steps[0].latent_calls == 1


def test_headroom_token_accounting_flows_into_trace():
    stub = StubClient(tokens_before=100, tokens_after=40)
    trace = recurse("x", client=stub, config=single_tier(n=2, T=1))
    # 2 latent + 1 answer + 1 judge = 4 calls, each 100 -> 40.
    assert trace.tokens_before == 400
    assert trace.tokens_after == 160
    assert trace.tokens_saved == 240
    assert round(trace.savings_pct) == 60

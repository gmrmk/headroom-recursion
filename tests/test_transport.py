"""CLITransportClient: retries around a flaky subprocess, no real CLI needed."""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from headroom_recursion.clients import CLITransportClient
from headroom_recursion.config import RecurseConfig, Tier
from headroom_recursion.ladder import recurse
from tests.conftest import StubClient


def ok(stdout="the answer"):
    return SimpleNamespace(returncode=0, stdout=stdout, stderr="")


def test_success_returns_text_and_headroom_accounting():
    calls = []

    def runner(argv, **kw):
        calls.append((argv, kw))
        return ok("hello world")

    client = CLITransportClient(runner=runner)
    res = client.complete(model="m1", system="sys", user="do it", use_headroom=False)

    assert res.text == "hello world"
    assert res.tokens_before > 0 and res.tokens_before == res.tokens_after
    argv, kw = calls[0]
    assert argv[:2] == ["claude", "-p"] and "m1" in argv
    assert kw["input"] == "do it" and kw["timeout"] == 420.0


def test_timeout_retries_then_succeeds():
    state = {"n": 0}

    def runner(argv, **kw):
        state["n"] += 1
        if state["n"] < 3:
            raise subprocess.TimeoutExpired(cmd=argv, timeout=kw["timeout"])
        return ok()

    client = CLITransportClient(attempts=3, runner=runner)
    assert client.complete(model="m", system="s", user="u", use_headroom=False).text == "the answer"
    assert state["n"] == 3


def test_exhausted_attempts_raise_last_error():
    def runner(argv, **kw):
        raise subprocess.TimeoutExpired(cmd=argv, timeout=kw["timeout"])

    client = CLITransportClient(attempts=2, runner=runner)
    with pytest.raises(subprocess.TimeoutExpired):
        client.complete(model="m", system="s", user="u", use_headroom=False)


def test_nonzero_exit_raises_runtime_error():
    def runner(argv, **kw):
        return SimpleNamespace(returncode=1, stdout="", stderr="boom")

    client = CLITransportClient(attempts=1, runner=runner)
    with pytest.raises(RuntimeError, match="boom"):
        client.complete(model="m", system="s", user="u", use_headroom=False)


@pytest.mark.parametrize("kwargs", [{"attempts": 0}, {"timeout_s": 0}, {"timeout_s": -5}])
def test_bad_construction_rejected(kwargs):
    with pytest.raises(ValueError):
        CLITransportClient(**kwargs)


def test_step_timeout_escalates_tier():
    # A tier whose steps exceed step_timeout_s hands the draft up after one step.
    cfg = RecurseConfig(ladder=(Tier("m0", step_timeout_s=1e-9), Tier("m1")), n=1, T=5)
    stub = StubClient()
    trace = recurse("x", client=stub, config=cfg)

    assert trace.tier_stops[0] == "m0: step-timeout"
    m0_steps = [s for s in trace.steps if s.tier_model == "m0"]
    assert len(m0_steps) == 1  # did not burn the remaining 4 steps
    assert stub.models_used() == ["m0", "m1"]

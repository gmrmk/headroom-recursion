"""CLITransportClient: JSON envelope parsing, retries, refusals — no real CLI needed."""

from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest

from headroom_recursion.clients import CLITransportClient, TransportRefused
from headroom_recursion.config import RecurseConfig, Tier
from headroom_recursion.ladder import recurse
from tests.conftest import StubClient


def envelope(result="the answer", **over):
    body = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": result,
        "stop_reason": "end_turn",
        "total_cost_usd": 0.0123,
    }
    body.update(over)
    return SimpleNamespace(returncode=0, stdout=json.dumps(body), stderr="")


def test_success_parses_envelope_and_headroom_accounting():
    calls = []

    def runner(argv, **kw):
        calls.append((argv, kw))
        return envelope("hello world")

    client = CLITransportClient(runner=runner)
    res = client.complete(model="m1", system="sys", user="do it", use_headroom=False)

    assert res.text == "hello world"
    assert res.stop_reason == "end_turn"  # real stop reason: truncation flag works over CLI
    assert res.cost_usd == pytest.approx(0.0123)
    assert res.tokens_before > 0 and res.tokens_before == res.tokens_after
    argv, kw = calls[0]
    assert argv[:2] == ["claude", "-p"] and "m1" in argv
    assert "--output-format" in argv and "json" in argv
    assert kw["input"] == "do it" and kw["timeout"] == 420.0


def test_timeout_retries_with_backoff_then_succeeds():
    state = {"n": 0}
    naps = []

    def runner(argv, **kw):
        state["n"] += 1
        if state["n"] < 3:
            raise subprocess.TimeoutExpired(cmd=argv, timeout=kw["timeout"])
        return envelope()

    client = CLITransportClient(attempts=3, runner=runner, sleeper=naps.append)
    assert client.complete(model="m", system="s", user="u", use_headroom=False).text == "the answer"
    assert state["n"] == 3
    assert naps == [2.0, 4.0]  # exponential backoff between attempts


def test_exhausted_attempts_raise_last_error():
    def runner(argv, **kw):
        raise subprocess.TimeoutExpired(cmd=argv, timeout=kw["timeout"])

    client = CLITransportClient(attempts=2, runner=runner, sleeper=lambda s: None)
    with pytest.raises(subprocess.TimeoutExpired):
        client.complete(model="m", system="s", user="u", use_headroom=False)


def test_nonzero_exit_raises_runtime_error():
    def runner(argv, **kw):
        return SimpleNamespace(returncode=1, stdout="", stderr="boom")

    client = CLITransportClient(attempts=1, runner=runner)
    with pytest.raises(RuntimeError, match="boom"):
        client.complete(model="m", system="s", user="u", use_headroom=False)


def test_refusal_envelope_raises_immediately_without_retry():
    # Measured live: refusals arrive on stdout with EXIT CODE 0. They are
    # deterministic — retrying is wasted spend, so exactly one call is made.
    calls = []

    def runner(argv, **kw):
        calls.append(argv)
        return envelope("API error", is_error=True, subtype="error_during_execution")

    client = CLITransportClient(attempts=3, runner=runner, sleeper=lambda s: None)
    with pytest.raises(TransportRefused):
        client.complete(model="m", system="s", user="u", use_headroom=False)
    assert len(calls) == 1


def test_api_error_text_with_success_envelope_still_refused():
    # Defense-in-depth against envelope drift: a "successful" envelope whose
    # result is the CLI's error banner must never become the answer.
    def runner(argv, **kw):
        return envelope("API Error: safeguards flagged this message")

    client = CLITransportClient(attempts=1, runner=runner)
    with pytest.raises(TransportRefused, match="safeguards"):
        client.complete(model="m", system="s", user="u", use_headroom=False)


def test_non_json_stdout_retries_then_raises():
    calls = []

    def runner(argv, **kw):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout="plain text, no envelope", stderr="")

    client = CLITransportClient(attempts=2, runner=runner, sleeper=lambda s: None)
    with pytest.raises(RuntimeError, match="non-JSON"):
        client.complete(model="m", system="s", user="u", use_headroom=False)
    assert len(calls) == 2


def test_extra_args_are_appended():
    calls = []

    def runner(argv, **kw):
        calls.append(argv)
        return envelope()

    CLITransportClient(runner=runner, extra_args=["--dangerously-skip-permissions"]).complete(
        model="m", system="s", user="u", use_headroom=False
    )
    assert calls[0][-1] == "--dangerously-skip-permissions"


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

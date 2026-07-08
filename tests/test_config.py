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


def test_research_mode_applies_doctrine_defaults():
    """--research defaults: Sonnet+ ladder, pinned OPUS judge, 3-vote median."""

    from types import SimpleNamespace

    from headroom_recursion.cli import build_config
    from headroom_recursion.config import OPUS, RESEARCH_LADDER

    def args(**over):
        base = dict(
            ladder=None, n=None, steps=None, threshold=None, temperature=None,
            judge_model=None, judge_votes=None, retrieval_k=None,
            retrieval_max_chars=None, max_calls=None, max_seconds=None,
            no_headroom=False, research=True,
        )
        base.update(over)
        return SimpleNamespace(**base)

    cfg = build_config(args())
    assert cfg.ladder == RESEARCH_LADDER
    assert cfg.judge_model == OPUS and cfg.judge_votes == 3

    cfg = build_config(args(judge_model="claude-haiku-4-5-20251001", judge_votes=1))
    assert cfg.judge_model == "claude-haiku-4-5-20251001" and cfg.judge_votes == 1


def test_trace_persist_writes_json_and_summary(tmp_path):
    from headroom_recursion.trace import RunTrace

    trace = RunTrace(problem="p", final_answer="42", halted=True, stop_reason="halt")
    path = trace.persist(str(tmp_path), stem="run-001")

    import json as _json

    with open(path) as fh:
        data = _json.load(fh)
    assert data["final_answer"] == "42"
    summary = (tmp_path / "run-001.summary.txt").read_text()
    assert "stop_reason : halt" in summary and "42" in summary

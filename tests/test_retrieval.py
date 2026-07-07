"""Retrieval layer: injection into prompts, per-step calls, and graceful failure."""

from __future__ import annotations

import asyncio

import pytest

from headroom_recursion import prompts
from headroom_recursion.config import RecurseConfig, Tier
from headroom_recursion.ladder import recurse
from headroom_recursion.retrieval import (
    NullRetriever,
    simple_local_embedding,
    build_claude_llm_func,
)
from tests.conftest import StubClient, StubRetriever


def one_tier(**kw) -> RecurseConfig:
    return RecurseConfig(ladder=(Tier("m0"),), **kw)


def test_no_retriever_is_a_noop(stub):
    trace = recurse("x", client=stub, config=one_tier(n=1, T=1))
    assert trace.steps[0].retrieved_snippets == 0
    # No RETRIEVED KNOWLEDGE block (header has a colon; the instruction phrase does not).
    assert all("RETRIEVED KNOWLEDGE:" not in u for _k, u in stub.prompts_seen)


def test_snippets_injected_into_prompts():
    stub = StubClient()
    ret = StubRetriever(snippets=["FACT: Ada Lovelace wrote the first algorithm."])
    trace = recurse("who wrote the first algorithm?", client=stub, config=one_tier(n=2, T=1, retriever=ret))

    assert trace.steps[0].retrieved_snippets == 1
    latent_prompts = [u for k, u in stub.prompts_seen if k == "latent"]
    assert latent_prompts and all("RETRIEVED KNOWLEDGE:" in u for u in latent_prompts)
    assert any("Ada Lovelace" in u for u in latent_prompts)
    # The answer-update prompt is grounded too.
    answer_prompts = [u for k, u in stub.prompts_seen if k == "answer"]
    assert any("Ada Lovelace" in u for u in answer_prompts)


def test_retrieval_runs_once_per_step():
    stub = StubClient()
    ret = StubRetriever()
    recurse("problem text", client=stub, config=one_tier(n=1, T=3, retriever=ret))
    assert len(ret.queries) == 3  # one retrieval per improvement step
    # Query is problem (+scratchpad) truncated to the configured budget.
    assert ret.queries[0][0].startswith("problem text")
    assert ret.queries[0][1] == 4  # default retrieval_k


def test_query_respects_char_budget():
    stub = StubClient()
    ret = StubRetriever()
    cfg = one_tier(n=1, T=1, retriever=ret, retrieval_query_chars=10)
    recurse("x" * 500, client=stub, config=cfg)
    assert len(ret.queries[0][0]) == 10


def test_retrieval_failure_is_swallowed():
    class Boom:
        def retrieve(self, query, *, k):
            raise RuntimeError("index offline")

    stub = StubClient()
    trace = recurse("x", client=stub, config=one_tier(n=1, T=1, retriever=Boom()))
    assert trace.steps[0].retrieved_snippets == 0  # loop still completes
    assert len(stub.calls) > 0


def test_null_retriever_returns_nothing():
    assert NullRetriever().retrieve("anything", k=5) == []


def test_format_context_block():
    assert prompts.format_context([]) == "\n"
    block = prompts.format_context(["one", "two"])
    assert "RETRIEVED KNOWLEDGE" in block and "[1] one" in block and "[2] two" in block


def test_simple_local_embedding_shape_and_determinism():
    emb = simple_local_embedding(dim=64)
    assert emb.dim == 64
    v1 = asyncio.run(emb.func(["hello world"]))
    v2 = asyncio.run(emb.func(["hello world"]))
    assert len(v1) == 1 and len(v1[0]) == 64
    assert v1 == v2  # deterministic
    # L2-normalized.
    assert abs(sum(x * x for x in v1[0]) - 1.0) < 1e-6


def test_build_claude_llm_func_adapts_signature():
    stub = StubClient()
    llm = build_claude_llm_func(stub, "m0")
    # LightRAG calls llm_model_func(prompt, system_prompt=..., history_messages=...).
    out = asyncio.run(llm("a prompt", system_prompt="sys", history_messages=[]))
    assert isinstance(out, str) and out  # returns text
    assert ("other", "a prompt") in stub.prompts_seen  # routed through ClaudeClient.complete
    assert stub.calls[-1][1] == "m0"  # on the requested model

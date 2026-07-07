"""Headroom integration: pass-through when off/absent, real compression when present."""

from __future__ import annotations

import sys
import types

from headroom_recursion import headroom


MSGS = [{"role": "user", "content": "x" * 400}]  # ~100 tokens by the 4-char estimate


def test_estimate_tokens():
    assert headroom.estimate_tokens(MSGS) == 100
    assert headroom.estimate_tokens([]) == 0


def test_disabled_is_passthrough():
    out, before, after = headroom.compress(MSGS, model="m", use_headroom=False)
    assert out is MSGS
    assert before == after == 100


def test_absent_library_is_passthrough(monkeypatch):
    # Force headroom_available() to report False regardless of environment.
    monkeypatch.setattr(headroom, "headroom_available", lambda: False)
    out, before, after = headroom.compress(MSGS, model="m", use_headroom=True)
    assert out is MSGS
    assert before == after == 100


def test_present_library_compresses(monkeypatch):
    calls = {}

    def fake_compress(messages, model=None):
        calls["model"] = model
        calls["n"] = len(messages)
        return [{"role": "user", "content": "x" * 40}]  # 10 tokens

    fake_mod = types.ModuleType("headroom")
    fake_mod.compress = fake_compress
    monkeypatch.setitem(sys.modules, "headroom", fake_mod)
    monkeypatch.setattr(headroom, "headroom_available", lambda: True)

    out, before, after = headroom.compress(MSGS, model="claude-haiku-4-5-20251001", use_headroom=True)

    assert calls == {"model": "claude-haiku-4-5-20251001", "n": 1}
    assert before == 100
    assert after == 10
    assert out[0]["content"] == "x" * 40


def test_compression_failure_falls_back(monkeypatch):
    def boom(messages, model=None):
        raise RuntimeError("headroom exploded")

    fake_mod = types.ModuleType("headroom")
    fake_mod.compress = boom
    monkeypatch.setitem(sys.modules, "headroom", fake_mod)
    monkeypatch.setattr(headroom, "headroom_available", lambda: True)

    out, before, after = headroom.compress(MSGS, model="m", use_headroom=True)
    assert out is MSGS  # reasoning is never broken by a compression error
    assert before == after == 100


def test_min_tokens_floor_skips_compression(monkeypatch):
    called = {}

    def fake_compress(messages, model=None):
        called["yes"] = True
        return [{"role": "user", "content": "tiny"}]

    fake_mod = types.ModuleType("headroom")
    fake_mod.compress = fake_compress
    monkeypatch.setitem(sys.modules, "headroom", fake_mod)
    monkeypatch.setattr(headroom, "headroom_available", lambda: True)

    # MSGS is ~100 tokens; below the 500-token floor the compressor is never touched.
    out, before, after = headroom.compress(MSGS, model="m", use_headroom=True, min_tokens=500)
    assert out is MSGS
    assert before == after == 100
    assert not called


def test_empty_compression_result_is_a_failure_not_a_saving(monkeypatch):
    fake_mod = types.ModuleType("headroom")
    fake_mod.compress = lambda messages, model=None: []  # "100% compression"
    monkeypatch.setitem(sys.modules, "headroom", fake_mod)
    monkeypatch.setattr(headroom, "headroom_available", lambda: True)

    out, before, after = headroom.compress(MSGS, model="m", use_headroom=True)
    assert out is MSGS  # never send Claude an empty message list
    assert before == after == 100


def test_async_compress_is_awaited(monkeypatch):
    async def acompress(messages, model=None):
        return [{"role": "user", "content": "x" * 20}]  # 5 tokens

    fake_mod = types.ModuleType("headroom")
    fake_mod.compress = acompress
    monkeypatch.setitem(sys.modules, "headroom", fake_mod)
    monkeypatch.setattr(headroom, "headroom_available", lambda: True)

    out, before, after = headroom.compress(MSGS, model="m", use_headroom=True)
    assert after == 5
    assert out[0]["content"] == "x" * 20

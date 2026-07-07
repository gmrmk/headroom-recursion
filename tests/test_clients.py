"""Model-agnostic backends: the protocol, the OpenAI client, and ladder overrides."""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace

from headroom_recursion.cli import build_config
from headroom_recursion.clients import CompletionClient
from tests.conftest import StubClient


def _fake_openai(monkeypatch, *, finish_reason="stop", text="hello"):
    """Install a fake `openai` module recording chat.completions.create calls."""

    calls = []

    class FakeCompletions:
        def create(self, **kw):
            calls.append(kw)
            choice = SimpleNamespace(
                message=SimpleNamespace(content=text), finish_reason=finish_reason
            )
            return SimpleNamespace(choices=[choice])

    class FakeOpenAI:
        def __init__(self, **kw):
            calls.append({"__init__": kw})
            self.chat = SimpleNamespace(completions=FakeCompletions())

    fake = types.ModuleType("openai")
    fake.OpenAI = FakeOpenAI
    monkeypatch.setitem(sys.modules, "openai", fake)
    return calls


def test_stub_satisfies_the_protocol():
    assert isinstance(StubClient(), CompletionClient)


def test_openai_client_maps_roles_and_params(monkeypatch):
    calls = _fake_openai(monkeypatch)
    from headroom_recursion.clients import OpenAIClient

    client = OpenAIClient(base_url="http://localhost:11434/v1", api_key="ollama")
    res = client.complete(
        model="llama3.2:3b", system="sys prompt", user="user prompt",
        max_tokens=99, temperature=0.3, use_headroom=False,
    )

    assert calls[0]["__init__"]["base_url"] == "http://localhost:11434/v1"
    create = calls[1]
    assert create["model"] == "llama3.2:3b"
    assert create["messages"][0] == {"role": "system", "content": "sys prompt"}
    assert create["messages"][1] == {"role": "user", "content": "user prompt"}
    assert create["max_tokens"] == 99 and create["temperature"] == 0.3
    assert res.text == "hello"
    assert isinstance(res.tokens_before, int) and res.tokens_before > 0


def test_openai_length_finish_maps_to_max_tokens(monkeypatch):
    _fake_openai(monkeypatch, finish_reason="length")
    from headroom_recursion.clients import OpenAIClient

    res = OpenAIClient(api_key="x").complete(model="m", system="s", user="u", use_headroom=False)
    assert res.stop_reason == "max_tokens"  # normalized so the truncation flag works


def _args(**over):
    base = dict(
        ladder=None, n=None, steps=None, threshold=None, temperature=None,
        judge_model=None, judge_votes=None, retrieval_k=None, retrieval_max_chars=None,
        max_calls=None, max_seconds=None, no_headroom=False,
    )
    base.update(over)
    return SimpleNamespace(**base)


def test_ladder_flag_overrides_models():
    cfg = build_config(_args(ladder="tiny-1, big-2 ,huge-3"))
    assert [t.model for t in cfg.ladder] == ["tiny-1", "big-2", "huge-3"]
    cfg.validate()  # arbitrary model names are legal


def test_default_ladder_is_claude():
    cfg = build_config(_args())
    assert all(m.startswith("claude-") for m in (t.model for t in cfg.ladder))

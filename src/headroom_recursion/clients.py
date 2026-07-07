"""Model backends. The recursion loop is model-agnostic by construction.

Everything in the loop (trm/ladder/halting) talks to a model exclusively through the
``CompletionClient`` protocol below — model names are opaque strings carried in
``Tier``s. Any provider works if you can implement one method.

Shipped backends:
* ``claude.ClaudeClient`` — the Anthropic SDK (the default).
* ``OpenAIClient`` (here) — the OpenAI SDK, which also covers every OpenAI-compatible
  server: Ollama, vLLM, LM Studio, llama.cpp, OpenRouter, ... — point ``base_url`` at
  it and put your model names in the ladder.

Example (local Ollama):

    from headroom_recursion import RecurseConfig, Tier, recurse
    from headroom_recursion.clients import OpenAIClient

    client = OpenAIClient(base_url="http://localhost:11434/v1", api_key="ollama")
    cfg = RecurseConfig(ladder=(Tier("llama3.2:3b"), Tier("llama3.3:70b")))
    trace = recurse("...", client=client, config=cfg)
"""

from __future__ import annotations

import os
from typing import Optional, Protocol, runtime_checkable

from headroom_recursion import headroom
from headroom_recursion.claude import CallResult


@runtime_checkable
class CompletionClient(Protocol):
    """What the recursion loop needs from a model backend — one method."""

    def complete(
        self,
        *,
        model: str,
        system: str,
        user: str,
        max_tokens: int = 2048,
        temperature: float = 0.7,
        use_headroom: bool = True,
    ) -> CallResult: ...


class OpenAIClient:
    """A ``CompletionClient`` backed by the OpenAI SDK (or anything API-compatible)."""

    def __init__(
        self,
        *,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: Optional[float] = None,
        max_retries: Optional[int] = None,
        headroom_min_tokens: int = 0,
    ):
        # Lazy import: the openai package is only needed if this backend is used.
        from openai import OpenAI

        kwargs = {}
        if api_key or os.environ.get("OPENAI_API_KEY"):
            kwargs["api_key"] = api_key or os.environ["OPENAI_API_KEY"]
        if base_url:
            kwargs["base_url"] = base_url
        if timeout is not None:
            kwargs["timeout"] = timeout
        if max_retries is not None:
            kwargs["max_retries"] = max_retries
        self._client = OpenAI(**kwargs)
        self._headroom_min_tokens = headroom_min_tokens

    def complete(
        self,
        *,
        model: str,
        system: str,
        user: str,
        max_tokens: int = 2048,
        temperature: float = 0.7,
        use_headroom: bool = True,
    ) -> CallResult:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        sent, before, after = headroom.compress(
            messages, model=model, use_headroom=use_headroom, min_tokens=self._headroom_min_tokens
        )

        resp = self._client.chat.completions.create(
            model=model,
            messages=sent,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        choice = resp.choices[0]
        finish = str(getattr(choice, "finish_reason", "") or "")
        return CallResult(
            text=(choice.message.content or "").strip(),
            tokens_before=before,
            tokens_after=after,
            # Normalize to the Anthropic vocabulary the loop's truncation flag checks.
            stop_reason="max_tokens" if finish == "length" else finish,
        )

"""Model backends. The recursion loop is model-agnostic by construction.

Everything in the loop (trm/ladder/halting) talks to a model exclusively through the
``CompletionClient`` protocol below — model names are opaque strings carried in
``Tier``s. Any provider works if you can implement one method.

Shipped backends:
* ``claude.ClaudeClient`` — the Anthropic SDK (the default).
* ``OpenAIClient`` (here) — the OpenAI SDK, which also covers every OpenAI-compatible
  server: Ollama, vLLM, LM Studio, llama.cpp, OpenRouter, ... — point ``base_url`` at
  it and put your model names in the ladder.
* ``CLITransportClient`` (here) — headless ``claude -p``; uses an existing Claude
  Code login instead of an API key. Retries per call: single stalled CLI calls
  were the leading cause of dead runs before this client existed.

Example (local Ollama):

    from headroom_recursion import RecurseConfig, Tier, recurse
    from headroom_recursion.clients import OpenAIClient

    client = OpenAIClient(base_url="http://localhost:11434/v1", api_key="ollama")
    cfg = RecurseConfig(ladder=(Tier("llama3.2:3b"), Tier("llama3.3:70b")))
    trace = recurse("...", client=client, config=cfg)
"""

from __future__ import annotations

import os
import subprocess
from typing import Callable, Optional, Protocol, runtime_checkable

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


class CLITransportClient:
    """A ``CompletionClient`` backed by headless ``claude -p``.

    Uses an existing Claude Code session login — no API key required. Each
    completion spawns one CLI process; because a single process occasionally
    stalls, every call gets ``attempts`` tries with a per-attempt ``timeout_s``.
    Headroom compression runs in front of the CLI exactly as it does for the
    SDK client, and the trace gets Headroom's token accounting.

    ``runner`` is injectable for tests (same signature as ``subprocess.run``).
    """

    def __init__(
        self,
        *,
        attempts: int = 3,
        timeout_s: float = 420.0,
        headroom_min_tokens: int = 0,
        executable: str = "claude",
        runner: Optional[Callable] = None,
    ):
        if attempts < 1:
            raise ValueError(f"attempts must be >= 1 (got {attempts})")
        if timeout_s <= 0:
            raise ValueError(f"timeout_s must be > 0 (got {timeout_s})")
        self._attempts = attempts
        self._timeout_s = timeout_s
        self._headroom_min_tokens = headroom_min_tokens
        self._executable = executable
        self._runner = runner or subprocess.run

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
        messages = [{"role": "user", "content": user}]
        sent, before, after = headroom.compress(
            messages, model=model, use_headroom=use_headroom, min_tokens=self._headroom_min_tokens
        )
        send_text = user
        if sent and isinstance(sent[0].get("content"), str):
            send_text = sent[0]["content"]

        last_exc: Optional[BaseException] = None
        for _ in range(self._attempts):
            try:
                out = self._runner(
                    [self._executable, "-p", "--model", model, "--system-prompt", system],
                    input=send_text,
                    capture_output=True,
                    text=True,
                    timeout=self._timeout_s,
                )
            except subprocess.TimeoutExpired as exc:
                last_exc = exc
                continue
            if out.returncode != 0:
                last_exc = RuntimeError(
                    f"claude CLI failed ({model}): {(out.stderr or '').strip()[:300]}"
                )
                continue
            return CallResult(
                text=(out.stdout or "").strip(), tokens_before=before, tokens_after=after
            )
        assert last_exc is not None
        raise last_exc

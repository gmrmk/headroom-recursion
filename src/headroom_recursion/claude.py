"""Thin Claude API wrapper with Headroom compression baked into every call.

The recursion core talks to Claude only through the ``complete`` method here, which:
  1. builds the message list,
  2. runs it through Headroom (library mode) before sending,
  3. calls ``messages.create``,
  4. returns the text plus the before/after token counts for the trace.

Tests substitute a stub with the same ``complete`` signature, so no network is
needed to exercise the loop.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from headroom_recursion import headroom


@dataclass
class CallResult:
    text: str
    tokens_before: int
    tokens_after: int
    # The API's stop reason (e.g. "end_turn", "max_tokens"). "max_tokens" means the
    # output was cut off mid-thought — the loop flags such steps as truncated.
    stop_reason: str = ""
    # Real dollar cost of the call when the backend reports one (the claude CLI's
    # JSON envelope does); 0.0 when unknown. Summed by cost meters, never billed from.
    cost_usd: float = 0.0


class ClaudeClient:
    """Wraps the Anthropic SDK. Construct once; reuse across the whole run."""

    def __init__(
        self,
        *,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: Optional[float] = None,
        max_retries: Optional[int] = None,
        headroom_min_tokens: int = 0,
    ):
        # Import lazily so the package imports (and unit tests) work without the SDK.
        from anthropic import Anthropic

        kwargs = {}
        if api_key or os.environ.get("ANTHROPIC_API_KEY"):
            kwargs["api_key"] = api_key or os.environ["ANTHROPIC_API_KEY"]
        # base_url lets you point at a running `headroom proxy` instead of using
        # library-mode compression.
        if base_url:
            kwargs["base_url"] = base_url
        # Only forward when set, so the SDK's own defaults (retries with backoff,
        # request timeout) apply otherwise.
        if timeout is not None:
            kwargs["timeout"] = timeout
        if max_retries is not None:
            kwargs["max_retries"] = max_retries
        self._client = Anthropic(**kwargs)
        # Skip compression for prompts below this size — negligible savings, and a
        # lossy pass over a small prompt (mostly the problem statement) is all risk.
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
        messages = [{"role": "user", "content": user}]
        sent, before, after = headroom.compress(
            messages, model=model, use_headroom=use_headroom, min_tokens=self._headroom_min_tokens
        )

        resp = self._client.messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system,
            messages=sent,
        )
        return CallResult(
            text=_text_of(resp),
            tokens_before=before,
            tokens_after=after,
            stop_reason=str(getattr(resp, "stop_reason", "") or ""),
        )


def _text_of(resp) -> str:
    """Extract the concatenated text from an Anthropic messages response."""

    parts = []
    for block in getattr(resp, "content", []) or []:
        text = getattr(block, "text", None)
        if text is not None:
            parts.append(text)
    return "".join(parts).strip()

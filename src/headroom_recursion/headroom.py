"""Headroom context-compression integration.

Uses the real ``headroom-ai`` library API (``from headroom import compress``) in
"library mode": every outbound message list is compressed before it reaches Claude.
Compression is reversible on Headroom's side (the model can call ``headroom_retrieve``),
so nothing the model needs is lost.

The dependency is optional. If ``headroom-ai`` is not installed, or the caller sets
``use_headroom=False``, compression is a transparent pass-through and the reported
token savings are zero.

Proxy mode is the documented alternative (see ``references/headroom-setup.md``):
run ``headroom proxy --port 8787`` and point the Anthropic client's ``base_url`` at
it — then no per-call compression is needed here.
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any

Messages = list[dict[str, Any]]


def headroom_available() -> bool:
    try:
        import headroom  # noqa: F401
    except Exception:
        return False
    return True


def estimate_tokens(messages: Messages) -> int:
    """Rough token estimate (~4 chars/token) over message text.

    Deliberately dependency-free so the trace has a number even when the Anthropic
    token counter or Headroom's own accounting is unavailable. It is an estimate,
    used only for the before/after savings display — not for billing.
    """

    chars = 0
    for m in messages:
        content = m.get("content", "")
        if isinstance(content, str):
            chars += len(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    chars += len(str(block.get("text", "")))
                else:
                    chars += len(str(block))
    return (chars + 3) // 4


def _run_maybe_async(value: Any) -> Any:
    """Return a value, awaiting it first if ``compress`` handed back a coroutine."""

    if inspect.isawaitable(value):
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            # We're already inside an event loop; run the coroutine on a fresh one
            # in a worker thread to avoid re-entrancy errors. The timeout keeps a
            # wedged compressor from hanging the whole run — the resulting
            # TimeoutError degrades to passthrough in ``compress``.
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                return ex.submit(asyncio.run, value).result(timeout=60)
        return asyncio.run(value)
    return value


def compress(
    messages: Messages, model: str, *, use_headroom: bool, min_tokens: int = 0
) -> tuple[Messages, int, int]:
    """Compress ``messages`` for ``model``.

    Returns ``(out_messages, tokens_before, tokens_after)``. When Headroom is
    disabled or unavailable, ``out_messages is messages`` and before == after.
    ``min_tokens`` skips compression for small prompts, where the savings are
    negligible and a lossy pass is all downside.
    """

    before = estimate_tokens(messages)

    if not use_headroom or not headroom_available() or before < min_tokens:
        return messages, before, before

    try:
        from headroom import compress as hr_compress  # type: ignore

        # The harness sends a single fat user message per call, but Headroom's
        # default profile is tuned for coding agents: user messages are skipped and
        # the most recent messages are protected — under it, NOTHING we send would
        # ever be compressed. Opt user messages in and unprotect our only message.
        kwargs = {}
        try:
            from headroom import CompressConfig  # type: ignore

            kwargs["config"] = CompressConfig(compress_user_messages=True, protect_recent=0)
        except Exception:
            pass
        try:
            result = _run_maybe_async(hr_compress(messages, model=model, **kwargs))
        except TypeError:
            # Older/other builds without the config kwarg.
            result = _run_maybe_async(hr_compress(messages, model=model))
    except Exception:
        # Never let a compression hiccup break reasoning — fall back to raw messages.
        return messages, before, before

    # Headroom returns the compressed message list (some builds return a
    # CompressResult with ``.messages`` plus its own token accounting); accept both.
    # An empty result is a failure, not a 100% compression — never send Claude nothing.
    out = getattr(result, "messages", result)
    if not isinstance(out, list) or not out:
        return messages, before, before

    # Prefer Headroom's real token counts over our 4-chars/token estimate.
    hb = getattr(result, "tokens_before", None)
    ha = getattr(result, "tokens_after", None)
    if isinstance(hb, int) and isinstance(ha, int) and hb > 0:
        return out, hb, ha
    return out, before, estimate_tokens(out)

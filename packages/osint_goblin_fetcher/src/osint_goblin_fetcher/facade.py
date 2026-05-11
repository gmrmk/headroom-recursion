"""fetch() / afetch() - the unified fetch facade.

Wraps Scrapling's 3 fetcher classes. Sync entrypoint plus an async wrapper
(via asyncio.to_thread for the sync Scrapling APIs) so Dramatiq actors that
spawn asyncio.TaskGroup (Diego sec.B2) don't block their event loop.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

from .result import FetcherTier, FetchResult

if TYPE_CHECKING:
    from collections.abc import Mapping


def _normalize(page: Any, *, tier: FetcherTier, url: str, elapsed_ms: int) -> FetchResult:
    """Three Adaptor shapes -> one FetchResult.

    Cascading attribute lookup handles Scrapling 0.4.7's per-tier inconsistency:
      - Fetcher:         page.text is the rendered HTML
      - DynamicFetcher:  page.text often empty; html on .html_content
      - StealthyFetcher: same as DynamicFetcher

    Never raises on missing attrs -- empty defaults for absent fields. The
    caller checks `result.ok` to decide if the response is useful.
    """
    body_bytes_raw = getattr(page, "body", None) or getattr(page, "body_bytes", None)
    body_text_raw = getattr(page, "text", None)

    # If the tier-1 .text is empty bytes/str or we're on a JS-rendered tier,
    # fall back to .html_content. Scrapling exposes the rendered HTML there.
    if not body_text_raw or (isinstance(body_text_raw, bytes | bytearray) and not body_text_raw):
        body_text_raw = getattr(page, "html_content", None) or ""

    body_text_str: str
    if isinstance(body_text_raw, bytes | bytearray):
        body_text_str = bytes(body_text_raw).decode("utf-8", errors="replace")
    else:
        body_text_str = body_text_raw or ""

    if not body_bytes_raw:
        body_bytes_raw = body_text_str.encode("utf-8") if body_text_str else b""

    return FetchResult(
        url=url,
        final_url=str(getattr(page, "url", url) or url),
        status=int(getattr(page, "status", 0) or 0),
        headers=dict(getattr(page, "headers", {}) or {}),
        body_bytes=bytes(body_bytes_raw),
        body_text=body_text_str,
        tier=tier,
        elapsed_ms=elapsed_ms,
    )


def fetch(
    url: str,
    *,
    tier: FetcherTier = "fetcher",
    timeout_s: float = 30.0,
    headers: Mapping[str, str] | None = None,
) -> FetchResult:
    """Sync fetch via the named Scrapling tier.

    Returns a FetchResult. On exception, returns a FetchResult with status=0
    and error=<exception class name>. The caller's escalation policy (CF
    interstitial detection, retry rules) lives outside this function.
    """
    t0 = time.time()
    try:
        page = _dispatch_sync(url, tier, timeout_s, dict(headers) if headers else None)
        return _normalize(page, tier=tier, url=url, elapsed_ms=int((time.time() - t0) * 1000))
    except Exception as e:
        return FetchResult(
            url=url,
            final_url=url,
            status=0,
            headers={},
            body_bytes=b"",
            body_text="",
            tier=tier,
            elapsed_ms=int((time.time() - t0) * 1000),
            error=type(e).__name__,
        )


async def afetch(
    url: str,
    *,
    tier: FetcherTier = "fetcher",
    timeout_s: float = 30.0,
    headers: Mapping[str, str] | None = None,
) -> FetchResult:
    """Async wrapper. Runs the sync Scrapling call in a thread so the calling
    asyncio event loop stays responsive (Diego sec.B2 -- one actor = one
    investigation step; asyncio.TaskGroup inside a Dramatiq actor)."""
    return await asyncio.to_thread(fetch, url, tier=tier, timeout_s=timeout_s, headers=headers)


def _dispatch_sync(
    url: str, tier: FetcherTier, timeout_s: float, headers: dict[str, str] | None
) -> Any:
    """Per-tier Scrapling import + call. Late import keeps optional-dep cost off
    the package's import path -- only the tier you actually use loads its deps."""
    if tier == "fetcher":
        from scrapling.fetchers import Fetcher

        return Fetcher.get(
            url,
            stealthy_headers=True,
            follow_redirects=True,
            timeout=timeout_s,
            headers=headers,
        )
    if tier == "dynamic":
        from scrapling.fetchers import DynamicFetcher

        return DynamicFetcher.fetch(
            url, headless=True, network_idle=True, timeout=int(timeout_s * 1000)
        )
    if tier == "stealthy":
        from scrapling.fetchers import StealthyFetcher

        return StealthyFetcher.fetch(
            url, headless=True, network_idle=True, timeout=int(timeout_s * 1000)
        )
    if tier == "camoufox":
        # Camoufox is the opt-in 4th tier (UPGRADE-PATH.md). Not in M1; gated
        # behind explicit configuration. For now, raise so callers see the
        # tier is not yet wired up rather than silently fall back.
        raise NotImplementedError("camoufox tier not implemented in M1")
    raise ValueError(f"unknown tier: {tier!r}")

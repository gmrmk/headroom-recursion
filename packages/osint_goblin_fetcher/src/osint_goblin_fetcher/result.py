"""FetchResult dataclass - the single canonical return type for the fetch facade.

Diego phase3/04-backend-data-engineer.md sec.3 spec. Immutable. Carries enough
metadata for the evidence pipeline (ADR-0006) to chain-emit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

FetcherTier = Literal["fetcher", "dynamic", "stealthy", "camoufox"]

# Cloudflare interstitial markers (from empirical/01-scrapling-smoke.md).
# Keep this tight; false-positive escalations to StealthyFetcher are
# 6-10x latency cost (see UPGRADE-PATH.md latency baselines).
CF_INTERSTITIAL_MARKERS: tuple[str, ...] = (
    "Just a moment",
    "cf-challenge",
    "challenge-platform",
    "_cf_chl_",
    "Checking your browser",
    "Attention Required",
)
CF_INTERSTITIAL_STATUSES: frozenset[int] = frozenset({403, 503})


@dataclass(frozen=True, slots=True)
class FetchResult:
    """Normalized result from any Scrapling fetcher tier.

    Empirical Item 1 cross-cutting finding: Scrapling's Adaptor object exposes
    rendered HTML on different attributes per tier. This dataclass is the
    single shape every downstream actor consumes.
    """

    url: str  # the URL we requested
    final_url: str  # after redirects
    status: int  # HTTP status; 0 if request never completed
    headers: dict[str, str]  # response headers (best-effort; some tiers omit)
    body_bytes: bytes  # raw bytes; falls back to encoded text if tier didn't expose
    body_text: str  # decoded text/HTML; '' if not text
    tier: FetcherTier  # which tier actually fetched this
    elapsed_ms: int  # wall-clock of the fetch call
    error: str | None = field(default=None)  # error class name if failed; None on success

    @property
    def ok(self) -> bool:
        """True if the fetch returned an HTTP response (any status). False on exception."""
        return self.error is None and self.status > 0


def is_interstitial(result: FetchResult) -> bool:
    """Heuristic: did this look like a CF/anti-bot challenge page?

    Used by callers (tool_runner actor) to decide whether to escalate to a
    higher tier. Conservative: false-positive cost = 6-10x latency; false-
    negative cost = junk evidence stored as truth.
    """
    if result.status in CF_INTERSTITIAL_STATUSES:
        return True
    if not result.body_text:
        return False
    return any(m in result.body_text for m in CF_INTERSTITIAL_MARKERS)

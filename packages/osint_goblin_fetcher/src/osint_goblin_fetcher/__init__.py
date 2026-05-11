"""osint_goblin_fetcher -- Scrapling 4-tier fetch facade.

The single fetching primitive for OSINT GOBLIN (ADR-0005). Wraps Scrapling's
3 fetcher classes behind a unified FetchResult so downstream actors don't see
the Adaptor body-capture inconsistency (empirical/01-scrapling-smoke.md Item 1
cross-cutting finding).

Quickstart:

    from osint_goblin_fetcher import fetch
    r = fetch("https://en.wikipedia.org/wiki/OSINT")
    assert r.status == 200
    assert len(r.body_text) > 1024

Async:

    from osint_goblin_fetcher import afetch
    r = await afetch("https://example.com")

Tier escalation is policy (CF detection, retry rules) -- not done here. The
caller decides; the facade just delivers what each tier returns. The
`is_interstitial` helper lets callers detect Cloudflare challenge pages.
"""

from .facade import afetch, fetch
from .result import FetcherTier, FetchResult, is_interstitial

__all__ = ["FetchResult", "FetcherTier", "afetch", "fetch", "is_interstitial"]

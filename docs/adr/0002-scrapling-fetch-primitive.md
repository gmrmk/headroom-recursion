# ADR-0002: Scrapling as the single fetch primitive

- **Status:** accepted
- **Date:** 2026-05-10
- **Deciders:** architect, backend, investigator
- **Tags:** stack, fetch, opsec

## Context

OSINT collection touches a long tail of targets: static HTML, JS-rendered SPAs, Cloudflare-fronted sites, Akamai-fronted sites, anti-bot Patchright-class adversaries, and `.onion` services. Naïve `requests` covers ~30% of the surface. The Phase 1b investigation surveyed Scrapling 0.4.7 (BSD-3, not MIT as initially memoed) against `httpx`, `curl_cffi`, raw Playwright, and Camoufox standalone. Empirical Item 1 (`empirical/01-scrapling-smoke.md`) ran the four-tier ladder against 5 real targets across 3 tiers: 14/15 reaches, 1 LinkedIn `DynamicFetcher` flake recovered by the next tier. Latencies (`Fetcher` 900ms / `DynamicFetcher` 5100ms / `StealthyFetcher` 6000ms median) inform the tier-aware UX loading shapes.

Scrapling's Adaptor body-capture API is **inconsistent across tiers** (`Fetcher.text` works; `DynamicFetcher.text` returned 0 bytes in our run). This is a known wart that the facade must normalize.

## Decision

All outbound HTTP/HTTPS/SOCKS traffic flows through one `fetch()` facade backed by **Scrapling 0.4.7**. The facade exposes four tiers, escalation policy, and a unified `FetchResult` dataclass:

```python
@dataclass
class FetchResult:
    body: bytes
    text: str
    status: int
    tier: Literal["fetcher", "dynamic", "stealthy", "camoufox"]
    headers: dict
    final_url: str
```

Tiers and intended targets:

- `Fetcher` (~900ms) — static HTML, JSON APIs.
- `DynamicFetcher` (~3–5s) — JS-rendered, no anti-bot.
- `StealthyFetcher` / Patchright (~6–30s) — anti-bot, CF js_challenge.
- `Camoufox` (10–30s, opt-in) — hardened anti-bot, e.g. some Turnstile cases.

Direct use of `requests`, `httpx`, raw Playwright, or `curl_cffi` from `api/`, `worker/`, or `adapters/` is forbidden. CI lint `lint-direct-http-imports` rejects.

Tor reach is via `socks5h://localhost:9050` (Scrapling reaches `.onion` natively because `curl_cffi` supports it). JS-rendered onion sites use a bespoke Tor Browser path documented in `docs/security/tor-opsec.md`.

## Consequences

- **Positive.** One place to apply rate limits, per-engine governors, OPSEC headers, and circuit rotation. One place to surface tier-aware UX loading shapes (`docs/explanation/tier-aware-ux.md`). One place to normalize the Adaptor body-capture quirk.
- **Positive.** Maigret-ScraplingChecker is ~120 LOC (Item 2), proving the adapter pattern.
- **Negative.** Lock-in to Scrapling versioning. Mitigated by pinning to 0.4.7 with an explicit upgrade gate; the body-capture API change between Fetcher and Dynamic/Stealthy already shows this risk surface.
- **Negative.** `StealthyFetcher` 30s+ latencies on OpenSea-class targets are a real productivity bottleneck. Mitigated by cancellable tier-badge dialog with `cmd-.` and mandatory defensibility copy in the chain artifact ("purpose: bypass cf_js_challenge").
- **Neutral.** Camoufox is opt-in per investigation. CF Turnstile bypass at 2026 is **unverified** (Item 1 didn't trigger a real challenge); a known-Turnstile target run is staged before M1 commit.

## References

- `INTEGRATION-SPEC.md` §6 (tier-aware loading + embedded CAPTCHA)
- `CONSOLIDATED-ROADMAP.md` §1, §3 (Item 1, Item 2)
- `empirical/01-scrapling-smoke.md`
- `empirical/02-maigret-scrapling-feasibility.md`
- ADR-0003 (Dramatiq actor that calls this facade)

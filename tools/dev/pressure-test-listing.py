"""Pressure test: VRBO + TripAdvisor failure characterization.

Systematic data collection on why our humanized fetcher gets 429/403
from major US-CDN-fronted travel platforms. Documents the failure
modes so we can match solutions to root causes.

Test matrix (per platform):
  T1: cold humanized fetcher (fresh instance, fresh session, warm-up runs)
  T2: T1 + 30s wait before target fetch (give cookies + browser-state
       a believable settle period)
  T3: warm fetcher (same instance, already-warmed platform, second
       deep-link within session)
  T4: bare StealthyFetcher (no warm-up, no session) -- baseline comparison
  T5: bare httpx with realistic UA + referer -- pre-humanize baseline

For each cell: status, body_len, top markers found in body (Cloudflare
challenge, Akamai BMA, Imperva, recaptcha, hCaptcha, generic 403, Bot or
Not, etc.). Output a single structured report.
"""

from __future__ import annotations

import dataclasses
import sys
import time
from pathlib import Path

WORKERS_SRC = Path(__file__).resolve().parents[2] / "apps" / "workers" / "src"
if WORKERS_SRC.exists():
    sys.path.insert(0, str(WORKERS_SRC))


# Markers that identify what kind of block is happening. Ordered most-
# specific first; first match wins.
_MARKERS: tuple[tuple[str, str], ...] = (
    ("Bot or Not", "Imperva Bot or Not challenge page"),
    ("Pardon Our Interruption", "Akamai BMA static challenge"),
    ("Access Denied", "Akamai EdgeKV reject"),
    ("Ray ID", "Cloudflare challenge"),
    ("Just a moment", "Cloudflare interstitial"),
    ("captcha-delivery.com", "DataDome captcha"),
    ("g-recaptcha", "Google reCAPTCHA"),
    ("h-captcha", "hCaptcha"),
    ("Turnstile", "Cloudflare Turnstile"),
    ("DDoS protection by Cloudflare", "Cloudflare DDoS shield"),
    ("Sucuri WebSite Firewall", "Sucuri WAF"),
    ("PerimeterX", "PerimeterX bot defense"),
    ("__cfduid", "Cloudflare cookie (generic)"),
    ("HTTP ERROR 403", "Server-level 403"),
    ("HTTP ERROR 429", "Server-level 429"),
    ("rate limit", "Generic rate-limit text"),
)


@dataclasses.dataclass
class TestResult:
    cell: str
    platform: str
    url: str
    elapsed_s: float
    status: int
    body_len: int
    markers: list[str]
    interpretation: str


def _interpret(status: int, body: str) -> tuple[list[str], str]:
    """Return (matched_markers, one-line interpretation)."""
    body_l = body[:5000]  # only scan first 5KB; challenge pages are tiny
    hits: list[str] = []
    for needle, label in _MARKERS:
        if needle in body_l:
            hits.append(label)
    if status == 200 and not hits and len(body) > 50_000:
        return ([], "OK -- full page returned")
    if status == 200 and not hits:
        return ([], "200 but suspicious -- short body, no challenge markers")
    if status in (429, 503) and hits:
        return (hits, f"rate-limited / soft-banned via {hits[0]}")
    if status == 403 and hits:
        return (hits, f"hard-blocked via {hits[0]}")
    if status == 403:
        return ([], "403 with no recognizable challenge marker")
    return (hits, f"status={status} mixed signal")


def _scrapling_fetch(url: str) -> tuple[float, int, str]:
    """Bare StealthyFetcher -- baseline (no warm-up, no session)."""
    from scrapling.fetchers import StealthyFetcher

    t0 = time.monotonic()
    try:
        page = StealthyFetcher.fetch(url, headless=True, network_idle=True, timeout=90_000)
        body = getattr(page, "html_content", None) or getattr(page, "text", "") or ""
        if isinstance(body, bytes | bytearray):
            body = bytes(body).decode("utf-8", errors="replace")
        return (time.monotonic() - t0, int(getattr(page, "status", 0) or 0), body)
    except Exception as e:
        return (time.monotonic() - t0, 0, f"exception:{type(e).__name__}")


def _httpx_fetch(url: str) -> tuple[float, int, str]:
    """Bare httpx with realistic UA + referer -- pre-humanize baseline."""
    import httpx

    t0 = time.monotonic()
    try:
        with httpx.Client(
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
                ),
                "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"),
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://www.google.com/",
            },
            follow_redirects=True,
            timeout=30.0,
        ) as c:
            r = c.get(url)
            return (time.monotonic() - t0, r.status_code, r.text)
    except Exception as e:
        return (time.monotonic() - t0, 0, f"exception:{type(e).__name__}")


def _humanized_fetch(
    url: str,
    platform: str,
    *,
    settle_s: float = 0.0,
    investigation_id: str = "pressure-test",
    reuse_fetcher: object | None = None,
) -> tuple[float, int, str, object]:
    """Humanized fetcher; optionally settle N seconds after warm-up
    before hitting the target. Returns (elapsed, status, body, fetcher)
    where fetcher is the instance (callers can reuse for T3)."""
    from osint_goblin_workers.humanize import HumanizedFetcher

    fetcher = reuse_fetcher or HumanizedFetcher(investigation_id=investigation_id)
    t0 = time.monotonic()
    try:
        if settle_s > 0 and reuse_fetcher is None:
            # Run warm-up explicitly via a dummy fetch to the homepage,
            # then settle, then hit the target.
            fetcher._ensure_browser()
            fetcher._run_warmup(platform, 120.0)
            fetcher._state.warmed_platforms.add(platform)
            time.sleep(settle_s)
        status, body = fetcher.fetch(
            url, platform=platform, jitter=False, synthetic_interaction=True, timeout_s=120.0
        )
        return (time.monotonic() - t0, status, body, fetcher)
    except Exception as e:
        return (time.monotonic() - t0, 0, f"exception:{type(e).__name__}", fetcher)


def _run_cell(cell: str, platform: str, url: str, runner: callable, **kwargs) -> TestResult:
    elapsed, status, body, *_ = runner(url, **kwargs)
    if isinstance(body, str) and body.startswith("exception:"):
        markers, interp = [], body
    else:
        markers, interp = _interpret(status, body)
    return TestResult(
        cell=cell,
        platform=platform,
        url=url,
        elapsed_s=elapsed,
        status=status,
        body_len=len(body) if isinstance(body, str) else 0,
        markers=markers,
        interpretation=interp,
    )


def run_matrix() -> list[TestResult]:
    targets = [
        ("vrbo", "https://www.vrbo.com/1682245"),
        ("tripadvisor", "https://www.tripadvisor.com/VacationRentals"),
    ]
    results: list[TestResult] = []

    for platform, url in targets:
        print(f"\n=== {platform} @ {url} ===")

        # T1: cold humanized
        print("  [T1] cold humanized fetch...", flush=True)
        results.append(
            _run_cell(
                "T1-cold-humanized",
                platform,
                url,
                lambda u, p=platform: _humanized_fetch(u, p, investigation_id=f"pt-cold-{p}-1")[:3],
            )
        )

        # T2: humanized with 30s settle between warm-up and target
        print("  [T2] humanized + 30s settle...", flush=True)
        results.append(
            _run_cell(
                "T2-humanized-30s-settle",
                platform,
                url,
                lambda u, p=platform: _humanized_fetch(
                    u, p, settle_s=30.0, investigation_id=f"pt-settle-{p}-1"
                )[:3],
            )
        )

        # T3: warm fetcher second deep-link (session continuity test)
        print("  [T3] warm fetcher, 2nd deep-link in session...", flush=True)
        from osint_goblin_workers.humanize import HumanizedFetcher

        warm = HumanizedFetcher(investigation_id=f"pt-warm-{platform}-1")
        try:
            # First fetch warms the platform.
            _humanized_fetch(url, platform, reuse_fetcher=warm)
            time.sleep(5)
            # Second fetch on same warm session.
            elapsed, status, body, _ = _humanized_fetch(url, platform, reuse_fetcher=warm)
            markers, interp = _interpret(status, body if isinstance(body, str) else "")
            results.append(
                TestResult(
                    "T3-warm-2nd-deep-link",
                    platform,
                    url,
                    elapsed,
                    status,
                    len(body) if isinstance(body, str) else 0,
                    markers,
                    interp,
                )
            )
        finally:
            warm.shred()

        # T4: bare StealthyFetcher baseline
        print("  [T4] bare StealthyFetcher...", flush=True)
        results.append(_run_cell("T4-bare-stealthy", platform, url, lambda u: _scrapling_fetch(u)))

        # T5: bare httpx pre-humanize baseline
        print("  [T5] bare httpx...", flush=True)
        results.append(_run_cell("T5-bare-httpx", platform, url, lambda u: _httpx_fetch(u)))

    return results


def print_report(results: list[TestResult]) -> None:
    print("\n\n========== PRESSURE TEST REPORT ==========")
    by_platform: dict[str, list[TestResult]] = {}
    for r in results:
        by_platform.setdefault(r.platform, []).append(r)

    for platform, rows in by_platform.items():
        print(f"\n--- {platform.upper()} ---")
        for r in rows:
            markers = "; ".join(r.markers) if r.markers else "-"
            print(
                f"  {r.cell:30} status={r.status:3} len={r.body_len:>8} "
                f"elapsed={r.elapsed_s:5.1f}s  markers=[{markers}]  -> {r.interpretation}"
            )


if __name__ == "__main__":
    results = run_matrix()
    print_report(results)

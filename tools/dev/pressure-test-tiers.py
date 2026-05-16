"""Pressure test the full humanize stack against VRBO + TripAdvisor.

Tests each browser tier (patchright / rebrowser / camoufox / firecrawl)
against the IP-burnt VRBO + TripAdvisor URLs that failed in the prior
pressure test. Documents which tier(s) recover what success rate.

Firecrawl requires OSINT_FIRECRAWL_API_KEY -- when unset, that cell
reports "skipped (no api key)" instead of failing.
"""

from __future__ import annotations

import os
import sys
import time
import traceback
from pathlib import Path

WORKERS_SRC = Path(__file__).resolve().parents[2] / "apps" / "workers" / "src"
if WORKERS_SRC.exists():
    sys.path.insert(0, str(WORKERS_SRC))

_OUT_DIR = Path(__file__).resolve().parent / "tier-pressure-bodies"
_OUT_DIR.mkdir(exist_ok=True)


_MARKERS: tuple[tuple[str, str], ...] = (
    ("Bot or Not", "Imperva Bot or Not"),
    ("Pardon Our Interruption", "Akamai BMA"),
    ("captcha-delivery.com", "DataDome"),
    ("Just a moment", "Cloudflare interstitial"),
    ("Ray ID", "Cloudflare challenge"),
    ("Turnstile", "Cloudflare Turnstile"),
)


def _markers(body: str) -> list[str]:
    head = body[:5000] if isinstance(body, str) else ""
    return [label for needle, label in _MARKERS if needle in head]


def _interpret(status: int, body: str) -> str:
    hits = _markers(body)
    if status == 200 and not hits and len(body) > 50_000:
        return "OK -- real page"
    if status == 200 and not hits:
        return f"200 / suspicious ({len(body)}b, no challenge markers)"
    if hits:
        return f"BLOCKED via {hits[0]}"
    if status == 0:
        return "exception / fetch never returned"
    return f"status={status}, no markers"


def _try_tier(tier: str, url: str, platform: str) -> dict:
    """Run one tier against one URL. Returns a result dict."""
    out: dict = {"tier": tier, "url": url, "platform": platform}

    if tier == "firecrawl" and not os.environ.get("OSINT_FIRECRAWL_API_KEY", "").strip():
        out["status"] = -1
        out["elapsed_s"] = 0.0
        out["body_len"] = 0
        out["interpretation"] = "SKIPPED -- OSINT_FIRECRAWL_API_KEY not set"
        return out

    # Set env var for this run.
    prev_tier = os.environ.get("OSINT_BROWSER_TIER")
    os.environ["OSINT_BROWSER_TIER"] = tier
    try:
        from osint_goblin_workers.humanize import HumanizedFetcher

        fetcher = HumanizedFetcher(investigation_id=f"tier-{tier}-{platform}-{int(time.time())}")
        try:
            t0 = time.monotonic()
            status, body = fetcher.fetch(
                url,
                platform=platform,
                jitter=False,
                synthetic_interaction=True,
                timeout_s=120.0,
            )
            out["elapsed_s"] = round(time.monotonic() - t0, 1)
            out["status"] = status
            out["body_len"] = len(body) if isinstance(body, str) else 0
            out["tier_used"] = fetcher._state.tier_used  # which tier actually fired
            out["interpretation"] = _interpret(status, body)
            if isinstance(body, str) and body:
                safe = f"{platform}-{tier}-status{status}.html".replace("/", "_")
                p = _OUT_DIR / safe
                p.write_text(body, encoding="utf-8")
                out["saved_to"] = str(p)
        except Exception:
            out["exception"] = traceback.format_exc()
        finally:
            fetcher.shred()
    finally:
        if prev_tier is None:
            os.environ.pop("OSINT_BROWSER_TIER", None)
        else:
            os.environ["OSINT_BROWSER_TIER"] = prev_tier
    return out


def run_matrix() -> list[dict]:
    targets = [
        ("vrbo", "https://www.vrbo.com/1682245"),
        ("tripadvisor", "https://www.tripadvisor.com/VacationRentals"),
    ]
    tiers = ["patchright", "rebrowser", "camoufox", "firecrawl"]
    results: list[dict] = []
    for platform, url in targets:
        print(f"\n=== {platform} @ {url} ===")
        for tier in tiers:
            print(f"  [{tier:10}] running...", end=" ", flush=True)
            r = _try_tier(tier, url, platform)
            if "exception" in r:
                print(f"EXCEPTION ({r['exception'].splitlines()[-1].strip()[:80]})")
            else:
                print(
                    f"status={r.get('status', '?')} "
                    f"len={r.get('body_len', '?'):>8} "
                    f"-> {r.get('interpretation', '?')}"
                )
            results.append(r)
    return results


def print_report(results: list[dict]) -> None:
    print("\n========== TIER PRESSURE TEST REPORT ==========")
    print(f"\nResponse bodies saved to: {_OUT_DIR}")
    print("\nPer-platform tier outcomes:")
    by_platform: dict[str, list[dict]] = {}
    for r in results:
        by_platform.setdefault(r["platform"], []).append(r)
    for platform, rows in by_platform.items():
        print(f"\n--- {platform.upper()} ---")
        for r in rows:
            tier = r["tier"]
            if "exception" in r:
                tail = r["exception"].splitlines()[-1][:80]
                print(f"  {tier:10}  EXCEPTION  {tail}")
            else:
                interp = r.get("interpretation", "?")
                stat = r.get("status", "?")
                length = r.get("body_len", 0)
                elapsed = r.get("elapsed_s", "?")
                print(
                    f"  {tier:10}  status={stat:>3} len={length:>8} elapsed={elapsed:>5}s  {interp}"
                )


if __name__ == "__main__":
    results = run_matrix()
    print_report(results)

"""Inspect listing-fetch failure modes -- capture exact response bytes
+ tracebacks for every failure cell in pressure-test-listing.py.

Per user directive 2026-05-15: "When we receive an error, inspect the
page to find out why."

For each test cell on each platform: run, capture response or exception
verbatim, save body to disk, print markers + interpretation.
"""

from __future__ import annotations

import sys
import time
import traceback
from pathlib import Path

WORKERS_SRC = Path(__file__).resolve().parents[2] / "apps" / "workers" / "src"
if WORKERS_SRC.exists():
    sys.path.insert(0, str(WORKERS_SRC))


_OUT_DIR = Path(__file__).resolve().parent / "listing-failure-bodies"
_OUT_DIR.mkdir(exist_ok=True)


def _save(label: str, body: str) -> Path:
    safe = label.replace("/", "_").replace(":", "_").replace("?", "_")[:120]
    p = _OUT_DIR / f"{safe}.html"
    p.write_text(body, encoding="utf-8")
    return p


def _summarize_markers(body: str) -> list[str]:
    markers = []
    needles = [
        ("Bot or Not", "Imperva Bot or Not"),
        ("Pardon Our Interruption", "Akamai BMA"),
        ("Access Denied", "Akamai EdgeKV"),
        ("Ray ID", "Cloudflare"),
        ("Just a moment", "Cloudflare interstitial"),
        ("captcha-delivery.com", "DataDome"),
        ("g-recaptcha", "reCAPTCHA"),
        ("h-captcha", "hCaptcha"),
        ("Turnstile", "Cloudflare Turnstile"),
        ("PerimeterX", "PerimeterX"),
        ("rate limit", "Generic rate-limit"),
        ("blocked", "generic-blocked-mention"),
        ("forbidden", "generic-forbidden-mention"),
        ("DDoS protection", "Generic DDoS shield"),
    ]
    body_head = body[:5000]
    for needle, label in needles:
        if needle.lower() in body_head.lower():
            markers.append(label)
    return markers


def _try_humanized(url: str, platform: str, label: str) -> dict:
    """Single humanized fetch with full exception capture."""
    from osint_goblin_workers.humanize import HumanizedFetcher

    fetcher = HumanizedFetcher(investigation_id=f"inspect-{platform}-{int(time.time())}")
    out: dict = {"label": label, "url": url, "platform": platform}
    try:
        t0 = time.monotonic()
        status, body = fetcher.fetch(
            url, platform=platform, jitter=False, synthetic_interaction=True, timeout_s=120.0
        )
        out["elapsed_s"] = round(time.monotonic() - t0, 1)
        out["status"] = status
        out["body_len"] = len(body)
        out["markers"] = _summarize_markers(body)
        if body:
            out["saved_to"] = str(_save(label, body))
            # Preview first 1000 chars of body.
            out["body_preview"] = body[:1000].replace("\n", " ")[:600]
    except Exception:
        out["exception"] = traceback.format_exc()
    finally:
        fetcher.shred()
    return out


def _try_httpx_with_inspection(url: str, label: str) -> dict:
    """Bare httpx -- captures response headers AND body for analysis."""
    import httpx

    out: dict = {"label": label, "url": url}
    try:
        t0 = time.monotonic()
        with httpx.Client(
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://www.google.com/",
            },
            follow_redirects=True,
            timeout=30.0,
        ) as c:
            r = c.get(url)
            out["elapsed_s"] = round(time.monotonic() - t0, 1)
            out["status"] = r.status_code
            out["body_len"] = len(r.text)
            out["markers"] = _summarize_markers(r.text)
            # Capture key response headers that anti-bot vendors set.
            interesting = {
                k.lower(): v
                for k, v in r.headers.items()
                if k.lower()
                in (
                    "cf-ray",
                    "cf-mitigated",
                    "server",
                    "x-amzn-bot-status",
                    "x-datadome",
                    "x-imperva-id",
                    "x-cdn",
                    "x-akamai-edgescape",
                    "x-akamai-pragma",
                    "x-akamai-transformed",
                    "x-amz-cf-id",
                    "x-amz-cf-pop",
                    "set-cookie",
                    "retry-after",
                    "x-iinfo",
                )
            }
            out["headers"] = interesting
            if r.text:
                out["saved_to"] = str(_save(label + "-httpx", r.text))
                out["body_preview"] = r.text[:1500].replace("\n", " ")[:1000]
    except Exception:
        out["exception"] = traceback.format_exc()
    return out


def main() -> None:
    cases = [
        ("vrbo-listing", "vrbo", "https://www.vrbo.com/1682245"),
        ("vrbo-homepage", "vrbo", "https://www.vrbo.com/"),
        ("tripadvisor-vr-landing", "tripadvisor", "https://www.tripadvisor.com/VacationRentals"),
        (
            "tripadvisor-hotel-search",
            "tripadvisor",
            "https://www.tripadvisor.com/Search?q=New+York&searchSessionId=",
        ),
        ("tripadvisor-homepage", "tripadvisor", "https://www.tripadvisor.com/"),
    ]
    print(f"\nOutput dir: {_OUT_DIR}\n")
    for label, platform, url in cases:
        print(f"=== {label}  ({url}) ===")
        # Humanized inspection.
        h = _try_humanized(url, platform, label + "-humanized")
        _print_result("humanized", h)
        # Plain httpx inspection with headers.
        x = _try_httpx_with_inspection(url, label)
        _print_result("httpx-with-headers", x)
        print()


def _print_result(prefix: str, r: dict) -> None:
    if "exception" in r:
        print(f"  [{prefix}] EXCEPTION:")
        # Last 3 lines of traceback are usually the most informative.
        tb_tail = "\n".join(r["exception"].splitlines()[-4:])
        for line in tb_tail.splitlines():
            print(f"    {line}")
        return
    print(
        f"  [{prefix}] status={r.get('status', '?')} "
        f"len={r.get('body_len', '?')} "
        f"elapsed={r.get('elapsed_s', '?')}s "
        f"markers={r.get('markers', [])}"
    )
    if r.get("headers"):
        print("    response-headers:")
        for k, v in r["headers"].items():
            print(f"      {k}: {v[:120]}")
    if "body_preview" in r:
        print(f"    body[:1000]: {r['body_preview'][:300]}...")
    if "saved_to" in r:
        print(f"    saved: {r['saved_to']}")


if __name__ == "__main__":
    main()

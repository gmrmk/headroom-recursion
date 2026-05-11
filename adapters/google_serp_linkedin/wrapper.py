"""Google-SERP-for-LinkedIn-URLs subprocess wrapper (Sprint 3+).

Searches Google for `site:linkedin.com/in "<name>" "<company>"` and
extracts the LinkedIn profile URLs from the SERP. Closes the
name-based-search gap that linkedin_profile (which needs a URL) and
RocketReach (which may fail intermittently) both leave open.

Contract:
  stdin:  {"name": "Alice Smith", "company": "Acme Corp"} -- name required
  stdout: NDJSON events
  stderr: free-form log
  exit:   0 on clean run; non-zero on adapter failure

Live mode posture:
  - **No Google API key required.** Hits the public SERP via Scrapling/
    Patchright stealth. Google rate-limits and shows captcha on
    detected bots; the stealth fetcher buys some runway but expect
    occasional failures.
  - **SERP snippets only.** Wrapper extracts the LinkedIn profile URL
    (the heading link) and the 2-line description snippet visible
    next to each result. Does NOT scrape the LinkedIn profiles
    themselves -- that's linkedin_profile's surface.
  - **Top 5 results.** SERP's first page typically has the right answer
    for a specific-enough query. Investigator can re-run with more
    narrowing terms (company, city) if the first page misses.

Synthetic mode (OSINT_ADAPTER_MODE=synthetic): deterministic fixture.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.parse
from datetime import UTC, datetime


def _emit(event: dict) -> None:
    sys.stdout.write(json.dumps(event, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _run_synthetic(payload: dict) -> int:
    name = payload.get("name", "Subject")
    _emit(
        {
            "event_type": "tool-run-accepted",
            "payload": {
                "adapter": "google_serp_linkedin",
                "name": name,
                "synthetic": True,
            },
        }
    )
    fixtures = [
        {
            "source": "google-serp-linkedin",
            "profile_url": "https://www.linkedin.com/in/alice-smith-synthetic-1",
            "snippet": "Software Engineer at Synthetic Co. - Springfield, IL",
            "title": f"{name} - LinkedIn",
        },
        {
            "source": "google-serp-linkedin",
            "profile_url": "https://www.linkedin.com/in/alice-smith-synthetic-2",
            "snippet": "Marketing Manager at Other Co. - Decatur, IL",
            "title": f"{name} - LinkedIn",
        },
    ]
    for m in fixtures:
        _emit({"event_type": "person-match", "payload": {**m, "synthetic": True}})
    _emit(
        {
            "event_type": "tool-run-result",
            "payload": {"name": name, "matches": len(fixtures), "synthetic": True},
        }
    )
    return 0


def _run_live(payload: dict) -> int:
    try:
        from scrapling.fetchers import StealthyFetcher
    except ImportError:
        sys.stderr.write("scrapling not importable; falling back to synthetic\n")
        return _run_synthetic(payload)

    name = (payload.get("name") or "").strip()
    if not name:
        _emit(
            {
                "event_type": "tool-run-error",
                "payload": {"reason": "missing 'name' in payload"},
            }
        )
        return 2

    company = (payload.get("company") or "").strip()
    city = (payload.get("city") or "").strip()

    started = time.monotonic()
    _emit(
        {
            "event_type": "tool-run-accepted",
            "payload": {
                "adapter": "google_serp_linkedin",
                "name": name,
                "company": company,
                "city": city,
                "started_at": _now_iso(),
            },
        }
    )

    # Build the query. site: constrains to LinkedIn profile pages.
    # Quoted name reduces false positives; company/city narrow further.
    query_parts = ['site:linkedin.com/in', f'"{name}"']
    if company:
        query_parts.append(f'"{company}"')
    if city:
        query_parts.append(f'"{city}"')
    query = " ".join(query_parts)
    url = "https://www.google.com/search?" + urllib.parse.urlencode({"q": query, "num": "10"})

    try:
        page = StealthyFetcher.fetch(url, headless=True, network_idle=True)
    except Exception as exc:
        sys.stderr.write(f"scrapling fetch failed: {type(exc).__name__}: {exc}\n")
        _emit(
            {
                "event_type": "tool-run-error",
                "payload": {
                    "reason": f"scrapling {type(exc).__name__}: {exc}",
                    "url": url,
                    "suggest": "Google may be captcha-walling; try synthetic or retry later",
                },
            }
        )
        return 3

    # Google SERP organic results have varied selectors; we look for
    # any anchor whose href points at linkedin.com/in/ and walk up to
    # the surrounding result block for the snippet text.
    matches = []
    try:
        # Heuristic: every anchor with linkedin.com/in/ in href
        links = page.css("a[href*='linkedin.com/in/']")
        seen_urls: set[str] = set()
        for link in links[:20]:  # over-fetch then dedup; cap at 5 below
            href = link.attrib.get("href", "")
            # Google often wraps URLs in /url?q=... redirect
            m = re.search(r"linkedin\.com/in/[A-Za-z0-9\-_%./]+", href)
            if not m:
                continue
            clean_url = "https://www." + m.group(0).lstrip(".")
            if "linkedin.com" not in clean_url[: clean_url.index("/in/")] + "/in/":
                clean_url = "https://www.linkedin.com/in/" + clean_url.split("/in/", 1)[1]
            if clean_url in seen_urls:
                continue
            seen_urls.add(clean_url)
            # Walk up for snippet -- Google nests snippet in a sibling div
            title = (link.text or "").strip()
            snippet = ""
            try:
                # Try a few common snippet selectors
                snippet_el = link.parent.parent.css_first(
                    ".VwiC3b, .yXK7lf, span[data-content-feature]"
                )
                if snippet_el:
                    snippet = (snippet_el.text or "").strip()
            except Exception:
                pass
            matches.append(
                {
                    "source": "google-serp-linkedin",
                    "profile_url": clean_url,
                    "title": title,
                    "snippet": snippet,
                }
            )
            if len(matches) >= 5:
                break
    except Exception as exc:
        sys.stderr.write(f"parse error: {type(exc).__name__}: {exc}\n")
        _emit(
            {
                "event_type": "tool-run-error",
                "payload": {
                    "reason": f"parse {type(exc).__name__}: {exc}",
                    "url": url,
                    "suggest": "Google SERP may have re-skinned; check selectors",
                },
            }
        )
        return 4

    for m in matches:
        _emit({"event_type": "person-match", "payload": m})
    _emit(
        {
            "event_type": "tool-run-result",
            "payload": {
                "source": "google-serp-linkedin",
                "name": name,
                "company": company,
                "city": city,
                "matches": len(matches),
                "duration_s": round(time.monotonic() - started, 2),
                "url": url,
                "note": "name-search shim for linkedin_profile -- feed any matched URL back into that adapter",
            },
        }
    )
    return 0


def main() -> int:
    raw = sys.stdin.read().strip()
    if not raw:
        sys.stderr.write("error: empty stdin payload\n")
        return 2
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        sys.stderr.write(f"error: invalid JSON payload: {exc}\n")
        return 2

    if os.environ.get("OSINT_ADAPTER_MODE") == "synthetic":
        sys.stderr.write("OSINT_ADAPTER_MODE=synthetic; bypassing live fetch\n")
        return _run_synthetic(payload)

    return _run_live(payload)


if __name__ == "__main__":
    raise SystemExit(main())

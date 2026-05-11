"""TruePeopleSearch subprocess wrapper (Sprint 3).

Runs in the empirical venv (Scrapling + Patchright). The osint-goblin
worker dispatches this via subprocess_adapter.make_subprocess_adapter
with python_executable pinned to the empirical venv path.

Contract (matches Sora ADR-0004 sec.5):
  stdin:  {"name": "...", "city": "...", "state": "..."} on one line, EOF
  stdout: NDJSON event objects, one per line
  stderr: free-form log
  exit:   0 on clean run; non-zero on adapter failure

Live-mode policy:
  TruePeopleSearch is a free public-records aggregator. Their ToS
  prohibits scraping; per the user's stated preference 2026-05-11
  (memory: aggressive legal scraping is fine -- don't conflate with
  account automation), scraping public-records aggregation for
  personal investigation is acceptable. This wrapper respects basic
  hygiene: identifies as a desktop browser via Scrapling/Patchright
  stealth fetching, no parallel queries from same IP, no scraping
  beyond what a human investigator would manually click through.

Synthetic mode (OSINT_ADAPTER_MODE=synthetic): bypasses Scrapling
entirely and emits a deterministic fixture. Used by M0 exit gate
and tests that should not depend on TruePeopleSearch availability.
"""

from __future__ import annotations

import json
import os
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
    """Synthetic event stream -- no network, deterministic."""
    name = payload.get("name", "Subject")
    _emit(
        {
            "event_type": "tool-run-accepted",
            "payload": {"adapter": "true_people_search", "name": name, "synthetic": True},
        }
    )
    matches = [
        {
            "name": name,
            "age_range": "40-45",
            "city": "Springfield",
            "state": "IL",
            "previous_addresses": ["123 Main St, Springfield IL"],
            "relatives": ["Bob Smith", "Carol Smith"],
            "result_url": "https://www.truepeoplesearch.com/results?name=" + urllib.parse.quote(name),
        },
        {
            "name": name,
            "age_range": "35-40",
            "city": "Decatur",
            "state": "IL",
            "previous_addresses": ["456 Oak Ave, Decatur IL"],
            "relatives": [],
            "result_url": "https://www.truepeoplesearch.com/results?name=" + urllib.parse.quote(name),
        },
    ]
    for m in matches:
        _emit({"event_type": "person-match", "payload": {**m, "synthetic": True}})
    _emit(
        {
            "event_type": "tool-run-result",
            "payload": {"name": name, "matches": len(matches), "synthetic": True},
        }
    )
    return 0


def _run_live(payload: dict) -> int:
    """Live mode: fetch via Scrapling, parse with built-in selectors.

    Conservative: top-5 results only, single page only. Investigator
    can re-run with more specific filters (city, state) if the first
    page misses.
    """
    try:
        from scrapling.fetchers import StealthyFetcher
    except ImportError:
        sys.stderr.write("scrapling not importable; falling back to synthetic mode\n")
        return _run_synthetic(payload)

    name = (payload.get("name") or "").strip()
    if not name:
        sys.stderr.write("error: missing 'name' in payload\n")
        _emit(
            {
                "event_type": "tool-run-error",
                "payload": {"reason": "missing 'name' in payload"},
            }
        )
        return 2

    city = (payload.get("city") or "").strip()
    state = (payload.get("state") or "").strip()
    started = time.monotonic()
    _emit(
        {
            "event_type": "tool-run-accepted",
            "payload": {
                "adapter": "true_people_search",
                "name": name,
                "city": city,
                "state": state,
                "started_at": _now_iso(),
            },
        }
    )

    # Build the search URL. TruePeopleSearch's basic query shape:
    #   https://www.truepeoplesearch.com/results?name=<NAME>&citystatezip=<CITY>%2C<STATE>
    params = {"name": name}
    if city or state:
        params["citystatezip"] = f"{city}, {state}".strip(", ")
    url = "https://www.truepeoplesearch.com/results?" + urllib.parse.urlencode(params)

    try:
        # StealthyFetcher uses Patchright (Playwright with stealth patches);
        # handles Cloudflare/Akamai bot-detection that plain httpx hits.
        page = StealthyFetcher.fetch(url, headless=True, network_idle=True)
    except Exception as exc:
        sys.stderr.write(f"scrapling fetch failed: {type(exc).__name__}: {exc}\n")
        _emit(
            {
                "event_type": "tool-run-error",
                "payload": {
                    "reason": f"scrapling {type(exc).__name__}: {exc}",
                    "url": url,
                    "suggest": "site may be blocking; try OSINT_ADAPTER_MODE=synthetic",
                },
            }
        )
        return 3

    # Parse results. TruePeopleSearch results land in `div.card-summary`
    # elements; each card has a name (h4.card-summary a) + age + address.
    # The selectors below mirror the empirical structure as of 2026-05;
    # if the site re-skins, the failure mode is zero matches (not a crash).
    matches = []
    try:
        cards = page.css("div.card-summary")[:5]  # top 5
        for card in cards:
            name_el = card.css_first("h4.card-summary > a, .h4.card-summary > a")
            full_name = (name_el.text or "").strip() if name_el else ""
            age_el = card.css_first("span:contains('Age')")
            age = (age_el.text or "").replace("Age", "").strip() if age_el else ""
            addr_el = card.css_first("span.content-value")
            addr = (addr_el.text or "").strip() if addr_el else ""
            href = name_el.attrib.get("href", "") if name_el else ""
            result_url = (
                f"https://www.truepeoplesearch.com{href}" if href.startswith("/") else href
            )
            if full_name:
                matches.append(
                    {
                        "name": full_name,
                        "age": age,
                        "address": addr,
                        "result_url": result_url,
                    }
                )
    except Exception as exc:
        sys.stderr.write(f"parse error: {type(exc).__name__}: {exc}\n")
        _emit(
            {
                "event_type": "tool-run-error",
                "payload": {
                    "reason": f"parse {type(exc).__name__}: {exc}",
                    "url": url,
                    "suggest": "site may have re-skinned; check selectors",
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
                "name": name,
                "city": city,
                "state": state,
                "matches": len(matches),
                "duration_s": round(time.monotonic() - started, 2),
                "url": url,
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

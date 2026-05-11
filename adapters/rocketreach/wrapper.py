"""RocketReach subprocess wrapper (Sprint 3+).

Scrapling/Patchright wrapper that searches RocketReach for a person
by name (and optionally company / city) and extracts the publicly-
visible result rows. Runs in the empirical venv.

Contract:
  stdin:  {"name": "Alice Smith"}                          -- minimum
          {"name": "Alice Smith", "company": "Acme Corp"}  -- narrowed
          {"name": "Alice Smith", "city": "Springfield"}   -- narrowed
  stdout: NDJSON events (person-match per result + tool-run-result)
  stderr: free-form log
  exit:   0 on clean run; non-zero on adapter failure

Live mode posture:
  - **No login.** RocketReach's free public search shows top results
    without authentication. The wrapper hits the public search page.
  - **Free-tier surface only.** Paid-tier fields (email, phone) are
    NOT scraped -- they're not in the public-view DOM. The wrapper
    surfaces name, headline, current company/title, location,
    profile URL. Email/phone require RocketReach API keys, deferred
    indefinitely.
  - **Property-vetting use case:** verify a host's claimed employer
    matches a real-world LinkedIn-adjacent professional record.
    Complements linkedin_profile (which needs a URL); RocketReach
    can be searched by name.

Synthetic mode (OSINT_ADAPTER_MODE=synthetic): deterministic fixture.
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
    name = payload.get("name", "Subject")
    _emit(
        {
            "event_type": "tool-run-accepted",
            "payload": {
                "adapter": "rocketreach_search",
                "name": name,
                "synthetic": True,
            },
        }
    )
    fixtures = [
        {
            "source": "rocketreach",
            "name": name,
            "headline": "Senior Engineer at Synthetic Co.",
            "current_company": "Synthetic Co.",
            "current_title": "Senior Engineer",
            "location": "Springfield, IL",
            "profile_url": (
                "https://rocketreach.co/alice-smith-synthetic-12345"
            ),
        },
        {
            "source": "rocketreach",
            "name": name,
            "headline": "Marketing Manager at Other Co.",
            "current_company": "Other Co.",
            "current_title": "Marketing Manager",
            "location": "Decatur, IL",
            "profile_url": (
                "https://rocketreach.co/alice-smith-synthetic-67890"
            ),
        },
    ]
    for m in fixtures:
        _emit({"event_type": "person-match", "payload": {**m, "synthetic": True}})
    _emit(
        {
            "event_type": "tool-run-result",
            "payload": {
                "source": "rocketreach",
                "name": name,
                "matches": len(fixtures),
                "synthetic": True,
            },
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
                "adapter": "rocketreach_search",
                "name": name,
                "company": company,
                "city": city,
                "started_at": _now_iso(),
            },
        }
    )

    # RocketReach public people-search shape:
    #   https://rocketreach.co/people?start=1&pageSize=10&keyword_name=<name>
    #     &keyword_current_employer=<company>&keyword_location=<city>
    params = {"start": "1", "pageSize": "10", "keyword_name": name}
    if company:
        params["keyword_current_employer"] = company
    if city:
        params["keyword_location"] = city
    url = "https://rocketreach.co/people?" + urllib.parse.urlencode(params)

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
                    "suggest": (
                        "RocketReach may be rate-limiting; retry later or use synthetic"
                    ),
                },
            }
        )
        return 3

    # Results live in `.profile-card` (or `.search-result`) rows.
    # Selectors aim for the public free-tier surface; paid email/phone
    # cells are intentionally not in this selector set.
    matches = []
    try:
        cards = page.css("div.profile-card, div.search-result, li.result-item")[:5]
        for card in cards:
            name_el = card.css_first("a.profile-name, h4 a, .name a")
            full_name = (name_el.text or "").strip() if name_el else ""
            href = name_el.attrib.get("href", "") if name_el else ""
            profile_url = (
                f"https://rocketreach.co{href}" if href.startswith("/") else href
            )
            headline_el = card.css_first(".headline, .title-line, .position")
            headline = (headline_el.text or "").strip() if headline_el else ""
            loc_el = card.css_first(".location, .city-state")
            location = (loc_el.text or "").strip() if loc_el else ""
            current_title = ""
            current_company = ""
            if " at " in headline:
                left, _, right = headline.partition(" at ")
                current_title = left.strip()
                current_company = right.strip()
            if full_name:
                matches.append(
                    {
                        "source": "rocketreach",
                        "name": full_name,
                        "headline": headline,
                        "current_title": current_title,
                        "current_company": current_company,
                        "location": location,
                        "profile_url": profile_url,
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
                    "suggest": "RocketReach may have re-skinned; check selectors",
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
                "source": "rocketreach",
                "name": name,
                "company": company,
                "city": city,
                "matches": len(matches),
                "duration_s": round(time.monotonic() - started, 2),
                "url": url,
                "note": "free-tier-only: email/phone require API key (deferred)",
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

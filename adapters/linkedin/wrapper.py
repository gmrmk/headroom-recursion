"""LinkedIn public-profile subprocess wrapper (Sprint 3+).

Scrapling/Patchright wrapper that fetches a public LinkedIn profile
page and extracts the publicly-visible fields. Runs in the empirical
venv (no Scrapling in the worker .venv).

Contract:
  stdin:  {"profile_url": "https://www.linkedin.com/in/<handle>"} -- OR
          {"name": "Alice Smith", "company": "Acme Corp"} for search
  stdout: NDJSON events (person-match per result + tool-run-result summary)
  stderr: free-form log
  exit:   0 on clean run; non-zero on adapter failure

Live mode posture (honest):
  - **No login.** This wrapper does NOT authenticate against LinkedIn,
    does NOT use the investigator's cookies, and does NOT risk an
    account lock. LinkedIn's anti-scraping is among the most hostile
    on the public web; a stealth fetch of the public-view URL may or
    may not succeed depending on the day.
  - **Public-view only.** Fields extracted are those a logged-out
    visitor sees: name, headline, current company/title text, location,
    profile photo URL. Anything behind the login wall is intentionally
    NOT scraped.
  - **Profile URL required for live mode.** Name-based search would
    require Google Search scraping or LinkedIn's own search (logged-in
    only). Out of scope for this wrapper; the investigator can find
    the URL via a manual Google query.

Synthetic mode (OSINT_ADAPTER_MODE=synthetic): deterministic fixture.

Property-vetting use case: confirm a property host's claimed identity
("Alice Smith, engineer at Acme Corp in Springfield IL") matches a
public LinkedIn profile. The wrapper extracts the four claim-relevant
fields (name, headline, company, location) so the investigator can
compare against the listing claim.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import UTC, datetime


def _emit(event: dict) -> None:
    sys.stdout.write(json.dumps(event, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _run_synthetic(payload: dict) -> int:
    profile_url = payload.get("profile_url", "https://www.linkedin.com/in/synthetic")
    name = payload.get("name", "")
    _emit(
        {
            "event_type": "tool-run-accepted",
            "payload": {
                "adapter": "linkedin_profile",
                "profile_url": profile_url,
                "name": name,
                "synthetic": True,
            },
        }
    )
    _emit(
        {
            "event_type": "person-match",
            "payload": {
                "source": "linkedin",
                "name": name or "Alice Synthetic",
                "headline": "Software Engineer at Synthetic Co.",
                "current_company": "Synthetic Co.",
                "current_title": "Software Engineer",
                "location": "Springfield, IL",
                "profile_url": profile_url,
                "photo_url": "https://media.licdn.com/dms/image/synthetic.jpg",
                "synthetic": True,
            },
        }
    )
    _emit(
        {
            "event_type": "tool-run-result",
            "payload": {
                "source": "linkedin",
                "profile_url": profile_url,
                "matches": 1,
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

    profile_url = (payload.get("profile_url") or "").strip()
    if not profile_url:
        sys.stderr.write("error: live mode requires 'profile_url'\n")
        _emit(
            {
                "event_type": "tool-run-error",
                "payload": {
                    "reason": "live mode requires 'profile_url' (name-based search is not implemented)",
                    "suggest": (
                        "Find the LinkedIn URL via Google ('site:linkedin.com/in <name>'),"
                        " then re-run with profile_url"
                    ),
                },
            }
        )
        return 2
    if "linkedin.com/in/" not in profile_url:
        _emit(
            {
                "event_type": "tool-run-error",
                "payload": {
                    "reason": "profile_url must be a linkedin.com/in/<handle> URL",
                    "got": profile_url,
                },
            }
        )
        return 2

    started = time.monotonic()
    _emit(
        {
            "event_type": "tool-run-accepted",
            "payload": {
                "adapter": "linkedin_profile",
                "profile_url": profile_url,
                "started_at": _now_iso(),
            },
        }
    )

    try:
        page = StealthyFetcher.fetch(profile_url, headless=True, network_idle=True)
    except Exception as exc:
        sys.stderr.write(f"scrapling fetch failed: {type(exc).__name__}: {exc}\n")
        _emit(
            {
                "event_type": "tool-run-error",
                "payload": {
                    "reason": f"scrapling {type(exc).__name__}: {exc}",
                    "url": profile_url,
                    "suggest": (
                        "LinkedIn may be blocking; try OSINT_ADAPTER_MODE=synthetic,"
                        " or wait + retry. No login retry path."
                    ),
                },
            }
        )
        return 3

    # LinkedIn public-view selectors as of 2026-05. These are the most stable
    # heuristics; if LinkedIn re-skins, the failure mode is empty fields,
    # not a crash.
    name = ""
    headline = ""
    location = ""
    current_company = ""
    current_title = ""
    photo_url = ""
    try:
        # Name + headline are in the top-card; multiple selectors to survive
        # A/B layouts.
        name_el = page.css_first("h1.top-card-layout__title, h1[class*='top-card']")
        name = (name_el.text or "").strip() if name_el else ""
        headline_el = page.css_first(
            "h2.top-card-layout__headline, .top-card-layout__headline"
        )
        headline = (headline_el.text or "").strip() if headline_el else ""
        loc_el = page.css_first(
            ".top-card-layout__first-subline span, .top-card__subline-item"
        )
        location = (loc_el.text or "").strip() if loc_el else ""
        photo_el = page.css_first("img.top-card__profile-image, img[class*='profile-photo']")
        photo_url = photo_el.attrib.get("src", "") if photo_el else ""

        # Headline often has format "<Title> at <Company>" -- parse heuristically.
        if " at " in headline:
            left, _, right = headline.partition(" at ")
            current_title = left.strip()
            current_company = right.strip()
    except Exception as exc:
        sys.stderr.write(f"parse soft-failure: {type(exc).__name__}: {exc}\n")
        # Fall through; partial fields are better than nothing.

    matches_count = 1 if name else 0
    if name:
        _emit(
            {
                "event_type": "person-match",
                "payload": {
                    "source": "linkedin",
                    "name": name,
                    "headline": headline,
                    "current_company": current_company,
                    "current_title": current_title,
                    "location": location,
                    "profile_url": profile_url,
                    "photo_url": photo_url,
                },
            }
        )
    _emit(
        {
            "event_type": "tool-run-result",
            "payload": {
                "source": "linkedin",
                "profile_url": profile_url,
                "matches": matches_count,
                "duration_s": round(time.monotonic() - started, 2),
                "note": ("login-wall-respected: only public-view fields extracted"),
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

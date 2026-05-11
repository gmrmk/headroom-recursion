"""Twitter/X public profile subprocess wrapper (Sprint 3+).

Scrapling/Patchright fetcher for a public Twitter/X profile.
Public fields only: display name, bio, location, joined date,
follower count, following count, tweet count. Used for property-
vetting cross-check ("host claims Springfield local since 2010"
vs profile bio + joined date).

Contract:
  stdin:  {"handle": "alice"} OR {"profile_url": "https://x.com/alice"}
  stdout: NDJSON events
  exit:   0 on clean run; non-zero on failure

Live mode posture:
  - No login. Public-view fields only.
  - Tries both x.com (primary) and a nitter mirror as fallback for
    when x.com's JS-heavy app blocks stealth fetching. Investigator
    can override the nitter base URL via OSINT_NITTER_BASE env.
  - Returns count-only data; does NOT extract follower lists or
    tweet contents.

Synthetic mode (OSINT_ADAPTER_MODE=synthetic): deterministic fixture.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import UTC, datetime


def _emit(event: dict) -> None:
    sys.stdout.write(json.dumps(event, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _extract_handle(payload: dict) -> str:
    h = (payload.get("handle") or "").strip().lstrip("@")
    if h:
        return h
    url = (payload.get("profile_url") or "").strip()
    m = re.search(r"(?:twitter|x)\.com/(?:@)?([A-Za-z0-9_]+)", url)
    return m.group(1) if m else ""


def _run_synthetic(payload: dict) -> int:
    handle = _extract_handle(payload) or "synthetic_user"
    _emit(
        {
            "event_type": "tool-run-accepted",
            "payload": {"adapter": "twitter_public", "handle": handle, "synthetic": True},
        }
    )
    _emit(
        {
            "event_type": "person-match",
            "payload": {
                "source": "twitter",
                "handle": handle,
                "display_name": "Alice Synthetic",
                "bio": "Engineer at Synthetic Co. Springfield IL.",
                "location": "Springfield, IL",
                "website": "https://example.com",
                "joined": "September 2014",
                "follower_count": 1234,
                "following_count": 567,
                "tweet_count": 8901,
                "verified": False,
                "profile_url": f"https://x.com/{handle}",
                "synthetic": True,
            },
        }
    )
    _emit(
        {
            "event_type": "tool-run-result",
            "payload": {"source": "twitter", "handle": handle, "matches": 1, "synthetic": True},
        }
    )
    return 0


def _parse_count(text: str) -> int:
    """Twitter shows counts like '1.2M', '3.4K', '567'. Return as int."""
    if not text:
        return 0
    s = text.strip().replace(",", "")
    multiplier = 1
    if s.endswith("K"):
        multiplier = 1000
        s = s[:-1]
    elif s.endswith("M"):
        multiplier = 1_000_000
        s = s[:-1]
    elif s.endswith("B"):
        multiplier = 1_000_000_000
        s = s[:-1]
    try:
        return int(float(s) * multiplier)
    except ValueError:
        return 0


def _run_live(payload: dict) -> int:
    try:
        from scrapling.fetchers import StealthyFetcher
    except ImportError:
        sys.stderr.write("scrapling not importable; falling back to synthetic\n")
        return _run_synthetic(payload)

    handle = _extract_handle(payload)
    if not handle:
        _emit(
            {
                "event_type": "tool-run-error",
                "payload": {"reason": "missing 'handle' or 'profile_url' in payload"},
            }
        )
        return 2

    started = time.monotonic()
    _emit(
        {
            "event_type": "tool-run-accepted",
            "payload": {
                "adapter": "twitter_public",
                "handle": handle,
                "started_at": _now_iso(),
            },
        }
    )

    # Try a nitter mirror first -- lightweight HTML, no JS. The user can
    # override the nitter base via OSINT_NITTER_BASE if their preferred
    # instance changes (nitter instances die regularly).
    nitter_base = os.environ.get("OSINT_NITTER_BASE", "https://nitter.poast.org")
    url = f"{nitter_base}/{handle}"
    via_nitter = True

    try:
        page = StealthyFetcher.fetch(url, headless=True, network_idle=True)
    except Exception:
        # Fall back to x.com if nitter is down
        via_nitter = False
        url = f"https://x.com/{handle}"
        try:
            page = StealthyFetcher.fetch(url, headless=True, network_idle=True)
        except Exception as exc:
            _emit(
                {
                    "event_type": "tool-run-error",
                    "payload": {
                        "reason": f"scrapling {type(exc).__name__}: {exc}",
                        "url": url,
                        "suggest": "Twitter + nitter both unreachable; try synthetic",
                    },
                }
            )
            return 3

    display_name = ""
    bio = ""
    location = ""
    website = ""
    joined = ""
    follower_count = 0
    following_count = 0
    tweet_count = 0
    try:
        if via_nitter:
            # Nitter selectors are stable and simple
            name_el = page.css_first(".profile-card-fullname")
            display_name = (name_el.text or "").strip() if name_el else ""
            bio_el = page.css_first(".profile-bio")
            bio = (bio_el.text or "").strip() if bio_el else ""
            loc_el = page.css_first(".profile-location")
            location = (loc_el.text or "").strip() if loc_el else ""
            web_el = page.css_first(".profile-website a")
            website = web_el.attrib.get("href", "") if web_el else ""
            joined_el = page.css_first(".profile-joindate")
            joined = (joined_el.text or "").replace("Joined ", "").strip() if joined_el else ""
            stats = page.css(".profile-stat-num")
            if len(stats) >= 3:
                tweet_count = _parse_count(stats[0].text or "")
                following_count = _parse_count(stats[1].text or "")
                follower_count = _parse_count(stats[2].text or "")
        else:
            # x.com fallback -- selectors more fragile but try
            name_el = page.css_first("div[data-testid='UserName'] span")
            display_name = (name_el.text or "").strip() if name_el else ""
            bio_el = page.css_first("div[data-testid='UserDescription']")
            bio = (bio_el.text or "").strip() if bio_el else ""
            loc_el = page.css_first("span[data-testid='UserLocation']")
            location = (loc_el.text or "").strip() if loc_el else ""
            # Counts on x.com are behind JS-rendered links
            link_followers = page.css_first(f"a[href$='/followers']")
            link_following = page.css_first(f"a[href$='/following']")
            if link_followers:
                m = re.search(r"([\d.,KMB]+)", link_followers.text or "")
                if m:
                    follower_count = _parse_count(m.group(1))
            if link_following:
                m = re.search(r"([\d.,KMB]+)", link_following.text or "")
                if m:
                    following_count = _parse_count(m.group(1))
    except Exception as exc:
        sys.stderr.write(f"parse soft-fail: {type(exc).__name__}: {exc}\n")

    if display_name or follower_count or bio:
        _emit(
            {
                "event_type": "person-match",
                "payload": {
                    "source": "twitter",
                    "handle": handle,
                    "display_name": display_name,
                    "bio": bio,
                    "location": location,
                    "website": website,
                    "joined": joined,
                    "follower_count": follower_count,
                    "following_count": following_count,
                    "tweet_count": tweet_count,
                    "profile_url": f"https://x.com/{handle}",
                    "fetched_via": "nitter" if via_nitter else "x.com",
                },
            }
        )
    _emit(
        {
            "event_type": "tool-run-result",
            "payload": {
                "source": "twitter",
                "handle": handle,
                "matches": 1 if (display_name or follower_count or bio) else 0,
                "duration_s": round(time.monotonic() - started, 2),
                "url": url,
            },
        }
    )
    return 0


def main() -> int:
    raw = sys.stdin.read().strip()
    if not raw:
        return 2
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        sys.stderr.write(f"invalid JSON: {exc}\n")
        return 2
    if os.environ.get("OSINT_ADAPTER_MODE") == "synthetic":
        return _run_synthetic(payload)
    return _run_live(payload)


if __name__ == "__main__":
    raise SystemExit(main())

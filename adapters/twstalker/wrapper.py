"""TWStalker public Twitter/X profile mirror (Sprint 3+).

twstalker.com is a third-party that mirrors public Twitter/X profile
data without requiring login. Complements twitter_public (nitter-first
+ x.com fallback) and twitter_followers as another no-login surface.
Useful when nitter mirrors are down AND x.com is blocking.

Contract:
  stdin:  {"handle": "alice"} OR {"profile_url": "..."}
  stdout: NDJSON

Live mode posture:
  - No login.
  - twstalker is a third-party scrape mirror; freshness varies (24-72h lag).
  - Anti-bot is moderate; Scrapling stealth is appropriate.
  - Public-view fields only.

Synthetic mode: deterministic fixture.
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
    m = re.search(r"(?:twitter|x|twstalker)\.com/(?:@)?([A-Za-z0-9_]+)", url)
    return m.group(1) if m else ""


def _parse_count(text: str) -> int:
    if not text:
        return 0
    s = text.strip().replace(",", "")
    mult = 1
    if s.endswith("K"):
        mult = 1000
        s = s[:-1]
    elif s.endswith("M"):
        mult = 1_000_000
        s = s[:-1]
    try:
        return int(float(s) * mult)
    except ValueError:
        return 0


def _run_synthetic(payload: dict) -> int:
    handle = _extract_handle(payload) or "synthetic_user"
    _emit({"event_type": "tool-run-accepted", "payload": {"adapter": "twstalker", "handle": handle, "synthetic": True}})
    _emit({
        "event_type": "person-match",
        "payload": {
            "source": "twstalker",
            "handle": handle,
            "display_name": "Alice Synthetic",
            "bio": "Synthetic Twitter bio for testing.",
            "location": "Springfield, IL",
            "joined": "September 2014",
            "follower_count": 1234,
            "following_count": 567,
            "tweet_count": 8901,
            "verified": False,
            "profile_url": f"https://twstalker.com/{handle}",
            "synthetic": True,
        },
    })
    _emit({"event_type": "tool-run-result", "payload": {"source": "twstalker", "handle": handle, "matches": 1, "synthetic": True}})
    return 0


def _run_live(payload: dict) -> int:
    try:
        from scrapling.fetchers import StealthyFetcher
    except ImportError:
        return _run_synthetic(payload)
    handle = _extract_handle(payload)
    if not handle:
        _emit({"event_type": "tool-run-error", "payload": {"reason": "missing 'handle' or 'profile_url'"}})
        return 2
    started = time.monotonic()
    url = f"https://twstalker.com/{handle}"
    _emit({"event_type": "tool-run-accepted", "payload": {"adapter": "twstalker", "handle": handle, "started_at": _now_iso()}})
    try:
        page = StealthyFetcher.fetch(url, headless=True, network_idle=True)
    except Exception as exc:
        _emit({"event_type": "tool-run-error", "payload": {"reason": f"scrapling {type(exc).__name__}: {exc}", "url": url, "suggest": "twstalker unreachable; try twitter_public for nitter fallback"}})
        return 3
    display_name = bio = location = joined = ""
    follower_count = following_count = tweet_count = 0
    try:
        name_el = page.css_first("h1, .profile-name, .user-fullname")
        display_name = (name_el.text or "").strip() if name_el else ""
        bio_el = page.css_first(".profile-bio, .user-description, .bio")
        bio = (bio_el.text or "").strip() if bio_el else ""
        loc_el = page.css_first(".profile-location, .user-location")
        location = (loc_el.text or "").strip() if loc_el else ""
        # twstalker often shows joined date in a span
        joined_el = page.css_first(".profile-joined, .join-date, .user-joined")
        joined = (joined_el.text or "").strip() if joined_el else ""
        # Counts -- twstalker layout varies; try several selectors
        body = page.html if hasattr(page, "html") else ""
        for label, var_setter in (
            ("Followers", "follower_count"),
            ("Following", "following_count"),
            ("Tweets", "tweet_count"),
        ):
            m = re.search(rf"([\d,.]+[KM]?)\s+{label}", body)
            if m:
                count = _parse_count(m.group(1))
                if var_setter == "follower_count":
                    follower_count = count
                elif var_setter == "following_count":
                    following_count = count
                else:
                    tweet_count = count
    except Exception as exc:
        sys.stderr.write(f"parse soft-fail: {type(exc).__name__}: {exc}\n")
    if display_name or follower_count or bio:
        _emit({
            "event_type": "person-match",
            "payload": {
                "source": "twstalker",
                "handle": handle,
                "display_name": display_name,
                "bio": bio,
                "location": location,
                "joined": joined,
                "follower_count": follower_count,
                "following_count": following_count,
                "tweet_count": tweet_count,
                "profile_url": url,
                "twitter_url": f"https://x.com/{handle}",
            },
        })
    _emit({"event_type": "tool-run-result", "payload": {"source": "twstalker", "handle": handle, "matches": 1 if display_name else 0, "duration_s": round(time.monotonic() - started, 2), "url": url}})
    return 0


def main() -> int:
    raw = sys.stdin.read().strip()
    if not raw:
        return 2
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return 2
    if os.environ.get("OSINT_ADAPTER_MODE") == "synthetic":
        return _run_synthetic(payload)
    return _run_live(payload)


if __name__ == "__main__":
    raise SystemExit(main())

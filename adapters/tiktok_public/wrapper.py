"""TikTok public-profile subprocess wrapper (Sprint 3+).

Scrapling/Patchright fetcher for a public TikTok profile.
Counts + bio only -- no follower lists, no video contents.

Contract:
  stdin:  {"handle": "alice"} OR {"profile_url": "https://www.tiktok.com/@alice"}
  stdout: NDJSON events
  exit:   0 on clean run; non-zero on failure

Live mode posture:
  - No login. Public-view fields only.
  - TikTok exposes follower/following/like/video counts on the public
    profile page; we extract those + display name + bio.
  - Anti-scraping: moderate. Less hostile than Instagram, more than
    Twitter. Expect occasional failures.

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
    m = re.search(r"tiktok\.com/@([A-Za-z0-9_.]+)", url)
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
    elif s.endswith("B"):
        mult = 1_000_000_000
        s = s[:-1]
    try:
        return int(float(s) * mult)
    except ValueError:
        return 0


def _run_synthetic(payload: dict) -> int:
    handle = _extract_handle(payload) or "synthetic_user"
    _emit(
        {
            "event_type": "tool-run-accepted",
            "payload": {"adapter": "tiktok_public", "handle": handle, "synthetic": True},
        }
    )
    _emit(
        {
            "event_type": "person-match",
            "payload": {
                "source": "tiktok",
                "handle": handle,
                "display_name": "Alice Synthetic",
                "bio": "Springfield local. Coffee + cats.",
                "follower_count": 4321,
                "following_count": 89,
                "like_count": 87654,
                "video_count": 42,
                "verified": False,
                "profile_url": f"https://www.tiktok.com/@{handle}",
                "synthetic": True,
            },
        }
    )
    _emit(
        {
            "event_type": "tool-run-result",
            "payload": {"source": "tiktok", "handle": handle, "matches": 1, "synthetic": True},
        }
    )
    return 0


def _run_live(payload: dict) -> int:
    try:
        from scrapling.fetchers import StealthyFetcher
    except ImportError:
        return _run_synthetic(payload)

    handle = _extract_handle(payload)
    if not handle:
        _emit(
            {
                "event_type": "tool-run-error",
                "payload": {"reason": "missing 'handle' or 'profile_url'"},
            }
        )
        return 2

    started = time.monotonic()
    url = f"https://www.tiktok.com/@{handle}"
    _emit(
        {
            "event_type": "tool-run-accepted",
            "payload": {
                "adapter": "tiktok_public",
                "handle": handle,
                "started_at": _now_iso(),
            },
        }
    )

    try:
        page = StealthyFetcher.fetch(url, headless=True, network_idle=True)
    except Exception as exc:
        _emit(
            {
                "event_type": "tool-run-error",
                "payload": {
                    "reason": f"scrapling {type(exc).__name__}: {exc}",
                    "url": url,
                    "suggest": "TikTok may be blocking; retry or use synthetic",
                },
            }
        )
        return 3

    display_name = ""
    bio = ""
    follower_count = 0
    following_count = 0
    like_count = 0
    video_count = 0
    verified = False
    try:
        # Display name + verified live in the user-page header.
        # TikTok ships heavy JS; stealth fetcher waits for network_idle
        # which usually gives us enough.
        name_el = page.css_first("h1[data-e2e='user-title'], h2[data-e2e='user-subtitle']")
        display_name = (name_el.text or "").strip() if name_el else ""
        bio_el = page.css_first("h2[data-e2e='user-bio']")
        bio = (bio_el.text or "").strip() if bio_el else ""
        # Stat counts have data-e2e attributes for stability
        followers_el = page.css_first("strong[data-e2e='followers-count']")
        if followers_el:
            follower_count = _parse_count(followers_el.text or "")
        following_el = page.css_first("strong[data-e2e='following-count']")
        if following_el:
            following_count = _parse_count(following_el.text or "")
        likes_el = page.css_first("strong[data-e2e='likes-count']")
        if likes_el:
            like_count = _parse_count(likes_el.text or "")
        # Video count -- TikTok shows it as a sibling of the avatar
        body_text = page.html if hasattr(page, "html") else ""
        m = re.search(r'"videoCount":(\d+)', body_text)
        if m:
            video_count = int(m.group(1))
        verified = '"verified":true' in body_text
    except Exception as exc:
        sys.stderr.write(f"parse soft-fail: {type(exc).__name__}: {exc}\n")

    if display_name or follower_count or bio:
        _emit(
            {
                "event_type": "person-match",
                "payload": {
                    "source": "tiktok",
                    "handle": handle,
                    "display_name": display_name,
                    "bio": bio,
                    "follower_count": follower_count,
                    "following_count": following_count,
                    "like_count": like_count,
                    "video_count": video_count,
                    "verified": verified,
                    "profile_url": url,
                },
            }
        )
    _emit(
        {
            "event_type": "tool-run-result",
            "payload": {
                "source": "tiktok",
                "handle": handle,
                "matches": 1 if (display_name or follower_count) else 0,
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

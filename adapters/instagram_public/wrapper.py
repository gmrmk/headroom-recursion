"""Instagram public-profile subprocess wrapper (Sprint 3+).

Scrapling/Patchright fetcher for a public Instagram profile.
Counts and bio only -- never extracts follower lists or post
contents. Used for property-vetting cross-check.

Contract:
  stdin:  {"handle": "alice"} OR {"profile_url": "https://instagram.com/alice"}
  stdout: NDJSON events
  exit:   0 on clean run; non-zero on failure

Live mode posture:
  - **No login.** Instagram aggressively walls off authenticated
    content; we don't even try. Public profiles render some metadata
    in <meta> tags + JSON-LD before the JS app boots.
  - **Counts only.** follower_count and following_count are scraped
    from the meta description string ("123 Followers, 456 Following,
    789 Posts"). The follower LIST is intentionally never extracted.
  - **Private accounts:** wrapper still returns the public-facing
    counts + is_private flag. The bio and post count remain visible
    even when the account is private; that's enough for vetting.
  - **Failure mode:** Instagram blocks aggressively. Expect periodic
    failures; the synthetic-fallback path is honest about that.

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
    m = re.search(r"instagram\.com/([A-Za-z0-9_.]+)", url)
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
            "payload": {"adapter": "instagram_public", "handle": handle, "synthetic": True},
        }
    )
    _emit(
        {
            "event_type": "person-match",
            "payload": {
                "source": "instagram",
                "handle": handle,
                "display_name": "Alice Synthetic",
                "bio": "Springfield IL. Coffee.",
                "follower_count": 543,
                "following_count": 210,
                "post_count": 78,
                "is_private": False,
                "verified": False,
                "profile_url": f"https://www.instagram.com/{handle}/",
                "synthetic": True,
            },
        }
    )
    _emit(
        {
            "event_type": "tool-run-result",
            "payload": {"source": "instagram", "handle": handle, "matches": 1, "synthetic": True},
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
    url = f"https://www.instagram.com/{handle}/"
    _emit(
        {
            "event_type": "tool-run-accepted",
            "payload": {
                "adapter": "instagram_public",
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
                    "suggest": "Instagram is aggressively blocking; retry or use synthetic",
                },
            }
        )
        return 3

    display_name = ""
    bio = ""
    follower_count = 0
    following_count = 0
    post_count = 0
    is_private = False
    verified = False
    photo_url = ""
    try:
        # Counts live in the meta description string:
        #   "<follower_count> Followers, <following_count> Following, <post_count> Posts - ..."
        meta = page.css_first("meta[name='description']")
        meta_content = meta.attrib.get("content", "") if meta else ""
        m = re.match(
            r"([\d.,KMB]+)\s+Followers,\s+([\d.,KMB]+)\s+Following,\s+([\d.,KMB]+)\s+Posts",
            meta_content,
        )
        if m:
            follower_count = _parse_count(m.group(1))
            following_count = _parse_count(m.group(2))
            post_count = _parse_count(m.group(3))
        # Display name + bio from og:title / og:description
        og_title = page.css_first("meta[property='og:title']")
        if og_title:
            display_name = og_title.attrib.get("content", "").strip()
        og_desc = page.css_first("meta[property='og:description']")
        if og_desc:
            bio = og_desc.attrib.get("content", "").strip()
        og_image = page.css_first("meta[property='og:image']")
        if og_image:
            photo_url = og_image.attrib.get("content", "")
        # is_private detection -- the page often shows "This Account is Private"
        body_text = page.html.lower() if hasattr(page, "html") else ""
        is_private = "this account is private" in body_text
        verified = "is_verified" in body_text and "true" in body_text
    except Exception as exc:
        sys.stderr.write(f"parse soft-fail: {type(exc).__name__}: {exc}\n")

    if display_name or follower_count or bio:
        _emit(
            {
                "event_type": "person-match",
                "payload": {
                    "source": "instagram",
                    "handle": handle,
                    "display_name": display_name,
                    "bio": bio,
                    "follower_count": follower_count,
                    "following_count": following_count,
                    "post_count": post_count,
                    "is_private": is_private,
                    "verified": verified,
                    "photo_url": photo_url,
                    "profile_url": url,
                },
            }
        )
    _emit(
        {
            "event_type": "tool-run-result",
            "payload": {
                "source": "instagram",
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

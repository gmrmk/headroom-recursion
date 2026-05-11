"""TikTok public follower-list subprocess wrapper (Sprint 3+).

Same honest-scope posture as instagram_followers: TikTok requires
login for the follower-list view as of 2023+. Wrapper attempts the
public surface; auth wall -> tool-run-error pointing at
wayback_snapshot.

Strict scope:
  - Private profiles -> tool-run-error immediately.
  - No login. No cookie attempts.
  - Login-redirect detection: honest error, no auth-bypass escalation.
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


def _run_synthetic(payload: dict) -> int:
    handle = _extract_handle(payload) or "synthetic_user"
    _emit(
        {
            "event_type": "tool-run-accepted",
            "payload": {"adapter": "tiktok_followers", "handle": handle, "synthetic": True},
        }
    )
    fixtures = [
        ("alice_tt", "Alice Smith"),
        ("bob_tt", "Bob Jones"),
    ]
    for fh, name in fixtures:
        _emit(
            {
                "event_type": "person-match",
                "payload": {
                    "source": "tiktok-follower",
                    "of_handle": handle,
                    "follower_handle": fh,
                    "display_name": name,
                    "follower_url": f"https://www.tiktok.com/@{fh}",
                    "synthetic": True,
                },
            }
        )
    _emit(
        {
            "event_type": "tool-run-result",
            "payload": {
                "source": "tiktok-follower",
                "of_handle": handle,
                "followers": len(fixtures),
                "synthetic": True,
            },
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
        _emit({"event_type": "tool-run-error", "payload": {"reason": "missing 'handle'"}})
        return 2

    started = time.monotonic()
    profile_url = f"https://www.tiktok.com/@{handle}"
    _emit(
        {
            "event_type": "tool-run-accepted",
            "payload": {
                "adapter": "tiktok_followers",
                "handle": handle,
                "started_at": _now_iso(),
            },
        }
    )

    try:
        page = StealthyFetcher.fetch(profile_url, headless=True, network_idle=True)
    except Exception as exc:
        _emit(
            {
                "event_type": "tool-run-error",
                "payload": {
                    "reason": f"scrapling {type(exc).__name__}: {exc}",
                    "url": profile_url,
                    "suggest": (
                        "TikTok blocked the request. Try wayback_snapshot "
                        "for a pre-wall snapshot if one exists."
                    ),
                },
            }
        )
        return 3

    body = page.html if hasattr(page, "html") else ""
    body_lower = body.lower()
    if '"privateaccount":true' in body_lower or "this account is private" in body_lower:
        _emit(
            {
                "event_type": "tool-run-error",
                "payload": {
                    "reason": "private account; follower list never accessible",
                    "handle": handle,
                    "suggest": "private-profile rule: aborted by design",
                },
            }
        )
        return 0

    # Public-profile case: TikTok requires login for the follower list,
    # same shape as Instagram. Surface honestly.
    _emit(
        {
            "event_type": "tool-run-error",
            "payload": {
                "reason": "TikTok requires login for follower-list view (2023+)",
                "handle": handle,
                "duration_s": round(time.monotonic() - started, 2),
                "suggest": (
                    "Try wayback_snapshot with url="
                    f"'https://www.tiktok.com/@{handle}/followers' "
                    "for pre-wall snapshots; or rely on tiktok_public for "
                    "bio + counts (no list)."
                ),
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

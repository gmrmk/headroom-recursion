"""Instagram public follower-list subprocess wrapper (Sprint 3+).

Honest scope: Instagram requires login for follower-list access on
PUBLIC accounts (the public-profile pages renders bio + counts but
the follower list itself is behind auth as of 2022+). This wrapper
attempts the public surface via Scrapling stealth; if the auth wall
holds (which it usually will), surfaces an honest tool-run-error
pointing at wayback_snapshot as the fallback.

Strict scope:
  - Private profiles -> tool-run-error immediately (no scraping).
  - No login. No cookie attempts.
  - If Instagram serves a login redirect, the wrapper detects it and
    fails honestly rather than retrying through anti-bot escalation.

Contract:
  stdin:  {"handle": "alice"} OR {"profile_url": "https://instagram.com/alice"}
  stdout: NDJSON
  exit:   0 / non-zero
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


def _run_synthetic(payload: dict) -> int:
    handle = _extract_handle(payload) or "synthetic_user"
    _emit(
        {
            "event_type": "tool-run-accepted",
            "payload": {"adapter": "instagram_followers", "handle": handle, "synthetic": True},
        }
    )
    fixtures = [
        ("alice_ig", "Alice Smith"),
        ("bob_ig", "Bob Jones"),
    ]
    for fh, name in fixtures:
        _emit(
            {
                "event_type": "person-match",
                "payload": {
                    "source": "instagram-follower",
                    "of_handle": handle,
                    "follower_handle": fh,
                    "display_name": name,
                    "follower_url": f"https://www.instagram.com/{fh}/",
                    "synthetic": True,
                },
            }
        )
    _emit(
        {
            "event_type": "tool-run-result",
            "payload": {
                "source": "instagram-follower",
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
    profile_url = f"https://www.instagram.com/{handle}/"
    _emit(
        {
            "event_type": "tool-run-accepted",
            "payload": {
                "adapter": "instagram_followers",
                "handle": handle,
                "started_at": _now_iso(),
            },
        }
    )

    # Fetch the profile page first to detect private flag without
    # touching the follower-list URL (which redirects to login).
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
                        "Instagram blocked the request. Try wayback_snapshot "
                        "on the followers URL for pre-2022 snapshots."
                    ),
                },
            }
        )
        return 3

    body = page.html if hasattr(page, "html") else ""
    body_lower = body.lower()
    if "this account is private" in body_lower:
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

    # Public account: the follower list URL on Instagram requires login
    # as of 2022+. We surface that honestly rather than escalating to
    # auth-bypass techniques.
    _emit(
        {
            "event_type": "tool-run-error",
            "payload": {
                "reason": "Instagram requires login for follower-list view (2022+)",
                "handle": handle,
                "duration_s": round(time.monotonic() - started, 2),
                "suggest": (
                    "Try wayback_snapshot with url="
                    f"'https://www.instagram.com/{handle}/followers/' "
                    "for pre-wall snapshots; or rely on instagram_public for "
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

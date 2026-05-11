"""Twitter/X public follower-list subprocess wrapper (Sprint 3+).

Articulating-link investigation: list a public Twitter handle's
followers so the investigator can scan for the property's legal-owner
name. Public accounts ONLY -- private profile detected -> tool-run-error.

Auth-wall reality:
  - x.com requires login for /<handle>/followers as of 2023+.
  - Nitter mirrors that still expose follower lists are the only
    no-login surface. Mileage varies per instance; OSINT_NITTER_BASE
    env overrides the default.
  - If both routes fail, the wrapper surfaces an honest tool-run-error
    pointing at wayback_snapshot as the fallback (pre-2023 follower
    page snapshots may still exist).

Strict scope:
  - Public accounts only.
  - No login attempt.
  - Follower handles + display names + bio snippets only (everything
    nitter exposes on the follower-list page).

Contract:
  stdin:  {"handle": "alice"} OR {"profile_url": "..."}, optional "limit"
  stdout: NDJSON person-match per follower + tool-run-result
  exit:   0 / non-zero on failure
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
            "payload": {"adapter": "twitter_followers", "handle": handle, "synthetic": True},
        }
    )
    fixtures = [
        ("alice_real", "Alice Smith"),
        ("bob_real", "Bob Jones"),
        ("carol_real", "Carol Wong"),
    ]
    for fh, name in fixtures:
        _emit(
            {
                "event_type": "person-match",
                "payload": {
                    "source": "twitter-follower",
                    "of_handle": handle,
                    "follower_handle": fh,
                    "display_name": name,
                    "bio_snippet": "Springfield local. Real-estate adjacent.",
                    "follower_url": f"https://x.com/{fh}",
                    "fetched_via": "nitter",
                    "synthetic": True,
                },
            }
        )
    _emit(
        {
            "event_type": "tool-run-result",
            "payload": {
                "source": "twitter-follower",
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
        sys.stderr.write("scrapling not importable; synthetic fallback\n")
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

    limit = min(int(payload.get("limit", 50)), 200)
    started = time.monotonic()
    _emit(
        {
            "event_type": "tool-run-accepted",
            "payload": {
                "adapter": "twitter_followers",
                "handle": handle,
                "started_at": _now_iso(),
            },
        }
    )

    nitter_base = os.environ.get("OSINT_NITTER_BASE", "https://nitter.poast.org")
    url = f"{nitter_base}/{handle}/followers"

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
                        "nitter mirror down or follower-list disabled. "
                        "Try wayback_snapshot on this URL for a pre-wall snapshot."
                    ),
                },
            }
        )
        return 3

    # Private-account guard: nitter shows "This account is private" text.
    try:
        body = page.html if hasattr(page, "html") else ""
    except Exception:
        body = ""
    if "this account is private" in body.lower() or "tweets are protected" in body.lower():
        _emit(
            {
                "event_type": "tool-run-error",
                "payload": {
                    "reason": "account is private; follower list not extracted",
                    "handle": handle,
                    "suggest": "private-profile rule: aborted by design",
                },
            }
        )
        return 0  # not an error per se; honest scope close

    # Nitter follower cards: .timeline-item with .profile-card-tiny inside.
    matches = []
    try:
        cards = page.css(
            ".timeline-item, .profile-card-tiny, .follow-card, .profile-tabs li"
        )[: limit + 10]
        seen: set[str] = set()
        for card in cards:
            name_el = card.css_first("a.username, .fullname, .tweet-name-row a")
            href = name_el.attrib.get("href", "") if name_el else ""
            m = re.search(r"/([A-Za-z0-9_]+)/?$", href)
            if not m:
                continue
            fh = m.group(1)
            if fh in seen or fh.lower() == handle.lower():
                continue
            seen.add(fh)
            display_el = card.css_first(".fullname")
            display_name = (display_el.text or "").strip() if display_el else ""
            bio_el = card.css_first(".profile-bio, .tweet-content")
            bio = (bio_el.text or "").strip() if bio_el else ""
            matches.append(
                {
                    "source": "twitter-follower",
                    "of_handle": handle,
                    "follower_handle": fh,
                    "display_name": display_name,
                    "bio_snippet": bio[:200],
                    "follower_url": f"https://x.com/{fh}",
                    "fetched_via": "nitter",
                }
            )
            if len(matches) >= limit:
                break
    except Exception as exc:
        sys.stderr.write(f"parse soft-fail: {type(exc).__name__}: {exc}\n")

    for m in matches:
        _emit({"event_type": "person-match", "payload": m})
    _emit(
        {
            "event_type": "tool-run-result",
            "payload": {
                "source": "twitter-follower",
                "of_handle": handle,
                "followers": len(matches),
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

"""Property-vetting adapters (R-5 Sprint 2).

Six-primitive triangulation for the user's pivoted use case
(2026-05-11): verify the legitimacy of a property listing's
named owner/host across vetted public sources. The primitives
this adapter set covers:

  Address  -> lat/lon       :: nominatim_geocode
  Email    -> deliverability :: email_mx_validate
  Email    -> breach hits    :: hibp_breach_check

The remaining three (TruePeopleSearch / Inside Airbnb /
TinEye) require Scrapling or signed API access and ship as
stub registrations with synthetic_mode only, gated on the
Sprint-3 Scrapling work.

All three implemented adapters are in-process HTTPS calls
against free public services. No AGPL imports, so subprocess
isolation is unnecessary. The adapter registry's contract
(callable returns list[dict]) is satisfied directly.

Rate limits respected:
  - Nominatim: 1 req/sec absolute max; we sleep before
    returning so callers cannot accidentally burst-loop.
  - HIBP (Have I Been Pwned) v3 free tier: 6 req/min for
    the breach-by-account endpoint with API key. The free
    'breach-by-name' endpoint we use here is unlimited.

User-Agent contract: Nominatim and HIBP both require a
descriptive User-Agent identifying the application; we
pin a stable string so they can rate-limit us specifically
rather than nuking the whole IP if abused.
"""

from __future__ import annotations

import csv
import os
import re
import socket
import time
from pathlib import Path
from typing import Any

import httpx

from .adapters import get_registry
from .subprocess_adapter import make_subprocess_adapter

# Pinned: the empirical venv ships Scrapling + Patchright + Playwright.
# The worker's own .venv intentionally does NOT ship these (heavy + only
# needed by anti-scraping wrappers); the wrappers run via this interpreter.
_EMPIRICAL_PY = (
    Path(r"C:\Users\strid\osint-dashboard-research\empirical\.venv\Scripts\python.exe")
    if os.name == "nt"
    else Path("/c/Users/strid/osint-dashboard-research/empirical/.venv/bin/python")
)
_REPO_ROOT_PROP = Path(__file__).resolve().parents[4]

# Stable identifier surfaced to upstream services. Email is OPSEC-leaky
# if used directly; the env var lets the deploy override with a real
# contact address that the user is OK exposing to OSM/HIBP.
_DEFAULT_UA = "osint-goblin/0.1 (https://github.com/local; personal-investigator)"
_USER_AGENT = os.environ.get("OSINT_USER_AGENT", _DEFAULT_UA)

# Nominatim's published usage policy: max 1 req/sec sustained.
_NOMINATIM_MIN_INTERVAL_S = 1.0
_NOMINATIM_LAST_CALL_AT: float = 0.0

# Simple RFC-5322-ish email regex. Not exhaustive; rejects obvious
# garbage and lets the MX check catch the rest. Anti-pattern would be
# trying to validate every email RFC-perfectly here.
_EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+\-]+@([a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})$")


def _client(timeout_s: float = 10.0) -> httpx.Client:
    """Shared client config. UA + sane timeout + redirects off
    (we are not in the business of chasing redirects that we
    didn't anticipate)."""
    return httpx.Client(
        timeout=timeout_s,
        headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
        follow_redirects=False,
    )


# ---------------------------------------------------------------------------
# 1. Nominatim geocode (OSM, free, address -> lat/lon)
# ---------------------------------------------------------------------------


def nominatim_geocode(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Resolve an address string to lat/lon via OpenStreetMap Nominatim.

    Payload:
      {"q": "123 Main St, Springfield IL"}  -- or "address"

    Emits one `geocode-match` event per top-3 result + one
    `tool-run-result` summary. Caps at top-3 because Nominatim
    can return 10+ matches and the dossier UX is "give me the
    most likely answer, not a long tail."
    """
    global _NOMINATIM_MIN_INTERVAL_S, _NOMINATIM_LAST_CALL_AT
    query = payload.get("q") or payload.get("address") or ""
    if not isinstance(query, str) or not query.strip():
        return [
            {
                "event_type": "tool-run-error",
                "payload": {"reason": "missing 'q' or 'address' in payload"},
            }
        ]

    # Self-throttle: enforce >=1s between calls per the OSM policy.
    elapsed = time.monotonic() - _NOMINATIM_LAST_CALL_AT
    if elapsed < _NOMINATIM_MIN_INTERVAL_S:
        time.sleep(_NOMINATIM_MIN_INTERVAL_S - elapsed)

    try:
        with _client(timeout_s=5.0) as c:
            r = c.get(
                "https://nominatim.openstreetmap.org/search",
                params={"q": query, "format": "json", "addressdetails": "1", "limit": "3"},
            )
        _NOMINATIM_LAST_CALL_AT = time.monotonic()
        if r.status_code != 200:
            return [
                {
                    "event_type": "tool-run-error",
                    "payload": {"reason": f"nominatim HTTP {r.status_code}", "query": query},
                }
            ]
        results = r.json()
    except httpx.RequestError as exc:
        return [
            {
                "event_type": "tool-run-error",
                "payload": {"reason": f"nominatim {type(exc).__name__}: {exc}", "query": query},
            }
        ]

    if not isinstance(results, list) or not results:
        return [
            {
                "event_type": "tool-run-result",
                "payload": {"query": query, "matches": 0},
            }
        ]

    events: list[dict[str, Any]] = []
    for hit in results[:3]:
        if not isinstance(hit, dict):
            continue
        events.append(
            {
                "event_type": "geocode-match",
                "payload": {
                    "query": query,
                    "lat": float(hit.get("lat", 0)) or None,
                    "lon": float(hit.get("lon", 0)) or None,
                    "display_name": hit.get("display_name", ""),
                    "place_id": hit.get("place_id"),
                    "type": hit.get("type", ""),
                    "importance": hit.get("importance", 0.0),
                    "address": hit.get("address", {}),
                },
            }
        )
    events.append(
        {
            "event_type": "tool-run-result",
            "payload": {"query": query, "matches": len(events)},
        }
    )
    return events


def _nominatim_synthetic(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Synthetic: returns a single deterministic match for the M0 path
    without touching the network."""
    query = payload.get("q") or payload.get("address") or "Synthetic Address"
    return [
        {
            "event_type": "geocode-match",
            "payload": {
                "query": query,
                "lat": 39.78,
                "lon": -89.65,
                "display_name": "123 Synthetic St, Springfield, IL, USA",
                "place_id": 0,
                "type": "house",
                "importance": 0.5,
                "synthetic": True,
            },
        },
        {
            "event_type": "tool-run-result",
            "payload": {"query": query, "matches": 1, "synthetic": True},
        },
    ]


# ---------------------------------------------------------------------------
# 2. Email MX validate (DNS lookup, no third-party service)
# ---------------------------------------------------------------------------


def email_mx_validate(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Validate an email address: format + MX record existence.

    Payload:
      {"email": "user@example.com"}

    Emits one `tool-run-result` event with the validity verdict.
    Uses `socket.getaddrinfo` for MX-host A-record probe rather
    than `dnspython` to avoid a new dependency for one call.
    """
    email = payload.get("email", "")
    if not isinstance(email, str):
        return [
            {
                "event_type": "tool-run-error",
                "payload": {"reason": "email field must be a string"},
            }
        ]
    match = _EMAIL_RE.match(email)
    if not match:
        return [
            {
                "event_type": "tool-run-result",
                "payload": {"email": email, "valid_format": False, "deliverable": False},
            }
        ]
    domain = match.group(1)
    # We can't query MX records without dnspython, but checking the
    # domain has any A record is a strong proxy: no A record == domain
    # doesn't exist == email is undeliverable. False positives possible
    # for MX-only setups; acceptable for the property-vetting workflow
    # where we're catching typos, not enterprise edge cases.
    try:
        socket.getaddrinfo(domain, None)
        deliverable = True
        reason = ""
    except socket.gaierror as exc:
        deliverable = False
        reason = f"DNS lookup failed: {exc}"
    return [
        {
            "event_type": "tool-run-result",
            "payload": {
                "email": email,
                "valid_format": True,
                "deliverable": deliverable,
                "reason": reason,
                "domain": domain,
            },
        }
    ]


def _email_mx_synthetic(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Synthetic: pretends all well-formatted emails are deliverable."""
    email = payload.get("email", "")
    match = _EMAIL_RE.match(email) if isinstance(email, str) else None
    return [
        {
            "event_type": "tool-run-result",
            "payload": {
                "email": email,
                "valid_format": bool(match),
                "deliverable": bool(match),
                "synthetic": True,
            },
        }
    ]


# ---------------------------------------------------------------------------
# 3. HIBP breach check (Have I Been Pwned, free 'breach-by-name' endpoint)
# ---------------------------------------------------------------------------
# The breach-by-name endpoint is unauthenticated and unlimited; it
# lists known breaches but does NOT confirm membership of a specific
# email (that requires the paid v3 by-account endpoint). For the
# property-vetting use case we use it as "is this email's domain
# associated with known breaches?" -- a proxy signal that the host's
# email might be on a leaked list. Future: upgrade to v3 by-account
# with an API key for direct hit verification.


def hibp_breach_check(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Look up breaches for an email's domain via HIBP free endpoint.

    Payload:
      {"email": "user@example.com"}  -- or {"domain": "example.com"}

    Emits one `breach-hit` event per breach + a summary
    `tool-run-result`. Free endpoint does NOT confirm the specific
    email is in the breach; it lists breaches affecting the domain
    so the investigator can decide on follow-up.
    """
    email = payload.get("email", "")
    domain = payload.get("domain", "")
    if isinstance(email, str) and "@" in email and not domain:
        domain = email.split("@", 1)[1].strip().lower()
    if not domain or not isinstance(domain, str):
        return [
            {
                "event_type": "tool-run-error",
                "payload": {"reason": "missing 'email' or 'domain'"},
            }
        ]

    try:
        with _client(timeout_s=10.0) as c:
            r = c.get(
                "https://haveibeenpwned.com/api/v3/breaches",
                params={"domain": domain},
            )
        if r.status_code == 404:
            return [
                {
                    "event_type": "tool-run-result",
                    "payload": {"domain": domain, "breaches": 0},
                }
            ]
        if r.status_code != 200:
            return [
                {
                    "event_type": "tool-run-error",
                    "payload": {"reason": f"hibp HTTP {r.status_code}", "domain": domain},
                }
            ]
        breaches = r.json()
    except httpx.RequestError as exc:
        return [
            {
                "event_type": "tool-run-error",
                "payload": {"reason": f"hibp {type(exc).__name__}: {exc}", "domain": domain},
            }
        ]

    if not isinstance(breaches, list):
        breaches = []
    events: list[dict[str, Any]] = []
    for b in breaches[:10]:  # cap dossier noise
        if not isinstance(b, dict):
            continue
        events.append(
            {
                "event_type": "breach-hit",
                "payload": {
                    "domain": domain,
                    "name": b.get("Name", ""),
                    "title": b.get("Title", ""),
                    "breach_date": b.get("BreachDate", ""),
                    "pwn_count": b.get("PwnCount", 0),
                    "data_classes": b.get("DataClasses", []),
                    "verified": b.get("IsVerified", False),
                },
            }
        )
    events.append(
        {
            "event_type": "tool-run-result",
            "payload": {"domain": domain, "breaches": len(events)},
        }
    )
    return events


def _hibp_synthetic(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Synthetic: a single fake breach to exercise the wire shape."""
    email = payload.get("email", "")
    domain = email.split("@", 1)[1] if "@" in email else (payload.get("domain") or "synthetic.test")
    return [
        {
            "event_type": "breach-hit",
            "payload": {
                "domain": domain,
                "name": "SyntheticBreach2024",
                "title": "Synthetic Breach (Test Fixture)",
                "breach_date": "2024-01-01",
                "pwn_count": 1,
                "data_classes": ["Email addresses"],
                "verified": False,
                "synthetic": True,
            },
        },
        {
            "event_type": "tool-run-result",
            "payload": {"domain": domain, "breaches": 1, "synthetic": True},
        },
    ]


# ---------------------------------------------------------------------------
# 3b. GitHub public profile (LinkedIn-alt for tech hosts)
# ---------------------------------------------------------------------------


def github_profile(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Fetch a public GitHub profile via the v3 REST API.

    Payload (one of):
      {"username": "octocat"}
      {"profile_url": "https://github.com/octocat"}

    GitHub's free unauth rate limit is 60 req/hour per IP -- generous
    for personal investigation. Returns a single person-match with the
    public profile fields: name, bio, current company, location, blog,
    public-repo count, follower count, account creation date.
    """
    username = (payload.get("username") or "").strip()
    profile_url = (payload.get("profile_url") or "").strip()
    if not username and profile_url:
        m = re.search(r"github\.com/([A-Za-z0-9\-]+)/?$", profile_url)
        if m:
            username = m.group(1)
    if not username:
        return [
            {
                "event_type": "tool-run-error",
                "payload": {"reason": "missing 'username' or 'profile_url'"},
            }
        ]

    try:
        with _client(timeout_s=8.0) as c:
            r = c.get(f"https://api.github.com/users/{username}")
        if r.status_code == 404:
            return [
                {
                    "event_type": "tool-run-result",
                    "payload": {"username": username, "matches": 0},
                }
            ]
        if r.status_code != 200:
            return [
                {
                    "event_type": "tool-run-error",
                    "payload": {
                        "reason": f"github HTTP {r.status_code}",
                        "username": username,
                    },
                }
            ]
        u = r.json()
    except httpx.RequestError as exc:
        return [
            {
                "event_type": "tool-run-error",
                "payload": {
                    "reason": f"github {type(exc).__name__}: {exc}",
                    "username": username,
                },
            }
        ]

    return [
        {
            "event_type": "person-match",
            "payload": {
                "source": "github",
                "username": username,
                "name": u.get("name") or "",
                "bio": u.get("bio") or "",
                "current_company": u.get("company") or "",
                "location": u.get("location") or "",
                "blog": u.get("blog") or "",
                "profile_url": u.get("html_url") or f"https://github.com/{username}",
                "photo_url": u.get("avatar_url") or "",
                "public_repos": u.get("public_repos", 0),
                "followers": u.get("followers", 0),
                "created_at": u.get("created_at") or "",
            },
        },
        {
            "event_type": "tool-run-result",
            "payload": {"username": username, "matches": 1},
        },
    ]


def _github_synthetic(payload: dict[str, Any]) -> list[dict[str, Any]]:
    username = payload.get("username") or "octocat-synthetic"
    return [
        {
            "event_type": "person-match",
            "payload": {
                "source": "github",
                "username": username,
                "name": "Alice Synthetic",
                "bio": "Engineer. Coffee enthusiast.",
                "current_company": "@Synthetic-Co",
                "location": "Springfield, IL",
                "blog": "https://example.com",
                "profile_url": f"https://github.com/{username}",
                "photo_url": "https://avatars.githubusercontent.com/synthetic.jpg",
                "public_repos": 42,
                "followers": 100,
                "created_at": "2018-03-15T09:00:00Z",
                "synthetic": True,
            },
        },
        {
            "event_type": "tool-run-result",
            "payload": {"username": username, "matches": 1, "synthetic": True},
        },
    ]


# ---------------------------------------------------------------------------
# 3c. Wayback Machine snapshot of a LinkedIn URL (LinkedIn-availability fallback)
# ---------------------------------------------------------------------------


def wayback_linkedin(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Check the Wayback Machine for a snapshot of a LinkedIn profile URL.

    Payload:
      {"profile_url": "https://www.linkedin.com/in/<handle>"}

    Uses archive.org's free availability API
    (http://archive.org/wayback/available?url=<URL>). Emits one
    `person-match` per available snapshot (typically the closest one)
    with the wayback-snapshot URL + timestamp. The investigator then
    opens the snapshot URL in a browser to view the historical
    profile state -- load-bearing when LinkedIn directly is blocking.

    Does NOT scrape the snapshot's contents here. Browsers handle the
    Wayback rendering better than a stealth fetcher would, and the
    snapshot URL is the durable artifact.
    """
    profile_url = (payload.get("profile_url") or "").strip()
    if not profile_url:
        return [
            {
                "event_type": "tool-run-error",
                "payload": {"reason": "missing 'profile_url' in payload"},
            }
        ]

    try:
        with _client(timeout_s=8.0) as c:
            r = c.get(
                "http://archive.org/wayback/available",
                params={"url": profile_url},
            )
        if r.status_code != 200:
            return [
                {
                    "event_type": "tool-run-error",
                    "payload": {
                        "reason": f"wayback HTTP {r.status_code}",
                        "url": profile_url,
                    },
                }
            ]
        data = r.json()
    except httpx.RequestError as exc:
        return [
            {
                "event_type": "tool-run-error",
                "payload": {
                    "reason": f"wayback {type(exc).__name__}: {exc}",
                    "url": profile_url,
                },
            }
        ]

    closest = (data.get("archived_snapshots") or {}).get("closest") or {}
    if not closest or not closest.get("available"):
        return [
            {
                "event_type": "tool-run-result",
                "payload": {"profile_url": profile_url, "snapshots": 0},
            }
        ]

    return [
        {
            "event_type": "person-match",
            "payload": {
                "source": "wayback-linkedin",
                "profile_url": profile_url,
                "snapshot_url": closest.get("url", ""),
                "snapshot_timestamp": closest.get("timestamp", ""),
                "snapshot_status": closest.get("status", ""),
            },
        },
        {
            "event_type": "tool-run-result",
            "payload": {"profile_url": profile_url, "snapshots": 1},
        },
    ]


def _wayback_synthetic(payload: dict[str, Any]) -> list[dict[str, Any]]:
    profile_url = payload.get("profile_url") or "https://www.linkedin.com/in/synthetic"
    return [
        {
            "event_type": "person-match",
            "payload": {
                "source": "wayback-linkedin",
                "profile_url": profile_url,
                "snapshot_url": "https://web.archive.org/web/20241001120000/" + profile_url,
                "snapshot_timestamp": "20241001120000",
                "snapshot_status": "200",
                "synthetic": True,
            },
        },
        {
            "event_type": "tool-run-result",
            "payload": {"profile_url": profile_url, "snapshots": 1, "synthetic": True},
        },
    ]


# ---------------------------------------------------------------------------
# 3d. Public follower-list adapters (articulating-link investigation)
# ---------------------------------------------------------------------------
# These adapters list a public account's followers so the investigator can
# scan for known names (e.g. the property's legal owner per public records).
# Strict scope: PUBLIC accounts only. Private profile -> tool-run-error,
# never bypass. The walled platforms (Twitter/Instagram/TikTok) live in
# adapters/<id>/wrapper.py with Scrapling; the cooperative ones here use
# plain httpx.


def github_followers(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """List a GitHub user's public followers via REST v3.

    Payload:
      {"username": "octocat"}  # one of these is required
      {"profile_url": "https://github.com/octocat"}
      {"limit": 100}           # optional, cap (default 100; max 300)

    Emits one `person-match` per follower + a summary. GitHub follower
    lists are public by API design (no login flow); rate-limited at
    60 req/hour unauth.
    """
    username = (payload.get("username") or "").strip()
    profile_url = (payload.get("profile_url") or "").strip()
    if not username and profile_url:
        m = re.search(r"github\.com/([A-Za-z0-9\-]+)/?$", profile_url)
        if m:
            username = m.group(1)
    if not username:
        return [
            {
                "event_type": "tool-run-error",
                "payload": {"reason": "missing 'username' or 'profile_url'"},
            }
        ]
    limit = min(int(payload.get("limit", 100)), 300)
    per_page = 100

    followers: list[dict[str, Any]] = []
    try:
        with _client(timeout_s=10.0) as c:
            page_num = 1
            while len(followers) < limit:
                r = c.get(
                    f"https://api.github.com/users/{username}/followers",
                    params={"per_page": per_page, "page": page_num},
                )
                if r.status_code == 404:
                    return [
                        {
                            "event_type": "tool-run-result",
                            "payload": {"username": username, "followers": 0},
                        }
                    ]
                if r.status_code != 200:
                    return [
                        {
                            "event_type": "tool-run-error",
                            "payload": {
                                "reason": f"github HTTP {r.status_code}",
                                "username": username,
                            },
                        }
                    ]
                batch = r.json()
                if not isinstance(batch, list) or not batch:
                    break
                followers.extend(batch)
                if len(batch) < per_page:
                    break
                page_num += 1
    except httpx.RequestError as exc:
        return [
            {
                "event_type": "tool-run-error",
                "payload": {
                    "reason": f"github {type(exc).__name__}: {exc}",
                    "username": username,
                },
            }
        ]

    followers = followers[:limit]
    events: list[dict[str, Any]] = []
    for f in followers:
        if not isinstance(f, dict):
            continue
        events.append(
            {
                "event_type": "person-match",
                "payload": {
                    "source": "github-follower",
                    "of_user": username,
                    "follower_login": f.get("login", ""),
                    "follower_url": f.get("html_url") or f"https://github.com/{f.get('login', '')}",
                    "photo_url": f.get("avatar_url", ""),
                },
            }
        )
    events.append(
        {
            "event_type": "tool-run-result",
            "payload": {"username": username, "followers": len(events) - 0},
        }
    )
    return events


def _github_followers_synthetic(payload: dict[str, Any]) -> list[dict[str, Any]]:
    username = payload.get("username") or "octocat-synthetic"
    fixtures = [
        ("alice-synthetic", "Alice Synthetic"),
        ("bob-synthetic", "Bob Synthetic"),
        ("carol-synthetic", "Carol Synthetic"),
    ]
    events = [
        {
            "event_type": "person-match",
            "payload": {
                "source": "github-follower",
                "of_user": username,
                "follower_login": login,
                "follower_url": f"https://github.com/{login}",
                "photo_url": f"https://avatars.githubusercontent.com/{login}.jpg",
                "synthetic": True,
            },
        }
        for login, _ in fixtures
    ]
    events.append(
        {
            "event_type": "tool-run-result",
            "payload": {"username": username, "followers": len(fixtures), "synthetic": True},
        }
    )
    return events


def bluesky_followers(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """List a Bluesky handle's followers via the public AT Protocol API.

    Payload:
      {"handle": "alice.bsky.social"}  # bare or with @ prefix; one required
      {"profile_url": "https://bsky.app/profile/alice.bsky.social"}
      {"limit": 100}                   # default 100, max 300

    Public API at public.api.bsky.app -- no auth required for public reads.
    """
    handle = (payload.get("handle") or "").strip().lstrip("@")
    if not handle:
        url = (payload.get("profile_url") or "").strip()
        m = re.search(r"bsky\.app/profile/([A-Za-z0-9.\-_]+)", url)
        if m:
            handle = m.group(1)
    if not handle:
        return [
            {
                "event_type": "tool-run-error",
                "payload": {"reason": "missing 'handle' or 'profile_url'"},
            }
        ]
    limit = min(int(payload.get("limit", 100)), 300)
    page_size = 100

    followers: list[dict[str, Any]] = []
    cursor: str | None = None
    try:
        with _client(timeout_s=10.0) as c:
            while len(followers) < limit:
                params: dict[str, str] = {"actor": handle, "limit": str(page_size)}
                if cursor:
                    params["cursor"] = cursor
                r = c.get(
                    "https://public.api.bsky.app/xrpc/app.bsky.graph.getFollowers",
                    params=params,
                )
                if r.status_code != 200:
                    return [
                        {
                            "event_type": "tool-run-error",
                            "payload": {
                                "reason": f"bluesky HTTP {r.status_code}",
                                "handle": handle,
                            },
                        }
                    ]
                data = r.json() or {}
                batch = data.get("followers") or []
                if not batch:
                    break
                followers.extend(batch)
                cursor = data.get("cursor")
                if not cursor or len(batch) < page_size:
                    break
    except httpx.RequestError as exc:
        return [
            {
                "event_type": "tool-run-error",
                "payload": {
                    "reason": f"bluesky {type(exc).__name__}: {exc}",
                    "handle": handle,
                },
            }
        ]

    followers = followers[:limit]
    events: list[dict[str, Any]] = []
    for f in followers:
        if not isinstance(f, dict):
            continue
        f_handle = f.get("handle", "")
        events.append(
            {
                "event_type": "person-match",
                "payload": {
                    "source": "bluesky-follower",
                    "of_handle": handle,
                    "follower_handle": f_handle,
                    "follower_did": f.get("did", ""),
                    "display_name": f.get("displayName", ""),
                    "follower_url": f"https://bsky.app/profile/{f_handle}" if f_handle else "",
                    "photo_url": f.get("avatar", ""),
                },
            }
        )
    events.append(
        {
            "event_type": "tool-run-result",
            "payload": {"handle": handle, "followers": len(events) - 0},
        }
    )
    return events


def _bluesky_followers_synthetic(payload: dict[str, Any]) -> list[dict[str, Any]]:
    handle = payload.get("handle") or "alice.bsky.social"
    fixtures = [
        ("bob.bsky.social", "Bob Synthetic"),
        ("carol.bsky.social", "Carol Synthetic"),
    ]
    events = [
        {
            "event_type": "person-match",
            "payload": {
                "source": "bluesky-follower",
                "of_handle": handle,
                "follower_handle": fh,
                "follower_did": f"did:plc:synthetic-{i}",
                "display_name": name,
                "follower_url": f"https://bsky.app/profile/{fh}",
                "synthetic": True,
            },
        }
        for i, (fh, name) in enumerate(fixtures)
    ]
    events.append(
        {
            "event_type": "tool-run-result",
            "payload": {"handle": handle, "followers": len(fixtures), "synthetic": True},
        }
    )
    return events


def mastodon_followers(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """List a Mastodon account's followers via the public REST API.

    Payload (one of):
      {"acct": "alice@mastodon.social"}        # webfinger-style handle
      {"profile_url": "https://mastodon.social/@alice"}
      {"limit": 80}                            # default 80, max 240

    Mastodon's `/api/v1/accounts/<id>/followers` is public on most
    instances; the wrapper does the account-id lookup first.
    """
    acct = (payload.get("acct") or "").strip().lstrip("@")
    profile_url = (payload.get("profile_url") or "").strip()
    instance = ""
    handle = ""
    if acct and "@" in acct:
        handle, instance = acct.split("@", 1)
    elif profile_url:
        m = re.match(r"https?://([^/]+)/@([A-Za-z0-9_]+)/?", profile_url)
        if m:
            instance = m.group(1)
            handle = m.group(2)
            acct = f"{handle}@{instance}"
    if not instance or not handle:
        return [
            {
                "event_type": "tool-run-error",
                "payload": {
                    "reason": ("need 'acct' (e.g. alice@mastodon.social) or " "full 'profile_url'"),
                },
            }
        ]
    limit = min(int(payload.get("limit", 80)), 240)

    try:
        with _client(timeout_s=10.0) as c:
            # Step 1: account-id lookup
            lookup = c.get(
                f"https://{instance}/api/v1/accounts/lookup",
                params={"acct": acct},
            )
            if lookup.status_code == 404:
                return [
                    {
                        "event_type": "tool-run-result",
                        "payload": {"acct": acct, "followers": 0},
                    }
                ]
            if lookup.status_code != 200:
                return [
                    {
                        "event_type": "tool-run-error",
                        "payload": {
                            "reason": f"mastodon lookup HTTP {lookup.status_code}",
                            "acct": acct,
                        },
                    }
                ]
            account = lookup.json()
            account_id = account.get("id", "")
            if not account_id:
                return [
                    {
                        "event_type": "tool-run-error",
                        "payload": {"reason": "mastodon lookup returned no id", "acct": acct},
                    }
                ]
            # Step 2: followers list
            r = c.get(
                f"https://{instance}/api/v1/accounts/{account_id}/followers",
                params={"limit": min(limit, 80)},
            )
            if r.status_code != 200:
                return [
                    {
                        "event_type": "tool-run-error",
                        "payload": {
                            "reason": f"mastodon followers HTTP {r.status_code}",
                            "acct": acct,
                        },
                    }
                ]
            followers = r.json() or []
    except httpx.RequestError as exc:
        return [
            {
                "event_type": "tool-run-error",
                "payload": {
                    "reason": f"mastodon {type(exc).__name__}: {exc}",
                    "acct": acct,
                },
            }
        ]

    if not isinstance(followers, list):
        followers = []
    followers = followers[:limit]
    events: list[dict[str, Any]] = []
    for f in followers:
        if not isinstance(f, dict):
            continue
        events.append(
            {
                "event_type": "person-match",
                "payload": {
                    "source": "mastodon-follower",
                    "of_acct": acct,
                    "follower_acct": f.get("acct", ""),
                    "follower_username": f.get("username", ""),
                    "display_name": f.get("display_name", ""),
                    "follower_url": f.get("url", ""),
                    "photo_url": f.get("avatar", ""),
                    "created_at": f.get("created_at", ""),
                    "follower_followers_count": f.get("followers_count", 0),
                },
            }
        )
    events.append(
        {
            "event_type": "tool-run-result",
            "payload": {"acct": acct, "followers": len(events) - 0},
        }
    )
    return events


def _mastodon_followers_synthetic(payload: dict[str, Any]) -> list[dict[str, Any]]:
    acct = payload.get("acct") or "alice@mastodon.social"
    fixtures = [
        ("bob@mastodon.social", "Bob Synthetic"),
        ("carol@hachyderm.io", "Carol Synthetic"),
    ]
    events = [
        {
            "event_type": "person-match",
            "payload": {
                "source": "mastodon-follower",
                "of_acct": acct,
                "follower_acct": fa,
                "follower_username": fa.split("@")[0],
                "display_name": name,
                "follower_url": f"https://{fa.split('@')[1]}/@{fa.split('@')[0]}",
                "synthetic": True,
            },
        }
        for fa, name in fixtures
    ]
    events.append(
        {
            "event_type": "tool-run-result",
            "payload": {"acct": acct, "followers": len(fixtures), "synthetic": True},
        }
    )
    return events


# ---------------------------------------------------------------------------
# 3e. Post-likes adapters (who-liked-this articulating-link)
# ---------------------------------------------------------------------------
# Point at a social-media post URL, get back the names of users who liked it.
# Tedious to do by hand; load-bearing for property-vetting because property
# owners sometimes liked a listing-related post under their real name.
#
# Public-data reality:
#   - Bluesky: post-likes are public via the AT Protocol getLikes endpoint.
#   - Mastodon: status favourites are public via /api/v1/statuses/<id>/favourited_by.
#   - Twitter/X: likes were made private in 2024. Cannot scrape; even logged
#     in users can only see their own. We do NOT ship a Twitter post-likes
#     adapter because the data is no longer public.
#   - Instagram + TikTok: post likes were never publicly visible at scale;
#     same situation. No adapter.
#   - YouTube + Reddit: upvotes private. No adapter.


def bluesky_post_likes(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """List users who liked a Bluesky post.

    Payload (one of):
      {"post_url": "https://bsky.app/profile/<handle>/post/<rkey>"}
      {"at_uri": "at://did:plc:<did>/app.bsky.feed.post/<rkey>"}
      {"limit": 100}  # default 100, max 300

    The web URL form is what investigators usually have; the adapter
    resolves handle -> DID -> AT URI -> getLikes.
    """
    post_url = (payload.get("post_url") or "").strip()
    at_uri = (payload.get("at_uri") or "").strip()
    limit = min(int(payload.get("limit", 100)), 300)

    if not at_uri and post_url:
        m = re.match(
            r"https?://bsky\.app/profile/([A-Za-z0-9.\-_]+)/post/([A-Za-z0-9]+)/?", post_url
        )
        if m:
            handle, rkey = m.group(1), m.group(2)
            # Resolve handle to DID via the public identity service
            try:
                with _client(timeout_s=8.0) as c:
                    rid = c.get(
                        "https://public.api.bsky.app/xrpc/com.atproto.identity.resolveHandle",
                        params={"handle": handle},
                    )
                if rid.status_code != 200:
                    return [
                        {
                            "event_type": "tool-run-error",
                            "payload": {
                                "reason": f"bluesky handle-resolve HTTP {rid.status_code}",
                                "handle": handle,
                            },
                        }
                    ]
                did = (rid.json() or {}).get("did", "")
                if not did:
                    return [
                        {
                            "event_type": "tool-run-error",
                            "payload": {"reason": "no DID for handle", "handle": handle},
                        }
                    ]
                at_uri = f"at://{did}/app.bsky.feed.post/{rkey}"
            except httpx.RequestError as exc:
                return [
                    {
                        "event_type": "tool-run-error",
                        "payload": {
                            "reason": f"bluesky resolve {type(exc).__name__}: {exc}",
                            "handle": handle,
                        },
                    }
                ]
    if not at_uri:
        return [
            {
                "event_type": "tool-run-error",
                "payload": {"reason": "need 'post_url' (bsky.app) or 'at_uri'"},
            }
        ]

    likers: list[dict[str, Any]] = []
    cursor: str | None = None
    try:
        with _client(timeout_s=10.0) as c:
            while len(likers) < limit:
                params: dict[str, str] = {"uri": at_uri, "limit": "100"}
                if cursor:
                    params["cursor"] = cursor
                r = c.get(
                    "https://public.api.bsky.app/xrpc/app.bsky.feed.getLikes",
                    params=params,
                )
                if r.status_code != 200:
                    return [
                        {
                            "event_type": "tool-run-error",
                            "payload": {
                                "reason": f"bluesky getLikes HTTP {r.status_code}",
                                "at_uri": at_uri,
                            },
                        }
                    ]
                data = r.json() or {}
                batch = data.get("likes") or []
                if not batch:
                    break
                likers.extend(batch)
                cursor = data.get("cursor")
                if not cursor or len(batch) < 100:
                    break
    except httpx.RequestError as exc:
        return [
            {
                "event_type": "tool-run-error",
                "payload": {
                    "reason": f"bluesky {type(exc).__name__}: {exc}",
                    "at_uri": at_uri,
                },
            }
        ]

    likers = likers[:limit]
    events: list[dict[str, Any]] = []
    for like in likers:
        actor = (like or {}).get("actor") or {}
        if not isinstance(actor, dict):
            continue
        a_handle = actor.get("handle", "")
        events.append(
            {
                "event_type": "person-match",
                "payload": {
                    "source": "bluesky-liker",
                    "of_post": at_uri,
                    "follower_handle": a_handle,
                    "follower_did": actor.get("did", ""),
                    "display_name": actor.get("displayName", ""),
                    "follower_url": f"https://bsky.app/profile/{a_handle}" if a_handle else "",
                    "photo_url": actor.get("avatar", ""),
                    "liked_at": (like or {}).get("createdAt", ""),
                },
            }
        )
    events.append(
        {
            "event_type": "tool-run-result",
            "payload": {"at_uri": at_uri, "likers": len(events) - 0},
        }
    )
    return events


def _bluesky_post_likes_synthetic(payload: dict[str, Any]) -> list[dict[str, Any]]:
    at_uri = payload.get("at_uri") or "at://did:plc:synthetic/app.bsky.feed.post/abc"
    fixtures = [
        ("alice.bsky.social", "Alice Smith"),
        ("bob.bsky.social", "Bob Jones"),
        ("carol.bsky.social", "Carol Wong"),
    ]
    events = [
        {
            "event_type": "person-match",
            "payload": {
                "source": "bluesky-liker",
                "of_post": at_uri,
                "follower_handle": fh,
                "display_name": name,
                "follower_url": f"https://bsky.app/profile/{fh}",
                "synthetic": True,
            },
        }
        for fh, name in fixtures
    ]
    events.append(
        {
            "event_type": "tool-run-result",
            "payload": {"at_uri": at_uri, "likers": len(fixtures), "synthetic": True},
        }
    )
    return events


def mastodon_post_likes(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """List users who favourited a Mastodon status.

    Payload:
      {"post_url": "https://mastodon.social/@alice/112345678901234567"}
      {"limit": 80}  # default 80, max 240

    Parses instance + status-id from the URL, hits the public
    /api/v1/statuses/<id>/favourited_by endpoint.
    """
    post_url = (payload.get("post_url") or "").strip()
    if not post_url:
        return [
            {
                "event_type": "tool-run-error",
                "payload": {"reason": "missing 'post_url'"},
            }
        ]
    m = re.match(
        r"https?://([^/]+)/(?:@[A-Za-z0-9_]+|notice|users/[A-Za-z0-9_]+/statuses)/(\d+)/?",
        post_url,
    )
    if not m:
        return [
            {
                "event_type": "tool-run-error",
                "payload": {
                    "reason": (
                        "post_url must look like https://<instance>/@<user>/<id> "
                        "or .../notice/<id>"
                    ),
                    "got": post_url,
                },
            }
        ]
    instance, status_id = m.group(1), m.group(2)
    limit = min(int(payload.get("limit", 80)), 240)

    try:
        with _client(timeout_s=10.0) as c:
            r = c.get(
                f"https://{instance}/api/v1/statuses/{status_id}/favourited_by",
                params={"limit": min(limit, 80)},
            )
        if r.status_code != 200:
            return [
                {
                    "event_type": "tool-run-error",
                    "payload": {
                        "reason": f"mastodon HTTP {r.status_code}",
                        "url": post_url,
                    },
                }
            ]
        accounts = r.json() or []
    except httpx.RequestError as exc:
        return [
            {
                "event_type": "tool-run-error",
                "payload": {
                    "reason": f"mastodon {type(exc).__name__}: {exc}",
                    "url": post_url,
                },
            }
        ]

    if not isinstance(accounts, list):
        accounts = []
    accounts = accounts[:limit]
    events: list[dict[str, Any]] = []
    for a in accounts:
        if not isinstance(a, dict):
            continue
        events.append(
            {
                "event_type": "person-match",
                "payload": {
                    "source": "mastodon-liker",
                    "of_post": post_url,
                    "follower_acct": a.get("acct", ""),
                    "follower_username": a.get("username", ""),
                    "display_name": a.get("display_name", ""),
                    "follower_url": a.get("url", ""),
                    "photo_url": a.get("avatar", ""),
                    "created_at": a.get("created_at", ""),
                },
            }
        )
    events.append(
        {
            "event_type": "tool-run-result",
            "payload": {"post_url": post_url, "likers": len(events) - 0},
        }
    )
    return events


def _mastodon_post_likes_synthetic(payload: dict[str, Any]) -> list[dict[str, Any]]:
    post_url = payload.get("post_url") or "https://mastodon.social/@synthetic/123"
    fixtures = [
        ("alice@mastodon.social", "Alice Smith"),
        ("bob@hachyderm.io", "Bob Jones"),
    ]
    events = [
        {
            "event_type": "person-match",
            "payload": {
                "source": "mastodon-liker",
                "of_post": post_url,
                "follower_acct": fa,
                "display_name": name,
                "follower_url": f"https://{fa.split('@')[1]}/@{fa.split('@')[0]}",
                "synthetic": True,
            },
        }
        for fa, name in fixtures
    ]
    events.append(
        {
            "event_type": "tool-run-result",
            "payload": {"post_url": post_url, "likers": len(fixtures), "synthetic": True},
        }
    )
    return events


def wayback_snapshot(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Generalized Wayback availability lookup for any URL.

    Payload:
      {"url": "https://www.instagram.com/<handle>/followers"}

    Use case: the walled-platform follower lists (Twitter, Instagram,
    TikTok) used to be public; the Wayback Machine has snapshots from
    that era. This adapter checks if such a snapshot exists for any
    URL and returns the snapshot URL + timestamp. The investigator
    opens the snapshot in a browser; the wrapper does NOT scrape the
    snapshot's contents.

    For LinkedIn URLs specifically, wayback_linkedin is the named
    convenience alias; wayback_snapshot accepts any URL.
    """
    url = (payload.get("url") or "").strip()
    if not url:
        return [
            {
                "event_type": "tool-run-error",
                "payload": {"reason": "missing 'url' in payload"},
            }
        ]

    try:
        with _client(timeout_s=8.0) as c:
            r = c.get(
                "http://archive.org/wayback/available",
                params={"url": url},
            )
        if r.status_code != 200:
            return [
                {
                    "event_type": "tool-run-error",
                    "payload": {"reason": f"wayback HTTP {r.status_code}", "url": url},
                }
            ]
        data = r.json()
    except httpx.RequestError as exc:
        return [
            {
                "event_type": "tool-run-error",
                "payload": {
                    "reason": f"wayback {type(exc).__name__}: {exc}",
                    "url": url,
                },
            }
        ]

    closest = (data.get("archived_snapshots") or {}).get("closest") or {}
    if not closest or not closest.get("available"):
        return [
            {
                "event_type": "tool-run-result",
                "payload": {"url": url, "snapshots": 0},
            }
        ]
    return [
        {
            "event_type": "person-match",
            "payload": {
                "source": "wayback-snapshot",
                "url": url,
                "snapshot_url": closest.get("url", ""),
                "snapshot_timestamp": closest.get("timestamp", ""),
                "snapshot_status": closest.get("status", ""),
            },
        },
        {
            "event_type": "tool-run-result",
            "payload": {"url": url, "snapshots": 1},
        },
    ]


def _wayback_snapshot_synthetic(payload: dict[str, Any]) -> list[dict[str, Any]]:
    url = payload.get("url") or "https://www.instagram.com/synthetic/followers"
    return [
        {
            "event_type": "person-match",
            "payload": {
                "source": "wayback-snapshot",
                "url": url,
                "snapshot_url": "https://web.archive.org/web/20200115120000/" + url,
                "snapshot_timestamp": "20200115120000",
                "snapshot_status": "200",
                "synthetic": True,
            },
        },
        {
            "event_type": "tool-run-result",
            "payload": {"url": url, "snapshots": 1, "synthetic": True},
        },
    ]


# ---------------------------------------------------------------------------
# 4. Inside Airbnb listings CSV (Sprint 3 advance)
# ---------------------------------------------------------------------------
# Inside Airbnb publishes city-level Airbnb listing snapshots quarterly at
# http://insideairbnb.com/get-the-data/. The data is a CSV per city; each
# row is one Airbnb listing with host_id, host_name, host_listings_count,
# room_type, neighbourhood, etc. For property-vetting the load-bearing
# question is "does this host operate one listing or 20?" -- the
# commercial-operator-vs-genuine-host signal.
#
# Design choice: we do NOT download the CSV from inside this adapter.
# Each city CSV is 5-50 MB; downloading on every adapter call would be
# wasteful, and the quarterly update cadence means a per-call fetch is
# also stale relative to actual freshness. Instead the investigator
# downloads the city CSV once a month into data/inside-airbnb/<city>.csv
# and the adapter parses it. Cache-management lives one level up.


def inside_airbnb_listings(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Search a pre-downloaded Inside Airbnb CSV for matching listings.

    Payload (at least one of host_name / host_id / listing_url required):
      {"csv_path": "data/inside-airbnb/springfield-il.csv",
       "host_name": "Alice S",         # optional, partial match
       "host_id": "12345",             # optional, exact match
       "listing_url": "https://www.airbnb.com/rooms/...",  # optional
       "limit": 50}                    # optional cap on matches, default 20

    Emits one `listing-match` event per matching row + one
    `tool-run-result` summary. The matched rows carry the
    commercial-operator signal in payload (host_total_listings,
    room_type, last_review date).
    """
    csv_path_str = payload.get("csv_path")
    if not csv_path_str or not isinstance(csv_path_str, str):
        return [
            {
                "event_type": "tool-run-error",
                "payload": {
                    "reason": "missing 'csv_path' in payload (download city CSV first)",
                    "suggest": "http://insideairbnb.com/get-the-data/",
                },
            }
        ]
    csv_path = Path(csv_path_str)
    if not csv_path.is_file():
        return [
            {
                "event_type": "tool-run-error",
                "payload": {
                    "reason": f"CSV not found at {csv_path}",
                    "suggest": "Download from http://insideairbnb.com/get-the-data/",
                },
            }
        ]

    host_name = (payload.get("host_name") or "").strip().lower()
    host_id = (payload.get("host_id") or "").strip()
    listing_url = (payload.get("listing_url") or "").strip()
    limit = int(payload.get("limit", 20))

    # Listing URL -> extract listing id from /rooms/<id> pattern
    listing_id = ""
    if listing_url:
        m = re.search(r"/rooms/(\d+)", listing_url)
        if m:
            listing_id = m.group(1)

    if not (host_name or host_id or listing_id):
        return [
            {
                "event_type": "tool-run-error",
                "payload": {
                    "reason": "must provide at least one of host_name, host_id, listing_url",
                },
            }
        ]

    matches: list[dict[str, Any]] = []
    try:
        with csv_path.open("r", encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                # Match logic: any of the three predicates matches.
                row_host_id = (row.get("host_id") or "").strip()
                row_host_name = (row.get("host_name") or "").strip().lower()
                row_listing_id = (row.get("id") or "").strip()

                matched = False
                if (
                    host_id
                    and row_host_id == host_id
                    or listing_id
                    and row_listing_id == listing_id
                    or host_name
                    and host_name in row_host_name
                ):
                    matched = True

                if matched:
                    matches.append(row)
                    if len(matches) >= limit:
                        break
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        return [
            {
                "event_type": "tool-run-error",
                "payload": {"reason": f"CSV read error: {type(exc).__name__}: {exc}"},
            }
        ]

    events: list[dict[str, Any]] = []
    for row in matches:
        # Surface the commercial-operator signal explicitly.
        try:
            host_total = int(row.get("host_listings_count") or 0)
        except (TypeError, ValueError):
            host_total = 0
        events.append(
            {
                "event_type": "listing-match",
                "payload": {
                    "listing_id": row.get("id", ""),
                    "listing_url": row.get("listing_url", "")
                    or f"https://www.airbnb.com/rooms/{row.get('id', '')}",
                    "name": row.get("name", ""),
                    "host_id": row.get("host_id", ""),
                    "host_name": row.get("host_name", ""),
                    "host_total_listings": host_total,
                    "neighbourhood": row.get("neighbourhood", "")
                    or row.get("neighbourhood_cleansed", ""),
                    "room_type": row.get("room_type", ""),
                    "last_review": row.get("last_review", ""),
                    "commercial_operator": host_total >= 2,
                },
            }
        )
    events.append(
        {
            "event_type": "tool-run-result",
            "payload": {
                "csv_path": str(csv_path),
                "matches": len(matches),
                "host_name": host_name,
                "host_id": host_id,
                "listing_id": listing_id,
            },
        }
    )
    return events


def _inside_airbnb_synthetic(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Synthetic: one fake match exercising the wire shape + the
    commercial-operator flag both true and false."""
    return [
        {
            "event_type": "listing-match",
            "payload": {
                "listing_id": "12345",
                "listing_url": "https://www.airbnb.com/rooms/12345",
                "name": "Cozy synthetic apartment downtown",
                "host_id": "99999",
                "host_name": "Synthetic Host",
                "host_total_listings": 3,
                "neighbourhood": "Synthetic Heights",
                "room_type": "Entire home/apt",
                "last_review": "2025-12-01",
                "commercial_operator": True,
                "synthetic": True,
            },
        },
        {
            "event_type": "listing-match",
            "payload": {
                "listing_id": "67890",
                "listing_url": "https://www.airbnb.com/rooms/67890",
                "name": "Synthetic guest room",
                "host_id": "11111",
                "host_name": "Single Host",
                "host_total_listings": 1,
                "neighbourhood": "Old Town",
                "room_type": "Private room",
                "last_review": "2025-08-15",
                "commercial_operator": False,
                "synthetic": True,
            },
        },
        {
            "event_type": "tool-run-result",
            "payload": {"matches": 2, "synthetic": True},
        },
    ]


# ---------------------------------------------------------------------------
# 5. + 6. Stub registrations: TruePeopleSearch, TinEye
# ---------------------------------------------------------------------------
# These two require Scrapling (anti-scraping bypass) or signed API
# access. Per the R-5 honest-scope split, we ship them as registered
# stubs with synthetic_mode only -- so the dossier UX can be exercised
# in synthetic mode while Sprint 3 wires the live path.


def _true_people_synthetic(payload: dict[str, Any]) -> list[dict[str, Any]]:
    name = payload.get("name", "Subject")
    return [
        {
            "event_type": "person-match",
            "payload": {
                "name": name,
                "age_range": "40-45",
                "city": "Springfield",
                "state": "IL",
                "synthetic": True,
                "comment": "Stub. Live mode requires Scrapling (Sprint 3+).",
            },
        },
        {
            "event_type": "tool-run-result",
            "payload": {"name": name, "matches": 1, "synthetic": True},
        },
    ]


def _true_people_live_stub(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "event_type": "tool-run-error",
            "payload": {
                "reason": (
                    "TruePeopleSearch live mode not yet implemented; "
                    "requires Scrapling (Sprint 3+)."
                ),
                "suggest": "Re-run with OSINT_ADAPTER_MODE=synthetic.",
            },
        }
    ]


def _tineye_synthetic(payload: dict[str, Any]) -> list[dict[str, Any]]:
    image_url = payload.get("image_url", "https://example.com/synthetic.jpg")
    return [
        {
            "event_type": "image-match",
            "payload": {
                "image_url": image_url,
                "match_url": "https://example.com/match-1.jpg",
                "domain": "example.com",
                "first_seen": "2023-06-15",
                "synthetic": True,
                "comment": "Stub. Live mode requires TinEye API key (Sprint 3+).",
            },
        },
        {
            "event_type": "tool-run-result",
            "payload": {"image_url": image_url, "matches": 1, "synthetic": True},
        },
    ]


def _tineye_live_stub(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "event_type": "tool-run-error",
            "payload": {
                "reason": (
                    "TinEye live mode not yet implemented; "
                    "requires API key + Scrapling fallback (Sprint 3+)."
                ),
                "suggest": "Re-run with OSINT_ADAPTER_MODE=synthetic.",
            },
        }
    ]


# ---------------------------------------------------------------------------
# Registry installation
# ---------------------------------------------------------------------------

_REGISTRY = get_registry()

_REGISTRY.register(
    "nominatim_geocode",
    nominatim_geocode,
    synthetic_mode=_nominatim_synthetic,
    in_process=True,
    description="OSM Nominatim address -> lat/lon. R-5 Sprint 2 property-vetting primitive.",
)

_REGISTRY.register(
    "email_mx_validate",
    email_mx_validate,
    synthetic_mode=_email_mx_synthetic,
    in_process=True,
    description="DNS-MX-based email deliverability check. R-5 Sprint 2.",
)

_REGISTRY.register(
    "hibp_breach_check",
    hibp_breach_check,
    synthetic_mode=_hibp_synthetic,
    in_process=True,
    description="HIBP breaches by domain. R-5 Sprint 2.",
)

_REGISTRY.register(
    "github_profile",
    github_profile,
    synthetic_mode=_github_synthetic,
    in_process=True,
    description="GitHub public profile via REST v3 (LinkedIn-alt for tech hosts).",
)

_REGISTRY.register(
    "wayback_linkedin",
    wayback_linkedin,
    synthetic_mode=_wayback_synthetic,
    in_process=True,
    description="Wayback snapshot of a LinkedIn URL (availability fallback).",
)

# Public-follower-list adapters (articulating-link investigation).
# Strict: PUBLIC accounts only; private profile -> tool-run-error.
_REGISTRY.register(
    "github_followers",
    github_followers,
    synthetic_mode=_github_followers_synthetic,
    in_process=True,
    description="GitHub public followers via REST v3.",
)
_REGISTRY.register(
    "bluesky_followers",
    bluesky_followers,
    synthetic_mode=_bluesky_followers_synthetic,
    in_process=True,
    description="Bluesky public followers via AT Protocol (no auth).",
)
_REGISTRY.register(
    "mastodon_followers",
    mastodon_followers,
    synthetic_mode=_mastodon_followers_synthetic,
    in_process=True,
    description="Mastodon public followers via the instance REST API.",
)
_REGISTRY.register(
    "wayback_snapshot",
    wayback_snapshot,
    synthetic_mode=_wayback_snapshot_synthetic,
    in_process=True,
    description="Generalized Wayback availability for any URL (snapshot of pre-wall pages).",
)
_REGISTRY.register(
    "bluesky_post_likes",
    bluesky_post_likes,
    synthetic_mode=_bluesky_post_likes_synthetic,
    in_process=True,
    description="Bluesky who-liked-this-post via AT Protocol getLikes (public).",
)
_REGISTRY.register(
    "mastodon_post_likes",
    mastodon_post_likes,
    synthetic_mode=_mastodon_post_likes_synthetic,
    in_process=True,
    description="Mastodon who-favourited-this-status via public REST API.",
)

_REGISTRY.register(
    "inside_airbnb_listings",
    inside_airbnb_listings,
    synthetic_mode=_inside_airbnb_synthetic,
    in_process=True,
    description="Inside Airbnb city-CSV search (Sprint 3). Commercial-operator fingerprint.",
)

# TruePeopleSearch: live mode upgraded from stub to subprocess wrapper
# pinned to the empirical venv (Scrapling + Patchright). The in-process
# synthetic + stub paths are retained as fallbacks via the subprocess
# wrapper's OSINT_ADAPTER_MODE env-var contract.
_TRUE_PEOPLE_WRAPPER = _REPO_ROOT_PROP / "adapters" / "true_people_search" / "wrapper.py"
if _TRUE_PEOPLE_WRAPPER.is_file() and _EMPIRICAL_PY.is_file():
    _REGISTRY.register(
        "true_people_search",
        make_subprocess_adapter(
            _TRUE_PEOPLE_WRAPPER,
            timeout_s=60.0,
            python_executable=str(_EMPIRICAL_PY),
        ),
        synthetic_mode=make_subprocess_adapter(
            _TRUE_PEOPLE_WRAPPER,
            timeout_s=30.0,
            python_executable=str(_EMPIRICAL_PY),
            extra_env={"OSINT_ADAPTER_MODE": "synthetic"},
        ),
        in_process=False,
        description="TruePeopleSearch via Scrapling subprocess (Sprint 3 live mode).",
    )
else:
    _REGISTRY.register(
        "true_people_search",
        _true_people_live_stub,
        synthetic_mode=_true_people_synthetic,
        in_process=True,
        description=(
            "TruePeopleSearch -- empirical venv or wrapper missing; " "in-process stub only."
        ),
    )

# LinkedIn public-profile fetch: same Scrapling subprocess pattern.
# Public-view only; no login, no account-lock risk. Profile URL required.
_LINKEDIN_WRAPPER = _REPO_ROOT_PROP / "adapters" / "linkedin" / "wrapper.py"
if _LINKEDIN_WRAPPER.is_file() and _EMPIRICAL_PY.is_file():
    _REGISTRY.register(
        "linkedin_profile",
        make_subprocess_adapter(
            _LINKEDIN_WRAPPER,
            timeout_s=60.0,
            python_executable=str(_EMPIRICAL_PY),
        ),
        synthetic_mode=make_subprocess_adapter(
            _LINKEDIN_WRAPPER,
            timeout_s=30.0,
            python_executable=str(_EMPIRICAL_PY),
            extra_env={"OSINT_ADAPTER_MODE": "synthetic"},
        ),
        in_process=False,
        description="LinkedIn public-profile fetch via Scrapling (no login).",
    )

# Public-profile + public-follower-list social adapters.
# - *_public: bio + counts via Scrapling.
# - *_followers: follower-list articulating-link investigation. Private
#   accounts are blocked by design; walled-platform follower lists
#   surface as honest tool-run-error pointing at wayback_snapshot when
#   the auth wall holds.
for _social_id, _social_dir in (
    ("twitter_public", "twitter_public"),
    ("instagram_public", "instagram_public"),
    ("tiktok_public", "tiktok_public"),
    ("twitter_followers", "twitter_followers"),
    ("instagram_followers", "instagram_followers"),
    ("tiktok_followers", "tiktok_followers"),
    ("twstalker", "twstalker"),
):
    _wrapper_path = _REPO_ROOT_PROP / "adapters" / _social_dir / "wrapper.py"
    if _wrapper_path.is_file() and _EMPIRICAL_PY.is_file():
        _REGISTRY.register(
            _social_id,
            make_subprocess_adapter(
                _wrapper_path,
                timeout_s=60.0,
                python_executable=str(_EMPIRICAL_PY),
            ),
            synthetic_mode=make_subprocess_adapter(
                _wrapper_path,
                timeout_s=30.0,
                python_executable=str(_EMPIRICAL_PY),
                extra_env={"OSINT_ADAPTER_MODE": "synthetic"},
            ),
            in_process=False,
            description=(
                f"{_social_id} -- public bio + counts via Scrapling "
                "(no login, no follower-list)."
            ),
        )

# Google SERP for LinkedIn profile URLs: name-based search shim that
# closes the gap linkedin_profile (URL-only) leaves open. Pinned to the
# empirical venv via Scrapling.
_GOOGLE_SERP_WRAPPER = _REPO_ROOT_PROP / "adapters" / "google_serp_linkedin" / "wrapper.py"
if _GOOGLE_SERP_WRAPPER.is_file() and _EMPIRICAL_PY.is_file():
    _REGISTRY.register(
        "google_serp_linkedin",
        make_subprocess_adapter(
            _GOOGLE_SERP_WRAPPER,
            timeout_s=60.0,
            python_executable=str(_EMPIRICAL_PY),
        ),
        synthetic_mode=make_subprocess_adapter(
            _GOOGLE_SERP_WRAPPER,
            timeout_s=30.0,
            python_executable=str(_EMPIRICAL_PY),
            extra_env={"OSINT_ADAPTER_MODE": "synthetic"},
        ),
        in_process=False,
        description="Google SERP -> LinkedIn profile URLs (name-search shim).",
    )

# RocketReach name search: free-tier public results only (no API key).
_ROCKETREACH_WRAPPER = _REPO_ROOT_PROP / "adapters" / "rocketreach" / "wrapper.py"
if _ROCKETREACH_WRAPPER.is_file() and _EMPIRICAL_PY.is_file():
    _REGISTRY.register(
        "rocketreach_search",
        make_subprocess_adapter(
            _ROCKETREACH_WRAPPER,
            timeout_s=60.0,
            python_executable=str(_EMPIRICAL_PY),
        ),
        synthetic_mode=make_subprocess_adapter(
            _ROCKETREACH_WRAPPER,
            timeout_s=30.0,
            python_executable=str(_EMPIRICAL_PY),
            extra_env={"OSINT_ADAPTER_MODE": "synthetic"},
        ),
        in_process=False,
        description="RocketReach name search via Scrapling (free-tier surface only).",
    )

# TinEye reverse-image search: same pattern as TruePeopleSearch.
# Empirical venv ships Scrapling; the wrapper does URL-based search
# against tineye.com/search (no image upload needed for property-vetting).
_TINEYE_WRAPPER = _REPO_ROOT_PROP / "adapters" / "tineye" / "wrapper.py"
if _TINEYE_WRAPPER.is_file() and _EMPIRICAL_PY.is_file():
    _REGISTRY.register(
        "tineye_image",
        make_subprocess_adapter(
            _TINEYE_WRAPPER,
            timeout_s=60.0,
            python_executable=str(_EMPIRICAL_PY),
        ),
        synthetic_mode=make_subprocess_adapter(
            _TINEYE_WRAPPER,
            timeout_s=30.0,
            python_executable=str(_EMPIRICAL_PY),
            extra_env={"OSINT_ADAPTER_MODE": "synthetic"},
        ),
        in_process=False,
        description="TinEye reverse image search via Scrapling subprocess (Sprint 3 live mode).",
    )
else:
    _REGISTRY.register(
        "tineye_image",
        _tineye_live_stub,
        synthetic_mode=_tineye_synthetic,
        in_process=True,
        description=("TinEye -- empirical venv or wrapper missing; in-process stub only."),
    )

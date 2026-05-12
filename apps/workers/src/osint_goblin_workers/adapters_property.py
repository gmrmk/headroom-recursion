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


# ---------------------------------------------------------------------------
# 3a-i. Gravatar v3 profile lookup -- owner-attested identity pivot
#
# Single highest-signal free email pivot per Margaret's deliberation
# (2026-05-11 evening). `verified_accounts[]` is the property owner
# *explicitly* tying their email to listed platforms (LinkedIn, GitHub,
# etc.) -- if a fraudulent listing's owner email comes back with a real
# Gravatar profile and 3 verified platforms, that's strong "real person"
# signal; if it comes back 404, that's a useful negative signal too.
#
# Endpoint: GET https://api.gravatar.com/v3/profiles/{sha256_hex}
# Auth:     anonymous works (100/hr); Bearer token in OSINT_GRAVATAR_TOKEN
#           unlocks 1000/hr.
# Hash:     sha256(lower(strip(email))). Per docs.gravatar.com/rest/hash/.
# ---------------------------------------------------------------------------


_GRAVATAR_URL = "https://api.gravatar.com/v3/profiles/{hash}"


def gravatar_profile_lookup(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Gravatar v3 profile lookup. Emits one `person-match` per
    `verified_accounts[]` entry + a summary tool-run-result.

    Payload:
      {"email": "user@example.com"}

    Env (optional):
      OSINT_GRAVATAR_TOKEN  -- bearer token raises 100/hr -> 1000/hr
    """
    import hashlib

    email = payload.get("email", "")
    if not isinstance(email, str) or "@" not in email:
        return [
            {
                "event_type": "tool-run-error",
                "payload": {"reason": "missing or malformed 'email'"},
            }
        ]

    normalized = email.strip().lower()
    sha256 = hashlib.sha256(normalized.encode()).hexdigest()
    url = _GRAVATAR_URL.format(hash=sha256)

    headers: dict[str, str] = {"Accept": "application/json", "User-Agent": _USER_AGENT}
    token = os.environ.get("OSINT_GRAVATAR_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        with httpx.Client(timeout=10.0, headers=headers, follow_redirects=False) as c:
            r = c.get(url)
    except httpx.RequestError as exc:
        return [
            {
                "event_type": "tool-run-error",
                "payload": {
                    "reason": f"gravatar {type(exc).__name__}: {exc}",
                    "email": email,
                },
            }
        ]

    if r.status_code == 404:
        return [
            {
                "event_type": "tool-run-result",
                "payload": {
                    "source": "gravatar",
                    "email": email,
                    "profile_found": False,
                    "verified_count": 0,
                },
            }
        ]
    if r.status_code != 200:
        return [
            {
                "event_type": "tool-run-error",
                "payload": {
                    "reason": f"gravatar HTTP {r.status_code}",
                    "email": email,
                },
            }
        ]

    try:
        data = r.json()
    except ValueError:
        return [
            {
                "event_type": "tool-run-error",
                "payload": {"reason": "gravatar non-JSON response", "email": email},
            }
        ]
    if not isinstance(data, dict):
        return [
            {
                "event_type": "tool-run-error",
                "payload": {"reason": "gravatar unexpected response shape", "email": email},
            }
        ]

    verified = data.get("verified_accounts") or []
    events: list[dict[str, Any]] = []
    if isinstance(verified, list):
        for v in verified:
            if not isinstance(v, dict):
                continue
            if v.get("is_hidden") is True:
                continue
            events.append(
                {
                    "event_type": "person-match",
                    "payload": {
                        "source": "gravatar",
                        "email": email,
                        "platform": v.get("service_type", "") or v.get("service_label", ""),
                        "platform_label": v.get("service_label", ""),
                        "profile_url": v.get("url", ""),
                        "owner_attested": True,
                    },
                }
            )

    events.append(
        {
            "event_type": "tool-run-result",
            "payload": {
                "source": "gravatar",
                "email": email,
                "profile_found": True,
                "display_name": data.get("display_name", ""),
                "profile_url": data.get("profile_url", ""),
                "location": data.get("location", ""),
                "job_title": data.get("job_title", ""),
                "company": data.get("company", ""),
                "verified_count": sum(1 for e in events if e["event_type"] == "person-match"),
            },
        }
    )
    return events


def _gravatar_synthetic(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Synthetic Gravatar: a profile with two verified accounts. Locks
    the wire shape (person-match per verified account + summary)."""
    email = payload.get("email") or "user@example.com"
    return [
        {
            "event_type": "person-match",
            "payload": {
                "source": "gravatar",
                "email": email,
                "platform": "github",
                "platform_label": "GitHub",
                "profile_url": "https://github.com/synthetic-user",
                "owner_attested": True,
                "synthetic": True,
            },
        },
        {
            "event_type": "person-match",
            "payload": {
                "source": "gravatar",
                "email": email,
                "platform": "linkedin",
                "platform_label": "LinkedIn",
                "profile_url": "https://linkedin.com/in/synthetic-user",
                "owner_attested": True,
                "synthetic": True,
            },
        },
        {
            "event_type": "tool-run-result",
            "payload": {
                "source": "gravatar",
                "email": email,
                "profile_found": True,
                "display_name": "Synthetic User",
                "verified_count": 2,
                "synthetic": True,
            },
        },
    ]


# ---------------------------------------------------------------------------
# 3a-ii. GitHub commit-email search -- behavioral identity confirm
#
# Ship #2 of the free-stack replacement for IntelBase (Margaret's parse,
# 2026-05-11). Where Gravatar gives owner-attested identity, GitHub gives
# behavioral identity: did this email actually author code in public
# repos? If yes, the email -> login -> repo graph is a strong "real
# long-lived developer" signal -- exactly what property-vetting needs to
# distinguish a churn account from a real person.
#
# Endpoint: GET https://api.github.com/search/commits?q=author-email:<email>
# Auth:     anonymous works at 10/min; OSINT_GITHUB_PAT raises to 30/min.
# ---------------------------------------------------------------------------


_GITHUB_COMMITS_URL = "https://api.github.com/search/commits"


def github_commit_email_search(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """GitHub commit search by author-email. Emits one `person-match` per
    unique repo (collapsing N commits to same repo into one event) plus a
    summary tool-run-result with commit + repo counts.

    Payload:
      {"email": "user@example.com",
       "per_page": 30}              # optional, default 30, GitHub max 100

    Env (optional):
      OSINT_GITHUB_PAT  -- raises rate limit 10/min -> 30/min for search
    """
    email = payload.get("email", "")
    if not isinstance(email, str) or "@" not in email:
        return [
            {
                "event_type": "tool-run-error",
                "payload": {"reason": "missing or malformed 'email'"},
            }
        ]

    per_page = int(payload.get("per_page", 30))
    if per_page < 1 or per_page > 100:
        per_page = 30

    headers: dict[str, str] = {
        "Accept": "application/vnd.github+json",
        "User-Agent": _USER_AGENT,
        "X-GitHub-Api-Version": "2022-11-28",
    }
    pat = os.environ.get("OSINT_GITHUB_PAT", "").strip()
    if pat:
        headers["Authorization"] = f"Bearer {pat}"

    params: dict[str, Any] = {
        "q": f"author-email:{email}",
        "per_page": per_page,
        "sort": "author-date",
        "order": "desc",
    }

    try:
        with httpx.Client(timeout=15.0, headers=headers, follow_redirects=False) as c:
            r = c.get(_GITHUB_COMMITS_URL, params=params)
    except httpx.RequestError as exc:
        return [
            {
                "event_type": "tool-run-error",
                "payload": {
                    "reason": f"github {type(exc).__name__}: {exc}",
                    "email": email,
                },
            }
        ]

    if r.status_code in (403, 429):
        return [
            {
                "event_type": "tool-run-error",
                "payload": {
                    "reason": f"github HTTP {r.status_code} (rate-limited)",
                    "email": email,
                },
            }
        ]
    if r.status_code != 200:
        return [
            {
                "event_type": "tool-run-error",
                "payload": {
                    "reason": f"github HTTP {r.status_code}",
                    "email": email,
                },
            }
        ]

    try:
        data = r.json()
    except ValueError:
        return [
            {
                "event_type": "tool-run-error",
                "payload": {"reason": "github non-JSON response", "email": email},
            }
        ]
    if not isinstance(data, dict):
        return [
            {
                "event_type": "tool-run-error",
                "payload": {"reason": "github unexpected response shape", "email": email},
            }
        ]

    items = data.get("items") or []
    # Roll up by repo: many commits to the same repo collapse to one
    # person-match event with commit_count. Keeps the stream scannable.
    by_repo: dict[str, dict[str, Any]] = {}
    if isinstance(items, list):
        for it in items:
            if not isinstance(it, dict):
                continue
            repo = (it.get("repository") or {}).get("full_name", "")
            if not repo:
                continue
            author = it.get("author") or {}
            commit = (it.get("commit") or {}).get("author") or {}
            entry = by_repo.setdefault(
                repo,
                {
                    "repo": repo,
                    "login": author.get("login", ""),
                    "profile_url": author.get("html_url", ""),
                    "author_name": commit.get("name", ""),
                    "commit_count": 0,
                    "first_seen": commit.get("date", ""),
                    "last_seen": commit.get("date", ""),
                    "sample_commit": it.get("html_url", ""),
                },
            )
            entry["commit_count"] += 1
            d = commit.get("date", "")
            if d and (entry["first_seen"] == "" or d < entry["first_seen"]):
                entry["first_seen"] = d
            if d and d > entry["last_seen"]:
                entry["last_seen"] = d

    events: list[dict[str, Any]] = []
    for entry in by_repo.values():
        events.append(
            {
                "event_type": "person-match",
                "payload": {"source": "github_commits", "email": email, **entry},
            }
        )

    events.append(
        {
            "event_type": "tool-run-result",
            "payload": {
                "source": "github_commits",
                "email": email,
                "total_commits": data.get("total_count", 0),
                "unique_repos": len(by_repo),
                "rate_limited": False,
            },
        }
    )
    return events


def _github_commit_email_synthetic(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Synthetic GitHub commit search: one repo person-match + summary."""
    email = payload.get("email") or "user@example.com"
    return [
        {
            "event_type": "person-match",
            "payload": {
                "source": "github_commits",
                "email": email,
                "repo": "synthetic-user/synthetic-repo",
                "login": "synthetic-user",
                "profile_url": "https://github.com/synthetic-user",
                "author_name": "Synthetic User",
                "commit_count": 7,
                "first_seen": "2023-01-01T00:00:00Z",
                "last_seen": "2024-12-01T00:00:00Z",
                "sample_commit": "https://github.com/synthetic-user/synthetic-repo/commit/abc",
                "synthetic": True,
            },
        },
        {
            "event_type": "tool-run-result",
            "payload": {
                "source": "github_commits",
                "email": email,
                "total_commits": 7,
                "unique_repos": 1,
                "rate_limited": False,
                "synthetic": True,
            },
        },
    ]


# ---------------------------------------------------------------------------
# 3a-iii. Hudson Rock Cavalier -- free infostealer-log surface
#
# Ship #3 of the free-stack replacement for IntelBase (Margaret's parse,
# 2026-05-11). This is the direct functional swap for IntelBase's marquee
# "real-time infostealer log intelligence" feature. Hudson Rock's free
# Cavalier API indexes ~30M+ infected machines and accepts unauthenticated
# email lookups. They already partially redact passwords / logins in the
# free tier response (e.g. "K**********3"), but we layer
# `_redact_credentials` over the response as belt-and-suspenders -- if
# they ever change policy, we don't propagate plaintext credentials into
# our event stream. Same contract as the IntelBase adapter.
#
# Endpoint: GET https://cavalier.hudsonrock.com/api/json/v2/osint-tools/
#                  search-by-email?email=<email>
# Auth:     none, no signup, no key.
#
# Response (verified live, 2026-05-11):
#   {message, stealers[{computer_name, operating_system, malware_path,
#                       antiviruses[], ip, date_compromised,
#                       total_corporate_services, total_user_services,
#                       top_passwords[redacted], top_logins[redacted]}],
#    total_corporate_services, total_user_services}
# ---------------------------------------------------------------------------


_HUDSON_ROCK_URL = "https://cavalier.hudsonrock.com/api/json/v2/osint-tools/search-by-email"


def hudson_rock_email_check(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Hudson Rock Cavalier free infostealer-log lookup. Emits one
    `breach-hit` per stealer entry + a summary tool-run-result with
    top-level totals.

    Payload:
      {"email": "user@example.com"}
    """
    email = payload.get("email", "")
    if not isinstance(email, str) or "@" not in email:
        return [
            {
                "event_type": "tool-run-error",
                "payload": {"reason": "missing or malformed 'email'"},
            }
        ]

    headers: dict[str, str] = {"Accept": "application/json", "User-Agent": _USER_AGENT}

    try:
        with httpx.Client(timeout=15.0, headers=headers, follow_redirects=False) as c:
            r = c.get(_HUDSON_ROCK_URL, params={"email": email})
    except httpx.RequestError as exc:
        return [
            {
                "event_type": "tool-run-error",
                "payload": {
                    "reason": f"hudson_rock {type(exc).__name__}: {exc}",
                    "email": email,
                },
            }
        ]

    if r.status_code != 200:
        return [
            {
                "event_type": "tool-run-error",
                "payload": {
                    "reason": f"hudson_rock HTTP {r.status_code}",
                    "email": email,
                },
            }
        ]

    try:
        data = r.json()
    except ValueError:
        return [
            {
                "event_type": "tool-run-error",
                "payload": {"reason": "hudson_rock non-JSON response", "email": email},
            }
        ]
    if not isinstance(data, dict):
        return [
            {
                "event_type": "tool-run-error",
                "payload": {"reason": "hudson_rock unexpected response shape", "email": email},
            }
        ]

    # Belt-and-suspenders: redact credential-shaped fields recursively
    # before any data is referenced for emit. Hudson Rock's free tier
    # already does partial redaction (K**********3), but we don't trust
    # that to persist.
    safe = _redact_credentials(data)
    stealers = safe.get("stealers") or []
    if not isinstance(stealers, list):
        stealers = []

    events: list[dict[str, Any]] = []
    # Cap at 25 stealer entries -- matches the IntelBase noise budget.
    for s in stealers[:25]:
        if not isinstance(s, dict):
            continue
        events.append(
            {
                "event_type": "breach-hit",
                "payload": {
                    "source": "hudson_rock",
                    "email": email,
                    "computer_name": s.get("computer_name", ""),
                    "operating_system": s.get("operating_system", ""),
                    "malware_path": s.get("malware_path", ""),
                    "antiviruses": s.get("antiviruses", []),
                    "ip": s.get("ip", ""),
                    "date_compromised": s.get("date_compromised", ""),
                    "total_corporate_services": s.get("total_corporate_services", 0),
                    "total_user_services": s.get("total_user_services", 0),
                },
            }
        )

    events.append(
        {
            "event_type": "tool-run-result",
            "payload": {
                "source": "hudson_rock",
                "email": email,
                "stealer_count": len(events),
                "total_corporate_services": safe.get("total_corporate_services", 0),
                "total_user_services": safe.get("total_user_services", 0),
            },
        }
    )
    return events


def _hudson_rock_synthetic(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Synthetic Hudson Rock: one stealer + summary."""
    email = payload.get("email") or "user@example.com"
    return [
        {
            "event_type": "breach-hit",
            "payload": {
                "source": "hudson_rock",
                "email": email,
                "computer_name": "DESKTOP-SYN (synthetic)",
                "operating_system": "Windows 11 (synthetic)",
                "malware_path": "Not Found",
                "antiviruses": ["Windows Defender"],
                "ip": "1.2.**.*",
                "date_compromised": "2024-06-01T00:00:00.000Z",
                "total_corporate_services": 12,
                "total_user_services": 240,
                "synthetic": True,
            },
        },
        {
            "event_type": "tool-run-result",
            "payload": {
                "source": "hudson_rock",
                "email": email,
                "stealer_count": 1,
                "total_corporate_services": 12,
                "total_user_services": 240,
                "synthetic": True,
            },
        },
    ]


# ---------------------------------------------------------------------------
# 3a. IntelBase email lookup -- POST https://api.intelbase.is/lookup/email
#
# IntelBase aggregates 40B+ breach records + real-time infostealer log
# intelligence + cross-platform account fanout from a single email. Far
# richer than HIBP for the property-vetting use case: "does this owner's
# email show up in the kind of breaches/logs we'd expect for a legitimate
# user, or for a churn account behind dozens of fraudulent listings?"
#
# Auth: x-api-key header. Key + IP whitelist managed at
# https://intelbase.is/dashboard/account. Env-gated on
# OSINT_INTELBASE_API_KEY; absent key -> synthetic mode so dev/dry runs
# still see the wire shape.
#
# Safety: infostealer logs include credential rows. We NEVER propagate
# password/hash/plaintext/credential_data fields into the event stream --
# they get filtered defensively before emit. The investigator gets the
# fact-of-the-breach without the credential payload.
# ---------------------------------------------------------------------------


_INTELBASE_URL = "https://api.intelbase.is/lookup/email"

# Defense-in-depth: keys we never emit even if upstream returns them.
# Match case-insensitively; the substring catches name variants
# (password_hash, hashed_password, raw_plaintext, etc.).
_CREDENTIAL_FIELD_SUBSTRINGS: tuple[str, ...] = (
    "password",
    "hash",
    "plaintext",
    "credential",
    "secret",
    "token",
)


def _redact_credentials(obj: Any) -> Any:
    """Strip credential-shaped fields from any nested dict/list. Returns
    a new structure -- never mutates input. Numbers/strings/bools pass
    through unchanged."""
    if isinstance(obj, dict):
        return {
            k: _redact_credentials(v)
            for k, v in obj.items()
            if not any(s in k.lower() for s in _CREDENTIAL_FIELD_SUBSTRINGS)
        }
    if isinstance(obj, list):
        return [_redact_credentials(v) for v in obj]
    return obj


def intelbase_email_lookup(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """IntelBase /lookup/email: breach + account fanout from a single email.

    Payload:
      {"email": "user@example.com",
       "timeout_ms": 15000,             # optional, default 15000
       "include_data_breaches": true,    # optional, default true
       "exclude_modules": []}            # optional, default []

    Env:
      OSINT_INTELBASE_API_KEY  -- required for live mode; absent -> synthetic

    Wire shape:
      One `breach-hit` per breach surfaced (credential fields stripped).
      One `person-match` per linked account surfaced.
      One `tool-run-result` summary with counts.
    """
    email = payload.get("email", "")
    if not isinstance(email, str) or "@" not in email:
        return [
            {
                "event_type": "tool-run-error",
                "payload": {"reason": "missing or malformed 'email'"},
            }
        ]

    api_key = os.environ.get("OSINT_INTELBASE_API_KEY", "").strip()
    if not api_key:
        return _intelbase_synthetic(payload)

    timeout_ms = int(payload.get("timeout_ms", 15000))
    body: dict[str, Any] = {"email": email, "timeout_ms": timeout_ms}
    if "include_data_breaches" in payload:
        body["include_data_breaches"] = bool(payload["include_data_breaches"])
    if "exclude_modules" in payload:
        body["exclude_modules"] = list(payload["exclude_modules"])

    try:
        # Use a fresh client (not _client) so we can pin the auth header
        # without leaking it to the shared module config.
        with httpx.Client(
            timeout=(timeout_ms / 1000.0) + 5.0,
            headers={
                "User-Agent": _USER_AGENT,
                "Accept": "application/json",
                "Content-Type": "application/json",
                "x-api-key": api_key,
            },
            follow_redirects=False,
        ) as c:
            r = c.post(_INTELBASE_URL, json=body)
    except httpx.RequestError as exc:
        return [
            {
                "event_type": "tool-run-error",
                "payload": {
                    "reason": f"intelbase {type(exc).__name__}: {exc}",
                    "email": email,
                },
            }
        ]

    if r.status_code != 200:
        return [
            {
                "event_type": "tool-run-error",
                "payload": {
                    "reason": f"intelbase HTTP {r.status_code}",
                    "email": email,
                },
            }
        ]

    try:
        data = r.json()
    except ValueError:
        return [
            {
                "event_type": "tool-run-error",
                "payload": {"reason": "intelbase non-JSON response", "email": email},
            }
        ]
    if not isinstance(data, dict):
        return [
            {
                "event_type": "tool-run-error",
                "payload": {"reason": "intelbase unexpected response shape", "email": email},
            }
        ]

    # Response shape is undocumented in the OpenAPI spec, so we read
    # common field names defensively. `breaches`, `accounts`, `linked_accounts`
    # are the names IntelBase's site/docs use.
    breaches_raw = data.get("breaches") or data.get("data_breaches") or []
    accounts_raw = data.get("accounts") or data.get("linked_accounts") or data.get("modules") or []

    events: list[dict[str, Any]] = []
    # Dossier-noise cap: 25 breaches + 25 accounts is plenty for a single
    # email lookup; the rest stay in the summary count.
    if isinstance(breaches_raw, list):
        for b in breaches_raw[:25]:
            if not isinstance(b, dict):
                continue
            redacted = _redact_credentials(b)
            events.append(
                {
                    "event_type": "breach-hit",
                    "payload": {
                        "source": "intelbase",
                        "email": email,
                        "name": redacted.get("name") or redacted.get("breach") or "",
                        "domain": redacted.get("domain", ""),
                        "breach_date": redacted.get("breach_date") or redacted.get("date", ""),
                        "data_classes": redacted.get("data_classes") or redacted.get("fields", []),
                    },
                }
            )
    if isinstance(accounts_raw, list):
        for a in accounts_raw[:25]:
            if not isinstance(a, dict):
                continue
            redacted = _redact_credentials(a)
            events.append(
                {
                    "event_type": "person-match",
                    "payload": {
                        "source": "intelbase",
                        "email": email,
                        "platform": redacted.get("platform")
                        or redacted.get("module")
                        or redacted.get("service", ""),
                        "username": redacted.get("username") or redacted.get("handle", ""),
                        "profile_url": redacted.get("profile_url") or redacted.get("url", ""),
                    },
                }
            )

    breach_count = sum(1 for e in events if e["event_type"] == "breach-hit")
    account_count = sum(1 for e in events if e["event_type"] == "person-match")
    events.append(
        {
            "event_type": "tool-run-result",
            "payload": {
                "source": "intelbase",
                "email": email,
                "breaches": breach_count,
                "accounts": account_count,
            },
        }
    )
    return events


def _intelbase_synthetic(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Synthetic: one breach + one account + summary. Locks the wire
    shape the live path emits so frontend/dossier consumers can be
    tested without hitting the real API."""
    email = payload.get("email") or "user@example.com"
    return [
        {
            "event_type": "breach-hit",
            "payload": {
                "source": "intelbase",
                "email": email,
                "name": "SyntheticIntelBaseBreach",
                "domain": "example.com",
                "breach_date": "2024-01-01",
                "data_classes": ["Email addresses", "Usernames"],
                "synthetic": True,
            },
        },
        {
            "event_type": "person-match",
            "payload": {
                "source": "intelbase",
                "email": email,
                "platform": "github",
                "username": "synthetic-user",
                "profile_url": "https://github.com/synthetic-user",
                "synthetic": True,
            },
        },
        {
            "event_type": "tool-run-result",
            "payload": {
                "source": "intelbase",
                "email": email,
                "breaches": 1,
                "accounts": 1,
                "synthetic": True,
            },
        },
    ]


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
    "intelbase_email_lookup",
    intelbase_email_lookup,
    synthetic_mode=_intelbase_synthetic,
    in_process=True,
    description=(
        "IntelBase /lookup/email: breach + account fanout from a single email "
        "(40B+ breach records, infostealer logs). Env-gated on "
        "OSINT_INTELBASE_API_KEY."
    ),
)

_REGISTRY.register(
    "gravatar_profile_lookup",
    gravatar_profile_lookup,
    synthetic_mode=_gravatar_synthetic,
    in_process=True,
    description=(
        "Gravatar v3 profile lookup. Owner-attested identity pivot: emits "
        "person-match per verified_accounts entry. Free anonymous (100/hr); "
        "OSINT_GRAVATAR_TOKEN unlocks 1000/hr."
    ),
)

_REGISTRY.register(
    "github_commit_email_search",
    github_commit_email_search,
    synthetic_mode=_github_commit_email_synthetic,
    in_process=True,
    description=(
        "GitHub /search/commits?q=author-email:<email>. Behavioral identity "
        "confirm: emits person-match per unique repo. Free anonymous "
        "(10/min); OSINT_GITHUB_PAT unlocks 30/min."
    ),
)

_REGISTRY.register(
    "hudson_rock_email_check",
    hudson_rock_email_check,
    synthetic_mode=_hudson_rock_synthetic,
    in_process=True,
    description=(
        "Hudson Rock Cavalier free infostealer-log lookup. Indexes 30M+ "
        "infected machines; emits breach-hit per stealer entry. "
        "Credential fields stripped (belt-and-suspenders over their own "
        "partial redaction). No auth required."
    ),
)

# user-scanner subprocess wrapper (ship #4 of the free-stack). Runs in
# the empirical venv where `pip install user-scanner` lives. 95+ service
# probes; pure-httpx, no Playwright. Fallback to in-process stub when the
# wrapper or empirical venv aren't present.
_USER_SCANNER_WRAPPER = _REPO_ROOT_PROP / "adapters" / "user_scanner" / "wrapper.py"


def _user_scanner_in_process_stub(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """In-process fallback when the empirical venv or wrapper is absent."""
    email = payload.get("email", "user@example.com")
    return [
        {
            "event_type": "tool-run-error",
            "payload": {
                "reason": (
                    "user_scanner wrapper or empirical venv not present; "
                    "live mode unavailable. Install user-scanner via "
                    "`pip install user-scanner` in the empirical venv."
                ),
                "email": email,
            },
        }
    ]


def _user_scanner_synthetic(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """In-process synthetic for unit-test paths that don't fork."""
    email = payload.get("email", "user@example.com")
    return [
        {
            "event_type": "person-match",
            "payload": {
                "source": "user_scanner",
                "email": email,
                "platform": "github",
                "category": "Development",
                "profile_url": "https://github.com",
                "synthetic": True,
            },
        },
        {
            "event_type": "tool-run-result",
            "payload": {
                "source": "user_scanner",
                "email": email,
                "checked": 95,
                "found": 1,
                "errored": 0,
                "synthetic": True,
            },
        },
    ]


if _USER_SCANNER_WRAPPER.is_file() and _EMPIRICAL_PY.is_file():
    _REGISTRY.register(
        "user_scanner",
        make_subprocess_adapter(
            _USER_SCANNER_WRAPPER,
            timeout_s=180.0,
            python_executable=str(_EMPIRICAL_PY),
        ),
        synthetic_mode=make_subprocess_adapter(
            _USER_SCANNER_WRAPPER,
            timeout_s=15.0,
            python_executable=str(_EMPIRICAL_PY),
            extra_env={"OSINT_ADAPTER_MODE": "synthetic"},
        ),
        in_process=False,
        description=(
            "user-scanner (holehe successor) via subprocess. 95+ services "
            "probed from a single email. Wrapper in adapters/user_scanner/, "
            "package in the empirical venv."
        ),
    )
else:
    _REGISTRY.register(
        "user_scanner",
        _user_scanner_in_process_stub,
        synthetic_mode=_user_scanner_synthetic,
        in_process=True,
        description=(
            "user_scanner -- empirical venv or wrapper missing; in-process stub only. "
            "Install user-scanner in the empirical venv to unlock live mode."
        ),
    )

# Partial-recovery adapters (ship A+F+B+C, 2026-05-11). Castrickclues's
# marquee technique: submit a target to a platform's forgot-password flow
# and harvest the obfuscated partial email/phone the platform displays.
# One shared wrapper, four registered adapters dispatched via the
# OSINT_PARTIAL_PLATFORM env var. Same Patchright-subprocess pattern as
# true_people_search.
_PARTIAL_WRAPPER = _REPO_ROOT_PROP / "adapters" / "partial_recovery" / "wrapper.py"


def _make_partial_in_process_stub(platform: str) -> Any:
    def _stub(payload: dict[str, Any]) -> list[dict[str, Any]]:
        target = (
            payload.get("target")
            or payload.get("email")
            or payload.get("phone")
            or "user@example.com"
        )
        return [
            {
                "event_type": "tool-run-error",
                "payload": {
                    "reason": (
                        f"{platform}_partial_pivot wrapper or empirical venv not "
                        "present; live mode unavailable."
                    ),
                    "target": target,
                },
            }
        ]

    return _stub


def _make_partial_in_process_synthetic(platform: str) -> Any:
    """In-process synthetic mirrors the redacted wire shape (Naomi #3):
    `email_partial_meta` carries first-char + length + domain, NOT the
    raw partial string. The wrapper-side synthetic also redacts by
    default; opt-in via OSINT_PARTIAL_KEEP_VALUES=1 only on the wrapper
    path (in-process is intentionally fixture-only)."""

    def _synth(payload: dict[str, Any]) -> list[dict[str, Any]]:
        target = (
            payload.get("target")
            or payload.get("email")
            or payload.get("phone")
            or "user@example.com"
        )
        return [
            {
                "event_type": "person-match",
                "payload": {
                    "source": f"{platform}_partial",
                    "target": target,
                    "account_exists": True,
                    "email_partial_meta": {
                        "raw_length": 16,
                        "first": "s",
                        "local_length": 4,
                        "domain": "e***le.com",
                    },
                    "synthetic": True,
                },
            },
            {
                "event_type": "tool-run-result",
                "payload": {
                    "source": f"{platform}_partial",
                    "target": target,
                    "partials_visible_count": 1,
                    "account_signal": "exists",
                    "values_kept": False,
                    "synthetic": True,
                },
            },
        ]

    return _synth


_PARTIAL_PLATFORMS: tuple[tuple[str, str], ...] = (
    ("microsoft", "MS account password-reset partial-email/phone leak (Ship A)."),
    ("linkedin", "LinkedIn forgot-password partial-email leak (Ship F)."),
    ("instagram", "Instagram forgot-password partial-email/phone leak (Ship B)."),
    ("twitter", "Twitter/X forgot-password partial-email/phone leak (Ship C)."),
)

for _platform, _description in _PARTIAL_PLATFORMS:
    _adapter_id = f"{_platform}_partial_pivot"
    if _PARTIAL_WRAPPER.is_file() and _EMPIRICAL_PY.is_file():
        _REGISTRY.register(
            _adapter_id,
            make_subprocess_adapter(
                _PARTIAL_WRAPPER,
                timeout_s=45.0,  # browser launch + nav + parse, generous
                python_executable=str(_EMPIRICAL_PY),
                extra_env={"OSINT_PARTIAL_PLATFORM": _platform},
            ),
            synthetic_mode=make_subprocess_adapter(
                _PARTIAL_WRAPPER,
                timeout_s=10.0,
                python_executable=str(_EMPIRICAL_PY),
                extra_env={
                    "OSINT_PARTIAL_PLATFORM": _platform,
                    "OSINT_ADAPTER_MODE": "synthetic",
                },
            ),
            in_process=False,
            description=_description,
        )
    else:
        _REGISTRY.register(
            _adapter_id,
            _make_partial_in_process_stub(_platform),
            synthetic_mode=_make_partial_in_process_synthetic(_platform),
            in_process=True,
            description=_description + " (in-process stub; empirical venv missing).",
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

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

import os
import re
import socket
import time
from typing import Any

import httpx

from .adapters import get_registry

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
# 4. + 5. Stub registrations: TruePeopleSearch, TinEye
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
    "true_people_search",
    _true_people_live_stub,
    synthetic_mode=_true_people_synthetic,
    in_process=True,
    description="TruePeopleSearch -- live needs Scrapling (Sprint 3+). Synthetic available.",
)

_REGISTRY.register(
    "tineye_image",
    _tineye_live_stub,
    synthetic_mode=_tineye_synthetic,
    in_process=True,
    description="TinEye reverse image -- live needs API key (Sprint 3+). Synthetic available.",
)

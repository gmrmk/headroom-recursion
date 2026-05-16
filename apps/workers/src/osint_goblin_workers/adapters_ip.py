"""IP-vetting adapters (W10.ip workflow, Sprint 4).

Closes the last gap in the user's six-primitive triangulation
(name / address / phone / email / lat-long / IP). All four adapters
are in-process; three need no API keys.

  - ip_geolocation: ip-api.com free tier (no key, 45/min limit).
  - ip_reverse_dns: stdlib socket.gethostbyaddr PTR lookup.
  - ip_asn_lookup: Team Cymru WHOIS via TCP socket (free, no auth).
  - ip_reputation: AbuseIPDB v2 API (needs OSINT_ABUSEIPDB_KEY env).

Property-vetting use case: investigators who have access to message
headers (Airbnb private messages with the host, or Vrbo inquiry
threads) can extract the host's IP from Received headers and
triangulate against the listing's claimed location.
"""

from __future__ import annotations

import ipaddress
import os
import socket
from typing import Any

import httpx

from ._ua import default_ua
from .adapters import get_registry

# W4-UA (Margaret wave-4 roadmap §3): default UA is now Chrome-on-Win11
# so a target webserver's access logs don't attribute probes back to
# the operator. OSINT_TRANSPARENT_UA=1 restores the osint-goblin literal.
# OSINT_USER_AGENT still wins if explicitly set (lets ops pin any string).
_DEFAULT_UA = default_ua()
_USER_AGENT = os.environ.get("OSINT_USER_AGENT", _DEFAULT_UA)


def _client(timeout_s: float = 10.0) -> httpx.Client:
    return httpx.Client(
        timeout=timeout_s,
        headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
        follow_redirects=True,
    )


def _is_valid_ip(s: str) -> bool:
    try:
        ipaddress.ip_address(s)
        return True
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# 1. ip_geolocation -- ip-api.com (free, no key)
# ---------------------------------------------------------------------------


def ip_geolocation(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """ip-api.com /json/ endpoint. Free tier: 45 req/min, no auth.

    Payload: {"ip": "8.8.8.8"}

    Returns country + region + city + ISP + lat/lon + AS. Useful for
    "host claims Springfield IL but IP geolocates to Manila" detection.
    """
    ip = (payload.get("ip") or "").strip()
    if not ip:
        return [
            {
                "event_type": "tool-run-error",
                "payload": {"reason": "missing 'ip'"},
            }
        ]
    if not _is_valid_ip(ip):
        return [
            {
                "event_type": "tool-run-error",
                "payload": {"reason": f"not a valid IP: {ip!r}"},
            }
        ]
    try:
        with _client(timeout_s=10.0) as c:
            r = c.get(f"http://ip-api.com/json/{ip}")
        if r.status_code != 200:
            return [
                {
                    "event_type": "tool-run-error",
                    "payload": {"reason": f"ip-api HTTP {r.status_code}", "ip": ip},
                }
            ]
        data = r.json() or {}
    except httpx.RequestError as exc:
        return [
            {
                "event_type": "tool-run-error",
                "payload": {"reason": f"ip-api {type(exc).__name__}: {exc}", "ip": ip},
            }
        ]
    if data.get("status") != "success":
        return [
            {
                "event_type": "tool-run-result",
                "payload": {
                    "ip": ip,
                    "found": False,
                    "reason": data.get("message", "lookup failed"),
                },
            }
        ]
    return [
        {
            "event_type": "person-match",
            "payload": {
                "source": "ip-api",
                "ip": ip,
                "country": data.get("country", ""),
                "country_code": data.get("countryCode", ""),
                "region": data.get("regionName", ""),
                "region_code": data.get("region", ""),
                "city": data.get("city", ""),
                "zip": data.get("zip", ""),
                "lat": data.get("lat"),
                "lon": data.get("lon"),
                "timezone": data.get("timezone", ""),
                "isp": data.get("isp", ""),
                "organization": data.get("org", ""),
                "as": data.get("as", ""),
                "is_proxy": data.get("proxy", False),
                "is_hosting": data.get("hosting", False),
                "is_mobile": data.get("mobile", False),
            },
        },
        {
            "event_type": "tool-run-result",
            "payload": {
                "ip": ip,
                "found": True,
                "country": data.get("country", ""),
                "city": data.get("city", ""),
                "isp": data.get("isp", ""),
            },
        },
    ]


def _ip_geolocation_synthetic(payload: dict[str, Any]) -> list[dict[str, Any]]:
    ip = payload.get("ip", "8.8.8.8")
    return [
        {
            "event_type": "person-match",
            "payload": {
                "source": "ip-api",
                "ip": ip,
                "country": "United States",
                "country_code": "US",
                "region": "Illinois",
                "region_code": "IL",
                "city": "Springfield",
                "zip": "62701",
                "lat": 39.78,
                "lon": -89.65,
                "timezone": "America/Chicago",
                "isp": "Synthetic Telecom",
                "organization": "Synthetic Telecom LLC",
                "as": "AS65000 Synthetic Net",
                "is_proxy": False,
                "is_hosting": False,
                "is_mobile": False,
                "synthetic": True,
            },
        },
        {
            "event_type": "tool-run-result",
            "payload": {
                "ip": ip,
                "found": True,
                "country": "United States",
                "city": "Springfield",
                "isp": "Synthetic Telecom",
                "synthetic": True,
            },
        },
    ]


# ---------------------------------------------------------------------------
# 2. ip_reverse_dns -- stdlib socket PTR lookup
# ---------------------------------------------------------------------------


def ip_reverse_dns(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """PTR (reverse DNS) lookup. No external dependency."""
    ip = (payload.get("ip") or "").strip()
    if not _is_valid_ip(ip):
        return [
            {
                "event_type": "tool-run-error",
                "payload": {"reason": f"not a valid IP: {ip!r}"},
            }
        ]
    try:
        host, aliases, _ = socket.gethostbyaddr(ip)
    except (socket.herror, socket.gaierror) as exc:
        return [
            {
                "event_type": "tool-run-result",
                "payload": {
                    "ip": ip,
                    "ptr": "",
                    "reason": f"no PTR: {exc}",
                },
            }
        ]
    except OSError as exc:
        return [
            {
                "event_type": "tool-run-error",
                "payload": {"reason": f"DNS error: {exc}", "ip": ip},
            }
        ]
    return [
        {
            "event_type": "person-match",
            "payload": {
                "source": "reverse-dns",
                "ip": ip,
                "ptr": host,
                "aliases": list(aliases),
            },
        },
        {
            "event_type": "tool-run-result",
            "payload": {"ip": ip, "ptr": host},
        },
    ]


def _ip_reverse_dns_synthetic(payload: dict[str, Any]) -> list[dict[str, Any]]:
    ip = payload.get("ip", "8.8.8.8")
    return [
        {
            "event_type": "person-match",
            "payload": {
                "source": "reverse-dns",
                "ip": ip,
                "ptr": "synthetic-host.example.com",
                "aliases": [],
                "synthetic": True,
            },
        },
        {
            "event_type": "tool-run-result",
            "payload": {"ip": ip, "ptr": "synthetic-host.example.com", "synthetic": True},
        },
    ]


# ---------------------------------------------------------------------------
# 3. ip_asn_lookup -- Team Cymru WHOIS over TCP (no auth)
# ---------------------------------------------------------------------------


def ip_asn_lookup(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Team Cymru IP-to-ASN WHOIS service. Free, no auth, no API key.

    Protocol: TCP connect to whois.cymru.com:43, send `-v <ip>\\n`,
    read the response, parse the pipe-delimited fields.
    """
    ip = (payload.get("ip") or "").strip()
    if not _is_valid_ip(ip):
        return [
            {
                "event_type": "tool-run-error",
                "payload": {"reason": f"not a valid IP: {ip!r}"},
            }
        ]
    timeout_s = float(payload.get("timeout_s", 8.0))
    try:
        with socket.create_connection(("whois.cymru.com", 43), timeout=timeout_s) as s:
            s.sendall(f" -v {ip}\n".encode("ascii"))
            buf = bytearray()
            while True:
                chunk = s.recv(4096)
                if not chunk:
                    break
                buf.extend(chunk)
                if len(buf) > 16 * 1024:
                    break  # cap defensive
        text = buf.decode("utf-8", errors="replace")
    except (TimeoutError, OSError) as exc:
        return [
            {
                "event_type": "tool-run-error",
                "payload": {
                    "reason": f"cymru WHOIS {type(exc).__name__}: {exc}",
                    "ip": ip,
                },
            }
        ]
    # Response shape: header line + data line
    #   AS      | IP     | BGP Prefix | CC | Registry | Allocated  | AS Name
    #   15169   | 8.8.8.8| 8.8.8.0/24 | US | arin     | 1992-12-01 | GOOGLE, US
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    data_line = next((line for line in lines if not line.lower().startswith("as ")), "")
    if not data_line:
        return [
            {
                "event_type": "tool-run-result",
                "payload": {"ip": ip, "asn": "", "as_name": "", "found": False},
            }
        ]
    parts = [p.strip() for p in data_line.split("|")]
    if len(parts) < 7:
        return [
            {
                "event_type": "tool-run-result",
                "payload": {
                    "ip": ip,
                    "asn": "",
                    "raw": data_line,
                    "found": False,
                    "reason": "unexpected cymru response shape",
                },
            }
        ]
    asn, _, bgp_prefix, cc, registry, allocated, as_name = (
        parts[0],
        parts[1],
        parts[2],
        parts[3],
        parts[4],
        parts[5],
        parts[6],
    )
    return [
        {
            "event_type": "person-match",
            "payload": {
                "source": "team-cymru",
                "ip": ip,
                "asn": asn,
                "as_name": as_name,
                "bgp_prefix": bgp_prefix,
                "country_code": cc,
                "registry": registry,
                "allocated": allocated,
            },
        },
        {
            "event_type": "tool-run-result",
            "payload": {"ip": ip, "asn": asn, "as_name": as_name, "found": True},
        },
    ]


def _ip_asn_lookup_synthetic(payload: dict[str, Any]) -> list[dict[str, Any]]:
    ip = payload.get("ip", "8.8.8.8")
    return [
        {
            "event_type": "person-match",
            "payload": {
                "source": "team-cymru",
                "ip": ip,
                "asn": "15169",
                "as_name": "GOOGLE, US",
                "bgp_prefix": "8.8.8.0/24",
                "country_code": "US",
                "registry": "arin",
                "allocated": "1992-12-01",
                "synthetic": True,
            },
        },
        {
            "event_type": "tool-run-result",
            "payload": {"ip": ip, "asn": "15169", "as_name": "GOOGLE, US", "synthetic": True},
        },
    ]


# ---------------------------------------------------------------------------
# 4. ip_reputation -- AbuseIPDB v2 (needs API key)
# ---------------------------------------------------------------------------


def ip_reputation(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """AbuseIPDB reputation lookup. Free tier: 1000 checks/day with key.

    Requires OSINT_ABUSEIPDB_KEY env. Honest error if not set; no
    silent uploads.
    """
    api_key = os.environ.get("OSINT_ABUSEIPDB_KEY", "").strip()
    if not api_key:
        # Missing optional API key isn't an adapter failure -- emit a
        # result-shaped skip so the dossier doesn't log it as an error.
        return [
            {
                "event_type": "tool-run-result",
                "payload": {
                    "adapter_id": "ip_reputation",
                    "skipped": True,
                    "reason": "AbuseIPDB API key not set",
                    "suggest": (
                        "Sign up free at abuseipdb.com (1000/day); set OSINT_ABUSEIPDB_KEY env var"
                    ),
                },
            }
        ]
    ip = (payload.get("ip") or "").strip()
    if not _is_valid_ip(ip):
        return [
            {
                "event_type": "tool-run-error",
                "payload": {"reason": f"not a valid IP: {ip!r}"},
            }
        ]
    try:
        with _client(timeout_s=10.0) as c:
            r = c.get(
                "https://api.abuseipdb.com/api/v2/check",
                params={"ipAddress": ip, "maxAgeInDays": "90", "verbose": ""},
                headers={"Key": api_key, "Accept": "application/json"},
            )
        if r.status_code != 200:
            return [
                {
                    "event_type": "tool-run-error",
                    "payload": {
                        "reason": f"abuseipdb HTTP {r.status_code}",
                        "ip": ip,
                    },
                }
            ]
        data = r.json().get("data") or {}
    except httpx.RequestError as exc:
        return [
            {
                "event_type": "tool-run-error",
                "payload": {
                    "reason": f"abuseipdb {type(exc).__name__}: {exc}",
                    "ip": ip,
                },
            }
        ]
    score = int(data.get("abuseConfidenceScore", 0) or 0)
    verdict = "high-abuse" if score >= 75 else "elevated-abuse" if score >= 25 else "clean"
    return [
        {
            "event_type": "breach-hit" if score >= 25 else "person-match",
            "payload": {
                "source": "abuseipdb",
                "ip": ip,
                "score": score,
                "verdict": verdict,
                "total_reports": data.get("totalReports", 0),
                "country_code": data.get("countryCode", ""),
                "isp": data.get("isp", ""),
                "domain": data.get("domain", ""),
                "usage_type": data.get("usageType", ""),
                "last_reported_at": data.get("lastReportedAt", ""),
            },
        },
        {
            "event_type": "tool-run-result",
            "payload": {"ip": ip, "score": score, "verdict": verdict},
        },
    ]


def _ip_reputation_synthetic(payload: dict[str, Any]) -> list[dict[str, Any]]:
    ip = payload.get("ip", "8.8.8.8")
    return [
        {
            "event_type": "person-match",
            "payload": {
                "source": "abuseipdb",
                "ip": ip,
                "score": 0,
                "verdict": "clean",
                "total_reports": 0,
                "country_code": "US",
                "isp": "Synthetic Telecom",
                "synthetic": True,
            },
        },
        {
            "event_type": "tool-run-result",
            "payload": {"ip": ip, "score": 0, "verdict": "clean", "synthetic": True},
        },
    ]


# ---------------------------------------------------------------------------
# Registry installation
# ---------------------------------------------------------------------------

_REGISTRY = get_registry()

_REGISTRY.register(
    "ip_geolocation",
    ip_geolocation,
    synthetic_mode=_ip_geolocation_synthetic,
    in_process=True,
    description="ip-api.com geolocation (W10.ip, no API key, 45/min).",
)
_REGISTRY.register(
    "ip_reverse_dns",
    ip_reverse_dns,
    synthetic_mode=_ip_reverse_dns_synthetic,
    in_process=True,
    description="PTR (reverse DNS) lookup via stdlib socket (W10.ip).",
)
_REGISTRY.register(
    "ip_asn_lookup",
    ip_asn_lookup,
    synthetic_mode=_ip_asn_lookup_synthetic,
    in_process=True,
    description="Team Cymru IP-to-ASN WHOIS (W10.ip, free, no auth).",
)
_REGISTRY.register(
    "ip_reputation",
    ip_reputation,
    synthetic_mode=_ip_reputation_synthetic,
    in_process=True,
    description="AbuseIPDB reputation (W10.ip, needs OSINT_ABUSEIPDB_KEY env).",
)

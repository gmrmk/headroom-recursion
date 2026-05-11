"""Domain + subdomain enumeration adapters (W5.do workflow, Sprint 4).

Four adapters. Two pure-HTTP (no binary dep), two subprocess wrappers
around the Project Discovery + OWASP tools that the OSINT community
treats as the subdomain-enumeration standards.

  - ct_log_lookup: crt.sh free public API; certificate transparency
    log query returns subdomains discovered via issued TLS certs.
  - wayback_cdx_subdomains: archive.org CDX API; historical subdomain
    sightings from Wayback's URL index.
  - subfinder_subprocess: wraps `subfinder` binary (Project Discovery)
    if installed. Honest error with install command if not on PATH.
  - amass_subprocess: wraps `amass` binary (OWASP) if installed; same.

Property-vetting use case: when a host claims a business website
("we own pizza-place.com"), subdomain enumeration surfaces the full
infrastructure footprint (subdomains, staging, dev environments,
historic certificates). Mismatches between claimed scale and actual
footprint are a fraud signal.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import httpx

from .adapters import get_registry

_DEFAULT_UA = "osint-goblin/0.1 (https://github.com/local; personal-investigator)"
_USER_AGENT = os.environ.get("OSINT_USER_AGENT", _DEFAULT_UA)


def _client(timeout_s: float = 15.0) -> httpx.Client:
    return httpx.Client(
        timeout=timeout_s,
        headers={"User-Agent": _USER_AGENT, "Accept": "*/*"},
        follow_redirects=True,
    )


# ---------------------------------------------------------------------------
# 1. ct_log_lookup -- crt.sh
# ---------------------------------------------------------------------------


def ct_log_lookup(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Certificate Transparency log query via crt.sh.

    Payload:
      {"domain": "example.com", "limit": 100}

    crt.sh exposes a JSON endpoint (output=json) that returns issuance
    records for any cert whose common name or SAN matches `%.<domain>`.
    Free, no auth. Dedups subdomains in-process. Anti-pattern would be
    parsing the HTML page; the JSON endpoint is the supported surface.
    """
    domain = (payload.get("domain") or "").strip().lower()
    if not domain:
        return [
            {
                "event_type": "tool-run-error",
                "payload": {"reason": "missing 'domain'"},
            }
        ]
    limit = min(int(payload.get("limit", 100)), 500)
    try:
        with _client(timeout_s=20.0) as c:
            r = c.get(
                "https://crt.sh/",
                params={"q": f"%.{domain}", "output": "json"},
            )
        if r.status_code != 200:
            return [
                {
                    "event_type": "tool-run-error",
                    "payload": {
                        "reason": f"crt.sh HTTP {r.status_code}",
                        "domain": domain,
                    },
                }
            ]
        rows = r.json() or []
    except httpx.RequestError as exc:
        return [
            {
                "event_type": "tool-run-error",
                "payload": {
                    "reason": f"crt.sh {type(exc).__name__}: {exc}",
                    "domain": domain,
                },
            }
        ]
    except ValueError as exc:
        # JSON decode error -- crt.sh occasionally serves HTML during load
        return [
            {
                "event_type": "tool-run-error",
                "payload": {
                    "reason": f"crt.sh non-JSON response: {exc}",
                    "domain": domain,
                    "suggest": "crt.sh may be overloaded; retry in a minute",
                },
            }
        ]

    seen: set[str] = set()
    events: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        # name_value can be multiline (one cert may cover many SANs)
        for sub in str(row.get("name_value") or "").splitlines():
            sub = sub.strip().lower().lstrip("*.")
            if not sub or sub in seen:
                continue
            if not sub.endswith(domain):
                continue
            seen.add(sub)
            events.append(
                {
                    "event_type": "listing-match",  # reusing for domain hits; semantics =
                    # "found a related entity tied to this subject"
                    "payload": {
                        "source": "crt.sh",
                        "domain": domain,
                        "subdomain": sub,
                        "issuer": row.get("issuer_name", ""),
                        "not_before": row.get("not_before", ""),
                        "not_after": row.get("not_after", ""),
                        "cert_id": row.get("id", ""),
                    },
                }
            )
            if len(events) >= limit:
                break
        if len(events) >= limit:
            break
    events.append(
        {
            "event_type": "tool-run-result",
            "payload": {"domain": domain, "subdomains": len(events) - 0},
        }
    )
    return events


def _ct_log_lookup_synthetic(payload: dict[str, Any]) -> list[dict[str, Any]]:
    domain = payload.get("domain") or "example.com"
    fixtures = [
        ("www", "Synthetic CA", "2025-01-01", "2026-01-01"),
        ("api", "Synthetic CA", "2025-03-15", "2026-03-15"),
        ("staging", "Synthetic CA", "2024-11-20", "2025-11-20"),
        ("admin", "Synthetic CA", "2024-06-01", "2025-06-01"),
    ]
    events = [
        {
            "event_type": "listing-match",
            "payload": {
                "source": "crt.sh",
                "domain": domain,
                "subdomain": f"{sub}.{domain}",
                "issuer": issuer,
                "not_before": nb,
                "not_after": na,
                "cert_id": 12345 + i,
                "synthetic": True,
            },
        }
        for i, (sub, issuer, nb, na) in enumerate(fixtures)
    ]
    events.append(
        {
            "event_type": "tool-run-result",
            "payload": {"domain": domain, "subdomains": len(fixtures), "synthetic": True},
        }
    )
    return events


# ---------------------------------------------------------------------------
# 2. wayback_cdx_subdomains -- archive.org CDX
# ---------------------------------------------------------------------------


def wayback_cdx_subdomains(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Subdomain enumeration via Wayback Machine's CDX API.

    Payload:
      {"domain": "example.com", "limit": 100}

    Queries http://web.archive.org/cdx/search/cdx with the `url=*.<domain>`
    filter; returns distinct hostnames Wayback has ever archived. Free,
    no key. Coverage is "what the IA has crawled" -- often broader than
    CT logs for old subdomains that never got HTTPS but narrower for
    brand-new ones.
    """
    domain = (payload.get("domain") or "").strip().lower()
    if not domain:
        return [
            {
                "event_type": "tool-run-error",
                "payload": {"reason": "missing 'domain'"},
            }
        ]
    limit = min(int(payload.get("limit", 100)), 500)
    try:
        with _client(timeout_s=25.0) as c:
            r = c.get(
                "https://web.archive.org/cdx/search/cdx",
                params={
                    "url": f"*.{domain}/*",
                    "output": "json",
                    "fl": "original",
                    "collapse": "urlkey",
                    "limit": str(limit * 3),  # over-fetch for dedup headroom
                },
            )
        if r.status_code != 200:
            return [
                {
                    "event_type": "tool-run-error",
                    "payload": {
                        "reason": f"wayback CDX HTTP {r.status_code}",
                        "domain": domain,
                    },
                }
            ]
        rows = r.json() or []
    except httpx.RequestError as exc:
        return [
            {
                "event_type": "tool-run-error",
                "payload": {"reason": f"wayback {type(exc).__name__}: {exc}"},
            }
        ]
    except ValueError as exc:
        return [
            {
                "event_type": "tool-run-error",
                "payload": {"reason": f"wayback non-JSON: {exc}"},
            }
        ]

    # CDX JSON: first row is the header; subsequent rows are [original_url].
    seen: set[str] = set()
    events: list[dict[str, Any]] = []
    for row in rows[1:]:  # skip header
        if not isinstance(row, list) or not row:
            continue
        url = str(row[0])
        # Extract host from URL
        host_part = url.split("//", 1)[-1].split("/", 1)[0].lower()
        # Strip port if present
        host_part = host_part.split(":", 1)[0]
        if not host_part or host_part in seen or not host_part.endswith(domain):
            continue
        seen.add(host_part)
        events.append(
            {
                "event_type": "listing-match",
                "payload": {
                    "source": "wayback-cdx",
                    "domain": domain,
                    "subdomain": host_part,
                    "first_seen_url": url,
                },
            }
        )
        if len(events) >= limit:
            break
    events.append(
        {
            "event_type": "tool-run-result",
            "payload": {"domain": domain, "subdomains": len(events) - 0},
        }
    )
    return events


def _wayback_cdx_subdomains_synthetic(payload: dict[str, Any]) -> list[dict[str, Any]]:
    domain = payload.get("domain") or "example.com"
    fixtures = ["www", "blog", "shop", "old", "support"]
    events = [
        {
            "event_type": "listing-match",
            "payload": {
                "source": "wayback-cdx",
                "domain": domain,
                "subdomain": f"{sub}.{domain}",
                "first_seen_url": f"https://{sub}.{domain}/",
                "synthetic": True,
            },
        }
        for sub in fixtures
    ]
    events.append(
        {
            "event_type": "tool-run-result",
            "payload": {"domain": domain, "subdomains": len(fixtures), "synthetic": True},
        }
    )
    return events


# ---------------------------------------------------------------------------
# 3. subfinder_subprocess -- Project Discovery binary
# ---------------------------------------------------------------------------


def _binary_subprocess(
    binary: str,
    install_hint: str,
    payload: dict[str, Any],
    source_label: str,
    extra_args: list[str],
) -> list[dict[str, Any]]:
    """Shared driver for subfinder + amass wrappers. Both share the
    contract: stdin none, stdout one host per line, single -d <domain>
    argument."""
    domain = (payload.get("domain") or "").strip().lower()
    if not domain:
        return [
            {
                "event_type": "tool-run-error",
                "payload": {"reason": "missing 'domain'"},
            }
        ]
    bin_path = shutil.which(binary)
    if not bin_path:
        return [
            {
                "event_type": "tool-run-error",
                "payload": {
                    "reason": f"{binary} not on PATH",
                    "suggest": install_hint,
                },
            }
        ]
    timeout_s = float(payload.get("timeout_s", 60.0))
    try:
        proc = subprocess.run(
            [bin_path, *extra_args, "-d", domain],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return [
            {
                "event_type": "tool-run-error",
                "payload": {"reason": f"{binary} timed out after {timeout_s}s"},
            }
        ]
    if proc.returncode != 0 and not proc.stdout.strip():
        return [
            {
                "event_type": "tool-run-error",
                "payload": {
                    "reason": f"{binary} exit {proc.returncode}",
                    "stderr": (proc.stderr or "")[:500],
                },
            }
        ]
    seen: set[str] = set()
    events: list[dict[str, Any]] = []
    for line in proc.stdout.splitlines():
        host = line.strip().lower()
        if not host or host in seen:
            continue
        seen.add(host)
        events.append(
            {
                "event_type": "listing-match",
                "payload": {
                    "source": source_label,
                    "domain": domain,
                    "subdomain": host,
                },
            }
        )
    events.append(
        {
            "event_type": "tool-run-result",
            "payload": {"domain": domain, "subdomains": len(events) - 0, "tool": binary},
        }
    )
    return events


def subfinder_subprocess(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Project Discovery subfinder. Install:
    https://github.com/projectdiscovery/subfinder/releases
    or via `go install github.com/projectdiscovery/subfinder/v2/cmd/subfinder@latest`.
    """
    return _binary_subprocess(
        "subfinder",
        (
            "Install from https://github.com/projectdiscovery/subfinder/releases "
            "or `go install github.com/projectdiscovery/subfinder/v2/cmd/subfinder@latest`."
        ),
        payload,
        source_label="subfinder",
        extra_args=["-silent"],
    )


def _subfinder_synthetic(payload: dict[str, Any]) -> list[dict[str, Any]]:
    domain = payload.get("domain") or "example.com"
    fixtures = ["mail", "vpn", "git", "ci", "docs"]
    events = [
        {
            "event_type": "listing-match",
            "payload": {
                "source": "subfinder",
                "domain": domain,
                "subdomain": f"{sub}.{domain}",
                "synthetic": True,
            },
        }
        for sub in fixtures
    ]
    events.append(
        {
            "event_type": "tool-run-result",
            "payload": {
                "domain": domain,
                "subdomains": len(fixtures),
                "tool": "subfinder",
                "synthetic": True,
            },
        }
    )
    return events


# ---------------------------------------------------------------------------
# 4. amass_subprocess -- OWASP binary
# ---------------------------------------------------------------------------


def amass_subprocess(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """OWASP Amass enum. Install:
    https://github.com/owasp-amass/amass/releases
    or via `go install -v github.com/owasp-amass/amass/v4/...@master`.

    Note: amass is heavier than subfinder + can run for minutes;
    timeout_s payload field caps wall time.
    """
    return _binary_subprocess(
        "amass",
        (
            "Install from https://github.com/owasp-amass/amass/releases "
            "or `go install -v github.com/owasp-amass/amass/v4/...@master`."
        ),
        payload,
        source_label="amass",
        extra_args=["enum", "-passive"],
    )


def _amass_synthetic(payload: dict[str, Any]) -> list[dict[str, Any]]:
    domain = payload.get("domain") or "example.com"
    fixtures = ["api-v2", "legacy", "prod", "internal"]
    events = [
        {
            "event_type": "listing-match",
            "payload": {
                "source": "amass",
                "domain": domain,
                "subdomain": f"{sub}.{domain}",
                "synthetic": True,
            },
        }
        for sub in fixtures
    ]
    events.append(
        {
            "event_type": "tool-run-result",
            "payload": {
                "domain": domain,
                "subdomains": len(fixtures),
                "tool": "amass",
                "synthetic": True,
            },
        }
    )
    return events


# ---------------------------------------------------------------------------
# Registry installation
# ---------------------------------------------------------------------------

_REGISTRY = get_registry()

_REGISTRY.register(
    "ct_log_lookup",
    ct_log_lookup,
    synthetic_mode=_ct_log_lookup_synthetic,
    in_process=True,
    description="Certificate Transparency log query via crt.sh (W5.do).",
)
_REGISTRY.register(
    "wayback_cdx_subdomains",
    wayback_cdx_subdomains,
    synthetic_mode=_wayback_cdx_subdomains_synthetic,
    in_process=True,
    description="Wayback CDX subdomain enumeration (W5.do, no API key).",
)
_REGISTRY.register(
    "subfinder_subprocess",
    subfinder_subprocess,
    synthetic_mode=_subfinder_synthetic,
    in_process=True,
    description="Project Discovery subfinder. Needs binary on PATH.",
)
_REGISTRY.register(
    "amass_subprocess",
    amass_subprocess,
    synthetic_mode=_amass_synthetic,
    in_process=True,
    description="OWASP Amass passive enum. Needs binary on PATH.",
)

# Keep `Path` referenced -- used implicitly via shutil.which only;
# avoid unused-import lint if someone later removes the helper.
_ = Path

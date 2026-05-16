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

import ipaddress
import os
import shutil
import socket
import subprocess
from pathlib import Path
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


def _client(timeout_s: float = 15.0) -> httpx.Client:
    return httpx.Client(
        timeout=timeout_s,
        headers={"User-Agent": _USER_AGENT, "Accept": "*/*"},
        follow_redirects=True,
    )


# ---------------------------------------------------------------------------
# 1. ct_log_lookup -- crt.sh
# ---------------------------------------------------------------------------


def _certspotter_fallback(domain: str, limit: int) -> list[dict[str, Any]] | None:
    """CertSpotter (sslmate) fallback when crt.sh is unreachable.

    Returns rows in crt.sh-equivalent shape:
      [{"name_value": "...", "issuer_name": "...",
        "not_before": "...", "not_after": "...", "id": "..."}]

    Returns None on any failure (caller should emit a skip).
    """
    try:
        with _client(timeout_s=20.0) as c:
            r = c.get(
                "https://api.certspotter.com/v1/issuances",
                params={
                    "domain": domain,
                    "include_subdomains": "true",
                    "expand": "dns_names,issuer",
                },
            )
        if r.status_code != 200:
            return None
        data = r.json() or []
    except (httpx.RequestError, ValueError):
        return None

    rows: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        dns_names = item.get("dns_names") or []
        if not isinstance(dns_names, list):
            continue
        issuer = item.get("issuer") or {}
        issuer_name = (issuer.get("name") if isinstance(issuer, dict) else "") or ""
        rows.append(
            {
                "name_value": "\n".join(str(n) for n in dns_names),
                "issuer_name": issuer_name,
                "not_before": item.get("not_before", ""),
                "not_after": item.get("not_after", ""),
                "id": item.get("id", ""),
                "_source": "certspotter",
            }
        )
        if len(rows) >= limit:
            break
    return rows


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
    # crt.sh is famously slow under load. Bump timeout to 45s and retry
    # once on a transient read timeout / 502 before failing.
    last_exc: Exception | None = None
    rows: list = []
    succeeded = False
    for attempt in (0, 1):
        try:
            with _client(timeout_s=45.0) as c:
                r = c.get(
                    "https://crt.sh/",
                    params={"q": f"%.{domain}", "output": "json"},
                )
            if r.status_code in (502, 503, 504) and attempt == 0:
                continue
            if r.status_code != 200:
                return [
                    {
                        "event_type": "tool-run-result",
                        "payload": {
                            "adapter_id": "ct_log_lookup",
                            "skipped": True,
                            "reason": f"crt.sh upstream HTTP {r.status_code}",
                            "domain": domain,
                            "suggest": "crt.sh is transiently unavailable; retry later",
                        },
                    }
                ]
            rows = r.json() or []
            succeeded = True
            break
        except httpx.ReadTimeout as exc:
            last_exc = exc
            if attempt == 0:
                continue
        except httpx.RequestError as exc:
            last_exc = exc
            break
        except ValueError as exc:
            last_exc = exc
            break
    if not succeeded:
        # crt.sh exhausted retries. Fall back to CertSpotter (sslmate) --
        # different infrastructure, same CT-log substrate. Free w/ rate
        # limits. Adapts the response into crt.sh-equivalent row shape so
        # the dedup loop below works unchanged.
        fallback_rows = _certspotter_fallback(domain, limit)
        if fallback_rows is None:
            return [
                {
                    "event_type": "tool-run-result",
                    "payload": {
                        "adapter_id": "ct_log_lookup",
                        "skipped": True,
                        "reason": (
                            f"crt.sh upstream unavailable: "
                            f"{type(last_exc).__name__}; CertSpotter fallback also failed"
                        ),
                        "domain": domain,
                        "suggest": "all CT-log mirrors transiently unavailable; retry later",
                    },
                }
            ]
        rows = fallback_rows
        # Don't set succeeded=True (variable unused after this point), but
        # fall through to the dedup loop with the fallback rows.
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
            row_source = row.get("_source") or "crt.sh"
            events.append(
                {
                    "event_type": "listing-match",  # reusing for domain hits; semantics =
                    # "found a related entity tied to this subject"
                    "payload": {
                        "source": row_source,
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
    # Wayback CDX is slow under heavy crawl load. Bump timeout to 45s
    # and retry once on read-timeout / 5xx before degrading to a result-
    # shaped skip (consistent with crt.sh handling above).
    last_exc: Exception | None = None
    rows: list = []
    succeeded = False
    for attempt in (0, 1):
        try:
            with _client(timeout_s=45.0) as c:
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
            if r.status_code in (502, 503, 504) and attempt == 0:
                continue
            if r.status_code != 200:
                return [
                    {
                        "event_type": "tool-run-result",
                        "payload": {
                            "adapter_id": "wayback_cdx_subdomains",
                            "skipped": True,
                            "reason": f"wayback CDX upstream HTTP {r.status_code}",
                            "domain": domain,
                            "suggest": "archive.org CDX is transiently unavailable; retry later",
                        },
                    }
                ]
            rows = r.json() or []
            succeeded = True
            break
        except httpx.ReadTimeout as exc:
            last_exc = exc
            if attempt == 0:
                continue
        except httpx.RequestError as exc:
            last_exc = exc
            break
        except ValueError as exc:
            last_exc = exc
            break
    if not succeeded:
        return [
            {
                "event_type": "tool-run-result",
                "payload": {
                    "adapter_id": "wayback_cdx_subdomains",
                    "skipped": True,
                    "reason": f"wayback upstream unavailable: {type(last_exc).__name__}",
                    "domain": domain,
                    "suggest": "archive.org CDX is transiently unavailable; retry later",
                },
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
        # Missing optional subprocess tool: result-shaped skip, not error.
        return [
            {
                "event_type": "tool-run-result",
                "payload": {
                    "adapter_id": f"{binary}_subprocess",
                    "skipped": True,
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
# 5. m365_autodiscover_probe -- passive M365 tenancy confirmation (Tomás P3)
# ---------------------------------------------------------------------------
#
# Resolves autodiscover.<domain> and checks if the answer lands in Microsoft
# Exchange Online IP space. This is definitive proof of M365 even when MX
# is wrapped by Mimecast / Proofpoint / Barracuda inbound filtering -- a
# case that defeats naive MX-based fingerprinting.
#
# Property-vetting value: a host who claims to operate a business at <addr>
# but whose business domain runs on M365 has a different scale signal than
# one running self-hosted mail. Tenancy fingerprints are also useful for
# distinguishing commercial-operators from genuine individuals (an
# individual rarely runs their own Microsoft 365 tenant).

# Representative MS Exchange Online IP ranges (truncated; covers the
# common landing CIDRs as of 2026-05). Full list:
# https://learn.microsoft.com/en-us/microsoft-365/enterprise/urls-and-ip-address-ranges
_MS_EXCHANGE_ONLINE_RANGES = [
    "40.96.0.0/13",
    "52.96.0.0/14",
    "13.107.6.152/31",
    "13.107.18.10/31",
    "13.107.128.0/22",
    "40.99.0.0/16",
    "40.104.0.0/15",
    "52.98.0.0/15",
    "104.47.0.0/17",
    "150.171.32.0/22",
]
_MS_HOSTNAME_SUFFIXES = (
    ".outlook.com",
    ".office.com",
    ".microsoft.com",
    ".office365.com",
    ".protection.outlook.com",
)


def _ip_in_microsoft(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    for cidr in _MS_EXCHANGE_ONLINE_RANGES:
        try:
            if ip in ipaddress.ip_network(cidr):
                return True
        except ValueError:
            continue
    return False


def m365_autodiscover_probe(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Confirm Microsoft 365 tenancy via autodiscover DNS landing.

    Payload:
      {"domain": "example.com"}

    Resolves `autodiscover.<domain>` and inspects (a) the CNAME chain for
    Microsoft-owned hostnames and (b) the A-record IPs for Exchange
    Online CIDRs. Passive (DNS-only); detectability: low.
    """
    domain = (payload.get("domain") or "").strip().lower().lstrip(".")
    if not domain:
        return [
            {
                "event_type": "tool-run-error",
                "payload": {"reason": "missing 'domain'"},
            }
        ]

    name = f"autodiscover.{domain}"
    try:
        canonical, aliases, ips = socket.gethostbyname_ex(name)
    except socket.gaierror as exc:
        # NXDOMAIN / no answer -- not a finding, just a no-match result.
        return [
            {
                "event_type": "tool-run-result",
                "payload": {
                    "adapter_id": "m365_autodiscover_probe",
                    "domain": domain,
                    "resolved": False,
                    "note": f"autodiscover.{domain} did not resolve: {exc.strerror or exc}",
                },
            }
        ]
    except OSError as exc:
        return [
            {
                "event_type": "tool-run-error",
                "payload": {
                    "reason": f"DNS lookup failed: {type(exc).__name__}: {exc}",
                    "domain": domain,
                },
            }
        ]

    chain_hosts = [canonical, *aliases]
    cname_hits_ms = any(
        any(h.lower().endswith(suf) for suf in _MS_HOSTNAME_SUFFIXES) for h in chain_hosts
    )
    ip_hits_ms = [ip for ip in ips if _ip_in_microsoft(ip)]
    confirmed = cname_hits_ms or bool(ip_hits_ms)

    events: list[dict[str, Any]] = []
    if confirmed:
        events.append(
            {
                "event_type": "tenant-match",
                "payload": {
                    "source": "autodiscover-dns",
                    "domain": domain,
                    "product": "Microsoft 365 / Exchange Online",
                    "evidence": {
                        "autodiscover_host": name,
                        "cname_chain": chain_hosts,
                        "a_records": ips,
                        "microsoft_ip_hits": ip_hits_ms,
                        "ms_hostname_match": cname_hits_ms,
                    },
                    "confidence": "confirmed",
                },
            }
        )
    events.append(
        {
            "event_type": "tool-run-result",
            "payload": {
                "adapter_id": "m365_autodiscover_probe",
                "domain": domain,
                "resolved": True,
                "m365_confirmed": confirmed,
                "tenants_found": 1 if confirmed else 0,
            },
        }
    )
    return events


def _m365_autodiscover_probe_synthetic(payload: dict[str, Any]) -> list[dict[str, Any]]:
    domain = payload.get("domain") or "example.com"
    return [
        {
            "event_type": "tenant-match",
            "payload": {
                "source": "autodiscover-dns",
                "domain": domain,
                "product": "Microsoft 365 / Exchange Online",
                "evidence": {
                    "autodiscover_host": f"autodiscover.{domain}",
                    "cname_chain": [f"autodiscover.{domain}", "autodiscover.outlook.com"],
                    "a_records": ["40.99.10.20"],
                    "synthetic": True,
                },
                "confidence": "confirmed",
                "synthetic": True,
            },
        },
        {
            "event_type": "tool-run-result",
            "payload": {
                "adapter_id": "m365_autodiscover_probe",
                "domain": domain,
                "m365_confirmed": True,
                "tenants_found": 1,
                "synthetic": True,
            },
        },
    ]


# ---------------------------------------------------------------------------
# 6. dns_prefix_sweep -- active prefix-probe subdomain enum (Tomás P4)
# ---------------------------------------------------------------------------
#
# crt.sh + passive sources miss 20-40% of high-value subdomains because
# (a) wildcard certs don't expose FQDNs, (b) HTTP-only hosts never get
# public certs, (c) new hosts haven't propagated to CT mirrors. Active
# prefix probing closes that gap and -- critically for us -- requires no
# external binary on PATH (replacing our flaky subfinder/amass deps).
#
# Ordered by empirical hit-rate from real engagements (per offensive-osint
# §16.24). Detectability: low (one DNS A query per host).

_PREFIX_SWEEP_DEFAULTS: tuple[str, ...] = (
    # Mail / collaboration
    "www",
    "mail",
    "webmail",
    "smtp",
    "imap",
    "pop",
    "owa",
    "autodiscover",
    "ftp",
    "sftp",
    # Remote access
    "vpn",
    "sslvpn",
    "gateway",
    "gp",
    "globalprotect",
    "citrix",
    "fortinet",
    "anyconnect",
    "remote",
    # Application surface
    "api",
    "app",
    "apps",
    "mobile",
    "m",
    # Identity / auth
    "portal",
    "login",
    "sso",
    "idp",
    "iam",
    "identity",
    "accounts",
    "oauth",
    "auth",
    "adfs",
    # Admin / management
    "admin",
    "manage",
    "console",
    "dashboard",
    "cp",
    "cpanel",
    # Business apps
    "intranet",
    "internal",
    "hr",
    "payroll",
    "finance",
    "sap",
    "erp",
    "crm",
    "helpdesk",
    "servicedesk",
    # Support / status
    "support",
    "help",
    "kb",
    "status",
    "monitoring",
    "grafana",
    "kibana",
    "prometheus",
    # Dev infrastructure
    "docs",
    "wiki",
    "confluence",
    "jira",
    "bitbucket",
    "gitlab",
    "jenkins",
    "sonar",
    "nexus",
    "git",
    "svn",
    "repo",
    "code",
    # Environment tiers
    "dev",
    "test",
    "staging",
    "stg",
    "qa",
    "uat",
    "sandbox",
    "preprod",
    "preview",
    "demo",
    # Recruiting / careers
    "careers",
    "jobs",
    "vacancies",
    "recruit",
    "eapps",
    # Commerce / billing
    "shop",
    "store",
    "ecommerce",
    "checkout",
    "payments",
    "pay",
    "billing",
    # Legacy / archival
    "old",
    "legacy",
    "archive",
    "backup",
    "beta",
    "v1",
    "v2",
    "classic",
    # Static assets
    "cdn",
    "static",
    "assets",
    "media",
    "img",
    "files",
    "downloads",
    "public",
    # DNS / mail infra
    "ns",
    "ns1",
    "ns2",
    "dns",
    "mx",
    "mx1",
    "mx2",
    # Voice / collab tools
    "zoom",
    "teams",
    "slack",
    "lync",
    "sip",
    "voice",
    "meet",
    # Vendor / procurement (property-vetting friendly)
    "sclepro",
    "tender",
    "tenders",
    "suppliers",
    "vendor",
    "vendors",
    "procurement",
    "purchase",
)


def dns_prefix_sweep(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Active prefix-probe subdomain enumeration via DNS A queries.

    Payload:
      {"domain": "example.com"}
      {"prefixes": ["api", "vpn", ...]}  # optional override
      {"limit": 500}                     # max hits to emit

    Pure DNS (stdlib socket.gethostbyname); no external binary required.
    Replaces the subfinder/amass subprocess adapters for the common case.
    """
    domain = (payload.get("domain") or "").strip().lower().lstrip(".")
    if not domain:
        return [
            {
                "event_type": "tool-run-error",
                "payload": {"reason": "missing 'domain'"},
            }
        ]
    prefixes_raw = payload.get("prefixes")
    if isinstance(prefixes_raw, list) and prefixes_raw:
        prefixes = tuple(str(p).strip().lower() for p in prefixes_raw if p)
    else:
        prefixes = _PREFIX_SWEEP_DEFAULTS
    limit = min(int(payload.get("limit", 200) or 200), 500)

    events: list[dict[str, Any]] = []
    hits = 0
    for prefix in prefixes:
        if not prefix:
            continue
        host = f"{prefix}.{domain}"
        try:
            _, _, ips = socket.gethostbyname_ex(host)
        except (socket.gaierror, OSError):
            continue
        if not ips:
            continue
        hits += 1
        events.append(
            {
                "event_type": "listing-match",
                "payload": {
                    "source": "dns-prefix-sweep",
                    "domain": domain,
                    "subdomain": host,
                    "ips": ips,
                    "prefix": prefix,
                },
            }
        )
        if hits >= limit:
            break

    events.append(
        {
            "event_type": "tool-run-result",
            "payload": {
                "adapter_id": "dns_prefix_sweep",
                "domain": domain,
                "subdomains_found": hits,
                "prefixes_tried": len(prefixes),
            },
        }
    )
    return events


def _dns_prefix_sweep_synthetic(payload: dict[str, Any]) -> list[dict[str, Any]]:
    domain = payload.get("domain") or "example.com"
    fixtures = [
        ("api", ["203.0.113.10"]),
        ("vpn", ["203.0.113.11"]),
        ("intranet", ["203.0.113.12"]),
        ("staging", ["203.0.113.13"]),
    ]
    events = [
        {
            "event_type": "listing-match",
            "payload": {
                "source": "dns-prefix-sweep",
                "domain": domain,
                "subdomain": f"{p}.{domain}",
                "ips": ips,
                "prefix": p,
                "synthetic": True,
            },
        }
        for p, ips in fixtures
    ]
    events.append(
        {
            "event_type": "tool-run-result",
            "payload": {
                "adapter_id": "dns_prefix_sweep",
                "domain": domain,
                "subdomains_found": len(fixtures),
                "synthetic": True,
            },
        }
    )
    return events


# ---------------------------------------------------------------------------
# 7. wayback_legacy_files -- decade-old-business pivot (Phase 4)
# ---------------------------------------------------------------------------
#
# Per offensive-osint §16.23: when a brochure-ware site returns empty for
# *.js (because the frontend was server-rendered), pivot to legacy
# extensions to enumerate the historical surface. .asp/.aspx/.cfm/.jsp/
# .php URLs surfaced in Wayback often reveal forgotten admin panels,
# legacy auth flows, and SQL-injection-prone parameters.
#
# Property-vetting value: a host claiming a "20-year family business"
# whose domain shows zero pre-2018 .asp/.cfm history is a fraud signal;
# a host claiming "new boutique" whose domain shows extensive 2005-era
# .asp activity is also a signal.


_LEGACY_EXTS: tuple[str, ...] = ("asp", "aspx", "cfm", "jsp", "php")


def wayback_legacy_files(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Wayback CDX pivot for legacy server-side file extensions.

    Payload:
      {"domain": "example.com"}
      {"limit": 200}              # max hits to emit

    Queries archive.org CDX with a regex filter for legacy script
    extensions. Each archived URL is emitted as a `listing-match` event;
    investigator can inspect any URL by clicking through.
    """
    domain = (payload.get("domain") or "").strip().lower().lstrip(".")
    if not domain:
        return [{"event_type": "tool-run-error", "payload": {"reason": "missing 'domain'"}}]
    limit = min(int(payload.get("limit", 200) or 200), 500)
    # Per-extension CDX queries with explicit URL-glob pattern. Five calls
    # in parallel-ish via sequential reuse, each handles its own retry +
    # 5xx degradation. Slower than one big regex-filtered query but
    # markedly more reliable (server-side regex filter is fussy under
    # httpx URL encoding + Wayback's flaky CDX backend).

    all_rows: list = []
    skipped_exts: list[tuple[str, str]] = []
    per_ext_caps = max(20, limit // len(_LEGACY_EXTS) + 10)

    for ext in _LEGACY_EXTS:
        last_exc: Exception | None = None
        ext_rows: list = []
        succeeded = False
        for attempt in (0, 1):
            try:
                with _client(timeout_s=30.0) as c:
                    r = c.get(
                        "https://web.archive.org/cdx/search/cdx",
                        params={
                            "url": f"{domain}/*.{ext}",
                            "output": "json",
                            "fl": "timestamp,original",
                            "filter": "statuscode:200",
                            "collapse": "urlkey",
                            "limit": str(per_ext_caps),
                        },
                    )
                if r.status_code in (502, 503, 504) and attempt == 0:
                    continue
                if r.status_code != 200:
                    break
                ext_rows = r.json() or []
                succeeded = True
                break
            except httpx.ReadTimeout as exc:
                last_exc = exc
                if attempt == 0:
                    continue
            except (httpx.RequestError, ValueError) as exc:
                last_exc = exc
                break
        if succeeded:
            # Skip CDX header row
            all_rows.extend(ext_rows[1:] if len(ext_rows) > 1 else [])
        else:
            skipped_exts.append((ext, type(last_exc).__name__ if last_exc else "unknown"))
        if len(all_rows) >= limit:
            break

    if not all_rows and skipped_exts and len(skipped_exts) == len(_LEGACY_EXTS):
        return [
            {
                "event_type": "tool-run-result",
                "payload": {
                    "adapter_id": "wayback_legacy_files",
                    "skipped": True,
                    "reason": (
                        f"wayback upstream unavailable for all {len(_LEGACY_EXTS)} "
                        f"legacy extensions: {dict(skipped_exts)}"
                    ),
                    "domain": domain,
                },
            }
        ]
    rows = all_rows
    if not succeeded:
        return [
            {
                "event_type": "tool-run-result",
                "payload": {
                    "adapter_id": "wayback_legacy_files",
                    "skipped": True,
                    "reason": f"wayback upstream unavailable: {type(last_exc).__name__}",
                    "domain": domain,
                },
            }
        ]

    seen: set[str] = set()
    events: list[dict[str, Any]] = []
    ext_tallies: dict[str, int] = {}
    for row in rows:  # headers already stripped per-extension above
        if not isinstance(row, list) or len(row) < 2:
            continue
        timestamp = str(row[0])
        url = str(row[1])
        if url in seen:
            continue
        seen.add(url)
        # Tally by extension for the summary.
        lower = url.lower()
        for ext in _LEGACY_EXTS:
            if f".{ext}" in lower:
                ext_tallies[ext] = ext_tallies.get(ext, 0) + 1
                break
        events.append(
            {
                "event_type": "listing-match",
                "payload": {
                    "source": "wayback-legacy",
                    "domain": domain,
                    "url": url,
                    "archived_at": timestamp,
                    "archive_url": (f"https://web.archive.org/web/{timestamp}/{url}"),
                },
            }
        )
        if len(events) >= limit:
            break

    events.append(
        {
            "event_type": "tool-run-result",
            "payload": {
                "adapter_id": "wayback_legacy_files",
                "domain": domain,
                "legacy_urls_found": len(events) - 0,
                "by_extension": ext_tallies,
            },
        }
    )
    return events


def _wayback_legacy_files_synthetic(payload: dict[str, Any]) -> list[dict[str, Any]]:
    domain = payload.get("domain") or "example.com"
    fixtures = [
        ("20050314120000", f"http://{domain}/admin/login.asp"),
        ("20071109094500", f"http://{domain}/forms/contact.php"),
        ("20120218144500", f"http://{domain}/default.aspx"),
    ]
    events = [
        {
            "event_type": "listing-match",
            "payload": {
                "source": "wayback-legacy",
                "domain": domain,
                "url": url,
                "archived_at": ts,
                "archive_url": f"https://web.archive.org/web/{ts}/{url}",
                "synthetic": True,
            },
        }
        for ts, url in fixtures
    ]
    events.append(
        {
            "event_type": "tool-run-result",
            "payload": {
                "adapter_id": "wayback_legacy_files",
                "domain": domain,
                "legacy_urls_found": len(fixtures),
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
_REGISTRY.register(
    "m365_autodiscover_probe",
    m365_autodiscover_probe,
    synthetic_mode=_m365_autodiscover_probe_synthetic,
    in_process=True,
    description=(
        "Passive M365/Exchange Online tenancy confirmation via autodiscover DNS landing (W5.do)."
    ),
)
_REGISTRY.register(
    "dns_prefix_sweep",
    dns_prefix_sweep,
    synthetic_mode=_dns_prefix_sweep_synthetic,
    in_process=True,
    description=(
        "Active prefix-probe subdomain enumeration via DNS A queries; "
        "no external binary required (W5.do)."
    ),
)
_REGISTRY.register(
    "wayback_legacy_files",
    wayback_legacy_files,
    synthetic_mode=_wayback_legacy_files_synthetic,
    in_process=True,
    description=(
        "Wayback CDX pivot for legacy server-side files "
        "(.asp/.aspx/.cfm/.jsp/.php) -- decade-old-business signal (W5.do)."
    ),
)

# Keep `Path` referenced -- used implicitly via shutil.which only;
# avoid unused-import lint if someone later removes the helper.
_ = Path

"""Identity-fabric / domain-posture adapters (W12.id workflow).

Tomas + Margaret Phase 2 rollout, 2026-05-12. Derived from /osint skills:
  - offensive-osint §16.14 (email security audit)
  - offensive-osint §16.21 (WHOIS / RDAP)
  - offensive-osint §16.22 (TXT verification token catalog -> SaaS tenants)
  - offensive-osint §22.1-22.5 (identity-fabric endpoints)
  - osint-methodology §11 (identity fabric mapping)

Four adapters:
  - domain_rdap                Structured WHOIS via rdap.org redirect (P6)
  - dns_txt_saas_inference     30+ token catalog -> SaaS tenancy (P2)
  - dns_email_security_audit   SPF/DMARC/MX -> tenancy + spoof feasibility (P1)
  - sso_discovery              8 prefix probes + OIDC discovery (P5)

Property-vetting value: "does the host's claimed digital identity stand
up?" -- combined output reveals scale (Workday/Salesforce = enterprise),
hygiene (DMARC posture), and ownership (RDAP registrant) signals that
distinguish genuine private individuals from commercial operators
pretending to be casual hosts.

Naomi gate (privacy, 2026-05-12): any tenant ID is INFO ceiling by
default. Only escalates when intersected with breach corpus per the
SSO_EXPOSURE pattern in lib/severity-rubric.ts.

DNS transport note: Python stdlib `socket` cannot do TXT/MX records. We
use Cloudflare DoH (`cloudflare-dns.com/dns-query`) via httpx. This DOES
leak the queried domain to Cloudflare for these specific record types --
documented tradeoff vs adding dnspython as a dep. The "DoH default"
decision (opt-in env var, 2026-05-12) governed A-record queries; TXT/MX
have no native option here.
"""

from __future__ import annotations

import json as _json
import os
import random
import socket
import ssl
import time
from datetime import UTC, datetime
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
# W4-SCRAPLING-MIG (Margaret wave-4 roadmap §4): target-webserver-facing
# adapters route through Scrapling so the TLS fingerprint pairs coherently
# with the UA we present. Plain httpx leaks "Python-httpx" JA3/JA4 which
# Cloudflare's ML tier flags as inconsistent against the Chrome UA we ship
# from W4-UA -- Camille's paired-fix (Vastel FP-Inconsistent).
#
# Scope: ONLY the OIDC discovery probes in sso_discovery. The DoH calls to
# Cloudflare and the rdap.org GET stay on plain httpx (stealth there is
# theatre per Camille -- those endpoints expect API clients).
# tls_cert_audit stays on stdlib ssl: the Scrapling facade exposes parsed
# HTTP responses only, no TLS-handshake-only surface.
# TODO(W4-SCRAPLING-MIG-tls): facade lacks TLS-handshake-only surface;
# revisit when Scrapling exposes JA3/JA4 randomization on raw sockets.
#
# UA discipline on the Scrapling path: let Scrapling's stealthy_headers
# randomize the UA so it lines up with the randomized TLS. Operators who
# pin OSINT_USER_AGENT or OSINT_TRANSPARENT_UA=1 still win (their explicit
# intent overrides the random pick).
# ---------------------------------------------------------------------------


def _scrapling_headers_override() -> dict[str, str] | None:
    """Return headers to pass to the Scrapling facade, or None to let
    Scrapling pick a randomized stealthy UA.

    - OSINT_USER_AGENT set -> respect operator pin.
    - OSINT_TRANSPARENT_UA=1 -> use the transparent literal.
    - Otherwise -> None (Scrapling picks; UA + TLS stay paired).
    """
    pinned = os.environ.get("OSINT_USER_AGENT")
    if pinned:
        return {"User-Agent": pinned, "Accept": "*/*"}
    if os.environ.get("OSINT_TRANSPARENT_UA") == "1":
        return {"User-Agent": _USER_AGENT, "Accept": "*/*"}
    return None


# ---------------------------------------------------------------------------
# DoH helpers (Cloudflare 1.1.1.1)
# ---------------------------------------------------------------------------

_DOH_ENDPOINT = "https://cloudflare-dns.com/dns-query"
_DOH_HEADERS = {"Accept": "application/dns-json"}


def _doh_query(name: str, rtype: str, timeout_s: float = 10.0) -> list[str]:
    """Query Cloudflare DoH for a single record type. Returns a flat list
    of record data strings (whatever the `data` field contains per record).
    Returns [] on any failure -- callers treat as no-records."""
    try:
        with httpx.Client(timeout=timeout_s, headers=_DOH_HEADERS) as c:
            r = c.get(_DOH_ENDPOINT, params={"name": name, "type": rtype})
        if r.status_code != 200:
            return []
        body = r.json() or {}
    except (httpx.RequestError, ValueError):
        return []
    if body.get("Status") != 0:
        return []
    answers = body.get("Answer") or []
    out: list[str] = []
    for a in answers:
        if not isinstance(a, dict):
            continue
        data = a.get("data")
        if isinstance(data, str):
            # TXT records come back with surrounding quotes; strip pair-wise.
            stripped = data.strip()
            if stripped.startswith('"') and stripped.endswith('"'):
                stripped = stripped[1:-1].replace('" "', "")
            out.append(stripped)
    return out


# ===========================================================================
# P6 -- domain_rdap
# ===========================================================================
#
# RFC 7480 RDAP via rdap.org (which redirects to the authoritative registry
# RDAP server). Returns structured JSON with registrant, registrar, status
# flags, name servers, registration / expiration / last-changed events.
# Smallest blast radius of the Phase 2 adapters: single JSON GET.


def domain_rdap(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Structured WHOIS via RDAP.

    Payload:
      {"domain": "example.com"}

    Emits one `infra-fact` event per significant fact (registrar,
    registrant org if present, key events, status flags, NS) and one
    `tool-run-result` summary. Detectability: low.
    """
    domain = (payload.get("domain") or "").strip().lower().lstrip(".")
    if not domain:
        return [{"event_type": "tool-run-error", "payload": {"reason": "missing 'domain'"}}]

    try:
        with _client(timeout_s=20.0) as c:
            r = c.get(f"https://rdap.org/domain/{domain}")
    except httpx.RequestError as exc:
        return [
            {
                "event_type": "tool-run-result",
                "payload": {
                    "adapter_id": "domain_rdap",
                    "skipped": True,
                    "reason": f"rdap unreachable: {type(exc).__name__}",
                    "domain": domain,
                },
            }
        ]
    if r.status_code == 404:
        return [
            {
                "event_type": "tool-run-result",
                "payload": {
                    "adapter_id": "domain_rdap",
                    "domain": domain,
                    "registered": False,
                    "note": "RDAP returned 404 -- domain unregistered or registry not RDAP-enabled",
                },
            }
        ]
    if r.status_code != 200:
        return [
            {
                "event_type": "tool-run-result",
                "payload": {
                    "adapter_id": "domain_rdap",
                    "skipped": True,
                    "reason": f"rdap upstream HTTP {r.status_code}",
                    "domain": domain,
                },
            }
        ]
    try:
        body = r.json()
    except ValueError:
        return [
            {
                "event_type": "tool-run-result",
                "payload": {
                    "adapter_id": "domain_rdap",
                    "skipped": True,
                    "reason": "rdap non-JSON response",
                    "domain": domain,
                },
            }
        ]

    events: list[dict[str, Any]] = []
    facts_emitted = 0

    # Status flags (clientTransferProhibited, clientHold, etc.)
    statuses = body.get("status") or []
    if isinstance(statuses, list) and statuses:
        events.append(
            {
                "event_type": "infra-fact",
                "payload": {
                    "source": "rdap",
                    "domain": domain,
                    "fact_type": "status",
                    "value": list(statuses),
                },
            }
        )
        facts_emitted += 1

    # Key events (registration, expiration, last-changed)
    for ev in body.get("events") or []:
        if not isinstance(ev, dict):
            continue
        action = ev.get("eventAction")
        date = ev.get("eventDate")
        if action and date:
            events.append(
                {
                    "event_type": "infra-fact",
                    "payload": {
                        "source": "rdap",
                        "domain": domain,
                        "fact_type": "event",
                        "action": action,
                        "date": date,
                    },
                }
            )
            facts_emitted += 1

    # Nameservers
    nameservers = []
    for ns in body.get("nameservers") or []:
        if isinstance(ns, dict):
            name = ns.get("ldhName") or ns.get("unicodeName")
            if name:
                nameservers.append(str(name).lower())
    if nameservers:
        events.append(
            {
                "event_type": "infra-fact",
                "payload": {
                    "source": "rdap",
                    "domain": domain,
                    "fact_type": "nameservers",
                    "value": nameservers,
                },
            }
        )
        facts_emitted += 1

    # Entities: registrar, registrant, admin, tech, abuse
    for ent in body.get("entities") or []:
        if not isinstance(ent, dict):
            continue
        roles = ent.get("roles") or []
        if not roles:
            continue
        handle = ent.get("handle", "")
        # vcardArray decoding (RFC 7095). Best-effort; entities often
        # redact under GDPR but the role + handle survives.
        vcard = ent.get("vcardArray")
        org_name = None
        if isinstance(vcard, list) and len(vcard) >= 2 and isinstance(vcard[1], list):
            for field in vcard[1]:
                if isinstance(field, list) and len(field) >= 4:
                    key = field[0]
                    val = field[3]
                    if (key == "fn" and isinstance(val, str)) or (
                        key == "org" and isinstance(val, str)
                    ):
                        org_name = val
        events.append(
            {
                "event_type": "infra-fact",
                "payload": {
                    "source": "rdap",
                    "domain": domain,
                    "fact_type": "entity",
                    "roles": list(roles),
                    "handle": handle,
                    "org_name": org_name,
                },
            }
        )
        facts_emitted += 1

    events.append(
        {
            "event_type": "tool-run-result",
            "payload": {
                "adapter_id": "domain_rdap",
                "domain": domain,
                "registered": True,
                "facts_emitted": facts_emitted,
            },
        }
    )
    return events


def _domain_rdap_synthetic(payload: dict[str, Any]) -> list[dict[str, Any]]:
    domain = payload.get("domain") or "example.com"
    return [
        {
            "event_type": "infra-fact",
            "payload": {
                "source": "rdap",
                "domain": domain,
                "fact_type": "event",
                "action": "registration",
                "date": "2010-01-15T00:00:00Z",
                "synthetic": True,
            },
        },
        {
            "event_type": "infra-fact",
            "payload": {
                "source": "rdap",
                "domain": domain,
                "fact_type": "entity",
                "roles": ["registrant"],
                "handle": "REDACTED",
                "org_name": "Synthetic Org Ltd",
                "synthetic": True,
            },
        },
        {
            "event_type": "tool-run-result",
            "payload": {
                "adapter_id": "domain_rdap",
                "domain": domain,
                "registered": True,
                "facts_emitted": 2,
                "synthetic": True,
            },
        },
    ]


# ===========================================================================
# P2 -- dns_txt_saas_inference
# ===========================================================================
#
# 30+ verification-token catalog per offensive §16.22. Each matched token
# reveals a SaaS tenancy.

# Catalog tuned for property-vetting -- includes commerce signals
# (Shopify/Stripe), enterprise scale signals (Workday/Salesforce),
# and regional presence signals (Mail.ru/Yandex).
_SAAS_TOKEN_CATALOG: tuple[tuple[str, str], ...] = (
    ("google-site-verification=", "Google Workspace / Search Console"),
    ("MS=ms", "Microsoft 365 (legacy verification)"),
    ("mscid=", "Microsoft 365"),
    ("apple-domain-verification=", "Apple Business Manager"),
    ("atlassian-domain-verification=", "Atlassian Cloud (Jira/Confluence)"),
    ("facebook-domain-verification=", "Facebook Business"),
    ("adobe-idp-site-verification=", "Adobe (Sign / Creative Cloud)"),
    ("adobe-sign-verification=", "Adobe Sign"),
    ("docusign=", "DocuSign"),
    ("dropbox-domain-verification=", "Dropbox Business"),
    ("box-verification=", "Box"),
    ("webexdomainverification.", "Cisco Webex"),
    ("zoom_verify_", "Zoom"),
    ("slack-domain-verification=", "Slack Enterprise"),
    ("asana-domain-verification=", "Asana Enterprise"),
    ("mongodb-site-verification=", "MongoDB Atlas"),
    ("pinterest-site-verification=", "Pinterest Business"),
    ("mailru-verification:", "Mail.ru (RU presence)"),
    ("yandex-verification:", "Yandex (RU presence)"),
    ("zscaler-verification-", "Zscaler (Zero Trust / SSE)"),
    ("cloudflare-verify=", "Cloudflare (Zero Trust / Access)"),
    ("_amazonses=", "AWS SES (mail sender)"),
    ("amazonses:", "AWS SES (mail sender)"),
    ("salesforce-domain-verification=", "Salesforce"),
    ("workday-domain-verification=", "Workday (HR + Finance)"),
    ("shopify-domain-verification=", "Shopify (commerce)"),
    ("stripe-verification=", "Stripe (payments)"),
    ("klaviyo-domain-verification=", "Klaviyo (marketing email)"),
    ("mailchimp-domain-verification=", "Mailchimp (marketing email)"),
    ("hubspot-domain-verification=", "HubSpot (CRM / marketing)"),
    ("zendesk-verification=", "Zendesk (support)"),
    ("freshworks-verification=", "Freshworks (support / CRM)"),
    ("intercom-verification=", "Intercom (messaging)"),
    ("loom-site-verification=", "Loom"),
    ("miro-site-verification=", "Miro"),
    ("gitlab-domain-verification=", "GitLab (self-hosted or cloud)"),
    ("notion=", "Notion (enterprise)"),
)


def dns_txt_saas_inference(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Infer SaaS tenancies from TXT verification tokens.

    Payload:
      {"domain": "example.com"}

    Pulls all TXT records via DoH and matches each against the catalog.
    Emits one `tenant-match` event per discovered tenancy + one summary.
    Naomi ceiling: each tenant-match defaults to INFO confidence.
    """
    domain = (payload.get("domain") or "").strip().lower().lstrip(".")
    if not domain:
        return [{"event_type": "tool-run-error", "payload": {"reason": "missing 'domain'"}}]

    txts = _doh_query(domain, "TXT")
    events: list[dict[str, Any]] = []
    matched: set[str] = set()  # dedup by product name
    for txt in txts:
        for prefix, product in _SAAS_TOKEN_CATALOG:
            if prefix.lower() in txt.lower() and product not in matched:
                matched.add(product)
                events.append(
                    {
                        "event_type": "tenant-match",
                        "payload": {
                            "source": "dns-txt-inference",
                            "domain": domain,
                            "product": product,
                            "evidence": {
                                "token_prefix": prefix,
                                "txt_record_preview": txt[:120],
                            },
                            "confidence": "info",  # Naomi ceiling
                        },
                    }
                )
                break  # one match per TXT line is enough

    events.append(
        {
            "event_type": "tool-run-result",
            "payload": {
                "adapter_id": "dns_txt_saas_inference",
                "domain": domain,
                "txt_records_inspected": len(txts),
                "tenants_found": len(matched),
                "products": sorted(matched),
            },
        }
    )
    return events


def _dns_txt_saas_inference_synthetic(payload: dict[str, Any]) -> list[dict[str, Any]]:
    domain = payload.get("domain") or "example.com"
    fixtures = [
        ("Google Workspace / Search Console", "google-site-verification="),
        ("Atlassian Cloud (Jira/Confluence)", "atlassian-domain-verification="),
        ("Stripe (payments)", "stripe-verification="),
    ]
    events = [
        {
            "event_type": "tenant-match",
            "payload": {
                "source": "dns-txt-inference",
                "domain": domain,
                "product": product,
                "evidence": {"token_prefix": prefix, "synthetic": True},
                "confidence": "info",
                "synthetic": True,
            },
        }
        for product, prefix in fixtures
    ]
    events.append(
        {
            "event_type": "tool-run-result",
            "payload": {
                "adapter_id": "dns_txt_saas_inference",
                "domain": domain,
                "tenants_found": len(fixtures),
                "products": [p for p, _ in fixtures],
                "synthetic": True,
            },
        }
    )
    return events


# ===========================================================================
# P1 -- dns_email_security_audit
# ===========================================================================
#
# Parse SPF + DMARC + MX. Infer SaaS via SPF includes. Compute spoof
# feasibility severity per offensive §16.14.

_SPF_INCLUDE_CATALOG: tuple[tuple[str, str], ...] = (
    ("_spf.google.com", "Google Workspace (mail)"),
    ("spf.protection.outlook.com", "Microsoft 365 (mail)"),
    ("mail.eo.outlook.com", "Microsoft 365 (legacy mail)"),
    ("_spf.salesforce.com", "Salesforce (mail)"),
    ("mail.zendesk.com", "Zendesk (support mail)"),
    ("sendgrid.net", "SendGrid (transactional mail)"),
    ("mailgun.org", "Mailgun (transactional mail)"),
    ("_spf.atlassian.net", "Atlassian Cloud (mail)"),
    ("amazonses.com", "AWS SES (mail)"),
    ("mktomail.com", "Marketo (marketing mail)"),
    ("_spf.intuit.com", "Intuit (QuickBooks / Mailchimp)"),
    ("spf.mandrillapp.com", "Mandrill (transactional mail)"),
    ("_spf.workday.com", "Workday (HR / Finance)"),
    ("klaviyo.com", "Klaviyo (marketing mail)"),
    ("shops.shopify.com", "Shopify (commerce mail)"),
    ("spf.constantcontact.com", "Constant Contact (marketing)"),
    ("_spf.mailchimp.com", "Mailchimp (marketing)"),
    ("spf.mtasv.net", "Postmark (transactional)"),
    ("spf.smtp2go.com", "SMTP2Go"),
    ("zoho.com", "Zoho (mail)"),
)

_MX_PRODUCT_PATTERNS: tuple[tuple[str, str], ...] = (
    (".mail.protection.outlook.com", "Microsoft 365 (Exchange Online)"),
    (".outlook.com", "Microsoft 365"),
    ("aspmx.l.google.com", "Google Workspace"),
    ("googlemail.com", "Google Workspace"),
    (".zoho.com", "Zoho Mail"),
    (".yandex.net", "Yandex 360"),
    (".fastmail.com", "Fastmail"),
    (".pphosted.com", "Proofpoint (filtering)"),
    (".proofpoint.com", "Proofpoint (filtering)"),
    (".mimecast.com", "Mimecast (filtering)"),
    (".mimecast-eu.com", "Mimecast EU (filtering)"),
    (".barracudanetworks.com", "Barracuda (filtering)"),
)


def _parse_spf_includes(spf_record: str) -> list[str]:
    """Extract include: domains from a single SPF v=spf1 record."""
    includes: list[str] = []
    for token in spf_record.split():
        if token.lower().startswith("include:"):
            includes.append(token.split(":", 1)[1].strip().lower())
    return includes


def _spf_qualifier(spf_record: str) -> str:
    """Return the final 'all' qualifier: -all / ~all / ?all / +all or 'none'."""
    parts = spf_record.split()
    for p in reversed(parts):
        pl = p.lower()
        if pl.endswith("all"):
            return pl
    return "none"


def _parse_dmarc(dmarc_record: str) -> dict[str, str]:
    """Parse DMARC TXT into a dict of tag=value pairs."""
    out: dict[str, str] = {}
    for token in dmarc_record.split(";"):
        token = token.strip()
        if "=" in token:
            k, v = token.split("=", 1)
            out[k.strip().lower()] = v.strip()
    return out


def _spoof_feasibility_tier(spf_q: str, dmarc_p: str, dmarc_pct: str) -> str:
    """Return one of: 'low', 'medium', 'high'.

    Mapping per offensive-osint §16.14 + §29.3:
      p=none / no DMARC                          -> high  (spoof feasible)
      p=quarantine + pct<100                     -> medium
      p=quarantine + pct=100 or p=reject relaxed -> medium
      p=reject + strict alignment OR SPF -all    -> low
    """
    if not dmarc_p or dmarc_p == "none":
        return "high"
    if dmarc_p == "quarantine":
        return "medium"
    if dmarc_p == "reject":
        return "low"
    # Unknown policy value -- conservative medium.
    return "medium"


def dns_email_security_audit(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """SPF + DMARC + MX posture audit with SaaS inference.

    Payload:
      {"domain": "example.com"}

    Emits `tenant-match` events for SaaS detected via SPF includes / MX
    hosts, one `email-posture` event with the full audit, and a summary.
    """
    domain = (payload.get("domain") or "").strip().lower().lstrip(".")
    if not domain:
        return [{"event_type": "tool-run-error", "payload": {"reason": "missing 'domain'"}}]

    spf_records = [t for t in _doh_query(domain, "TXT") if "v=spf1" in t.lower()]
    dmarc_records = _doh_query(f"_dmarc.{domain}", "TXT")
    mx_records_raw = _doh_query(domain, "MX")
    # MX data field shape: "10 mail.example.com." -- pull just the host.
    mx_hosts: list[str] = []
    for mx in mx_records_raw:
        parts = mx.split()
        if len(parts) >= 2:
            host = parts[-1].rstrip(".").lower()
            mx_hosts.append(host)
        elif len(parts) == 1:
            mx_hosts.append(parts[0].rstrip(".").lower())

    spf_record = spf_records[0] if spf_records else ""
    spf_qual = _spf_qualifier(spf_record) if spf_record else "absent"
    spf_includes = _parse_spf_includes(spf_record) if spf_record else []

    dmarc_record = next((d for d in dmarc_records if "v=DMARC1" in d), "")
    dmarc_tags = _parse_dmarc(dmarc_record) if dmarc_record else {}
    dmarc_p = dmarc_tags.get("p", "none" if not dmarc_record else "")
    dmarc_sp = dmarc_tags.get("sp", dmarc_p)
    dmarc_pct = dmarc_tags.get("pct", "100")
    dmarc_rua = dmarc_tags.get("rua", "")

    spoof_tier = _spoof_feasibility_tier(spf_qual, dmarc_p, dmarc_pct)

    events: list[dict[str, Any]] = []
    matched_tenants: set[str] = set()

    # SaaS inference from SPF includes
    for inc in spf_includes:
        for pattern, product in _SPF_INCLUDE_CATALOG:
            if pattern in inc and product not in matched_tenants:
                matched_tenants.add(product)
                events.append(
                    {
                        "event_type": "tenant-match",
                        "payload": {
                            "source": "spf-include",
                            "domain": domain,
                            "product": product,
                            "evidence": {"spf_include": inc},
                            "confidence": "info",
                        },
                    }
                )
                break

    # SaaS inference from MX hosts
    for mx_host in mx_hosts:
        for pattern, product in _MX_PRODUCT_PATTERNS:
            if pattern in mx_host and product not in matched_tenants:
                matched_tenants.add(product)
                events.append(
                    {
                        "event_type": "tenant-match",
                        "payload": {
                            "source": "mx-host",
                            "domain": domain,
                            "product": product,
                            "evidence": {"mx_host": mx_host},
                            "confidence": "info",
                        },
                    }
                )
                break

    events.append(
        {
            "event_type": "email-posture",
            "payload": {
                "source": "email-security-audit",
                "domain": domain,
                "spf": {
                    "present": bool(spf_record),
                    "qualifier": spf_qual,
                    "includes": spf_includes,
                },
                "dmarc": {
                    "present": bool(dmarc_record),
                    "policy": dmarc_p or None,
                    "subpolicy": dmarc_sp or None,
                    "pct": dmarc_pct,
                    "rua": dmarc_rua or None,
                },
                "mx": {
                    "hosts": mx_hosts,
                },
                "spoof_feasibility": spoof_tier,
            },
        }
    )
    events.append(
        {
            "event_type": "tool-run-result",
            "payload": {
                "adapter_id": "dns_email_security_audit",
                "domain": domain,
                "spoof_feasibility": spoof_tier,
                "tenants_found": len(matched_tenants),
            },
        }
    )
    return events


def _dns_email_security_audit_synthetic(payload: dict[str, Any]) -> list[dict[str, Any]]:
    domain = payload.get("domain") or "example.com"
    return [
        {
            "event_type": "tenant-match",
            "payload": {
                "source": "spf-include",
                "domain": domain,
                "product": "Google Workspace (mail)",
                "evidence": {"spf_include": "_spf.google.com"},
                "confidence": "info",
                "synthetic": True,
            },
        },
        {
            "event_type": "email-posture",
            "payload": {
                "source": "email-security-audit",
                "domain": domain,
                "spf": {"present": True, "qualifier": "~all", "includes": ["_spf.google.com"]},
                "dmarc": {"present": True, "policy": "quarantine", "pct": "100"},
                "mx": {"hosts": ["aspmx.l.google.com"]},
                "spoof_feasibility": "medium",
                "synthetic": True,
            },
        },
        {
            "event_type": "tool-run-result",
            "payload": {
                "adapter_id": "dns_email_security_audit",
                "domain": domain,
                "spoof_feasibility": "medium",
                "tenants_found": 1,
                "synthetic": True,
            },
        },
    ]


# ===========================================================================
# P5 -- sso_discovery
# ===========================================================================
#
# Probe the 8 SSO subdomain prefixes per offensive §16.7. For each
# resolving host, GET /.well-known/openid-configuration and parse the
# `issuer` URL to identify the IdP product per methodology §11.6.

_SSO_PREFIXES: tuple[str, ...] = (
    "auth",
    "login",
    "sso",
    "idp",
    "iam",
    "identity",
    "accounts",
    "oauth",
)

# W4-SSO-HARDEN (Margaret wave-4 roadmap §4): the 8-probes-in-3-seconds
# pattern matches Cloudflare bot heuristics. SystemRandom for
# crypto-quality jitter -- weakens the regularity heuristic without
# changing functionality. Tests flip _SSO_DISCOVERY_DETERMINISTIC_ORDER
# to True for reproducibility (wave-3 C-5 pattern).
_RNG = random.SystemRandom()
_SSO_DISCOVERY_DETERMINISTIC_ORDER = False
_SSO_JITTER_MIN_S = 0.1
_SSO_JITTER_MAX_S = 0.5

# Env-gate (wave-3 C-5 / C-6-SSO registry-default-off pattern). sso_discovery
# fires unauthenticated probes against 8+ identity-fabric subdomains per
# target -- the AdapterRegistry has no `requires_env` field today, so the
# guard lives inline at the top of the adapter. Operator opts in with
# OSINT_ENABLE_SSO_DISCOVERY=1.
_SSO_ENABLE_ENV = "OSINT_ENABLE_SSO_DISCOVERY"

# Product fingerprints per methodology §11.6 + offensive §22.5.
_IDP_PATTERNS: tuple[tuple[str, str], ...] = (
    ("login.microsoftonline.com", "Microsoft Entra (Azure AD)"),
    ("accounts.google.com", "Google Workspace"),
    (".auth0.com", "Auth0"),
    (".okta.com", "Okta"),
    (".oktapreview.com", "Okta (preview)"),
    (".onelogin.com", "OneLogin"),
    (".pingone.com", "Ping Identity"),
    (".pingidentity.com", "Ping Identity"),
    (".duosecurity.com", "Duo"),
    ("/realms/", "Keycloak"),
    ("/adfs/", "ADFS"),
    (".cloudflareaccess.com", "Cloudflare Access"),
    (".workspaceone.com", "VMware Workspace ONE"),
    ("idp.amazonworkspaces.com", "AWS Workspaces"),
    (".jumpcloud.com", "JumpCloud"),
)


def _fingerprint_idp(issuer_url: str) -> str | None:
    if not issuer_url:
        return None
    iu = issuer_url.lower()
    for pattern, product in _IDP_PATTERNS:
        if pattern in iu:
            return product
    return None


def _try_oidc_discovery(host: str) -> dict[str, Any] | None:
    """GET /.well-known/openid-configuration on `host`. Returns parsed
    JSON dict on success, None on any failure (404, non-JSON, etc).

    W4-SCRAPLING-MIG: routes through the Scrapling fetcher facade so the
    TLS fingerprint pairs coherently with the UA the target webserver
    sees (otherwise Cloudflare's ML tier flags UA<->TLS inconsistency).
    The facade swallows transport exceptions and returns a FetchResult
    with `error` populated, so we just check `result.ok`.
    """
    # Late import: keeps the optional fetcher dependency off the module's
    # import path until the SSO discovery code actually runs.
    from osint_goblin_fetcher import fetch as _scrapling_fetch

    headers = _scrapling_headers_override()
    for scheme in ("https", "http"):
        result = _scrapling_fetch(
            f"{scheme}://{host}/.well-known/openid-configuration",
            tier="fetcher",
            timeout_s=8.0,
            headers=headers,
        )
        if not result.ok or result.status != 200:
            continue
        try:
            body = _json.loads(result.body_text) if result.body_text else None
        except (ValueError, _json.JSONDecodeError):
            continue
        if isinstance(body, dict) and ("issuer" in body or "authorization_endpoint" in body):
            return body
    return None


def sso_discovery(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Discover SSO / IdP tenants for a domain.

    Payload:
      {"domain": "example.com"}
      {"extra_hosts": ["sub1.example.com", ...]}  # optional, e.g. from W5.do

    Probes the 8 SSO subdomain prefixes + any extra hosts. For each that
    resolves AND serves OIDC metadata, emits an `sso-discovery` event with
    the IdP product fingerprint.

    Gated on OSINT_ENABLE_SSO_DISCOVERY=1 (W4-SSO-HARDEN, wave-3 C-6-SSO
    registry-default-off pattern). Unauthenticated probes against 8+
    identity-fabric subdomains per target are off by default; operator
    opts in. Skipped-by-policy is a distinct signal from missing-input.
    """
    # W4-SSO-HARDEN: env-gate at the very top -- precedes input validation
    # so skip-by-policy is a different signal than missing-domain error.
    if os.environ.get(_SSO_ENABLE_ENV) != "1":
        return [
            {
                "event_type": "tool-run-result",
                "payload": {
                    "adapter_id": "sso_discovery",
                    "skipped": True,
                    "reason": (
                        f"sso_discovery disabled by default; set {_SSO_ENABLE_ENV}=1 to opt in"
                    ),
                },
            }
        ]

    domain = (payload.get("domain") or "").strip().lower().lstrip(".")
    if not domain:
        return [{"event_type": "tool-run-error", "payload": {"reason": "missing 'domain'"}}]

    extra_hosts_raw = payload.get("extra_hosts")
    extra_hosts: list[str] = []
    if isinstance(extra_hosts_raw, list):
        extra_hosts = [str(h).strip().lower() for h in extra_hosts_raw if h]

    probe_hosts = [f"{p}.{domain}" for p in _SSO_PREFIXES] + extra_hosts

    # W4-SSO-HARDEN: randomize probe order so the 8-prefix pattern doesn't
    # match Cloudflare's bot heuristic. Deterministic flag for tests.
    if not _SSO_DISCOVERY_DETERMINISTIC_ORDER:
        _RNG.shuffle(probe_hosts)

    events: list[dict[str, Any]] = []
    discovered = 0
    for idx, host in enumerate(probe_hosts):
        # W4-SSO-HARDEN: 100-500ms jitter BETWEEN probes (not before the
        # first). Breaks the "8 requests in 3 seconds" regularity.
        if idx > 0:
            time.sleep(_RNG.uniform(_SSO_JITTER_MIN_S, _SSO_JITTER_MAX_S))
        # Skip hosts that don't resolve -- saves the HTTP round trip.
        try:
            socket.gethostbyname(host)
        except (socket.gaierror, OSError):
            continue
        meta = _try_oidc_discovery(host)
        if meta is None:
            continue
        issuer = meta.get("issuer") or ""
        product = _fingerprint_idp(str(issuer)) or "Generic OIDC IdP"
        discovered += 1
        events.append(
            {
                "event_type": "sso-discovery",
                "payload": {
                    "source": "oidc-discovery",
                    "domain": domain,
                    "host": host,
                    "product": product,
                    "issuer": issuer,
                    "evidence": {
                        "authorization_endpoint": meta.get("authorization_endpoint"),
                        "token_endpoint": meta.get("token_endpoint"),
                        "scopes_supported": meta.get("scopes_supported"),
                    },
                    "confidence": "confirmed",  # OIDC metadata served = strong evidence
                },
            }
        )

    events.append(
        {
            "event_type": "tool-run-result",
            "payload": {
                "adapter_id": "sso_discovery",
                "domain": domain,
                "hosts_probed": len(probe_hosts),
                "sso_endpoints_found": discovered,
            },
        }
    )
    return events


def _sso_discovery_synthetic(payload: dict[str, Any]) -> list[dict[str, Any]]:
    domain = payload.get("domain") or "example.com"
    return [
        {
            "event_type": "sso-discovery",
            "payload": {
                "source": "oidc-discovery",
                "domain": domain,
                "host": f"login.{domain}",
                "product": "Microsoft Entra (Azure AD)",
                "issuer": f"https://login.microsoftonline.com/{domain}",
                "evidence": {
                    "authorization_endpoint": f"https://login.microsoftonline.com/{domain}/oauth2/v2.0/authorize",
                    "synthetic": True,
                },
                "confidence": "confirmed",
                "synthetic": True,
            },
        },
        {
            "event_type": "tool-run-result",
            "payload": {
                "adapter_id": "sso_discovery",
                "domain": domain,
                "sso_endpoints_found": 1,
                "synthetic": True,
            },
        },
    ]


# ===========================================================================
# P7 -- tls_cert_audit
# ===========================================================================
#
# Single TLS handshake against <domain>:443. Parses the negotiated cert
# for: issuer (commercial / Let's Encrypt / self-signed / internal CA),
# validity window, SAN list, days-until-expiry. TLS version + cipher
# captured for the negotiated handshake (NOT full enumeration -- we
# accept the one-shot signal vs. adding an sslyze subprocess dep).
#
# Property-vetting signal: cert valid >397 days is a browser-warning
# trigger; self-signed on a "professional business" is a fraud signal;
# wildcard SAN list reveals adjacent infrastructure the host operates.


def _parse_cert_dt(s: str | None) -> datetime | None:
    """Parse ssl.getpeercert()'s notBefore/notAfter format. Returns UTC."""
    if not s:
        return None
    try:
        return datetime.strptime(s, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=UTC)
    except ValueError:
        return None


def _flatten_rdn(rdn: tuple) -> dict[str, str]:
    """Flatten ssl.getpeercert()'s subject/issuer RDN sequence."""
    out: dict[str, str] = {}
    for level in rdn:
        for pair in level:
            if isinstance(pair, tuple) and len(pair) == 2:
                k, v = pair
                if isinstance(k, str) and isinstance(v, str):
                    out[k] = v
    return out


def tls_cert_audit(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Audit the TLS cert on <domain>:443.

    Payload:
      {"domain": "example.com"}
      {"port": 443}              # optional, defaults to 443

    Emits `infra-fact` events for cert characteristics + `tool-run-result`
    summary. Detectability: low (single handshake, no probing).
    """
    domain = (payload.get("domain") or "").strip().lower().lstrip(".")
    if not domain:
        return [{"event_type": "tool-run-error", "payload": {"reason": "missing 'domain'"}}]
    port = int(payload.get("port", 443) or 443)

    ctx = ssl.create_default_context()
    cert: dict[str, Any] = {}
    tls_version: str | None = None
    cipher_negotiated: tuple[str, str, int] | None = None
    try:
        with (
            socket.create_connection((domain, port), timeout=10.0) as sock,
            ctx.wrap_socket(sock, server_hostname=domain) as ssock,
        ):
            cert = ssock.getpeercert() or {}
            tls_version = ssock.version()
            cipher_negotiated = ssock.cipher()
    except (socket.gaierror, TimeoutError, ConnectionError, OSError, ssl.SSLError) as exc:
        return [
            {
                "event_type": "tool-run-result",
                "payload": {
                    "adapter_id": "tls_cert_audit",
                    "skipped": True,
                    "reason": f"tls handshake failed: {type(exc).__name__}: {exc}",
                    "domain": domain,
                    "port": port,
                },
            }
        ]

    subject = _flatten_rdn(cert.get("subject", ()))
    issuer = _flatten_rdn(cert.get("issuer", ()))
    not_before = _parse_cert_dt(cert.get("notBefore"))
    not_after = _parse_cert_dt(cert.get("notAfter"))
    sans_raw = cert.get("subjectAltName", ()) or ()
    sans = [v for (k, v) in sans_raw if k == "DNS"]

    now = datetime.now(UTC)
    validity_days = (not_after - not_before).days if (not_before and not_after) else None
    days_to_expiry = (not_after - now).days if not_after else None
    self_signed = bool(subject) and subject == issuer
    issuer_org = issuer.get("organizationName") or issuer.get("commonName") or ""

    events: list[dict[str, Any]] = []
    # Core cert fact
    events.append(
        {
            "event_type": "infra-fact",
            "payload": {
                "source": "tls-audit",
                "domain": domain,
                "fact_type": "tls_cert",
                "subject_cn": subject.get("commonName") or "",
                "issuer_org": issuer_org,
                "issuer_cn": issuer.get("commonName") or "",
                "self_signed": self_signed,
                "not_before": cert.get("notBefore"),
                "not_after": cert.get("notAfter"),
                "validity_days": validity_days,
                "days_to_expiry": days_to_expiry,
                "tls_version": tls_version,
                "cipher": (
                    {
                        "name": cipher_negotiated[0],
                        "protocol": cipher_negotiated[1],
                        "bits": cipher_negotiated[2],
                    }
                    if cipher_negotiated
                    else None
                ),
            },
        }
    )
    # SAN list as a separate fact (useful for adjacent-host enumeration)
    if sans:
        events.append(
            {
                "event_type": "infra-fact",
                "payload": {
                    "source": "tls-audit",
                    "domain": domain,
                    "fact_type": "tls_sans",
                    "sans": sans[:50],  # cap to avoid huge dossier entries
                    "san_count": len(sans),
                },
            }
        )

    # Fraud/posture signals -- structured for the dossier UI to flag.
    signals: list[str] = []
    if self_signed:
        signals.append("self_signed")
    if days_to_expiry is not None and days_to_expiry < 0:
        signals.append("expired")
    if days_to_expiry is not None and 0 <= days_to_expiry < 30:
        signals.append("expires_within_30_days")
    if validity_days is not None and validity_days > 397:
        # 397 days is the post-2020 industry maximum (CA/Browser Forum).
        signals.append("validity_over_397_days")
    if tls_version in ("TLSv1", "TLSv1.1"):
        signals.append("weak_tls_protocol")
    if cipher_negotiated and cipher_negotiated[2] < 128:
        signals.append("weak_cipher_bits")
    if signals:
        events.append(
            {
                "event_type": "infra-fact",
                "payload": {
                    "source": "tls-audit",
                    "domain": domain,
                    "fact_type": "tls_signals",
                    "signals": signals,
                },
            }
        )

    events.append(
        {
            "event_type": "tool-run-result",
            "payload": {
                "adapter_id": "tls_cert_audit",
                "domain": domain,
                "issuer": issuer_org,
                "self_signed": self_signed,
                "days_to_expiry": days_to_expiry,
                "san_count": len(sans),
                "signal_count": len(signals),
            },
        }
    )
    return events


def _tls_cert_audit_synthetic(payload: dict[str, Any]) -> list[dict[str, Any]]:
    domain = payload.get("domain") or "example.com"
    return [
        {
            "event_type": "infra-fact",
            "payload": {
                "source": "tls-audit",
                "domain": domain,
                "fact_type": "tls_cert",
                "subject_cn": domain,
                "issuer_org": "Let's Encrypt",
                "self_signed": False,
                "validity_days": 90,
                "days_to_expiry": 45,
                "tls_version": "TLSv1.3",
                "synthetic": True,
            },
        },
        {
            "event_type": "tool-run-result",
            "payload": {
                "adapter_id": "tls_cert_audit",
                "domain": domain,
                "issuer": "Let's Encrypt",
                "self_signed": False,
                "signal_count": 0,
                "synthetic": True,
            },
        },
    ]


# ===========================================================================
# Registry installation
# ===========================================================================

_REGISTRY = get_registry()

_REGISTRY.register(
    "domain_rdap",
    domain_rdap,
    synthetic_mode=_domain_rdap_synthetic,
    in_process=True,
    description="Structured WHOIS via RDAP redirect (W12.id P6).",
)
_REGISTRY.register(
    "dns_txt_saas_inference",
    dns_txt_saas_inference,
    synthetic_mode=_dns_txt_saas_inference_synthetic,
    in_process=True,
    description="Infer SaaS tenants from TXT verification tokens (W12.id P2).",
)
_REGISTRY.register(
    "dns_email_security_audit",
    dns_email_security_audit,
    synthetic_mode=_dns_email_security_audit_synthetic,
    in_process=True,
    description=("SPF / DMARC / MX audit -> SaaS inference + spoof feasibility (W12.id P1)."),
)
_REGISTRY.register(
    "sso_discovery",
    sso_discovery,
    synthetic_mode=_sso_discovery_synthetic,
    in_process=True,
    description="SSO subdomain + OIDC discovery -> IdP product (W12.id P5).",
)
_REGISTRY.register(
    "tls_cert_audit",
    tls_cert_audit,
    synthetic_mode=_tls_cert_audit_synthetic,
    in_process=True,
    description=(
        "TLS handshake + cert parse: issuer, SANs, validity window, posture signals (W12.id P7)."
    ),
)

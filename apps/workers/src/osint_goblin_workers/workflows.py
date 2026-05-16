"""Workflow registry (ADR-0017 §3 W1-W9).

A workflow is a curated sequence of adapter dispatches against a single
investigation. Each step names an adapter id + a payload template that
maps from the workflow's seed payload to the adapter's input.

Payload templates use `{key}` substitution: the workflow's seed dict
provides the keys, and templated string values get formatted at
dispatch time.

Step dispatch policy:
  - Steps fire in declaration order via `tool_runner.send()` (not
    in-process); Dramatiq handles parallelism + retries.
  - Output mapping (one step's output feeding the next's input) is
    deferred to a follow-up; for the initial cut, all steps receive
    the seed dict at runtime.
  - A step with `required_seed_keys` that aren't present in the seed
    is skipped with a warning event.

Property-vetting (W9.pv) is the user's daily-driver workflow.
"""

from __future__ import annotations

import re
from typing import Any


class WorkflowStep:
    """One step in a workflow. The adapter id is dispatched against the
    investigation; the payload is built from the seed via the template.

    `inputs_from` declares output-mapping from prior steps:
      inputs_from={"lat": "step0.payload.lat", "lon": "step0.payload.lon"}
    workflow_runner resolves the references after this step's seed-based
    payload is built and merges the override values on top.
    """

    __slots__ = (
        "adapter_id",
        "description",
        "inputs_from",
        "payload_template",
        "required_seed_keys",
    )

    def __init__(
        self,
        adapter_id: str,
        payload_template: dict[str, Any],
        required_seed_keys: tuple[str, ...] = (),
        description: str = "",
        inputs_from: dict[str, str] | None = None,
    ) -> None:
        self.adapter_id = adapter_id
        self.payload_template = payload_template
        self.required_seed_keys = required_seed_keys
        self.description = description
        self.inputs_from = inputs_from or {}

    def build_payload(self, seed: dict[str, Any]) -> dict[str, Any] | None:
        """Return the formatted payload, or None if a required seed key
        is absent (skip the step).

        Optional template keys (those not in `required_seed_keys`)
        default to empty string when the seed doesn't supply them --
        the step still fires; the adapter handles empty optional inputs.
        """
        for k in self.required_seed_keys:
            if not seed.get(k):
                return None
        # Use a defaultdict-like wrapper so format() resolves missing keys
        # to "" instead of raising KeyError.
        safe_seed = _DefaultEmpty(seed)
        out: dict[str, Any] = {}
        for k, v in self.payload_template.items():
            if isinstance(v, str) and "{" in v and "}" in v:
                try:
                    out[k] = v.format_map(safe_seed)
                except (KeyError, ValueError):
                    return None
            else:
                out[k] = v
        return out


# Reference syntax: `step{N}.payload.{key}` where N is the 0-indexed
# prior step. The resolver scans step_results[N] for the first event
# whose payload contains `key` and returns that value.
_INPUTS_FROM_RE = re.compile(r"^step(\d+)\.payload\.([A-Za-z0-9_]+)$")


def resolve_inputs_from(
    inputs_from: dict[str, str],
    step_results: list[list[dict[str, Any]]],
) -> dict[str, Any]:
    """Resolve every reference in `inputs_from` against accumulated
    `step_results`. Returns a dict of payload-key overrides to merge.

    Missing references (step index out of range, no event carrying the
    key, malformed syntax) are silently omitted -- the dependent step's
    required_seed_keys / template defaults take over.
    """
    out: dict[str, Any] = {}
    for key, ref in inputs_from.items():
        if not isinstance(ref, str):
            continue
        m = _INPUTS_FROM_RE.match(ref.strip())
        if m is None:
            continue
        step_idx = int(m.group(1))
        field = m.group(2)
        if step_idx < 0 or step_idx >= len(step_results):
            continue
        for event in step_results[step_idx]:
            payload = event.get("payload") if isinstance(event, dict) else None
            if isinstance(payload, dict) and field in payload:
                value = payload[field]
                if value is not None and value != "":
                    out[key] = value
                    break
    return out


class _DefaultEmpty(dict):
    """dict subclass where missing keys resolve to empty string via
    format_map. Lets workflow step templates reference optional seed
    fields without aborting the step on absence."""

    def __missing__(self, key: str) -> str:
        return ""


class Workflow:
    __slots__ = ("description", "id", "name", "steps")

    def __init__(
        self,
        id: str,
        name: str,
        description: str,
        steps: list[WorkflowStep],
    ) -> None:
        self.id = id
        self.name = name
        self.description = description
        self.steps = steps


# Registry. Keyed by workflow id (W1-W9 per ADR-0017 §3 + W9 pivot).
WORKFLOWS: dict[str, Workflow] = {
    "w1.un": Workflow(
        id="w1.un",
        name="Username Dossier",
        description="Maigret + Sherlock + social fan-out across N platforms.",
        steps=[
            WorkflowStep(
                "maigret",
                {"username": "{username}"},
                required_seed_keys=("username",),
                description="Maigret N-site probe",
            ),
            WorkflowStep(
                "twitter_public",
                {"handle": "{username}"},
                required_seed_keys=("username",),
            ),
            WorkflowStep(
                "github_profile",
                {"username": "{username}"},
                required_seed_keys=("username",),
            ),
            WorkflowStep(
                "bluesky_followers",
                {"handle": "{username}", "limit": 50},
                required_seed_keys=("username",),
            ),
        ],
    ),
    "w2.em": Workflow(
        id="w2.em",
        name="Email Lookup",
        description=(
            "MX validate -> HIBP domain breaches -> IntelBase breach + account "
            "fanout (40B+ records, infostealer logs)."
        ),
        steps=[
            WorkflowStep(
                "email_mx_validate",
                {"email": "{email}"},
                required_seed_keys=("email",),
            ),
            WorkflowStep(
                "hibp_breach_check",
                {"email": "{email}"},
                required_seed_keys=("email",),
            ),
            WorkflowStep(
                "intelbase_email_lookup",
                {"email": "{email}"},
                required_seed_keys=("email",),
                description="IntelBase: breach + cross-platform account fanout",
            ),
        ],
    ),
    "w3.ph": Workflow(
        id="w3.ph",
        name="Phone Pivot",
        description="Format validate + carrier + timezone + Google-SERP mention scan.",
        steps=[
            WorkflowStep(
                "phone_format_validate",
                {"phone": "{phone}", "region": "{region}"},
                required_seed_keys=("phone",),
            ),
            WorkflowStep(
                "phone_carrier_lookup",
                {"phone": "{phone}", "region": "{region}"},
                required_seed_keys=("phone",),
            ),
            WorkflowStep(
                "phone_timezone_lookup",
                {"phone": "{phone}", "region": "{region}"},
                required_seed_keys=("phone",),
            ),
            WorkflowStep(
                "google_serp_phone",
                {"phone": "{phone}"},
                required_seed_keys=("phone",),
            ),
        ],
    ),
    "w4.im": Workflow(
        id="w4.im",
        name="Image OSINT",
        description="Reverse-image aggregator + EXIF + provenance + AI-tell heuristics + geo.",
        steps=[
            WorkflowStep(
                "reverse_image_aggregator",
                {"image_url": "{image_url}"},
                required_seed_keys=("image_url",),
            ),
            WorkflowStep(
                "image_provenance_check",
                {"image_url": "{image_url}"},
                required_seed_keys=("image_url",),
            ),
            WorkflowStep(
                "image_ai_local_detect",
                {"image_url": "{image_url}"},
                required_seed_keys=("image_url",),
                description="Local AI-image heuristic ensemble (no upload)",
            ),
            WorkflowStep(
                "phash_dedupe",
                {"image_url": "{image_url}", "case_id": "{case_id}"},
                required_seed_keys=("image_url",),
            ),
        ],
    ),
    "w5.do": Workflow(
        id="w5.do",
        name="Domain + CT Timeline",
        description=(
            "CT log + Wayback CDX + DNS prefix sweep + M365 autodiscover "
            "+ subfinder/amass (if installed)."
        ),
        steps=[
            WorkflowStep(
                "ct_log_lookup",
                {"domain": "{domain}", "limit": 200},
                required_seed_keys=("domain",),
            ),
            WorkflowStep(
                "wayback_cdx_subdomains",
                {"domain": "{domain}", "limit": 200},
                required_seed_keys=("domain",),
            ),
            WorkflowStep(
                "dns_prefix_sweep",
                {"domain": "{domain}", "limit": 200},
                required_seed_keys=("domain",),
                description=(
                    "Active prefix-probe enumeration; closes the 20-40% gap "
                    "left by passive CT/Wayback."
                ),
            ),
            WorkflowStep(
                "wayback_legacy_files",
                {"domain": "{domain}", "limit": 150},
                required_seed_keys=("domain",),
                description=(
                    "Legacy server-side files (.asp/.cfm/.jsp/.php) -- "
                    "decade-old-business pivot (Phase 4, 2026-05-12)."
                ),
            ),
            WorkflowStep(
                "m365_autodiscover_probe",
                {"domain": "{domain}"},
                required_seed_keys=("domain",),
                description=(
                    "Passive Microsoft 365 tenancy confirmation via autodiscover DNS landing."
                ),
            ),
            WorkflowStep(
                "subfinder_subprocess",
                {"domain": "{domain}"},
                required_seed_keys=("domain",),
            ),
            WorkflowStep(
                "amass_subprocess",
                {"domain": "{domain}"},
                required_seed_keys=("domain",),
            ),
        ],
    ),
    "w6.pe": Workflow(
        id="w6.pe",
        name="Person Background",
        description="TruePeopleSearch + LinkedIn + GitHub + breach surface.",
        steps=[
            WorkflowStep(
                "true_people_search",
                {"name": "{name}", "city": "{city}", "state": "{state}"},
                required_seed_keys=("name",),
            ),
            WorkflowStep(
                "google_serp_linkedin",
                {"name": "{name}", "company": "{company}"},
                required_seed_keys=("name",),
            ),
            WorkflowStep(
                "rocketreach_search",
                {"name": "{name}", "company": "{company}"},
                required_seed_keys=("name",),
            ),
            WorkflowStep(
                "hibp_breach_check",
                {"email": "{email}"},
                required_seed_keys=("email",),
            ),
        ],
    ),
    "w7.fa": Workflow(
        id="w7.fa",
        name="Face Match",
        description="Reverse image + biometric gate. OPSEC red by default.",
        steps=[
            WorkflowStep(
                "reverse_image_aggregator",
                {"image_url": "{image_url}"},
                required_seed_keys=("image_url",),
            ),
        ],
    ),
    "w8.ge": Workflow(
        id="w8.ge",
        name="Event Geolocation",
        description="Image geo + KartaView + sun-angle. Time-pinned.",
        steps=[
            WorkflowStep(
                "image_exif",
                {"image_url": "{image_url}"},
                required_seed_keys=("image_url",),
            ),
            WorkflowStep(
                "seasonal_metadata_check",
                {"image_url": "{image_url}", "claimed_season": "{season}"},
                required_seed_keys=("image_url",),
            ),
            WorkflowStep(
                "kartaview_nearby",
                {"lat": "{lat}", "lon": "{lon}", "radius_m": 200},
                required_seed_keys=("lat", "lon"),
            ),
        ],
    ),
    "w11.em": Workflow(
        id="w11.em",
        name="Email Deep (free-stack)",
        description=(
            "Margaret's free-stack replacement for IntelBase: MX -> HIBP "
            "-> Gravatar (owner-attested) -> GitHub commits (behavioral) "
            "-> Hudson Rock (infostealer logs). All free, no paid keys."
        ),
        steps=[
            WorkflowStep(
                "email_mx_validate",
                {"email": "{email}"},
                required_seed_keys=("email",),
            ),
            WorkflowStep(
                "hibp_breach_check",
                {"email": "{email}"},
                required_seed_keys=("email",),
            ),
            WorkflowStep(
                "gravatar_profile_lookup",
                {"email": "{email}"},
                required_seed_keys=("email",),
                description="Owner-attested verified_accounts",
            ),
            WorkflowStep(
                "github_commit_email_search",
                {"email": "{email}"},
                required_seed_keys=("email",),
                description="Behavioral identity via public commits",
            ),
            WorkflowStep(
                "hudson_rock_email_check",
                {"email": "{email}"},
                required_seed_keys=("email",),
                description="Infostealer log lookup (30M+ machines)",
            ),
            WorkflowStep(
                "user_scanner",
                {"email": "{email}"},
                required_seed_keys=("email",),
                description="95+ service probe (holehe successor)",
            ),
        ],
    ),
    "w10.ip": Workflow(
        id="w10.ip",
        name="IP Vetting",
        description=(
            "Geolocation + reverse DNS + ASN + reputation (closes 6-primitive triangulation)."
        ),
        steps=[
            WorkflowStep(
                "ip_geolocation",
                {"ip": "{ip}"},
                required_seed_keys=("ip",),
            ),
            WorkflowStep(
                "ip_reverse_dns",
                {"ip": "{ip}"},
                required_seed_keys=("ip",),
            ),
            WorkflowStep(
                "ip_asn_lookup",
                {"ip": "{ip}"},
                required_seed_keys=("ip",),
            ),
            WorkflowStep(
                "ip_reputation",
                {"ip": "{ip}"},
                required_seed_keys=("ip",),
            ),
        ],
    ),
    "w9.pv": Workflow(
        id="w9.pv",
        name="Property Vetting",
        description=(
            "Nominatim -> Inside Airbnb -> reverse-image + EXIF on listing "
            "photos -> host name cross-check. The user's daily-driver."
        ),
        steps=[
            WorkflowStep(
                "nominatim_geocode",
                {"q": "{address}"},
                required_seed_keys=("address",),
            ),
            WorkflowStep(
                "address_nearby_features",
                {"radius_m": 200},
                required_seed_keys=(),
                inputs_from={
                    "lat": "step0.payload.lat",
                    "lon": "step0.payload.lon",
                },
                description=(
                    "OSM Overpass neighborhood profile. Reads lat/lon "
                    "from the prior nominatim_geocode step via "
                    "inputs_from output-mapping."
                ),
            ),
            WorkflowStep(
                "inside_airbnb_listings",
                {
                    "csv_path": "{csv_path}",
                    "host_name": "{host_name}",
                },
                required_seed_keys=("csv_path",),
            ),
            WorkflowStep(
                "reverse_image_aggregator",
                {"image_url": "{photo_url}"},
                required_seed_keys=("photo_url",),
            ),
            WorkflowStep(
                "image_provenance_check",
                {"image_url": "{photo_url}"},
                required_seed_keys=("photo_url",),
            ),
            WorkflowStep(
                "image_ai_local_detect",
                {"image_url": "{photo_url}"},
                required_seed_keys=("photo_url",),
                description="Local AI-image heuristic ensemble on the listing photo",
            ),
            WorkflowStep(
                "true_people_search",
                {"name": "{host_name}", "city": "{city}", "state": "{state}"},
                required_seed_keys=("host_name",),
            ),
            WorkflowStep(
                "hibp_breach_check",
                {"email": "{email}"},
                required_seed_keys=("email",),
            ),
        ],
    ),
    "w12.id": Workflow(
        id="w12.id",
        name="Identity Fabric / Domain Posture",
        description=(
            "RDAP + TXT SaaS inference + SPF/DMARC/MX audit + M365 "
            "autodiscover + SSO/OIDC discovery. Answers 'does the host's "
            "claimed digital identity stand up?' (Tomás Phase 2, 2026-05-12)."
        ),
        steps=[
            WorkflowStep(
                "domain_rdap",
                {"domain": "{domain}"},
                required_seed_keys=("domain",),
                description="Registrant / NS / status / dates (P6).",
            ),
            WorkflowStep(
                "dns_txt_saas_inference",
                {"domain": "{domain}"},
                required_seed_keys=("domain",),
                description="SaaS tenancy fingerprints from TXT tokens (P2).",
            ),
            WorkflowStep(
                "dns_email_security_audit",
                {"domain": "{domain}"},
                required_seed_keys=("domain",),
                description="SPF/DMARC/MX -> tenancy + spoof feasibility (P1).",
            ),
            WorkflowStep(
                "m365_autodiscover_probe",
                {"domain": "{domain}"},
                required_seed_keys=("domain",),
                description="M365 confirmation via autodiscover landing (P3).",
            ),
            WorkflowStep(
                "sso_discovery",
                {"domain": "{domain}"},
                required_seed_keys=("domain",),
                description="SSO subdomain prefix probe + OIDC discovery (P5).",
            ),
            WorkflowStep(
                "tls_cert_audit",
                {"domain": "{domain}"},
                required_seed_keys=("domain",),
                description=(
                    "TLS handshake + cert parse: issuer, SANs, validity "
                    "window, posture signals (P7, Phase 4 2026-05-12)."
                ),
            ),
        ],
    ),
    "w13.dk": Workflow(
        id="w13.dk",
        name="Dork Sweep (open-web acquisition)",
        description=(
            "Open-web search engine sweep across DDG (default), Brave "
            "(env-gated), and Serper/Google (env-gated). Property-vetting "
            "curated dork corpus (~15 templates) from offensive-osint §18. "
            "All hits TENTATIVE until investigator opens in-tab "
            "(methodology §2.1). Phase 5 (2026-05-12)."
        ),
        steps=[
            WorkflowStep(
                "dork_sweep_ddg",
                {
                    "name": "{name}",
                    "email": "{email}",
                    "phone": "{phone}",
                    "domain": "{domain}",
                    "username": "{username}",
                    "address": "{address}",
                },
                required_seed_keys=(),  # any seed combo runs; empty seed -> no-op
                description="DuckDuckGo HTML scrape (keyless default).",
            ),
            WorkflowStep(
                "dork_sweep_brave",
                {
                    "name": "{name}",
                    "email": "{email}",
                    "phone": "{phone}",
                    "domain": "{domain}",
                    "username": "{username}",
                    "address": "{address}",
                },
                required_seed_keys=(),
                description="Brave Search API (env-gated OSINT_BRAVE_API_KEY).",
            ),
            WorkflowStep(
                "dork_sweep_serper",
                {
                    "name": "{name}",
                    "email": "{email}",
                    "phone": "{phone}",
                    "domain": "{domain}",
                    "username": "{username}",
                    "address": "{address}",
                },
                required_seed_keys=(),
                description="Serper.dev / Google results (env-gated OSINT_SERPER_API_KEY).",
            ),
            WorkflowStep(
                "dork_sweep_bing",
                {
                    "name": "{name}",
                    "email": "{email}",
                    "phone": "{phone}",
                    "domain": "{domain}",
                    "username": "{username}",
                    "address": "{address}",
                },
                required_seed_keys=(),
                description=(
                    "Bing main-UI HTML scrape (keyless 4th engine; "
                    "UA-rotated for burst-protection softening). "
                    "Sits in cost-tier between DDG and API engines; "
                    "cross-engine corroboration with DDG graduates "
                    "shared URLs TENTATIVE -> MEDIUM (methodology §2.1)."
                ),
            ),
            WorkflowStep(
                "dork_sweep_yandex",
                {
                    "name": "{name}",
                    "email": "{email}",
                    "phone": "{phone}",
                    "domain": "{domain}",
                    "username": "{username}",
                    "address": "{address}",
                },
                required_seed_keys=(),
                description=(
                    "Yandex through Scrapling StealthyFetcher (keyless 5th "
                    "engine, RU/UA/PL/CZ/EE coverage Western engines miss). "
                    "Direct URL anchors (no redirect unwrap); native site: "
                    "operator support (no query rewrite needed)."
                ),
            ),
            WorkflowStep(
                "dork_sweep_baidu",
                {
                    "name": "{name}",
                    "email": "{email}",
                    "phone": "{phone}",
                    "domain": "{domain}",
                    "username": "{username}",
                    "address": "{address}",
                },
                required_seed_keys=(),
                description=(
                    "Baidu through Scrapling StealthyFetcher (keyless 6th "
                    "engine, CN-language coverage of ~96% of CN-web that "
                    "Google blocks and Bing/Yandex miss). Destination URL "
                    "from result-container `mu=` attribute (no redirect "
                    "unwrap); native site: operator support."
                ),
            ),
            WorkflowStep(
                "dork_sweep_naver",
                {
                    "name": "{name}",
                    "email": "{email}",
                    "phone": "{phone}",
                    "domain": "{domain}",
                    "username": "{username}",
                    "address": "{address}",
                },
                required_seed_keys=(),
                description=(
                    "Naver through Scrapling + BS4 card-walk (keyless 7th "
                    "engine, KR-language coverage; #1 search engine in "
                    "South Korea ~70% market share). React-rendered DOM "
                    "with random class hashes -- BS4 card-walk anchors on "
                    "stable sds-comps-text-type-headline1 + nocr=1 "
                    "selectors."
                ),
            ),
        ],
    ),
}


def get_workflow(workflow_id: str) -> Workflow | None:
    return WORKFLOWS.get(workflow_id)


def is_workflow_id(adapter_id: str) -> bool:
    """The API uses this to decide whether to dispatch through
    workflow_runner (true) or tool_runner (false). Workflow ids match
    /^w\\d+\\./ (e.g. w1.un, w9.pv)."""
    if not adapter_id or "." not in adapter_id:
        return False
    head = adapter_id.split(".", 1)[0]
    return head.startswith("w") and head[1:].isdigit()

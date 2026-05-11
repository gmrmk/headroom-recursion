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

from typing import Any


class WorkflowStep:
    """One step in a workflow. The adapter id is dispatched against the
    investigation; the payload is built from the seed via the template."""

    __slots__ = ("adapter_id", "payload_template", "required_seed_keys", "description")

    def __init__(
        self,
        adapter_id: str,
        payload_template: dict[str, Any],
        required_seed_keys: tuple[str, ...] = (),
        description: str = "",
    ) -> None:
        self.adapter_id = adapter_id
        self.payload_template = payload_template
        self.required_seed_keys = required_seed_keys
        self.description = description

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


class _DefaultEmpty(dict):
    """dict subclass where missing keys resolve to empty string via
    format_map. Lets workflow step templates reference optional seed
    fields without aborting the step on absence."""

    def __missing__(self, key: str) -> str:
        return ""


class Workflow:
    __slots__ = ("id", "name", "description", "steps")

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
        description="Reverse-image aggregator + EXIF + provenance + geo.",
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
                "phash_dedupe",
                {"image_url": "{image_url}", "case_id": "{case_id}"},
                required_seed_keys=("image_url",),
            ),
        ],
    ),
    "w5.do": Workflow(
        id="w5.do",
        name="Domain + CT Timeline",
        description="CT log + Wayback CDX + subfinder + amass subdomain enum.",
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
        ],
    ),
    "w10.ip": Workflow(
        id="w10.ip",
        name="IP Vetting",
        description=(
            "Geolocation + reverse DNS + ASN + reputation " "(closes 6-primitive triangulation)."
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

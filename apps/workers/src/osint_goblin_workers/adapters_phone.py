"""Phone-vetting adapters (W3.ph workflow, Sprint 4).

Three in-process adapters built on Google's libphonenumber (via the
phonenumbers Python port). All free, all offline (no network):

  - phone_format_validate: parse + is_valid + country + region + line type
  - phone_carrier_lookup: carrier name from prefix database
  - phone_timezone_lookup: timezone(s) covering the number's region

Plus one subprocess wrapper for Google SERP phone search:
  - google_serp_phone: site-agnostic SERP scrape for any mention of
    the phone number (catches social-media listings, business pages,
    Yelp/Craigslist that publish phone numbers).

Property-vetting use case: host claims a phone. We validate the
format, confirm the carrier matches the claimed origin (e.g. "host
says Springfield IL but carrier is a non-US VoIP"), and search for
other places the number appears publicly.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .adapters import get_registry
from .subprocess_adapter import make_subprocess_adapter

_EMPIRICAL_PY = (
    Path(r"C:\Users\strid\osint-dashboard-research\empirical\.venv\Scripts\python.exe")
    if os.name == "nt"
    else Path("/c/Users/strid/osint-dashboard-research/empirical/.venv/bin/python")
)
_REPO_ROOT = Path(__file__).resolve().parents[4]


def _parse_number(raw: str, region: str = "US") -> Any:
    """Try parsing with explicit region first; fall back to E.164 (no
    region) if the input starts with '+'. Returns the PhoneNumber
    object or None on parse failure."""
    import phonenumbers

    try:
        if raw.startswith("+"):
            return phonenumbers.parse(raw, None)
        return phonenumbers.parse(raw, region)
    except phonenumbers.NumberParseException:
        return None


def _line_type_name(num_type: int) -> str:
    """Convert phonenumbers numeric type to human-readable string."""
    import phonenumbers

    mapping = {
        phonenumbers.PhoneNumberType.FIXED_LINE: "fixed-line",
        phonenumbers.PhoneNumberType.MOBILE: "mobile",
        phonenumbers.PhoneNumberType.FIXED_LINE_OR_MOBILE: "fixed-or-mobile",
        phonenumbers.PhoneNumberType.TOLL_FREE: "toll-free",
        phonenumbers.PhoneNumberType.PREMIUM_RATE: "premium-rate",
        phonenumbers.PhoneNumberType.SHARED_COST: "shared-cost",
        phonenumbers.PhoneNumberType.VOIP: "voip",
        phonenumbers.PhoneNumberType.PERSONAL_NUMBER: "personal",
        phonenumbers.PhoneNumberType.PAGER: "pager",
        phonenumbers.PhoneNumberType.UAN: "uan",
        phonenumbers.PhoneNumberType.VOICEMAIL: "voicemail",
        phonenumbers.PhoneNumberType.UNKNOWN: "unknown",
    }
    return mapping.get(num_type, "unknown")


# ---------------------------------------------------------------------------
# 1. phone_format_validate -- libphonenumber parse + classify
# ---------------------------------------------------------------------------


def phone_format_validate(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Validate a phone number's format + extract metadata.

    Payload:
      {"phone": "+1-555-867-5309",
       "region": "US"}     # optional, default "US"; only used if no +
    """
    raw = (payload.get("phone") or "").strip()
    if not raw:
        return [
            {
                "event_type": "tool-run-error",
                "payload": {"reason": "missing 'phone' in payload"},
            }
        ]
    region = (payload.get("region") or "US").strip().upper() or "US"
    try:
        import phonenumbers
    except ImportError:
        return [
            {
                "event_type": "tool-run-error",
                "payload": {"reason": "phonenumbers not installed"},
            }
        ]

    num = _parse_number(raw, region)
    if num is None:
        return [
            {
                "event_type": "tool-run-result",
                "payload": {
                    "phone": raw,
                    "valid": False,
                    "reason": "unparseable",
                },
            }
        ]

    is_valid = phonenumbers.is_valid_number(num)
    is_possible = phonenumbers.is_possible_number(num)
    line_type = _line_type_name(phonenumbers.number_type(num))
    e164 = phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.E164)
    intl = phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.INTERNATIONAL)
    national = phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.NATIONAL)
    country_code = num.country_code
    region_code = phonenumbers.region_code_for_number(num) or ""

    return [
        {
            "event_type": "person-match",  # repurpose; phone is person-identifying
            "payload": {
                "source": "phone-format",
                "phone": raw,
                "e164": e164,
                "international": intl,
                "national": national,
                "country_code": country_code,
                "region": region_code,
                "valid": is_valid,
                "possible": is_possible,
                "line_type": line_type,
                "voip_likely": line_type == "voip",
            },
        },
        {
            "event_type": "tool-run-result",
            "payload": {
                "phone": raw,
                "valid": is_valid,
                "e164": e164,
                "line_type": line_type,
            },
        },
    ]


def _phone_format_validate_synthetic(payload: dict[str, Any]) -> list[dict[str, Any]]:
    phone = payload.get("phone", "+1-217-555-0123")
    return [
        {
            "event_type": "person-match",
            "payload": {
                "source": "phone-format",
                "phone": phone,
                "e164": "+12175550123",
                "international": "+1 217-555-0123",
                "national": "(217) 555-0123",
                "country_code": 1,
                "region": "US",
                "valid": True,
                "possible": True,
                "line_type": "mobile",
                "voip_likely": False,
                "synthetic": True,
            },
        },
        {
            "event_type": "tool-run-result",
            "payload": {
                "phone": phone,
                "valid": True,
                "e164": "+12175550123",
                "line_type": "mobile",
                "synthetic": True,
            },
        },
    ]


# ---------------------------------------------------------------------------
# 2. phone_carrier_lookup -- libphonenumber carrier database
# ---------------------------------------------------------------------------


def phone_carrier_lookup(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Carrier name + region geocode from libphonenumber's prefix tables.

    No network. Coverage is best-effort -- libphonenumber's carrier
    database covers most countries but is more complete for mobile
    than fixed-line. Returns empty carrier string when unknown.
    """
    raw = (payload.get("phone") or "").strip()
    region = (payload.get("region") or "US").strip().upper() or "US"
    if not raw:
        return [
            {
                "event_type": "tool-run-error",
                "payload": {"reason": "missing 'phone'"},
            }
        ]
    try:
        import phonenumbers
        from phonenumbers import carrier, geocoder
    except ImportError:
        return [
            {
                "event_type": "tool-run-error",
                "payload": {"reason": "phonenumbers not installed"},
            }
        ]

    num = _parse_number(raw, region)
    if num is None:
        return [
            {
                "event_type": "tool-run-result",
                "payload": {"phone": raw, "valid": False, "carrier": "", "geocode": ""},
            }
        ]
    car = carrier.name_for_number(num, "en") or ""
    geo = geocoder.description_for_number(num, "en") or ""
    return [
        {
            "event_type": "person-match",
            "payload": {
                "source": "phone-carrier",
                "phone": raw,
                "e164": phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.E164),
                "carrier": car,
                "geocode": geo,
                "region": phonenumbers.region_code_for_number(num) or "",
                "country_code": num.country_code,
            },
        },
        {
            "event_type": "tool-run-result",
            "payload": {"phone": raw, "carrier": car, "geocode": geo},
        },
    ]


def _phone_carrier_lookup_synthetic(payload: dict[str, Any]) -> list[dict[str, Any]]:
    phone = payload.get("phone", "+1-217-555-0123")
    return [
        {
            "event_type": "person-match",
            "payload": {
                "source": "phone-carrier",
                "phone": phone,
                "e164": "+12175550123",
                "carrier": "Synthetic Mobile",
                "geocode": "Springfield, IL",
                "region": "US",
                "country_code": 1,
                "synthetic": True,
            },
        },
        {
            "event_type": "tool-run-result",
            "payload": {
                "phone": phone,
                "carrier": "Synthetic Mobile",
                "geocode": "Springfield, IL",
                "synthetic": True,
            },
        },
    ]


# ---------------------------------------------------------------------------
# 3. phone_timezone_lookup -- timezone(s) from prefix database
# ---------------------------------------------------------------------------


def phone_timezone_lookup(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Timezone(s) covering the phone number's region.

    Useful for "host says Springfield IL but the phone's timezone is
    GMT+8" detection. Multiple timezones returned for numbers that
    span time zones (e.g. US country code 1 covers multiple US zones).
    """
    raw = (payload.get("phone") or "").strip()
    region = (payload.get("region") or "US").strip().upper() or "US"
    if not raw:
        return [
            {
                "event_type": "tool-run-error",
                "payload": {"reason": "missing 'phone'"},
            }
        ]
    try:
        import phonenumbers
        from phonenumbers import timezone as ptz
    except ImportError:
        return [
            {
                "event_type": "tool-run-error",
                "payload": {"reason": "phonenumbers not installed"},
            }
        ]

    num = _parse_number(raw, region)
    if num is None:
        return [
            {
                "event_type": "tool-run-result",
                "payload": {"phone": raw, "valid": False, "timezones": []},
            }
        ]
    tzs = list(ptz.time_zones_for_number(num))
    return [
        {
            "event_type": "person-match",
            "payload": {
                "source": "phone-timezone",
                "phone": raw,
                "e164": phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.E164),
                "timezones": tzs,
                "region": phonenumbers.region_code_for_number(num) or "",
            },
        },
        {
            "event_type": "tool-run-result",
            "payload": {"phone": raw, "timezones": tzs},
        },
    ]


def _phone_timezone_lookup_synthetic(payload: dict[str, Any]) -> list[dict[str, Any]]:
    phone = payload.get("phone", "+1-217-555-0123")
    return [
        {
            "event_type": "person-match",
            "payload": {
                "source": "phone-timezone",
                "phone": phone,
                "e164": "+12175550123",
                "timezones": ["America/Chicago"],
                "region": "US",
                "synthetic": True,
            },
        },
        {
            "event_type": "tool-run-result",
            "payload": {
                "phone": phone,
                "timezones": ["America/Chicago"],
                "synthetic": True,
            },
        },
    ]


# ---------------------------------------------------------------------------
# Registry installation
# ---------------------------------------------------------------------------

_REGISTRY = get_registry()

_REGISTRY.register(
    "phone_format_validate",
    phone_format_validate,
    synthetic_mode=_phone_format_validate_synthetic,
    in_process=True,
    description="libphonenumber parse + classify (W3.ph). Catches typos + VoIP.",
)
_REGISTRY.register(
    "phone_carrier_lookup",
    phone_carrier_lookup,
    synthetic_mode=_phone_carrier_lookup_synthetic,
    in_process=True,
    description="Carrier name + geocode from libphonenumber prefix DB (W3.ph).",
)
_REGISTRY.register(
    "phone_timezone_lookup",
    phone_timezone_lookup,
    synthetic_mode=_phone_timezone_lookup_synthetic,
    in_process=True,
    description="Timezone(s) for the phone's region (W3.ph claim-vs-region check).",
)

# Scrapling subprocess wrapper for Google SERP phone search
_GOOGLE_SERP_PHONE_WRAPPER = _REPO_ROOT / "adapters" / "google_serp_phone" / "wrapper.py"
if _GOOGLE_SERP_PHONE_WRAPPER.is_file() and _EMPIRICAL_PY.is_file():
    _REGISTRY.register(
        "google_serp_phone",
        make_subprocess_adapter(
            _GOOGLE_SERP_PHONE_WRAPPER,
            timeout_s=60.0,
            python_executable=str(_EMPIRICAL_PY),
        ),
        synthetic_mode=make_subprocess_adapter(
            _GOOGLE_SERP_PHONE_WRAPPER,
            timeout_s=30.0,
            python_executable=str(_EMPIRICAL_PY),
            extra_env={"OSINT_ADAPTER_MODE": "synthetic"},
        ),
        in_process=False,
        description="Google SERP -> any public mention of the phone (W3.ph).",
    )

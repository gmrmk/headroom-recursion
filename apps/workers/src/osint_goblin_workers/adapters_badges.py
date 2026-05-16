"""Sub-brand detection adapter (W4-SUB-BRAND, Margaret wave-4 §4).

Scans listing copy / page text for verification sub-brand mentions
(AirCover, Autohost, Truvi, GuestVerify, Shield Suite, Screen & Protect)
and emits one `platform_verification_floor` event per matched brand.

A hit on "AirCover" in a listing tells the investigator that Airbnb's
platform-baseline identity assurance ran on this host -- useful
triangulation primitive at hour-3, zero third-party API cost.

severity_basis: matrix:PV_LISTING_PHOTO_REUSE (adjacent -- verification
floor inference, not a contradiction signal; surfaced as trust-positive).

Source: Tomás wave-4 highest-ROI finding
(phase6/wave4/tomas-wave4.md); owner: Tomás (Margaret wave-4 §10).

Catalog is loaded once at import time. Adding a new brand is a
catalog.json edit + restart -- no code change.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .adapters import get_registry

# Catalog lives in repo-root packages/sub-brand-catalog/. parents[4] resolves
# to the repo root:
#   adapters_badges.py
#   parents[0] = osint_goblin_workers/
#   parents[1] = src/
#   parents[2] = workers/
#   parents[3] = apps/
#   parents[4] = <repo root>
_REPO_ROOT = Path(__file__).resolve().parents[4]
_CATALOG_PATH = _REPO_ROOT / "packages" / "sub-brand-catalog" / "catalog.json"


def _load_catalog() -> dict[str, Any]:
    """Read catalog.json; return empty-sub_brands stub if missing."""
    if not _CATALOG_PATH.is_file():
        return {"sub_brands": []}
    return json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))


def _compile_patterns(
    catalog: dict[str, Any],
) -> list[tuple[dict[str, Any], list[re.Pattern[str]]]]:
    """Pre-compile each brand's regex patterns with word boundaries +
    case-insensitive matching. Each pattern is re.escape()-d so symbols
    like '&' in 'Screen & Protect' match literally."""
    compiled: list[tuple[dict[str, Any], list[re.Pattern[str]]]] = []
    for brand in catalog.get("sub_brands", []):
        patterns = [
            re.compile(rf"(?<!\w){re.escape(p)}(?!\w)", re.IGNORECASE)
            for p in brand.get("patterns", [])
        ]
        compiled.append((brand, patterns))
    return compiled


# Load + compile once at module import. Catalog is small (six brands today,
# 20-30 at long horizon); the regex cost is amortized across every adapter
# invocation in the worker process lifetime.
_CATALOG = _load_catalog()
_COMPILED = _compile_patterns(_CATALOG)


def _emit_for_text(text: str) -> list[dict[str, Any]]:
    """Scan `text` against the compiled catalog; return one
    platform_verification_floor event per matched brand (max one event
    per brand even if multiple patterns hit, since the inferred floor
    is identical)."""
    events: list[dict[str, Any]] = []
    for brand, patterns in _COMPILED:
        for pat in patterns:
            m = pat.search(text)
            if m:
                events.append(
                    {
                        "event_type": "platform_verification_floor",
                        "payload": {
                            "source": "badges_sub_brand_detect",
                            "sub_brand_id": brand["id"],
                            "sub_brand_label": brand["label"],
                            "platform": brand["platform"],
                            "verification_tier": brand["verification_tier"],
                            "inferred_floor": brand["inferred_floor"],
                            "matched_text": m.group(0),
                        },
                    }
                )
                break  # one hit per brand is enough
    return events


def badges_sub_brand_detect(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Live adapter: scan `payload["listing_text"]` for sub-brand mentions.

    Payload:
      {"listing_text": "...listing description copy..."}

    Emits one `platform_verification_floor` event per matched brand,
    plus a terminal `tool-run-result` summary.
    """
    text = payload.get("listing_text") or ""
    if not isinstance(text, str):
        return [
            {
                "event_type": "tool-run-error",
                "payload": {
                    "source": "badges_sub_brand_detect",
                    "reason": "listing_text must be a string",
                },
            }
        ]
    events = _emit_for_text(text)
    events.append(
        {
            "event_type": "tool-run-result",
            "payload": {
                "source": "badges_sub_brand_detect",
                "matches": len(events),
                "brands_in_catalog": len(_COMPILED),
            },
        }
    )
    return events


def _badges_sub_brand_synthetic(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Synthetic: returns a single canned platform_verification_floor
    event (AirCover hit) so the M0 exit gate exercises the wire shape
    without depending on caller-provided text.

    If the caller DOES supply listing_text, we run the real detector
    on it -- this is a pure in-process operation, no network, so the
    live path is already deterministic. The catalog-bypass branch
    exists only to guarantee >=1 hit when listing_text is empty.
    """
    text = payload.get("listing_text") or ""
    if isinstance(text, str) and text:
        events = _emit_for_text(text)
        if events:
            events.append(
                {
                    "event_type": "tool-run-result",
                    "payload": {
                        "source": "badges_sub_brand_detect",
                        "matches": len(events),
                        "synthetic": True,
                    },
                }
            )
            return events
    # Fallback: canned hit so the gate sees the wire shape.
    return [
        {
            "event_type": "platform_verification_floor",
            "payload": {
                "source": "badges_sub_brand_detect",
                "sub_brand_id": "aircover",
                "sub_brand_label": "AirCover for Hosts",
                "platform": "airbnb",
                "verification_tier": "platform-baseline",
                "inferred_floor": "Airbnb identity-baseline ran on this host",
                "matched_text": "AirCover",
                "synthetic": True,
            },
        },
        {
            "event_type": "tool-run-result",
            "payload": {
                "source": "badges_sub_brand_detect",
                "matches": 1,
                "synthetic": True,
            },
        },
    ]


# ---------------------------------------------------------------------------
# Registry installation
# ---------------------------------------------------------------------------

_REGISTRY = get_registry()

_REGISTRY.register(
    "badges_sub_brand_detect",
    badges_sub_brand_detect,
    synthetic_mode=_badges_sub_brand_synthetic,
    in_process=True,
    description=(
        "W4-SUB-BRAND (Margaret wave-4 §4): scan listing copy for "
        "verification sub-brand mentions (AirCover, Autohost, Truvi, "
        "GuestVerify, Shield Suite, Screen & Protect) and infer the "
        "platform-side verification floor. Pure regex, no network."
    ),
)

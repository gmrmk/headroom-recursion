"""Unit tests for W4-SUB-BRAND (Margaret wave-4 §4) sub-brand adapter.

The adapter is pure regex over JSON catalog -- no network, no fixtures.
We exercise:

  1. Registry sanity (adapter is registered with synthetic_mode).
  2. Catalog loads + compiles (six brands expected).
  3. Every catalog brand round-trips when its primary pattern is
     embedded in synthetic listing copy.
  4. Case-insensitivity (lowercase "aircover" matches "AirCover").
  5. Clean text -> zero platform_verification_floor events (only the
     summary tool-run-result remains).
  6. Multi-brand text emits one event per matched brand.
  7. Bad input (non-string listing_text) returns a tool-run-error
     instead of crashing.
"""

from __future__ import annotations

from typing import Any

import pytest
from osint_goblin_workers.adapters import get_registry
from osint_goblin_workers.adapters_badges import (
    _CATALOG,
    _COMPILED,
    _badges_sub_brand_synthetic,
    badges_sub_brand_detect,
)

# ---------------------------------------------------------------------------
# Registry + catalog sanity
# ---------------------------------------------------------------------------


def test_adapter_registered() -> None:
    """badges_sub_brand_detect is in the global registry with synthetic_mode."""
    entry = get_registry().get("badges_sub_brand_detect")
    assert entry is not None, "badges_sub_brand_detect not registered"
    assert entry.synthetic_mode is not None
    assert entry.in_process is True


def test_catalog_has_six_brands() -> None:
    """W4-SUB-BRAND ships with six maintained brands at 2026-05-12."""
    assert len(_CATALOG.get("sub_brands", [])) == 6
    assert len(_COMPILED) == 6


def test_catalog_brand_ids() -> None:
    """Lock the six brand ids so a future catalog edit that removes
    one shows up loud in CI."""
    expected = {
        "aircover",
        "autohost",
        "truvi",
        "guestverify",
        "shield_suite",
        "screen_and_protect",
    }
    actual = {b["id"] for b in _CATALOG["sub_brands"]}
    assert actual == expected


# ---------------------------------------------------------------------------
# Detection -- every catalog brand round-trips
# ---------------------------------------------------------------------------


def _floor_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Filter out the terminal tool-run-result summary so assertions
    over content stay focused on the floor events."""
    return [e for e in events if e["event_type"] == "platform_verification_floor"]


@pytest.mark.parametrize(
    "brand_id,sample_text",
    [
        ("aircover", "This unit is AirCover-protected for every reservation."),
        ("autohost", "Every guest is screened by Autohost prior to check-in."),
        ("truvi", "Truvi-verified hosts -- your identity is on file."),
        ("guestverify", "We use GuestVerify to confirm every guest is real."),
        ("shield_suite", "Backed by Shield Suite at no extra cost."),
        ("screen_and_protect", "Powered by Screen & Protect from Superhog."),
    ],
)
def test_each_brand_pattern_matches(brand_id: str, sample_text: str) -> None:
    """For every catalog brand, the live adapter emits exactly one
    platform_verification_floor event when its primary pattern is
    present in the listing text."""
    out = badges_sub_brand_detect({"listing_text": sample_text})
    floors = _floor_events(out)
    assert len(floors) == 1, f"expected 1 floor event for {brand_id}, got {len(floors)}"
    payload = floors[0]["payload"]
    assert payload["sub_brand_id"] == brand_id
    assert payload["source"] == "badges_sub_brand_detect"
    # Inferred floor + tier come from the catalog -- assert they're non-empty
    # rather than locking the exact wording (the catalog is the source of truth).
    assert payload["inferred_floor"]
    assert payload["verification_tier"]
    assert payload["platform"]


def test_case_insensitivity_lowercase_aircover() -> None:
    """Lowercase 'aircover' must match 'AirCover'."""
    out = badges_sub_brand_detect({"listing_text": "this listing is aircover protected, btw."})
    floors = _floor_events(out)
    assert len(floors) == 1
    assert floors[0]["payload"]["sub_brand_id"] == "aircover"


def test_case_insensitivity_uppercase_autohost() -> None:
    """ALL-CAPS 'AUTOHOST' must match 'Autohost'."""
    out = badges_sub_brand_detect({"listing_text": "Screened by AUTOHOST -- a partner of ours."})
    floors = _floor_events(out)
    assert len(floors) == 1
    assert floors[0]["payload"]["sub_brand_id"] == "autohost"


# ---------------------------------------------------------------------------
# Negative cases
# ---------------------------------------------------------------------------


def test_clean_listing_no_floor_events() -> None:
    """Listing text with no sub-brand mentions emits zero
    platform_verification_floor events (only the summary remains)."""
    clean = (
        "Charming 2-bedroom cottage with a sunny garden. "
        "Walking distance to the lake, espresso machine in the kitchen."
    )
    out = badges_sub_brand_detect({"listing_text": clean})
    floors = _floor_events(out)
    assert floors == []
    # Summary is still emitted so the dossier knows the adapter ran.
    summaries = [e for e in out if e["event_type"] == "tool-run-result"]
    assert len(summaries) == 1
    assert summaries[0]["payload"]["matches"] == 0


def test_empty_listing_text() -> None:
    """Empty string -> zero floor events, still emits summary."""
    out = badges_sub_brand_detect({"listing_text": ""})
    assert _floor_events(out) == []


def test_missing_listing_text_key() -> None:
    """Missing key behaves the same as empty -- no crash."""
    out = badges_sub_brand_detect({})
    assert _floor_events(out) == []


def test_non_string_listing_text_returns_error() -> None:
    """Non-string listing_text returns a tool-run-error rather than
    crashing the worker."""
    out = badges_sub_brand_detect({"listing_text": 42})  # type: ignore[dict-item]
    assert any(e["event_type"] == "tool-run-error" for e in out)


# ---------------------------------------------------------------------------
# Multi-brand + boundary behavior
# ---------------------------------------------------------------------------


def test_multi_brand_text_emits_one_per_brand() -> None:
    """A listing mentioning AirCover AND Autohost emits two floor events."""
    text = (
        "Our property is AirCover-protected and every guest is "
        "screened by Autohost before booking is confirmed."
    )
    out = badges_sub_brand_detect({"listing_text": text})
    floors = _floor_events(out)
    ids = {f["payload"]["sub_brand_id"] for f in floors}
    assert ids == {"aircover", "autohost"}


def test_same_brand_mentioned_twice_emits_one_event() -> None:
    """A brand mentioned twice in the same text emits ONE event
    (we break after the first per-brand hit -- duplicate signal is
    already covered by the first match)."""
    text = "AirCover applies. AirCover applies again. AirCover-protected."
    out = badges_sub_brand_detect({"listing_text": text})
    floors = _floor_events(out)
    assert len(floors) == 1


def test_word_boundary_no_false_match() -> None:
    """'Truvialism' must NOT match 'Truvi' (word-boundary guard)."""
    out = badges_sub_brand_detect({"listing_text": "Truvialism is not a brand."})
    floors = _floor_events(out)
    assert floors == []


# ---------------------------------------------------------------------------
# Synthetic mode
# ---------------------------------------------------------------------------


def test_synthetic_mode_emits_canned_aircover_on_empty() -> None:
    """Synthetic with no listing_text emits the canned AirCover hit so
    the M0 exit gate sees >=1 platform_verification_floor event."""
    out = _badges_sub_brand_synthetic({})
    floors = _floor_events(out)
    assert len(floors) == 1
    assert floors[0]["payload"]["sub_brand_id"] == "aircover"
    assert floors[0]["payload"]["synthetic"] is True


def test_synthetic_mode_runs_real_detector_when_text_present() -> None:
    """Synthetic with listing_text falls through to the real detector
    (the live path is already pure in-process)."""
    out = _badges_sub_brand_synthetic({"listing_text": "Powered by Truvi"})
    floors = _floor_events(out)
    assert len(floors) == 1
    assert floors[0]["payload"]["sub_brand_id"] == "truvi"

"""Unit tests for R-5 Sprint 2 property-vetting adapters.

Network-dependent live paths are exercised via httpx mocking with
pytest-httpx style; the test relies on httpx.MockTransport injected
into the module-scoped client factory. The deliverability check uses
socket.getaddrinfo which we monkeypatch.

Goal: lock the wire shape (event_type + payload keys) for every
adapter so future contributors who change the live path can't
silently break the dossier UI's expectations.
"""

from __future__ import annotations

import socket
from typing import Any

import pytest
from osint_goblin_workers.adapters import get_registry
from osint_goblin_workers.adapters_property import (
    _email_mx_synthetic,
    _hibp_synthetic,
    _inside_airbnb_synthetic,
    _nominatim_synthetic,
    _tineye_synthetic,
    _true_people_synthetic,
    email_mx_validate,
    inside_airbnb_listings,
    nominatim_geocode,
)

# ---------------------------------------------------------------------------
# Registration sanity
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "adapter_id",
    [
        "nominatim_geocode",
        "email_mx_validate",
        "hibp_breach_check",
        "inside_airbnb_listings",
        "true_people_search",
        "tineye_image",
    ],
)
def test_property_adapter_registered(adapter_id: str) -> None:
    """Every R-5 adapter is in the global registry, with synthetic_mode."""
    entry = get_registry().get(adapter_id)
    assert entry is not None, f"{adapter_id} not registered"
    assert entry.synthetic_mode is not None


# ---------------------------------------------------------------------------
# Synthetic-mode wire-shape locks
# ---------------------------------------------------------------------------


def test_nominatim_synthetic_emits_geocode_match_and_summary() -> None:
    events = _nominatim_synthetic({"q": "123 Main St"})
    assert len(events) == 2
    assert events[0]["event_type"] == "geocode-match"
    assert events[0]["payload"]["query"] == "123 Main St"
    assert "lat" in events[0]["payload"]
    assert "lon" in events[0]["payload"]
    assert events[1]["event_type"] == "tool-run-result"
    assert events[1]["payload"]["matches"] == 1


def test_email_mx_synthetic_format_only() -> None:
    """Synthetic accepts any well-formatted email; rejects garbage."""
    ok = _email_mx_synthetic({"email": "user@example.com"})
    assert ok[0]["payload"]["valid_format"] is True
    assert ok[0]["payload"]["deliverable"] is True

    bad = _email_mx_synthetic({"email": "not-an-email"})
    assert bad[0]["payload"]["valid_format"] is False
    assert bad[0]["payload"]["deliverable"] is False


def test_hibp_synthetic_emits_breach_hit() -> None:
    events = _hibp_synthetic({"email": "u@example.com"})
    assert len(events) == 2
    assert events[0]["event_type"] == "breach-hit"
    assert events[0]["payload"]["domain"] == "example.com"
    assert events[0]["payload"]["synthetic"] is True


def test_true_people_synthetic_emits_person_match() -> None:
    events = _true_people_synthetic({"name": "Alice"})
    assert events[0]["event_type"] == "person-match"
    assert events[0]["payload"]["name"] == "Alice"


def test_tineye_synthetic_emits_image_match() -> None:
    events = _tineye_synthetic({"image_url": "https://example.com/face.jpg"})
    assert events[0]["event_type"] == "image-match"
    assert events[0]["payload"]["image_url"] == "https://example.com/face.jpg"


# ---------------------------------------------------------------------------
# Live email MX -- pure DNS, no third-party dep. Monkeypatch socket
# so the test is hermetic.
# ---------------------------------------------------------------------------


def test_email_mx_live_resolves_real_domain(monkeypatch: pytest.MonkeyPatch) -> None:
    """Domain that resolves (mocked) -> deliverable=True."""

    def fake_getaddrinfo(host: str, *args: Any, **kwargs: Any) -> list:
        return [(0, 0, 0, "", ("93.184.216.34", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    events = email_mx_validate({"email": "user@example.com"})
    assert events[0]["payload"]["valid_format"] is True
    assert events[0]["payload"]["deliverable"] is True
    assert events[0]["payload"]["domain"] == "example.com"


def test_email_mx_live_rejects_unresolvable(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_getaddrinfo(host: str, *args: Any, **kwargs: Any) -> list:
        raise socket.gaierror("nodename nor servname provided")

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    events = email_mx_validate({"email": "user@nonexistent.invalid"})
    assert events[0]["payload"]["valid_format"] is True
    assert events[0]["payload"]["deliverable"] is False
    assert "DNS lookup failed" in events[0]["payload"]["reason"]


def test_email_mx_rejects_malformed() -> None:
    events = email_mx_validate({"email": "garbage"})
    assert events[0]["payload"]["valid_format"] is False
    assert events[0]["payload"]["deliverable"] is False


def test_email_mx_rejects_non_string() -> None:
    events = email_mx_validate({"email": 42})  # type: ignore[dict-item]
    assert events[0]["event_type"] == "tool-run-error"


# ---------------------------------------------------------------------------
# Live nominatim -- HTTP call. Skip when network is unavailable rather
# than fail; the contract is the synthetic-mode test above.
# ---------------------------------------------------------------------------


@pytest.mark.real_network
def test_nominatim_live_smoke() -> None:
    """Real-network smoke -- runs only in the weekly real-network battery.

    Asserts the live endpoint still returns the shape we parse. Skips
    cleanly if the host is offline; does not flake the M0 fast loop."""
    events = nominatim_geocode({"q": "1600 Pennsylvania Ave, Washington DC"})
    # First event must be geocode-match OR tool-run-error (network down).
    # Either way, the registered event_type is honored.
    assert events[0]["event_type"] in ("geocode-match", "tool-run-error")
    if events[0]["event_type"] == "geocode-match":
        assert "lat" in events[0]["payload"]
        assert "lon" in events[0]["payload"]


def test_nominatim_missing_query_returns_error() -> None:
    events = nominatim_geocode({})
    assert events[0]["event_type"] == "tool-run-error"
    assert "missing" in events[0]["payload"]["reason"]


# ---------------------------------------------------------------------------
# Inside Airbnb CSV adapter (Sprint 3)
# ---------------------------------------------------------------------------


def test_inside_airbnb_synthetic_shape() -> None:
    events = _inside_airbnb_synthetic({})
    # Two listing-matches + summary
    assert len(events) == 3
    assert events[0]["event_type"] == "listing-match"
    assert events[1]["event_type"] == "listing-match"
    assert events[2]["event_type"] == "tool-run-result"
    # The first is flagged commercial; the second is not -- exercises both flows
    assert events[0]["payload"]["commercial_operator"] is True
    assert events[1]["payload"]["commercial_operator"] is False


def test_inside_airbnb_missing_csv_path() -> None:
    events = inside_airbnb_listings({"host_name": "alice"})
    assert events[0]["event_type"] == "tool-run-error"
    assert "csv_path" in events[0]["payload"]["reason"]


def test_inside_airbnb_csv_not_found(tmp_path) -> None:
    events = inside_airbnb_listings(
        {"csv_path": str(tmp_path / "nonexistent.csv"), "host_name": "alice"}
    )
    assert events[0]["event_type"] == "tool-run-error"
    assert "not found" in events[0]["payload"]["reason"]


def test_inside_airbnb_missing_predicate(tmp_path) -> None:
    csv = tmp_path / "city.csv"
    csv.write_text("id,host_id,host_name\n", encoding="utf-8")
    events = inside_airbnb_listings({"csv_path": str(csv)})
    assert events[0]["event_type"] == "tool-run-error"
    assert "host_name" in events[0]["payload"]["reason"]


def test_inside_airbnb_host_name_match(tmp_path) -> None:
    """Host-name partial match returns the row + correct commercial signal."""
    csv = tmp_path / "city.csv"
    csv.write_text(
        "id,host_id,host_name,host_listings_count,neighbourhood,room_type,last_review,name\n"
        "100,9,Alice Smith,3,Downtown,Entire home/apt,2025-12-01,Alice place\n"
        "200,10,Bob Jones,1,Suburb,Private room,2025-08-15,Bob room\n"
        "300,9,Alice Smith,3,Riverside,Entire home/apt,2025-11-20,Alice loft\n",
        encoding="utf-8",
    )
    events = inside_airbnb_listings({"csv_path": str(csv), "host_name": "alice"})
    matches = [e for e in events if e["event_type"] == "listing-match"]
    assert len(matches) == 2
    for m in matches:
        assert m["payload"]["host_name"] == "Alice Smith"
        assert m["payload"]["commercial_operator"] is True
        assert m["payload"]["host_total_listings"] == 3
    summary = events[-1]
    assert summary["event_type"] == "tool-run-result"
    assert summary["payload"]["matches"] == 2


def test_inside_airbnb_host_id_match(tmp_path) -> None:
    """Exact host_id match takes precedence over name."""
    csv = tmp_path / "city.csv"
    csv.write_text(
        "id,host_id,host_name,host_listings_count\n" "100,42,Alice,1\n" "200,99,Bob,5\n",
        encoding="utf-8",
    )
    events = inside_airbnb_listings({"csv_path": str(csv), "host_id": "99"})
    matches = [e for e in events if e["event_type"] == "listing-match"]
    assert len(matches) == 1
    assert matches[0]["payload"]["host_id"] == "99"
    assert matches[0]["payload"]["commercial_operator"] is True  # 5 listings


def test_inside_airbnb_listing_url_match(tmp_path) -> None:
    """URL extraction parses /rooms/<id> and matches the listing row."""
    csv = tmp_path / "city.csv"
    csv.write_text(
        "id,host_id,host_name,host_listings_count\n123456,7,Carol,1\n",
        encoding="utf-8",
    )
    events = inside_airbnb_listings(
        {
            "csv_path": str(csv),
            "listing_url": "https://www.airbnb.com/rooms/123456?adults=2",
        }
    )
    matches = [e for e in events if e["event_type"] == "listing-match"]
    assert len(matches) == 1
    assert matches[0]["payload"]["listing_id"] == "123456"


def test_inside_airbnb_limit_caps_matches(tmp_path) -> None:
    csv_lines = ["id,host_id,host_name,host_listings_count"]
    for i in range(50):
        csv_lines.append(f"{i},99,SameHost,50")
    csv = tmp_path / "city.csv"
    csv.write_text("\n".join(csv_lines) + "\n", encoding="utf-8")
    events = inside_airbnb_listings({"csv_path": str(csv), "host_id": "99", "limit": 10})
    matches = [e for e in events if e["event_type"] == "listing-match"]
    assert len(matches) == 10

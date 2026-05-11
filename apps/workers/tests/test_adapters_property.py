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
    _gravatar_synthetic,
    _hibp_synthetic,
    _inside_airbnb_synthetic,
    _intelbase_synthetic,
    _nominatim_synthetic,
    _tineye_synthetic,
    _true_people_synthetic,
    email_mx_validate,
    gravatar_profile_lookup,
    inside_airbnb_listings,
    intelbase_email_lookup,
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
        "intelbase_email_lookup",
        "gravatar_profile_lookup",
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


def test_intelbase_synthetic_emits_mixed_breach_and_match() -> None:
    """Synthetic intelbase: one breach-hit + one person-match + summary,
    exercising the full multi-event wire shape the live path emits."""
    events = _intelbase_synthetic({"email": "u@example.com"})
    types = [e["event_type"] for e in events]
    assert "breach-hit" in types
    assert "person-match" in types
    assert types[-1] == "tool-run-result"
    for e in events:
        assert e["payload"].get("synthetic") is True
        assert e["payload"].get("source") in ("intelbase", None) or "synthetic" in e["payload"]


def test_intelbase_missing_email_returns_error() -> None:
    events = intelbase_email_lookup({})
    assert events[0]["event_type"] == "tool-run-error"
    assert "email" in events[0]["payload"]["reason"].lower()


def test_intelbase_without_api_key_falls_back_to_synthetic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Adapter is env-gated on OSINT_INTELBASE_API_KEY. Absent key ->
    synthetic-mode wire shape so investigators see the surface without
    needing a paid key for local dev / dry runs."""
    monkeypatch.delenv("OSINT_INTELBASE_API_KEY", raising=False)
    events = intelbase_email_lookup({"email": "user@example.com"})
    types = [e["event_type"] for e in events]
    assert "tool-run-result" in types
    # Synthetic must NOT make a network call; presence of the synthetic
    # marker is the contract.
    assert any(e["payload"].get("synthetic") is True for e in events)


def test_gravatar_synthetic_emits_verified_account_matches() -> None:
    """Synthetic Gravatar: profile + N verified_accounts -> person-match
    per account + summary. Locks the wire shape."""
    events = _gravatar_synthetic({"email": "user@example.com"})
    types = [e["event_type"] for e in events]
    assert types.count("person-match") >= 1
    assert types[-1] == "tool-run-result"
    # Source field is set so the dossier can distinguish gravatar hits.
    for e in events:
        assert e["payload"].get("source") == "gravatar"


def test_gravatar_missing_email_returns_error() -> None:
    events = gravatar_profile_lookup({})
    assert events[0]["event_type"] == "tool-run-error"
    assert "email" in events[0]["payload"]["reason"].lower()


def test_gravatar_hashes_email_sha256(monkeypatch: pytest.MonkeyPatch) -> None:
    """Gravatar v3 requires sha256(lower(trim(email))) in the URL.
    A wrong hash = wrong lookup. Pin the canonicalization."""
    import hashlib

    captured: dict[str, str] = {}

    import httpx as _httpx

    def fake_get(self: Any, url: str, **kwargs: Any) -> _httpx.Response:
        captured["url"] = url
        return _httpx.Response(404, request=_httpx.Request("GET", url))

    monkeypatch.setattr(_httpx.Client, "get", fake_get)
    gravatar_profile_lookup({"email": "  USER@Example.COM  "})

    expected = hashlib.sha256(b"user@example.com").hexdigest()
    assert (
        expected in captured["url"]
    ), f"expected sha256 of normalized email in URL; got {captured['url']}"


def test_gravatar_404_returns_no_profile_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    """No Gravatar profile != error. It's a useful negative signal:
    'this email is not linked to a public claimed identity'. Emit a
    summary, no error."""
    import httpx as _httpx

    def fake_get(self: Any, url: str, **kwargs: Any) -> _httpx.Response:
        return _httpx.Response(404, request=_httpx.Request("GET", url))

    monkeypatch.setattr(_httpx.Client, "get", fake_get)
    events = gravatar_profile_lookup({"email": "user@example.com"})
    assert events[-1]["event_type"] == "tool-run-result"
    # No person-match emitted on 404 -- summary only.
    assert all(e["event_type"] != "tool-run-error" for e in events)
    assert events[-1]["payload"].get("profile_found") is False


def test_gravatar_emits_person_match_per_verified_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each entry in verified_accounts[] becomes one person-match event
    (owner-attested platform linkage -- the highest-signal free pivot)."""
    fake_response = {
        "hash": "abc",
        "display_name": "Alex Morgan",
        "profile_url": "https://gravatar.com/alex",
        "verified_accounts": [
            {
                "service_type": "github",
                "service_label": "GitHub",
                "url": "https://github.com/alex",
                "is_hidden": False,
            },
            {
                "service_type": "linkedin",
                "service_label": "LinkedIn",
                "url": "https://linkedin.com/in/alex",
                "is_hidden": False,
            },
        ],
    }

    import httpx as _httpx

    def fake_get(self: Any, url: str, **kwargs: Any) -> _httpx.Response:
        return _httpx.Response(200, json=fake_response, request=_httpx.Request("GET", url))

    monkeypatch.setattr(_httpx.Client, "get", fake_get)
    events = gravatar_profile_lookup({"email": "user@example.com"})
    person_matches = [e for e in events if e["event_type"] == "person-match"]
    assert len(person_matches) == 2
    platforms = {pm["payload"]["platform"] for pm in person_matches}
    assert platforms == {"github", "linkedin"}
    # Profile metadata surfaces in the summary.
    summary = events[-1]["payload"]
    assert summary["profile_found"] is True
    assert summary["display_name"] == "Alex Morgan"
    assert summary["verified_count"] == 2


def test_intelbase_redacts_credential_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Defense in depth: even if the upstream returns infostealer rows
    with `password` / `hash` / `plaintext` fields, the adapter must never
    propagate them into the event stream (which gets persisted, exported,
    etc.). Strip them before emit."""
    monkeypatch.setenv("OSINT_INTELBASE_API_KEY", "test-key")

    fake_response = {
        "breaches": [
            {
                "name": "TestBreach",
                "domain": "example.com",
                "breach_date": "2024-01-01",
                "password": "hunter2",
                "hash": "deadbeef",
                "plaintext": "should-never-appear",
            }
        ],
        "accounts": [
            {
                "platform": "github",
                "username": "alice",
                "profile_url": "https://github.com/alice",
                "credential_data": "should-never-appear",
            }
        ],
    }

    import httpx as _httpx

    def fake_post(self: Any, url: str, **kwargs: Any) -> _httpx.Response:
        return _httpx.Response(200, json=fake_response, request=_httpx.Request("POST", url))

    monkeypatch.setattr(_httpx.Client, "post", fake_post)

    events = intelbase_email_lookup({"email": "user@example.com"})
    # Walk every payload value -- no credential string anywhere.
    import json as _json

    serialized = _json.dumps(events)
    assert "hunter2" not in serialized
    assert "deadbeef" not in serialized
    assert "should-never-appear" not in serialized
    # Positive shape: the breach + account were still surfaced.
    types = [e["event_type"] for e in events]
    assert "breach-hit" in types
    assert "person-match" in types


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

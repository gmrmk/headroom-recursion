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
    _github_commit_email_synthetic,
    _gravatar_synthetic,
    _hibp_synthetic,
    _hudson_rock_synthetic,
    _inside_airbnb_synthetic,
    _intelbase_synthetic,
    _nominatim_synthetic,
    _tineye_synthetic,
    _true_people_synthetic,
    _user_scanner_synthetic,
    email_mx_validate,
    github_commit_email_search,
    gravatar_profile_lookup,
    hudson_rock_email_check,
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
        "github_commit_email_search",
        "hudson_rock_email_check",
        "user_scanner",
        "microsoft_partial_pivot",
        "linkedin_partial_pivot",
        "instagram_partial_pivot",
        "twitter_partial_pivot",
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


def test_github_commit_synthetic_emits_person_match() -> None:
    """Synthetic GitHub commit search: one person-match per repo + summary."""
    events = _github_commit_email_synthetic({"email": "u@example.com"})
    types = [e["event_type"] for e in events]
    assert "person-match" in types
    assert types[-1] == "tool-run-result"
    for e in events:
        assert e["payload"].get("source") == "github_commits"


def test_github_commit_missing_email_returns_error() -> None:
    events = github_commit_email_search({})
    assert events[0]["event_type"] == "tool-run-error"


def test_github_commit_emits_person_match_per_unique_repo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each unique repo touched by the email becomes one person-match.
    Same email committing to the same repo 50x = one event, not 50."""
    fake_response = {
        "total_count": 3,
        "items": [
            {
                "sha": "abc1",
                "html_url": "https://github.com/octo/repo1/commit/abc1",
                "commit": {
                    "author": {
                        "name": "Octo Cat",
                        "email": "u@example.com",
                        "date": "2024-03-01T12:00:00Z",
                    },
                    "message": "Fix",
                },
                "author": {
                    "login": "octocat",
                    "html_url": "https://github.com/octocat",
                },
                "repository": {"full_name": "octo/repo1", "private": False},
            },
            {
                "sha": "abc2",
                "html_url": "https://github.com/octo/repo1/commit/abc2",
                "commit": {
                    "author": {
                        "name": "Octo Cat",
                        "email": "u@example.com",
                        "date": "2024-03-02T12:00:00Z",
                    },
                    "message": "Fix more",
                },
                "author": {
                    "login": "octocat",
                    "html_url": "https://github.com/octocat",
                },
                "repository": {"full_name": "octo/repo1", "private": False},
            },
            {
                "sha": "def1",
                "html_url": "https://github.com/octo/repo2/commit/def1",
                "commit": {
                    "author": {
                        "name": "Octo Cat",
                        "email": "u@example.com",
                        "date": "2024-03-03T12:00:00Z",
                    },
                    "message": "Another repo",
                },
                "author": {
                    "login": "octocat",
                    "html_url": "https://github.com/octocat",
                },
                "repository": {"full_name": "octo/repo2", "private": False},
            },
        ],
    }

    import httpx as _httpx

    def fake_get(self: Any, url: str, **kwargs: Any) -> _httpx.Response:
        return _httpx.Response(200, json=fake_response, request=_httpx.Request("GET", url))

    monkeypatch.setattr(_httpx.Client, "get", fake_get)
    events = github_commit_email_search({"email": "u@example.com"})
    person_matches = [e for e in events if e["event_type"] == "person-match"]
    # 3 commits across 2 repos -> 2 person-matches.
    assert len(person_matches) == 2
    repos = {pm["payload"]["repo"] for pm in person_matches}
    assert repos == {"octo/repo1", "octo/repo2"}
    # Each should carry login + name + commit count for that repo.
    by_repo = {pm["payload"]["repo"]: pm["payload"] for pm in person_matches}
    assert by_repo["octo/repo1"]["commit_count"] == 2
    assert by_repo["octo/repo1"]["login"] == "octocat"
    assert by_repo["octo/repo1"]["author_name"] == "Octo Cat"


def test_github_commit_403_rate_limit_returns_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """403 / 429 are rate-limit signals from GitHub. Surface as tool-run-
    error so the investigator sees it (not as silent empty result)."""
    import httpx as _httpx

    def fake_get(self: Any, url: str, **kwargs: Any) -> _httpx.Response:
        return _httpx.Response(403, request=_httpx.Request("GET", url))

    monkeypatch.setattr(_httpx.Client, "get", fake_get)
    events = github_commit_email_search({"email": "u@example.com"})
    assert events[0]["event_type"] == "tool-run-error"
    assert "403" in events[0]["payload"]["reason"]


def test_github_commit_uses_pat_when_env_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OSINT_GITHUB_PAT raises 10/min unauthed -> 30/min authed.
    When set, the Authorization header must be on the request."""
    monkeypatch.setenv("OSINT_GITHUB_PAT", "ghp_synthetic")

    captured: dict[str, Any] = {}

    import httpx as _httpx

    def fake_get(self: Any, url: str, **kwargs: Any) -> _httpx.Response:
        captured["headers"] = dict(self.headers)
        return _httpx.Response(
            200,
            json={"total_count": 0, "items": []},
            request=_httpx.Request("GET", url),
        )

    monkeypatch.setattr(_httpx.Client, "get", fake_get)
    github_commit_email_search({"email": "u@example.com"})
    # Header key is lowercased by httpx; check case-insensitively.
    headers_lower = {k.lower(): v for k, v in captured["headers"].items()}
    assert "authorization" in headers_lower
    assert "ghp_synthetic" in headers_lower["authorization"]


def test_hudson_rock_synthetic_emits_breach_hit_and_summary() -> None:
    """Synthetic Hudson Rock: one breach-hit per stealer + summary."""
    events = _hudson_rock_synthetic({"email": "u@example.com"})
    types = [e["event_type"] for e in events]
    assert "breach-hit" in types
    assert types[-1] == "tool-run-result"
    for e in events:
        assert e["payload"].get("source") == "hudson_rock"


def test_hudson_rock_missing_email_returns_error() -> None:
    events = hudson_rock_email_check({})
    assert events[0]["event_type"] == "tool-run-error"


def test_hudson_rock_emits_breach_hit_per_stealer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each entry in stealers[] becomes a breach-hit event with infostealer
    metadata (machine name, OS, malware path, date). Top-level totals go
    in the summary."""
    fake_response = {
        "message": "Email is associated with infected computers.",
        "total_corporate_services": 50,
        "total_user_services": 1200,
        "stealers": [
            {
                "total_corporate_services": 30,
                "total_user_services": 700,
                "date_compromised": "2024-06-01T00:00:00.000Z",
                "computer_name": "DESKTOP-X (alice)",
                "operating_system": "Windows 11",
                "malware_path": "C:\\Users\\alice\\AppData\\...\\stealer.exe",
                "antiviruses": ["Windows Defender"],
                "ip": "1.2.**.*",
                "top_passwords": ["A*******1", "B*******2"],
                "top_logins": ["a***@example.com", "b***@example.com"],
            },
            {
                "total_corporate_services": 20,
                "total_user_services": 500,
                "date_compromised": "2024-08-15T00:00:00.000Z",
                "computer_name": "LAPTOP-Y (bob)",
                "operating_system": "Windows 10",
                "malware_path": "Not Found",
                "antiviruses": [],
                "ip": "Not Found",
                "top_passwords": [],
                "top_logins": [],
            },
        ],
    }

    import httpx as _httpx

    def fake_get(self: Any, url: str, **kwargs: Any) -> _httpx.Response:
        return _httpx.Response(200, json=fake_response, request=_httpx.Request("GET", url))

    monkeypatch.setattr(_httpx.Client, "get", fake_get)
    events = hudson_rock_email_check({"email": "u@example.com"})
    breach_hits = [e for e in events if e["event_type"] == "breach-hit"]
    assert len(breach_hits) == 2
    # Compromise metadata preserved.
    computers = {bh["payload"]["computer_name"] for bh in breach_hits}
    assert "DESKTOP-X (alice)" in computers
    assert "LAPTOP-Y (bob)" in computers
    # Summary carries top-level totals.
    summary = events[-1]["payload"]
    assert summary["stealer_count"] == 2
    assert summary["total_corporate_services"] == 50
    assert summary["total_user_services"] == 1200


def test_hudson_rock_strips_credential_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Belt-and-suspenders over Hudson Rock's own partial redaction:
    even if they ever start returning full top_passwords / top_logins,
    our `_redact_credentials` recursion must strip them. Same contract
    as the IntelBase adapter."""
    fake_response = {
        "stealers": [
            {
                "computer_name": "DESKTOP-X",
                "top_passwords": ["PLAIN_PASSWORD_LEAKED"],
                "top_logins": ["user@example.com"],
                "password_hash": "deadbeef",
                "credential_data": "should-never-appear",
            }
        ],
    }

    import httpx as _httpx

    def fake_get(self: Any, url: str, **kwargs: Any) -> _httpx.Response:
        return _httpx.Response(200, json=fake_response, request=_httpx.Request("GET", url))

    monkeypatch.setattr(_httpx.Client, "get", fake_get)
    events = hudson_rock_email_check({"email": "u@example.com"})
    import json as _json

    serialized = _json.dumps(events)
    assert "PLAIN_PASSWORD_LEAKED" not in serialized
    assert "deadbeef" not in serialized
    assert "should-never-appear" not in serialized
    # The non-credential metadata still surfaced.
    breach_hits = [e for e in events if e["event_type"] == "breach-hit"]
    assert len(breach_hits) == 1
    assert breach_hits[0]["payload"]["computer_name"] == "DESKTOP-X"


def test_hudson_rock_empty_stealers_returns_summary_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No infostealer matches != error. Useful negative signal:
    'this email does not appear in any known stealer dump'."""
    import httpx as _httpx

    def fake_get(self: Any, url: str, **kwargs: Any) -> _httpx.Response:
        return _httpx.Response(
            200,
            json={"stealers": [], "total_corporate_services": 0, "total_user_services": 0},
            request=_httpx.Request("GET", url),
        )

    monkeypatch.setattr(_httpx.Client, "get", fake_get)
    events = hudson_rock_email_check({"email": "u@example.com"})
    assert all(e["event_type"] != "breach-hit" for e in events)
    assert events[-1]["event_type"] == "tool-run-result"
    assert events[-1]["payload"]["stealer_count"] == 0


def test_partial_pivot_synthetic_emits_partial_per_platform() -> None:
    """Each of the four partial-recovery adapters has a synthetic mode
    that emits the wire shape the live path produces: one person-match
    with `<platform>_partial` source + redacted partial metadata +
    account_exists, and a tool-run-result summary. Naomi #3: raw partial
    values must NOT appear in the default emit shape."""
    from osint_goblin_workers.adapters import get_registry

    registry = get_registry()
    for platform in ("microsoft", "linkedin", "instagram", "twitter"):
        adapter_id = f"{platform}_partial_pivot"
        entry = registry.get(adapter_id)
        assert entry is not None, f"{adapter_id} not registered"
        assert entry.synthetic_mode is not None
        events = entry.synthetic_mode({"target": "user@example.com"})
        types = [e["event_type"] for e in events]
        assert "person-match" in types, f"{platform}: missing person-match"
        assert types[-1] == "tool-run-result", f"{platform}: missing summary"
        for e in events:
            if e["event_type"] != "tool-run-accepted":
                assert e["payload"].get("source") == f"{platform}_partial"
        # Naomi #3 redaction: the default in-process synthetic carries
        # metadata, not the raw partial string. The summary records
        # values_kept=False to mark this.
        person_match = next(e for e in events if e["event_type"] == "person-match")
        assert (
            "email_partial_meta" in person_match["payload"]
        ), f"{platform}: redacted metadata missing"
        assert (
            "email_partial" not in person_match["payload"]
        ), f"{platform}: raw email_partial leaked in default emit (Naomi #3)"
        summary = events[-1]["payload"]
        assert (
            summary.get("values_kept") is False
        ), f"{platform}: summary must mark values_kept=False by default"
        assert summary.get("partials_visible_count") == 1


def test_redact_email_partial_keeps_first_length_domain() -> None:
    """Naomi #3 redaction shape: `j***@gmail.com` -> first/local_length/domain.
    The raw asterisks-leak gets dropped in favor of structured metadata."""
    import importlib.util
    from pathlib import Path

    wrapper_path = (
        Path(__file__).resolve().parents[3] / "adapters" / "partial_recovery" / "wrapper.py"
    )
    spec = importlib.util.spec_from_file_location("partial_wrapper", wrapper_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    meta = mod._redact_email_partial("j***@gmail.com")
    assert meta["first"] == "j"
    assert meta["domain"] == "gmail.com"
    assert meta["local_length"] == 4
    assert meta["raw_length"] == len("j***@gmail.com")


def test_redact_phone_partial_keeps_last_digits_length() -> None:
    """Phone partial 'ending in 1234' style -> last_digits + implied length."""
    import importlib.util
    from pathlib import Path

    wrapper_path = (
        Path(__file__).resolve().parents[3] / "adapters" / "partial_recovery" / "wrapper.py"
    )
    spec = importlib.util.spec_from_file_location("partial_wrapper", wrapper_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    meta = mod._redact_phone_partial("+1 *** *** 1234")
    assert meta["last_digits"] == "1234"
    assert meta["implied_length"] >= 7  # +1 + 4 digits + asterisks counted


def test_eu_guardrail_refuses_without_lawful_basis_flag(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Naomi #5: OSINT_PARTIAL_REGION=EU set without _LAWFUL_BASIS_CONFIRMED
    must refuse (SystemExit with non-zero code + tool-run-error emit)."""
    import importlib.util
    from pathlib import Path

    wrapper_path = (
        Path(__file__).resolve().parents[3] / "adapters" / "partial_recovery" / "wrapper.py"
    )
    spec = importlib.util.spec_from_file_location("partial_wrapper", wrapper_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    monkeypatch.setenv("OSINT_PARTIAL_REGION", "EU")
    monkeypatch.delenv("OSINT_PARTIAL_LAWFUL_BASIS_CONFIRMED", raising=False)
    with pytest.raises(SystemExit) as exc_info:
        mod._check_region_guardrail()
    assert exc_info.value.code == 5
    out = capsys.readouterr().out
    assert "lawful" in out.lower() or "legitimate" in out.lower()


def test_eu_guardrail_passes_with_lawful_basis_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Setting OSINT_PARTIAL_LAWFUL_BASIS_CONFIRMED=1 unblocks the EU path."""
    import importlib.util
    from pathlib import Path

    wrapper_path = (
        Path(__file__).resolve().parents[3] / "adapters" / "partial_recovery" / "wrapper.py"
    )
    spec = importlib.util.spec_from_file_location("partial_wrapper", wrapper_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    monkeypatch.setenv("OSINT_PARTIAL_REGION", "EU")
    monkeypatch.setenv("OSINT_PARTIAL_LAWFUL_BASIS_CONFIRMED", "1")
    # No SystemExit raised.
    mod._check_region_guardrail()


def test_eu_guardrail_noop_for_non_eu_region(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-EU regions don't require the lawful-basis flag."""
    import importlib.util
    from pathlib import Path

    wrapper_path = (
        Path(__file__).resolve().parents[3] / "adapters" / "partial_recovery" / "wrapper.py"
    )
    spec = importlib.util.spec_from_file_location("partial_wrapper", wrapper_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    monkeypatch.setenv("OSINT_PARTIAL_REGION", "US")
    monkeypatch.delenv("OSINT_PARTIAL_LAWFUL_BASIS_CONFIRMED", raising=False)
    mod._check_region_guardrail()  # no raise


def test_no_audit_log_written_on_live_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pytest.TempPathFactory,
) -> None:
    """Logless contract (user directive 2026-05-11): no audit log
    function exists in the wrapper, and no `partial-pivots-audit`
    directory is ever created. Target data flows through the event
    stream to the one-shot report ONLY, never to a side channel."""
    import importlib.util
    from pathlib import Path

    wrapper_path = (
        Path(__file__).resolve().parents[3] / "adapters" / "partial_recovery" / "wrapper.py"
    )
    spec = importlib.util.spec_from_file_location("partial_wrapper", wrapper_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    # No audit function exists at all.
    assert not hasattr(
        mod, "_audit_log"
    ), "audit log removed per logless contract; do not re-introduce"

    # The data-root override should never spawn a partial-pivots-audit
    # subdir from the wrapper's other code paths either.
    data_root = Path(str(tmp_path)) / "data"
    monkeypatch.setenv("OSINT_DATA_ROOT", str(data_root))
    # Touch the rate-limit path to confirm THAT is the only thing the
    # wrapper writes (and it carries zero target data).
    monkeypatch.delenv("OSINT_ADAPTER_MODE", raising=False)
    mod._enforce_rate_limit("instagram")
    rate_limit_dir = data_root / "partial-pivots-rate-limit"
    audit_dir = data_root / "partial-pivots-audit"
    assert rate_limit_dir.exists(), "rate-limit lockfile should exist"
    assert not audit_dir.exists(), "audit dir must never be created -- logless contract"
    # The rate-limit lockfile carries platform + timestamp only.
    contents = (rate_limit_dir / "instagram.last").read_text()
    assert "@" not in contents, "lockfile must contain zero target data"


def test_user_scanner_synthetic_emits_person_match_and_summary() -> None:
    """user-scanner synthetic locks the subprocess wire shape: one
    person-match per found platform + tool-run-result summary with
    checked/found/errored counts."""
    events = _user_scanner_synthetic({"email": "u@example.com"})
    types = [e["event_type"] for e in events]
    assert "person-match" in types
    assert types[-1] == "tool-run-result"
    for e in events:
        assert e["payload"].get("source") == "user_scanner"
    summary = events[-1]["payload"]
    assert "checked" in summary
    assert "found" in summary
    assert "errored" in summary


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

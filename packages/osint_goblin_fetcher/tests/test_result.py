"""Unit tests for osint_goblin_fetcher.result."""

from __future__ import annotations

import pytest
from osint_goblin_fetcher.result import (
    CF_INTERSTITIAL_MARKERS,
    CF_INTERSTITIAL_STATUSES,
    FetchResult,
    is_interstitial,
)


def _make(*, status: int = 200, body_text: str = "", error: str | None = None) -> FetchResult:
    return FetchResult(
        url="https://example.com/",
        final_url="https://example.com/",
        status=status,
        headers={},
        body_bytes=b"",
        body_text=body_text,
        tier="fetcher",
        elapsed_ms=100,
        error=error,
    )


def test_fetch_result_immutable() -> None:
    r = _make()
    with pytest.raises((AttributeError, TypeError)):
        r.status = 500  # type: ignore[misc]


def test_ok_true_on_200() -> None:
    assert _make(status=200).ok


def test_ok_false_on_error() -> None:
    assert _make(status=0, error="ConnectionError").ok is False


def test_ok_false_on_zero_status_no_error() -> None:
    """ok requires positive status AND no error."""
    assert _make(status=0).ok is False


def test_is_interstitial_403() -> None:
    assert is_interstitial(_make(status=403)) is True


def test_is_interstitial_503() -> None:
    assert is_interstitial(_make(status=503)) is True


def test_is_interstitial_marker_just_a_moment() -> None:
    body = "<html>...Just a moment...</html>"
    assert is_interstitial(_make(status=200, body_text=body)) is True


def test_is_interstitial_marker_cf_challenge() -> None:
    body = '<div class="cf-challenge"></div>'
    assert is_interstitial(_make(status=200, body_text=body)) is True


def test_is_interstitial_clean_200() -> None:
    assert is_interstitial(_make(status=200, body_text="<html>real content</html>")) is False


def test_is_interstitial_empty_body() -> None:
    assert is_interstitial(_make(status=200, body_text="")) is False


def test_interstitial_markers_frozen() -> None:
    """The marker tuple is intentionally tight; do not regress to a wider set
    without acknowledging the false-positive cost (6-10x latency)."""
    assert len(CF_INTERSTITIAL_MARKERS) == 6
    assert frozenset({403, 503}) == CF_INTERSTITIAL_STATUSES

"""Unit tests for osint_goblin_fetcher.facade.

Uses mocked Scrapling page objects -- no network. Validates the 3-shape
normalization (Item 1 cross-cutting finding) without needing live targets.
"""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest
from osint_goblin_fetcher.facade import _normalize, afetch, fetch
from osint_goblin_fetcher.result import FetchResult

# --- _normalize: the three Adaptor shapes ---


class _Tier1Page:
    """Fetcher (curl_cffi) tier: .text returns rendered HTML."""

    status = 200
    url = "https://example.com/final"
    headers: dict[str, str] = {"content-type": "text/html"}  # noqa: RUF012
    body = b"<html>tier 1 body</html>"
    text = "<html>tier 1 body</html>"


class _Tier2Page:
    """DynamicFetcher tier: .text is empty bytes; html lives on .html_content."""

    status = 200
    url = "https://example.com/final"
    headers: dict[str, str] = {"content-type": "text/html"}  # noqa: RUF012
    body = None
    text = b""
    html_content = "<html>tier 2 rendered</html>"


class _Tier3Page:
    """StealthyFetcher tier: same shape as Tier 2."""

    status = 200
    url = "https://example.com/final"
    headers: dict[str, str] = {"content-type": "text/html"}  # noqa: RUF012
    body = None
    text = None
    html_content = "<html>tier 3 stealthy</html>"


def test_normalize_tier_1_text_attr() -> None:
    r = _normalize(_Tier1Page(), tier="fetcher", url="https://example.com", elapsed_ms=100)
    assert r.body_text == "<html>tier 1 body</html>"
    assert r.body_bytes == b"<html>tier 1 body</html>"
    assert r.tier == "fetcher"
    assert r.status == 200


def test_normalize_tier_2_html_content_fallback() -> None:
    """Item 1 fix: empty .text -> fall back to .html_content."""
    r = _normalize(_Tier2Page(), tier="dynamic", url="https://example.com", elapsed_ms=200)
    assert r.body_text == "<html>tier 2 rendered</html>"
    assert r.tier == "dynamic"


def test_normalize_tier_3_html_content_fallback() -> None:
    r = _normalize(_Tier3Page(), tier="stealthy", url="https://example.com", elapsed_ms=300)
    assert r.body_text == "<html>tier 3 stealthy</html>"
    assert r.tier == "stealthy"


def test_normalize_never_raises_on_missing_attrs() -> None:
    """Missing everything -> empty FetchResult, not exception."""
    empty = types.SimpleNamespace()
    r = _normalize(empty, tier="fetcher", url="https://example.com", elapsed_ms=1)
    assert r.body_text == ""
    assert r.body_bytes == b""
    assert r.status == 0


def test_normalize_preserves_final_url_on_redirect() -> None:
    r = _normalize(_Tier1Page(), tier="fetcher", url="https://example.com/", elapsed_ms=1)
    assert r.final_url == "https://example.com/final"


# --- fetch(): catches exceptions, returns error-tagged FetchResult ---


def test_fetch_unknown_tier_raises_caught_returned_as_error() -> None:
    r: FetchResult = fetch("https://example.com", tier="camoufox")  # type: ignore[arg-type]
    assert r.error == "NotImplementedError"
    assert r.status == 0
    assert r.ok is False


def test_fetch_dispatches_to_scrapling_fetcher(monkeypatch: pytest.MonkeyPatch) -> None:
    """fetch(tier='fetcher') -> Scrapling Fetcher.get."""
    fake_fetchers = types.SimpleNamespace()

    class _FakeFetcher:
        @staticmethod
        def get(url: str, **_: Any) -> _Tier1Page:
            return _Tier1Page()

    fake_fetchers.Fetcher = _FakeFetcher  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "scrapling", types.SimpleNamespace())
    monkeypatch.setitem(sys.modules, "scrapling.fetchers", fake_fetchers)

    r = fetch("https://example.com", tier="fetcher")
    assert r.ok
    assert r.status == 200
    assert r.body_text == "<html>tier 1 body</html>"


# --- afetch(): async wrapper ---


@pytest.mark.asyncio
async def test_afetch_returns_same_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    """afetch is just asyncio.to_thread around fetch."""
    fake_fetchers = types.SimpleNamespace()

    class _FakeFetcher:
        @staticmethod
        def get(url: str, **_: Any) -> _Tier1Page:
            return _Tier1Page()

    fake_fetchers.Fetcher = _FakeFetcher  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "scrapling", types.SimpleNamespace())
    monkeypatch.setitem(sys.modules, "scrapling.fetchers", fake_fetchers)

    r = await afetch("https://example.com", tier="fetcher")
    assert r.ok
    assert r.body_text == "<html>tier 1 body</html>"

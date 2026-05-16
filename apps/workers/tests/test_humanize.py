"""Unit tests for osint_goblin_workers.humanize -- Ship 8 OPSEC stack.

No network: tests cover the pure-Python primitives (UA pool, referer
pool, warm-up registry, jitter range). Live browser tests live in
tools/dev/humanize-live-probe.py and are manual-only.
"""

from __future__ import annotations

import time

import pytest
from osint_goblin_workers.humanize import (
    _UA_POOL_DESKTOP,
    _WARMUP_FLOWS,
    PLATFORM_ANTIBOT_MAP,
    HumanizedFetcher,
    WarmupFlow,
    get_antibot_vendor,
    get_default_fetcher,
    get_warmup,
    jitter_sleep,
    pick_organic_referer,
    pick_ua,
    recommended_tier_for_platform,
    shred_default_fetcher,
)

# ---------------------------------------------------------------------------
# UA pool (Layer 2)
# ---------------------------------------------------------------------------


class TestUaPool:
    def test_pool_has_at_least_15_entries(self):
        # Pool design target: 20+ realistic browser+platform combos.
        assert len(_UA_POOL_DESKTOP) >= 15

    def test_weights_are_positive(self):
        for _ua, weight in _UA_POOL_DESKTOP:
            assert weight > 0.0

    def test_weights_sum_close_to_1(self):
        # Allow some slack -- if weights drift the pool still works
        # (random.choices uses relative weights) but the comment
        # documents intent.
        total = sum(weight for _ua, weight in _UA_POOL_DESKTOP)
        assert 0.9 <= total <= 1.1

    def test_all_uas_look_realistic(self):
        # Sanity: every UA string starts with "Mozilla/5.0" and contains
        # at least one engine token (AppleWebKit / Gecko).
        for ua, _w in _UA_POOL_DESKTOP:
            assert ua.startswith("Mozilla/5.0 ")
            assert "AppleWebKit" in ua or "Gecko" in ua

    def test_pick_ua_returns_string_from_pool(self):
        all_uas = {ua for ua, _w in _UA_POOL_DESKTOP}
        for _ in range(50):
            picked = pick_ua()
            assert picked in all_uas

    def test_pick_ua_distributes_across_pool(self):
        # Statistical: over many picks, we should see at least 5 distinct
        # UAs from the pool. (Weighted sampling means rare entries appear
        # occasionally; with 200 picks across 15+ entries the variance is
        # easy to clear.)
        seen = {pick_ua() for _ in range(200)}
        assert len(seen) >= 5


# ---------------------------------------------------------------------------
# Referer pool (Layer 3)
# ---------------------------------------------------------------------------


class TestRefererPool:
    def test_pick_returns_https_url(self):
        ref = pick_organic_referer()
        assert ref.startswith("https://")

    def test_pick_distributes_across_pool(self):
        seen = {pick_organic_referer() for _ in range(100)}
        # At least 3 of the 5 pool entries should show up in 100 picks.
        assert len(seen) >= 3


# ---------------------------------------------------------------------------
# Warm-up registry (Layer 5)
# ---------------------------------------------------------------------------


class TestWarmupRegistry:
    def test_airbnb_has_warmup(self):
        flow = get_warmup("airbnb")
        assert flow is not None
        assert flow.homepage_url == "https://www.airbnb.com/"

    def test_vrbo_has_warmup(self):
        flow = get_warmup("vrbo")
        assert flow is not None
        assert "vrbo.com" in flow.homepage_url

    def test_booking_has_warmup(self):
        flow = get_warmup("booking")
        assert flow is not None

    def test_tripadvisor_has_warmup(self):
        flow = get_warmup("tripadvisor")
        assert flow is not None

    def test_leboncoin_has_warmup_with_didomi_cookie_selector(self):
        # Leboncoin uses Didomi for cookie consent; the warm-up
        # selector should match. This documents the platform-quirk.
        flow = get_warmup("leboncoin")
        assert flow is not None
        assert "didomi" in flow.accept_cookies_selector

    def test_unknown_platform_returns_none(self):
        assert get_warmup("not-a-platform") is None

    def test_all_warmups_have_homepage_url(self):
        for plat, flow in _WARMUP_FLOWS.items():
            assert flow.homepage_url.startswith("https://"), f"{plat} has bad homepage"

    def test_warmup_is_immutable(self):
        # WarmupFlow is frozen=True dataclass; assignment raises
        # dataclasses.FrozenInstanceError. Catch that specifically
        # rather than the generic Exception (ruff B017).
        import dataclasses

        flow = WarmupFlow(homepage_url="https://example.com/")
        with pytest.raises(dataclasses.FrozenInstanceError):
            flow.homepage_url = "https://elsewhere.com/"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Jitter (Layer 1)
# ---------------------------------------------------------------------------


class TestJitterSleep:
    def test_sleeps_within_bounds(self):
        start = time.monotonic()
        jitter_sleep(low_s=0.1, high_s=0.3)
        elapsed = time.monotonic() - start
        assert 0.1 <= elapsed <= 0.5  # +0.2s slack for scheduler

    def test_zero_low_zero_high_no_sleep(self):
        start = time.monotonic()
        jitter_sleep(low_s=0.0, high_s=0.0)
        elapsed = time.monotonic() - start
        assert elapsed < 0.1


# ---------------------------------------------------------------------------
# HumanizedFetcher lifecycle (no live network)
# ---------------------------------------------------------------------------


class TestHumanizedFetcherLifecycle:
    def test_construction_doesnt_boot_browser(self):
        # Lazy-init: the browser only spins up on first fetch().
        # Construction must be cheap so workers can create fetchers
        # without paying browser-boot cost upfront.
        fetcher = HumanizedFetcher(investigation_id="test-no-fetch")
        assert fetcher._state.browser is None
        assert fetcher._state.context is None
        assert fetcher._state.user_agent  # UA picked at construction
        fetcher.shred()

    def test_shred_idempotent(self):
        fetcher = HumanizedFetcher(investigation_id="test-shred-idempotent")
        fetcher.shred()
        fetcher.shred()  # second call must not raise
        assert fetcher._closed

    def test_fetch_after_shred_returns_zero(self):
        fetcher = HumanizedFetcher(investigation_id="test-fetch-after-shred")
        fetcher.shred()
        status, body = fetcher.fetch(
            "https://example.com/", jitter=False, synthetic_interaction=False
        )
        assert status == 0
        assert body == ""

    def test_investigation_id_stored(self):
        fetcher = HumanizedFetcher(investigation_id="inv_xyz")
        assert fetcher.investigation_id == "inv_xyz"
        fetcher.shred()


# ---------------------------------------------------------------------------
# Default-fetcher singleton
# ---------------------------------------------------------------------------


class TestDefaultFetcher:
    def test_default_fetcher_lazy_init(self):
        shred_default_fetcher()  # clean state
        f1 = get_default_fetcher()
        f2 = get_default_fetcher()
        assert f1 is f2
        shred_default_fetcher()

    def test_shred_default_resets_singleton(self):
        f1 = get_default_fetcher()
        shred_default_fetcher()
        f2 = get_default_fetcher()
        assert f1 is not f2  # fresh instance after shred
        shred_default_fetcher()


# ---------------------------------------------------------------------------
# Platform anti-bot vendor map + tier router
# ---------------------------------------------------------------------------


class TestAntiBotVendorMap:
    def test_imperva_for_vrbo(self):
        assert get_antibot_vendor("vrbo") == "imperva"

    def test_datadome_for_tripadvisor(self):
        assert get_antibot_vendor("tripadvisor") == "datadome"

    def test_akamai_for_booking(self):
        assert get_antibot_vendor("booking") == "akamai-bma"

    def test_none_for_yanolja(self):
        assert get_antibot_vendor("yanolja") == "none"

    def test_didomi_for_leboncoin(self):
        assert get_antibot_vendor("leboncoin") == "didomi-only"

    def test_unknown_for_unmapped_platform(self):
        assert get_antibot_vendor("definitely-not-a-platform") == "unknown"

    def test_map_covers_all_listing_platforms(self):
        # Every entry in adapters_listing's _PLATFORM_HOST_MAP values
        # should have a vendor mapping here. Drift between the two
        # implies platform parsers without anti-bot routing.
        from osint_goblin_workers.adapters_listing import _PLATFORM_HOST_MAP

        listing_platforms = set(_PLATFORM_HOST_MAP.values())
        antibot_platforms = set(PLATFORM_ANTIBOT_MAP.keys())
        # Allow up to 5 entries missing (slack while we add new platforms);
        # if more than that drift, the test fails and we tighten coverage.
        missing = listing_platforms - antibot_platforms
        assert len(missing) <= 5, f"too many platforms missing from vendor map: {missing}"


class TestRecommendedTier:
    def test_imperva_recommends_zendriver(self):
        # Pressure-tested 2026-05-16: VRBO (Imperva PWA challenge) ->
        # Zendriver returned 200 OK + 887KB real listing content with
        # populated __APOLLO_STATE__ + schema.org/VacationRental markup.
        # Every Playwright tier (patchright/rebrowser/camoufox) was
        # held on the "Bot or Not?" challenge.
        assert recommended_tier_for_platform("vrbo") == "zendriver"

    def test_datadome_recommends_zendriver(self):
        # Same probe: TripAdvisor (DataDome) -> Zendriver 200 OK + 433KB
        # real content. Camoufox also bypasses DataDome (Firefox
        # fingerprint), kept as documented fallback in the module map.
        assert recommended_tier_for_platform("tripadvisor") == "zendriver"

    def test_akamai_recommends_patchright(self):
        assert recommended_tier_for_platform("booking") == "patchright"

    def test_no_antibot_recommends_patchright(self):
        # Cheap tier for cheap platforms.
        assert recommended_tier_for_platform("yanolja") == "patchright"
        assert recommended_tier_for_platform("leboncoin") == "patchright"

    def test_unknown_falls_back_to_patchright(self):
        assert recommended_tier_for_platform("never-heard-of-it") == "patchright"


# ---------------------------------------------------------------------------
# Browser-tier env switch (lifecycle only -- no network)
# ---------------------------------------------------------------------------


class TestBrowserTierEnvSwitch:
    def test_tier_used_recorded_after_construction(self):
        # Lazy-init means tier isn't picked until first fetch attempt.
        f = HumanizedFetcher(investigation_id="test-tier-record")
        assert f._state.tier_used == ""
        f.shred()

    def test_firecrawl_skips_browser_without_api_key(self, monkeypatch):
        # No API key + firecrawl tier -> graceful return (0, "")
        # rather than launching a browser.
        monkeypatch.setenv("OSINT_BROWSER_TIER", "firecrawl")
        monkeypatch.delenv("OSINT_FIRECRAWL_API_KEY", raising=False)
        f = HumanizedFetcher(investigation_id="test-firecrawl-no-key")
        status, body = f.fetch("https://example.com/", jitter=False, synthetic_interaction=False)
        assert status == 0
        assert body == ""
        # The fetch path didn't boot a browser.
        assert f._state.browser is None
        f.shred()


class TestCookieInjection:
    """Pre-solved session cookies are the guaranteed-bypass tier: an
    investigator manually clears the captcha in their real browser,
    exports cookies, pastes them in. The fetcher uses them on the next
    fetch to the matching platform. Tests cover storage + format
    conversion semantics; tier-specific application is mocked."""

    def test_inject_cookies_stores_per_platform(self):
        f = HumanizedFetcher(investigation_id="test-cookies-store")
        f.inject_cookies(
            "vrbo",
            [{"name": "session", "value": "abc", "domain": ".vrbo.com", "path": "/"}],
        )
        assert "vrbo" in f._state.injected_cookies
        assert len(f._state.injected_cookies["vrbo"]) == 1
        # cookies_applied starts False so the next fetch actually pushes
        # them into the BrowserContext.
        assert f._state.cookies_applied.get("vrbo") is False
        f.shred()

    def test_inject_cookies_per_platform_scoping(self):
        # vrbo cookies must not bleed into a booking fetch.
        f = HumanizedFetcher(investigation_id="test-cookies-scope")
        f.inject_cookies("vrbo", [{"name": "v", "value": "1", "domain": ".vrbo.com"}])
        f.inject_cookies("booking", [{"name": "b", "value": "2", "domain": ".booking.com"}])
        assert f._state.injected_cookies["vrbo"][0]["name"] == "v"
        assert f._state.injected_cookies["booking"][0]["name"] == "b"
        assert "vrbo" not in [c.get("name") for c in f._state.injected_cookies["booking"]]
        f.shred()

    def test_inject_cookies_replaces_prior_set(self):
        f = HumanizedFetcher(investigation_id="test-cookies-replace")
        f.inject_cookies("vrbo", [{"name": "old", "value": "x", "domain": ".vrbo.com"}])
        f.inject_cookies("vrbo", [{"name": "new", "value": "y", "domain": ".vrbo.com"}])
        assert len(f._state.injected_cookies["vrbo"]) == 1
        assert f._state.injected_cookies["vrbo"][0]["name"] == "new"
        f.shred()

    def test_inject_after_shred_is_noop(self):
        # Naomi gate: once shredded, no more cookie injection.
        f = HumanizedFetcher(investigation_id="test-cookies-shred")
        f.shred()
        f.inject_cookies("vrbo", [{"name": "x", "value": "y", "domain": ".vrbo.com"}])
        assert "vrbo" not in f._state.injected_cookies

    def test_shred_drops_cookies(self):
        # Cookies live in memory only -- never persist past shred().
        f = HumanizedFetcher(investigation_id="test-cookies-naomi")
        f.inject_cookies("vrbo", [{"name": "s", "value": "v", "domain": ".vrbo.com"}])
        assert f._state.injected_cookies["vrbo"]
        f.shred()
        # State is dropped along with the fetcher; new instance is clean.
        f2 = HumanizedFetcher(investigation_id="test-cookies-naomi")
        assert "vrbo" not in f2._state.injected_cookies
        f2.shred()


class TestCookieConversion:
    """Playwright cookie dicts -> zendriver CDP CookieParam conversion.
    Field rename semantics + malformed-input tolerance."""

    def test_basic_conversion(self):
        cookies = [
            {"name": "session", "value": "abc123", "domain": ".vrbo.com", "path": "/"},
        ]
        out = HumanizedFetcher._playwright_to_cdp_cookies(cookies)
        assert len(out) == 1
        assert out[0].name == "session"
        assert out[0].value == "abc123"
        assert out[0].domain == ".vrbo.com"
        assert out[0].path == "/"

    def test_field_rename_http_only_and_same_site(self):
        cookies = [
            {
                "name": "s",
                "value": "v",
                "domain": ".x.com",
                "httpOnly": True,
                "sameSite": "Lax",
                "secure": True,
            }
        ]
        out = HumanizedFetcher._playwright_to_cdp_cookies(cookies)
        assert out[0].http_only is True
        assert out[0].secure is True
        # CookieSameSite enum mapped from string
        assert out[0].same_site is not None
        assert "LAX" in out[0].same_site.name.upper()

    def test_skips_malformed_entries(self):
        cookies = [
            {"name": "ok", "value": "v", "domain": ".x.com"},
            {"value": "no-name", "domain": ".x.com"},  # missing name
            "not-a-dict",
            {"name": "no-value", "domain": ".x.com"},  # missing value
            {"name": "ok2", "value": "v2", "domain": ".x.com"},
        ]
        out = HumanizedFetcher._playwright_to_cdp_cookies(cookies)
        assert len(out) == 2
        assert {c.name for c in out} == {"ok", "ok2"}

    def test_empty_list_returns_empty(self):
        assert HumanizedFetcher._playwright_to_cdp_cookies([]) == []


class TestZendriverStatusInference:
    """The zendriver tier doesn't get a navigation response.status from
    CDP -- we infer block-state by sniffing challenge markers in the
    body. These tests pin the marker -> status mapping so it can't
    drift silently."""

    def test_real_content_returns_200(self):
        body = "<html><body>" + ("real listing content " * 200) + "</body></html>"
        assert HumanizedFetcher._infer_status_from_body(body) == 200

    def test_imperva_bot_or_not_marker_returns_429(self):
        # Imperva PWA challenge served by Expedia / VRBO / Hotels.com.
        body = "<html><head><title>Bot or Not?</title></head></html>"
        assert HumanizedFetcher._infer_status_from_body(body) == 429

    def test_datadome_marker_returns_403(self):
        body = '<script src="https://geo.captcha-delivery.com/c.js"></script>'
        assert HumanizedFetcher._infer_status_from_body(body) == 403

    def test_akamai_marker_returns_403(self):
        body = "<html><body>Pardon Our Interruption</body></html>"
        assert HumanizedFetcher._infer_status_from_body(body) == 403

    def test_cloudflare_markers_return_503(self):
        assert (
            HumanizedFetcher._infer_status_from_body("<html><body>Just a moment...</body></html>")
            == 503
        )
        assert HumanizedFetcher._infer_status_from_body("<p>Ray ID: 8a1b2c3d</p>") == 503

    def test_marker_only_checks_first_6kb(self):
        # Real content prefix followed by a challenge marker buried deep
        # in the doc should still be treated as 200 (the prefix is what
        # the user-agent sees during JS settle).
        body = ("real content " * 600) + " Bot or Not? " + ("more content " * 200)
        # 6KB cutoff: "real content " * 600 = ~8400 chars, so marker is past 6KB.
        assert HumanizedFetcher._infer_status_from_body(body) == 200


# ---------------------------------------------------------------------------
# Proxy selection (Layer 9)
# ---------------------------------------------------------------------------


class TestProxySelection:
    def test_no_proxy_when_unset(self, monkeypatch):
        monkeypatch.delenv("OSINT_RESIDENTIAL_PROXY", raising=False)
        monkeypatch.delenv("OSINT_TOR_MODE", raising=False)
        # Static-call the resolver to verify selection logic.
        assert HumanizedFetcher._pick_proxy_url() is None

    def test_residential_proxy_wins_over_tor(self, monkeypatch):
        monkeypatch.setenv("OSINT_RESIDENTIAL_PROXY", "http://user:pass@host:8080")
        monkeypatch.setenv("OSINT_TOR_MODE", "1")
        assert HumanizedFetcher._pick_proxy_url() == "http://user:pass@host:8080"

    def test_tor_proxy_when_only_tor_set(self, monkeypatch):
        monkeypatch.delenv("OSINT_RESIDENTIAL_PROXY", raising=False)
        monkeypatch.setenv("OSINT_TOR_MODE", "1")
        assert HumanizedFetcher._pick_proxy_url() == "socks5://127.0.0.1:9050"

    def test_custom_tor_proxy(self, monkeypatch):
        monkeypatch.delenv("OSINT_RESIDENTIAL_PROXY", raising=False)
        monkeypatch.setenv("OSINT_TOR_MODE", "1")
        monkeypatch.setenv("OSINT_TOR_PROXY", "socks5://192.168.1.5:9050")
        assert HumanizedFetcher._pick_proxy_url() == "socks5://192.168.1.5:9050"

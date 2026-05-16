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
    HumanizedFetcher,
    WarmupFlow,
    get_default_fetcher,
    get_warmup,
    jitter_sleep,
    pick_organic_referer,
    pick_ua,
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

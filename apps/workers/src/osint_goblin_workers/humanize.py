"""Humanized fetcher facade (Ship 8 -- OPSEC + LOGLESS hardening).

Per user directive 2026-05-15 "graduate to Ship 8 OPSEC -- bulletproof
fetching for every platform parser": Scrapling's `StealthyFetcher.fetch()`
boots a fresh browser per call with no warm-up, no session continuity,
no synthetic interaction. Travel platforms (VRBO, Booking, Airbnb) +
high-anti-bot SERPs (Bing, Google) increasingly need richer humanization
to avoid 429s and Cloudflare interstitials.

This module layers 8 humanization techniques on top of Patchright
(patched Playwright with anti-detection):

  1. Per-call jitter         3-12s random delay between requests
  2. UA rotation             20+ realistic Chrome/Firefox/Edge strings
                             weighted by 2026 real-world distribution
  3. Referer rotation        google/bing/duckduckgo/platform-homepage
  4. Session continuity      persistent BrowserContext per
                             (investigation_id, platform) tuple
  5. Warm-up flow            per-platform: visit homepage -> accept
                             cookies -> search -> click into listing
                             (not deep-link to listing URL directly)
  6. Mouse + scroll          synthetic page interaction before reading
                             content (defeats behavior-based ML)
  7. TLS fingerprint cycle   curl_cffi `impersonate=` rotation when
                             using the static-fetch tier
  8. Context per persona     BrowserContext isolation prevents
                             fingerprint carry-over across investigations

NAOMI GATE (logless contract)
  Persistent BrowserContexts hold cookies in MEMORY ONLY. No browser
  state is persisted to disk. Investigation contexts are torn down on
  worker shutdown OR when explicit `shred(investigation_id)` is called.
  No target-PII is logged anywhere in this module.

TOR INTEGRATION
  Set OSINT_TOR_MODE=1 to route the browser through Tor SOCKS5 (default
  127.0.0.1:9050). Tor is free but many platforms (VRBO, Booking,
  LinkedIn) pre-block exit nodes; expect ~50% success rate.

USAGE
  >>> from osint_goblin_workers.humanize import HumanizedFetcher
  >>> fetcher = HumanizedFetcher(investigation_id="inv_xyz")
  >>> status, body = fetcher.fetch(
  ...     "https://www.vrbo.com/1234567",
  ...     platform="vrbo",
  ... )
  >>> fetcher.shred()  # tear down browser context on investigation close
"""

from __future__ import annotations

import contextlib
import os
import random
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Any

# ===========================================================================
# Layer 2: UA rotation pool
# ===========================================================================
#
# Top 20 browser+platform combinations from StatCounter 2026-Q1 global
# desktop+mobile share. Weighted by real-world distribution so the pool
# returns realistic frequencies (Chrome dominant; Safari 2nd; Edge 3rd;
# Firefox + smaller share). All strings are current as of build time;
# rotate quarterly.

_UA_POOL_DESKTOP: tuple[tuple[str, float], ...] = (
    # Chrome (~65% desktop share)
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
        0.20,
    ),
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        0.15,
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
        0.10,
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        0.08,
    ),
    (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
        0.04,
    ),
    # Safari (~15% desktop share, ~25% mobile)
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/17.6 Safari/605.1.15",
        0.08,
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/18.0 Safari/605.1.15",
        0.05,
    ),
    # Edge (~10% desktop share)
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0",
        0.06,
    ),
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0",
        0.05,
    ),
    # Firefox (~3% desktop)
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) " "Gecko/20100101 Firefox/131.0",
        0.02,
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.6; rv:131.0) " "Gecko/20100101 Firefox/131.0",
        0.02,
    ),
    # iPhone Safari (mobile crossover)
    (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1",
        0.05,
    ),
    (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_6 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Mobile/15E148 Safari/604.1",
        0.03,
    ),
    # Android Chrome (mobile crossover)
    (
        "Mozilla/5.0 (Linux; Android 14; SM-S928U) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0.0.0 Mobile Safari/537.36",
        0.04,
    ),
    (
        "Mozilla/5.0 (Linux; Android 14; Pixel 9) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36",
        0.03,
    ),
)


def pick_ua() -> str:
    """Weighted-random UA selection from the 2026-Q1 distribution pool.

    Each call returns a fresh pick so multi-fetch sessions see variation.
    The weights sum to ~1.0; if they don't (e.g., after editing the pool)
    `random.choices` still works correctly with relative weights.
    """
    uas, weights = zip(*_UA_POOL_DESKTOP, strict=True)
    return random.choices(uas, weights=weights, k=1)[0]


# ===========================================================================
# Layer 3: Referer rotation pool
# ===========================================================================
#
# The most credible referer for any travel-platform landing depends on
# context. For the "I'm researching this listing" use case, organic-
# search referers (google/bing/duckduckgo) are realistic; for follow-up
# fetches within a session, internal-platform referers (the platform's
# own search results page) are MORE realistic. Both are rotated.

_REFERER_POOL_ORGANIC: tuple[str, ...] = (
    "https://www.google.com/",
    "https://www.google.com/search",
    "https://duckduckgo.com/",
    "https://www.bing.com/",
    "https://search.brave.com/",
)


def pick_organic_referer() -> str:
    """Pick a credible organic-search referer for the initial visit to a
    platform. Subsequent visits within a session use internal referers
    (the platform's own pages) which feel more natural."""
    return random.choice(_REFERER_POOL_ORGANIC)


# ===========================================================================
# Layer 5: Per-platform warm-up flow
# ===========================================================================
#
# Each platform has a credible "real user" landing pattern. Mimicking it
# builds session cookies + behavioral signals that anti-bot ML reads as
# human. Skipping warm-up + deep-linking to a listing URL is the #1
# tell that flags requests as scrapers.
#
# Per-platform tables: (homepage_url, search_url_with_query_placeholder,
# accept_cookies_selector_or_None, wait_for_selector_or_None). The
# warm-up runner visits homepage, dismisses any cookie banner, performs
# a benign search, then yields control back so the caller can navigate
# to the actual target URL within the same browser context.


@dataclass(frozen=True)
class WarmupFlow:
    """One platform's warm-up sequence."""

    homepage_url: str
    # Optional: a search URL template the warm-up visits before the
    # target. Leave empty to skip the search step.
    search_url_template: str = ""
    # CSS selector for an "accept cookies" or "got it" banner to click
    # if it appears within 3 seconds of page load. Empty = skip.
    accept_cookies_selector: str = ""
    # Selector to wait for before declaring the page loaded (in addition
    # to network_idle). Empty = network_idle only.
    wait_for_selector: str = ""


_WARMUP_FLOWS: dict[str, WarmupFlow] = {
    "airbnb": WarmupFlow(
        homepage_url="https://www.airbnb.com/",
        search_url_template="https://www.airbnb.com/s/{query}/homes",
        accept_cookies_selector='button[data-testid="accept-btn"]',
        wait_for_selector='[data-testid="card-container"]',
    ),
    "vrbo": WarmupFlow(
        homepage_url="https://www.vrbo.com/",
        search_url_template="https://www.vrbo.com/search?q={query}",
        accept_cookies_selector="button#onetrust-accept-btn-handler",
        wait_for_selector="",
    ),
    "booking": WarmupFlow(
        homepage_url="https://www.booking.com/",
        search_url_template="https://www.booking.com/searchresults.html?ss={query}",
        accept_cookies_selector="button#onetrust-accept-btn-handler",
        wait_for_selector="",
    ),
    "tripadvisor": WarmupFlow(
        homepage_url="https://www.tripadvisor.com/",
        search_url_template="https://www.tripadvisor.com/Search?q={query}",
        accept_cookies_selector="button#onetrust-accept-btn-handler",
        wait_for_selector="",
    ),
    "yanolja": WarmupFlow(
        homepage_url="https://www.yanolja.com/",
        search_url_template="",
        accept_cookies_selector="",
        wait_for_selector="",
    ),
    "leboncoin": WarmupFlow(
        homepage_url="https://www.leboncoin.fr/",
        search_url_template="https://www.leboncoin.fr/recherche?text={query}",
        accept_cookies_selector="button#didomi-notice-agree-button",
        wait_for_selector="",
    ),
    "expedia": WarmupFlow(
        homepage_url="https://www.expedia.com/",
        search_url_template="",
        accept_cookies_selector="button#onetrust-accept-btn-handler",
        wait_for_selector="",
    ),
    "agoda": WarmupFlow(
        homepage_url="https://www.agoda.com/",
        search_url_template="",
        accept_cookies_selector="",
        wait_for_selector="",
    ),
    "hipcamp": WarmupFlow(
        homepage_url="https://www.hipcamp.com/",
        search_url_template="",
        accept_cookies_selector="",
        wait_for_selector="",
    ),
}


def get_warmup(platform: str) -> WarmupFlow | None:
    """Return the warm-up flow for a platform, or None if not defined.

    None means the fetcher falls back to a single network_idle-waited
    direct fetch (still humanized via UA / referer / mouse but without
    multi-page warm-up).
    """
    return _WARMUP_FLOWS.get(platform)


# ===========================================================================
# Layer 1: Jitter
# ===========================================================================


def jitter_sleep(low_s: float = 3.0, high_s: float = 12.0) -> None:
    """Sleep a random interval in [low, high). Default 3-12s span is
    the empirical sweet spot: short enough to be tolerable for the
    investigator, long enough that platform anti-bot ML accepts the
    cadence as human."""
    duration = random.uniform(low_s, high_s)
    time.sleep(duration)


# ===========================================================================
# Platform anti-bot vendor map (documentation + future routing)
# ===========================================================================
#
# Pressure-tested 2026-05-16: travel + SERP platforms each ship behind a
# different anti-bot vendor. The right humanization layer to deploy
# depends on which vendor blocks: Imperva needs in-browser JS challenge
# solving; DataDome blocks at CloudFront edge BEFORE any JS runs (so
# in-browser stealth is useless, only proxy rotation works); Akamai BMA
# is in-between.
#
# Use this map to:
#   - Route VRBO / similar (Imperva) to the camoufox tier first.
#   - Route TripAdvisor / similar (DataDome) directly to proxy tier.
#   - Skip humanization overhead on platforms with no anti-bot.
#
# Add entries here as new platforms are observed; pressure-test results
# go in tools/dev/listing-failure-bodies/.

PLATFORM_ANTIBOT_MAP: dict[str, str] = {
    # Vacation rentals
    "airbnb": "akamai-bma",
    "vrbo": "imperva",  # serves "Bot or Not?" challenge; JS-solvable
    "booking": "akamai-bma",  # OneTrust cookies; mild rate-limiting
    "tripadvisor": "datadome",  # CloudFront edge block; proxy-only bypass
    "flipkey": "datadome",  # TripAdvisor subsidiary; same vendor
    "niumba": "datadome",  # TripAdvisor ES subsidiary
    "expedia": "akamai-bma",
    "hotels.com": "akamai-bma",
    "agoda": "akamai-bma",
    # Regional / low-friction
    "yanolja": "none",  # KR; no observed anti-bot
    # FR; DataDome confirmed via live pressure-test 2026-05-16
    # (all 3 Playwright tiers + zendriver got 403 on public listing URLs;
    # cookie-injection is the documented operator path).
    "leboncoin": "datadome",
    "hipcamp": "none",  # US camping; minimal anti-bot
    "couchsurfing": "none",
    "homestay": "none",
    "vacasa": "none",
    "sonder": "none",
    "plumguide": "none",
    "ostrovok": "none",
    "tripcom": "akamai-bma",
    "tujia": "none",
    "9flats": "none",
    "ferienhausmiete": "none",
    "despegar": "none",
    "makemytrip": "none",
    "marriott_homes_villas": "akamai-bma",
    # --- China booking platforms ---
    # CN sites favor Geetest (极验) / Tencent T-Sec challenges -- different
    # from western anti-bot vendors. Our current tier ladder doesn't
    # explicitly solve them; default to "unknown" so the recommended-tier
    # router lands on patchright (which is fine for most CN platforms
    # because they're more rate-limit-driven than challenge-driven).
    "fliggy": "unknown",
    "qunar": "unknown",  # Trip Group; same Akamai infra likely
    "mafengwo": "unknown",
    "lvmama": "unknown",
    "tongcheng": "unknown",
    "tuniu": "unknown",
    "meituan": "unknown",  # known for Geetest captcha on heavy use
    "xiaozhu": "unknown",
    "muniao": "unknown",
    "elong": "akamai-bma",  # Trip Group subsidiary; same infra
    # --- DACH / Germany regional ---
    "traum": "none",  # primarily a marketplace; light anti-bot
    "ferienwohnungen": "none",
    "atraveo": "none",
    "bestfewo": "none",
    "novasol": "didomi-only",  # Awaze cookie banner; otherwise open
    "edomizil": "none",
    "interhome": "didomi-only",
    "belvilla": "didomi-only",
    "casamundo": "datadome",  # HomeToGo subsidiary; same vendor
    # --- Iberia (ES/PT) ---
    "idealista": "datadome",  # observed: DataDome on idealista.com
    "fotocasa": "datadome",  # Spanish property portals lean DataDome
    "pisos": "none",
    "rentalia": "none",
    "toprural": "none",
    "spainholiday": "none",
    # --- France (FR) ---
    "pap": "didomi-only",
    "seloger": "datadome",  # SeLoger uses DataDome (verified)
    "morningcroissant": "didomi-only",
    # --- Italy (IT) ---
    "immobiliare": "datadome",  # Italian property portals lean DataDome
    "casait": "didomi-only",
    "subito": "didomi-only",
    "casevacanza": "none",
    # --- Pan-EU meta-search ---
    "hometogo": "datadome",  # observed DataDome on hometogo.de
    "holidu": "datadome",
    "onefinestay": "none",  # luxury / lower volume = less defended
    # --- LATAM (BR/MX/PA) ---
    "hurb": "datadome",  # large BR OTA; common DataDome target
    "cvc": "akamai-bma",
    "decolar": "akamai-bma",  # Despegar's BR brand; same infra
    "vivareal": "datadome",
    "zapimoveis": "datadome",
    "temporada": "didomi-only",
    "aluguetemporada": "imperva",  # Vrbo BR brand -> same Imperva infra
    "quintoandar": "datadome",
    "bestday": "akamai-bma",
    "pricetravel": "didomi-only",
    "lamudi": "datadome",  # observed DataDome on lamudi.com.mx
    "vivanuncios": "didomi-only",
    "inmuebles24": "datadome",
    "olx": "datadome",  # OLX heavy DataDome user globally
    "mercadolibre": "datadome",
    "encuentra24": "didomi-only",
    "compraventa": "none",
    # --- Africa (NG/KE/ZA) ---
    "lekkeslaap": "none",
    "travelground": "none",
    "safarinow": "none",
    "nightsbridge": "none",
    "property24": "didomi-only",  # ZA + KE under same brand
    "hotelsng": "none",
    "jumia_travel": "didomi-only",  # Jumia uses OneTrust+light WAF
    "privateproperty_ng": "none",
    "propertypro_ng": "none",
    "jiji": "didomi-only",  # Adevinta-style banners
    "wakanow": "none",
    "buyrentkenya": "none",
    # --- Middle East / Saudi Arabia ---
    "almosafer": "akamai-bma",  # SA OTA likely on Akamai infra
    "rehlat": "didomi-only",
    "bayut": "datadome",  # observed DataDome on Bayut
    "aqar": "none",
    # --- India (IN) ---
    "goibibo": "akamai-bma",  # MakeMyTrip group -> shared infra
    "cleartrip": "akamai-bma",
    "yatra": "didomi-only",
    "oyo": "akamai-bma",
    "acres99": "datadome",  # 99acres uses DataDome
    "magicbricks": "datadome",
    "housing_in": "datadome",
    "nobroker": "didomi-only",
    "sulekha": "none",
    # --- Sri Lanka (LK) ---
    "lakpura": "none",
    "lankapropertyweb": "none",
    "ikman": "datadome",  # Adevinta brand; likely DataDome
    # --- Southeast Asia (PH/ID/VN) ---
    "wego": "akamai-bma",
    "dotproperty": "didomi-only",
    "carousell": "datadome",  # Carousell heavy DataDome user
    "mynimo": "none",
    "traveloka": "akamai-bma",  # big SEA OTA, hardened infra
    "tiket": "akamai-bma",
    "pegipegi": "didomi-only",
    "rumah": "didomi-only",
    "rumah123": "didomi-only",
    "co99": "didomi-only",  # 99.co
    "mytour": "none",
    "ivivu": "none",
    "vntrip": "none",
    "luxstay": "didomi-only",
    "batdongsan": "didomi-only",
    "chotot": "didomi-only",
    # --- Pre-existing gaps closed ---
    "outdoorsy": "akamai-bma",  # RV rental marketplace; cloudfront infra
    "rvshare": "akamai-bma",  # RV rental marketplace
    "domiztel": "none",  # niche; minimal anti-bot
    # --- United Kingdom / Ireland ---
    "rightmove": "datadome",  # observed DataDome on rightmove.co.uk
    "zoopla": "datadome",
    "onthemarket": "akamai-bma",
    "spareroom": "didomi-only",
    "gumtree": "datadome",  # eBay-group, DataDome user
    "daft": "datadome",  # IE property portal, DataDome
    "myhome": "didomi-only",
    "property_ie": "didomi-only",
    "sykescottages": "didomi-only",
    "hoseasons": "didomi-only",
    "cottagesandcastles": "none",
    "hostunusual": "none",
    # --- Portugal (PT) ---
    "imovirtual": "datadome",  # PT property; DataDome
    "custojusto": "datadome",  # PT classifieds
    "casasapo": "didomi-only",
    # --- Chile (CL) ---
    "portalinmobiliario": "akamai-bma",  # MercadoLibre-owned; shared infra
    "yapo": "didomi-only",
    # --- Canada (CA) ---
    "realtor_ca": "akamai-bma",  # CREA platform; Akamai
    "kijiji": "datadome",  # eBay Group; DataDome
    "cottagesincanada": "none",
    "canadastays": "none",
    "cottagecountry": "none",
    # --- Nordic (NO/SE/DK/FI/IS) ---
    "finn": "datadome",  # Adevinta flagship; DataDome on hot pages
    "hybel": "didomi-only",
    "hemnet": "datadome",  # SE property; DataDome
    "blocket": "datadome",  # Adevinta SE
    "bostadsportal": "didomi-only",
    "boliga": "didomi-only",
    "dba": "didomi-only",
    "lejebolig": "didomi-only",
    "oikotie": "didomi-only",
    "tori": "datadome",  # Adevinta FI
    "etuovi": "didomi-only",
    # --- Netherlands (NL) ---
    "funda": "datadome",  # NL #1 property; DataDome
    "pararius": "datadome",  # NL rentals; DataDome
    "marktplaats": "datadome",  # eBay NL; DataDome
    "huurwoningen": "didomi-only",
    # --- Switzerland (CH) ---
    "homegate": "datadome",  # CH #1 property; DataDome
    "immoscout24": "datadome",  # also covers AT
    "comparis": "datadome",
    "anibis": "didomi-only",
    "tutti": "didomi-only",
    # --- Austria (AT) ---
    "willhaben": "datadome",  # AT #1 classifieds; DataDome
    "immowelt": "datadome",
    # --- Belgium (BE) ---
    "immoweb": "datadome",  # BE #1 property; DataDome
    "zimmo": "didomi-only",
    "tweedehands": "datadome",  # Marktplaats sibling; same infra
    "logicimmo": "didomi-only",
}


def get_antibot_vendor(platform: str) -> str:
    """Return the known anti-bot vendor for a platform, or 'unknown'.

    Callers can route through different humanization tiers based on
    vendor: imperva -> camoufox (defeats fingerprint-based detection);
    datadome -> firecrawl (edge-blocked, only fleet-IP fetches work);
    akamai-bma -> patchright default; none/didomi-only -> fastest OK.
    """
    return PLATFORM_ANTIBOT_MAP.get(platform, "unknown")


# Per-vendor recommended tier. Callers may override via env or argument
# but this map captures pressure-tested defaults from 2026-05-16:
#   imperva       -> Zendriver (empirical: defeated VRBO/Expedia's
#                    Imperva PWA challenge 2026-05-16, 200 OK + 887KB
#                    real content with populated __APOLLO_STATE__ +
#                    schema.org/VacationRental markup. Every Playwright
#                    tier -- patchright, rebrowser, camoufox -- hit the
#                    "Bot or Not?" challenge and never passed. Zendriver
#                    is CDP-direct, no Playwright Runtime.Enable leak
#                    class). Camoufox is the documented fallback when
#                    zendriver fails (e.g. on a burnt residential IP).
#   datadome      -> Zendriver (same probe, TripAdvisor 200 OK + 433KB
#                    real content). Camoufox also works on DataDome
#                    (Firefox fingerprint), kept as fallback.
#   akamai-bma    -> Patchright (default; Akamai BMA is the most
#                    forgiving major vendor)
#   none          -> Patchright (default; cheapest)
#   didomi-only   -> Patchright (just need cookie dismissal)
#   unknown       -> Patchright (default)
_RECOMMENDED_TIER_BY_VENDOR: dict[str, str] = {
    "imperva": "zendriver",
    "datadome": "zendriver",
    "akamai-bma": "patchright",
    "none": "patchright",
    "didomi-only": "patchright",
    "unknown": "patchright",
}


def recommended_tier_for_platform(platform: str) -> str:
    """Look up the pressure-tested-best tier for a platform.

    Caller resolution order:
      1. Explicit `OSINT_BROWSER_TIER` env var wins.
      2. Otherwise this function's recommendation per anti-bot vendor.
      3. Falls back to patchright if vendor unknown.

    Returns one of: "patchright", "rebrowser", "camoufox", "firecrawl".
    """
    vendor = get_antibot_vendor(platform)
    return _RECOMMENDED_TIER_BY_VENDOR.get(vendor, "patchright")


# ===========================================================================
# Layer 4 + 8: Persistent BrowserContext per (investigation_id, platform)
# ===========================================================================
#
# Patchright session continuity. Each (investigation_id, platform) tuple
# gets its own browser context with isolated cookies / localStorage /
# fingerprint. Contexts persist for the lifetime of the
# `HumanizedFetcher` instance and are torn down on `shred()` or process
# exit.


@dataclass
class _CtxState:
    """Per-context bookkeeping."""

    browser: Any = None  # underlying Browser (Patchright / rebrowser / Camoufox)
    context: Any = None  # BrowserContext (Camoufox tier sets this directly)
    page: Any = None  # Page (reused across fetches)
    user_agent: str = ""
    warmed_platforms: set[str] = field(default_factory=set)
    # which browser tier actually launched:
    # patchright / rebrowser / camoufox / zendriver / firecrawl
    tier_used: str = ""
    # platform -> Playwright-format cookie list. Pre-solved sessions
    # (investigator manually cleared a captcha in their real browser,
    # exported cookies) get injected here and applied before each fetch
    # to the matching platform. Naomi gate: in-memory only, dropped on
    # shred() with the rest of the context state.
    injected_cookies: dict[str, list[dict]] = field(default_factory=dict)
    # platform -> True once cookies have been applied to the current
    # BrowserContext. Reset to False whenever the context is recreated.
    cookies_applied: dict[str, bool] = field(default_factory=dict)


class HumanizedFetcher:
    """Investigation-scoped humanized fetcher.

    Instance owns one Patchright `chromium.launch()` + one
    `BrowserContext`. Multiple fetches against different platforms in
    the same investigation reuse the context; each platform gets a
    one-time warm-up the first time it's hit.

    Thread-safe: a single fetcher instance can be shared across multiple
    worker threads (Dramatiq actors); the Playwright sync API holds an
    internal lock per browser.
    """

    _instances_lock = threading.Lock()

    def __init__(self, investigation_id: str = "default") -> None:
        self.investigation_id = investigation_id
        self._state = _CtxState()
        self._state.user_agent = pick_ua()
        self._closed = False

    # -- public surface --

    def fetch(
        self,
        url: str,
        *,
        platform: str | None = None,
        timeout_s: float = 90.0,
        jitter: bool = True,
        synthetic_interaction: bool = True,
    ) -> tuple[int, str]:
        """Humanized fetch.

        Args:
          url: target URL.
          platform: platform key (airbnb/vrbo/booking/...); when set,
            the per-platform warm-up flow runs once on first visit.
          timeout_s: max wall time for the fetch (including warm-up).
          jitter: insert a 3-12s pre-fetch sleep. Disable for
            interactive testing.
          synthetic_interaction: emulate mouse move + scroll after
            page load. Defeats behavior-based bot detection.

        Returns:
          (status, body_text). status=0 on exception or browser failure.
        """
        if self._closed:
            return (0, "")

        # Tier-5 (zendriver) and Tier-4 (firecrawl) both short-circuit
        # the Playwright browser-context lifecycle. Each owns its own
        # browser launch/teardown per fetch -- no warm-up, no session
        # continuity, no Playwright sync API at all. Naomi-clean by
        # construction (browser dies after every fetch).
        tier = (os.environ.get("OSINT_BROWSER_TIER") or "patchright").strip().lower()
        if tier == "firecrawl":
            return self._fetch_via_firecrawl(url, timeout_s=timeout_s)
        if tier == "zendriver":
            return self._fetch_via_zendriver(url, platform=platform, timeout_s=timeout_s)

        try:
            self._ensure_browser()
            if jitter:
                jitter_sleep()
            if platform and platform not in self._state.warmed_platforms:
                self._run_warmup(platform, timeout_s)
                self._state.warmed_platforms.add(platform)
            # Apply pre-solved session cookies (if any) BEFORE the warmup
            # would have re-validated them. If cookies are present, they
            # short-circuit any captcha challenge the warmup might have
            # otherwise faced.
            if platform:
                self._apply_injected_cookies_playwright(platform)
            return self._fetch_target(
                url,
                referer=self._pick_referer(platform),
                timeout_s=timeout_s,
                synthetic_interaction=synthetic_interaction,
            )
        except Exception:
            return (0, "")

    def inject_cookies(self, platform: str, cookies: list[dict]) -> None:
        """Inject pre-solved session cookies for a platform.

        Use case: the investigator manually visits the target platform
        in their REAL browser, solves whatever captcha / Imperva PWA
        challenge it presents, then exports the resulting session
        cookies (via Chrome DevTools Application -> Cookies, or a
        browser-extension cookie exporter). They paste the cookie list
        into the goblin's UI; this method stores them and the next
        fetch for `platform` picks them up automatically.

        Why this is the guaranteed bypass: the cookies represent a
        successfully-cleared captcha session from a real human user.
        Imperva / DataDome / Akamai treat the request as resumed-human-
        session and serve real content. No browser stealth required.

        Cookie format follows Playwright's `BrowserContext.add_cookies`
        spec -- a list of dicts with at minimum `name`, `value`,
        `domain`, `path`. Optional: `expires` (Unix timestamp),
        `httpOnly`, `secure`, `sameSite` ("Strict"|"Lax"|"None").

        Naomi gate: cookies live in this fetcher's _CtxState only.
        Never persisted to disk. Dropped on shred() with everything
        else. Re-injection is required after shred().

        Args:
          platform: platform key (must match the `platform=` arg passed
            to fetch()). Cookies are scoped per-platform so vrbo cookies
            don't accidentally bleed into a booking fetch.
          cookies: Playwright-format cookie list.
        """
        if self._closed:
            return
        self._state.injected_cookies[platform] = list(cookies)
        # Mark not-yet-applied so the next fetch for this platform
        # actually pushes them into the BrowserContext / CDP session.
        self._state.cookies_applied[platform] = False

    def shred(self) -> None:
        """Tear down browser context. Naomi gate: cookies + localStorage
        + any cached state is dropped. Investigation data does NOT
        persist past this call."""
        if self._closed:
            return
        self._closed = True
        try:
            if self._state.context is not None:
                self._state.context.close()
        except Exception:
            pass
        try:
            if self._state.browser is not None:
                self._state.browser.close()
        except Exception:
            pass
        if hasattr(self, "_pw_ctx"):
            with contextlib.suppress(Exception):
                self._pw_ctx.__exit__(None, None, None)
        # Naomi gate: drop the per-investigation Camoufox user_data_dir
        # so cookies + localStorage don't persist past shred().
        if hasattr(self, "_camoufox_data_dir"):
            import shutil

            with contextlib.suppress(Exception):
                shutil.rmtree(self._camoufox_data_dir, ignore_errors=True)

    # -- internals --

    def _ensure_browser(self) -> None:
        """Lazy-init the configured browser tier + context on first fetch.

        Idempotent. After this returns, `self._state.browser` and
        `self._state.context` are usable.

        BROWSER TIER (env-gated `OSINT_BROWSER_TIER`):
          - "patchright" (default): Patchright (patched Playwright-Chromium).
                The current Patchright build patches the `Runtime.Enable`
                CDP leak that "all major anti-bot software" detects
                (Cloudflare, DataDome, Imperva) per the rebrowser-patches
                research. Best general-purpose tier.
          - "rebrowser": rebrowser-playwright (1352* upstream, drop-in
                Playwright fork with the same Runtime.Enable patch via
                addBinding technique). Use when Patchright trips a
                detection that rebrowser doesn't.
          - "camoufox": Camoufox (patched Firefox source, completely
                different fingerprint surface from Chromium-based tiers).
                Use when Imperva flags Chromium fingerprints consistently.

        PROXY TIER (env-gated, Layer 9):
          - `OSINT_RESIDENTIAL_PROXY=scheme://[user:pass@]host:port`
                Routes the browser through a residential proxy. The
                only reliable bypass for DataDome (edge-blocked before
                JS runs) + Imperva session-level IP blacklists at scale.
                Works with Bright Data / Smartproxy / IPRoyal / etc.
          - `OSINT_TOR_MODE=1` (+ optional `OSINT_TOR_PROXY=...`)
                Free SOCKS5 egress; ~50% success rate on travel platforms
                because most Tor exits are pre-blocked. Lower priority
                than residential proxy when both are set.
        """
        if self._state.browser is not None and self._state.context is not None:
            return

        tier = (os.environ.get("OSINT_BROWSER_TIER") or "patchright").strip().lower()
        proxy_url = self._pick_proxy_url()

        launch_kwargs: dict[str, Any] = {
            "headless": True,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        }
        if proxy_url:
            launch_kwargs["proxy"] = {"server": proxy_url}

        if tier == "camoufox":
            self._init_camoufox(launch_kwargs)
        elif tier == "rebrowser":
            self._init_rebrowser(launch_kwargs)
        else:
            self._init_patchright(launch_kwargs)

        # Skip the manual `new_context` step if the tier already returned
        # a persistent context (Camoufox path).
        if self._state.context is None and self._state.browser is not None:
            self._state.context = self._state.browser.new_context(
                user_agent=self._state.user_agent,
                locale="en-US",
                timezone_id="America/New_York",
                viewport={"width": 1440, "height": 900},
                device_scale_factor=1.0,
            )

    @staticmethod
    def _pick_proxy_url() -> str | None:
        """Resolve which proxy (if any) to route the browser through.

        Layer 9 (residential proxy) wins over Tor when both are set --
        residential proxies have far higher success rates against
        Imperva / DataDome / Akamai.
        """
        residential = os.environ.get("OSINT_RESIDENTIAL_PROXY", "").strip()
        if residential:
            return residential
        if os.environ.get("OSINT_TOR_MODE", "0") == "1":
            return os.environ.get("OSINT_TOR_PROXY", "socks5://127.0.0.1:9050")
        return None

    def _init_patchright(self, launch_kwargs: dict[str, Any]) -> None:
        """Default tier: Patchright (patched Playwright-Chromium)."""
        from patchright.sync_api import sync_playwright

        self._pw_ctx = sync_playwright()
        pw = self._pw_ctx.__enter__()
        self._state.browser = pw.chromium.launch(**launch_kwargs)
        self._state.tier_used = "patchright"

    def _init_rebrowser(self, launch_kwargs: dict[str, Any]) -> None:
        """Alternate tier: rebrowser-playwright (Patchright sibling).

        Same Runtime.Enable patch class, different patching technique
        (`addBinding` vs Patchright's approach). Worth trying when
        Patchright fails a specific anti-bot check.
        """
        try:
            from rebrowser_playwright.sync_api import sync_playwright
        except ImportError:
            # Graceful fallback if rebrowser isn't installed: drop to Patchright.
            self._init_patchright(launch_kwargs)
            return

        self._pw_ctx = sync_playwright()
        pw = self._pw_ctx.__enter__()
        self._state.browser = pw.chromium.launch(**launch_kwargs)
        self._state.tier_used = "rebrowser"

    def _init_camoufox(self, launch_kwargs: dict[str, Any]) -> None:
        """Alternate tier: Camoufox (patched Firefox source).

        Camoufox is a fork of Firefox with anti-detection patches built
        into the binary itself (not the automation library). Completely
        different fingerprint surface from Chromium-based tiers. Best
        choice when Imperva consistently flags Chromium even with
        Patchright/rebrowser patches.

        WINDOWS / MS-STORE PYTHON SANDBOX
          When Python is installed from the Microsoft Store, writes to
          `%LOCALAPPDATA%\\camoufox\\...` get redirected into a per-package
          container at `%LOCALAPPDATA%\\Packages\\PythonSoftwareFoundation
          .Python.<ver>_qbz5n2kfra8p0\\LocalCache\\Local\\camoufox\\...`.
          Only UWP-Python processes see the unredirected path; the
          Playwright node driver (a regular Win32 process) cannot find
          the binary at the un-redirected path and aborts with "Failed
          to launch firefox because executable doesn't exist". The fix
          here is to call `os.path.realpath()` on every Camoufox-managed
          path (executable + UBO addon) before handing the options dict
          off to Playwright. On non-MS-Store Pythons realpath is a no-op.
        """
        try:
            from camoufox.utils import launch_options
            from playwright.sync_api import sync_playwright
        except ImportError:
            self._init_patchright(launch_kwargs)
            return

        self._pw_ctx = sync_playwright()
        pw = self._pw_ctx.__enter__()
        # Camoufox's persistent_context path requires a user_data_dir.
        # We use a per-investigation tempdir scoped to this fetcher's
        # lifetime, then clean it up on shred() (Naomi gate: cookies +
        # localStorage held there are dropped when the fetcher closes).
        import tempfile

        self._camoufox_data_dir = tempfile.mkdtemp(prefix=f"camoufox-{self.investigation_id}-")

        # Build the launch_options dict via Camoufox's helper, then
        # rewrite every sandboxed path to its unredirected realpath
        # before handing it to Playwright. See class docstring above
        # for the MS-Store Python sandbox rationale.
        opts = launch_options(headless=launch_kwargs.get("headless", True))
        if "proxy" in launch_kwargs:
            opts["proxy"] = launch_kwargs["proxy"]
        opts["executable_path"] = os.path.realpath(opts["executable_path"])

        # CAMOU_CONFIG_1 env JSON carries the UBO addon path; addon load
        # happens inside the launched Firefox child, which also can't see
        # the sandbox-redirected path. Rewrite addons[] in place.
        import json

        env = opts.get("env") or {}
        cfg_key = "CAMOU_CONFIG_1"
        if cfg_key in env:
            try:
                cfg = json.loads(env[cfg_key])
                addons = cfg.get("addons")
                if isinstance(addons, list):
                    cfg["addons"] = [os.path.realpath(p) for p in addons]
                    env[cfg_key] = json.dumps(cfg)
                    opts["env"] = env
            except (json.JSONDecodeError, TypeError):
                pass

        # Bypass camoufox.NewBrowser and call launch_persistent_context
        # directly so we can inject user_data_dir without colliding with
        # NewBrowser's own from_options handling.
        self._state.context = pw.firefox.launch_persistent_context(
            user_data_dir=self._camoufox_data_dir,
            **opts,
        )
        self._state.tier_used = "camoufox"

    # Body-marker table mapping anti-bot challenge text -> inferred HTTP
    # status. Zendriver's CDP path doesn't surface the navigation response
    # status code directly (the API hands back the rendered DOM, not the
    # raw response), so we infer block-state from challenge content. The
    # status returned matches what the underlying request actually got
    # (Imperva = 429, DataDome = 403, Akamai = 403, Cloudflare = 503).
    _BODY_MARKER_STATUS: tuple[tuple[str, int], ...] = (
        ("Bot or Not", 429),
        ("captcha-delivery.com", 403),
        ("Pardon Our Interruption", 403),
        ("Just a moment", 503),
        ("Ray ID", 503),
    )

    @classmethod
    def _infer_status_from_body(cls, body: str) -> int:
        """Infer HTTP status by sniffing the first 6KB for known anti-bot
        challenge markers. Used by tiers that don't expose response.status
        on their navigation API (zendriver/CDP-direct). Defaults to 200
        when no marker hits, on the principle that a fully-rendered DOM
        without a challenge banner is by definition real content."""
        head = body[:6000] if isinstance(body, str) else ""
        for needle, status in cls._BODY_MARKER_STATUS:
            if needle in head:
                return status
        return 200

    def _apply_injected_cookies_playwright(self, platform: str) -> None:
        """Apply manually-injected cookies to the current Playwright
        BrowserContext. Idempotent: only fires once per (platform,
        context-lifetime). Reset via shred() -> next ensure_browser()."""
        if self._state.cookies_applied.get(platform):
            return
        cookies = self._state.injected_cookies.get(platform)
        if not cookies or self._state.context is None:
            return
        try:
            self._state.context.add_cookies(cookies)
            self._state.cookies_applied[platform] = True
        except Exception:
            # Cookie injection is best-effort -- malformed entries
            # shouldn't kill the fetch. Naomi gate is intact regardless.
            pass

    @staticmethod
    def _playwright_to_cdp_cookies(cookies: list[dict]) -> list:
        """Convert Playwright-format cookie dicts to zendriver's
        cdp.network.CookieParam objects. Field renames:

          httpOnly -> http_only
          sameSite -> same_site (string -> CookieSameSite enum)

        Other fields (name, value, domain, path, secure, expires) pass
        straight through. Unknown fields are dropped silently so older
        cookie exports still work after CDP schema bumps.
        """
        from zendriver.cdp.network import CookieParam, CookieSameSite

        same_site_map = {
            "strict": CookieSameSite.STRICT,
            "lax": CookieSameSite.LAX,
            "none": CookieSameSite.NONE,
        }
        out: list = []
        for c in cookies:
            if not isinstance(c, dict) or "name" not in c or "value" not in c:
                continue
            kwargs: dict[str, Any] = {"name": c["name"], "value": c["value"]}
            for src, dst in (
                ("url", "url"),
                ("domain", "domain"),
                ("path", "path"),
                ("secure", "secure"),
                ("expires", "expires"),
            ):
                if src in c:
                    kwargs[dst] = c[src]
            if "httpOnly" in c:
                kwargs["http_only"] = bool(c["httpOnly"])
            elif "http_only" in c:
                kwargs["http_only"] = bool(c["http_only"])
            ss = c.get("sameSite") or c.get("same_site")
            if isinstance(ss, str) and ss.lower() in same_site_map:
                kwargs["same_site"] = same_site_map[ss.lower()]
            try:
                out.append(CookieParam(**kwargs))
            except Exception:
                continue
        return out

    def _fetch_via_zendriver(
        self, url: str, *, platform: str | None = None, timeout_s: float
    ) -> tuple[int, str]:
        """Tier-5 fetch: Zendriver (CDP-direct, no Playwright).

        Zendriver talks raw Chrome DevTools Protocol -- no Playwright
        driver, no Runtime.Enable leak class, no addBinding patches
        needed. As of pressure-test 2026-05-16 this tier defeats both
        Imperva PWA challenges (VRBO/Expedia, 887KB real content) AND
        DataDome (TripAdvisor, 433KB real content) where every
        Playwright-based tier (patchright / rebrowser / camoufox) hits
        a challenge.

        Async-only by design. We wrap in `asyncio.run()` per fetch so:
          - Every fetch gets a fresh browser (Naomi gate: no carry-over
            cookies/localStorage/fingerprint across investigations).
          - Imperva can't cross-correlate sessions via persistent state.
          - No event-loop conflicts with the sync Playwright tiers in
            the same process.

        Trade-off: no session continuity within an investigation.
        Acceptable because zendriver is reserved for the hardest-anti-bot
        platforms where continuity itself is a detection signal.

        ENV CONFIG
          - OSINT_RESIDENTIAL_PROXY=...    Same env var as other tiers;
                                           routed via --proxy-server flag.
          - OSINT_TOR_MODE=1               Tor fallback proxy.
        """
        import asyncio

        proxy_url = self._pick_proxy_url()
        injected = self._state.injected_cookies.get(platform) if platform else None

        async def _run() -> tuple[int, str]:
            # Naomi-strict guard: zendriver is declared in pyproject.toml,
            # but if a venv is corrupted or the install was incomplete,
            # fail loud with a clear status rather than crashing the worker.
            try:
                import zendriver as zd
            except ImportError as exc:
                return (
                    503,
                    f"zendriver-unavailable: {exc} -- " "run `uv sync` to install the declared dep",
                )

            config = zd.Config(headless=True)
            if proxy_url:
                config.add_argument(f"--proxy-server={proxy_url}")
            browser = await zd.start(config=config)
            try:
                # Pre-solved session cookies (manual captcha-clear from
                # investigator's real browser) are pushed before any
                # navigation so the very first request to the platform
                # carries the cleared-session signal. This bypasses the
                # Imperva PWA challenge even on burnt IPs.
                if injected:
                    cdp_cookies = self._playwright_to_cdp_cookies(injected)
                    if cdp_cookies:
                        with contextlib.suppress(Exception):
                            await browser.cookies.set_all(cdp_cookies)
                tab = await browser.get(url)
                # Generous JS settle window: Imperva PWA's challenge JS
                # takes ~2-4 seconds to score + redirect. We cap at 5s
                # or 1/4 of timeout, whichever is smaller, so we don't
                # eat the full timeout budget on a fast platform.
                settle_s = min(5.0, max(2.0, timeout_s / 4.0))
                await asyncio.sleep(settle_s)
                body = await tab.get_content()
                status = self._infer_status_from_body(body)
                return (status, body)
            finally:
                with contextlib.suppress(Exception):
                    await browser.stop()

        self._state.tier_used = "zendriver"
        try:
            return asyncio.run(_run())
        except Exception:
            return (0, "")

    def _fetch_via_firecrawl(self, url: str, *, timeout_s: float) -> tuple[int, str]:
        """Tier-4 fetch: Firecrawl hosted scraping API.

        Use when local-browser tiers (Patchright, rebrowser, Camoufox)
        all fail OR when the platform is DataDome-blocked at edge (where
        in-browser stealth is irrelevant). Firecrawl operates its own
        proxy + browser fleet, so the request never originates from our
        IP -- bypasses CloudFront IP-reputation bans.

        ENV CONFIG
          - OSINT_FIRECRAWL_API_KEY=<key>        Required for hosted firecrawl.dev.
          - OSINT_FIRECRAWL_HOST=<base-url>      Optional self-hosted endpoint
                                                 (e.g. http://localhost:3002).
                                                 Avoids leaking target URLs to
                                                 firecrawl.dev; preferred for
                                                 Naomi-strict deployments.

        NAOMI GATE: when using the hosted API, the listing URL IS sent
        to firecrawl.dev. For Naomi-strict mode prefer self-hosted, OR
        keep firecrawl as a fallback-only tier so target URLs only leak
        when other tiers fail.
        """
        api_key = os.environ.get("OSINT_FIRECRAWL_API_KEY", "").strip()
        api_url = os.environ.get("OSINT_FIRECRAWL_HOST", "https://api.firecrawl.dev").strip()
        if not api_key:
            return (0, "")
        try:
            from firecrawl.v1 import V1FirecrawlApp
        except ImportError:
            return (0, "")
        try:
            app = V1FirecrawlApp(api_key=api_key, api_url=api_url, timeout=timeout_s)
            result = app.scrape_url(
                url,
                formats=["rawHtml"],
                wait_for=2000,
                timeout=int(timeout_s * 1000),
                only_main_content=False,
            )
            # Firecrawl SDK returns a typed dataclass-ish object or dict
            # depending on version. Handle both shapes.
            body = ""
            status = 0
            if hasattr(result, "rawHtml") and result.rawHtml:
                body = result.rawHtml
                status = 200
            elif isinstance(result, dict):
                body = result.get("rawHtml") or result.get("html") or ""
                status = 200 if body else 0
            elif hasattr(result, "html") and result.html:
                body = result.html
                status = 200
            self._state.tier_used = "firecrawl"
            return (status, body)
        except Exception:
            return (0, "")

    def _run_warmup(self, platform: str, timeout_s: float) -> None:
        """Visit the platform's homepage (and optionally a search page)
        to seed session cookies + behavioral signals."""
        warm = get_warmup(platform)
        if warm is None or self._state.context is None:
            return

        page = self._state.context.new_page()
        try:
            # 1. Homepage visit.
            page.goto(
                warm.homepage_url,
                wait_until="domcontentloaded",
                timeout=int(timeout_s * 1000),
                referer=pick_organic_referer(),
            )
            self._safe_wait_for_network_idle(page, 5000)
            # 2. Accept cookies if banner present.
            if warm.accept_cookies_selector:
                self._safe_click(page, warm.accept_cookies_selector, 3000)
            # 3. Synthetic browse: small scroll + mouse move.
            self._synthetic_interact(page)
            # 4. Optional search-page visit.
            if warm.search_url_template:
                search_url = warm.search_url_template.format(
                    query=urllib.parse.quote_plus("vacation")
                )
                try:
                    page.goto(
                        search_url,
                        wait_until="domcontentloaded",
                        timeout=int(timeout_s * 1000),
                        referer=warm.homepage_url,
                    )
                    self._safe_wait_for_network_idle(page, 5000)
                    self._synthetic_interact(page)
                except Exception:
                    pass  # search step is best-effort
        except Exception:
            pass
        finally:
            with contextlib.suppress(Exception):
                page.close()

    def _fetch_target(
        self,
        url: str,
        *,
        referer: str,
        timeout_s: float,
        synthetic_interaction: bool,
    ) -> tuple[int, str]:
        """Navigate to the target URL within the warmed context and
        return (status, body_text)."""
        if self._state.context is None:
            return (0, "")
        page = self._state.context.new_page()
        try:
            response = page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=int(timeout_s * 1000),
                referer=referer,
            )
            self._safe_wait_for_network_idle(page, 8000)
            if synthetic_interaction:
                self._synthetic_interact(page)
            status = response.status if response else 0
            body = page.content()
            return (int(status), body)
        except Exception:
            return (0, "")
        finally:
            with contextlib.suppress(Exception):
                page.close()

    # -- humanization micro-helpers --

    def _pick_referer(self, platform: str | None) -> str:
        """Pick a referer. If a platform has been warmed, use its
        homepage as a credible internal referer. Otherwise use a
        rotated organic-search referer."""
        if platform and platform in self._state.warmed_platforms:
            warm = get_warmup(platform)
            if warm is not None:
                return warm.homepage_url
        return pick_organic_referer()

    def _synthetic_interact(self, page: Any) -> None:
        """Emulate human page interaction: small scroll + mouse move +
        brief settle. Defeats behavior-based bot detection (Cloudflare
        Bot Fight Mode, Akamai BMA, etc.)."""
        try:
            # Random mouse movement to a believable spot.
            x = random.randint(200, 1200)
            y = random.randint(200, 700)
            page.mouse.move(x, y, steps=random.randint(8, 20))
            # Scroll a credible amount.
            page.evaluate(f"window.scrollBy(0, {random.randint(200, 600)})")
            # Brief settle so the scroll fires lazy-loaders before
            # we serialize the DOM.
            page.wait_for_timeout(random.randint(800, 2200))
        except Exception:
            pass

    def _safe_click(self, page: Any, selector: str, timeout_ms: int) -> None:
        """Click a selector if it appears within timeout_ms. Swallow
        errors -- cookie banners are best-effort."""
        try:
            page.click(selector, timeout=timeout_ms)
            page.wait_for_timeout(500)
        except Exception:
            pass

    def _safe_wait_for_network_idle(self, page: Any, timeout_ms: int) -> None:
        with contextlib.suppress(Exception):
            page.wait_for_load_state("networkidle", timeout=timeout_ms)


# ===========================================================================
# Convenience: shared default fetcher for callers that don't need
# investigation-scoped isolation
# ===========================================================================


_default_fetcher_lock = threading.Lock()
_default_fetcher: HumanizedFetcher | None = None


def get_default_fetcher() -> HumanizedFetcher:
    """Lazy singleton for one-shot humanized fetches. Callers that
    need investigation-scoped isolation should instantiate their own
    HumanizedFetcher(investigation_id=...) instead."""
    global _default_fetcher
    with _default_fetcher_lock:
        if _default_fetcher is None or _default_fetcher._closed:
            _default_fetcher = HumanizedFetcher(investigation_id="default")
    return _default_fetcher


def shred_default_fetcher() -> None:
    """Tear down the default fetcher. Use on worker shutdown to release
    the browser process cleanly."""
    global _default_fetcher
    with _default_fetcher_lock:
        if _default_fetcher is not None:
            _default_fetcher.shred()
            _default_fetcher = None

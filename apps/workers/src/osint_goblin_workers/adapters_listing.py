"""Travel-platform listing adapters (W20.tr workflow).

Phase 6 rollout, 2026-05-15. Per user directive "every conceivable travel
platform on the same level": each adapter takes a listing URL, detects
the platform from the URL host, dispatches to the per-platform extractor,
and emits a normalized listing-data event.

Output shape (normalized across platforms):
    {
      "event_type": "listing-data",
      "payload": {
        "source": "listing",
        "platform":          "airbnb" | "vrbo" | "booking" | "tripadvisor" |
                             "yanolja" | "leboncoin" | "expedia" | "hipcamp" |
                             "homeaway" | ...,
        "listing_url":       "<original input URL>",
        "listing_id":        "<platform-internal id where extractable>",
        "title":             "Cozy 2BR in Cambridge",
        "host_name":         "Alice",
        "host_url":          "https://www.airbnb.com/users/show/12345",
        "host_member_since": "January 2018",
        "host_is_superhost": True,
        "host_verifications": ["email", "phone", "government_id"],
        "host_response_rate": "100%",
        "host_response_time": "within an hour",
        "cohost_names":      ["Bob"],
        "cohost_urls":       ["https://www.airbnb.com/users/show/67890"],
        "address_displayed": "Cambridge, Massachusetts, United States",
        "neighborhood":      "Mid-Cambridge",
        "city":              "Cambridge",
        "country":           "United States",
        "gps_lat":           42.3736,
        "gps_lon":           -71.1097,
        "gps_source":        "json-ld" | "page-meta" | "map-iframe" | "absent",
        "review_count":      127,
        "review_rating":     4.92,
        "review_sample":     [{"author": "...", "date": "...", "text": "..."}, ...],
        "photo_urls":        ["https://..."],
        "amenities":         ["Wifi", "Kitchen", ...],
        "bedrooms":          2,
        "bathrooms":         1,
        "max_guests":        4,
        "property_type":     "Apartment",
        "currency":          "USD",
        "nightly_price":     145,
        "extraction_tier":   "json-ld" | "dom" | "mixed",
        "raw_jsonld_count":  3,
      }
    }

PROPERTY-VETTING VALUE
  Direct verification that the host's claims hold up. Cross-references
  the user-supplied host name + address against the listing's actual
  data. Reviews + GPS pin let the investigator confirm:
    - "Is the location what the listing says it is?"
    - "Is the host who they say they are?"
    - "Do reviews mention things that should worry me?"
    - "Are there cohosts not disclosed elsewhere?"

NAOMI GATE (logless contract)
  Listing URLs are operator-provided (the operator is researching a
  specific listing they're considering); the URL itself isn't
  target-PII. Extracted host names + reviews ARE PII for the listing
  owner -- surface via SSE stream + in-tab dossier only, never persist
  to disk past the event bus. Existing httpx + uvicorn-access log
  silencing (commit ae1def7) covers this.

LICENSE / TERMS OF SERVICE
  Per user directive 2026-05-15 ("aggressive techniques, no fair-use
  concerns, every conceivable platform"), this adapter scrapes
  individual public-facing listing pages for personal-use property
  vetting. Each platform's ToS is the operator's responsibility to
  evaluate against their local lawful-self-protection posture.
"""

from __future__ import annotations

import html as _html
import json as _json
import re
import urllib.parse
from typing import Any

from .adapters import get_registry

# ===========================================================================
# Platform detection
# ===========================================================================

# Host-suffix -> platform-id lookup. Suffix-matched against the URL's
# hostname so subdomain variants (e.g. ko.airbnb.com, fr.airbnb.com) all
# route to the same platform. Order doesn't matter -- the longest-suffix
# match wins.
_PLATFORM_HOST_MAP: dict[str, str] = {
    "airbnb.com": "airbnb",
    "airbnb.co.uk": "airbnb",
    "airbnb.fr": "airbnb",
    "airbnb.de": "airbnb",
    "airbnb.it": "airbnb",
    "airbnb.es": "airbnb",
    "airbnb.jp": "airbnb",
    "airbnb.co.kr": "airbnb",
    "vrbo.com": "vrbo",
    "homeaway.com": "vrbo",  # HomeAway merged into Vrbo (Expedia)
    "homeaway.co.uk": "vrbo",
    "homeaway.fr": "vrbo",
    "homeaway.de": "vrbo",
    "stayz.com.au": "vrbo",  # AU subsidiary
    "abritel.fr": "vrbo",  # FR subsidiary
    "fewo-direkt.de": "vrbo",  # DE subsidiary
    "booking.com": "booking",
    "tripadvisor.com": "tripadvisor",
    "tripadvisor.co.uk": "tripadvisor",
    "tripadvisor.fr": "tripadvisor",
    "tripadvisor.de": "tripadvisor",
    "tripadvisor.it": "tripadvisor",
    "tripadvisor.jp": "tripadvisor",
    "flipkey.com": "tripadvisor",  # TripAdvisor subsidiary
    "yanolja.com": "yanolja",
    "leboncoin.fr": "leboncoin",
    "expedia.com": "expedia",
    "expedia.co.uk": "expedia",
    "expedia.fr": "expedia",
    "expedia.de": "expedia",
    "hotels.com": "expedia",  # Expedia-owned
    "hotwire.com": "expedia",
    "orbitz.com": "expedia",
    "agoda.com": "agoda",  # Booking-owned but separate index
    "hipcamp.com": "hipcamp",
    "outdoorsy.com": "outdoorsy",
    "rvshare.com": "rvshare",
    "vacasa.com": "vacasa",
    "sonder.com": "sonder",
    "plumguide.com": "plumguide",
    "trip.com": "tripcom",  # CN-owned (Trip Group / Ctrip), int'l brand
    "ctrip.com": "tripcom",  # CN sibling brand (domestic)
    "9flats.com": "9flats",
    "ostrovok.ru": "ostrovok",  # RU
    "despegar.com": "despegar",  # LATAM
    "makemytrip.com": "makemytrip",  # IN
    "ferienhausmiete.de": "ferienhausmiete",  # DE
    "homestay.com": "homestay",
    "couchsurfing.com": "couchsurfing",
    "marriott.com": "marriott_homes_villas",  # /homes-and-villas/ path
    # --- China booking platforms ---
    # Source for inclusion: top CN travel apps by 2024-25 MAU reporting.
    # CN sites favor Tencent T-Sec / Geetest captcha; rough vendor
    # classification lives in PLATFORM_ANTIBOT_MAP (humanize.py).
    "tujia.com": "tujia",  # 途家 -- CN Airbnb-equivalent (short-term rental)
    "xiaozhu.com": "xiaozhu",  # 小猪短租 -- short-term rentals
    "muniao.com": "muniao",  # 木鸟短租 -- short-term rentals
    "fliggy.com": "fliggy",  # 飞猪 -- Alibaba Travel (international brand)
    "alitrip.com": "fliggy",  # 阿里旅行 -- Fliggy's legacy domain (still resolves)
    "qunar.com": "qunar",  # 去哪儿 -- meta-search (Trip Group subsidiary)
    "mafengwo.cn": "mafengwo",  # 马蜂窝 -- travel community + booking
    "lvmama.com": "lvmama",  # 驴妈妈 -- tours + tickets + hotels
    "tongcheng.com": "tongcheng",  # 同程旅行 -- OTA (HK-listed)
    "ly.com": "tongcheng",  # Tongcheng's primary user-facing domain
    "tuniu.com": "tuniu",  # 途牛 -- package tours + tickets
    "meituan.com": "meituan",  # 美团旅行 -- hotel/travel section
    "elong.com": "elong",  # 艺龙 -- Trip Group subsidiary
    "elong.net": "elong",
    # --- Germany / DACH (DE/AT/CH) ---
    # Rationale: short-stay fraud often uses photos scraped from regional
    # DE-market portals the operator wouldn't think to check. Reverse-
    # image searching against these sites is a load-bearing PV move.
    "traum-ferienwohnungen.de": "traum",  # huge DE vacation rentals (~190K)
    "ferienwohnungen.de": "ferienwohnungen",
    "atraveo.de": "atraveo",  # TUI brand, DE primary
    "atraveo.com": "atraveo",
    "bestfewo.de": "bestfewo",
    "novasol.com": "novasol",  # Awaze (Wyndham) brand
    "novasol.de": "novasol",
    "novasol.dk": "novasol",
    "e-domizil.de": "edomizil",  # DE/CH vacation rentals
    "e-domizil.ch": "edomizil",
    "interhome.com": "interhome",  # CH-based, EU coverage
    "interhome.de": "interhome",
    "interhome.ch": "interhome",
    "belvilla.com": "belvilla",  # Awaze brand
    "belvilla.de": "belvilla",
    "casamundo.com": "casamundo",  # HomeToGo subsidiary
    "casamundo.de": "casamundo",
    # --- Spain (ES) ---
    # Top ES property portals -- both short + long term photos commonly
    # scraped to seed Airbnb/VRBO scam listings outside Spain.
    "idealista.com": "idealista",  # #1 ES property portal
    "idealista.es": "idealista",
    "idealista.it": "idealista",  # also IT presence
    "idealista.pt": "idealista",  # PT presence
    "fotocasa.es": "fotocasa",  # #2 ES property portal
    "pisos.com": "pisos",  # ES property
    "niumba.com": "niumba",  # TripAdvisor-owned vacation rentals ES
    "rentalia.com": "rentalia",  # ES vacation rentals
    "toprural.com": "toprural",  # ES rural rentals
    "spain-holiday.com": "spainholiday",
    # --- France (FR) ---
    "pap.fr": "pap",  # Particulier-à-Particulier rentals
    "seloger.com": "seloger",  # FR property
    "morningcroissant.fr": "morningcroissant",  # FR short-term rentals
    # --- Italy (IT) ---
    "immobiliare.it": "immobiliare",  # #1 IT property portal
    "casa.it": "casait",  # IT property
    "subito.it": "subito",  # IT classifieds (also rentals)
    "casevacanza.it": "casevacanza",  # IT vacation rentals
    # --- Pan-EU meta-search + brands ---
    "hometogo.com": "hometogo",  # EU vacation rental meta-search
    "hometogo.de": "hometogo",
    "hometogo.es": "hometogo",
    "hometogo.fr": "hometogo",
    "hometogo.it": "hometogo",
    "holidu.com": "holidu",  # DE-built EU vacation rental meta-search
    "holidu.de": "holidu",
    "onefinestay.com": "onefinestay",  # luxury short-term EU
    # --- South Africa (ZA) ---
    # SA short-stay scams frequently use photos from regional portals
    # that overseas operators don't recognize. Reverse-image search
    # against these is high-signal.
    "lekkeslaap.co.za": "lekkeslaap",  # ZA #1 vacation rentals
    "travelground.com": "travelground",  # ZA + safari accommodations
    "safarinow.com": "safarinow",  # SA-focused safari + lodge
    "nightsbridge.com": "nightsbridge",  # SA B&B / boutique-hotel platform
    "property24.com": "property24",  # ZA property portal (long+short)
    # --- Brazil (BR) ---
    "hurb.com": "hurb",  # BR travel packages
    "cvc.com.br": "cvc",  # BR OTA (CVC Corp)
    "decolar.com": "decolar",  # BR brand of Despegar (LATAM #1)
    "decolar.com.br": "decolar",
    "vivareal.com.br": "vivareal",  # BR property (Grupo OLX)
    "zapimoveis.com.br": "zapimoveis",  # BR property
    "temporada.com.br": "temporada",  # BR short-term rentals
    "aluguetemporada.com.br": "aluguetemporada",  # legacy Vrbo BR brand
    "alugueltemporada.com.br": "aluguetemporada",  # common alternate spelling
    "quintoandar.com.br": "quintoandar",  # BR long+short rentals
    # --- Mexico (MX) ---
    "bestday.com": "bestday",  # MX OTA
    "bestday.com.mx": "bestday",
    "pricetravel.com": "pricetravel",  # MX OTA
    "pricetravel.com.mx": "pricetravel",
    "lamudi.com.mx": "lamudi",  # MX property portal
    "vivanuncios.com.mx": "vivanuncios",  # MX classifieds
    # --- Mexico + Panama (shared brands) ---
    "inmuebles24.com": "inmuebles24",  # MX/LATAM property; same brand across ccTLDs
    "inmuebles24.com.mx": "inmuebles24",
    "inmuebles24.com.pa": "inmuebles24",
    # --- LATAM classifieds shared across BR/MX ---
    "olx.com.br": "olx",  # OLX Brazil
    "olx.com.mx": "olx",  # OLX Mexico
    "mercadolibre.com.mx": "mercadolibre",  # MX classifieds
    # --- Panama (PA) ---
    "encuentra24.com": "encuentra24",  # PA+CR+SV+NI classifieds (regional)
    "compraventa.com.pa": "compraventa",  # PA classifieds
    # --- Nigeria (NG) ---
    "hotels.ng": "hotelsng",  # NG OTA (different brand from hotels.com)
    "jumia.com.ng": "jumia_travel",  # Jumia Travel Nigeria
    "travel.jumia.com": "jumia_travel",  # Jumia Travel main domain
    "privateproperty.com.ng": "privateproperty_ng",  # NG property portal
    "propertypro.ng": "propertypro_ng",  # NG property portal
    "jiji.ng": "jiji",  # NG classifieds (also covers .ke, .ug, .gh)
    "jiji.co.ke": "jiji",  # Kenya
    "jiji.ug": "jiji",  # Uganda
    "jiji.com.gh": "jiji",  # Ghana
    "wakanow.com": "wakanow",  # NG OTA
    # --- Kenya (KE) ---
    "buyrentkenya.com": "buyrentkenya",  # KE property portal
    "property24.co.ke": "property24",  # ZA brand has KE presence
    # --- Saudi Arabia (SA) ---
    "almosafer.com": "almosafer",  # SA OTA (formerly Tajawal)
    "tajawal.com": "almosafer",  # legacy brand
    "rehlat.com": "rehlat",  # SA OTA
    "bayut.sa": "bayut",  # SA property (Dubai-based brand)
    "bayut.com": "bayut",  # UAE primary; also covers SA inventory
    "aqar.fm": "aqar",  # SA property
    # --- India (IN) ---
    "goibibo.com": "goibibo",  # MakeMyTrip group, separate brand
    "cleartrip.com": "cleartrip",  # IN OTA
    "yatra.com": "yatra",  # IN OTA
    "oyorooms.com": "oyo",  # IN/global budget hotel platform
    "oyo.com": "oyo",
    "99acres.com": "acres99",  # IN property
    "magicbricks.com": "magicbricks",  # IN property
    "housing.com": "housing_in",  # IN rentals/property
    "nobroker.in": "nobroker",  # IN long+short rentals
    "olx.in": "olx",  # IN classifieds (folded into existing olx id)
    "sulekha.com": "sulekha",  # IN classifieds
    # --- Sri Lanka (LK) ---
    "lakpura.com": "lakpura",  # LK travel
    "lankapropertyweb.com": "lankapropertyweb",
    "ikman.lk": "ikman",  # LK classifieds (Adevinta-owned)
    # --- Philippines (PH) ---
    "wego.com.ph": "wego",
    "wego.com": "wego",  # global brand, also covers MENA
    "lamudi.com.ph": "lamudi",  # extends existing lamudi entry
    "lamudi.co.id": "lamudi",  # PH+ID under same brand
    "dotproperty.com.ph": "dotproperty",
    "carousell.ph": "carousell",  # SG classifieds, big in PH
    "carousell.com": "carousell",
    "carousell.com.sg": "carousell",
    "carousell.com.my": "carousell",
    "mynimo.com": "mynimo",  # PH local rentals
    # --- Indonesia (ID) ---
    "traveloka.com": "traveloka",  # ID #1 OTA, multi-country in SE Asia
    "tiket.com": "tiket",  # ID OTA
    "pegipegi.com": "pegipegi",
    "rumah.com": "rumah",  # ID property
    "rumah123.com": "rumah123",  # ID property
    "99.co": "co99",  # ID/SG property
    # --- Vietnam (VN) ---
    "mytour.vn": "mytour",  # VN OTA
    "ivivu.com": "ivivu",  # VN OTA
    "vntrip.vn": "vntrip",  # VN OTA
    "luxstay.com": "luxstay",  # VN short-term vacation rentals
    "batdongsan.com.vn": "batdongsan",  # VN property
    "chotot.com": "chotot",  # VN classifieds (Adevinta)
    # --- United Kingdom / Ireland / Scotland ---
    # Scotland: served via .co.uk and dedicated cottage brands. .scot is
    # rarely used for booking sites. Most Scottish vacation rentals live
    # on Sykes Cottages / Cottages & Castles / Airbnb / Vrbo.
    "rightmove.co.uk": "rightmove",  # UK #1 property portal
    "zoopla.co.uk": "zoopla",  # UK #2 property portal
    "onthemarket.com": "onthemarket",  # UK property portal
    "spareroom.co.uk": "spareroom",  # UK shared accommodations
    "gumtree.com": "gumtree",  # UK classifieds (also IE coverage)
    "daft.ie": "daft",  # IE #1 property portal
    "myhome.ie": "myhome",  # IE property portal
    "property.ie": "property_ie",  # IE property
    "sykescottages.co.uk": "sykescottages",  # UK vacation rentals (Scotland heavy)
    "hoseasons.co.uk": "hoseasons",  # UK vacation rentals
    "cottagesandcastles.co.uk": "cottagesandcastles",  # Scottish vacation rentals
    "hostunusual.co.uk": "hostunusual",  # UK unique stays
    # --- Portugal (PT) ---
    # idealista.pt already covered above via "idealista" brand.
    "imovirtual.com": "imovirtual",  # PT #1 property portal
    "olx.pt": "olx",  # extends OLX brand to Portugal
    "custojusto.pt": "custojusto",  # PT classifieds
    "casa.sapo.pt": "casasapo",  # PT property portal
    # --- Chile (CL) ---
    "portalinmobiliario.com": "portalinmobiliario",  # CL #1 property
    "yapo.cl": "yapo",  # CL classifieds
    "mercadolibre.cl": "mercadolibre",  # extends ML brand to Chile
    # --- Canada (CA) ---
    # Toronto-priority -- the user called this out. Realtor.ca and Kijiji
    # both serve Toronto natively; cottage sites cover Ontario rentals.
    "realtor.ca": "realtor_ca",  # CA #1 property portal
    "kijiji.ca": "kijiji",  # CA classifieds (eBay Group)
    "cottagesincanada.com": "cottagesincanada",  # CA vacation rentals
    "canadastays.com": "canadastays",  # CA vacation rentals
    "cottagecountry.ca": "cottagecountry",  # CA vacation rentals
    # --- Nordic (NO/SE/DK/FI/IS) ---
    "finn.no": "finn",  # NO #1 classifieds (Adevinta flagship)
    "hybel.no": "hybel",  # NO rentals
    "hemnet.se": "hemnet",  # SE #1 property
    "blocket.se": "blocket",  # SE #1 classifieds (Adevinta)
    "bostadsportal.se": "bostadsportal",  # SE property
    "boliga.dk": "boliga",  # DK property
    "dba.dk": "dba",  # DK classifieds (eBay)
    "lejebolig.dk": "lejebolig",  # DK rentals
    "oikotie.fi": "oikotie",  # FI #1 classifieds + property
    "tori.fi": "tori",  # FI classifieds (Adevinta)
    "etuovi.com": "etuovi",  # FI property
    # --- Netherlands (NL) ---
    "funda.nl": "funda",  # NL #1 property
    "pararius.nl": "pararius",  # NL rentals
    "marktplaats.nl": "marktplaats",  # NL classifieds (eBay)
    "huurwoningen.nl": "huurwoningen",  # NL rentals
    # --- Switzerland (CH) ---
    # e-domizil.ch / interhome.ch already covered as DACH brands.
    "homegate.ch": "homegate",  # CH #1 property
    "immoscout24.ch": "immoscout24",  # CH property
    "comparis.ch": "comparis",  # CH meta-search property
    "anibis.ch": "anibis",  # CH classifieds
    "tutti.ch": "tutti",  # CH classifieds
    # --- Austria (AT) ---
    "willhaben.at": "willhaben",  # AT #1 classifieds
    "immoscout24.at": "immoscout24",  # extends immoscout24 brand
    "immowelt.at": "immowelt",  # AT property
    "immowelt.de": "immowelt",  # DE primary
    # --- Belgium (BE) ---
    "immoweb.be": "immoweb",  # BE #1 property
    "zimmo.be": "zimmo",  # BE property
    "2dehands.be": "tweedehands",  # BE classifieds (Marktplaats sibling)
    "logic-immo.be": "logicimmo",  # BE property
    "domiztel.com": "domiztel",
}


def detect_platform(listing_url: str) -> str | None:
    """Return platform-id for a listing URL, or None if unrecognized.

    Suffix-matches the URL's host against `_PLATFORM_HOST_MAP`. Subdomain
    variants (ko.airbnb.com, m.booking.com) all route to the parent
    platform.

    Returns None for unparseable URLs and for hosts not in the map. The
    caller emits an unsupported-platform event in that case.
    """
    try:
        host = urllib.parse.urlparse(listing_url).hostname or ""
    except Exception:
        return None
    host = host.lower()
    if not host:
        return None
    # Suffix-match in longest-first order so airbnb.co.uk beats airbnb.com
    # for a `m.airbnb.co.uk` URL.
    for suffix in sorted(_PLATFORM_HOST_MAP.keys(), key=len, reverse=True):
        if host == suffix or host.endswith("." + suffix):
            return _PLATFORM_HOST_MAP[suffix]
    return None


# ===========================================================================
# JSON-LD universal extractor
# ===========================================================================
#
# Most travel platforms ship structured data via JSON-LD <script> blocks
# (schema.org/Product, schema.org/LodgingBusiness, schema.org/Place,
# schema.org/Review, schema.org/AggregateRating). When present these are
# the highest-fidelity source for listing data -- platforms typically
# keep them up-to-date because they feed Google rich snippets.
#
# Airbnb in particular ships a dense schema.org block with title,
# address, lat/lng, image list, aggregate rating, and review count.

_JSONLD_BLOCK_RE = re.compile(
    r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)


def extract_jsonld_blocks(html_body: str) -> list[dict[str, Any]]:
    """Find every <script type="application/ld+json"> block and JSON-parse.

    Returns a list of dicts. Malformed JSON is silently skipped (some
    platforms ship JSON-LD with raw control chars; better to skip the
    blob than to fail the whole extraction).
    """
    blocks: list[dict[str, Any]] = []
    for m in _JSONLD_BLOCK_RE.finditer(html_body):
        raw = m.group(1).strip()
        if not raw:
            continue
        try:
            parsed = _json.loads(raw)
        except (ValueError, _json.JSONDecodeError):
            continue
        if isinstance(parsed, dict):
            blocks.append(parsed)
        elif isinstance(parsed, list):
            for item in parsed:
                if isinstance(item, dict):
                    blocks.append(item)
    return blocks


def _walk_jsonld(
    blocks: list[dict[str, Any]], type_filter: str | tuple[str, ...]
) -> list[dict[str, Any]]:
    """Walk JSON-LD blocks and return every object whose @type matches.

    schema.org blocks frequently nest objects via @graph or sub-fields.
    Walk one level deep into @graph to catch nested objects.
    """
    types = (type_filter,) if isinstance(type_filter, str) else type_filter
    out: list[dict[str, Any]] = []

    def _matches(obj: dict[str, Any]) -> bool:
        t = obj.get("@type")
        if isinstance(t, str):
            return t in types
        if isinstance(t, list):
            return any(isinstance(x, str) and x in types for x in t)
        return False

    for blk in blocks:
        if _matches(blk):
            out.append(blk)
        graph = blk.get("@graph")
        if isinstance(graph, list):
            for item in graph:
                if isinstance(item, dict) and _matches(item):
                    out.append(item)
    return out


# ===========================================================================
# Airbnb extractor (cornerstone)
# ===========================================================================
#
# Airbnb ships JSON-LD for the listing's core attributes (title, address,
# image list, aggregate rating) and uses a separate Apollo / Next.js
# data-pipeline for everything else (host name, cohost names, reviews,
# amenities, GPS lat/lng). The Next.js __NEXT_DATA__ blob -- a single
# <script id="data-deferred-state-0"> JSON dump -- contains nearly
# everything the rendered page renders.
#
# Strategy: try JSON-LD first (free, fast); fall back to __NEXT_DATA__
# extraction when JSON-LD is missing fields. Both surfaces are
# Airbnb-owned data; neither requires login for public listings.

# Airbnb's deferred-state blob is keyed by a per-deploy hash; the script
# tag's id starts with "data-deferred-state-". Match that prefix.
_AIRBNB_NEXT_DATA_RE = re.compile(
    r'<script[^>]+id="data-deferred-state[^"]*"[^>]+type="application/json"[^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)

# Older Next.js layouts use __NEXT_DATA__.
_AIRBNB_NEXT_DATA_LEGACY_RE = re.compile(
    r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)


def _airbnb_extract_next_data(html_body: str) -> dict[str, Any] | None:
    """Find Airbnb's hydration blob and return the parsed JSON.

    Tries the modern `data-deferred-state-*` script first, falls back to
    the legacy `__NEXT_DATA__` script. Returns None if neither matches
    or if the JSON is malformed.
    """
    for rx in (_AIRBNB_NEXT_DATA_RE, _AIRBNB_NEXT_DATA_LEGACY_RE):
        m = rx.search(html_body)
        if not m:
            continue
        raw = m.group(1).strip()
        try:
            return _json.loads(raw)
        except (ValueError, _json.JSONDecodeError):
            continue
    return None


# ===========================================================================
# Universal review owner-mention scanner (load-bearing PV signal)
# ===========================================================================
#
# Per user directive 2026-05-15 "I want to see if the owner is mentioned
# anywhere in the reviews -- functionality built into every single travel
# platform scrape": every per-platform extractor MUST collect review text
# AND pass it through `review_owner_mention_scan()` so the dossier flags
# host_name <-> review-mentioned-name drift.
#
# Tiers (severity_basis ids -> lib/severity-rubric.ts):
#   LISTING_OWNER_DRIFT_GOOD:  reviews mention host_name + nothing else
#                              name-like  -> identity confirmed.
#   LISTING_OWNER_DRIFT_WARN:  reviews mention host_name AND other names
#                              (possible cohost/family) OR family-relation
#                              phrases -> investigator-review needed.
#   LISTING_OWNER_DRIFT_BAD:   reviews use explicit ownership phrasing
#                              ("Bob's house", "owner Bob", "Bob owns")
#                              for a name that ISN'T host_name -> likely
#                              impersonation / undisclosed owner.
#   LISTING_OWNER_DRIFT_INFO:  no reviews to scan, or no host_name to
#                              compare against -> no signal.

# Common English words that look like capitalized first-name tokens but
# aren't. Expanded over real-review review.
_NAME_FALSE_POSITIVES = frozenset(
    [
        "I",
        "My",
        "We",
        "Our",
        "You",
        "Your",
        "They",
        "Their",
        "He",
        "She",
        "His",
        "Her",
        "The",
        "This",
        "That",
        "These",
        "Those",
        "It",
        "If",
        "When",
        "Where",
        "What",
        "Why",
        "How",
        "Who",
        "Which",
        "Yes",
        "No",
        "OK",
        "Okay",
        "Wifi",
        "WiFi",
        "Wi-Fi",
        "Netflix",
        "Uber",
        "Lyft",
        "Airbnb",
        "Vrbo",
        "Booking",
        "Tripadvisor",
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday",
        "Saturday",
        "Sunday",
        "Christmas",
        "Easter",
        "Thanksgiving",
        "Halloween",
        "Valentine",
        "Great",
        "Excellent",
        "Amazing",
        "Wonderful",
        "Perfect",
        "Nice",
        "Good",
        "Bad",
        "Best",
        "Beautiful",
        "Awesome",
        "Highly",
        "Definitely",
        "Absolutely",
        "Really",
        "Very",
        "Super",
        "Thank",
        "Thanks",
        "Thankful",
        "Welcome",
        "Stay",
        "Stayed",
        "Staying",
        "Trip",
        "Visit",
        "Visited",
        "Loved",
        "Liked",
        "Enjoyed",
        "Hated",
        "Disliked",
        "Booked",
        "Arrived",
        "Departed",
        "Returned",
        "Tried",
        "Wanted",
        "Needed",
        "Got",
        "Had",
        "Saw",
        "Found",
        "Felt",
        "Spent",
        "Slept",
        "Cooked",
        "Walked",
        "Drove",
        "Took",
        "Made",
        "Helped",
        "Provided",
        "Booked",
        "Checked",
        "Used",
        "Place",
        "Property",
        "Home",
        "House",
        "Apartment",
        "Room",
        "Bedroom",
        "Bathroom",
        "Kitchen",
        "Living",
        "Pool",
        "Beach",
        "Hot",
        "Cold",
        "Warm",
        "Clean",
        "Spotless",
        "Communication",
        "Location",
        "Host",
        "Hosts",
        "Hosting",
        "Guest",
        "Guests",
        "Family",
        "Friend",
        "Friends",
        "Couple",
        "Recommend",
        "Recommended",
        "Will",
        "Would",
        "Could",
        "Should",
        "Must",
        "Might",
        "North",
        "South",
        "East",
        "West",
        "Downtown",
        "Uptown",
        "USA",
        "US",
        "UK",
        "EU",
        "City",
        "Town",
        "Park",
        "Lake",
        "River",
        "Mountain",
        "Spring",
        "Summer",
        "Fall",
        "Winter",
        "Morning",
        "Evening",
        "Night",
        "Day",
        "Days",
        "Weekend",
    ]
)

# Single capitalized word, 2-19 chars after the first letter.
_NAME_TOKEN_RE = re.compile(r"\b([A-Z][a-zA-Z]{1,19})\b")

# "<Name>'s house", "<Name>'s home", "<Name>'s place/property/apartment/..."
# Strongest signal that the named person owns / operates the listing.
_POSSESSIVE_OWNERSHIP_RE = re.compile(
    r"\b([A-Z][a-zA-Z]{1,19})(?:'s|s')\s+"
    r"(?:house|home|place|property|apartment|cottage|cabin|villa|condo|loft|rental)\b",
    re.IGNORECASE,
)

# "owner X", "X owns", "X owned", "X hosts" -- explicit ownership/host
# attribution.
_EXPLICIT_OWNERSHIP_RE = re.compile(
    r"(?:(?:owner|host|hostess|landlord|landlady)\s+([A-Z][a-zA-Z]{1,19}))"
    r"|(?:\b([A-Z][a-zA-Z]{1,19})\s+(?:owns|owned|hosts|hosted|manages|managed))",
)

# "<Name>'s father / mother / dad / mom / husband / wife / ..." -- family
# operates the listing on behalf of the named person.
_FAMILY_RELATION_RE = re.compile(
    r"\b([A-Z][a-zA-Z]{1,19})(?:'s|s')\s+"
    r"(?:father|mother|dad|mom|husband|wife|brother|sister|son|daughter|partner)\b",
    re.IGNORECASE,
)

_EMAIL_REDACT_RE = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
_PHONE_REDACT_RE = re.compile(
    r"\b(?:\+?\d{1,3}[\s.\-]?)?(?:\(?\d{3}\)?[\s.\-]?)?\d{3}[\s.\-]?\d{4}\b"
)


def _redact_pii(text: str) -> str:
    """Naomi gate: redact emails + phone numbers from review text.
    Guests sometimes leave their personal contact info in reviews; that
    data must never persist past this scan."""
    if not isinstance(text, str):
        return ""
    out = _EMAIL_REDACT_RE.sub("<email-redacted>", text)
    out = _PHONE_REDACT_RE.sub("<phone-redacted>", out)
    return out


def _normalize_host_name_tokens(name: str) -> set[str]:
    """Return casefolded first-name tokens that should count as the host.
    Splits multi-word names so 'Jolie Smith' matches 'Jolie' alone."""
    if not isinstance(name, str):
        return set()
    name = name.strip()
    if not name:
        return set()
    tokens = re.split(r"[\s\-]+", name)
    out: set[str] = set()
    for t in tokens:
        t = t.strip(".,;:!?'\"")
        if not t:
            continue
        out.add(t.casefold())
    return out


def review_owner_mention_scan(host_name: str, reviews: list[str]) -> dict[str, Any]:
    """Universal review owner-mention scanner.

    PV-critical primitive (user directive 2026-05-15): every per-platform
    listing extractor calls this and emits the result in the listing
    payload as `owner_mention`. Flags impersonation, undisclosed-cohost,
    and relisting scenarios where the listed host_name doesn't match the
    name(s) guests use in reviews.

    Args:
      host_name: the canonical host name pulled from listing metadata.
        Multi-word names are split into tokens; any token match counts.
      reviews: list of review-text strings (already PII-redacted ideally,
        but we re-redact defensively).

    Returns:
      dict with fields suitable for direct emission as event payload:
        host_name, reviews_scanned, host_name_mentions, other_names,
        possessive_ownership, explicit_ownership, family_relations,
        tier ('info'/'good'/'warn'/'bad'), severity_basis.
    """
    host_tokens = _normalize_host_name_tokens(host_name)
    n_reviews = len(reviews) if isinstance(reviews, list) else 0
    host_mentions = 0
    other_names: dict[str, int] = {}
    possessive: list[str] = []
    explicit: list[str] = []
    family: list[list[str]] = []

    for text in reviews or []:
        if not isinstance(text, str) or not text:
            continue
        # Strip residual HTML + entity-decode + redact PII before scanning.
        clean = re.sub(r"<[^>]+>", " ", text)
        clean = _html.unescape(clean)
        clean = _redact_pii(clean)

        # Possessive: "<Name>'s house".
        for m in _POSSESSIVE_OWNERSHIP_RE.finditer(clean):
            nm = m.group(1)
            if not nm or nm in _NAME_FALSE_POSITIVES:
                continue
            if nm.casefold() in host_tokens:
                continue
            possessive.append(nm)

        # Explicit ownership/host attribution.
        for m in _EXPLICIT_OWNERSHIP_RE.finditer(clean):
            nm = m.group(1) or m.group(2) or ""
            if not nm or nm in _NAME_FALSE_POSITIVES:
                continue
            if nm.casefold() in host_tokens:
                continue
            explicit.append(nm)

        # Family-relation phrases. NOTE: we DO record matches where the
        # named person IS the host -- "Jolie's mother runs the place"
        # means the mother is the actual operator regardless of whether
        # Jolie is the listed host, and that's a real PV signal.
        for m in _FAMILY_RELATION_RE.finditer(clean):
            nm = m.group(1)
            if not nm or nm in _NAME_FALSE_POSITIVES:
                continue
            relation_chunk = clean[m.end(1) : m.end(1) + 40]
            rel_m = re.search(r"(?:'s|s')\s+(\w+)", relation_chunk, re.IGNORECASE)
            relation = rel_m.group(1).lower() if rel_m else "relative"
            family.append([nm, relation])

        # General first-name tokens.
        for m in _NAME_TOKEN_RE.finditer(clean):
            tok = m.group(1)
            if tok in _NAME_FALSE_POSITIVES or len(tok) < 2:
                continue
            if tok.casefold() in host_tokens:
                host_mentions += 1
                continue
            other_names[tok] = other_names.get(tok, 0) + 1

    if n_reviews == 0 or not host_tokens:
        tier = "info"
        basis = "matrix:LISTING_OWNER_DRIFT_INFO"
    elif possessive or explicit:
        tier = "bad"
        basis = "matrix:LISTING_OWNER_DRIFT_BAD"
    elif other_names and host_mentions == 0 or family:
        tier = "warn"
        basis = "matrix:LISTING_OWNER_DRIFT_WARN"
    elif host_mentions > 0 and not other_names:
        tier = "good"
        basis = "matrix:LISTING_OWNER_DRIFT_GOOD"
    elif other_names:
        tier = "warn"
        basis = "matrix:LISTING_OWNER_DRIFT_WARN"
    else:
        tier = "info"
        basis = "matrix:LISTING_OWNER_DRIFT_INFO"

    return {
        "host_name": host_name,
        "reviews_scanned": n_reviews,
        "host_name_mentions": host_mentions,
        "other_names": other_names,
        "possessive_ownership": possessive,
        "explicit_ownership": explicit,
        "family_relations": family,
        "tier": tier,
        "severity_basis": basis,
    }


def _airbnb_extract_listing_id(listing_url: str) -> str | None:
    """Airbnb listing URLs: /rooms/<id> or /rooms/plus/<id> or /h/<slug>/<id>.

    Returns the numeric id string if present, else None.
    """
    try:
        path = urllib.parse.urlparse(listing_url).path
    except Exception:
        return None
    # Match the final numeric segment of typical airbnb listing paths.
    m = re.search(r"/rooms(?:/plus)?/(\d+)", path)
    if m:
        return m.group(1)
    m = re.search(r"/(\d+)(?:\?|$)", path)
    return m.group(1) if m else None


def extract_airbnb(html_body: str, listing_url: str) -> dict[str, Any]:
    """Extract a normalized Airbnb listing record from the page HTML.

    Two-tier:
      1. JSON-LD (schema.org/Product or LodgingBusiness) for title +
         address + image list + aggregateRating.
      2. Deferred-state JSON blob for host name + cohost names + GPS +
         reviews + amenities + bedrooms/bathrooms/guests.

    Missing-field policy: every output key is present with a None / 0 /
    [] / "" default so the dossier renderer can dispatch on absence
    without KeyError.
    """
    blocks = extract_jsonld_blocks(html_body)
    deferred = _airbnb_extract_next_data(html_body) or {}

    # JSON-LD: Product / LodgingBusiness / Place are the schemas Airbnb
    # uses in 2026.
    products = _walk_jsonld(blocks, ("Product", "LodgingBusiness", "Place"))
    primary = products[0] if products else {}

    title = primary.get("name") or ""
    # Image list -- can be a single URL string, an object, or a list.
    img_field = primary.get("image")
    photo_urls: list[str] = []
    if isinstance(img_field, str):
        photo_urls = [img_field]
    elif isinstance(img_field, list):
        photo_urls = [x for x in img_field if isinstance(x, str)]

    # Address -- nested PostalAddress; locality + country + street where present.
    addr = primary.get("address") or {}
    if isinstance(addr, list):
        addr = addr[0] if addr else {}
    address_displayed = ""
    city = (addr.get("addressLocality") if isinstance(addr, dict) else "") or ""
    country = (addr.get("addressCountry") if isinstance(addr, dict) else "") or ""
    if isinstance(addr, dict):
        parts = [
            addr.get("streetAddress") or "",
            city,
            addr.get("addressRegion") or "",
            country,
        ]
        address_displayed = ", ".join(p for p in parts if p)

    # GeoCoordinates -- schema.org/Place style.
    geo = primary.get("geo") or {}
    if isinstance(geo, list):
        geo = geo[0] if geo else {}
    gps_lat = None
    gps_lon = None
    gps_source = "absent"
    if isinstance(geo, dict):
        try:
            lat_raw = geo.get("latitude")
            lon_raw = geo.get("longitude")
            if lat_raw is not None and lon_raw is not None:
                gps_lat = float(lat_raw)
                gps_lon = float(lon_raw)
                gps_source = "json-ld"
        except (TypeError, ValueError):
            pass

    # AggregateRating -- review count + average.
    rating_obj = primary.get("aggregateRating") or {}
    review_count = 0
    review_rating: float | None = None
    if isinstance(rating_obj, dict):
        try:
            review_count = int(rating_obj.get("reviewCount") or 0)
        except (TypeError, ValueError):
            review_count = 0
        try:
            rv = rating_obj.get("ratingValue")
            if rv is not None:
                review_rating = float(rv)
        except (TypeError, ValueError):
            review_rating = None

    # Currency / price -- offers field.
    offers = primary.get("offers") or {}
    if isinstance(offers, list):
        offers = offers[0] if offers else {}
    currency = ""
    nightly_price: float | None = None
    if isinstance(offers, dict):
        currency = offers.get("priceCurrency") or ""
        try:
            p = offers.get("price")
            if p is not None:
                nightly_price = float(p)
        except (TypeError, ValueError):
            nightly_price = None

    # Host name + cohost names + GPS fallback + bedrooms/bathrooms/guests
    # all live in the deferred-state blob. Airbnb's blob schema is huge
    # and changes between deploys; we walk it defensively by recursively
    # searching for keys we care about rather than assuming a fixed path.
    host_name = ""
    host_url = ""
    host_member_since = ""
    host_is_superhost = False
    host_verifications: list[str] = []
    host_response_rate = ""
    host_response_time = ""
    cohost_names: list[str] = []
    cohost_urls: list[str] = []
    bedrooms: int | None = None
    bathrooms: float | None = None
    max_guests: int | None = None
    property_type = ""
    amenities: list[str] = []
    neighborhood = ""

    def _walk(obj: Any, depth: int = 0) -> None:
        # Defensive recursive walk -- depth-capped to avoid pathological
        # JSON-bombs. Collects every key we want by name.
        nonlocal \
            host_name, \
            host_url, \
            host_member_since, \
            host_is_superhost, \
            host_response_rate, \
            host_response_time, \
            gps_lat, \
            gps_lon, \
            gps_source, \
            bedrooms, \
            bathrooms, \
            max_guests, \
            property_type, \
            neighborhood
        if depth > 25 or obj is None:
            return
        if isinstance(obj, dict):
            for k, v in obj.items():
                kl = k.lower() if isinstance(k, str) else ""
                if kl in ("hostname", "host_name") and isinstance(v, str) and not host_name:
                    host_name = v
                elif kl in ("hosturl", "host_url") and isinstance(v, str) and not host_url:
                    host_url = v
                elif (
                    kl in ("memberssince", "membersince", "member_since")
                    and isinstance(v, str)
                    and not host_member_since
                ):
                    host_member_since = v
                elif kl in ("issuperhost", "is_superhost") and isinstance(v, bool):
                    host_is_superhost = host_is_superhost or v
                elif (
                    kl in ("hostresponserate", "responserate")
                    and isinstance(v, str)
                    and not host_response_rate
                ):
                    host_response_rate = v
                elif (
                    kl in ("hostresponsetime", "responsetime")
                    and isinstance(v, str)
                    and not host_response_time
                ):
                    host_response_time = v
                elif (
                    kl in ("verifications", "host_verifications")
                    and isinstance(v, list)
                    and not host_verifications
                ):
                    for x in v:
                        if isinstance(x, str):
                            host_verifications.append(x)
                elif kl in ("lat", "latitude") and gps_lat is None:
                    try:
                        gps_lat = float(v)
                        if gps_lon is not None:
                            gps_source = "deferred-state"
                    except (TypeError, ValueError):
                        pass
                elif kl in ("lng", "longitude") and gps_lon is None:
                    try:
                        gps_lon = float(v)
                        if gps_lat is not None:
                            gps_source = "deferred-state"
                    except (TypeError, ValueError):
                        pass
                elif kl in ("bedrooms",) and isinstance(v, int | float) and bedrooms is None:
                    bedrooms = int(v)
                elif kl in ("bathrooms",) and isinstance(v, int | float) and bathrooms is None:
                    bathrooms = float(v)
                elif (
                    kl
                    in (
                        "guestlimit",
                        "personcapacity",
                        "maxguests",
                        "guests",
                    )
                    and isinstance(v, int | float)
                    and max_guests is None
                ):
                    max_guests = int(v)
                elif (
                    kl in ("propertytype", "roomtype") and isinstance(v, str) and not property_type
                ):
                    property_type = v
                elif (
                    kl in ("neighborhood", "neighbourhood")
                    and isinstance(v, str)
                    and not neighborhood
                ):
                    neighborhood = v
                _walk(v, depth + 1)
        elif isinstance(obj, list):
            for item in obj:
                _walk(item, depth + 1)

    _walk(deferred)

    # ---- Review text extraction (universal owner-mention scan input) ----
    # Airbnb embeds review-card bodies as "comments":"..." in the deferred
    # state JSON. Pull every one (capped), JSON-decode escapes, redact
    # PII guests may have left in, then feed to review_owner_mention_scan.
    review_sample: list[str] = []
    _AIRBNB_COMMENT_RE = re.compile(r'"comments"\s*:\s*"((?:[^"\\]|\\.){10,2000})"')
    for cm in _AIRBNB_COMMENT_RE.finditer(html_body):
        raw_text = cm.group(1)
        try:
            decoded = _json.loads(f'"{raw_text}"')
        except (_json.JSONDecodeError, ValueError):
            decoded = raw_text.replace("\\n", "\n").replace('\\"', '"')
        review_sample.append(_redact_pii(decoded))
        if len(review_sample) >= 50:
            break  # cap to keep payload lean

    # Fire the universal owner-mention scan against the extracted reviews.
    owner_mention = review_owner_mention_scan(host_name, review_sample)

    extraction_tier = "json-ld" if products and not deferred else "mixed" if products else "dom"

    return {
        "platform": "airbnb",
        "listing_url": listing_url,
        "listing_id": _airbnb_extract_listing_id(listing_url) or "",
        "title": title,
        "host_name": host_name,
        "host_url": host_url,
        "host_member_since": host_member_since,
        "host_is_superhost": host_is_superhost,
        "host_verifications": host_verifications,
        "host_response_rate": host_response_rate,
        "host_response_time": host_response_time,
        "cohost_names": cohost_names,
        "cohost_urls": cohost_urls,
        "address_displayed": address_displayed,
        "neighborhood": neighborhood,
        "city": city,
        "country": country,
        "gps_lat": gps_lat,
        "gps_lon": gps_lon,
        "gps_source": gps_source,
        "review_count": review_count,
        "review_rating": review_rating,
        "review_sample": review_sample[:10],  # first 10 redacted bodies
        "review_extracted_count": len(review_sample),
        "owner_mention": owner_mention,
        "photo_urls": photo_urls,
        "amenities": amenities,
        "bedrooms": bedrooms,
        "bathrooms": bathrooms,
        "max_guests": max_guests,
        "property_type": property_type,
        "currency": currency,
        "nightly_price": nightly_price,
        "extraction_tier": extraction_tier,
        "raw_jsonld_count": len(blocks),
    }


# ===========================================================================
# Generic JSON-LD extractor (fallback for platforms without per-platform
# code; works on most schema.org-compliant pages)
# ===========================================================================


def extract_generic_jsonld(html_body: str, listing_url: str, platform: str) -> dict[str, Any]:
    """Platform-agnostic extractor for listings with schema.org JSON-LD.

    Most major platforms (Booking, TripAdvisor, Vrbo, Expedia) ship
    standardized schema.org data. We don't need per-platform parsers for
    them as long as their JSON-LD is complete; this function returns
    the same normalized shape as `extract_airbnb` filled from whatever
    schema.org blocks are present.

    Per-platform parsers can be added incrementally as needed (e.g.
    when a platform's JSON-LD is sparse and we need DOM extraction).
    """
    blocks = extract_jsonld_blocks(html_body)
    products = _walk_jsonld(
        blocks, ("Product", "LodgingBusiness", "Place", "Accommodation", "Hotel")
    )
    primary = products[0] if products else {}

    title = primary.get("name") or ""

    img_field = primary.get("image")
    photo_urls: list[str] = []
    if isinstance(img_field, str):
        photo_urls = [img_field]
    elif isinstance(img_field, list):
        photo_urls = [x for x in img_field if isinstance(x, str)]

    addr = primary.get("address") or {}
    if isinstance(addr, list):
        addr = addr[0] if addr else {}
    address_displayed = ""
    city = ""
    country = ""
    if isinstance(addr, dict):
        city = addr.get("addressLocality") or ""
        country = addr.get("addressCountry") or ""
        parts = [
            addr.get("streetAddress") or "",
            city,
            addr.get("addressRegion") or "",
            country,
        ]
        address_displayed = ", ".join(p for p in parts if p)

    geo = primary.get("geo") or {}
    if isinstance(geo, list):
        geo = geo[0] if geo else {}
    gps_lat = None
    gps_lon = None
    gps_source = "absent"
    if isinstance(geo, dict):
        try:
            lat_raw = geo.get("latitude")
            lon_raw = geo.get("longitude")
            if lat_raw is not None and lon_raw is not None:
                gps_lat = float(lat_raw)
                gps_lon = float(lon_raw)
                gps_source = "json-ld"
        except (TypeError, ValueError):
            pass

    rating_obj = primary.get("aggregateRating") or {}
    review_count = 0
    review_rating: float | None = None
    if isinstance(rating_obj, dict):
        try:
            review_count = int(rating_obj.get("reviewCount") or 0)
        except (TypeError, ValueError):
            review_count = 0
        try:
            rv = rating_obj.get("ratingValue")
            if rv is not None:
                review_rating = float(rv)
        except (TypeError, ValueError):
            review_rating = None

    offers = primary.get("offers") or {}
    if isinstance(offers, list):
        offers = offers[0] if offers else {}
    currency = ""
    nightly_price: float | None = None
    if isinstance(offers, dict):
        currency = offers.get("priceCurrency") or ""
        try:
            p = offers.get("price")
            if p is not None:
                nightly_price = float(p)
        except (TypeError, ValueError):
            nightly_price = None

    # ---- Review text extraction (universal owner-mention scan input) ----
    # Generic JSON-LD path: schema.org/Review entries inline on
    # LodgingBusiness / Hotel / Product blocks. Booking.com,
    # TripAdvisor, Hotels.com all expose at least the most-recent N
    # reviews this way. Per-platform parsers can override this when
    # they want richer DOM-extracted reviews.
    raw_reviews: list[str] = []
    # Author-name pre-population: schema.org Review entries often carry
    # author.name, which is the GUEST's name -- we use it to seed the
    # name-token false-positive set so guest names don't masquerade as
    # owner-mention drift.
    guest_names: set[str] = set()
    for block in blocks:
        for rev in block.get("review") or []:
            if not isinstance(rev, dict):
                continue
            body_text = rev.get("reviewBody") or rev.get("description") or rev.get("text") or ""
            if isinstance(body_text, str) and body_text.strip():
                raw_reviews.append(body_text.strip())
            author = rev.get("author") or {}
            if isinstance(author, dict):
                an = author.get("name")
                if isinstance(an, str) and an.strip():
                    # Only the first-name token to match scanner conventions.
                    first = re.split(r"[\s\-]+", an.strip())[0].strip(".,;:!?'\"")
                    if first:
                        guest_names.add(first)
        if len(raw_reviews) >= 50:
            break  # cap

    # PII redaction defensively (some platforms leak email/phone in
    # review bodies despite TOS).
    review_sample_clean = [_redact_pii(r) for r in raw_reviews]

    # Owner-mention scan. Generic JSON-LD path rarely carries host_name
    # (schema.org LodgingBusiness doesn't standardize host identity);
    # the scan still surfaces ownership-attribution phrasing and
    # possessive-of-name patterns from review text, so it's worth
    # running even with an empty host_name (tier reports as INFO).
    owner_mention = review_owner_mention_scan("", review_sample_clean)
    if guest_names:
        # Filter out guest-name false positives from other_names: if a
        # review mentions another guest from a separate review (common in
        # group stays), that's not owner drift.
        filtered_other = {
            k: v for k, v in owner_mention["other_names"].items() if k not in guest_names
        }
        owner_mention = {**owner_mention, "other_names": filtered_other}

    return {
        "platform": platform,
        "listing_url": listing_url,
        "listing_id": "",
        "title": title,
        "host_name": "",
        "host_url": "",
        "host_member_since": "",
        "host_is_superhost": False,
        "host_verifications": [],
        "host_response_rate": "",
        "host_response_time": "",
        "cohost_names": [],
        "cohost_urls": [],
        "address_displayed": address_displayed,
        "neighborhood": "",
        "city": city,
        "country": country,
        "gps_lat": gps_lat,
        "gps_lon": gps_lon,
        "gps_source": gps_source,
        "review_count": review_count,
        "review_rating": review_rating,
        "review_sample": review_sample_clean[:10],
        "review_extracted_count": len(review_sample_clean),
        "owner_mention": owner_mention,
        "photo_urls": photo_urls,
        "amenities": [],
        "bedrooms": None,
        "bathrooms": None,
        "max_guests": None,
        "property_type": "",
        "currency": currency,
        "nightly_price": nightly_price,
        "extraction_tier": "json-ld" if products else "dom",
        "raw_jsonld_count": len(blocks),
    }


# ===========================================================================
# Booking.com bespoke extractor
# ===========================================================================
#
# Booking.com 2026 page structure (verified live 2026-05-15):
#   - JSON-LD <script type="application/ld+json"> with @type=Hotel:
#       name, description, address (full street!), aggregateRating
#       (ratingValue out of 10 NOT 5, reviewCount), image
#     -- but NO geo block.
#   - GPS lives in DOM attribute on the map-link anchor:
#       <a data-atlas-latlng="40.7458,-73.9882" data-atlas-bbox="...">
#   - Embedded GraphQL/Apollo response carries BasicPropertyData with:
#       id, ufi, location.latitude, location.longitude, location.city,
#       location.countryCode, location.formattedAddress, name
#   - Reviews surface as "positiveText":"..." and "negativeText":"..."
#     in embedded JSON (paired per-review).
#
# Booking's review rating scale is 0-10 (not the 0-5 schema.org typically
# implies). We pass it through as-is and tag scale="0-10" in the output
# so the dossier renderer can label appropriately.

_BOOKING_DATA_ATLAS_LATLNG_RE = re.compile(r'data-atlas-latlng="(-?\d+\.\d+),(-?\d+\.\d+)"')
_BOOKING_BASIC_PROPERTY_RE = re.compile(
    r'"BasicPropertyData"\s*,\s*"id"\s*:\s*(\d+)\s*,\s*'
    r'"ufi"\s*:\s*(\d+)\s*,\s*"location"\s*:\s*\{([^}]+)\}',
    re.DOTALL,
)
_BOOKING_POSITIVE_TEXT_RE = re.compile(r'"positiveText"\s*:\s*"((?:[^"\\]|\\.){5,2000})"')
_BOOKING_NEGATIVE_TEXT_RE = re.compile(r'"negativeText"\s*:\s*"((?:[^"\\]|\\.){5,2000})"')


def _booking_extract_listing_id(listing_url: str) -> str:
    """Booking URLs: /hotel/<country>/<slug>.html.
    Returns the slug portion as a stable id token."""
    try:
        path = urllib.parse.urlparse(listing_url).path
    except Exception:
        return ""
    m = re.search(r"/hotel/[a-z]{2}/([a-z0-9\-]+)\.html", path)
    return m.group(1) if m else ""


def extract_booking(html_body: str, listing_url: str) -> dict[str, Any]:
    """Extract a normalized Booking.com listing record.

    Strategy:
      1. JSON-LD `@type=Hotel` for name/description/full-address/rating
         (rating is 0-10 scale; passed through as-is + tagged scale=0-10).
      2. data-atlas-latlng DOM attribute for GPS.
      3. BasicPropertyData JSON regex for internal id + city + canonical
         formatted-address.
      4. positiveText + negativeText regex for review-text pairs.
      5. Universal owner-mention scan on all review text (Booking
         doesn't surface host names in standard markup, so the scan
         primarily catches explicit-ownership phrasing in review text).
    """
    blocks = extract_jsonld_blocks(html_body)
    products = _walk_jsonld(blocks, ("Hotel", "LodgingBusiness", "Product"))
    primary = products[0] if products else {}

    title = primary.get("name") or ""
    description = primary.get("description") or ""
    description = description[:600]

    addr = primary.get("address") or {}
    if isinstance(addr, list):
        addr = addr[0] if addr else {}
    address_displayed = ""
    city = ""
    country = ""
    if isinstance(addr, dict):
        city = addr.get("addressLocality") or ""
        country = addr.get("addressCountry") or ""
        parts = [
            addr.get("streetAddress") or "",
            city,
            addr.get("addressRegion") or "",
            country,
        ]
        address_displayed = ", ".join(p for p in parts if p)

    rating_obj = primary.get("aggregateRating") or {}
    review_count = 0
    review_rating: float | None = None
    if isinstance(rating_obj, dict):
        try:
            review_count = int(rating_obj.get("reviewCount") or 0)
        except (TypeError, ValueError):
            review_count = 0
        try:
            rv = rating_obj.get("ratingValue")
            if rv is not None:
                review_rating = float(rv)
        except (TypeError, ValueError):
            review_rating = None

    img_field = primary.get("image")
    photo_urls: list[str] = []
    if isinstance(img_field, str):
        photo_urls = [img_field]
    elif isinstance(img_field, list):
        photo_urls = [x for x in img_field if isinstance(x, str)]

    # GPS from data-atlas-latlng DOM attribute (JSON-LD has none).
    gps_lat: float | None = None
    gps_lon: float | None = None
    gps_source = "absent"
    m_gps = _BOOKING_DATA_ATLAS_LATLNG_RE.search(html_body)
    if m_gps:
        try:
            gps_lat = float(m_gps.group(1))
            gps_lon = float(m_gps.group(2))
            gps_source = "data-atlas-latlng"
        except (TypeError, ValueError):
            pass

    # BasicPropertyData for internal id + canonical city + GPS fallback.
    # Booking's JSON-LD `addressLocality` is unreliable (Booking puts the
    # street address there); BasicPropertyData's `location.city` is the
    # canonical city name. Always prefer it when present.
    booking_property_id = ""
    m_bp = _BOOKING_BASIC_PROPERTY_RE.search(html_body)
    if m_bp:
        booking_property_id = m_bp.group(1)
        loc_chunk = m_bp.group(3)
        mc = re.search(r'"city"\s*:\s*"([^"]+)"', loc_chunk)
        if mc:
            city = mc.group(1)  # override JSON-LD's mis-populated locality
        if gps_lat is None or gps_lon is None:
            ml = re.search(r'"latitude"\s*:\s*(-?\d+\.?\d*)', loc_chunk)
            mlo = re.search(r'"longitude"\s*:\s*(-?\d+\.?\d*)', loc_chunk)
            if ml and mlo:
                try:
                    gps_lat = float(ml.group(1))
                    gps_lon = float(mlo.group(1))
                    gps_source = "basic-property-data"
                except (TypeError, ValueError):
                    pass
        # Also prefer the BasicPropertyData formattedAddress over the
        # concatenated JSON-LD parts (cleaner string).
        mfa = re.search(r'"formattedAddress"\s*:\s*"([^"]+)"', loc_chunk)
        if mfa:
            address_displayed = mfa.group(1)

    # Review-text extraction. Booking pairs positiveText + negativeText
    # per-review; we concatenate them as a single review body for the
    # owner-mention scan input.
    raw_reviews: list[str] = []
    pos_iter = _BOOKING_POSITIVE_TEXT_RE.finditer(html_body)
    neg_iter = _BOOKING_NEGATIVE_TEXT_RE.finditer(html_body)
    pos_texts = []
    neg_texts = []
    for m in pos_iter:
        try:
            pos_texts.append(_json.loads(f'"{m.group(1)}"'))
        except (ValueError, _json.JSONDecodeError):
            pos_texts.append(m.group(1))
    for m in neg_iter:
        try:
            neg_texts.append(_json.loads(f'"{m.group(1)}"'))
        except (ValueError, _json.JSONDecodeError):
            neg_texts.append(m.group(1))
    # Pair positive + negative as a single review (Booking renders them
    # side-by-side in the review card). If counts differ, fall back to
    # whichever has the bigger sample size.
    for i in range(max(len(pos_texts), len(neg_texts))):
        parts = []
        if i < len(pos_texts):
            parts.append("Positive: " + pos_texts[i])
        if i < len(neg_texts):
            parts.append("Negative: " + neg_texts[i])
        if parts:
            raw_reviews.append(" | ".join(parts))
        if len(raw_reviews) >= 50:
            break

    review_sample_clean = [_redact_pii(r) for r in raw_reviews]
    owner_mention = review_owner_mention_scan("", review_sample_clean)

    return {
        "platform": "booking",
        "listing_url": listing_url,
        "listing_id": booking_property_id or _booking_extract_listing_id(listing_url),
        "title": title,
        "host_name": "",  # Booking doesn't surface host identity in standard markup
        "host_url": "",
        "host_member_since": "",
        "host_is_superhost": False,
        "host_verifications": [],
        "host_response_rate": "",
        "host_response_time": "",
        "cohost_names": [],
        "cohost_urls": [],
        "address_displayed": address_displayed,
        "neighborhood": "",
        "city": city,
        "country": country,
        "gps_lat": gps_lat,
        "gps_lon": gps_lon,
        "gps_source": gps_source,
        "review_count": review_count,
        "review_rating": review_rating,
        "review_rating_scale": "0-10",  # Booking-specific; differs from Airbnb's 0-5
        "review_sample": review_sample_clean[:10],
        "review_extracted_count": len(review_sample_clean),
        "owner_mention": owner_mention,
        "photo_urls": photo_urls,
        "amenities": [],
        "bedrooms": None,
        "bathrooms": None,
        "max_guests": None,
        "property_type": primary.get("@type") or "",
        "currency": "",
        "nightly_price": None,
        "extraction_tier": "mixed" if products and m_gps else "json-ld" if products else "dom",
        "raw_jsonld_count": len(blocks),
    }


# Per-platform extractor dispatch. Airbnb + Booking have bespoke parsing
# for richer data than schema.org JSON-LD alone provides; the others
# fall through to extract_generic_jsonld until their bespoke parsers
# land. Per-platform parsers can be added incrementally; each MUST
# preserve the owner_mention field by calling review_owner_mention_scan
# on extracted review text (user directive 2026-05-15).
_PLATFORM_EXTRACTORS: dict[str, Any] = {
    "airbnb": extract_airbnb,
    "booking": extract_booking,
}


def _listing_fetch(
    url: str,
    *,
    platform: str | None = None,
    investigation_id: str = "default",
    timeout_s: float = 90.0,
) -> tuple[int, str]:
    """Humanized listing fetch (Ship 8 OPSEC).

    Routes through `osint_goblin_workers.humanize.HumanizedFetcher` for
    a full anti-bot bypass stack: persistent BrowserContext per
    investigation, per-platform warm-up flow (homepage -> accept-cookies
    -> search -> click into listing), UA + referer rotation, synthetic
    mouse + scroll interaction, optional Tor egress via env flag.

    Why humanized rather than naked StealthyFetcher: travel platforms
    (especially VRBO + Booking) rate-limit any client that deep-links to
    listing URLs without a believable browse session. Pressure-test
    2026-05-15 confirmed VRBO 429s on the second deep-link from the
    same IP within 60s. The humanized fetcher's persistent context +
    warm-up flow avoids that detection class.
    """
    from .humanize import HumanizedFetcher

    # Per-investigation fetcher instance; falls back to default for
    # callers that don't carry an investigation_id (synthetic tests +
    # cmd-K manual dispatches).
    fetcher = HumanizedFetcher(investigation_id=investigation_id)
    try:
        return fetcher.fetch(
            url,
            platform=platform,
            timeout_s=timeout_s,
            jitter=True,
            synthetic_interaction=True,
        )
    finally:
        # The single-shot use here ALWAYS shreds the fetcher on exit so
        # the browser process doesn't leak. Future Ship-10 work that
        # wants multi-fetch session continuity will instantiate
        # HumanizedFetcher directly + manage the lifecycle.
        fetcher.shred()


# ===========================================================================
# listing_scrape adapter
# ===========================================================================


def listing_scrape(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Scrape a travel-platform listing URL and emit normalized listing data.

    Payload:
      {"listing_url": "https://www.airbnb.com/rooms/12345678"}

    Emits one `listing-data` event per successful extraction, plus one
    `tool-run-result` summary. On unrecognized platform, emits a single
    `tool-run-result` with `skipped: True`.

    Per-platform extractors:
      - Airbnb: bespoke (JSON-LD + Apollo deferred-state walk for host,
        cohost, GPS, amenities)
      - Booking / TripAdvisor / Vrbo / Expedia / Hipcamp / etc.: generic
        schema.org JSON-LD extractor. Falls back gracefully if the
        platform's JSON-LD is sparse; per-platform parsers can be added
        as needed.

    Naomi gate: queries (listing URLs) never logged; extracted PII
    surfaces only via SSE event stream + in-tab dossier.
    """
    listing_url = (payload.get("listing_url") or "").strip()
    if not listing_url:
        return [
            {
                "event_type": "tool-run-result",
                "payload": {
                    "adapter_id": "listing_scrape",
                    "skipped": True,
                    "reason": "no listing_url provided",
                },
            }
        ]

    platform = detect_platform(listing_url)
    if platform is None:
        return [
            {
                "event_type": "tool-run-result",
                "payload": {
                    "adapter_id": "listing_scrape",
                    "skipped": True,
                    "reason": (
                        f"unrecognized platform host in {listing_url!r}; "
                        f"supported: {sorted(set(_PLATFORM_HOST_MAP.values()))}"
                    ),
                },
            }
        ]

    investigation_id = (payload.get("investigation_id") or "default").strip() or "default"
    status, body = _listing_fetch(
        listing_url,
        platform=platform,
        investigation_id=investigation_id,
    )
    if status != 200 or not body:
        return [
            {
                "event_type": "tool-run-result",
                "payload": {
                    "adapter_id": "listing_scrape",
                    "platform": platform,
                    "listing_url": listing_url,
                    "skipped": True,
                    "reason": f"fetch failed: status={status} body_len={len(body)}",
                },
            }
        ]

    extractor = _PLATFORM_EXTRACTORS.get(platform)
    if extractor is not None:
        data = extractor(body, listing_url)
    else:
        data = extract_generic_jsonld(body, listing_url, platform)

    return [
        {
            "event_type": "listing-data",
            "payload": {
                "source": "listing",
                **data,
                "confidence": "firm",  # platform-published data
            },
        },
        {
            "event_type": "tool-run-result",
            "payload": {
                "adapter_id": "listing_scrape",
                "platform": platform,
                "listing_url": listing_url,
                "extraction_tier": data.get("extraction_tier", "unknown"),
                "fetch_method": "humanized-patchright",  # Ship 8 OPSEC
                "investigation_id": investigation_id,
            },
        },
    ]


def _listing_scrape_synthetic(payload: dict[str, Any]) -> list[dict[str, Any]]:
    listing_url = (payload.get("listing_url") or "https://www.airbnb.com/rooms/12345").strip()
    platform = detect_platform(listing_url) or "airbnb"
    return [
        {
            "event_type": "listing-data",
            "payload": {
                "source": "listing",
                "platform": platform,
                "listing_url": listing_url,
                "listing_id": "12345",
                "title": "Synthetic Cozy 2BR (test)",
                "host_name": "Test Host",
                "host_url": f"https://www.{platform}.com/users/show/99999",
                "host_member_since": "January 2020",
                "host_is_superhost": True,
                "host_verifications": ["email", "phone"],
                "host_response_rate": "100%",
                "host_response_time": "within an hour",
                "cohost_names": [],
                "cohost_urls": [],
                "address_displayed": "Cambridge, Massachusetts, United States",
                "neighborhood": "Mid-Cambridge",
                "city": "Cambridge",
                "country": "United States",
                "gps_lat": 42.3736,
                "gps_lon": -71.1097,
                "gps_source": "json-ld",
                "review_count": 127,
                "review_rating": 4.92,
                "review_sample": [],
                "photo_urls": ["https://example.com/synthetic-photo.jpg"],
                "amenities": ["Wifi", "Kitchen"],
                "bedrooms": 2,
                "bathrooms": 1.0,
                "max_guests": 4,
                "property_type": "Apartment",
                "currency": "USD",
                "nightly_price": 145.0,
                "extraction_tier": "synthetic",
                "raw_jsonld_count": 0,
                "confidence": "firm",
                "synthetic": True,
            },
        },
        {
            "event_type": "tool-run-result",
            "payload": {
                "adapter_id": "listing_scrape",
                "platform": platform,
                "listing_url": listing_url,
                "extraction_tier": "synthetic",
                "synthetic": True,
            },
        },
    ]


# ===========================================================================
# Registry
# ===========================================================================

_REGISTRY = get_registry()
_REGISTRY.register(
    "listing_scrape",
    listing_scrape,
    synthetic_mode=_listing_scrape_synthetic,
    in_process=True,
    description=(
        "Travel-platform listing extractor (W20.tr). Takes a listing URL "
        "(Airbnb, VRBO, Booking, TripAdvisor, Yanolja, Leboncoin, "
        "Expedia, Hipcamp, etc.); returns normalized host/cohost/location/"
        "GPS-pin/reviews via Scrapling StealthyFetcher + JSON-LD + DOM "
        "extraction. Naomi-logless."
    ),
)

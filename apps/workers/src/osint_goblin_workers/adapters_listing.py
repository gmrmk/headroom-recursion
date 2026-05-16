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
    "trip.com": "tripcom",  # CN-owned, separate from TripAdvisor
    "ctrip.com": "tripcom",  # CN sibling brand
    "9flats.com": "9flats",
    "ostrovok.ru": "ostrovok",  # RU
    "despegar.com": "despegar",  # LATAM
    "makemytrip.com": "makemytrip",  # IN
    "ferienhausmiete.de": "ferienhausmiete",  # DE
    "homestay.com": "homestay",
    "couchsurfing.com": "couchsurfing",
    "marriott.com": "marriott_homes_villas",  # /homes-and-villas/ path
    "tujia.com": "tujia",  # CN Airbnb-equivalent
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


# Per-platform extractor dispatch. Airbnb has bespoke parsing for the
# deferred-state blob (richer than its JSON-LD); the others fall back
# to schema.org JSON-LD which is usually sufficient. Per-platform
# parsers can be added incrementally.
_PLATFORM_EXTRACTORS: dict[str, Any] = {
    "airbnb": extract_airbnb,
}


def _listing_fetch(url: str, timeout_s: float = 60.0) -> tuple[int, str]:
    """Fetch a listing page via Scrapling's StealthyFetcher.

    Travel platforms uniformly require JS-rendered scrape: Airbnb checks
    for browser fingerprint, Booking serves a captcha to non-browsers,
    Yanolja redirects non-CN-IP requests through a verification page.
    StealthyFetcher (Patchright) handles all of these.
    """
    try:
        from scrapling.fetchers import StealthyFetcher
    except ImportError:
        return (0, "")
    try:
        page = StealthyFetcher.fetch(
            url,
            headless=True,
            network_idle=True,
            timeout=int(timeout_s * 1000),
        )
        body = getattr(page, "html_content", None) or getattr(page, "text", "") or ""
        if isinstance(body, bytes | bytearray):
            body = bytes(body).decode("utf-8", errors="replace")
        return (int(getattr(page, "status", 0) or 0), body)
    except Exception:
        return (0, "")


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

    status, body = _listing_fetch(listing_url)
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
                "fetch_method": "scrapling-stealthy-patchright",
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

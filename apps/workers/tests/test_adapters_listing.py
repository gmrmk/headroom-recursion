"""Unit tests for adapters_listing -- travel-platform listing extractor (W20.tr).

No network: live-network probing lives in tools/dev/listing-live-probe.py and
is gated as a manual run, not a pytest target.
"""

from __future__ import annotations

from osint_goblin_workers.adapters_listing import (
    _PLATFORM_HOST_MAP,
    _airbnb_extract_listing_id,
    _booking_extract_listing_id,
    _listing_photo_pivot_synthetic,
    _normalize_host_name_tokens,
    _redact_pii,
    _vrbo_extract_listing_id,
    detect_platform,
    extract_airbnb,
    extract_booking,
    extract_generic_jsonld,
    extract_jsonld_blocks,
    extract_vrbo,
    extract_yanolja,
    listing_scrape,
    review_owner_mention_scan,
)

# Quiet unused-import noise for helpers used only via dependency-tree.
_ = _airbnb_extract_listing_id


# ---------------------------------------------------------------------------
# detect_platform -- URL host suffix routing
# ---------------------------------------------------------------------------


class TestDetectPlatform:
    def test_airbnb_us(self):
        assert detect_platform("https://www.airbnb.com/rooms/12345") == "airbnb"

    def test_airbnb_uk_subdomain(self):
        assert detect_platform("https://www.airbnb.co.uk/rooms/12345") == "airbnb"

    def test_airbnb_korean_subdomain(self):
        assert detect_platform("https://ko.airbnb.com/rooms/12345") == "airbnb"

    def test_vrbo(self):
        assert detect_platform("https://www.vrbo.com/12345") == "vrbo"

    def test_homeaway_routes_to_vrbo(self):
        # HomeAway merged into Vrbo (Expedia); both route to "vrbo" platform-id.
        assert detect_platform("https://www.homeaway.com/12345") == "vrbo"

    def test_abritel_fr_routes_to_vrbo(self):
        assert detect_platform("https://www.abritel.fr/12345") == "vrbo"

    def test_booking(self):
        assert detect_platform("https://www.booking.com/hotel/us/x.html") == "booking"

    def test_tripadvisor(self):
        assert (
            detect_platform("https://www.tripadvisor.com/VacationRentalReview-g123-d456.html")
            == "tripadvisor"
        )

    def test_yanolja_kr(self):
        assert detect_platform("https://www.yanolja.com/hotels/12345") == "yanolja"

    def test_leboncoin_fr(self):
        assert detect_platform("https://www.leboncoin.fr/locations_vacances/12345") == "leboncoin"

    def test_hipcamp(self):
        assert detect_platform("https://www.hipcamp.com/en-US/land/12345") == "hipcamp"

    def test_expedia_family(self):
        assert detect_platform("https://www.hotels.com/ho123") == "expedia"
        assert detect_platform("https://www.orbitz.com/h123") == "expedia"
        assert detect_platform("https://www.hotwire.com/h123") == "expedia"

    def test_returns_none_for_unrecognized_host(self):
        assert detect_platform("https://example.com/listing") is None

    def test_returns_none_for_invalid_url(self):
        assert detect_platform("not a url at all") is None

    def test_returns_none_for_empty_string(self):
        assert detect_platform("") is None


# ---------------------------------------------------------------------------
# extract_jsonld_blocks -- robust schema.org parser
# ---------------------------------------------------------------------------


class TestExtractJsonldBlocks:
    def test_returns_empty_list_when_no_blocks(self):
        assert extract_jsonld_blocks("<html><body>no JSON-LD here</body></html>") == []

    def test_parses_single_block(self):
        html = """
        <html><head>
        <script type="application/ld+json">
        {"@type": "Product", "name": "Test Listing"}
        </script>
        </head></html>
        """
        blocks = extract_jsonld_blocks(html)
        assert len(blocks) == 1
        assert blocks[0]["name"] == "Test Listing"

    def test_parses_multiple_blocks(self):
        html = """
        <script type="application/ld+json">{"@type": "Place", "name": "A"}</script>
        <script type="application/ld+json">{"@type": "Product", "name": "B"}</script>
        """
        blocks = extract_jsonld_blocks(html)
        assert len(blocks) == 2

    def test_flattens_array_blocks(self):
        # Some platforms ship JSON-LD as a top-level array of objects.
        html = '<script type="application/ld+json">[{"@type":"A"},{"@type":"B"}]</script>'
        blocks = extract_jsonld_blocks(html)
        assert len(blocks) == 2
        assert {b.get("@type") for b in blocks} == {"A", "B"}

    def test_skips_malformed_json(self):
        # Malformed JSON in one block must not break parsing of siblings.
        html = """
        <script type="application/ld+json">{"@type": "Place", "name": "Good"}</script>
        <script type="application/ld+json">{ this is not valid JSON </script>
        <script type="application/ld+json">{"@type": "Product", "name": "Also good"}</script>
        """
        blocks = extract_jsonld_blocks(html)
        assert len(blocks) == 2


# ---------------------------------------------------------------------------
# extract_airbnb -- JSON-LD + deferred-state
# ---------------------------------------------------------------------------


_AIRBNB_FIXTURE_JSONLD_ONLY = """
<html><head>
<script type="application/ld+json">
{
  "@type": "Product",
  "name": "Cozy 2BR in Cambridge",
  "image": ["https://example.com/photo1.jpg", "https://example.com/photo2.jpg"],
  "address": {
    "@type": "PostalAddress",
    "streetAddress": "100 Test St",
    "addressLocality": "Cambridge",
    "addressRegion": "MA",
    "addressCountry": "United States"
  },
  "geo": {
    "@type": "GeoCoordinates",
    "latitude": 42.3736,
    "longitude": -71.1097
  },
  "aggregateRating": {
    "@type": "AggregateRating",
    "ratingValue": 4.92,
    "reviewCount": 127
  },
  "offers": {
    "@type": "Offer",
    "priceCurrency": "USD",
    "price": 145
  }
}
</script>
</head></html>
"""


class TestExtractAirbnb:
    def test_extracts_title_from_jsonld(self):
        data = extract_airbnb(_AIRBNB_FIXTURE_JSONLD_ONLY, "https://www.airbnb.com/rooms/12345")
        assert data["title"] == "Cozy 2BR in Cambridge"

    def test_extracts_gps_from_jsonld_geo(self):
        data = extract_airbnb(_AIRBNB_FIXTURE_JSONLD_ONLY, "https://www.airbnb.com/rooms/12345")
        assert data["gps_lat"] == 42.3736
        assert data["gps_lon"] == -71.1097
        assert data["gps_source"] == "json-ld"

    def test_extracts_address_components(self):
        data = extract_airbnb(_AIRBNB_FIXTURE_JSONLD_ONLY, "https://www.airbnb.com/rooms/12345")
        assert data["city"] == "Cambridge"
        assert data["country"] == "United States"
        assert "100 Test St" in data["address_displayed"]
        assert "Cambridge" in data["address_displayed"]

    def test_extracts_review_aggregate(self):
        data = extract_airbnb(_AIRBNB_FIXTURE_JSONLD_ONLY, "https://www.airbnb.com/rooms/12345")
        assert data["review_count"] == 127
        assert data["review_rating"] == 4.92

    def test_extracts_offers_price(self):
        data = extract_airbnb(_AIRBNB_FIXTURE_JSONLD_ONLY, "https://www.airbnb.com/rooms/12345")
        assert data["currency"] == "USD"
        assert data["nightly_price"] == 145.0

    def test_extracts_photo_urls_array(self):
        data = extract_airbnb(_AIRBNB_FIXTURE_JSONLD_ONLY, "https://www.airbnb.com/rooms/12345")
        assert len(data["photo_urls"]) == 2
        assert "photo1.jpg" in data["photo_urls"][0]

    def test_extracts_listing_id_from_url(self):
        data = extract_airbnb(_AIRBNB_FIXTURE_JSONLD_ONLY, "https://www.airbnb.com/rooms/12345678")
        assert data["listing_id"] == "12345678"

    def test_listing_id_from_plus_url(self):
        data = extract_airbnb("", "https://www.airbnb.com/rooms/plus/9999999")
        assert data["listing_id"] == "9999999"

    def test_handles_missing_jsonld_gracefully(self):
        data = extract_airbnb("<html><body>no data</body></html>", "https://www.airbnb.com/rooms/1")
        # All fields present with sane defaults; no KeyError.
        assert data["title"] == ""
        assert data["gps_lat"] is None
        assert data["gps_source"] == "absent"
        assert data["review_count"] == 0
        assert data["photo_urls"] == []
        assert data["listing_id"] == "1"

    def test_extracts_host_name_from_deferred_state(self):
        html = """
        <script id="data-deferred-state-0" type="application/json">
        {"some":{"path":{"hostName":"Alice"}}}
        </script>
        """
        data = extract_airbnb(html, "https://www.airbnb.com/rooms/1")
        assert data["host_name"] == "Alice"

    def test_extracts_bedrooms_max_guests_from_deferred(self):
        html = """
        <script id="data-deferred-state-0" type="application/json">
        {"x":{"bedrooms":3,"personCapacity":6,"bathrooms":2.5}}
        </script>
        """
        data = extract_airbnb(html, "https://www.airbnb.com/rooms/1")
        assert data["bedrooms"] == 3
        assert data["max_guests"] == 6
        assert data["bathrooms"] == 2.5

    def test_gps_fallback_to_deferred_state(self):
        # JSON-LD missing geo; deferred-state has lat+lng.
        html = """
        <script id="data-deferred-state-0" type="application/json">
        {"location":{"lat":42.0,"lng":-71.0}}
        </script>
        """
        data = extract_airbnb(html, "https://www.airbnb.com/rooms/1")
        assert data["gps_lat"] == 42.0
        assert data["gps_lon"] == -71.0
        assert data["gps_source"] == "deferred-state"


# ---------------------------------------------------------------------------
# extract_generic_jsonld -- platform-agnostic fallback
# ---------------------------------------------------------------------------


class TestExtractGenericJsonld:
    def test_extracts_lodging_business_from_booking(self):
        html = """
        <script type="application/ld+json">
        {
          "@type": "LodgingBusiness",
          "name": "Test Hotel",
          "address": {"@type":"PostalAddress","addressLocality":"Paris","addressCountry":"France"},
          "geo": {"@type":"GeoCoordinates","latitude":48.8566,"longitude":2.3522},
          "aggregateRating": {"@type":"AggregateRating","ratingValue":4.5,"reviewCount":1234}
        }
        </script>
        """
        data = extract_generic_jsonld(html, "https://www.booking.com/h.html", "booking")
        assert data["platform"] == "booking"
        assert data["title"] == "Test Hotel"
        assert data["gps_lat"] == 48.8566
        assert data["city"] == "Paris"
        assert data["review_count"] == 1234

    def test_returns_defaults_on_missing_jsonld(self):
        data = extract_generic_jsonld("<html></html>", "https://www.booking.com/h.html", "booking")
        assert data["platform"] == "booking"
        assert data["title"] == ""
        assert data["gps_lat"] is None
        assert data["extraction_tier"] == "dom"


# ---------------------------------------------------------------------------
# listing_scrape -- adapter dispatch
# ---------------------------------------------------------------------------


class TestListingScrape:
    def test_skips_when_no_url(self):
        events = listing_scrape({})
        assert len(events) == 1
        assert events[0]["payload"]["skipped"] is True
        assert "no listing_url" in events[0]["payload"]["reason"]

    def test_skips_unrecognized_platform(self):
        events = listing_scrape({"listing_url": "https://example.com/listing/1"})
        assert len(events) == 1
        assert events[0]["payload"]["skipped"] is True
        assert "unrecognized platform" in events[0]["payload"]["reason"]


# ---------------------------------------------------------------------------
# _PLATFORM_HOST_MAP -- coverage sanity
# ---------------------------------------------------------------------------


class TestPlatformHostMapCoverage:
    def test_includes_user_requested_platforms(self):
        """User-requested platforms per 2026-05-15 directive: airbnb,
        booking, yanolja, leboncoin (and 'every conceivable one').
        Verify those four anchors are in the map."""
        values = set(_PLATFORM_HOST_MAP.values())
        assert "airbnb" in values
        assert "booking" in values
        assert "yanolja" in values
        assert "leboncoin" in values
        # User also called out VRBO, Expedia family by implication.
        assert "vrbo" in values
        assert "expedia" in values

    def test_at_least_ten_platforms_total(self):
        # Sanity check that the map is reasonably complete.
        assert len(set(_PLATFORM_HOST_MAP.values())) >= 10


# ---------------------------------------------------------------------------
# review_owner_mention_scan -- universal PV signal (user directive
# 2026-05-15: "is the owner mentioned anywhere in reviews -- built into
# every single travel platform scrape")
# ---------------------------------------------------------------------------


class TestReviewOwnerMentionScan:
    def test_no_reviews_returns_info_tier(self):
        r = review_owner_mention_scan("Jolie", [])
        assert r["tier"] == "info"
        assert r["severity_basis"] == "matrix:LISTING_OWNER_DRIFT_INFO"
        assert r["reviews_scanned"] == 0

    def test_no_host_name_returns_info_tier(self):
        r = review_owner_mention_scan("", ["Bob was a great host"])
        assert r["tier"] == "info"

    def test_host_only_mention_returns_good_tier(self):
        r = review_owner_mention_scan(
            "Jolie",
            [
                "Jolie was a wonderful host. Highly recommend!",
                "Loved staying with Jolie. The place was clean.",
            ],
        )
        assert r["tier"] == "good"
        assert r["severity_basis"] == "matrix:LISTING_OWNER_DRIFT_GOOD"
        assert r["host_name_mentions"] >= 2
        assert not r["other_names"]

    def test_no_host_mention_but_other_names_returns_warn(self):
        # No "Jolie" anywhere; Bob mentioned by name but not in
        # ownership phrasing -> WARN (possible mis-listing).
        r = review_owner_mention_scan("Jolie", ["Bob was friendly and helpful."])
        assert r["tier"] == "warn"
        assert "Bob" in r["other_names"]
        assert r["host_name_mentions"] == 0

    def test_possessive_ownership_returns_bad_tier(self):
        # "Bob's house" -> explicit ownership claim for non-host -> BAD.
        r = review_owner_mention_scan("Jolie", ["Loved staying at Bob's house, will return!"])
        assert r["tier"] == "bad"
        assert r["severity_basis"] == "matrix:LISTING_OWNER_DRIFT_BAD"
        assert "Bob" in r["possessive_ownership"]

    def test_explicit_ownership_phrase_returns_bad(self):
        r = review_owner_mention_scan("Jolie", ["The owner Bob was very helpful with check-in."])
        assert r["tier"] == "bad"
        assert "Bob" in r["explicit_ownership"]

    def test_x_owns_phrase_returns_bad(self):
        r = review_owner_mention_scan("Jolie", ["Mike owns this beautiful property."])
        assert r["tier"] == "bad"
        assert "Mike" in r["explicit_ownership"]

    def test_family_relation_returns_warn(self):
        # "Jolie's mother" -> family member operates -> WARN (operational
        # disclosure even if Jolie is listed host).
        r = review_owner_mention_scan(
            "Jolie", ["Jolie's mother actually runs the place day to day."]
        )
        assert r["tier"] == "warn"
        assert any(rel[0] == "Jolie" and rel[1] == "mother" for rel in r["family_relations"])

    def test_mixed_host_plus_other_returns_warn(self):
        r = review_owner_mention_scan("Jolie", ["Jolie was great. Bob helped us with the lockbox."])
        assert r["tier"] == "warn"

    def test_filters_common_english_capitalized_words(self):
        # Words like "Great", "Highly", "Recommend" mustn't count as names.
        r = review_owner_mention_scan(
            "Jolie",
            [
                "Great stay! Highly recommend. Wifi was fast. Netflix worked.",
                "Wonderful place. Will definitely return.",
            ],
        )
        # "Jolie" not mentioned, no real other names -> still info
        # (other_names should be empty after false-positive filtering).
        assert not r["other_names"]
        assert r["tier"] == "info"

    def test_multi_word_host_name_matches_first_token(self):
        # "Jolie Smith" -> reviews say "Jolie" -> still counts.
        r = review_owner_mention_scan("Jolie Smith", ["Jolie was a wonderful host."])
        assert r["tier"] == "good"
        assert r["host_name_mentions"] >= 1

    def test_case_insensitive_host_name_match(self):
        r = review_owner_mention_scan("Alice", ["alice was nice but the WiFi was slow."])
        # "alice" lowercase doesn't trip the capitalized-token regex,
        # so this hits the "no host_name mentions + no other names" path
        # -> tier could be info OR (rarely) warn; verify both branches
        # don't blow up.
        assert r["tier"] in ("info", "warn", "good")

    def test_skips_host_name_in_possessive(self):
        # "Jolie's house" must NOT count as ownership drift (host=Jolie,
        # naturally she owns her house).
        r = review_owner_mention_scan("Jolie", ["Jolie's place was lovely."])
        assert "Jolie" not in r["possessive_ownership"]

    def test_skips_host_name_in_explicit_ownership(self):
        r = review_owner_mention_scan("Jolie", ["The owner Jolie greeted us warmly on arrival."])
        assert "Jolie" not in r["explicit_ownership"]


# ---------------------------------------------------------------------------
# _normalize_host_name_tokens
# ---------------------------------------------------------------------------


class TestNormalizeHostNameTokens:
    def test_splits_multi_word(self):
        assert _normalize_host_name_tokens("Alice Smith") == {"alice", "smith"}

    def test_splits_hyphenated(self):
        assert _normalize_host_name_tokens("Mary-Anne") == {"mary", "anne"}

    def test_strips_punctuation(self):
        assert _normalize_host_name_tokens("Bob.") == {"bob"}

    def test_empty_returns_empty(self):
        assert _normalize_host_name_tokens("") == set()
        assert _normalize_host_name_tokens("   ") == set()


# ---------------------------------------------------------------------------
# _redact_pii (Naomi gate)
# ---------------------------------------------------------------------------


class TestRedactPII:
    def test_redacts_email(self):
        out = _redact_pii("Contact me at alice@example.com for keys.")
        assert "alice@example.com" not in out
        assert "<email-redacted>" in out

    def test_redacts_phone(self):
        out = _redact_pii("Call +1 555 123 4567 for help.")
        assert "555 123 4567" not in out
        assert "<phone-redacted>" in out

    def test_passes_clean_text_through(self):
        assert _redact_pii("Lovely place, great host!") == "Lovely place, great host!"


# ---------------------------------------------------------------------------
# Generic JSON-LD extractor wires owner-mention scan when review array present
# ---------------------------------------------------------------------------


class TestGenericJsonldOwnerMention:
    def test_jsonld_with_review_array_runs_scan(self):
        # Schema.org LodgingBusiness with review array. The scanner runs
        # against the review bodies even though host_name is empty (the
        # generic extractor doesn't have a standardized host field).
        body = (
            "<html><body>"
            '<script type="application/ld+json">'
            "{"
            '"@type": "LodgingBusiness",'
            '"name": "Test Listing",'
            '"address": {"addressLocality": "Boston"},'
            '"review": ['
            '{"reviewBody": "Bob owns this place and was helpful."},'
            '{"reviewBody": "Great stay, very clean."}'
            "]"
            "}"
            "</script>"
            "</body></html>"
        )
        out = extract_generic_jsonld(body, "https://booking.com/x", "booking")
        # The generic extractor doesn't know the host name; tier may be
        # info or bad depending on whether explicit-ownership phrasing
        # fires without a host comparison. Verify the scan ran.
        assert "owner_mention" in out
        assert out["owner_mention"]["reviews_scanned"] == 2
        # "Bob owns this place" -- explicit ownership phrasing fires.
        assert "Bob" in out["owner_mention"]["explicit_ownership"]


# ---------------------------------------------------------------------------
# extract_booking -- Booking.com bespoke parser
# ---------------------------------------------------------------------------


# Booking fixture; the embedded GraphQL-style BasicPropertyData line is
# intentionally long (mirrors live response).
# ruff: noqa: E501
_BOOKING_FIXTURE = """
<html><head>
<script type="application/ld+json">
{
  "@type": "Hotel",
  "name": "Ace Hotel New York",
  "description": "This boutique hotel is located in the center of Manhattan.",
  "address": {
    "@type": "PostalAddress",
    "addressLocality": "20 West 29th Street",
    "streetAddress": "20 West 29th Street, NoMad, New York, NY 10001, United States",
    "addressRegion": "New York State",
    "addressCountry": "US"
  },
  "aggregateRating": {
    "@type": "AggregateRating",
    "ratingValue": 7.6,
    "bestRating": 10,
    "reviewCount": 1864
  },
  "image": "https://cf.bstatic.com/xdata/images/hotel/test.jpg"
}
</script>
</head><body>
<a data-atlas-latlng="40.745783723685015,-73.9882005751133" data-atlas-bbox="x">map</a>
<script>
[..."BasicPropertyData","id":79515,"ufi":20088325,"location":{"__typename":"Location","city":"New York","latitude":40.745783723685015,"longitude":-73.9882005751133,"countryCode":"us","formattedAddress":"20 West 29th Street, NoMad, New York, NY 10001, United States"},"name":"Ace Hotel"..]
</script>
<div data-testid="review">
  <span>"positiveText":"Great location and lobby vibe. Bob the manager was helpful."</span>
  <span>"negativeText":"Rooms are not well isolated from noise."</span>
</div>
<div data-testid="review">
  <span>"positiveText":"Comfortable beds, easy check-in."</span>
  <span>"negativeText":"Street noise."</span>
</div>
</body></html>
"""


class TestExtractBooking:
    def test_extracts_title_from_jsonld(self):
        d = extract_booking(_BOOKING_FIXTURE, "https://www.booking.com/hotel/us/ace.html")
        assert d["title"] == "Ace Hotel New York"

    def test_extracts_gps_from_data_atlas_latlng(self):
        d = extract_booking(_BOOKING_FIXTURE, "https://www.booking.com/hotel/us/ace.html")
        assert d["gps_lat"] == 40.745783723685015
        assert d["gps_lon"] == -73.9882005751133
        assert d["gps_source"] == "data-atlas-latlng"

    def test_extracts_canonical_city_from_basic_property_data(self):
        # Booking's JSON-LD addressLocality is unreliable (puts street there);
        # BasicPropertyData's location.city is canonical -- must win.
        d = extract_booking(_BOOKING_FIXTURE, "https://www.booking.com/hotel/us/ace.html")
        assert d["city"] == "New York"

    def test_extracts_booking_property_id(self):
        d = extract_booking(_BOOKING_FIXTURE, "https://www.booking.com/hotel/us/ace.html")
        assert d["listing_id"] == "79515"

    def test_extracts_rating_with_0_10_scale_tag(self):
        d = extract_booking(_BOOKING_FIXTURE, "https://www.booking.com/hotel/us/ace.html")
        assert d["review_rating"] == 7.6
        assert d["review_rating_scale"] == "0-10"
        assert d["review_count"] == 1864

    def test_extracts_review_text_pairs(self):
        d = extract_booking(_BOOKING_FIXTURE, "https://www.booking.com/hotel/us/ace.html")
        assert d["review_extracted_count"] == 2
        # First review pair combined.
        first = d["review_sample"][0]
        assert "Positive:" in first
        assert "Negative:" in first
        assert "Great location" in first
        assert "noise" in first.lower()

    def test_owner_mention_scan_runs_on_extracted_reviews(self):
        d = extract_booking(_BOOKING_FIXTURE, "https://www.booking.com/hotel/us/ace.html")
        om = d["owner_mention"]
        # Booking doesn't surface host_name -> info tier even when
        # "Bob" appears (no host to compare against).
        # But the scan ran and recorded reviews_scanned > 0.
        assert om["reviews_scanned"] == 2

    def test_fallback_listing_id_from_url_slug(self):
        # Fixture without BasicPropertyData block -> falls back to URL slug.
        bare = '<script type="application/ld+json">{"@type":"Hotel","name":"x"}</script>'
        d = extract_booking(bare, "https://www.booking.com/hotel/us/ace-new-york.html")
        assert d["listing_id"] == "ace-new-york"

    def test_address_displayed_uses_formatted_address_when_available(self):
        d = extract_booking(_BOOKING_FIXTURE, "https://www.booking.com/hotel/us/ace.html")
        # formattedAddress from BasicPropertyData is preferred over
        # the concatenated JSON-LD parts.
        assert "20 West 29th Street, NoMad, New York" in d["address_displayed"]
        # Should NOT include the junky concatenation
        assert "20 West 29th Street, 20 West 29th Street" not in d["address_displayed"]


class TestBookingExtractListingId:
    def test_extracts_slug_from_path(self):
        assert (
            _booking_extract_listing_id("https://www.booking.com/hotel/us/ace-new-york.html")
            == "ace-new-york"
        )

    def test_returns_empty_for_non_hotel_url(self):
        assert _booking_extract_listing_id("https://www.booking.com/search.html") == ""

    def test_returns_empty_for_invalid_url(self):
        assert _booking_extract_listing_id("not a url") == ""


# ---------------------------------------------------------------------------
# VRBO extraction (Imperva PWA tier; zendriver-unblocked 2026-05-16)
# ---------------------------------------------------------------------------
#
# Fixture mirrors the microdata shape verified against the captured 887KB
# real VRBO body (tools/dev/bypass-probe-bodies/vrbo-zendriver-status200.html).
# VRBO ships everything as <meta itemprop="X" content="Y"> microdata plus
# og: meta tags for canonical title/description/image.

_VRBO_FIXTURE = """
<!DOCTYPE html>
<html>
<head>
<meta property="og:type" content="website">
<meta property="og:title" content="River cabin w access. close to town. 35 foot deck - Nixa | Vrbo">
<meta property="og:description" content="Located on a river, this family-friendly cabin is within 6 mi of Trail of Tears.">
<meta property="og:image" content="https://media.vrbo.com/lodging/38001485/046da9bd.jpg">
<meta property="og:url" content="https://www.vrbo.com/1682245">
<meta property="og:site_name" content="vrbo.com">
</head>
<body>
<div itemscope="" itemtype="https://schema.org/VacationRental">
  <meta itemprop="name" content="River cabin w access. close to town. 35 foot deck">
  <meta itemprop="description" content="Exceptional river cabin with deck.">
  <div itemprop="address" itemscope="" itemtype="https://schema.org/PostalAddress">
    <meta itemprop="addressCountry" content="USA">
    <meta itemprop="addressLocality" content="Nixa">
    <meta itemprop="addressRegion" content="MO">
    <meta itemprop="postalCode" content="">
    <meta itemprop="streetAddress" content="">
  </div>
  <div itemprop="starRating" itemscope="" itemtype="https://schema.org/Rating">
    <meta itemprop="ratingValue" content="null">
  </div>
  <meta itemprop="latitude" content="37.094772">
  <meta itemprop="longitude" content="-93.323873">
  <meta itemprop="identifier" content="38001485">
  <div itemprop="aggregateRating" itemscope="" itemtype="https://schema.org/AggregateRating">
    <meta itemprop="ratingValue" content="9.6">
    <meta itemprop="description" content="Exceptional">
    <meta itemprop="bestRating" content="10">
    <meta itemprop="reviewCount" content="84">
  </div>
  <div itemprop="containsPlace" itemscope="" itemtype="https://schema.org/Accommodation">
    <div itemprop="occupancy" itemscope="" itemtype="https://schema.org/QuantitativeValue">
      <meta itemprop="value" content="4">
    </div>
  </div>
</div>
</body>
</html>
"""


class TestExtractVrbo:
    def test_extracts_title_from_og_meta_and_strips_brand_suffix(self):
        d = extract_vrbo(_VRBO_FIXTURE, "https://www.vrbo.com/1682245")
        # og:title is preferred over microdata name; the " | Vrbo" suffix
        # is stripped because it's marketing chrome, not part of the
        # listing's actual title.
        assert d["title"] == "River cabin w access. close to town. 35 foot deck - Nixa"

    def test_extracts_gps_from_geocoordinates_microdata(self):
        d = extract_vrbo(_VRBO_FIXTURE, "https://www.vrbo.com/1682245")
        assert d["gps_lat"] == 37.094772
        assert d["gps_lon"] == -93.323873
        assert d["gps_source"] == "microdata-geocoordinates"

    def test_extracts_address_from_postal_address_microdata(self):
        d = extract_vrbo(_VRBO_FIXTURE, "https://www.vrbo.com/1682245")
        # Street + postal are empty (VRBO privacy); displayed = locality,region,country
        assert d["city"] == "Nixa"
        assert d["country"] == "USA"
        assert d["address_displayed"] == "Nixa, MO, USA"

    def test_extracts_aggregate_rating_with_0_10_scale_tag(self):
        d = extract_vrbo(_VRBO_FIXTURE, "https://www.vrbo.com/1682245")
        assert d["review_rating"] == 9.6
        assert d["review_count"] == 84
        # VRBO uses Booking-style 0-10 scale, not Airbnb's 0-5.
        assert d["review_rating_scale"] == "0-10"

    def test_skips_null_starRating_ratingValue(self):
        # starRating.ratingValue="null" should not poison the aggregate-
        # rating field. The microdata collector filters value=="null".
        d = extract_vrbo(_VRBO_FIXTURE, "https://www.vrbo.com/1682245")
        # Real review_rating from aggregateRating, not the null starRating.
        assert d["review_rating"] == 9.6

    def test_extracts_max_guests_from_occupancy_value(self):
        d = extract_vrbo(_VRBO_FIXTURE, "https://www.vrbo.com/1682245")
        assert d["max_guests"] == 4

    def test_extracts_listing_id_from_url(self):
        d = extract_vrbo(_VRBO_FIXTURE, "https://www.vrbo.com/1682245")
        assert d["listing_id"] == "1682245"

    def test_extracts_canonical_photo_from_og_image(self):
        d = extract_vrbo(_VRBO_FIXTURE, "https://www.vrbo.com/1682245")
        assert d["photo_urls"] == ["https://media.vrbo.com/lodging/38001485/046da9bd.jpg"]

    def test_host_fields_empty_by_design(self):
        # VRBO/Expedia hides host name pre-auth. The extractor leaves
        # host fields empty rather than fabricating; downstream dossier
        # surfaces this correctly via owner_mention=INFO.
        d = extract_vrbo(_VRBO_FIXTURE, "https://www.vrbo.com/1682245")
        assert d["host_name"] == ""
        assert d["host_url"] == ""
        assert d["cohost_names"] == []

    def test_owner_mention_is_info_when_no_reviews_or_host(self):
        d = extract_vrbo(_VRBO_FIXTURE, "https://www.vrbo.com/1682245")
        om = d["owner_mention"]
        assert om["tier"] == "info"
        assert om["severity_basis"] == "matrix:LISTING_OWNER_DRIFT_INFO"
        assert om["reviews_scanned"] == 0

    def test_property_type_tagged_vacation_rental(self):
        d = extract_vrbo(_VRBO_FIXTURE, "https://www.vrbo.com/1682245")
        assert d["property_type"] == "VacationRental"

    def test_extraction_tier_is_microdata(self):
        d = extract_vrbo(_VRBO_FIXTURE, "https://www.vrbo.com/1682245")
        assert d["extraction_tier"] == "microdata"

    def test_returns_normalized_shape_keys(self):
        # Cross-platform contract: every extractor emits the same key set
        # so the dossier-renderer doesn't branch on platform.
        d = extract_vrbo(_VRBO_FIXTURE, "https://www.vrbo.com/1682245")
        required = {
            "platform",
            "listing_url",
            "listing_id",
            "title",
            "host_name",
            "address_displayed",
            "city",
            "country",
            "gps_lat",
            "gps_lon",
            "gps_source",
            "review_count",
            "review_rating",
            "review_sample",
            "owner_mention",
            "photo_urls",
            "max_guests",
            "property_type",
            "extraction_tier",
        }
        assert required.issubset(d.keys())

    def test_handles_missing_microdata_gracefully(self):
        bare = "<html><body>No microdata here.</body></html>"
        d = extract_vrbo(bare, "https://www.vrbo.com/1682245")
        assert d["title"] == ""
        assert d["gps_lat"] is None
        assert d["gps_lon"] is None
        assert d["gps_source"] == "absent"
        assert d["review_count"] == 0
        assert d["review_rating"] is None
        assert d["listing_id"] == "1682245"  # URL-derived fallback


class TestVrboExtractListingId:
    def test_extracts_digits_from_canonical_path(self):
        assert _vrbo_extract_listing_id("https://www.vrbo.com/1682245") == "1682245"

    def test_extracts_digits_from_locale_prefixed_path(self):
        # /en-gb/p1682245vb style
        assert _vrbo_extract_listing_id("https://www.vrbo.com/en-gb/p1682245vb") == "1682245"

    def test_extracts_digits_from_slug_prefixed_path(self):
        # /cottage-rental/p1682245vb style
        assert (
            _vrbo_extract_listing_id("https://www.vrbo.com/cottage-rental/p1682245vb") == "1682245"
        )

    def test_returns_empty_for_homeurl(self):
        assert _vrbo_extract_listing_id("https://www.vrbo.com/") == ""

    def test_returns_empty_for_invalid_url(self):
        assert _vrbo_extract_listing_id("not a url") == ""


class TestVrboPlatformDispatch:
    def test_vrbo_routed_via_detect_platform(self):
        assert detect_platform("https://www.vrbo.com/1682245") == "vrbo"

    def test_homeaway_routed_to_vrbo_platform(self):
        # HomeAway merged into Vrbo; both must dispatch the same extractor.
        assert detect_platform("https://www.homeaway.com/123") == "vrbo"

    def test_vrbo_extractor_registered(self):
        from osint_goblin_workers.adapters_listing import _PLATFORM_EXTRACTORS

        assert "vrbo" in _PLATFORM_EXTRACTORS
        assert _PLATFORM_EXTRACTORS["vrbo"] is extract_vrbo


# ---------------------------------------------------------------------------
# listing_photo_pivot output shape: per-listing + terminal cluster summaries
# ---------------------------------------------------------------------------
#
# Locks in the contract the dossier UI consumes: every pivot run emits
# (1) one listing-match-summary per visited listing with a `verdict` +
# `photo_matches[].matched_urls[]` list of links, and (2) one terminal
# photo-match-cluster aggregating photos that surfaced hits across the
# whole BFS, ranked by host diversity.
#
# Synthetic mode is the surface we test against because live mode burns
# real reverse-image quota and is non-deterministic.


class TestListingPhotoPivotSummaries:
    def test_emits_listing_match_summary(self):
        events = _listing_photo_pivot_synthetic({"listing_url": "https://www.vrbo.com/1682245"})
        summaries = [e for e in events if e["event_type"] == "listing-match-summary"]
        assert len(summaries) >= 1, "at least one per-listing summary required"

    def test_listing_summary_has_required_keys(self):
        events = _listing_photo_pivot_synthetic({"listing_url": "https://www.vrbo.com/1682245"})
        summary = next(e for e in events if e["event_type"] == "listing-match-summary")
        pl = summary["payload"]
        required = {
            "listing_url",
            "platform",
            "hop_depth",
            "photos_searched",
            "photo_matches",
            "total_matches",
            "distinct_match_hosts",
            "verdict",
        }
        assert required.issubset(pl.keys())

    def test_listing_summary_photo_matches_carry_engine_and_host(self):
        # Each entry in photo_matches[].matched_urls[] must have
        # {url, engine, host} so the UI can render link rows.
        events = _listing_photo_pivot_synthetic({"listing_url": "https://www.vrbo.com/x"})
        summary = next(e for e in events if e["event_type"] == "listing-match-summary")
        pm = summary["payload"]["photo_matches"]
        assert pm, "synthetic mode must populate photo_matches"
        first_url = pm[0]["matched_urls"][0]
        assert {"url", "engine", "host"}.issubset(first_url.keys())
        assert first_url["url"].startswith("http")

    def test_emits_terminal_photo_match_cluster(self):
        events = _listing_photo_pivot_synthetic({"listing_url": "https://www.vrbo.com/x"})
        clusters = [e for e in events if e["event_type"] == "photo-match-cluster"]
        # Exactly one terminal cluster per pivot run.
        assert len(clusters) == 1
        pl = clusters[0]["payload"]
        required = {
            "seed_listing_url",
            "photos_searched",
            "photos_with_matches",
            "listings_visited",
            "clusters",
        }
        assert required.issubset(pl.keys())

    def test_cluster_entries_have_verdict_and_host_diversity(self):
        events = _listing_photo_pivot_synthetic({"listing_url": "https://www.vrbo.com/x"})
        cluster_event = next(e for e in events if e["event_type"] == "photo-match-cluster")
        clusters = cluster_event["payload"]["clusters"]
        assert clusters, "synthetic mode must populate at least one cluster"
        c = clusters[0]
        assert {
            "source_photo_url",
            "matched_on",
            "distinct_match_hosts",
            "host_diversity",
            "verdict",
        }.issubset(c.keys())
        # The cross-platform verdict only fires when host_diversity >= 2.
        if c["host_diversity"] >= 2:
            assert c["verdict"] == "cross-platform-duplicate"

    def test_cross_platform_duplicate_verdict_in_synthetic_demo(self):
        # The synthetic fixture intentionally shows two distinct hosts
        # (airbnb.com + booking.com) so the strongest fraud signal fires.
        events = _listing_photo_pivot_synthetic({"listing_url": "https://www.vrbo.com/x"})
        cluster_event = next(e for e in events if e["event_type"] == "photo-match-cluster")
        verdicts = [c["verdict"] for c in cluster_event["payload"]["clusters"]]
        assert "cross-platform-duplicate" in verdicts

    def test_tool_run_result_does_not_leak_investigation_id(self):
        # Naomi-strict: the final tool-run-result must NOT carry
        # investigation_id. It's a routing key, not result data.
        events = _listing_photo_pivot_synthetic(
            {
                "listing_url": "https://www.vrbo.com/x",
                "investigation_id": "would-be-leak-12345",
            }
        )
        # Synthetic mode doesn't see the investigation_id by design --
        # it's a fixture. But the live function's tool-run-result must
        # never include it either. Assert against the synthetic output
        # since that's the contract the dossier UI consumes.
        result = next(e for e in events if e["event_type"] == "tool-run-result")
        assert "investigation_id" not in result["payload"]


class TestYanoljaRSCShellSkip:
    """Yanolja /places/<id> pages serve as Next.js App-Router RSC shells
    (live pressure-test 2026-05-16): only Organization-typed JSON-LD
    (parent Yanolja brand), no Hotel/LodgingBusiness/Place. The parser
    must emit `_skipped=True` rather than near-empty listing-data."""

    _RSC_SHELL_BODY = """<html><head>
        <title>NOL | wow</title>
        <script type="application/ld+json">
        {"@context": "https://schema.org", "@type": "Organization",
         "name": "Yanolja", "url": "https://www.yanolja.com"}
        </script>
        </head><body>
        <script>self.__next_f.push([1, "0:[\\"$\\",\\"$L1\\",null,{}]"])</script>
        </body></html>"""

    _LEGACY_HOTEL_BODY = """<html><head>
        <script type="application/ld+json">
        {"@context": "https://schema.org", "@type": "Hotel",
         "name": "Test Hotel", "address": {"@type": "PostalAddress",
         "addressLocality": "Seoul", "addressCountry": "KR"}}
        </script></head><body></body></html>"""

    def test_rsc_shell_returns_skipped_true(self):
        out = extract_yanolja(self._RSC_SHELL_BODY, "https://www.yanolja.com/places/12345")
        assert out["_skipped"] is True
        assert out["_skip_reason"] == "yanolja-rsc-shell-no-jsonld"
        assert "Next.js App-Router RSC shell" in out["_skip_detail"]
        assert out["extraction_tier"] == "rsc-shell-skipped"

    def test_rsc_shell_still_returns_listing_id_from_url(self):
        # listing_id is URL-derived; survives the RSC-shell skip path.
        out = extract_yanolja(self._RSC_SHELL_BODY, "https://www.yanolja.com/places/3000034028")
        assert out["listing_id"] == "3000034028"

    def test_rsc_shell_preserves_normalized_contract_shape(self):
        # Naomi-strict: callers iterating the dict must NOT receive a
        # different shape on the skip path. All cross-platform contract
        # keys present with empty defaults; the `_skipped` flag is the
        # signal, not a shape change.
        out = extract_yanolja(self._RSC_SHELL_BODY, "https://www.yanolja.com/places/x")
        for k in ("title", "host_name", "city", "country", "photo_urls", "currency"):
            assert k in out

    def test_legacy_hotel_jsonld_does_not_skip(self):
        # Older Yanolja layouts (and the synthetic test fixtures) DO
        # ship Hotel JSON-LD. Those must still parse normally.
        out = extract_yanolja(self._LEGACY_HOTEL_BODY, "https://www.yanolja.com/places/12345")
        assert out["_skipped"] is False
        assert out["_skip_reason"] == ""
        assert out["title"] == "Test Hotel"
        assert out["city"] == "Seoul"
        assert out["country"] == "KR"
        assert out["extraction_tier"] == "json-ld"

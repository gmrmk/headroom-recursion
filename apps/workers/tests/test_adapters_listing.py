"""Unit tests for adapters_listing -- travel-platform listing extractor (W20.tr).

No network: live-network probing lives in tools/dev/listing-live-probe.py and
is gated as a manual run, not a pytest target.
"""

from __future__ import annotations

from osint_goblin_workers.adapters_listing import (
    _PLATFORM_HOST_MAP,
    _airbnb_extract_listing_id,
    detect_platform,
    extract_airbnb,
    extract_generic_jsonld,
    extract_jsonld_blocks,
    listing_scrape,
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

"""Unit tests for adapters_dork — template builder + DDG HTML parser.

No network: live-network probing lives in tools/dev/dork-live-probe.py and
is gated as a manual run, not a pytest target.
"""

from __future__ import annotations

from osint_goblin_workers.adapters_dork import (
    _PV_TEMPLATES,
    _build_dork_queries,
    _parse_baidu_html,
    _parse_bing_html,
    _parse_ddg_html,
    _parse_google_html,
    _parse_naver_html,
    _parse_seznam_html,
    _parse_yahoojp_html,
    _parse_yandex_html,
    _rewrite_query_for_bing,
    _strip_bing_redirect,
    _strip_ddg_redirect,
    _url_matches_domain,
    dork_sweep_baidu,
    dork_sweep_bing,
    dork_sweep_brave,
    dork_sweep_ddg,
    dork_sweep_google,
    dork_sweep_naver,
    dork_sweep_serper,
    dork_sweep_seznam,
    dork_sweep_yahoojp,
    dork_sweep_yandex,
)

# ---------------------------------------------------------------------------
# _build_dork_queries — required-seed gating
# ---------------------------------------------------------------------------


class TestBuildDorkQueries:
    def test_empty_seed_returns_no_queries(self):
        assert _build_dork_queries({}) == []

    def test_seed_with_only_unrelated_key_returns_no_queries(self):
        # None of the templates require "ip"
        assert _build_dork_queries({"ip": "8.8.8.8"}) == []

    def test_whitespace_only_seed_treated_as_empty(self):
        # required-key check uses .strip(); whitespace must not satisfy it
        out = _build_dork_queries({"email": "   ", "name": "\t"})
        assert out == []

    def test_email_seed_produces_email_templates(self):
        out = _build_dork_queries({"email": "alice@example.com"})
        ids = {q["id"] for q in out}
        assert "email_paste_leaks" in ids
        assert "email_documents" in ids
        assert "email_broad" in ids
        # Templates requiring keys we didn't provide must be skipped
        assert "host_business_admin_panels" not in ids  # needs domain
        assert "name_linkedin" not in ids  # needs name

    def test_name_seed_produces_name_templates(self):
        out = _build_dork_queries({"name": "Alice Smith"})
        ids = {q["id"] for q in out}
        assert "name_linkedin" in ids
        assert "name_facebook_instagram" in ids
        assert "name_documents" in ids

    def test_query_substitutes_seed_values(self):
        out = _build_dork_queries({"email": "alice@example.com"})
        broad = next(q for q in out if q["id"] == "email_broad")
        assert "alice@example.com" in broad["query"]

    def test_cross_correlation_template_requires_both_keys(self):
        # name_plus_email requires BOTH name and email
        with_name_only = _build_dork_queries({"name": "Alice"})
        ids = {q["id"] for q in with_name_only}
        assert "name_plus_email" not in ids

        with_both = _build_dork_queries({"name": "Alice", "email": "a@b.com"})
        ids2 = {q["id"] for q in with_both}
        assert "name_plus_email" in ids2

    def test_no_template_id_collisions(self):
        seen = set()
        for tpl in _PV_TEMPLATES:
            assert tpl["id"] not in seen, f"duplicate id {tpl['id']!r}"
            seen.add(tpl["id"])

    def test_every_template_declares_required_keys(self):
        for tpl in _PV_TEMPLATES:
            assert "required" in tpl
            assert isinstance(tpl["required"], tuple)
            assert len(tpl["required"]) >= 1


# ---------------------------------------------------------------------------
# _strip_ddg_redirect — DDG sometimes wraps result URLs
# ---------------------------------------------------------------------------


class TestStripDdgRedirect:
    def test_passes_through_plain_url(self):
        assert _strip_ddg_redirect("https://example.com/page") == "https://example.com/page"

    def test_unwraps_uddg_redirect(self):
        wrapped = "//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fpage&rut=abc"
        assert _strip_ddg_redirect(wrapped) == "https://example.com/page"

    def test_promotes_protocol_relative_url(self):
        assert _strip_ddg_redirect("//example.com/page") == "https://example.com/page"


# ---------------------------------------------------------------------------
# _parse_ddg_html — extract result anchors
# ---------------------------------------------------------------------------


# DDG-shape fixture; long URL strings inside the HTML can't be reflowed without
# changing what the parser sees, so we silence E501 within the fixture.
# ruff: noqa: E501
_FIXTURE_HTML = """
<html><body>
<div class="results">
  <div class="result">
    <a rel="nofollow" class="result__a" href="https://www.linkedin.com/in/alice-smith">Alice Smith — LinkedIn</a>
    <a class="result__snippet" href="...">Some snippet</a>
  </div>
  <div class="result">
    <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fbio&rut=abc">Alice Smith — Example Bio</a>
  </div>
  <div class="result">
    <a class="result__a" href="https://github.com/alicesmith">alice <b>smith</b>'s GitHub</a>
  </div>
</div>
</body></html>
"""


class TestParseDdgHtml:
    def test_extracts_three_hits_from_fixture(self):
        hits = _parse_ddg_html(_FIXTURE_HTML)
        assert len(hits) == 3

    def test_first_hit_url_and_title(self):
        hits = _parse_ddg_html(_FIXTURE_HTML)
        assert hits[0]["url"] == "https://www.linkedin.com/in/alice-smith"
        assert hits[0]["title"] == "Alice Smith — LinkedIn"

    def test_unwraps_uddg_wrapped_result(self):
        hits = _parse_ddg_html(_FIXTURE_HTML)
        assert hits[1]["url"] == "https://example.com/bio"

    def test_strips_inline_html_from_title(self):
        hits = _parse_ddg_html(_FIXTURE_HTML)
        # The 3rd result has <b>smith</b> inline — should be stripped
        assert hits[2]["title"] == "alice smith's GitHub"

    def test_empty_input_returns_empty_list(self):
        assert _parse_ddg_html("") == []

    def test_non_http_urls_are_skipped(self):
        html = '<a class="result__a" href="javascript:void(0)">bad</a>'
        assert _parse_ddg_html(html) == []


# ---------------------------------------------------------------------------
# Adapter-level: skip path when no templates match
# ---------------------------------------------------------------------------


class TestAdapterSkipPath:
    def test_ddg_returns_skip_event_with_empty_seed(self):
        events = dork_sweep_ddg({})
        assert len(events) == 1
        ev = events[0]
        assert ev["event_type"] == "tool-run-result"
        assert ev["payload"]["skipped"] is True
        assert "no dork templates matched" in ev["payload"]["reason"]

    def test_brave_returns_skip_event_when_key_missing(self, monkeypatch):
        monkeypatch.delenv("OSINT_BRAVE_API_KEY", raising=False)
        events = dork_sweep_brave({"email": "a@b.com"})
        assert len(events) == 1
        assert events[0]["payload"]["skipped"] is True
        assert "OSINT_BRAVE_API_KEY" in events[0]["payload"]["reason"]

    def test_serper_returns_skip_event_when_key_missing(self, monkeypatch):
        monkeypatch.delenv("OSINT_SERPER_API_KEY", raising=False)
        events = dork_sweep_serper({"email": "a@b.com"})
        assert len(events) == 1
        assert events[0]["payload"]["skipped"] is True
        assert "OSINT_SERPER_API_KEY" in events[0]["payload"]["reason"]

    def test_bing_returns_skip_event_with_empty_seed(self):
        events = dork_sweep_bing({})
        assert len(events) == 1
        assert events[0]["event_type"] == "tool-run-result"
        assert events[0]["payload"]["skipped"] is True


# ---------------------------------------------------------------------------
# _strip_bing_redirect -- unwrap Bing's bing.com/ck/a tracking redirect
# ---------------------------------------------------------------------------


class TestStripBingRedirect:
    def test_passes_through_plain_url(self):
        assert (
            _strip_bing_redirect("https://www.linkedin.com/in/alice-smith")
            == "https://www.linkedin.com/in/alice-smith"
        )

    def test_unwraps_ck_redirect(self):
        # Bing's `u` param is base64url-encoded with an `a1` prefix.
        # Target: https://example.com/profile
        # base64url("https://example.com/profile") = aHR0cHM6Ly9leGFtcGxlLmNvbS9wcm9maWxl
        # Prefixed with "a1" -> "a1aHR0cHM6Ly9leGFtcGxlLmNvbS9wcm9maWxl"
        wrapped = (
            "https://www.bing.com/ck/a?!&&p=abc"
            "&u=a1aHR0cHM6Ly9leGFtcGxlLmNvbS9wcm9maWxl"
            "&ntb=1"
        )
        assert _strip_bing_redirect(wrapped) == "https://example.com/profile"

    def test_returns_input_on_parse_error(self):
        # Malformed `u` param -- adapter should pass through, not raise.
        wrapped = "https://www.bing.com/ck/a?u=a1invalid-base64-***"
        # Function returns either the unwrapped value OR the original URL
        # on parse error; both are acceptable (no exception, no None).
        result = _strip_bing_redirect(wrapped)
        assert isinstance(result, str)
        assert result == wrapped or result.startswith(("http://", "https://"))


# ---------------------------------------------------------------------------
# _parse_bing_html -- extract algorithmic result blocks
# ---------------------------------------------------------------------------


# Fixture mirrors Bing's 2026 b_algo result-block shape:
# <li class="b_algo" data-id="" iid="SERP.5618">
#   <style>...</style>             <!-- inline CSS to strip -->
#   <div class="b_imgcap_main">
#     <div class="b_tpcn">         <!-- top citation chrome -->
#       <a class="tilk" href="<ck/a-redirect>">...LinkedIn...</a>
#     </div>
#     <h2><a href="<ck/a-redirect>">TITLE</a></h2>
#     <p class="b_lineclamp2">SNIPPET</p>
#   </div>
# </li>
# Real-world: each result href is wrapped in bing.com/ck/a redirect.
_BING_LINKEDIN_HREF = "https://www.linkedin.com/in/alice-smith"
_BING_GITHUB_HREF = "https://github.com/alicesmith"
_BING_FIXTURE = (
    "<html><body>"
    '<ol id="b_results">'
    '<li class="b_algo" data-id="" iid="SERP.5001">'
    "<style>.b_algo { color: red; }</style>"  # inline style must be stripped
    '<div class="b_tpcn"><a class="tilk" href="x">LinkedIn</a></div>'
    "<h2>"
    '<a target="_blank" href="' + _BING_LINKEDIN_HREF + '" h="ID=abc">'
    "Alice Smith — LinkedIn</a>"
    "</h2>"
    '<p class="b_lineclamp2">'
    '<span class="news_dt">Oct 21, 2025</span>'
    " · Senior engineer. Boston, MA. Profile snippet for testing.</p>"
    "</li>"
    '<li class="b_algo" data-id="" iid="SERP.5002">'
    "<h2>"
    '<a target="_blank" href="' + _BING_GITHUB_HREF + '" h="ID=def">'
    "alice <b>smith</b>&#39;s GitHub</a>"
    "</h2>"
    '<p class="b_lineclamp2">Public repos by alicesmith on GitHub.</p>'
    "</li>"
    "</ol>"
    "</body></html>"
)


class TestParseBingHtml:
    def test_extracts_two_hits_from_fixture(self):
        hits = _parse_bing_html(_BING_FIXTURE)
        assert len(hits) == 2

    def test_first_hit_url_title_and_snippet(self):
        hits = _parse_bing_html(_BING_FIXTURE)
        assert hits[0]["url"] == _BING_LINKEDIN_HREF
        assert "Alice Smith" in hits[0]["title"]
        assert "LinkedIn" in hits[0]["title"]
        assert "Senior engineer" in hits[0]["snippet"]

    def test_strips_inline_html_from_title(self):
        hits = _parse_bing_html(_BING_FIXTURE)
        # Fixture has <b>smith</b> + &#39; entity in second title; both
        # should be cleaned to plain text "alice smith's GitHub".
        assert hits[1]["title"] == "alice smith's GitHub"

    def test_empty_input_returns_empty_list(self):
        assert _parse_bing_html("") == []
        assert _parse_bing_html("<html><body>no results</body></html>") == []

    def test_handles_missing_snippet_gracefully(self):
        # Bing sometimes returns a b_algo block with no caption paragraph
        # (image / video / news-card result types). The parser must not
        # crash and must still extract the title + URL.
        no_caption_fixture = (
            '<li class="b_algo" iid="SERP.1">'
            '<h2><a href="https://example.com/news">News Title</a></h2>'
            "</li>"
            '<li class="b_algo" iid="SERP.2">'
            '<h2><a href="https://example.com/other">Other</a></h2>'
            '<p class="b_lineclamp2">Has snippet.</p>'
            "</li>"
        )
        hits = _parse_bing_html(no_caption_fixture)
        assert len(hits) == 2
        assert hits[0]["snippet"] == ""
        assert "snippet" in hits[1]["snippet"].lower()

    def test_strips_inline_style_from_block(self):
        # b_algo blocks contain inline <style> tags whose CSS rules
        # reference "b_algo" -- if not stripped, downstream code would
        # mistakenly treat CSS as content.
        fixture_with_style = (
            '<li class="b_algo">'
            "<style>.foo .b_algo { display: none; }</style>"
            '<h2><a href="https://example.com">Title</a></h2>'
            '<p class="b_lineclamp2">Snippet body.</p>'
            "</li>"
        )
        hits = _parse_bing_html(fixture_with_style)
        assert len(hits) == 1
        assert hits[0]["url"] == "https://example.com"
        assert hits[0]["title"] == "Title"
        assert "Snippet body" in hits[0]["snippet"]

    def test_unwraps_ck_a_redirect_with_entities(self):
        # Real-world Bing wraps result URLs in bing.com/ck/a redirects
        # AND the rendered DOM keeps `&amp;` entities. Both must be
        # handled or the URL never unwraps.
        b64 = "aHR0cHM6Ly9leGFtcGxlLmNvbS9wcm9maWxl"  # base64url for https://example.com/profile
        wrapped = (
            f'<li class="b_algo"><h2>'
            f'<a href="https://www.bing.com/ck/a?!&amp;&amp;p=abc&amp;u=a1{b64}&amp;ntb=1">'
            f"Title</a></h2>"
            f'<p class="b_lineclamp2">Snippet</p>'
            f"</li>"
        )
        hits = _parse_bing_html(wrapped)
        assert len(hits) == 1
        assert hits[0]["url"] == "https://example.com/profile"

    def test_rejects_bing_internal_image_video_urls(self):
        # Image-search and video-search hrefs sometimes get captured
        # in a b_algo block; they should NOT count as hits.
        internal = (
            '<li class="b_algo"><h2>'
            '<a href="https://www.bing.com/images/search?view=detailV2">img</a>'
            "</h2></li>"
            '<li class="b_algo"><h2>'
            '<a href="https://www.bing.com/videos/search?q=foo">vid</a>'
            "</h2></li>"
            '<li class="b_algo"><h2>'
            '<a href="https://example.com/real">real</a>'
            "</h2></li>"
        )
        hits = _parse_bing_html(internal)
        assert len(hits) == 1
        assert hits[0]["url"] == "https://example.com/real"


# ---------------------------------------------------------------------------
# Bing query rewriter -- site:operator stub-avoidance pressure tests
# ---------------------------------------------------------------------------


class TestRewriteQueryForBing:
    def test_passes_through_query_without_site_operator(self):
        rew, doms = _rewrite_query_for_bing('"Alice Smith" filetype:pdf')
        assert rew == '"Alice Smith" filetype:pdf'
        assert doms == []

    def test_strips_single_site_operator_and_appends_domain(self):
        rew, doms = _rewrite_query_for_bing('site:linkedin.com "John Doe"')
        assert "site:" not in rew
        assert "linkedin.com" in rew
        assert '"John Doe"' in rew
        assert doms == ["linkedin.com"]

    def test_strips_multiple_site_operators_in_or_group(self):
        rew, doms = _rewrite_query_for_bing(
            '"alice@example.com" (site:pastebin.com OR site:ghostbin.com OR site:rentry.co)'
        )
        assert "site:" not in rew
        # Empty `( OR OR )` collapses; multiple-domain mention appended.
        assert "(" not in rew or ")" not in rew or "OR" not in rew.split("(")[1].split(")")[0]
        assert "pastebin.com" in rew
        assert "ghostbin.com" in rew
        assert "rentry.co" in rew
        assert sorted(doms) == ["ghostbin.com", "pastebin.com", "rentry.co"]

    def test_strips_negation_dash_with_site_operator(self):
        # "-site:nope.com" should not leave a stray "-" in the output.
        rew, doms = _rewrite_query_for_bing('"alice" -site:nope.com')
        assert "site:" not in rew
        # The "-" prefix should be consumed along with the operator.
        assert " - " not in rew
        assert doms == ["nope.com"]

    def test_lowercases_extracted_domains(self):
        _, doms = _rewrite_query_for_bing('site:LinkedIn.COM "X"')
        assert doms == ["linkedin.com"]

    def test_dedupes_extracted_domains_in_output(self):
        rew, _ = _rewrite_query_for_bing("site:linkedin.com site:linkedin.com site:linkedin.com X")
        # The domain mention should appear exactly once in the appended portion.
        assert rew.count("linkedin.com") == 1

    def test_preserves_other_operators(self):
        rew, _ = _rewrite_query_for_bing('site:example.com "X" filetype:pdf inurl:profile')
        assert "filetype:pdf" in rew
        assert "inurl:profile" in rew


# ---------------------------------------------------------------------------
# URL domain filter -- preserves site:-operator intent post-hoc
# ---------------------------------------------------------------------------


class TestUrlMatchesDomain:
    def test_empty_allowed_list_passes_everything(self):
        assert _url_matches_domain("https://example.com/page", []) is True

    def test_exact_host_match(self):
        assert _url_matches_domain("https://linkedin.com/in/x", ["linkedin.com"]) is True

    def test_subdomain_match(self):
        assert _url_matches_domain("https://uk.linkedin.com/in/x", ["linkedin.com"]) is True
        assert _url_matches_domain("https://www.linkedin.com/in/x", ["linkedin.com"]) is True

    def test_non_match_rejected(self):
        assert _url_matches_domain("https://github.com/x", ["linkedin.com"]) is False

    def test_invalid_url_returns_false(self):
        assert _url_matches_domain("not a url", ["linkedin.com"]) is False


# ---------------------------------------------------------------------------
# _parse_yandex_html -- extract OrganicTitle-Link + OrganicTextContentSpan
# ---------------------------------------------------------------------------


# Fixture mirrors Yandex's 2026 result structure: per-result generated-class
# <li>, with <a class="OrganicTitle-Link" href="<DIRECT-URL>"> wrapping a
# <span class="OrganicTitleContentSpan">TITLE</span>, then a separate
# <div class="Organic-ContentWrapper"> containing <div class="OrganicText">
# with <span class="OrganicTextContentSpan">SNIPPET</span>.
_YANDEX_FIXTURE = (
    "<html><body>"
    '<li class="ujeRJBw1">'
    '<div class="OrganicTitle">'
    '<a class="OrganicTitle-Link link" target="_blank" '
    'href="https://www.linkedin.com/in/alice-smith">'
    '<h2 class="OrganicTitle-LinkText">'
    '<span class="OrganicTitleContentSpan">'
    "<b>Alice</b> <b>Smith</b> - Senior Engineer | LinkedIn</span>"
    "</h2></a></div>"
    "<button class='Extralinks'></button>"
    '<div class="Organic-ContentWrapper">'
    '<div class="OrganicText Typo Typo_text_m">'
    '<span class="OrganicTextContentSpan">'
    "Experience: Acme Corp · Boston, MA · 500+ connections on <b>LinkedIn</b>."
    "</span></div></div></li>"
    '<li class="ujeRJBw2">'
    '<div class="OrganicTitle">'
    '<a class="OrganicTitle-Link link" '
    'href="https://github.com/alicesmith">'
    '<h2 class="OrganicTitle-LinkText">'
    '<span class="OrganicTitleContentSpan">'
    "alice <b>smith</b>&#39;s GitHub</span>"
    "</h2></a></div>"
    '<div class="Organic-ContentWrapper">'
    '<div class="OrganicText">'
    '<span class="OrganicTextContentSpan">'
    "Public repositories by alicesmith on GitHub."
    "</span></div></div></li>"
    "</body></html>"
)


class TestParseYandexHtml:
    def test_extracts_two_hits_from_fixture(self):
        hits = _parse_yandex_html(_YANDEX_FIXTURE)
        assert len(hits) == 2

    def test_first_hit_url_title_and_snippet(self):
        hits = _parse_yandex_html(_YANDEX_FIXTURE)
        assert hits[0]["url"] == "https://www.linkedin.com/in/alice-smith"
        assert "Alice Smith" in hits[0]["title"]
        assert "LinkedIn" in hits[0]["title"]
        assert "Experience: Acme Corp" in hits[0]["snippet"]
        assert "Boston, MA" in hits[0]["snippet"]

    def test_strips_inline_html_from_title(self):
        hits = _parse_yandex_html(_YANDEX_FIXTURE)
        # Fixture has <b>smith</b> + &#39; entity; both stripped/unescaped.
        assert hits[1]["title"] == "alice smith's GitHub"

    def test_strips_inline_html_from_snippet(self):
        hits = _parse_yandex_html(_YANDEX_FIXTURE)
        # First snippet has <b>LinkedIn</b>; stripped to "LinkedIn".
        assert "<b>" not in hits[0]["snippet"]
        assert "</b>" not in hits[0]["snippet"]

    def test_empty_input_returns_empty_list(self):
        assert _parse_yandex_html("") == []
        assert _parse_yandex_html("<html><body>no results</body></html>") == []

    def test_skips_relative_urls(self):
        relative_fixture = (
            '<a class="OrganicTitle-Link" href="/search/internal?q=foo">'
            '<span class="OrganicTitleContentSpan">Internal</span></a>'
            '<a class="OrganicTitle-Link" href="https://example.com/real">'
            '<span class="OrganicTitleContentSpan">Real</span></a>'
        )
        hits = _parse_yandex_html(relative_fixture)
        assert len(hits) == 1
        assert hits[0]["url"] == "https://example.com/real"

    def test_dedupes_repeated_urls(self):
        # Yandex sometimes renders the same URL in multiple result containers
        # (e.g., as a deeplink variant under the same canonical result).
        dup_fixture = (
            '<a class="OrganicTitle-Link" href="https://example.com/page">'
            '<span class="OrganicTitleContentSpan">First</span></a>'
            '<span class="OrganicTextContentSpan">First snippet.</span>'
            '<a class="OrganicTitle-Link" href="https://example.com/page">'
            '<span class="OrganicTitleContentSpan">Duplicate</span></a>'
            '<span class="OrganicTextContentSpan">Dup snippet.</span>'
        )
        hits = _parse_yandex_html(dup_fixture)
        assert len(hits) == 1

    def test_handles_missing_snippet_gracefully(self):
        no_snippet = (
            '<a class="OrganicTitle-Link" href="https://example.com/a">'
            '<span class="OrganicTitleContentSpan">A</span></a>'
            '<a class="OrganicTitle-Link" href="https://example.com/b">'
            '<span class="OrganicTitleContentSpan">B</span></a>'
            '<span class="OrganicTextContentSpan">Only B snippet.</span>'
        )
        hits = _parse_yandex_html(no_snippet)
        assert len(hits) == 2
        # B's snippet should attach to B, not bleed back to A.
        assert hits[0]["snippet"] == ""
        assert "B snippet" in hits[1]["snippet"]


class TestYandexAdapter:
    def test_yandex_returns_skip_event_with_empty_seed(self):
        events = dork_sweep_yandex({})
        assert len(events) == 1
        assert events[0]["event_type"] == "tool-run-result"
        assert events[0]["payload"]["skipped"] is True


# ---------------------------------------------------------------------------
# _parse_baidu_html -- extract result c-container + mu= URL + summary-text
# ---------------------------------------------------------------------------


# Fixture mirrors Baidu's 2026 result-container shape: the `mu=` attribute on
# the outer div is the destination URL; <h3 class="t"> contains the title;
# <span class="summary-text_XXXX"> contains the snippet (random suffix).
_BAIDU_FIXTURE = (
    "<html><body>"
    '<div class="result c-container xpath-log new-pmd" srcid="1599" id="1" '
    'mu="https://www.linkedin.com/in/alice-smith" '
    'data-op="{}" data-click="{}">'
    '<h3 class="t _sc-title">'
    '<a class="sc-link block" href="http://www.baidu.com/link?url=xxxxxx" '
    'target="_blank" data-module="title">'
    "<span><span>"
    "<em>Alice</em> <em>Smith</em> - LinkedIn Profile</span></span>"
    "</a></h3>"
    '<div data-module="struct-info">chrome</div>'
    '<div class="cos-row">'
    '<div data-module="abstract">'
    '<span class="summary-text_560AW">'
    "<em>Alice</em> <em>Smith</em>, Senior Engineer at Acme Corp. "
    "Based in Boston, MA. 500+ connections on LinkedIn."
    "</span>"
    "</div></div></div>"
    '<div class="result c-container new-pmd" srcid="1600" id="2" '
    'mu="https://github.com/alicesmith">'
    '<h3 class="t">'
    '<a href="x"><span>alice <b>smith</b>&#39;s GitHub</span></a></h3>'
    '<span class="summary-text_99ZZZ">Public repos by alicesmith.</span>'
    "</div>"
    "</body></html>"
)


class TestParseBaiduHtml:
    def test_extracts_two_hits_from_fixture(self):
        hits = _parse_baidu_html(_BAIDU_FIXTURE)
        assert len(hits) == 2

    def test_first_hit_url_title_and_snippet(self):
        hits = _parse_baidu_html(_BAIDU_FIXTURE)
        # URL comes from mu= attribute, NOT from baidu.com/link?url=
        assert hits[0]["url"] == "https://www.linkedin.com/in/alice-smith"
        assert "Alice Smith" in hits[0]["title"]
        assert "LinkedIn" in hits[0]["title"]
        assert "Senior Engineer at Acme Corp" in hits[0]["snippet"]

    def test_strips_inline_html_from_title(self):
        hits = _parse_baidu_html(_BAIDU_FIXTURE)
        assert hits[1]["title"] == "alice smith's GitHub"

    def test_strips_em_match_tags_from_snippet(self):
        # Baidu wraps query-matched terms in <em>; should be stripped.
        hits = _parse_baidu_html(_BAIDU_FIXTURE)
        assert "<em>" not in hits[0]["snippet"]

    def test_empty_input_returns_empty_list(self):
        assert _parse_baidu_html("") == []
        assert _parse_baidu_html("<html><body>no results</body></html>") == []

    def test_skips_baidu_internal_urls(self):
        # Some result containers point at baidu.com itself (search refinements,
        # related-queries blocks). The parser must drop those.
        internal = (
            '<div class="result c-container" mu="https://www.baidu.com/refine">'
            '<h3 class="t"><a href="x">internal</a></h3></div>'
            '<div class="result c-container" mu="https://example.com/real">'
            '<h3 class="t"><a href="x">real</a></h3></div>'
        )
        hits = _parse_baidu_html(internal)
        assert len(hits) == 1
        assert hits[0]["url"] == "https://example.com/real"

    def test_dedupes_repeated_urls(self):
        dup = (
            '<div class="result c-container" mu="https://example.com/page">'
            '<h3 class="t"><a href="x">First</a></h3></div>'
            '<div class="result c-container" mu="https://example.com/page">'
            '<h3 class="t"><a href="x">Dup</a></h3></div>'
        )
        hits = _parse_baidu_html(dup)
        assert len(hits) == 1


class TestBaiduAdapter:
    def test_baidu_returns_skip_event_with_empty_seed(self):
        events = dork_sweep_baidu({})
        assert len(events) == 1
        assert events[0]["event_type"] == "tool-run-result"
        assert events[0]["payload"]["skipped"] is True


# ---------------------------------------------------------------------------
# _parse_naver_html -- BS4 card-walk extraction
# ---------------------------------------------------------------------------


# Fixture mirrors Naver's 2026 component-tree structure with random class
# hashes: title in <span class="sds-comps-text-type-headline1">, snippet
# in <span class="sds-comps-text-ellipsis-3">, result link in
# <a nocr="1" href="<external-url>">. All wrapped in a card div.
_NAVER_FIXTURE = (
    "<html><body>"
    '<div class="card fender-ui_abc123">'
    '<a nocr="1" href="https://www.linkedin.com/in/alice-smith" '
    'class="fender-ui_def456" data-heatmap-target=".link">'
    '<div class="sds-comps-profile-thumb"></div></a>'
    '<div class="card-body fender-ui_ghi789">'
    '<span class="sds-comps-text sds-comps-text-type-headline1 '
    'sds-comps-text-weight-sm">Alice Smith - Senior Engineer | LinkedIn</span>'
    '<span class="sds-comps-text sds-comps-text-ellipsis '
    "sds-comps-text-ellipsis-3 sds-comps-text-type-body1 "
    'sds-comps-text-weight-sm">Experience at Acme Corp · Boston, MA · '
    "500+ connections on LinkedIn. View Alice's profile on LinkedIn.</span>"
    "</div></div>"
    '<div class="card fender-ui_xyz000">'
    '<a nocr="1" href="https://github.com/alicesmith">'
    "<div></div></a>"
    '<div class="card-body">'
    '<span class="sds-comps-text-type-headline1">alice <b>smith</b>&#39;s GitHub</span>'
    '<span class="sds-comps-text-ellipsis-3">Public repos by alicesmith on GitHub.</span>'
    "</div></div>"
    "</body></html>"
)


class TestParseNaverHtml:
    def test_extracts_two_hits_from_fixture(self):
        hits = _parse_naver_html(_NAVER_FIXTURE)
        assert len(hits) == 2

    def test_first_hit_url_title_and_snippet(self):
        hits = _parse_naver_html(_NAVER_FIXTURE)
        assert hits[0]["url"] == "https://www.linkedin.com/in/alice-smith"
        assert "Alice Smith" in hits[0]["title"]
        assert "Senior Engineer" in hits[0]["title"]
        assert "Experience at Acme Corp" in hits[0]["snippet"]
        assert "500+ connections" in hits[0]["snippet"]

    def test_strips_inline_html_from_title(self):
        hits = _parse_naver_html(_NAVER_FIXTURE)
        # Second hit has <b>smith</b> + &#39; entity; both stripped.
        assert hits[1]["title"] == "alice smith's GitHub"

    def test_empty_input_returns_empty_list(self):
        assert _parse_naver_html("") == []
        assert _parse_naver_html("<html><body>no results</body></html>") == []

    def test_skips_naver_internal_anchors(self):
        # Cards whose only nocr=1 anchor points at naver.com (login, dict,
        # shopping refinements) should not produce a hit -- no external URL.
        internal_only = (
            '<div class="card">'
            '<a nocr="1" href="https://nid.naver.com/nidlogin.login">x</a>'
            '<span class="sds-comps-text-type-headline1">naver login</span>'
            "</div>"
            '<div class="card">'
            '<a nocr="1" href="https://example.com/real">y</a>'
            '<span class="sds-comps-text-type-headline1">real result</span>'
            "</div>"
        )
        hits = _parse_naver_html(internal_only)
        assert len(hits) == 1
        assert hits[0]["url"] == "https://example.com/real"

    def test_dedupes_repeated_urls(self):
        dup = (
            '<div class="card">'
            '<a nocr="1" href="https://example.com/page">x</a>'
            '<span class="sds-comps-text-type-headline1">First</span>'
            "</div>"
            '<div class="card">'
            '<a nocr="1" href="https://example.com/page">y</a>'
            '<span class="sds-comps-text-type-headline1">Duplicate</span>'
            "</div>"
        )
        hits = _parse_naver_html(dup)
        assert len(hits) == 1

    def test_handles_korean_text_in_title_and_snippet(self):
        # Naver's primary content is Korean; the parser must round-trip
        # multi-byte characters through BS4 without corruption.
        kr = (
            '<div class="card">'
            '<a nocr="1" href="https://kr.linkedin.com/in/이재용">x</a>'
            '<span class="sds-comps-text-type-headline1">이재용 - LinkedIn</span>'
            '<span class="sds-comps-text-ellipsis-3">삼성전자 회장. 서울 거주.</span>'
            "</div>"
        )
        hits = _parse_naver_html(kr)
        assert len(hits) == 1
        assert "이재용" in hits[0]["title"]
        assert "삼성전자" in hits[0]["snippet"]
        assert "서울" in hits[0]["snippet"]


class TestNaverAdapter:
    def test_naver_returns_skip_event_with_empty_seed(self):
        events = dork_sweep_naver({})
        assert len(events) == 1
        assert events[0]["event_type"] == "tool-run-result"
        assert events[0]["payload"]["skipped"] is True


# ---------------------------------------------------------------------------
# _parse_yahoojp_html -- sw-Card Algo direct-URL extraction
# ---------------------------------------------------------------------------


_YAHOOJP_FIXTURE = (
    "<html><body>"
    '<div class="sw-Card Algo Algo-result"><section>'
    '<div class="sw-Card__section sw-Card__section--header">'
    '<div class="sw-Card__title">'
    '<a href="https://www.linkedin.com/in/alice-smith" '
    'class="sw-Card__titleInner" data-cl-params="x" ping="y">'
    '<h3 class="sw-Card__titleMain sw-Card__titleMain--clamp">'
    "<span>Alice Smith — <b>Senior</b> Engineer | LinkedIn</span>"
    "</h3></a></div></div>"
    '<div class="sw-Card__section">'
    '<p class="sw-Card__summary">'
    '<span class="util-Text--sub">2024/01/15</span>'
    '<span class="util-Delimiter">-</span>Senior engineer at Acme Corp. '
    "Boston, MA. 500+ connections."
    "</p></div></section></div>"
    '<div class="sw-Card Algo">'
    '<div class="sw-Card__title">'
    '<a href="https://github.com/alicesmith" class="sw-Card__titleInner">'
    '<h3 class="sw-Card__titleMain">'
    "<span>alice <b>smith</b>&#39;s GitHub</span></h3></a></div>"
    '<p class="sw-Card__summary">Public repos.</p>'
    "</div>"
    "</body></html>"
)


class TestParseYahooJpHtml:
    def test_extracts_two_hits_from_fixture(self):
        hits = _parse_yahoojp_html(_YAHOOJP_FIXTURE)
        assert len(hits) == 2

    def test_first_hit_url_title_and_snippet(self):
        hits = _parse_yahoojp_html(_YAHOOJP_FIXTURE)
        assert hits[0]["url"] == "https://www.linkedin.com/in/alice-smith"
        assert "Alice Smith" in hits[0]["title"]
        assert "Senior" in hits[0]["title"]
        assert "500+ connections" in hits[0]["snippet"]

    def test_strips_inline_html_from_title(self):
        hits = _parse_yahoojp_html(_YAHOOJP_FIXTURE)
        assert hits[1]["title"] == "alice smith's GitHub"

    def test_empty_input_returns_empty_list(self):
        assert _parse_yahoojp_html("") == []

    def test_skips_yahoo_internal_urls(self):
        internal = (
            '<div class="sw-Card Algo">'
            '<a href="https://search.yahoo.co.jp/refine" class="sw-Card__titleInner">'
            '<h3 class="sw-Card__titleMain"><span>refine</span></h3></a></div>'
            '<div class="sw-Card Algo">'
            '<a href="https://example.com/real" class="sw-Card__titleInner">'
            '<h3 class="sw-Card__titleMain"><span>real</span></h3></a></div>'
        )
        hits = _parse_yahoojp_html(internal)
        assert len(hits) == 1
        assert hits[0]["url"] == "https://example.com/real"


class TestYahooJpAdapter:
    def test_yahoojp_returns_skip_event_with_empty_seed(self):
        events = dork_sweep_yahoojp({})
        assert len(events) == 1
        assert events[0]["payload"]["skipped"] is True


# ---------------------------------------------------------------------------
# _parse_seznam_html -- BS4 Result__title-link extraction
# ---------------------------------------------------------------------------


_SEZNAM_FIXTURE = (
    "<html><body>"
    '<li class="Result">'
    '<a class="Result__title-link" href="https://www.linkedin.com/in/alice-smith">'
    "<h3>Alice Smith — LinkedIn</h3></a>"
    '<p class="Result__perex">Senior engineer at Acme Corp.</p>'
    "</li>"
    '<li class="Result">'
    '<a class="Result__title-link" href="https://github.com/alicesmith">'
    "<h3>alice <b>smith</b>&#39;s GitHub</h3></a>"
    '<p class="Result__perex">Public repos.</p>'
    "</li>"
    "</body></html>"
)


class TestParseSeznamHtml:
    def test_extracts_two_hits_from_fixture(self):
        hits = _parse_seznam_html(_SEZNAM_FIXTURE)
        assert len(hits) == 2

    def test_first_hit_url_title_and_snippet(self):
        hits = _parse_seznam_html(_SEZNAM_FIXTURE)
        assert hits[0]["url"] == "https://www.linkedin.com/in/alice-smith"
        assert "Alice Smith" in hits[0]["title"]
        assert "Senior engineer" in hits[0]["snippet"]

    def test_strips_inline_html_from_title(self):
        hits = _parse_seznam_html(_SEZNAM_FIXTURE)
        assert hits[1]["title"] == "alice smith's GitHub"

    def test_empty_input_returns_empty_list(self):
        assert _parse_seznam_html("") == []

    def test_skips_seznam_internal_urls(self):
        internal = (
            '<li class="Result">'
            '<a class="Result__title-link" href="https://seznam.cz/refine">x</a>'
            "</li>"
            '<li class="Result">'
            '<a class="Result__title-link" href="https://example.com/real">'
            "<h3>real</h3></a></li>"
        )
        hits = _parse_seznam_html(internal)
        assert len(hits) == 1
        assert hits[0]["url"] == "https://example.com/real"


class TestSeznamAdapter:
    def test_seznam_returns_skip_event_with_empty_seed(self):
        events = dork_sweep_seznam({})
        assert len(events) == 1
        assert events[0]["payload"]["skipped"] is True


# ---------------------------------------------------------------------------
# _parse_google_html -- BS4 MjjYud + h3 + VwiC3b extraction
# ---------------------------------------------------------------------------


_GOOGLE_FIXTURE = (
    "<html><body>"
    '<div class="MjjYud">'
    '<a href="https://www.linkedin.com/in/alice-smith">'
    "<h3>Alice Smith — Senior Engineer | LinkedIn</h3></a>"
    '<div class="VwiC3b">Senior engineer at Acme Corp. Boston, MA. '
    "500+ connections on LinkedIn.</div>"
    "</div>"
    '<div class="MjjYud">'
    '<a href="https://github.com/alicesmith">'
    "<h3>alice <b>smith</b>&#39;s GitHub</h3></a>"
    '<div class="VwiC3b">Public repos by alicesmith.</div>'
    "</div>"
    "</body></html>"
)


class TestParseGoogleHtml:
    def test_extracts_two_hits_from_fixture(self):
        hits = _parse_google_html(_GOOGLE_FIXTURE)
        assert len(hits) == 2

    def test_first_hit_url_title_and_snippet(self):
        hits = _parse_google_html(_GOOGLE_FIXTURE)
        assert hits[0]["url"] == "https://www.linkedin.com/in/alice-smith"
        assert "Alice Smith" in hits[0]["title"]
        assert "500+ connections" in hits[0]["snippet"]

    def test_strips_inline_html_from_title(self):
        hits = _parse_google_html(_GOOGLE_FIXTURE)
        assert hits[1]["title"] == "alice smith's GitHub"

    def test_skips_google_self_references(self):
        internal = (
            '<div class="MjjYud">'
            '<a href="https://www.google.com/imgres?...">'
            "<h3>image</h3></a></div>"
            '<div class="MjjYud">'
            '<a href="https://example.com/real">'
            "<h3>real</h3></a></div>"
        )
        hits = _parse_google_html(internal)
        assert len(hits) == 1
        assert hits[0]["url"] == "https://example.com/real"

    def test_empty_input_returns_empty_list(self):
        assert _parse_google_html("") == []


class TestGoogleStealthAdapter:
    def test_google_skips_when_env_gate_off(self, monkeypatch):
        monkeypatch.delenv("OSINT_GOOGLE_STEALTH", raising=False)
        events = dork_sweep_google({"name": "Alice"})
        assert len(events) == 1
        assert events[0]["payload"]["skipped"] is True
        assert "OSINT_GOOGLE_STEALTH=1" in events[0]["payload"]["reason"]

    def test_google_skips_with_empty_seed_when_env_gate_on(self, monkeypatch):
        monkeypatch.setenv("OSINT_GOOGLE_STEALTH", "1")
        events = dork_sweep_google({})
        assert len(events) == 1
        assert events[0]["payload"]["skipped"] is True
        assert "no dork templates matched" in events[0]["payload"]["reason"]

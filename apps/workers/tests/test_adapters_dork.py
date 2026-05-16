"""Unit tests for adapters_dork — template builder + DDG HTML parser.

No network: live-network probing lives in tools/dev/dork-live-probe.py and
is gated as a manual run, not a pytest target.
"""

from __future__ import annotations

from osint_goblin_workers.adapters_dork import (
    _PV_TEMPLATES,
    _build_dork_queries,
    _parse_ddg_html,
    _strip_ddg_redirect,
    dork_sweep_brave,
    dork_sweep_ddg,
    dork_sweep_serper,
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

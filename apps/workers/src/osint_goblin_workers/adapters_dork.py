"""Dork-sweep adapters (W13.dk workflow).

Phase 5 rollout, 2026-05-12. Derived from /osint skills:
  - offensive-osint §18 (80+ dork corpus -- we use a property-vetting subset)
  - offensive-osint §24.3 (multi-engine corpus run methodology)
  - osint-methodology §2.1 (TENTATIVE -> FIRM upgrade on operator visit)

Three engines, all behind a common interface:
  - dork_sweep_ddg       DuckDuckGo HTML scrape (default, keyless)
  - dork_sweep_brave     Brave Search API (env-gated OSINT_BRAVE_API_KEY)
  - dork_sweep_serper    Serper.dev (Google) API (env-gated OSINT_SERPER_API_KEY)

PV CORPUS
  ~15 templates from §18 tuned for property-vetting. Each template
  declares its required seed keys (name / email / phone / domain /
  username) -- templates with missing seeds are skipped.

NAOMI GATE (logless contract)
  The QUERY STRING contains target identifiers and DOES leak to the
  engine at request time. This is the unavoidable cost of dork-sweep.
  What we DON'T do:
    * Log the query to disk or stderr
    * Persist results outside the SSE event stream + in-tab dossier
    * Include the query verbatim in error payloads beyond a length cap
  Investigator runbook documents this tradeoff. Each adapter is
  rate-limited at the engine level (DDG soft, Brave 2K/mo, Serper 2.5K).

SEVERITY DISCIPLINE
  Every dork-hit emits with severity_basis="matrix:DORK_HIT_SNIPPET"
  (INFO tier in lib/severity-rubric.ts). Investigator visiting the
  link upgrades the asset's confidence per methodology §2.1.

LINT NOTE
  Dork templates are search-engine query strings with multi-site OR
  clauses (e.g. `site:A OR site:B OR site:C ...`); they intrinsically
  exceed the 100-char limit and can't reflow without breaking the
  parser. File-level E501 ignore below.
"""

from __future__ import annotations

import html
import os
import re
import time
import urllib.parse
from typing import Any

import httpx

from ._ua import default_ua
from .adapters import get_registry

# DDG HTML scrape requires a browser-like UA. Historically we pinned a
# Chrome string here while the rest of the worker shipped the literal
# osint-goblin UA. After W4-UA (Margaret wave-4 roadmap §3) the worker
# default is itself a Chrome-on-Win11 string, so this collapses to one
# source of truth. OSINT_TRANSPARENT_UA=1 still flips back to the
# osint-goblin literal across the package -- if a user opts in to that,
# DDG will likely block (acceptable: transparent opt-in is explicit).
_DEFAULT_UA = default_ua()

_HIT_LIMIT_PER_DORK = 8  # cap hits per query to keep the dossier lean


# ===========================================================================
# Property-vetting curated dork corpus (80 templates, weapon-grade)
# ===========================================================================
#
# Each template:
#   id              stable identifier (no collisions; enforced by test)
#   query           Python format string with named seed placeholders
#   required        tuple of seed keys that MUST be non-empty (enforced by test)
#   label           human-readable purpose (renders in dossier)
#
# Categories (counts):
#   - business-domain exposure         (4)
#   - paste-site leaks                 (6)
#   - social-media presence            (12)
#   - document mentions                (4)
#   - rental / lodging-platform (PV)   (5)
#   - property records (PV)            (4)
#   - public-records aggregators       (5)
#   - court / legal records            (5)
#   - aggressive identity (arrest etc) (3)
#   - image hosts (EXIF-bearing)       (3)
#   - code hosts                       (3)
#   - cloud storage exposure           (2)
#   - forum / community                (4)
#   - job / professional               (3)
#   - personal websites                (3)
#   - archive / Wayback                (2)
#   - cross-correlation                (7)
#   - local news / press               (2)
#   - generic broad                    (3)

_PV_TEMPLATES: tuple[dict[str, Any], ...] = (
    # ------------------------------------------------------------------
    # Business-domain exposure -- if host has a claimed business website,
    # what shouldn't be reachable?
    # ------------------------------------------------------------------
    {
        "id": "host_business_admin_panels",
        "query": "site:{domain} inurl:admin OR inurl:login OR inurl:dashboard",
        "required": ("domain",),
        "label": "Admin / login panels on host's business domain",
    },
    {
        "id": "host_business_config_files",
        "query": "site:{domain} ext:env OR ext:ini OR ext:conf",
        "required": ("domain",),
        "label": "Config file exposure on host's business domain",
    },
    {
        "id": "host_business_secret_files",
        "query": "site:{domain} ext:sql OR ext:log OR ext:bak OR ext:dump OR ext:sqlite",
        "required": ("domain",),
        "label": "Database dumps / log files / backup files on business domain",
    },
    {
        "id": "host_business_credential_files",
        "query": "site:{domain} ext:pem OR ext:key OR ext:pfx OR ext:p12",
        "required": ("domain",),
        "label": "Private keys / certificates exposed on business domain",
    },
    # ------------------------------------------------------------------
    # Paste-site leaks -- pastebin clones index high-value text dumps
    # ------------------------------------------------------------------
    {
        "id": "email_paste_leaks",
        "query": '"{email}" (site:pastebin.com OR site:ghostbin.com OR site:rentry.co OR site:gist.github.com)',
        "required": ("email",),
        "label": "Host email in paste-site leaks (core 4 hosts)",
    },
    {
        "id": "email_paste_leaks_broader",
        "query": '"{email}" (site:hastebin.com OR site:controlc.com OR site:justpaste.it OR site:0bin.net OR site:dpaste.com OR site:ix.io OR site:paste.ee)',
        "required": ("email",),
        "label": "Host email in paste-site leaks (broader 7 hosts)",
    },
    {
        "id": "name_paste_leaks",
        "query": '"{name}" (site:pastebin.com OR site:rentry.co OR site:gist.github.com)',
        "required": ("name",),
        "label": "Host name in paste-site leaks",
    },
    {
        "id": "phone_paste_leaks",
        "query": '"{phone}" (site:pastebin.com OR site:rentry.co OR site:ghostbin.com)',
        "required": ("phone",),
        "label": "Host phone in paste-site leaks",
    },
    {
        "id": "username_paste_leaks",
        "query": '"{username}" (site:pastebin.com OR site:rentry.co OR site:gist.github.com)',
        "required": ("username",),
        "label": "Username in paste-site leaks",
    },
    {
        "id": "email_credential_combos",
        "query": '"{email}" (password OR passwd OR pwd OR "email:password" OR combolist)',
        "required": ("email",),
        "label": "Email in credential / combolist context (breach hint)",
    },
    # ------------------------------------------------------------------
    # Social media presence -- 12 platforms; each catches a specific
    # subculture / age cohort. Username-keyed templates require an exact
    # handle match (high precision); name-keyed are higher-recall.
    # ------------------------------------------------------------------
    {
        "id": "name_linkedin",
        "query": 'site:linkedin.com/in "{name}"',
        "required": ("name",),
        "label": "Host name on LinkedIn profiles",
    },
    {
        "id": "name_facebook_instagram",
        "query": '"{name}" (site:facebook.com OR site:instagram.com)',
        "required": ("name",),
        "label": "Host name on Facebook / Instagram",
    },
    {
        "id": "username_twitter_reddit",
        "query": '"{username}" (site:twitter.com OR site:x.com OR site:reddit.com)',
        "required": ("username",),
        "label": "Username on Twitter/X / Reddit",
    },
    {
        "id": "username_tiktok_threads",
        "query": '"{username}" (site:tiktok.com OR site:threads.net)',
        "required": ("username",),
        "label": "Username on TikTok / Threads",
    },
    {
        "id": "username_bluesky_mastodon",
        "query": '"{username}" (site:bsky.app OR site:mastodon.social OR site:mastodon.online)',
        "required": ("username",),
        "label": "Username on Bluesky / Mastodon (fediverse)",
    },
    {
        "id": "username_pinterest_tumblr",
        "query": '"{username}" (site:pinterest.com OR site:tumblr.com)',
        "required": ("username",),
        "label": "Username on Pinterest / Tumblr",
    },
    {
        "id": "username_youtube_channel",
        "query": '"{username}" (site:youtube.com/@{username} OR site:youtube.com/c/ OR site:youtube.com/user/)',
        "required": ("username",),
        "label": "Username as YouTube channel handle",
    },
    {
        "id": "name_substack_medium",
        "query": '"{name}" (site:substack.com OR site:medium.com)',
        "required": ("name",),
        "label": "Host name on Substack / Medium (long-form publishing)",
    },
    {
        "id": "username_reddit_user_path",
        "query": "site:reddit.com inurl:/user/{username}",
        "required": ("username",),
        "label": "Reddit user profile by exact path match",
    },
    {
        "id": "username_xenforo_vbulletin",
        "query": '"{username}" (inurl:members inurl:php OR inurl:member.php)',
        "required": ("username",),
        "label": "Username on XenForo / vBulletin forums",
    },
    {
        "id": "username_discourse_phpbb",
        "query": '"{username}" (inurl:/u/ OR inurl:viewtopic OR inurl:memberlist)',
        "required": ("username",),
        "label": "Username on Discourse / phpBB / similar forums",
    },
    {
        "id": "name_facebook_groups",
        "query": '"{name}" site:facebook.com inurl:groups',
        "required": ("name",),
        "label": "Host name in Facebook groups (local community presence)",
    },
    # ------------------------------------------------------------------
    # Document mentions -- PDFs, slides, spreadsheets index real names
    # ------------------------------------------------------------------
    {
        "id": "name_documents",
        "query": '"{name}" (filetype:pdf OR filetype:docx OR filetype:xlsx)',
        "required": ("name",),
        "label": "Host name in indexed documents",
    },
    {
        "id": "email_documents",
        "query": '"{email}" (filetype:pdf OR filetype:docx OR filetype:xlsx)',
        "required": ("email",),
        "label": "Host email in indexed documents",
    },
    {
        "id": "name_scribd_slideshare",
        "query": '"{name}" (site:scribd.com OR site:slideshare.net)',
        "required": ("name",),
        "label": "Host name on Scribd / SlideShare (public doc-share)",
    },
    {
        "id": "name_academia_researchgate",
        "query": '"{name}" (site:academia.edu OR site:researchgate.net)',
        "required": ("name",),
        "label": "Host name on Academia / ResearchGate (academic record)",
    },
    # ------------------------------------------------------------------
    # Rental / lodging-platform (PV-specific) -- the meat of property
    # vetting: does this host operate commercially / at scale?
    # ------------------------------------------------------------------
    {
        "id": "name_rental_keywords",
        "query": '"{name}" ("vacation rental" OR "short-term rental" OR airbnb OR vrbo)',
        "required": ("name",),
        "label": "Host name + rental keywords (commercial-operator signal)",
    },
    {
        "id": "address_listing_sites",
        "query": '"{address}" (airbnb.com OR vrbo.com OR booking.com)',
        "required": ("address",),
        "label": "Property address on listing platforms",
    },
    {
        "id": "address_listing_sites_broader",
        "query": '"{address}" (tripadvisor.com OR yelp.com OR furnishedfinder.com OR plumguide.com)',
        "required": ("address",),
        "label": "Property address on TripAdvisor / Yelp / FurnishedFinder",
    },
    {
        "id": "name_vacation_property_managers",
        "query": '"{name}" (vacasa.com OR sonder.com OR evolve.com OR airconcierge.net OR turnkey.com)',
        "required": ("name",),
        "label": "Host name in commercial vacation-rental management portfolios",
    },
    {
        "id": "email_rental_platforms",
        "query": '"{email}" (airbnb.com OR vrbo.com OR booking.com OR furnishedfinder.com)',
        "required": ("email",),
        "label": "Host email on rental platforms",
    },
    # ------------------------------------------------------------------
    # Property records (PV-specific) -- ownership history, valuation
    # ------------------------------------------------------------------
    {
        "id": "address_zillow_redfin",
        "query": '"{address}" (site:zillow.com OR site:redfin.com OR site:realtor.com OR site:trulia.com)',
        "required": ("address",),
        "label": "Property on major real-estate aggregators (history + valuation)",
    },
    {
        "id": "address_county_records",
        "query": '"{address}" (inurl:assessor OR inurl:appraisal OR inurl:property OR "tax records" OR "parcel")',
        "required": ("address",),
        "label": "Property on county assessor / tax records (ownership chain)",
    },
    {
        "id": "address_loopnet_commercial",
        "query": '"{address}" (site:loopnet.com OR site:crexi.com OR "commercial real estate" OR "multifamily")',
        "required": ("address",),
        "label": "Property on commercial / multifamily listings (rental-business signal)",
    },
    {
        "id": "name_property_owner_records",
        "query": '"{name}" ("property owner" OR "deed" OR "title" OR "homeowner" OR "landlord")',
        "required": ("name",),
        "label": "Host name in property-ownership / deed / landlord context",
    },
    # ------------------------------------------------------------------
    # Public-records aggregators -- US-centric people-search engines
    # ------------------------------------------------------------------
    {
        "id": "name_whitepages_spokeo",
        "query": '"{name}" (site:whitepages.com OR site:spokeo.com OR site:411.com)',
        "required": ("name",),
        "label": "Host name on Whitepages / Spokeo / 411 (people-search)",
    },
    {
        "id": "name_truepeoplesearch_fastpeoplesearch",
        "query": '"{name}" (site:truepeoplesearch.com OR site:fastpeoplesearch.com OR site:peoplefinder.com)',
        "required": ("name",),
        "label": "Host name on TruePeopleSearch / FastPeopleSearch / PeopleFinder",
    },
    {
        "id": "name_beenverified_radaris",
        "query": '"{name}" (site:beenverified.com OR site:radaris.com OR site:peekyou.com)',
        "required": ("name",),
        "label": "Host name on BeenVerified / Radaris / PeekYou",
    },
    {
        "id": "phone_reverse_lookup",
        "query": '"{phone}" (site:thatsthem.com OR site:anywho.com OR site:411.com OR site:whitepages.com)',
        "required": ("phone",),
        "label": "Reverse phone lookup across aggregators",
    },
    {
        "id": "email_reverse_lookup",
        "query": '"{email}" (site:thatsthem.com OR site:spokeo.com OR site:beenverified.com)',
        "required": ("email",),
        "label": "Reverse email lookup across aggregators",
    },
    # ------------------------------------------------------------------
    # Court / legal records -- civil, criminal, business filings
    # ------------------------------------------------------------------
    {
        "id": "name_courtlistener_pacer",
        "query": '"{name}" (site:courtlistener.com OR site:pacer.gov OR site:law.justia.com)',
        "required": ("name",),
        "label": "Host name in federal court records (PACER / CourtListener / Justia)",
    },
    {
        "id": "name_state_court_records",
        "query": '"{name}" ("court records" OR "case docket" OR "plaintiff" OR "defendant" OR "civil case")',
        "required": ("name",),
        "label": "Host name in state-court / general case-docket context",
    },
    {
        "id": "name_opencorporates",
        "query": '"{name}" (site:opencorporates.com OR site:bizapedia.com OR site:corporationwiki.com)',
        "required": ("name",),
        "label": "Host name as officer / agent in business filings",
    },
    {
        "id": "name_sec_edgar",
        "query": '"{name}" (site:sec.gov OR site:edgar-online.com)',
        "required": ("name",),
        "label": "Host name in SEC filings (executive / large-shareholder signal)",
    },
    {
        "id": "name_lawsuit_lien_records",
        "query": '"{name}" ("lien" OR "lawsuit" OR "judgment" OR "bankruptcy" OR "foreclosure")',
        "required": ("name",),
        "label": "Host name in lien / judgment / bankruptcy / foreclosure context",
    },
    # ------------------------------------------------------------------
    # Aggressive identity -- public arrest / sex-offender / mugshot
    # ------------------------------------------------------------------
    {
        "id": "name_arrest_records",
        "query": '"{name}" (site:mugshots.com OR site:arrests.org OR site:mugshotsonline.com OR "booking photo")',
        "required": ("name",),
        "label": "Host name in arrest / mugshot / booking-photo records",
    },
    {
        "id": "name_sex_offender_registry",
        "query": '"{name}" (site:nsopw.gov OR "sex offender" OR "Megan\'s Law")',
        "required": ("name",),
        "label": "Host name in sex-offender registry (NSOPW + state registries)",
    },
    {
        "id": "name_criminal_record_news",
        "query": '"{name}" (arrested OR charged OR sentenced OR convicted OR indicted) (-fiction -novel)',
        "required": ("name",),
        "label": "Host name in criminal-news mentions (filters fiction)",
    },
    # ------------------------------------------------------------------
    # Image hosts (EXIF-bearing) -- photos with embedded metadata
    # ------------------------------------------------------------------
    {
        "id": "name_image_hosts",
        "query": '"{name}" (site:flickr.com OR site:smugmug.com OR site:500px.com OR site:imgur.com)',
        "required": ("name",),
        "label": "Host name on image hosts (Flickr / SmugMug / 500px / imgur)",
    },
    {
        "id": "username_image_hosts",
        "query": '"{username}" (site:flickr.com OR site:smugmug.com OR site:500px.com)',
        "required": ("username",),
        "label": "Username on image hosts",
    },
    {
        "id": "address_image_geotag",
        "query": '"{address}" (site:flickr.com OR site:panoramio.com OR site:instagram.com inurl:location)',
        "required": ("address",),
        "label": "Address as geotagged-photo location",
    },
    # ------------------------------------------------------------------
    # Code hosts -- developer-identity correlation
    # ------------------------------------------------------------------
    {
        "id": "email_code_hosts",
        "query": '"{email}" (site:github.com OR site:gitlab.com OR site:bitbucket.org)',
        "required": ("email",),
        "label": "Host email in code-host repos / commits",
    },
    {
        "id": "username_code_hosts",
        "query": '"{username}" (site:github.com OR site:gitlab.com OR site:bitbucket.org OR site:codepen.io OR site:replit.com)',
        "required": ("username",),
        "label": "Username as code-host handle",
    },
    {
        "id": "name_stackoverflow",
        "query": '"{name}" (site:stackoverflow.com OR site:stackexchange.com)',
        "required": ("name",),
        "label": "Host name on Stack Overflow / Stack Exchange",
    },
    # ------------------------------------------------------------------
    # Cloud storage exposure -- public share links via S3 / GCS / Drive
    # ------------------------------------------------------------------
    {
        "id": "name_cloud_storage_shares",
        "query": '"{name}" (site:s3.amazonaws.com OR site:storage.googleapis.com OR site:blob.core.windows.net OR site:drive.google.com OR site:1drv.ms)',
        "required": ("name",),
        "label": "Host name in public cloud-storage shares",
    },
    {
        "id": "email_cloud_storage_shares",
        "query": '"{email}" (site:s3.amazonaws.com OR site:storage.googleapis.com OR site:drive.google.com)',
        "required": ("email",),
        "label": "Host email in public cloud-storage shares",
    },
    # ------------------------------------------------------------------
    # Job / professional -- careers + reputational record
    # ------------------------------------------------------------------
    {
        "id": "name_indeed_glassdoor",
        "query": '"{name}" (site:indeed.com OR site:glassdoor.com OR site:angellist.com OR site:wellfound.com)',
        "required": ("name",),
        "label": "Host name on job / company-review platforms",
    },
    {
        "id": "name_creative_portfolios",
        "query": '"{name}" (site:dribbble.com OR site:behance.net OR site:artstation.com)',
        "required": ("name",),
        "label": "Host name on creative-portfolio platforms (designer / artist)",
    },
    {
        "id": "name_consulting_freelance",
        "query": '"{name}" (site:upwork.com OR site:fiverr.com OR site:freelancer.com OR site:toptal.com)',
        "required": ("name",),
        "label": "Host name on freelance / consulting platforms",
    },
    # ------------------------------------------------------------------
    # Personal websites -- self-hosted identity surface
    # ------------------------------------------------------------------
    {
        "id": "name_personal_sites",
        "query": '"{name}" (site:about.me OR site:carrd.co OR site:linktr.ee OR site:wordpress.com OR site:blogspot.com)',
        "required": ("name",),
        "label": "Host name on personal-bio / link-in-bio sites",
    },
    {
        "id": "username_personal_sites",
        "query": '"{username}" (site:about.me OR site:carrd.co OR site:linktr.ee)',
        "required": ("username",),
        "label": "Username on personal-bio sites",
    },
    {
        "id": "name_personal_blog",
        "query": '"{name}" ("about me" OR "personal blog" OR "homepage")',
        "required": ("name",),
        "label": "Host name in personal-homepage / blog context",
    },
    # ------------------------------------------------------------------
    # Archive coverage -- defunct or recently-removed profiles
    # ------------------------------------------------------------------
    {
        "id": "name_wayback_archive",
        "query": '"{name}" site:web.archive.org',
        "required": ("name",),
        "label": "Host name in Wayback Machine archives (deleted profiles)",
    },
    {
        "id": "name_archive_today",
        "query": '"{name}" (site:archive.today OR site:archive.ph OR site:archive.is)',
        "required": ("name",),
        "label": "Host name on archive.today (often catches what Wayback missed)",
    },
    # ------------------------------------------------------------------
    # Forum / community broader
    # ------------------------------------------------------------------
    {
        "id": "name_quora",
        "query": 'site:quora.com "{name}"',
        "required": ("name",),
        "label": "Host name on Quora",
    },
    # ------------------------------------------------------------------
    # Cross-correlation -- two-identifier intersections kill false-positives
    # ------------------------------------------------------------------
    {
        "id": "name_plus_email",
        "query": '"{name}" "{email}"',
        "required": ("name", "email"),
        "label": "Name + email correlation across the open web",
    },
    {
        "id": "name_plus_phone",
        "query": '"{name}" "{phone}"',
        "required": ("name", "phone"),
        "label": "Name + phone correlation (high confidence when present)",
    },
    {
        "id": "name_plus_address",
        "query": '"{name}" "{address}"',
        "required": ("name", "address"),
        "label": "Name + address correlation (occupancy confirmation)",
    },
    {
        "id": "email_plus_phone",
        "query": '"{email}" "{phone}"',
        "required": ("email", "phone"),
        "label": "Email + phone correlation",
    },
    {
        "id": "name_plus_username",
        "query": '"{name}" "{username}"',
        "required": ("name", "username"),
        "label": "Name + username correlation (handle ownership confirmation)",
    },
    {
        "id": "email_plus_username",
        "query": '"{email}" "{username}"',
        "required": ("email", "username"),
        "label": "Email + username correlation",
    },
    {
        "id": "email_offsite_mentions",
        "query": '"{email}" -site:{domain}',
        "required": ("email", "domain"),
        "label": "Host email mentioned outside their own domain",
    },
    # ------------------------------------------------------------------
    # Local news / press -- local-paper indexing catches civic mentions
    # ------------------------------------------------------------------
    {
        "id": "name_local_news",
        "query": '"{name}" (intitle:news OR intitle:obituary OR intitle:police OR intitle:fire OR intitle:council)',
        "required": ("name",),
        "label": "Host name in local-news / civic context",
    },
    {
        "id": "name_obituary_legacy",
        "query": '"{name}" (site:legacy.com OR site:obituaries.com OR site:tributes.com OR "in loving memory")',
        "required": ("name",),
        "label": "Host name in obituary / memorial records (relative pivot)",
    },
    # ------------------------------------------------------------------
    # Generic broad sweep -- floor coverage; cheap; often informative
    # ------------------------------------------------------------------
    {
        "id": "email_broad",
        "query": '"{email}"',
        "required": ("email",),
        "label": "Broad email mentions (any site)",
    },
    {
        "id": "phone_broad",
        "query": '"{phone}"',
        "required": ("phone",),
        "label": "Broad phone mentions (any site)",
    },
    {
        "id": "username_broad",
        "query": '"{username}"',
        "required": ("username",),
        "label": "Broad username mentions (any site)",
    },
)


def _build_dork_queries(seed: dict[str, Any]) -> list[dict[str, Any]]:
    """Given a seed dict, return the list of dork queries the adapter
    should run -- only templates whose required seed keys are non-empty."""
    out: list[dict[str, Any]] = []
    for tpl in _PV_TEMPLATES:
        required: tuple[str, ...] = tpl["required"]
        if not all(str(seed.get(k) or "").strip() for k in required):
            continue
        try:
            query = tpl["query"].format(
                **{
                    k: str(seed.get(k) or "").strip()
                    for k in (*required, "domain", "name", "email", "phone", "address", "username")
                    if seed.get(k)
                }
            )
        except (KeyError, IndexError, ValueError):
            continue
        if not query.strip():
            continue
        out.append(
            {
                "id": tpl["id"],
                "label": tpl["label"],
                "query": query,
            }
        )
    return out


# ===========================================================================
# DDG HTML-endpoint scrape (default; keyless)
# ===========================================================================
#
# Uses html.duckduckgo.com/html/ which returns minimal HTML easier to
# parse than the JS-heavy main UI. Result format (stable since 2020):
#   <a class="result__a" rel="nofollow" href="<url>">title</a>
#   <a class="result__snippet" href="...">snippet</a>
# We extract href + title via regex. Fragile but workable; if DDG ships
# breaking changes the adapter degrades to result-shaped skip.

_DDG_URL = "https://html.duckduckgo.com/html/"

# Capture each result block: title anchor + everything until the next
# title anchor (or end of body). The "intervening" group is where the
# snippet lives -- DDG renders it as <a class="result__snippet"> or
# <span class="result__snippet"> depending on the result type.
_DDG_RESULT_BLOCK_RE = re.compile(
    r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="([^"]+)"[^>]*>'
    r"(.*?)"  # title content (HTML, stripped below)
    r"</a>"
    r"(.*?)"  # intervening HTML (snippet lives here, when present)
    r"(?="
    r'<a[^>]+class="[^"]*result__a[^"]*"|\Z'  # next result OR end-of-body
    r")",
    re.IGNORECASE | re.DOTALL,
)

# Snippet selector within a result block. Matches either anchor or span/div
# variants and any other tag DDG might wrap it in.
_DDG_SNIPPET_RE = re.compile(
    r'<[a-z]+[^>]+class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</[a-z]+>',
    re.IGNORECASE | re.DOTALL,
)

# Inline HTML stripper used by both title and snippet extraction.
_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _strip_ddg_redirect(url: str) -> str:
    """DDG sometimes wraps result URLs in a redirect like
    //duckduckgo.com/l/?uddg=<encoded-url>. Extract the real one."""
    if "/l/?uddg=" in url:
        try:
            parsed = urllib.parse.urlparse(url)
            qs = urllib.parse.parse_qs(parsed.query)
            inner = qs.get("uddg", [""])[0]
            if inner:
                return urllib.parse.unquote(inner)
        except Exception:
            pass
    if url.startswith("//"):
        return "https:" + url
    return url


def _clean_html_fragment(fragment: str, max_len: int = 300) -> str:
    """Strip tags + unescape entities + collapse whitespace + truncate.

    Used for both result titles and result snippets. The truncate cap
    keeps dossier payloads lean; full body text is one click away.
    """
    # Empty-string tag sub preserves "<b>smith</b>'s" → "smith's" semantics
    # the existing fixture-based tests rely on.
    text = _HTML_TAG_RE.sub("", fragment)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_len:
        text = text[: max_len - 1].rstrip() + "…"
    return text


def _parse_ddg_html(html_body: str) -> list[dict[str, str]]:
    """Parse DDG's HTML endpoint into hits with url + title + snippet.

    Snippet is best-effort; missing snippet returns empty string, not a
    KeyError. Naomi gate: only result text leaves this function -- the
    page bytes never touch disk past the SSE bus.
    """
    hits: list[dict[str, str]] = []
    for m in _DDG_RESULT_BLOCK_RE.finditer(html_body):
        raw_url = m.group(1).strip()
        title_html = m.group(2)
        intervening = m.group(3) or ""

        url = _strip_ddg_redirect(raw_url)
        if not url or not url.startswith(("http://", "https://")):
            continue

        title_text = _clean_html_fragment(title_html, max_len=200)
        snippet_text = ""
        sn = _DDG_SNIPPET_RE.search(intervening)
        if sn:
            snippet_text = _clean_html_fragment(sn.group(1), max_len=300)

        hits.append({"url": url, "title": title_text, "snippet": snippet_text})
    return hits


def dork_sweep_ddg(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Dork-sweep via DuckDuckGo HTML endpoint.

    Payload (any subset of seed keys; templates with missing keys skip):
      {"name", "email", "phone", "domain", "username", "address"}
      {"limit_per_query": 8}  # optional override

    Each matched template -> 1 HTTP request -> up to N hits -> dork-hit
    events. Naomi gate: queries never logged; only result URLs + titles
    surface back through the event stream.
    """
    seed = payload or {}
    limit_per = min(
        int(seed.get("limit_per_query", _HIT_LIMIT_PER_DORK) or _HIT_LIMIT_PER_DORK), 25
    )
    queries = _build_dork_queries(seed)
    if not queries:
        return [
            {
                "event_type": "tool-run-result",
                "payload": {
                    "adapter_id": "dork_sweep_ddg",
                    "skipped": True,
                    "reason": "no dork templates matched available seed keys",
                    "templates_total": len(_PV_TEMPLATES),
                },
            }
        ]

    events: list[dict[str, Any]] = []
    queries_run = 0
    queries_failed = 0
    total_hits = 0
    with httpx.Client(
        timeout=15.0,
        headers={"User-Agent": _DEFAULT_UA, "Accept": "text/html"},
        follow_redirects=True,
    ) as c:
        for idx, q in enumerate(queries):
            # Polite-citizen sleep between DDG queries to avoid the
            # rate-limit kick. DDG HTML endpoint blocks aggressively
            # under burst traffic from a single IP.
            if idx > 0:
                time.sleep(2.0)
            try:
                r = c.post(_DDG_URL, data={"q": q["query"]})
            except httpx.RequestError:
                queries_failed += 1
                continue
            if r.status_code != 200:
                queries_failed += 1
                continue
            hits = _parse_ddg_html(r.text)[:limit_per]
            queries_run += 1
            for h in hits:
                total_hits += 1
                events.append(
                    {
                        "event_type": "dork-hit",
                        "payload": {
                            "source": "dork-sweep",
                            "engine": "ddg",
                            "template_id": q["id"],
                            "template_label": q["label"],
                            "url": h["url"],
                            "title": h["title"],
                            "snippet": h.get("snippet", ""),
                            "confidence": "tentative",
                        },
                    }
                )

    events.append(
        {
            "event_type": "tool-run-result",
            "payload": {
                "adapter_id": "dork_sweep_ddg",
                "queries_attempted": len(queries),
                "queries_run": queries_run,
                "queries_failed": queries_failed,
                "total_hits": total_hits,
            },
        }
    )
    return events


def _dork_sweep_ddg_synthetic(payload: dict[str, Any]) -> list[dict[str, Any]]:
    name = payload.get("name") or "Test Host"
    return [
        {
            "event_type": "dork-hit",
            "payload": {
                "source": "dork-sweep",
                "engine": "ddg",
                "template_id": "name_linkedin",
                "template_label": "Host name on LinkedIn profiles",
                "url": "https://www.linkedin.com/in/test-host",
                "title": f"{name} — LinkedIn (synthetic)",
                "confidence": "tentative",
                "synthetic": True,
            },
        },
        {
            "event_type": "tool-run-result",
            "payload": {
                "adapter_id": "dork_sweep_ddg",
                "queries_run": 1,
                "total_hits": 1,
                "synthetic": True,
            },
        },
    ]


# ===========================================================================
# Brave Search API (env-gated; free 2K/month)
# ===========================================================================
#
# Endpoint: https://api.search.brave.com/res/v1/web/search?q=<query>
# Auth: X-Subscription-Token: <key>
# Returns structured JSON. Independent index -- catches things Google
# buries and DDG (Bing-flavored) misses.

_BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"


def dork_sweep_brave(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Dork-sweep via Brave Search API."""
    api_key = os.environ.get("OSINT_BRAVE_API_KEY", "").strip()
    if not api_key:
        return [
            {
                "event_type": "tool-run-result",
                "payload": {
                    "adapter_id": "dork_sweep_brave",
                    "skipped": True,
                    "reason": "OSINT_BRAVE_API_KEY env var not set",
                    "suggest": (
                        "Sign up free at brave.com/search/api/ (2K/month "
                        "free tier); set OSINT_BRAVE_API_KEY"
                    ),
                },
            }
        ]

    seed = payload or {}
    limit_per = min(
        int(seed.get("limit_per_query", _HIT_LIMIT_PER_DORK) or _HIT_LIMIT_PER_DORK), 20
    )
    queries = _build_dork_queries(seed)
    if not queries:
        return [
            {
                "event_type": "tool-run-result",
                "payload": {
                    "adapter_id": "dork_sweep_brave",
                    "skipped": True,
                    "reason": "no dork templates matched available seed keys",
                },
            }
        ]

    events: list[dict[str, Any]] = []
    queries_run = 0
    total_hits = 0
    with httpx.Client(
        timeout=15.0,
        headers={
            "X-Subscription-Token": api_key,
            "Accept": "application/json",
            "User-Agent": _DEFAULT_UA,
        },
    ) as c:
        for q in queries:
            try:
                r = c.get(_BRAVE_URL, params={"q": q["query"], "count": str(limit_per)})
            except httpx.RequestError:
                continue
            if r.status_code != 200:
                # 429 = rate limited; 401 = bad key; either way we degrade.
                continue
            try:
                body = r.json()
            except ValueError:
                continue
            results = (body.get("web") or {}).get("results") or []
            queries_run += 1
            for item in results[:limit_per]:
                if not isinstance(item, dict):
                    continue
                url = item.get("url") or ""
                title = item.get("title") or ""
                # Brave returns "description" (occasionally with <strong> wrappers).
                description = item.get("description") or ""
                if not url.startswith(("http://", "https://")):
                    continue
                total_hits += 1
                events.append(
                    {
                        "event_type": "dork-hit",
                        "payload": {
                            "source": "dork-sweep",
                            "engine": "brave",
                            "template_id": q["id"],
                            "template_label": q["label"],
                            "url": url,
                            "title": _clean_html_fragment(title, max_len=200) if title else "",
                            "snippet": _clean_html_fragment(description, max_len=300)
                            if description
                            else "",
                            "confidence": "tentative",
                        },
                    }
                )

    events.append(
        {
            "event_type": "tool-run-result",
            "payload": {
                "adapter_id": "dork_sweep_brave",
                "queries_attempted": len(queries),
                "queries_run": queries_run,
                "total_hits": total_hits,
            },
        }
    )
    return events


def _dork_sweep_brave_synthetic(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "event_type": "dork-hit",
            "payload": {
                "source": "dork-sweep",
                "engine": "brave",
                "template_id": "name_linkedin",
                "url": "https://example.com/brave-result",
                "title": "Brave synthetic result",
                "synthetic": True,
            },
        },
        {
            "event_type": "tool-run-result",
            "payload": {
                "adapter_id": "dork_sweep_brave",
                "queries_run": 1,
                "total_hits": 1,
                "synthetic": True,
            },
        },
    ]


# ===========================================================================
# Serper.dev API (env-gated; Google results)
# ===========================================================================
#
# Endpoint: POST https://google.serper.dev/search with {"q": "<query>"}
# Auth: X-API-KEY: <key>
# Serper proxies Google's results structured as JSON. They handle the
# scraping + legal posture; we just consume the API.

_SERPER_URL = "https://google.serper.dev/search"


def dork_sweep_serper(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Dork-sweep via Serper.dev (Google results)."""
    api_key = os.environ.get("OSINT_SERPER_API_KEY", "").strip()
    if not api_key:
        return [
            {
                "event_type": "tool-run-result",
                "payload": {
                    "adapter_id": "dork_sweep_serper",
                    "skipped": True,
                    "reason": "OSINT_SERPER_API_KEY env var not set",
                    "suggest": (
                        "Sign up at serper.dev (2,500 free queries); set OSINT_SERPER_API_KEY"
                    ),
                },
            }
        ]

    seed = payload or {}
    limit_per = min(
        int(seed.get("limit_per_query", _HIT_LIMIT_PER_DORK) or _HIT_LIMIT_PER_DORK), 20
    )
    queries = _build_dork_queries(seed)
    if not queries:
        return [
            {
                "event_type": "tool-run-result",
                "payload": {
                    "adapter_id": "dork_sweep_serper",
                    "skipped": True,
                    "reason": "no dork templates matched available seed keys",
                },
            }
        ]

    events: list[dict[str, Any]] = []
    queries_run = 0
    total_hits = 0
    with httpx.Client(
        timeout=15.0,
        headers={
            "X-API-KEY": api_key,
            "Content-Type": "application/json",
            "User-Agent": _DEFAULT_UA,
        },
    ) as c:
        for q in queries:
            try:
                r = c.post(_SERPER_URL, json={"q": q["query"], "num": limit_per})
            except httpx.RequestError:
                continue
            if r.status_code != 200:
                continue
            try:
                body = r.json()
            except ValueError:
                continue
            results = body.get("organic") or []
            queries_run += 1
            for item in results[:limit_per]:
                if not isinstance(item, dict):
                    continue
                url = item.get("link") or ""
                title = item.get("title") or ""
                # Serper exposes both "snippet" and "date" sometimes; keep snippet only.
                snippet = item.get("snippet") or ""
                if not url.startswith(("http://", "https://")):
                    continue
                total_hits += 1
                events.append(
                    {
                        "event_type": "dork-hit",
                        "payload": {
                            "source": "dork-sweep",
                            "engine": "serper",
                            "template_id": q["id"],
                            "template_label": q["label"],
                            "url": url,
                            "title": _clean_html_fragment(title, max_len=200) if title else "",
                            "snippet": _clean_html_fragment(snippet, max_len=300)
                            if snippet
                            else "",
                            "confidence": "tentative",
                        },
                    }
                )

    events.append(
        {
            "event_type": "tool-run-result",
            "payload": {
                "adapter_id": "dork_sweep_serper",
                "queries_attempted": len(queries),
                "queries_run": queries_run,
                "total_hits": total_hits,
            },
        }
    )
    return events


def _dork_sweep_serper_synthetic(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "event_type": "dork-hit",
            "payload": {
                "source": "dork-sweep",
                "engine": "serper",
                "template_id": "name_linkedin",
                "url": "https://example.com/serper-result",
                "title": "Serper synthetic result",
                "synthetic": True,
            },
        },
        {
            "event_type": "tool-run-result",
            "payload": {
                "adapter_id": "dork_sweep_serper",
                "queries_run": 1,
                "total_hits": 1,
                "synthetic": True,
            },
        },
    ]


# ===========================================================================
# Bing main-UI HTML scrape (keyless 4th engine)
# ===========================================================================
#
# Endpoint: https://www.bing.com/search?q=<query>&cc=us&setlang=en-US
# Auth: none. Anti-bot: per-IP rate limit ~10/min (returns 429 then captcha).
# Result block (stable since 2021):
#   <li class="b_algo">
#     <h2><a href="<url>"...>title</a></h2>
#     <div class="b_caption"><p class="b_lineclamp...">snippet</p></div>
#   </li>
#
# Why we ship Bing despite DDG being "Bing-flavored": DDG's html.duckduckgo.com
# endpoint returns a curated subset (privacy-pruned, no personalization). The
# Bing main UI returns the full result set + sometimes surfaces sites that DDG
# de-prioritizes. Cross-engine corroboration in dossier-shape.ts then
# graduates URLs surfaced by ≥2 engines from TENTATIVE to MEDIUM. (Naomi:
# the cost of redundancy is one extra TCP-to-Microsoft-edge per query.)

_BING_URL = "https://www.bing.com/search"

# Find each algorithmic result li: anchored on `<li class="b_algo">` opener
# until the next `<li class="b_algo">` opener or end-of-document. Bing's
# 2026 layout puts an inline `<style>` block, a top-citation `tpcn` div,
# an `<h2>` containing the actual title anchor, and a `<p class="b_lineclamp...">`
# snippet inside each result li. We slice the whole li and let per-field
# regexes pick out the bits we want -- one pass through the page, no
# selector library dependency.
_BING_RESULT_LI_RE = re.compile(
    r'(<li[^>]+class="[^"]*b_algo[^"]*"[^>]*>'
    r".*?"
    r")"
    r'(?=<li[^>]+class="[^"]*b_algo[^"]*"|\Z)',
    re.IGNORECASE | re.DOTALL,
)

# Inline <style>...</style> blocks inside b_algo li elements; stripped
# before per-field extraction so style-rules that mention "b_algo" don't
# confuse downstream regexes.
_BING_INLINE_STYLE_RE = re.compile(
    r"<style[^>]*>.*?</style>",
    re.IGNORECASE | re.DOTALL,
)

# Title anchor: <h2 ...><a ... href="<bing-redirect-or-real-url>">TITLE</a></h2>
_BING_TITLE_ANCHOR_RE = re.compile(
    r"<h2[^>]*>\s*" r'<a[^>]+href="([^"]+)"[^>]*>' r"(.*?)" r"</a>\s*" r"</h2>",
    re.IGNORECASE | re.DOTALL,
)

# Snippet paragraph: <p class="b_lineclamp..." or "b_paractl" or "b_caption">...</p>
# 2026 Bing prefers `b_lineclamp2`; older results use `b_caption b_rich`.
# Skip the leading <span class="news_dt">date</span> prefix when present
# (we keep dates structurally; the body text follows).
_BING_SNIPPET_RE = re.compile(
    r'<p[^>]+class="[^"]*(?:b_lineclamp|b_paractl)[^"]*"[^>]*>' r"(.*?)" r"</p>",
    re.IGNORECASE | re.DOTALL,
)


def _parse_bing_html(html_body: str) -> list[dict[str, str]]:
    """Parse Bing main-UI HTML into hits with url + title + snippet.

    Bing's 2026 layout wraps every result URL in a `bing.com/ck/a?...&u=a1<base64>`
    redirect; `_strip_bing_redirect` unwraps it back to the real URL.

    Naomi gate: only result text leaves this function -- the page bytes
    never touch disk past the SSE bus.

    Robustness: each li is sliced independently; failures on one li (e.g.
    image-result li with no h2) don't break extraction of sibling lis.
    """
    hits: list[dict[str, str]] = []
    for m in _BING_RESULT_LI_RE.finditer(html_body):
        block_raw = m.group(1)
        block = _BING_INLINE_STYLE_RE.sub("", block_raw)

        title_match = _BING_TITLE_ANCHOR_RE.search(block)
        if not title_match:
            continue  # image-card or carousel li without a title anchor

        raw_url = title_match.group(1).strip()
        title_html = title_match.group(2)

        url = _strip_bing_redirect(raw_url)
        if not url or not url.startswith(("http://", "https://")):
            continue
        # Reject Bing-internal URLs that slipped through: image-search and
        # video-search links go to /images/search?... which isn't a hit.
        if "bing.com/images/" in url or "bing.com/videos/" in url:
            continue

        title_text = _clean_html_fragment(title_html, max_len=200)
        snippet_text = ""
        sn = _BING_SNIPPET_RE.search(block)
        if sn:
            snippet_text = _clean_html_fragment(sn.group(1), max_len=300)

        hits.append({"url": url, "title": title_text, "snippet": snippet_text})
    return hits


# ===========================================================================
# Bing query rewriter (site:-operator stub-avoidance)
# ===========================================================================
#
# Empirically (pressure-tested 2026-05-15): Bing serves a stub error page
# in response to `site:DOMAIN ...` queries scraped via Patchright/Camoufox,
# but returns full results when the same intent is expressed as a free-text
# domain mention (`"QUERY" DOMAIN`). The Bing anti-scrape ML clearly
# fingerprints the `site:` operator + suspicious-looking PII context as a
# scrape signature.
#
# `_rewrite_query_for_bing` performs the transform; `_url_matches_domain`
# is the post-filter we apply to result URLs so the original intent of the
# `site:` operator (restrict to that domain) is preserved. Templates that
# don't use `site:` pass through unchanged.

_BING_SITE_OPERATOR_RE = re.compile(r"\bsite:([A-Za-z0-9.\-]+)", re.IGNORECASE)


def _rewrite_query_for_bing(query: str) -> tuple[str, list[str]]:
    """Strip site:DOMAIN operators and return (rewritten_query, extracted_domains).

    The extracted domain list feeds `_url_matches_domain` to filter results
    post-hoc -- preserving the original dork's "this domain only" intent
    without triggering Bing's anti-scrape stub. Domain mentions are
    appended as free-text tokens so Bing still favors those domains in
    ranking.

    Cleanup: removing `site:DOMAIN` from a string like
    `"alice@example.com" (site:pastebin.com OR site:ghostbin.com)` leaves
    orphan punctuation `( OR )` that confuses Bing's parser. We
    additionally:
      - drop the `-` prefix when a `-site:` operator gets stripped
      - drop empty `(...)` parens
      - drop dangling `OR` / `AND` operators with no operand
      - collapse multiple spaces
    """
    domains = [d.lower() for d in _BING_SITE_OPERATOR_RE.findall(query)]
    # Match optional leading `-` (Bing-style negation) + `site:DOMAIN`.
    rewritten = re.sub(r"-?\s*\bsite:[A-Za-z0-9.\-]+", "", query, flags=re.IGNORECASE)
    # Drop now-empty parenthetical groups: "( OR )", "(   )", "( OR OR )".
    for _ in range(3):
        rewritten = re.sub(
            r"\(\s*(?:OR\s+|AND\s+)*(?:OR|AND)?\s*\)",
            "",
            rewritten,
            flags=re.IGNORECASE,
        )
    # Drop dangling boolean operators at start/end or doubled.
    rewritten = re.sub(r"^\s*(?:OR|AND)\b", "", rewritten, flags=re.IGNORECASE)
    rewritten = re.sub(r"\b(?:OR|AND)\s*$", "", rewritten, flags=re.IGNORECASE)
    rewritten = re.sub(r"\b(?:OR|AND)\s+(?:OR|AND)\b", "OR", rewritten, flags=re.IGNORECASE)
    # Collapse whitespace.
    rewritten = re.sub(r"\s+", " ", rewritten).strip()
    if domains:
        unique_domains = list(dict.fromkeys(domains))
        rewritten = f"{rewritten} {' '.join(unique_domains)}".strip()
    return (rewritten, domains)


def _url_matches_domain(url: str, allowed_domains: list[str]) -> bool:
    """True if the URL's host matches one of the allowed domains.

    Match is suffix-based: `linkedin.com` matches `www.linkedin.com` and
    `subdomain.linkedin.com`. Empty allowed_domains list returns True
    (no filter applied).
    """
    if not allowed_domains:
        return True
    try:
        host = urllib.parse.urlparse(url).hostname or ""
    except Exception:
        return False
    host = host.lower().removeprefix("www.")
    for d in allowed_domains:
        d_norm = d.lower().removeprefix("www.")
        if host == d_norm or host.endswith("." + d_norm):
            return True
    return False


def _strip_bing_redirect(url: str) -> str:
    """Bing wraps result URLs in `bing.com/ck/a?...&u=a1<base64-url>`.
    The `u` param's value is the real URL, base64url-encoded with an
    `a1` prefix Bing prepends.

    The href captured from a rendered HTML page is entity-encoded
    (`&amp;` not `&`); we unescape first so `parse_qs` sees real `&`
    separators. Without this step, `u` is part of the previous param's
    value and the URL never unwraps.
    """
    # Unescape entities first; the href content from rendered DOM is
    # often still entity-encoded (Patchright serializes the raw HTML).
    url = html.unescape(url)
    if "bing.com/ck/a" not in url:
        return url
    try:
        parsed = urllib.parse.urlparse(url)
        qs = urllib.parse.parse_qs(parsed.query)
        u = qs.get("u", [""])[0]
        if u.startswith("a1"):
            u = u[2:]
        import base64

        padded = u + "=" * (-len(u) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8", errors="ignore")
        if decoded.startswith(("http://", "https://")):
            return decoded
    except Exception:
        return url
    return url


# Bing browser-UA pool. Real Chrome strings from major versions; rotated
# per-query so the per-IP rate-limit governor sees varied client fingerprints.
# (We don't ship the full 50-string offensive-osint §6.4 pool inline here --
# that lives in `_ua.py` once Ship 8 lands. This is a tight Bing-only set.)
_BING_UA_POOL: tuple[str, ...] = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36 Edg/128.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.6 Safari/605.1.15",
)


def _bing_ua_for_query(idx: int) -> str:
    """Pick a UA deterministically from the pool, indexed by query position
    so multi-query sweeps cycle through Chrome/Edge/Safari variants. Avoids
    importing `random` here (RNG state in workers complicates reproducibility
    of synthetic tests)."""
    return _BING_UA_POOL[idx % len(_BING_UA_POOL)]


def _bing_fetch(url: str, ua: str, timeout_s: float = 60.0) -> tuple[int, str]:
    """Fetch a Bing search URL via Scrapling's StealthyFetcher (Patchright + Camoufox).

    Returns (status, body_text). status=0 on exception, body_text='' on failure.

    Why StealthyFetcher and not plain httpx / curl_cffi: Bing's 2026 results
    body is JavaScript-rendered after page load. curl_cffi gets the page
    chrome only (no `b_algo` blocks). StealthyFetcher boots a real browser
    (Patchright = patched Playwright with anti-detection), waits for
    network-idle, then serializes the rendered DOM -- which contains the
    actual result list.

    Cost: 5-15s per query vs ~500ms for static fetchers. This is the
    necessary cost of beating Bing's anti-scrape ML.
    """
    # Late-import: keeps Patchright/Playwright out of the module's import
    # path until an adapter actually fires up the browser.
    try:
        from scrapling.fetchers import StealthyFetcher
    except ImportError:
        return (0, "")

    try:
        # network_idle=True lets the JS bundle finish rendering before we
        # serialize the DOM. headless=True keeps the headless-fingerprint
        # masking that Patchright applies; we want headless because we're
        # running inside a worker, not interactively.
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


def dork_sweep_bing(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Dork-sweep via Bing main-UI through Scrapling StealthyFetcher.

    Routes through Patchright (patched Playwright) + Camoufox-class
    headless Chrome with network_idle waiting so the JS-rendered results
    DOM is fully populated before we serialize. Plain httpx / curl_cffi
    only get the page chrome (zero `b_algo` blocks); StealthyFetcher
    fires up a real browser and beats Bing's anti-scrape ML.

    Per-query pre-processing rewrites `site:DOMAIN ...` templates into
    `"..." DOMAIN` free-text form -- empirical pressure-test 2026-05-15
    showed Bing's bot wall fingerprints the `site:` operator + scrape
    signatures and returns a stub error page. The intent of the `site:`
    operator is preserved post-hoc by `_url_matches_domain` filtering
    on result URLs.

    UA rotates per-query through 5 realistic browser strings. Cost:
    5-15s per query (browser boot + render + serialize). This is the
    real cost of beating Bing; cheaper engines (DDG, Brave API, Serper
    API) run in parallel from the workflow definition.

    Payload: same as dork_sweep_ddg.

    Naomi gate: queries never logged; only result URLs + titles +
    snippets surface back through the event stream.
    """
    seed = payload or {}
    limit_per = min(
        int(seed.get("limit_per_query", _HIT_LIMIT_PER_DORK) or _HIT_LIMIT_PER_DORK), 25
    )
    queries = _build_dork_queries(seed)
    if not queries:
        return [
            {
                "event_type": "tool-run-result",
                "payload": {
                    "adapter_id": "dork_sweep_bing",
                    "skipped": True,
                    "reason": "no dork templates matched available seed keys",
                    "templates_total": len(_PV_TEMPLATES),
                },
            }
        ]

    events: list[dict[str, Any]] = []
    queries_run = 0
    queries_failed = 0
    total_hits = 0
    for idx, q in enumerate(queries):
        # Anti-detection jitter between queries; modest because the
        # browser-render path is already 5-15s/query (rate-limit headroom
        # is not the bottleneck, BBPC -- browser boot per call -- is).
        if idx > 0:
            jitter = 0.5 * ((idx * 7) % 11) / 10
            time.sleep(1.0 + jitter)

        # Rewrite site:-operator queries into free-text form to dodge
        # Bing's anti-scrape stub. Preserve original intent via post-filter.
        rewritten_query, allowed_domains = _rewrite_query_for_bing(q["query"])
        encoded_q = urllib.parse.quote_plus(rewritten_query)
        url = f"{_BING_URL}?q={encoded_q}&cc=us&setlang=en-US"

        _ua = _bing_ua_for_query(idx)
        status, body = _bing_fetch(url, _ua)
        if status != 200 or not body:
            queries_failed += 1
            continue

        parsed_hits = _parse_bing_html(body)
        # Apply post-hoc domain filter only when the original template
        # had a `site:` operator; otherwise pass-through.
        if allowed_domains:
            parsed_hits = [h for h in parsed_hits if _url_matches_domain(h["url"], allowed_domains)]
        hits = parsed_hits[:limit_per]
        queries_run += 1
        for h in hits:
            total_hits += 1
            events.append(
                {
                    "event_type": "dork-hit",
                    "payload": {
                        "source": "dork-sweep",
                        "engine": "bing",
                        "template_id": q["id"],
                        "template_label": q["label"],
                        "url": h["url"],
                        "title": h["title"],
                        "snippet": h.get("snippet", ""),
                        "confidence": "tentative",
                    },
                }
            )

    events.append(
        {
            "event_type": "tool-run-result",
            "payload": {
                "adapter_id": "dork_sweep_bing",
                "queries_attempted": len(queries),
                "queries_run": queries_run,
                "queries_failed": queries_failed,
                "total_hits": total_hits,
                "fetch_method": "scrapling-stealthy-patchright",
            },
        }
    )
    return events


def _dork_sweep_bing_synthetic(payload: dict[str, Any]) -> list[dict[str, Any]]:
    name = payload.get("name") or "Test Host"
    return [
        {
            "event_type": "dork-hit",
            "payload": {
                "source": "dork-sweep",
                "engine": "bing",
                "template_id": "name_linkedin",
                "template_label": "Host name on LinkedIn profiles",
                "url": "https://www.linkedin.com/in/test-host",
                "title": f"{name} — LinkedIn (synthetic via bing)",
                "snippet": "Synthetic snippet preview for Bing engine smoke test.",
                "confidence": "tentative",
                "synthetic": True,
            },
        },
        {
            "event_type": "tool-run-result",
            "payload": {
                "adapter_id": "dork_sweep_bing",
                "queries_run": 1,
                "total_hits": 1,
                "synthetic": True,
            },
        },
    ]


# ===========================================================================
# Yandex main-UI HTML scrape (keyless 5th engine; RU/EU/Eastern European)
# ===========================================================================
#
# Endpoint: https://yandex.com/search/?text=<query>
# Auth: none. Anti-bot: ML-driven. JS-rendered results layer same as Bing.
#
# Why Yandex matters for PV: indexes a substantial set of RU/UA/PL/CZ
# Eastern-European sites that Google de-prioritizes and Bing rarely
# surfaces. For a host claiming Eastern European residence/identity,
# Yandex frequently returns the canonical local-language record while
# Western engines miss entirely.
#
# Yandex result structure (2026, rendered):
#   <a class="OrganicTitle-Link" href="<DIRECT-URL>">
#     <h2 class="OrganicTitle-LinkText">
#       <span class="OrganicTitleContentSpan">TITLE (with <b> match tags)</span>
#     </h2>
#   </a>
#   ...intervening chrome / extralinks...
#   <div class="Organic-ContentWrapper">
#     <div class="...OrganicText...">
#       <span class="OrganicTextContentSpan">SNIPPET</span>
#     </div>
#   </div>
#
# Direct URLs (no redirect wrapping) -- nice change vs Bing's ck/a pattern.
# `site:` operator works on Yandex (unlike Bing) so no query rewrite needed.

_YANDEX_URL = "https://yandex.com/search/"

# Capture title anchor (URL + title-text span). Each result has exactly
# one OrganicTitle-Link; we use it as the structural anchor for the result.
_YANDEX_TITLE_ANCHOR_RE = re.compile(
    r'<a[^>]*\bclass="[^"]*\bOrganicTitle-Link\b[^"]*"[^>]*href="([^"]+)"[^>]*>'
    r".*?"
    r'<span[^>]*\bclass="[^"]*\bOrganicTitleContentSpan\b[^"]*"[^>]*>'
    r"(.*?)"
    r"</span>"
    r".*?"
    r"</a>",
    re.IGNORECASE | re.DOTALL,
)

# Capture snippet text span. Anchored on OrganicTextContentSpan -- the
# direct child span inside Organic-ContentWrapper / OrganicText.
_YANDEX_SNIPPET_SPAN_RE = re.compile(
    r'<span[^>]*\bclass="[^"]*\bOrganicTextContentSpan\b[^"]*"[^>]*>' r"(.*?)" r"</span>",
    re.IGNORECASE | re.DOTALL,
)


def _parse_yandex_html(html_body: str) -> list[dict[str, str]]:
    """Parse Yandex search results into hits with url + title + snippet.

    Yandex paths title anchor and snippet span separately within each
    result li (which uses generated random class names per page-load,
    so we can't anchor on the li class). Strategy: find each title
    anchor, then scan forward in a bounded window for the matching
    OrganicTextContentSpan; pair them up in document order.

    Naomi gate: only result text leaves this function -- the page bytes
    never touch disk past the SSE bus.
    """
    title_matches = list(_YANDEX_TITLE_ANCHOR_RE.finditer(html_body))
    snippet_matches = list(_YANDEX_SNIPPET_SPAN_RE.finditer(html_body))

    # Strict pairing: a snippet belongs to title T iff it falls between
    # T and the next title T+1. If no snippet exists in that window, the
    # title gets an empty snippet (no bleed-back from later results).
    hits: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    for i, t in enumerate(title_matches):
        raw_url = t.group(1).strip()
        title_html = t.group(2)
        # Normalize URL: skip relative + javascript: + Yandex internal.
        if raw_url.startswith("//"):
            raw_url = "https:" + raw_url
        if not raw_url.startswith(("http://", "https://")):
            continue
        try:
            host = raw_url.split("/", 3)[2]
        except IndexError:
            continue
        if host.endswith("yandex.com") or host.endswith("yandex.ru") or host == "yandex.com":
            # Yandex-internal links (translate.yandex.com, etc.) -- skip.
            continue
        # Dedup by URL within a single page; Yandex sometimes renders the
        # same URL in multiple result containers (deeplink variants).
        if raw_url in seen_urls:
            continue
        seen_urls.add(raw_url)

        # Window for this title's snippet: from end-of-title to start of
        # next title (or end of document for the last title).
        win_start = t.end()
        win_end = title_matches[i + 1].start() if i + 1 < len(title_matches) else len(html_body)

        snippet_text = ""
        for snip in snippet_matches:
            if snip.start() < win_start:
                continue
            if snip.start() >= win_end:
                break
            snippet_text = _clean_html_fragment(snip.group(1), max_len=300)
            break  # first snippet in window wins

        title_text = _clean_html_fragment(title_html, max_len=200)
        hits.append({"url": raw_url, "title": title_text, "snippet": snippet_text})

    return hits


def _yandex_fetch(url: str, timeout_s: float = 60.0) -> tuple[int, str]:
    """Fetch a Yandex search URL via Scrapling's StealthyFetcher.

    Same JS-render rationale as Bing: Yandex's results are populated
    after page load via XHR. Plain HTTP gets the page chrome only.
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


def dork_sweep_yandex(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Dork-sweep via Yandex through Scrapling StealthyFetcher.

    Routes through Patchright headless Chrome with network_idle wait so
    the JS-rendered OrganicTitle-Link anchors are populated before we
    serialize. Plain httpx gets the page chrome only.

    Yandex supports `site:` operator natively, so no query-rewrite
    pre-processing is needed (unlike Bing). Direct URLs in results --
    no redirect-unwrap step needed either.

    Payload: same as dork_sweep_ddg.

    Naomi gate: queries never logged; only result URLs + titles +
    snippets surface back through the event stream.

    Cost tier: 5-15s per query (browser boot + render). Run in parallel
    with DDG/Brave/Serper/Bing from the workflow step list.

    Coverage rationale: Yandex indexes ~98% of the RU-language web,
    plus significant UA/BY/KZ/PL/CZ coverage that Western engines miss.
    Highest-value engine for hosts/targets in those regions.
    """
    seed = payload or {}
    limit_per = min(
        int(seed.get("limit_per_query", _HIT_LIMIT_PER_DORK) or _HIT_LIMIT_PER_DORK), 25
    )
    queries = _build_dork_queries(seed)
    if not queries:
        return [
            {
                "event_type": "tool-run-result",
                "payload": {
                    "adapter_id": "dork_sweep_yandex",
                    "skipped": True,
                    "reason": "no dork templates matched available seed keys",
                    "templates_total": len(_PV_TEMPLATES),
                },
            }
        ]

    events: list[dict[str, Any]] = []
    queries_run = 0
    queries_failed = 0
    total_hits = 0
    for idx, q in enumerate(queries):
        if idx > 0:
            jitter = 0.5 * ((idx * 7) % 11) / 10
            time.sleep(1.0 + jitter)
        encoded_q = urllib.parse.quote_plus(q["query"])
        url = f"{_YANDEX_URL}?text={encoded_q}"
        status, body = _yandex_fetch(url)
        if status != 200 or not body:
            queries_failed += 1
            continue
        hits = _parse_yandex_html(body)[:limit_per]
        queries_run += 1
        for h in hits:
            total_hits += 1
            events.append(
                {
                    "event_type": "dork-hit",
                    "payload": {
                        "source": "dork-sweep",
                        "engine": "yandex",
                        "template_id": q["id"],
                        "template_label": q["label"],
                        "url": h["url"],
                        "title": h["title"],
                        "snippet": h.get("snippet", ""),
                        "confidence": "tentative",
                    },
                }
            )

    events.append(
        {
            "event_type": "tool-run-result",
            "payload": {
                "adapter_id": "dork_sweep_yandex",
                "queries_attempted": len(queries),
                "queries_run": queries_run,
                "queries_failed": queries_failed,
                "total_hits": total_hits,
                "fetch_method": "scrapling-stealthy-patchright",
            },
        }
    )
    return events


def _dork_sweep_yandex_synthetic(payload: dict[str, Any]) -> list[dict[str, Any]]:
    name = payload.get("name") or "Test Host"
    return [
        {
            "event_type": "dork-hit",
            "payload": {
                "source": "dork-sweep",
                "engine": "yandex",
                "template_id": "name_linkedin",
                "template_label": "Host name on LinkedIn profiles",
                "url": "https://www.linkedin.com/in/test-host",
                "title": f"{name} — LinkedIn (synthetic via yandex)",
                "snippet": "Synthetic snippet preview for Yandex engine smoke test.",
                "confidence": "tentative",
                "synthetic": True,
            },
        },
        {
            "event_type": "tool-run-result",
            "payload": {
                "adapter_id": "dork_sweep_yandex",
                "queries_run": 1,
                "total_hits": 1,
                "synthetic": True,
            },
        },
    ]


# ===========================================================================
# Baidu main-UI HTML scrape (keyless 6th engine; CN coverage)
# ===========================================================================
#
# Endpoint: https://www.baidu.com/s?wd=<query>
# Auth: none.
#
# Coverage rationale: Baidu indexes ~96% of the CN-language web that
# Google blocks and Bing/Yandex de-prioritize. For PV-investigations
# touching CN-residence/identity, Baidu is the canonical local-language
# record. Distant second: Sogou (planned for a future ship).
#
# Baidu 2026 result structure:
#   <div class="result c-container ..." mu="<DESTINATION_URL>" srcid="..." id="N">
#     <h3 class="t ..."><a class="sc-link ..." href="<baidu-redirect>" data-module="title">
#       ...nested spans with title text (with <em> match tags)
#     </a></h3>
#     ...
#     <span class="summary-text_XXX">SNIPPET TEXT</span>
#   </div>
#
# Key insight: the `mu=` attribute on the outer container holds the
# canonical destination URL. No redirect unwrap needed -- just read it.

_BAIDU_URL = "https://www.baidu.com/s"

_BAIDU_RESULT_BLOCK_RE = re.compile(
    r'<div[^>]+class="result c-container[^"]*"[^>]*\bmu="([^"]+)"[^>]*>'
    r"(.*?)"
    r'(?=<div[^>]+class="result c-container[^"]*"|\Z)',
    re.IGNORECASE | re.DOTALL,
)

_BAIDU_TITLE_BLOCK_RE = re.compile(
    r'<h3[^>]*\bclass="[^"]*\bt\b[^"]*"[^>]*>' r"(.*?)" r"</h3>",
    re.IGNORECASE | re.DOTALL,
)

_BAIDU_SNIPPET_SPAN_RE = re.compile(
    r'<span[^>]*\bclass="[^"]*\bsummary-text_[A-Za-z0-9]+\b[^"]*"[^>]*>' r"(.*?)" r"</span>",
    re.IGNORECASE | re.DOTALL,
)


def _parse_baidu_html(html_body: str) -> list[dict[str, str]]:
    """Parse Baidu search results into hits with url + title + snippet.

    URL comes from the result container's `mu=` attribute (canonical
    destination, no Baidu-redirect resolution required). Title from
    the h3.t block. Snippet from the summary-text span.

    Naomi gate: only result text leaves this function -- the page bytes
    never touch disk past the SSE bus.
    """
    hits: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    for m in _BAIDU_RESULT_BLOCK_RE.finditer(html_body):
        raw_url = m.group(1).strip()
        block = m.group(2)
        if not raw_url.startswith(("http://", "https://")):
            continue
        try:
            host = raw_url.split("/", 3)[2]
        except IndexError:
            continue
        if host.endswith("baidu.com"):
            continue
        if raw_url in seen_urls:
            continue
        seen_urls.add(raw_url)

        title_match = _BAIDU_TITLE_BLOCK_RE.search(block)
        if not title_match:
            continue
        title_text = _clean_html_fragment(title_match.group(1), max_len=200)
        if not title_text:
            continue

        snippet_text = ""
        snip = _BAIDU_SNIPPET_SPAN_RE.search(block)
        if snip:
            snippet_text = _clean_html_fragment(snip.group(1), max_len=300)

        hits.append({"url": raw_url, "title": title_text, "snippet": snippet_text})
    return hits


def _baidu_fetch(url: str, timeout_s: float = 60.0) -> tuple[int, str]:
    """Fetch a Baidu search URL via Scrapling's StealthyFetcher."""
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


def dork_sweep_baidu(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Dork-sweep via Baidu through Scrapling StealthyFetcher.

    Coverage: ~96% of the CN-language web. Highest-value engine for
    targets with CN residence/identity. Native `site:` operator support.

    Payload: same as dork_sweep_ddg.

    Naomi gate: queries never logged.
    """
    seed = payload or {}
    limit_per = min(
        int(seed.get("limit_per_query", _HIT_LIMIT_PER_DORK) or _HIT_LIMIT_PER_DORK), 25
    )
    queries = _build_dork_queries(seed)
    if not queries:
        return [
            {
                "event_type": "tool-run-result",
                "payload": {
                    "adapter_id": "dork_sweep_baidu",
                    "skipped": True,
                    "reason": "no dork templates matched available seed keys",
                    "templates_total": len(_PV_TEMPLATES),
                },
            }
        ]

    events: list[dict[str, Any]] = []
    queries_run = 0
    queries_failed = 0
    total_hits = 0
    for idx, q in enumerate(queries):
        if idx > 0:
            jitter = 0.5 * ((idx * 7) % 11) / 10
            time.sleep(1.0 + jitter)
        encoded_q = urllib.parse.quote_plus(q["query"])
        url = f"{_BAIDU_URL}?wd={encoded_q}"
        status, body = _baidu_fetch(url)
        if status != 200 or not body:
            queries_failed += 1
            continue
        hits = _parse_baidu_html(body)[:limit_per]
        queries_run += 1
        for h in hits:
            total_hits += 1
            events.append(
                {
                    "event_type": "dork-hit",
                    "payload": {
                        "source": "dork-sweep",
                        "engine": "baidu",
                        "template_id": q["id"],
                        "template_label": q["label"],
                        "url": h["url"],
                        "title": h["title"],
                        "snippet": h.get("snippet", ""),
                        "confidence": "tentative",
                    },
                }
            )

    events.append(
        {
            "event_type": "tool-run-result",
            "payload": {
                "adapter_id": "dork_sweep_baidu",
                "queries_attempted": len(queries),
                "queries_run": queries_run,
                "queries_failed": queries_failed,
                "total_hits": total_hits,
                "fetch_method": "scrapling-stealthy-patchright",
            },
        }
    )
    return events


def _dork_sweep_baidu_synthetic(payload: dict[str, Any]) -> list[dict[str, Any]]:
    name = payload.get("name") or "Test Host"
    return [
        {
            "event_type": "dork-hit",
            "payload": {
                "source": "dork-sweep",
                "engine": "baidu",
                "template_id": "name_linkedin",
                "template_label": "Host name on LinkedIn profiles",
                "url": "https://www.linkedin.com/in/test-host",
                "title": f"{name} — LinkedIn (synthetic via baidu)",
                "snippet": "Synthetic snippet preview for Baidu engine smoke test.",
                "confidence": "tentative",
                "synthetic": True,
            },
        },
        {
            "event_type": "tool-run-result",
            "payload": {
                "adapter_id": "dork_sweep_baidu",
                "queries_run": 1,
                "total_hits": 1,
                "synthetic": True,
            },
        },
    ]


# ===========================================================================
# Naver main-UI HTML scrape (keyless 7th engine; KR coverage)
# ===========================================================================
#
# Endpoint: https://search.naver.com/search.naver?where=web&query=<query>
# Coverage: #1 in South Korea (~70% market share, ahead of Google in KR).
# Indexes kr.linkedin.com, naver.blog, cafe.naver.com, KR news sites that
# Google de-prioritizes outside KR. Native `site:` operator support.
#
# Naver 2026 result structure: React/Vue-style component tree with random
# content-hashed class names (`fender-ui_22887c0d` etc). Stable markers:
#   - Title:  span.sds-comps-text-type-headline1
#   - Snippet: span.sds-comps-text-ellipsis-3
#   - Result link: <a nocr="1" href="<external>">
#
# Strategy: BeautifulSoup card-walk. For each title span, walk up the DOM
# up to 12 levels to find a containing card with a nocr=1 anchor +
# external URL; pair title + URL + snippet from sibling spans in the card.
# Regex parsing is hopeless against the React markup; BS4 is required.

_NAVER_URL = "https://search.naver.com/search.naver"


def _parse_naver_html(html_body: str) -> list[dict[str, str]]:
    """Parse Naver search results into hits with url + title + snippet."""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return []

    soup = BeautifulSoup(html_body, "html.parser")
    titles = soup.find_all("span", class_="sds-comps-text-type-headline1")

    hits: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    for t in titles:
        title_text = _clean_html_fragment(str(t), max_len=200)
        if not title_text:
            continue

        card = t
        url: str | None = None
        for _ in range(12):
            card = getattr(card, "parent", None)
            if card is None:
                break
            anchors = card.find_all("a", attrs={"nocr": "1"})
            for a in anchors:
                href = (a.get("href") or "").strip()
                if not href.startswith(("http://", "https://")):
                    continue
                try:
                    host = href.split("/", 3)[2]
                except IndexError:
                    continue
                if "naver.com" in host or "pstatic.net" in host:
                    continue
                url = href
                break
            if url:
                break
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)

        snippet_text = ""
        if card is not None:
            snip = card.find("span", class_="sds-comps-text-ellipsis-3")
            if snip is not None:
                snippet_text = _clean_html_fragment(str(snip), max_len=300)

        hits.append({"url": url, "title": title_text, "snippet": snippet_text})
    return hits


def _naver_fetch(url: str, timeout_s: float = 60.0) -> tuple[int, str]:
    """Fetch a Naver search URL via Scrapling's StealthyFetcher."""
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


def dork_sweep_naver(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Dork-sweep via Naver through Scrapling StealthyFetcher.

    Coverage: #1 in South Korea (~70% market share). Highest-value
    engine for hosts/targets with KR residence/identity.

    Naomi gate: queries never logged.
    """
    seed = payload or {}
    limit_per = min(
        int(seed.get("limit_per_query", _HIT_LIMIT_PER_DORK) or _HIT_LIMIT_PER_DORK), 25
    )
    queries = _build_dork_queries(seed)
    if not queries:
        return [
            {
                "event_type": "tool-run-result",
                "payload": {
                    "adapter_id": "dork_sweep_naver",
                    "skipped": True,
                    "reason": "no dork templates matched available seed keys",
                    "templates_total": len(_PV_TEMPLATES),
                },
            }
        ]

    events: list[dict[str, Any]] = []
    queries_run = 0
    queries_failed = 0
    total_hits = 0
    for idx, q in enumerate(queries):
        if idx > 0:
            jitter = 0.5 * ((idx * 7) % 11) / 10
            time.sleep(1.0 + jitter)
        encoded_q = urllib.parse.quote_plus(q["query"])
        url = f"{_NAVER_URL}?where=web&query={encoded_q}"
        status, body = _naver_fetch(url)
        if status != 200 or not body:
            queries_failed += 1
            continue
        hits = _parse_naver_html(body)[:limit_per]
        queries_run += 1
        for h in hits:
            total_hits += 1
            events.append(
                {
                    "event_type": "dork-hit",
                    "payload": {
                        "source": "dork-sweep",
                        "engine": "naver",
                        "template_id": q["id"],
                        "template_label": q["label"],
                        "url": h["url"],
                        "title": h["title"],
                        "snippet": h.get("snippet", ""),
                        "confidence": "tentative",
                    },
                }
            )

    events.append(
        {
            "event_type": "tool-run-result",
            "payload": {
                "adapter_id": "dork_sweep_naver",
                "queries_attempted": len(queries),
                "queries_run": queries_run,
                "queries_failed": queries_failed,
                "total_hits": total_hits,
                "fetch_method": "scrapling-stealthy-patchright",
            },
        }
    )
    return events


def _dork_sweep_naver_synthetic(payload: dict[str, Any]) -> list[dict[str, Any]]:
    name = payload.get("name") or "Test Host"
    return [
        {
            "event_type": "dork-hit",
            "payload": {
                "source": "dork-sweep",
                "engine": "naver",
                "template_id": "name_linkedin",
                "template_label": "Host name on LinkedIn profiles",
                "url": "https://kr.linkedin.com/in/test-host",
                "title": f"{name} — LinkedIn (synthetic via naver)",
                "snippet": "Synthetic snippet preview for Naver engine smoke test.",
                "confidence": "tentative",
                "synthetic": True,
            },
        },
        {
            "event_type": "tool-run-result",
            "payload": {
                "adapter_id": "dork_sweep_naver",
                "queries_run": 1,
                "total_hits": 1,
                "synthetic": True,
            },
        },
    ]


# ===========================================================================
# Registry installation
# ===========================================================================

_REGISTRY = get_registry()

_REGISTRY.register(
    "dork_sweep_ddg",
    dork_sweep_ddg,
    synthetic_mode=_dork_sweep_ddg_synthetic,
    in_process=True,
    description="Dork-sweep via DuckDuckGo HTML scrape (W13.dk, keyless).",
)
_REGISTRY.register(
    "dork_sweep_brave",
    dork_sweep_brave,
    synthetic_mode=_dork_sweep_brave_synthetic,
    in_process=True,
    description=("Dork-sweep via Brave Search API (W13.dk, requires OSINT_BRAVE_API_KEY)."),
)
_REGISTRY.register(
    "dork_sweep_serper",
    dork_sweep_serper,
    synthetic_mode=_dork_sweep_serper_synthetic,
    in_process=True,
    description=(
        "Dork-sweep via Serper.dev (Google results, W13.dk, requires OSINT_SERPER_API_KEY)."
    ),
)
_REGISTRY.register(
    "dork_sweep_bing",
    dork_sweep_bing,
    synthetic_mode=_dork_sweep_bing_synthetic,
    in_process=True,
    description=(
        "Dork-sweep via Bing main-UI HTML scrape (W13.dk, keyless 4th engine, "
        "UA-rotated for per-IP burst-protection softening)."
    ),
)
_REGISTRY.register(
    "dork_sweep_yandex",
    dork_sweep_yandex,
    synthetic_mode=_dork_sweep_yandex_synthetic,
    in_process=True,
    description=(
        "Dork-sweep via Yandex through Scrapling StealthyFetcher (W13.dk, "
        "keyless 5th engine, RU/EU/Eastern European coverage)."
    ),
)
_REGISTRY.register(
    "dork_sweep_baidu",
    dork_sweep_baidu,
    synthetic_mode=_dork_sweep_baidu_synthetic,
    in_process=True,
    description=(
        "Dork-sweep via Baidu through Scrapling StealthyFetcher (W13.dk, "
        "keyless 6th engine, CN-language coverage)."
    ),
)
_REGISTRY.register(
    "dork_sweep_naver",
    dork_sweep_naver,
    synthetic_mode=_dork_sweep_naver_synthetic,
    in_process=True,
    description=(
        "Dork-sweep via Naver through Scrapling StealthyFetcher + BS4 "
        "card-walk (W13.dk, keyless 7th engine, KR-language coverage; "
        "Naver is #1 search engine in South Korea ~70%% market share)."
    ),
)

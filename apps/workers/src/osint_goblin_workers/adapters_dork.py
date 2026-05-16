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

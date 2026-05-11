// Adapter catalog -- single source of truth for the dropdown + palette.
// Mirrors osint_goblin_workers registry; a future /adapters endpoint
// can replace this when registry introspection lands (Sprint-4).
//
// Iris-IA grouping (wave-3 accept 2026-05-11): six-primitive scope
// anchor maps adapter id -> group. Re-open trigger: total >50 OR any
// one primitive >12.

export type AdapterGroup =
  | "workflow"
  | "addrgeo"
  | "email"
  | "phone"
  | "domain"
  | "person"
  | "social"
  | "image"
  | "smoke";

export interface AdapterMeta {
  readonly id: string;
  readonly label: string;
  readonly hint: string;
  readonly examplePayload: string;
  readonly group: AdapterGroup;
}

export const GROUP_LABELS: Record<AdapterGroup, string> = {
  workflow: "Workflows (ADR-0017 §3)",
  addrgeo: "Address / Geo",
  email: "Email",
  phone: "Phone",
  domain: "Domain / subdomain",
  person: "Person — name-based",
  social: "Social handle",
  image: "Image",
  smoke: "Smoke / test",
};

export const GROUP_ORDER: ReadonlyArray<AdapterGroup> = [
  "workflow",
  "addrgeo",
  "email",
  "phone",
  "domain",
  "person",
  "social",
  "image",
  "smoke",
];

export const ADAPTERS: ReadonlyArray<AdapterMeta> = [
  // Workflows -- run via workflow_runner instead of tool_runner; the API
  // routes w*.* ids automatically (is_workflow_id in broker.py).
  {
    id: "w1.un",
    label: "W1 — Username Dossier",
    hint: "Maigret + Sherlock + social fan-out across N platforms.",
    examplePayload: '{\n  "username": "alice"\n}',
    group: "workflow",
  },
  {
    id: "w2.em",
    label: "W2 — Email Lookup",
    hint: "MX validate → HIBP breaches → email-to-account pivot.",
    examplePayload: '{\n  "email": "alice@example.com"\n}',
    group: "workflow",
  },
  {
    id: "w3.ph",
    label: "W3 — Phone Pivot",
    hint: "Format + carrier + timezone + Google-SERP mention scan.",
    examplePayload: '{\n  "phone": "+1 217 555 0123",\n  "region": "US"\n}',
    group: "workflow",
  },
  {
    id: "w5.do",
    label: "W5 — Domain + CT Timeline",
    hint: "CT log + Wayback CDX + subfinder + amass subdomain enum.",
    examplePayload: '{\n  "domain": "example.com"\n}',
    group: "workflow",
  },
  {
    id: "w4.im",
    label: "W4 — Image OSINT",
    hint: "Reverse-image aggregator + EXIF + provenance + geo.",
    examplePayload:
      '{\n  "image_url": "https://example.com/photo.jpg",\n  "case_id": "case-2026-05-alice"\n}',
    group: "workflow",
  },
  {
    id: "w6.pe",
    label: "W6 — Person Background",
    hint: "TruePeopleSearch + LinkedIn + GitHub + breach surface.",
    examplePayload:
      '{\n  "name": "Alice Smith",\n  "city": "Springfield",\n  "state": "IL",\n  "company": "Acme Corp",\n  "email": "alice@example.com"\n}',
    group: "workflow",
  },
  {
    id: "w7.fa",
    label: "W7 — Face Match (OPSEC red)",
    hint: "Reverse image + biometric gate. Surfaces image-match events.",
    examplePayload: '{\n  "image_url": "https://example.com/face.jpg"\n}',
    group: "workflow",
  },
  {
    id: "w8.ge",
    label: "W8 — Event Geolocation",
    hint: "Image geo + KartaView + sun-angle. Time-pinned.",
    examplePayload:
      '{\n  "image_url": "https://example.com/photo.jpg",\n  "lat": 39.78,\n  "lon": -89.65,\n  "season": "winter"\n}',
    group: "workflow",
  },
  {
    id: "w9.pv",
    label: "W9 — Property Vetting (pivot)",
    hint: "Geocode + Inside Airbnb + reverse-image + EXIF + host name + breach.",
    examplePayload:
      '{\n  "address": "123 Main St, Springfield IL",\n  "csv_path": "data/inside-airbnb/springfield-il.csv",\n  "host_name": "Alice Smith",\n  "city": "Springfield",\n  "state": "IL",\n  "email": "alice@example.com",\n  "photo_url": "https://example.com/listing-photo.jpg"\n}',
    group: "workflow",
  },
  // Address / Geo
  {
    id: "nominatim_geocode",
    label: "Nominatim — address → lat/lon",
    hint: "OSM geocoding. 1 req/sec self-throttled.",
    examplePayload: '{\n  "q": "1600 Pennsylvania Ave, Washington DC"\n}',
    group: "addrgeo",
  },
  {
    id: "inside_airbnb_listings",
    label: "Inside Airbnb — host fingerprint",
    hint: "Search pre-downloaded city CSV; flags commercial operators.",
    examplePayload: '{\n  "csv_path": "data/inside-airbnb/city.csv",\n  "host_name": "Alice"\n}',
    group: "addrgeo",
  },
  {
    id: "kartaview_nearby",
    label: "KartaView — OSM street-level at lat/lon",
    hint: "Open street-level imagery. No API key.",
    examplePayload: '{\n  "lat": 39.78,\n  "lon": -89.65,\n  "radius_m": 200\n}',
    group: "addrgeo",
  },
  // Phone
  {
    id: "phone_format_validate",
    label: "Phone — format + classify (libphonenumber)",
    hint: "Parse + valid + region + line type (mobile / VoIP / fixed-line).",
    examplePayload: '{\n  "phone": "+1 217 555 0123",\n  "region": "US"\n}',
    group: "phone",
  },
  {
    id: "phone_carrier_lookup",
    label: "Phone — carrier + geocode",
    hint: "Carrier name + region geocode from libphonenumber prefix DB.",
    examplePayload: '{\n  "phone": "+1 217 555 0123",\n  "region": "US"\n}',
    group: "phone",
  },
  {
    id: "phone_timezone_lookup",
    label: "Phone — timezone(s)",
    hint: "Timezone for the number's region. Claim-vs-region check.",
    examplePayload: '{\n  "phone": "+1 217 555 0123",\n  "region": "US"\n}',
    group: "phone",
  },
  {
    id: "google_serp_phone",
    label: "Google SERP — phone mentions",
    hint: "Search Google for public mentions of the number.",
    examplePayload: '{\n  "phone": "+1 217 555 0123"\n}',
    group: "phone",
  },
  // Domain
  {
    id: "ct_log_lookup",
    label: "CT logs — crt.sh subdomain enum",
    hint: "Certificate Transparency log query. Free, no auth.",
    examplePayload: '{\n  "domain": "example.com",\n  "limit": 100\n}',
    group: "domain",
  },
  {
    id: "wayback_cdx_subdomains",
    label: "Wayback CDX — subdomain history",
    hint: "Historical archived URLs grouped by subdomain.",
    examplePayload: '{\n  "domain": "example.com",\n  "limit": 100\n}',
    group: "domain",
  },
  {
    id: "subfinder_subprocess",
    label: "subfinder — Project Discovery enum",
    hint: "Passive subdomain enum. Requires subfinder on PATH.",
    examplePayload: '{\n  "domain": "example.com"\n}',
    group: "domain",
  },
  {
    id: "amass_subprocess",
    label: "amass — OWASP enum",
    hint: "Heavy passive enum. Requires amass on PATH.",
    examplePayload: '{\n  "domain": "example.com",\n  "timeout_s": 120\n}',
    group: "domain",
  },
  // Email
  {
    id: "email_mx_validate",
    label: "Email — DNS MX validate",
    hint: "Format + DNS-A record probe. Catches typos.",
    examplePayload: '{\n  "email": "user@example.com"\n}',
    group: "email",
  },
  {
    id: "hibp_breach_check",
    label: "HIBP — breach by domain",
    hint: "Have I Been Pwned free 'breaches-by-domain' endpoint.",
    examplePayload: '{\n  "email": "user@example.com"\n}',
    group: "email",
  },
  // Person — name-based
  {
    id: "true_people_search",
    label: "TruePeopleSearch — person",
    hint: "Scrapling stealth subprocess. Top 5 results, single page.",
    examplePayload: '{\n  "name": "Alice Smith",\n  "city": "Springfield",\n  "state": "IL"\n}',
    group: "person",
  },
  {
    id: "rocketreach_search",
    label: "RocketReach — name search",
    hint: "Free-tier name search (no email/phone). Scrapling subprocess.",
    examplePayload: '{\n  "name": "Alice Smith",\n  "company": "Acme Corp"\n}',
    group: "person",
  },
  {
    id: "google_serp_linkedin",
    label: "Google SERP — LinkedIn URL discovery",
    hint: "site:linkedin.com/in name search. Feeds linkedin_profile.",
    examplePayload: '{\n  "name": "Alice Smith",\n  "company": "Acme Corp"\n}',
    group: "person",
  },
  {
    id: "wayback_linkedin",
    label: "Wayback — LinkedIn snapshot",
    hint: "archive.org snapshot of a LinkedIn URL (use when LinkedIn 429s).",
    examplePayload: '{\n  "profile_url": "https://www.linkedin.com/in/alice-smith"\n}',
    group: "person",
  },
  // Social handle
  {
    id: "linkedin_profile",
    label: "LinkedIn — public profile",
    hint: "Public-view fetch (no login). Needs profile URL.",
    examplePayload: '{\n  "profile_url": "https://www.linkedin.com/in/alice-smith"\n}',
    group: "social",
  },
  {
    id: "github_profile",
    label: "GitHub — public profile",
    hint: "GitHub REST v3. Free, 60 req/h unauth. Tech-host verification.",
    examplePayload: '{\n  "username": "octocat"\n}',
    group: "social",
  },
  {
    id: "twitter_public",
    label: "Twitter/X — public profile",
    hint: "Bio + counts + joined date via nitter mirror or x.com fallback.",
    examplePayload: '{\n  "handle": "username"\n}',
    group: "social",
  },
  {
    id: "twitter_followers",
    label: "Twitter/X — public followers (nitter)",
    hint: "Via nitter mirror. Private accounts blocked by design.",
    examplePayload: '{\n  "handle": "username",\n  "limit": 50\n}',
    group: "social",
  },
  {
    id: "twstalker",
    label: "TWStalker — Twitter mirror (public-view)",
    hint: "Third no-login Twitter surface; complements nitter + x.com.",
    examplePayload: '{\n  "handle": "username"\n}',
    group: "social",
  },
  {
    id: "instagram_public",
    label: "Instagram — public profile",
    hint: "Bio + counts + is_private flag. No login, no follower list.",
    examplePayload: '{\n  "handle": "username"\n}',
    group: "social",
  },
  {
    id: "instagram_followers",
    label: "Instagram — public followers (auth-walled)",
    hint: "Honest: auth wall. Surfaces error pointing at wayback_snapshot.",
    examplePayload: '{\n  "handle": "username"\n}',
    group: "social",
  },
  {
    id: "tiktok_public",
    label: "TikTok — public profile",
    hint: "Bio + follower/like/video counts. Public-view only.",
    examplePayload: '{\n  "handle": "username"\n}',
    group: "social",
  },
  {
    id: "tiktok_followers",
    label: "TikTok — public followers (auth-walled)",
    hint: "Honest: auth wall. Surfaces error pointing at wayback_snapshot.",
    examplePayload: '{\n  "handle": "username"\n}',
    group: "social",
  },
  {
    id: "github_followers",
    label: "GitHub — public followers",
    hint: "REST v3 follower-list (public, no login).",
    examplePayload: '{\n  "username": "octocat",\n  "limit": 100\n}',
    group: "social",
  },
  {
    id: "bluesky_followers",
    label: "Bluesky — public followers",
    hint: "AT Protocol getFollowers (no auth, public reads).",
    examplePayload: '{\n  "handle": "alice.bsky.social",\n  "limit": 100\n}',
    group: "social",
  },
  {
    id: "mastodon_followers",
    label: "Mastodon — public followers",
    hint: "Instance REST API. Handle format: user@instance.tld",
    examplePayload: '{\n  "acct": "alice@mastodon.social",\n  "limit": 80\n}',
    group: "social",
  },
  {
    id: "bluesky_post_likes",
    label: "Bluesky — who liked a post",
    hint: "AT Protocol getLikes. Post URL or at:// URI.",
    examplePayload:
      '{\n  "post_url": "https://bsky.app/profile/alice.bsky.social/post/abc123",\n  "limit": 100\n}',
    group: "social",
  },
  {
    id: "mastodon_post_likes",
    label: "Mastodon — who favourited a post",
    hint: "Instance REST API. Public favourites list.",
    examplePayload:
      '{\n  "post_url": "https://mastodon.social/@alice/112345678901234567",\n  "limit": 80\n}',
    group: "social",
  },
  // Image
  {
    id: "tineye_image",
    label: "TinEye — reverse image (exact)",
    hint: "Exact-match reverse image search. Pair with image_flip_check for flipped duplicates.",
    examplePayload: '{\n  "image_url": "https://example.com/photo.jpg"\n}',
    group: "image",
  },
  {
    id: "yandex_image_reverse",
    label: "Yandex — reverse image (catches flips)",
    hint: "Neural-feature matcher; best engine for flipped/cropped variants.",
    examplePayload: '{\n  "image_url": "https://example.com/photo.jpg"\n}',
    group: "image",
  },
  {
    id: "bing_visual_reverse",
    label: "Bing Visual Search — reverse image",
    hint: "Third triangulation engine; catches what Google misses.",
    examplePayload: '{\n  "image_url": "https://example.com/photo.jpg"\n}',
    group: "image",
  },
  {
    id: "reverse_image_aggregator",
    label: "Reverse image — ALL engines (meta)",
    hint: "Fan-out: TinEye + Yandex + Google Lens + Bing. Single call.",
    examplePayload: '{\n  "image_url": "https://example.com/photo.jpg"\n}',
    group: "image",
  },
  {
    id: "image_flip_check",
    label: "Image — generate flipped variant",
    hint: "Horizontally flip; feed result into exact-match engines.",
    examplePayload:
      '{\n  "image_url": "https://example.com/photo.jpg",\n  "output_format": "file"\n}',
    group: "image",
  },
  {
    id: "exiftool_full",
    label: "ExifTool — full metadata (gold standard)",
    hint: "~23,000 tags. Requires exiftool on PATH.",
    examplePayload: '{\n  "image_url": "https://example.com/photo.jpg"\n}',
    group: "image",
  },
  {
    id: "image_ela_check",
    label: "Image ELA — manipulation detector",
    hint: "Error Level Analysis. Flags retouched / clone-stamped regions.",
    examplePayload:
      '{\n  "image_url": "https://example.com/photo.jpg",\n  "quality": 90\n}',
    group: "image",
  },
  {
    id: "image_provenance_check",
    label: "Image provenance — composite (EXIF + ELA + C2PA)",
    hint: "One call, aggregated verdict (high-risk / elevated / low).",
    examplePayload: '{\n  "image_url": "https://example.com/photo.jpg"\n}',
    group: "image",
  },
  {
    id: "phash_dedupe",
    label: "pHash dedupe — multi-listing photo theft",
    hint: "Catches same photo reused across cases. Append-only local DB.",
    examplePayload:
      '{\n  "image_url": "https://example.com/photo.jpg",\n  "case_id": "case-2026-05-alice"\n}',
    group: "image",
  },
  {
    id: "seasonal_metadata_check",
    label: "Image seasonal check — EXIF date vs claim",
    hint: "Catches summer-photo-on-winter-listing fraud. Solar angle bonus.",
    examplePayload:
      '{\n  "image_url": "https://example.com/photo.jpg",\n  "claimed_season": "winter"\n}',
    group: "image",
  },
  {
    id: "ai_image_detection",
    label: "AI image detection (Sightengine)",
    hint: "GenAI-fabricated photo detector. Needs OSINT_SIGHTENGINE_* keys.",
    examplePayload: '{\n  "image_url": "https://example.com/photo.jpg"\n}',
    group: "image",
  },
  {
    id: "c2pa_verify",
    label: "C2PA — Content Credentials verify",
    hint: "Cryptographic provenance chain (2024+ Sony/Leica/Nikon).",
    examplePayload: '{\n  "image_url": "https://example.com/photo.jpg"\n}',
    group: "image",
  },
  {
    id: "wayback_snapshot",
    label: "Wayback — any-URL snapshot",
    hint: "Generalized archive.org availability check. Pre-wall follower pages.",
    examplePayload:
      '{\n  "url": "https://www.instagram.com/username/followers/"\n}',
    group: "image",
  },
  // Smoke / test
  {
    id: "echo",
    label: "echo — smoke",
    hint: "Trivial in-process adapter. Returns payload as tool-run-result.",
    examplePayload: '{\n  "hello": "world"\n}',
    group: "smoke",
  },
  {
    id: "m0_gate_stress",
    label: "m0_gate_stress — 32-event burst",
    hint: "M0 exit gate path. In-process; bypasses Dramatiq.",
    examplePayload: "{}",
    group: "smoke",
  },
];

export const ADAPTER_BY_ID: Record<string, AdapterMeta> = Object.fromEntries(
  ADAPTERS.map((a) => [a.id, a]),
);

// W1-W8 workflows per ADR-0017 §3. Rendered as the cmd-K palette's
// empty-state grid (cold start). Each workflow has a 2-letter prefix
// the §4 ranker uses + a brief "what it does" summary.
//
// Property-vetting note: the original W1-W8 were general-OSINT workflows
// from before the property-vetting pivot. The pivot adds property_vetting
// as an extra (W9) so the dossier surface includes the case the user
// actually runs every day.

export interface WorkflowMeta {
  readonly prefix: string;
  readonly name: string;
  readonly summary: string;
  readonly subject: string;
  readonly id: string; // for rank candidate parity
}

export const WORKFLOWS: ReadonlyArray<WorkflowMeta> = [
  {
    id: "w1.un",
    prefix: "un",
    name: "Username Dossier",
    summary: "Maigret + Sherlock + social fan-out across N platforms.",
    subject: "username",
  },
  {
    id: "w2.em",
    prefix: "em",
    name: "Email Lookup",
    summary: "MX validate → HIBP breaches → email-to-account pivot.",
    subject: "email",
  },
  {
    id: "w3.ph",
    prefix: "ph",
    name: "Phone Pivot",
    summary: "Carrier lookup → SMS-account pivot → social handle scan.",
    subject: "phone",
  },
  {
    id: "w4.im",
    prefix: "im",
    name: "Image OSINT",
    summary: "Reverse-image aggregator + EXIF + provenance + geo.",
    subject: "image",
  },
  {
    id: "w5.do",
    prefix: "do",
    name: "Domain + CT Timeline",
    summary: "Subfinder + Amass + CT log + Wayback CDX timeline.",
    subject: "domain",
  },
  {
    id: "w6.pe",
    prefix: "pe",
    name: "Person Background",
    summary: "TruePeopleSearch + LinkedIn + GitHub + breach surface.",
    subject: "person",
  },
  {
    id: "w7.fa",
    prefix: "fa",
    name: "Face Match",
    summary: "Reverse image + biometric gate. OPSEC red by default.",
    subject: "face",
  },
  {
    id: "w8.ge",
    prefix: "ge",
    name: "Event Geolocation",
    summary: "Image geo + KartaView + sun-angle. Time-pinned.",
    subject: "event",
  },
  {
    id: "w9.pv",
    prefix: "pv",
    name: "Property Vetting (pivot)",
    summary:
      "Nominatim → Inside Airbnb → reverse-image + EXIF on listing photos → host name cross-check.",
    subject: "property",
  },
];

/** Event name dispatched on `window` when the palette selects an adapter.
 *  RunToolForm listens for this and updates its own adapter selection. */
export const ADAPTER_SELECT_EVENT = "osint:adapter-select";

export interface AdapterSelectDetail {
  readonly adapterId: string;
}

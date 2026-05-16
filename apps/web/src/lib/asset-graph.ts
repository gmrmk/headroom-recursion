// Asset graph + confidence upgrade rules.
//
// Adopted 2026-05-12 from osint-methodology §2.1 ("confidence upgrade
// workflows"). Phase 1 doctrine (Margaret roadmap, Tomás ownership).
//
// PURPOSE
//   Every discovery during an investigation is a typed asset, not a free-
//   floating string. Each asset has a documented path from TENTATIVE
//   (plausible) -> FIRM (directly observed) -> CONFIRMED (independently
//   corroborated). This module codifies the per-type rules so we don't
//   re-invent them per adapter.
//
// PROPERTY-VETTING ASSET TAXONOMY
//   The skill's §8.1 lists 29 asset types tuned for external red-team
//   recon. We're narrower: property-vetting reduces to ~10 types.
//
// USAGE
//   import { assessConfidence } from "@/lib/asset-graph";
//   const conf = assessConfidence({ type: "domain", value: "acme.com",
//                                    sources: ["crt.sh","dns"] });
//   // -> "firm"

export type AssetType =
  | "domain"      // host's claimed business domain
  | "subdomain"   // discovered subdomain (resolves or has been seen)
  | "ip"          // IP address (host's, or one referenced by host)
  | "email"       // email address (host's, or one tied to host)
  | "phone"       // phone number
  | "username"    // social/account handle
  | "address"     // physical mailing address
  | "person"      // named individual (the host)
  | "photo"       // listing photo (image URL)
  | "tenant"      // SaaS tenant (M365, Workspace, Okta, etc.)
  | "ai_artifact";  // content (text, image, copy block) flagged AI-suspected

export type Confidence = "tentative" | "firm" | "confirmed";

export interface Asset {
  readonly type: AssetType;
  readonly value: string;
  /**
   * Every source that confirmed this asset. Free-form strings; consumers
   * MAY interpret known names (e.g. "crt.sh", "dns", "wayback", "gravatar",
   * "hibp", "hudson_rock", "github_commit", "user_scanner", "intelbase").
   */
  readonly sources: ReadonlyArray<string>;
  /** Per-type observation flags (see UPGRADE_RULES table). */
  readonly observations?: Readonly<Record<string, boolean>>;
}

// ---------------------------------------------------------------------------
// Per-type upgrade rules (methodology §2.1)
// ---------------------------------------------------------------------------
//
// firm_predicate    : when does this asset escalate from TENTATIVE to FIRM?
// confirmed_predicate: when does it escalate from FIRM to CONFIRMED?
//
// Predicate signature is `(asset) -> boolean`. Predicates run in order
// (firm then confirmed); the result is the highest level whose predicate
// returns true.

interface UpgradeRule {
  readonly firm: (a: Asset) => boolean;
  readonly confirmed: (a: Asset) => boolean;
}

const hasObs = (a: Asset, key: string): boolean => a.observations?.[key] === true;
const sourcesAtLeast = (a: Asset, n: number): boolean => a.sources.length >= n;
const hasSource = (a: Asset, name: string): boolean =>
  a.sources.some((s) => s.toLowerCase() === name.toLowerCase());

const UPGRADE_RULES: Readonly<Record<AssetType, UpgradeRule>> = {
  // Subdomain: returned by >=2 passive sources OR DNS resolves -> FIRM.
  // Serves on standard port AND banner/cert returned -> CONFIRMED.
  subdomain: {
    firm: (a) => sourcesAtLeast(a, 2) || hasObs(a, "dns_resolves"),
    confirmed: (a) => hasObs(a, "http_banner") || hasObs(a, "tls_cert"),
  },

  // Domain: registered + WHOIS returned -> FIRM. Cert + DNS + WHOIS all
  // agree -> CONFIRMED.
  domain: {
    firm: (a) => hasObs(a, "whois_returned") || hasObs(a, "dns_resolves"),
    confirmed: (a) => sourcesAtLeast(a, 3),
  },

  // IP: returned by >=2 sources (passive DNS, ASN, Shodan) -> FIRM. Active
  // probe responds (SYN-ACK or ICMP echo) -> CONFIRMED.
  ip: {
    firm: (a) => sourcesAtLeast(a, 2),
    confirmed: (a) => hasObs(a, "active_probe_ok"),
  },

  // Email: generated from name pattern OR snippet-only dork hit -> TENTATIVE.
  // Listed in Hunter/EmailRep/IntelX/breach corpus -> FIRM.
  // SMTP MAIL FROM/RCPT TO returns 250 (without sending) -> CONFIRMED.
  email: {
    firm: (a) =>
      hasObs(a, "in_breach_corpus") ||
      hasObs(a, "in_hunter") ||
      hasObs(a, "in_emailrep") ||
      hasSource(a, "intelbase") ||
      hasSource(a, "hibp"),
    confirmed: (a) => hasObs(a, "smtp_rcpt_ok") || hasObs(a, "gravatar_verified"),
  },

  // Phone: format-validated -> FIRM. Carrier lookup or live verification
  // -> CONFIRMED.
  phone: {
    firm: (a) => hasObs(a, "format_valid"),
    confirmed: (a) => hasObs(a, "carrier_known") || hasObs(a, "live_verified"),
  },

  // Username: present on >=1 platform (snscrape, github, etc.) -> FIRM.
  // Same handle across >=3 platforms with consistent profile data
  // -> CONFIRMED.
  username: {
    firm: (a) => sourcesAtLeast(a, 1),
    confirmed: (a) => sourcesAtLeast(a, 3),
  },

  // Address: geocoded successfully -> FIRM. Geocode + reverse-geocode
  // agree AND nearby OSM features confirm context -> CONFIRMED.
  address: {
    firm: (a) => hasObs(a, "geocoded"),
    confirmed: (a) => hasObs(a, "osm_context_match"),
  },

  // Person: name from single source -> TENTATIVE. Confirmed by 2nd source
  // (Hunter + LinkedIn, or 2 breach sources w/ same email) -> FIRM.
  // Match in multiple person-search systems with consistent details
  // -> CONFIRMED.
  person: {
    firm: (a) => sourcesAtLeast(a, 2),
    confirmed: (a) => sourcesAtLeast(a, 3),
  },

  // Photo: URL exists + HTTP 200 -> FIRM. Multiple metadata sources
  // (EXIF + reverse-image + AI-detect) all returned -> CONFIRMED.
  photo: {
    firm: (a) => hasObs(a, "fetch_ok"),
    confirmed: (a) =>
      hasObs(a, "exif_parsed") &&
      (hasObs(a, "reverse_image_returned") || hasObs(a, "ai_detect_returned")),
  },

  // Tenant: discovery endpoint returned OIDC metadata -> FIRM. Tenant
  // GUID extracted AND domain resolves through tenant's expected
  // MX/autodiscover/SP record -> CONFIRMED.
  tenant: {
    firm: (a) => hasObs(a, "oidc_metadata_returned") || hasObs(a, "autodiscover_resolved"),
    confirmed: (a) =>
      hasObs(a, "tenant_id_extracted") &&
      (hasObs(a, "mx_matches") || hasObs(a, "autodiscover_in_ms_range")),
  },

  // AI artifact: a detector ran AND (C2PA Content Credentials absent OR
  // a single detector returned positive) -> FIRM. Multi-detector
  // concurrence (or C2PA-absent + single-detector-positive + human-
  // confirmed) -> CONFIRMED. Anchors Camille's wave-4 Provenance schema
  // (camille-wave4.md §AI-content) at the asset-graph layer; rubric
  // anchors live at PV_AI_CONTENT_SUSPECTED / PV_AI_CONTENT_MULTI_DETECTOR.
  ai_artifact: {
    firm: (a) => hasObs(a, "detector_ran") &&
      (hasObs(a, "c2pa_absent") || hasObs(a, "single_detector_positive")),
    confirmed: (a) =>
      hasObs(a, "multi_detector_concurrence") ||
      (hasObs(a, "c2pa_absent") && hasObs(a, "single_detector_positive") && hasObs(a, "human_confirmed")),
  },
};

/**
 * Assess an asset's confidence level by running the per-type upgrade
 * predicates. Returns the highest level whose predicate passes.
 *
 * Default: TENTATIVE. If neither firm nor confirmed predicates pass, the
 * asset has been observed but not corroborated.
 */
export function assessConfidence(asset: Asset): Confidence {
  const rule = UPGRADE_RULES[asset.type];
  if (rule.confirmed(asset)) return "confirmed";
  if (rule.firm(asset)) return "firm";
  return "tentative";
}

/**
 * Visible label per confidence level. Renders in dossier UI when the
 * investigator hovers an asset chip; also used in dossier exports.
 */
export const CONFIDENCE_LABEL: Readonly<Record<Confidence, string>> = {
  tentative: "tentative (1 weak source)",
  firm: "firm (directly observed)",
  confirmed: "confirmed (independently corroborated)",
};

/**
 * Color hint per confidence level. Mirrors severity-rubric tier colors
 * but applied to confidence semantics. Consumers decide whether to use.
 */
export const CONFIDENCE_COLOR: Readonly<Record<Confidence, string>> = {
  tentative: "var(--color-text-muted)",
  firm: "var(--color-text-secondary)",
  confirmed: "var(--color-text-primary)",
};

/**
 * Diagnostic helper -- describes which observations would be needed to
 * upgrade an asset to the next level. Used by the dossier UI's "how would
 * I confirm this?" tooltip (Phase 5 work; available now as a hook).
 */
export function nextUpgradeHint(asset: Asset): string | null {
  const current = assessConfidence(asset);
  if (current === "confirmed") return null;
  switch (asset.type) {
    case "subdomain":
      return current === "tentative"
        ? "Resolve DNS or find a second passive source."
        : "Connect on standard port + capture HTTP banner or TLS cert.";
    case "domain":
      return current === "tentative"
        ? "Run WHOIS or resolve DNS."
        : "Add a third independent source (cert + WHOIS + DNS).";
    case "ip":
      return current === "tentative"
        ? "Find a second passive DNS / ASN / Shodan source."
        : "Run an authorized active probe (SYN-ACK or ICMP).";
    case "email":
      return current === "tentative"
        ? "Look up in Hunter / EmailRep / breach corpus."
        : "Probe SMTP RCPT TO (no DATA) or verify via Gravatar.";
    case "phone":
      return current === "tentative"
        ? "Validate format (libphonenumber)."
        : "Run carrier lookup or live verification.";
    case "username":
      return current === "tentative"
        ? "Find the handle on at least one platform."
        : "Find the same handle on >=3 platforms with consistent profile data.";
    case "address":
      return current === "tentative"
        ? "Geocode it (Nominatim)."
        : "Reverse-geocode and confirm nearby OSM features match.";
    case "person":
      return current === "tentative"
        ? "Find one corroborating source (Hunter + LinkedIn)."
        : "Find a third source agreeing on identifying details.";
    case "photo":
      return current === "tentative"
        ? "Fetch the URL successfully (HTTP 200)."
        : "Parse EXIF AND run reverse-image-search or AI-detect.";
    case "tenant":
      return current === "tentative"
        ? "Resolve OIDC discovery endpoint or autodiscover."
        : "Extract tenant GUID and confirm MX or autodiscover lands in vendor IP range.";
    case "ai_artifact":
      return current === "tentative"
        ? "Run a second AI-content detector against this asset, OR check for C2PA Content Credentials."
        : "Run a second independent detector; multi-detector concurrence upgrades to CONFIRMED.";
    default:
      return null;
  }
}

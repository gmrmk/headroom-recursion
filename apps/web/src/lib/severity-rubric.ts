// Severity rubric — the single source of truth for "what does CRITICAL /
// HIGH / MEDIUM / LOW / INFO mean and which matrix row anchors a given
// finding's severity claim."
//
// Adopted 2026-05-12 from the /osint skills:
//   - osint-methodology §9          findings rubric & severity mapping
//   - offensive-osint §40           severity decision matrix (worked examples)
//
// Phase 1 doctrine work (Margaret roadmap, Iris-IA ownership).
//
// USAGE
//   import { ImpactTier, RUBRIC, severityBasisRef } from "@/lib/severity-rubric";
//   const basis = severityBasisRef("OPEN_FIREBASE_RTDB");
//   // basis === "matrix:OPEN_FIREBASE_RTDB"  -> Finding.severity_basis
//
// Every emit-site that claims a severity SHOULD attach `severity_basis`
// citing the matrix entry that grounds the claim. Findings without
// `severity_basis` are visible but flagged as un-grounded in code review.
//
// Property-vetting note: this rubric uses red-team / external-attack-
// surface anchors directly. For property-vetting we additionally scale
// the BREACH_* entries down per Margaret roadmap Q4 — see BREACH_HOST
// entries below.

/** Impact tier — independent of the visual `Severity` color hint in
 *  dossier-shape.ts. A finding may carry both. */
export type ImpactTier = "info" | "low" | "medium" | "high" | "critical";

export interface RubricEntry {
  /** Stable id, used in `severity_basis: "matrix:<id>"` citations. */
  readonly id: string;
  /** Impact tier this matrix row anchors. */
  readonly tier: ImpactTier;
  /** One-line technical description matching the matrix row. */
  readonly technical: string;
  /** Plain-language business-impact translation (methodology §31.3). */
  readonly business_impact: string;
  /** Source skill section anchor for traceability. */
  readonly source: string;
}

// ---------------------------------------------------------------------------
// The matrix entries. Add new rows here when new finding types land.
// Property-vetting-specific entries marked with "PV:" prefix in source field.
// ---------------------------------------------------------------------------

export const RUBRIC: Readonly<Record<string, RubricEntry>> = {
  // ------- CRITICAL anchors -------

  OPEN_FIREBASE_RTDB: {
    id: "OPEN_FIREBASE_RTDB",
    tier: "critical",
    technical: "Firebase Realtime Database accessible without auth",
    business_impact:
      "Mobile app's backend database is publicly readable. All user data, possibly writable. If exploited: full data breach + potential service disruption.",
    source: "offensive-osint §40",
  },
  LISTABLE_CLOUD_BUCKET_PII: {
    id: "LISTABLE_CLOUD_BUCKET_PII",
    tier: "critical",
    technical: "Listable cloud bucket containing PII",
    business_impact:
      "Customer records publicly downloadable. Potential GDPR/CCPA notification trigger if accessed. Notification + credit monitoring + legal review cost.",
    source: "offensive-osint §40",
  },
  EXPOSED_ENV_FILE: {
    id: "EXPOSED_ENV_FILE",
    tier: "critical",
    technical: ".env file reachable on production webapp",
    business_impact:
      "Database access to all customer data. Pivots to backups, billing, employee PII. Full data-breach scope if exploited.",
    source: "offensive-osint §40",
  },
  EXPOSED_GIT_CONFIG: {
    id: "EXPOSED_GIT_CONFIG",
    tier: "critical",
    technical: ".git/config reachable on production webapp",
    business_impact:
      "Full source code disclosure with secret history reconstructable. Attacker has every credential ever committed.",
    source: "offensive-osint §40",
  },
  LIVE_CLOUD_ADMIN_KEY: {
    id: "LIVE_CLOUD_ADMIN_KEY",
    tier: "critical",
    technical: "Live-validated cloud admin access key found in the wild",
    business_impact:
      "Full cloud account compromise. Attacker can exfiltrate all data, spin up cryptominers, lateral-move. Six-figure cloud bill + complete environment rebuild on confirmed exploit.",
    source: "offensive-osint §40 + §23",
  },
  SSO_EXPOSURE: {
    id: "SSO_EXPOSURE",
    tier: "critical",
    technical: "Identified IdP tenant intersects breach corpus",
    business_impact:
      "Stolen credentials for staff are circulating; attackers can attempt these against SSO. Password reuse means other services are also at risk.",
    source: "osint-methodology §22.3",
  },

  // ------- HIGH anchors -------

  EXPOSED_SOURCEMAP: {
    id: "EXPOSED_SOURCEMAP",
    tier: "high",
    technical: "Production sourcemap (.js.map) accessible",
    business_impact:
      "Frontend source disclosure. Grep'able for inline secrets and internal hostnames; full attack-surface map handed to attackers.",
    source: "offensive-osint §40",
  },
  OPEN_GRAPHQL_INTROSPECTION: {
    id: "OPEN_GRAPHQL_INTROSPECTION",
    tier: "high",
    technical: "GraphQL introspection enabled on production",
    business_impact:
      "API attack surface fully mapped by attackers. Enables precise follow-on attacks; reconnaissance is now zero-effort.",
    source: "offensive-osint §40",
  },
  SUBDOMAIN_TAKEOVER: {
    id: "SUBDOMAIN_TAKEOVER",
    tier: "high",
    technical: "Subdomain CNAME points to unclaimed third-party resource",
    business_impact:
      "Attackers can host content under your trusted domain. Phishing from this domain bypasses brand-impersonation defenses; users will trust it.",
    source: "offensive-osint §40",
  },
  PV_LISTING_PHOTO_PDQ_CROSS_PLATFORM: {
    id: "PV_LISTING_PHOTO_PDQ_CROSS_PLATFORM",
    tier: "high",
    technical:
      "Listing photo PDQ hash matches a photo hash previously indexed under a different host_id OR a different platform (Airbnb/Vrbo/Booking)",
    business_impact:
      "The same photo appears under multiple host identities or on multiple lodging platforms. Could be legitimate cross-listing by one operator OR account-rotation fraud (Land Lordz / Goel-indictment TTP). The cross-reference itself does not decide; investigator must compare the matched listings before extending trust.",
    source:
      "PV: Feynman wave-4 Thread 1.3 (Steinebach PHASER + Madden robustness eval) + Goel/Raheja indictment TTP table",
  },
  DMARC_PERMISSIVE: {
    id: "DMARC_PERMISSIVE",
    tier: "high",
    technical: "DMARC policy p=none on production sending domain",
    business_impact:
      "Anyone on the internet can send email appearing to be from your domain. Phishing becomes trivially convincing for customers and employees.",
    source: "offensive-osint §40 + §16.14",
  },
  VENDOR_PRODUCT_KEV: {
    id: "VENDOR_PRODUCT_KEV",
    tier: "high",
    technical: "Vendor product banner discloses unpatched CVE on CISA KEV list",
    business_impact:
      "Network appliance has a known-exploited vulnerability. Attackers actively scan the internet for this exact issue. Immediate patch required.",
    source: "offensive-osint §40 + §16.16",
  },

  // ------- MEDIUM anchors -------

  MISSING_SECURITY_HEADER: {
    id: "MISSING_SECURITY_HEADER",
    tier: "medium",
    technical: "Production webapp missing HSTS / CSP",
    business_impact:
      "Hardening gap. CSP gone means XSS impact escalates; HSTS missing on standard pages is a configuration miss.",
    source: "offensive-osint §40 + §16.4",
  },
  INTERNAL_HOST_LEAK: {
    id: "INTERNAL_HOST_LEAK",
    tier: "medium",
    technical: "Internal IP / K8s service DNS leaked in client-side JS",
    business_impact:
      "Internal topology disclosed. Attackers planning lateral movement get a free network map.",
    source: "offensive-osint §40 + §16.11",
  },
  TLS_WEAK_PROTOCOL: {
    id: "TLS_WEAK_PROTOCOL",
    tier: "medium",
    technical: "TLS 1.0 / 1.1 supported on production",
    business_impact:
      "Compliance gap (PCI-DSS forbids TLS 1.0). Customers on hostile networks have weaker protection than they should.",
    source: "offensive-osint §40 + §28.4",
  },
  PV_AI_CONTENT_MULTI_DETECTOR: {
    id: "PV_AI_CONTENT_MULTI_DETECTOR",
    tier: "medium",
    technical:
      "Two distinct AI-content detectors agree on the same asset (e.g., wavelet-ResNet50 + Fourier-ResNet50; OR GPTZero + DetectGPT)",
    business_impact:
      "Multi-detector concurrence is the lowest reliable AI-content signal in 2026. Treat as MEDIUM — actionable for re-verification; not sufficient for verdict without independent corroboration of the underlying claim.",
    source:
      "PV: Camille wave-4 §three-signal-rules + arXiv:2510.21822 + arXiv:2510.19840 + arXiv:2511.19499",
  },
  PV_OPSEC_UA_SELF_IDENTIFICATION: {
    id: "PV_OPSEC_UA_SELF_IDENTIFICATION",
    tier: "medium",
    technical:
      "Adapter run sent a tool-identifying User-Agent string (e.g., `osint-goblin/0.1 (...)`) directly to the target's own webserver",
    business_impact:
      "The target webserver's access log records that an OSINT tool, identifying itself by name, probed it from the operator's IP within minutes of the listing being read. A fraudulent host reviewing their own logs can attribute the investigation back to the operator. Operator chooses transparency vs. covert posture per the opsec runbook.",
    source:
      "PV: Camille wave-4 §network-fingerprint-posture-review + osint-methodology opsec runbook §9",
  },
  PV_ENTITY_FINGERPRINT_MATCH: {
    id: "PV_ENTITY_FINGERPRINT_MATCH",
    tier: "medium",
    technical:
      "Two or more distinct evidence sources cite the same canonicalized entity (address, person, LLC, domain, email, or phone)",
    business_impact:
      "Multi-source corroboration of an entity raises confidence in its existence and the operator's ability to identify it. Pre-correlates evidence by real-world object so the investigator reads the dossier by entity, not by data-source. Escalates when ≥3 sources concur OR when a person and an LLC share the same address (potential nominee / shell-company signal).",
    source:
      "PV: Hideo wave-4 keystone (claims-by-entity drift call, hideo-wave4.md:222-227) + Iris wave-4 §C.5 (entity-fingerprint primitive ratification, iris-deliberation-wave4.md:304-360)",
  },

  // ------- LOW / INFO anchors -------

  MISSING_HARDENING_HEADER: {
    id: "MISSING_HARDENING_HEADER",
    tier: "low",
    technical: "Missing X-Frame-Options / X-Content-Type-Options",
    business_impact:
      "Minor browser-hardening gap. Marginal real-world exposure.",
    source: "offensive-osint §40 + §16.4",
  },
  PV_AI_CONTENT_SUSPECTED: {
    id: "PV_AI_CONTENT_SUSPECTED",
    tier: "low",
    technical:
      "AI-generated content suspected (single detector p>0.7 OR C2PA Content Credentials absent on professional-grade asset)",
    business_impact:
      "Asset (listing copy, profile photo, review text) may be AI-generated. SOFT signal only — false-positive rates on non-native English exceed 10% per GPTZero's own TOEFL data; spectral image detectors degrade cross-generator. Surface as suspicion; never as proof; never silently suppress evidence flagged AI-suspected.",
    source:
      "PV: Camille wave-4 §AI-content-defense + Feynman wave-4 Thread 2.3 (GPTZero / MAiDE-up / wavelet+Fourier ResNet50)",
  },
  SAAS_TENANT_DISCOVERED: {
    id: "SAAS_TENANT_DISCOVERED",
    tier: "info",
    technical: "SaaS tenancy fingerprinted (e.g., M365 / Workspace / Atlassian)",
    business_impact:
      "Tenant identity known. By itself this is informational; escalates to CRITICAL only when intersected with a breach corpus (SSO_EXPOSURE pattern).",
    source: "osint-methodology §11 + §22.3 (Naomi ceiling 2026-05-12)",
  },
  BREACH_CONTEXT_ONLY: {
    id: "BREACH_CONTEXT_ONLY",
    tier: "info",
    technical: "Domain seen in breach corpus with 0 named accounts",
    business_impact:
      "Contextual signal only. Worth noting in case future accounts surface; not actionable on its own.",
    source: "offensive-osint §15.1",
  },

  // ------- Property-vetting scaled breach thresholds (Margaret Q4) -------
  //
  // Skill default (offensive §15.1): ≥10 employees → CRITICAL.
  // Property-vetting context: one compromised host IS the verdict.
  // Per Margaret roadmap 2026-05-12, scale thresholds down.

  PV_BREACH_HOST_SINGLE: {
    id: "PV_BREACH_HOST_SINGLE",
    tier: "high",
    technical: "Host's email present in 1 breach corpus",
    business_impact:
      "Host's identity has been compromised at least once. Stolen credentials may be re-used to access the listing platform; treat the listing's claims with skepticism.",
    source: "PV-scaled: offensive §15.1 (≥1 = HIGH, was ≥1-9)",
  },
  PV_BREACH_HOST_REPEATED: {
    id: "PV_BREACH_HOST_REPEATED",
    tier: "critical",
    technical: "Host's email present in 3+ breach corpora OR 1 hit + active SSO tenancy",
    business_impact:
      "Host has repeated credential compromise OR breach intersects an active corporate identity. High likelihood the listing is operated by a compromised or impersonating account.",
    source: "PV-scaled: offensive §15.2 SSO_EXPOSURE (Margaret 2026-05-12)",
  },
  PV_BREACH_END_USER: {
    id: "PV_BREACH_END_USER",
    tier: "medium",
    technical: "End-user (non-host) breach hit at the platform domain",
    business_impact:
      "Adjacent breach signal. Lower priority than direct host hit but still worth recording.",
    source: "PV-scaled: offensive §15.1 end-user tier",
  },

  // ------- Phase 5 dork-sweep anchors -------
  //
  // Per osint-methodology §2.1: snippet-only dork hits land TENTATIVE.
  // Investigator must visit the URL in-tab to upgrade to FIRM. We model
  // that as an INFO tier finding here -- not actionable on its own;
  // surfaces in the dossier so the investigator can click through.

  DORK_HIT_SNIPPET: {
    id: "DORK_HIT_SNIPPET",
    tier: "info",
    technical: "Open-web search engine hit (snippet only, unvisited)",
    business_impact:
      "Possible mention of the host on the public web. The snippet match is suggestive but not confirmed; the investigator should open the link to assess whether the page actually concerns this person.",
    source: "osint-methodology §2.1 + offensive §18 (Phase 5)",
  },

  // Same URL surfaced independently by >=2 engines (DDG + Bing, etc.).
  // Cross-engine corroboration raises the prior on relevance without
  // upgrading to FIRM (investigator still must visit). Tier kept low
  // so corroborated hits don't crowd out higher-tier section findings.
  DORK_HIT_CORROBORATED: {
    id: "DORK_HIT_CORROBORATED",
    tier: "low",
    technical:
      "Open-web hit corroborated across two or more engines (e.g. DDG + Bing both surface the same URL)",
    business_impact:
      "Two independent search indexes agree the URL exists and matched the query; reduces the chance of a one-engine false-positive. Investigator should prioritize visiting corroborated hits over single-engine ones.",
    source: "osint-methodology §2 (rule-of-three for attribution) + §24.3 multi-engine corpus",
  },

  // ------- Ship 10 / W20.tr -- review owner-mention drift -------
  //
  // Universal PV signal across every travel-platform scrape (Airbnb,
  // VRBO, Booking, TripAdvisor, Yanolja, Leboncoin, +). Compares the
  // host name in listing metadata against names guests use in reviews.
  //
  // GOOD: reviews repeatedly name the listed host (identity confirmed).
  // WARN: reviews mention OTHER capitalized names alongside the host
  //       OR family-relation phrases ("Jolie's mother runs it") --
  //       investigator should review for undisclosed cohost / operator.
  // BAD:  reviews use explicit ownership phrasing ("Bob's house",
  //       "owner Bob", "Mike owns") for a name that is NOT the host --
  //       strong impersonation / undisclosed-owner / relisting signal.
  // INFO: no reviews to scan, or no host name in listing metadata.

  LISTING_OWNER_DRIFT_GOOD: {
    id: "LISTING_OWNER_DRIFT_GOOD",
    tier: "info",
    technical:
      "Reviews mention the listed host name without any other personal names; identity claim is consistent with guest accounts",
    business_impact:
      "Multiple past guests refer to the host by the name on the listing. No drift detected; the host's claimed identity is consistent with what guests experienced.",
    source: "user directive 2026-05-15 + osint-methodology §2 confidence-upgrade-by-corroboration",
  },

  LISTING_OWNER_DRIFT_WARN: {
    id: "LISTING_OWNER_DRIFT_WARN",
    tier: "warn",
    technical:
      "Reviews mention the listed host name AND other names (possible cohost/family operator) OR contain family-relation phrasing such as `<Host>'s mother runs it`",
    business_impact:
      "Past guests mention people besides the listed host (cohosts, family members, or maintenance staff). Could be legitimate operational disclosure or could indicate undisclosed party-of-interest. Investigator should read affected reviews directly.",
    source: "user directive 2026-05-15 + offensive-osint §41 (related-party enumeration)",
  },

  LISTING_OWNER_DRIFT_BAD: {
    id: "LISTING_OWNER_DRIFT_BAD",
    tier: "bad",
    technical:
      "Reviews use explicit ownership phrasing (`Bob's house`, `owner Bob`, `Mike owns`) for a name that is NOT the listed host",
    business_impact:
      "Past guests attribute property ownership or hosting to a person whose name doesn't match the listing. Strong indicator of impersonation, undisclosed real owner, recent listing transfer, or co-listed property where the displayed host isn't the operator. High-priority signal for property-vetting.",
    source: "user directive 2026-05-15 (load-bearing PV signal)",
  },

  LISTING_OWNER_DRIFT_INFO: {
    id: "LISTING_OWNER_DRIFT_INFO",
    tier: "info",
    technical:
      "Owner-mention scan ran but had no actionable signal -- either no reviews available or no host name in listing metadata to compare against",
    business_impact:
      "Insufficient data to assess owner-mention drift. Either the platform didn't surface review text in its schema.org payload, or the host name field was empty. No conclusion either way.",
    source: "user directive 2026-05-15",
  },

  // ------- W4-TIMELINE (Sprint-5 wave-4 keystone) -------
  //
  // Severity anchor for the forensic-timeline temporal-cluster surface.
  // Cluster Findings are a NAVIGATION primitive: they reveal that N
  // dated events compressed into a 14-day window. The cluster itself
  // does not decide; downstream evidence (W4-CLAIMS-BY-ENTITY or the
  // breach corpus) does. Tier kept at "info" so the cluster surfaces
  // without inflating verdict-card severity until corroborated.

  UX_FORENSIC: {
    id: "UX_FORENSIC",
    tier: "info",
    technical: "Forensic-timeline temporal-cluster surface",
    business_impact:
      "Temporal clustering of dated events reveals coordinated patterns (rapid review accrual, account-creation bursts) that scatter across sections in a flat dossier. The cluster Finding is a navigation primitive; severity escalates only when downstream evidence (W4-CLAIMS-BY-ENTITY or breach corpus) corroborates.",
    source: "PV: Feynman wave-4 Thread 3 §timeline + Iris wave-4 §C ratification (non-empty day-one)",
  },
};

/** Compose a `severity_basis` citation string. Stable across emits. */
export function severityBasisRef(rubricId: keyof typeof RUBRIC | string): string {
  return `matrix:${rubricId}`;
}

/** Look up the impact tier anchored by a `severity_basis` citation. */
export function tierFromBasis(severityBasis: string | undefined): ImpactTier | undefined {
  if (!severityBasis || !severityBasis.startsWith("matrix:")) return undefined;
  const id = severityBasis.slice("matrix:".length);
  return RUBRIC[id]?.tier;
}

/** Look up the business-impact phrase anchored by a `severity_basis` citation. */
export function businessImpactFromBasis(
  severityBasis: string | undefined,
): string | undefined {
  if (!severityBasis || !severityBasis.startsWith("matrix:")) return undefined;
  const id = severityBasis.slice("matrix:".length);
  return RUBRIC[id]?.business_impact;
}

/** All rubric entries — useful for admin / debug surfaces. */
export function allRubricEntries(): ReadonlyArray<RubricEntry> {
  return Object.values(RUBRIC);
}

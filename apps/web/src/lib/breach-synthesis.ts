// Breach severity synthesizer — Phase 3 doctrine, 2026-05-12.
//
// Adopted from osint-methodology §22.2 + offensive-osint §15.1, then
// PROPERTY-VETTING-SCALED per Margaret's roadmap Q4 answer:
//
//   ≥1 host-account hit          -> HIGH (PV_BREACH_HOST_SINGLE)
//   ≥3 host-account hits         -> CRITICAL (PV_BREACH_HOST_REPEATED)
//   1+ hit + active SSO tenancy  -> CRITICAL (PV_BREACH_HOST_REPEATED)
//   End-user (non-host) hits     -> MEDIUM (PV_BREACH_END_USER)  [future]
//
// Where "host-account hit count" = count of DISTINCT breach corpora that
// returned a hit for the host's email (not count of breach records --
// HIBP returning 5 records on the same domain is one corpus presence,
// not five).
//
// Naomi reminder (logless contract): counts surface in the dossier, but
// named accounts only live in the in-tab dossier; nothing persists to
// disk. The synthesizer reads from the existing event stream; no new
// storage layer introduced.

import type { ImpactTier } from "@/lib/severity-rubric";
import { severityBasisRef } from "@/lib/severity-rubric";
import type { InvestigationEvent } from "@/types/api";

/** Result of the breach synthesis. null = no breach signal at all. */
export interface BreachVerdict {
  readonly tier: ImpactTier;
  /** Number of distinct breach corpora with at least one hit. */
  readonly host_corpus_count: number;
  /** Raw event count -- shown for transparency. */
  readonly raw_record_count: number;
  /** Per-source breakdown. */
  readonly by_source: Readonly<Record<string, number>>;
  /** True if a tenant-match event was emitted on the same domain. */
  readonly has_active_sso_tenant: boolean;
  /** Rubric id for `Finding.severity_basis`. */
  readonly rubric_id: string;
  /** Headline phrase ready for dossier rendering. */
  readonly headline: string;
  /** One-line explanation of the tier decision. */
  readonly detail: string;
}

/** Sources we recognize as breach corpora. Free-form strings from the
 *  adapter event payload's `source` field. The legacy HIBP "domain-shape"
 *  events have no source field; we detect them by event_type+domain. */
const BREACH_SOURCE_KNOWN = new Set<string>([
  "hudson_rock",
  "intelbase",
  "dehashed",
  "leakcheck",
  "snusbase",
]);

interface SourceCounts {
  readonly raw: number;
  readonly hudson_rock: number;
  readonly hibp_domain: number;
  readonly intelbase: number;
  readonly other_named: number;
}

function countBreachSources(
  events: ReadonlyArray<InvestigationEvent>,
): { counts: SourceCounts; by_source: Record<string, number> } {
  let raw = 0;
  let hudson_rock = 0;
  let hibp_domain = 0;
  let intelbase = 0;
  let other_named = 0;
  const by_source: Record<string, number> = {};

  for (const e of events) {
    if (e.event_type !== "breach-hit") continue;
    raw += 1;
    const src = (e.payload as Record<string, unknown>)?.source;
    if (typeof src === "string" && src) {
      if (src === "hudson_rock") hudson_rock += 1;
      else if (src === "intelbase") intelbase += 1;
      else if (BREACH_SOURCE_KNOWN.has(src)) other_named += 1;
      by_source[src] = (by_source[src] ?? 0) + 1;
    } else {
      // Legacy HIBP shape: no source field, but has `domain` field.
      const domain = (e.payload as Record<string, unknown>)?.domain;
      if (typeof domain === "string" && domain) {
        hibp_domain += 1;
        by_source["hibp"] = (by_source["hibp"] ?? 0) + 1;
      }
    }
  }

  return {
    counts: { raw, hudson_rock, hibp_domain, intelbase, other_named },
    by_source,
  };
}

/** Did W12.id (or any other adapter) surface an SSO tenancy for the host? */
function hasActiveSsoTenant(events: ReadonlyArray<InvestigationEvent>): boolean {
  return events.some(
    (e) =>
      e.event_type === "sso-discovery" ||
      (e.event_type === "tenant-match" &&
        // M365 / Workspace / Okta / etc. -- any product fingerprint counts
        typeof (e.payload as Record<string, unknown>)?.product === "string"),
  );
}

/**
 * Synthesize a breach severity verdict over the event stream.
 *
 * Returns null when there is NO breach signal at all (no breach-hit
 * events). When breach hits exist, returns the matched PV-scaled tier.
 *
 * The caller (dossier-shape's projectCompromise) prepends a Finding
 * grounded in `verdict.rubric_id` so the verdict shows above the
 * per-source breakdowns.
 */
export function synthesizeBreachVerdict(
  events: ReadonlyArray<InvestigationEvent>,
): BreachVerdict | null {
  const { counts, by_source } = countBreachSources(events);
  if (counts.raw === 0) return null;

  // Count distinct corpora (sources) with >=1 hit. The legacy HIBP
  // domain-shape (no source field) counts as one corpus.
  const corpus_count = Object.keys(by_source).length;
  const has_sso = hasActiveSsoTenant(events);

  // Apply PV-scaled thresholds (Margaret Q4 answer 2026-05-12).
  let tier: ImpactTier;
  let rubric_id: string;
  let detail: string;

  if (corpus_count >= 3) {
    tier = "critical";
    rubric_id = "PV_BREACH_HOST_REPEATED";
    detail = `Host's email present in ${corpus_count} independent breach corpora. High likelihood of repeated credential compromise.`;
  } else if (corpus_count >= 1 && has_sso) {
    tier = "critical";
    rubric_id = "PV_BREACH_HOST_REPEATED";
    detail = `Host's email in ${corpus_count} breach corpus AND an active SSO tenancy was detected on the host's domain. Stolen credentials likely re-usable against corporate identity.`;
  } else if (corpus_count >= 1) {
    tier = "high";
    rubric_id = "PV_BREACH_HOST_SINGLE";
    detail = `Host's email present in ${corpus_count} breach corpus. Treat the listing's claims with skepticism; stolen credentials may already be in use against the listing platform.`;
  } else {
    // counts.raw > 0 but no recognized source -- conservative INFO.
    tier = "info";
    rubric_id = "BREACH_CONTEXT_ONLY";
    detail = `${counts.raw} breach record(s) of unknown provenance. Worth noting; not actionable on its own.`;
  }

  // Compose a scan-readable headline. Property-vetting investigators
  // want the count + tier visible without expanding the finding.
  const tier_label = tier.toUpperCase();
  const headline =
    has_sso && corpus_count >= 1
      ? `Breach verdict: ${tier_label} — ${corpus_count} corpus hit + active SSO tenant`
      : `Breach verdict: ${tier_label} — ${corpus_count} breach corpus${corpus_count === 1 ? "" : "a"}`;

  return {
    tier,
    host_corpus_count: corpus_count,
    raw_record_count: counts.raw,
    by_source,
    has_active_sso_tenant: has_sso,
    rubric_id,
    headline,
    detail,
  };
}

/** Convenience: composed `severity_basis` citation for the verdict's
 *  Finding emission. Matches the format used elsewhere in the codebase. */
export function breachVerdictSeverityBasis(verdict: BreachVerdict): string {
  return severityBasisRef(verdict.rubric_id);
}

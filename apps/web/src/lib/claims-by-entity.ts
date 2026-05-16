// claims-by-entity.ts -- W4-CLAIMS-BY-ENTITY 7th non-breaking projection.
//
// Wave-4 Hideo keystone (hideo-wave4.md:222-227): the flat dossier loses
// entity-correlation; multi-source evidence about the same real-world
// object scatters across sections. Iris ratification
// (iris-deliberation-wave4.md:304-360) routes the fix as a 7th projection
// over the events already in memory, not a schema break.
//
// SCOPE: intra-investigation only. Two fingerprints with the same string
// inside one investigation collapse to one entity; cross-investigation
// entity resolution is the deferred `actor_dossier` decision.
//
// CONSUMES:
//   - InvestigationEvent payloads (structured entity extraction)
//   - existing Finding rows from the six pre-existing projections
//     (best-effort source attribution for entities discovered upstream)
//
// EMITS: Finding rows where >=2 DISTINCT sources cite the same
// canonical entity-fingerprint. Single-source clusters stay in their
// original section -- collapsing them under entity adds nothing.

import {
  canonicalize,
  entityFingerprint,
  type EntityFingerprint,
  type EntityKind,
} from "./entity-canonicalization.ts";
import type { Finding, MatchType, Sample, Severity } from "./dossier-shape.ts";
import type { InvestigationEvent } from "../types/api.ts";

// T2 (Tomás polish-use §Item-2): every cluster carries a match_type so
// the UI can distinguish canonical-normalized matches (FIRM — a transform
// actually happened) from literal-string matches (TENTATIVE — the two
// strings happened to be identical). The discriminator runs against
// EntityFingerprint.normalization_notes: any note in the
// TRANSFORM_NOTES set means a real canonicalization happened.
const TRANSFORM_NOTE_PREFIXES: ReadonlyArray<string> = [
  "abbrev_expanded:",
  "zip_plus4_dropped",
  "suffix_stripped",
  "title_prefix_stripped:",
  "title_suffix_stripped:",
  "gmail_dots_stripped",
  "plus_tag_stripped",
  "e164_explicit_plus",
  "us_country_code_normalized",
  "us_default_10_digit",
  "digits_only_fallback",
  "www_prefix_stripped",
  "fqdn_root_dot_stripped",
  "ein_keyed",
  "state_id_keyed",
];

function isTransformNote(note: string): boolean {
  for (const prefix of TRANSFORM_NOTE_PREFIXES) {
    if (note === prefix || note.startsWith(prefix)) return true;
  }
  return false;
}

/** A single canonicalize() output -> match_type discriminator. */
function matchTypeOf(fp: EntityFingerprint): MatchType {
  const notes = fp.normalization_notes;
  if (notes === undefined || notes.length === 0) return "string_match";
  for (const n of notes) {
    if (isTransformNote(n)) return "canonical_normalized";
  }
  return "string_match";
}

// ---------------------------------------------------------------------------
// Internal types
// ---------------------------------------------------------------------------

interface EntityRef {
  readonly kind: EntityKind;
  readonly raw: string;
  readonly canonical_id: string;
  readonly source: string;
  readonly url?: string;
  readonly headline?: string;
  /** T2: did canonicalize() perform a real transform on this raw input? */
  readonly match_type: MatchType;
}

interface EntityCluster {
  readonly kind: EntityKind;
  readonly canonical_id: string;
  /** Best-readable label (longest non-empty raw seen) for headline rendering. */
  label: string;
  /** Distinct sources contributing to this cluster. */
  readonly sources: Set<string>;
  /** Per-source representative refs, for samples + URL surfacing. */
  readonly refs: EntityRef[];
  /**
   * T2: strongest match_type across all refs. One canonical-normalized
   * ref anywhere in the cluster makes the whole collapse FIRM.
   */
  match_type: MatchType;
}

// ---------------------------------------------------------------------------
// Payload helpers
// ---------------------------------------------------------------------------

function payloadString(payload: unknown, key: string): string {
  if (typeof payload !== "object" || payload === null) return "";
  const v = (payload as Record<string, unknown>)[key];
  return typeof v === "string" ? v : "";
}

function firstNonEmpty(...candidates: string[]): string {
  for (const c of candidates) {
    if (c && c.length > 0) return c;
  }
  return "";
}

// ---------------------------------------------------------------------------
// Per-event entity extraction
// ---------------------------------------------------------------------------
//
// Each adapter type emits payloads with different shapes. We pull every
// recognizable entity reference into a flat list so a single event may
// contribute address + person + LLC simultaneously (required for the
// cross-kind person+LLC-same-address fraud signal).

function extractEntitiesFromEvent(e: InvestigationEvent): EntityRef[] {
  const out: EntityRef[] = [];
  const p = e.payload;
  if (typeof p !== "object" || p === null) return out;

  // Source attribution: prefer payload.source over event_type.
  const source = firstNonEmpty(payloadString(p, "source"), e.event_type);

  // Address-bearing payload keys, in priority order.
  const addressCandidates = [
    payloadString(p, "display_name"),
    payloadString(p, "address"),
    payloadString(p, "street_address"),
    payloadString(p, "formatted_address"),
  ];
  for (const raw of addressCandidates) {
    if (!raw) continue;
    const fp = canonicalize("address", raw);
    if (fp.canonical_id) {
      out.push(toRef("address", raw, fp.canonical_id, source, urlFor(p), matchTypeOf(fp)));
      break; // one address per event is enough -- avoid double-counting
    }
  }

  // City/state-only "address" -- skip; too coarse to be a useful entity key.

  // Person names.
  const personCandidates = [
    payloadString(p, "name"),
    payloadString(p, "host_name"),
    payloadString(p, "author_name"),
    payloadString(p, "display_name_person"),
  ];
  // Also: display_name on person-match events (but display_name is the
  // address on geocode-match events; disambiguate by event_type).
  if (e.event_type === "person-match") {
    personCandidates.unshift(payloadString(p, "name"));
  }
  for (const raw of personCandidates) {
    if (!raw) continue;
    // Cheap address-vs-name guard: if it has digits up front, it's an
    // address, not a person.
    if (/^\d/.test(raw.trim())) continue;
    const fp = canonicalize("person", raw);
    if (fp.canonical_id) {
      out.push(toRef("person", raw, fp.canonical_id, source, urlFor(p), matchTypeOf(fp)));
      break;
    }
  }

  // LLC / business names.
  const llcCandidates = [
    payloadString(p, "llc_name"),
    payloadString(p, "business_name"),
    payloadString(p, "company"),
    payloadString(p, "organization"),
    payloadString(p, "entity_name"),
  ];
  for (const raw of llcCandidates) {
    if (!raw) continue;
    const fp = canonicalize("llc", raw);
    if (fp.canonical_id) {
      out.push(toRef("llc", raw, fp.canonical_id, source, urlFor(p), matchTypeOf(fp)));
      break;
    }
  }

  // Email -- already lowercased upstream but canonicalize for plus-tag /
  // gmail-dot normalization.
  const emailCandidates = [payloadString(p, "email")];
  for (const raw of emailCandidates) {
    if (!raw || !raw.includes("@")) continue;
    const fp = canonicalize("email", raw);
    if (fp.canonical_id) {
      out.push(toRef("email", raw, fp.canonical_id, source, urlFor(p), matchTypeOf(fp)));
      break;
    }
  }

  // Phone.
  const phoneCandidates = [
    payloadString(p, "phone"),
    payloadString(p, "phone_number"),
  ];
  for (const raw of phoneCandidates) {
    if (!raw) continue;
    const fp = canonicalize("phone", raw);
    if (fp.canonical_id) {
      out.push(toRef("phone", raw, fp.canonical_id, source, urlFor(p), matchTypeOf(fp)));
      break;
    }
  }

  // Domain.
  const domainCandidates = [
    payloadString(p, "domain"),
    payloadString(p, "host_domain"),
  ];
  for (const raw of domainCandidates) {
    if (!raw) continue;
    const fp = canonicalize("domain", raw);
    if (fp.canonical_id) {
      out.push(toRef("domain", raw, fp.canonical_id, source, urlFor(p), matchTypeOf(fp)));
      break;
    }
  }

  return out;
}

function urlFor(p: unknown): string | undefined {
  const u =
    payloadString(p, "url") ||
    payloadString(p, "profile_url") ||
    payloadString(p, "listing_url") ||
    payloadString(p, "result_url");
  return u || undefined;
}

function toRef(
  kind: EntityKind,
  raw: string,
  canonical_id: string,
  source: string,
  url: string | undefined,
  match_type: MatchType,
): EntityRef {
  return url === undefined
    ? { kind, raw, canonical_id, source, match_type }
    : { kind, raw, canonical_id, source, url, match_type };
}

// ---------------------------------------------------------------------------
// Best-effort entity extraction from existing Findings
// ---------------------------------------------------------------------------
//
// Findings carry free-text headline + samples[].label. We do NOT try to
// reverse-engineer addresses out of arbitrary text -- the risk of false
// matches is too high. Instead we use existing findings purely to add
// source attribution to entities ALREADY discovered upstream from events.
// (Iris sketch was option (a) -- pure text parsing; we pick option (c) --
// structured-first, text-parse only as attribution sweetener.)
//
// Specifically: if a Finding's source matches a source already present in
// a cluster, we leave the cluster alone. If a Finding mentions a known
// canonical_id anywhere in headline/detail/samples text, we register that
// Finding's source against the cluster. This lets cross-section evidence
// collapse even when the Finding doesn't have a structured payload entity.

function tryFingerprintFromText(text: string): EntityFingerprint | null {
  if (!text || text.length === 0) return null;
  // Address heuristic: starts with 1-5 digits then a space and a word.
  if (/^\s*\d{1,5}\s+\S+/.test(text)) {
    const fp = canonicalize("address", text);
    if (fp.canonical_id) return fp;
  }
  return null;
}

function attributionSweepFromFinding(
  finding: Finding,
  byKey: Map<string, EntityCluster>,
): void {
  const source = finding.source ?? "unknown";
  const candidates: string[] = [finding.headline];
  if (finding.detail) candidates.push(finding.detail);
  if (finding.samples) {
    for (const s of finding.samples) candidates.push(s.label);
  }

  // For each cluster, look for its canonical_id in any candidate text.
  // The match is on the canonical_id (a normalized string) -- callers
  // wanting cross-finding attribution should pass normalized labels.
  for (const [, cluster] of byKey) {
    if (cluster.sources.has(source)) continue;
    for (const text of candidates) {
      const fp = tryFingerprintFromText(text);
      if (fp === null) continue;
      if (fp.kind !== cluster.kind) continue;
      if (fp.canonical_id !== cluster.canonical_id) continue;
      const mt = matchTypeOf(fp);
      cluster.sources.add(source);
      cluster.refs.push({
        kind: cluster.kind,
        canonical_id: cluster.canonical_id,
        raw: text,
        source,
        match_type: mt,
        ...(finding.source_url !== undefined && { url: finding.source_url }),
      });
      if (text.length > cluster.label.length) cluster.label = text;
      if (mt === "canonical_normalized") cluster.match_type = "canonical_normalized";
      break;
    }
  }
}

// ---------------------------------------------------------------------------
// Cluster build
// ---------------------------------------------------------------------------

function addRef(
  byKey: Map<string, EntityCluster>,
  ref: EntityRef,
): void {
  const key = entityFingerprint({
    kind: ref.kind,
    canonical_id: ref.canonical_id,
    raw: ref.raw,
  });
  const existing = byKey.get(key);
  if (existing === undefined) {
    byKey.set(key, {
      kind: ref.kind,
      canonical_id: ref.canonical_id,
      label: ref.raw,
      sources: new Set([ref.source]),
      refs: [ref],
      match_type: ref.match_type,
    });
    return;
  }
  existing.sources.add(ref.source);
  existing.refs.push(ref);
  if (ref.raw.length > existing.label.length) existing.label = ref.raw;
  // Strongest-wins: any canonical-normalized ref upgrades the cluster.
  if (ref.match_type === "canonical_normalized") {
    existing.match_type = "canonical_normalized";
  }
}

// ---------------------------------------------------------------------------
// Emit
// ---------------------------------------------------------------------------

function kindLabel(kind: EntityKind): string {
  switch (kind) {
    case "address":
      return "Address";
    case "person":
      return "Person";
    case "llc":
      return "LLC";
    case "domain":
      return "Domain";
    case "email":
      return "Email";
    case "phone":
      return "Phone";
  }
}

function clusterToFinding(cluster: EntityCluster, severity: Severity): Finding {
  const distinctSources = cluster.sources.size;
  const sourcesList = Array.from(cluster.sources);
  // One sample per source -- pick the first ref for that source.
  const samples: Sample[] = sourcesList.map((src) => {
    const ref = cluster.refs.find((r) => r.source === src);
    if (ref === undefined) return { label: src };
    return ref.url === undefined
      ? { label: `${src}: ${ref.raw}` }
      : { label: `${src}: ${ref.raw}`, url: ref.url };
  });
  const friendly = kindLabel(cluster.kind);
  const headline = `${friendly}: ${cluster.label} — ${distinctSources} sources agree`;
  const detail = `This ${friendly.toLowerCase()} appears in: ${sourcesList.join(", ")}.`;
  // T1 + T2 carry: emit asset (so FindingRow can grade confidence) and
  // match_type (so the renderer can glyph the headline ≡ FIRM canonical
  // vs ~ TENTATIVE literal). cluster.sources.size >= 2 by Pass-3 guard,
  // which is enough to give "person" / "username" assets a FIRM upgrade.
  const assetType = clusterKindToAssetType(cluster.kind);
  return {
    headline,
    detail,
    samples,
    severity,
    source: "entity-fingerprint",
    severity_basis: "matrix:PV_ENTITY_FINGERPRINT_MATCH",
    match_type: cluster.match_type,
    ...(assetType !== null && {
      asset: {
        type: assetType,
        value: cluster.canonical_id,
        sources: sourcesList,
      },
    }),
  };
}

/** T1 helper: bridge EntityKind -> AssetType for asset-graph grading. */
function clusterKindToAssetType(
  kind: EntityKind,
): import("./asset-graph.ts").AssetType | null {
  switch (kind) {
    case "address":
      return "address";
    case "person":
      return "person";
    case "llc":
      // LLCs aren't in the asset taxonomy; treat as a person-grade entity
      // for confidence-laddering purposes (>=2 sources -> FIRM matches
      // the "person" rule and is conceptually correct for a named entity).
      return "person";
    case "domain":
      return "domain";
    case "email":
      return "email";
    case "phone":
      return "phone";
  }
}

/**
 * Decide severity for a cluster.
 *   - info  by default
 *   - warn  on >=3 distinct sources
 *   - warn  on address-cluster that has co-located person + LLC across
 *           any source (potential nominee / shell-company signal)
 */
function clusterSeverity(
  cluster: EntityCluster,
  coLocatedKinds: Set<EntityKind>,
): Severity {
  if (cluster.sources.size >= 3) return "warn";
  if (cluster.kind === "address") {
    if (coLocatedKinds.has("person") && coLocatedKinds.has("llc")) {
      return "warn";
    }
  }
  return "info";
}

// ---------------------------------------------------------------------------
// Public projector
// ---------------------------------------------------------------------------

export function projectClaimsByEntity(
  events: ReadonlyArray<InvestigationEvent>,
  existingFindings: ReadonlyArray<Finding>,
): Finding[] {
  const byKey: Map<string, EntityCluster> = new Map();

  // Pass 1: structured entity extraction from event payloads. Also build
  // a per-address co-location map so we can detect person+LLC at the same
  // address even when they come from different sources.
  const coLocationByAddress: Map<string, Set<EntityKind>> = new Map();
  // address-key -> set of source ids that mentioned EACH co-kind so we
  // can require evidence from somewhere (not just the address mention).
  for (const e of events) {
    const refs = extractEntitiesFromEvent(e);
    let addressKey: string | null = null;
    for (const ref of refs) {
      addRef(byKey, ref);
      if (ref.kind === "address") {
        addressKey = entityFingerprint({
          kind: ref.kind,
          canonical_id: ref.canonical_id,
          raw: ref.raw,
        });
      }
    }
    if (addressKey !== null) {
      let kinds = coLocationByAddress.get(addressKey);
      if (kinds === undefined) {
        kinds = new Set<EntityKind>();
        coLocationByAddress.set(addressKey, kinds);
      }
      for (const ref of refs) {
        if (ref.kind !== "address") kinds.add(ref.kind);
      }
    }
  }

  // Pass 2: attribution sweep over existing findings. Picks up
  // cross-section evidence for already-discovered entities.
  for (const finding of existingFindings) {
    attributionSweepFromFinding(finding, byKey);
  }

  // Pass 3: emit. Only clusters with >=2 distinct sources.
  const out: Finding[] = [];
  for (const [key, cluster] of byKey) {
    if (cluster.sources.size < 2) continue;
    const coKinds = coLocationByAddress.get(key) ?? new Set<EntityKind>();
    const sev = clusterSeverity(cluster, coKinds);
    out.push(clusterToFinding(cluster, sev));
  }

  // Stable order: warn before info; within a tier, by source count desc,
  // then by label asc.
  out.sort((a, b) => {
    const sevRank = (s: Severity | undefined): number =>
      s === "warn" ? 0 : s === "bad" ? -1 : 1;
    const sr = sevRank(a.severity) - sevRank(b.severity);
    if (sr !== 0) return sr;
    return a.headline.localeCompare(b.headline);
  });

  return out;
}

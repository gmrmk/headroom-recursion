// Forensic timeline projector — Sprint-5 W4-TIMELINE
// (Margaret roadmap 2026-05-12 wave-4 §5; Iris ratification
// `phase6/wave4/iris-deliberation-wave4.md` §C.3).
//
// Of Feynman's four proposed wave-4 dossier sections, only `timeline`
// has non-empty day-one data: existing event types carry temporal
// anchors that today scatter across sections without a chronological
// projection. This module is that projection.
//
// Inputs: the same flat `InvestigationEvent[]` stream the rest of
// `dossier-shape.ts` consumes. Outputs: a `Finding[]` for the
// `timeline` section.
//
// Two kinds of findings emitted:
//
//   1. Temporal-cluster Finding (per Iris C.3 + Hideo figure-ground).
//      Runs of ≥3 dated events within a 14-day window. Severity scales
//      to "warn" at 5+ events. Severity_basis: matrix:UX_FORENSIC
//      (added this sprint to severity-rubric.ts).
//
//   2. Timeline-overview Finding. Single Finding listing earliest +
//      latest dated event with total span. Emits only when ≥2 dated
//      events exist (a single event has no span; 0 events emits
//      nothing per empty-day-one discipline).
//
// Forensic-cleanliness rule: payload-anchored dates first (the date
// the EVENT describes), then `e.ts` (the bridge timestamp — when the
// adapter happened to fire) only when no payload date is available
// and the adapter itself is temporally meaningful. We never fall
// through silently to "now" — events with no anchor are dropped.
//
// Co-located test: `timeline.test.ts`.

import type { Finding, Sample, Severity } from "@/lib/dossier-shape";
import type { InvestigationEvent } from "@/types/api";

const CLUSTER_WINDOW_DAYS = 14;
const CLUSTER_WINDOW_MS = CLUSTER_WINDOW_DAYS * 24 * 60 * 60 * 1000;
const CLUSTER_MIN_EVENTS = 3;
const CLUSTER_WARN_THRESHOLD = 5;

/** A successfully date-anchored event extracted from an InvestigationEvent. */
export interface DatedEvent {
  readonly date: Date;
  readonly label: string;
  readonly url?: string;
  readonly source_event_type: string;
  readonly anchor_field: string;
}

function payloadString(payload: unknown, key: string): string {
  if (typeof payload !== "object" || payload === null) return "";
  const v = (payload as Record<string, unknown>)[key];
  return typeof v === "string" ? v : "";
}

/** Parse a string into a Date. Returns null if unparseable or NaN. */
function parseDate(s: string): Date | null {
  if (!s) return null;
  const d = new Date(s);
  if (Number.isNaN(d.getTime())) return null;
  return d;
}

/**
 * Per-event-type anchor extraction. Returns the first payload field
 * that parses to a real Date; falls back to `e.ts` (bridge timestamp)
 * only for event types whose own dispatch time is forensically
 * meaningful (geocode-match: when we resolved the address).
 *
 * Adding a new event type? Add the payload candidates here, not in
 * the projector. Keeps the temporal-anchor policy in one place.
 */
function extractTemporalAnchor(e: InvestigationEvent): DatedEvent | null {
  // Per-event-type payload date candidates, in priority order.
  // First parseable hit wins. Falls through to the generic candidates
  // (created_at / timestamp / discovered_at) for any event type.
  const candidates: ReadonlyArray<{ field: string; labelSuffix: string }> = (() => {
    switch (e.event_type) {
      case "listing-match":
        return [
          { field: "first_seen", labelSuffix: "first seen" },
          { field: "last_seen", labelSuffix: "last seen" },
        ];
      case "breach-hit":
        return [
          { field: "breach_date", labelSuffix: "breach disclosed" },
          { field: "date_compromised", labelSuffix: "machine compromised" },
        ];
      case "infra-fact":
        return [
          { field: "created", labelSuffix: "RDAP created" },
          { field: "updated", labelSuffix: "RDAP updated" },
          { field: "expires", labelSuffix: "RDAP expires" },
        ];
      case "dork-hit":
        return [{ field: "date", labelSuffix: "indexed" }];
      case "tenant-match":
        return [{ field: "discovered_at", labelSuffix: "tenant discovered" }];
      case "geocode-match":
        // Nominatim payloads rarely carry a date. The geocode dispatch
        // itself is the relevant temporal anchor (when we resolved this
        // address); fall through to `ts` below.
        return [];
      default:
        return [];
    }
  })();

  // Try the event-type-specific payload fields first.
  for (const { field, labelSuffix } of candidates) {
    const raw = payloadString(e.payload, field);
    const d = parseDate(raw);
    if (d !== null) {
      return buildDated(d, buildLabel(e, labelSuffix), e, field);
    }
  }

  // Generic payload date fields (any event type).
  for (const field of ["created_at", "timestamp", "discovered_at"]) {
    const raw = payloadString(e.payload, field);
    const d = parseDate(raw);
    if (d !== null) {
      return buildDated(d, buildLabel(e, field.replace("_", " ")), e, field);
    }
  }

  // Final fallback: `e.ts` (bridge timestamp), only for event types
  // where the dispatch time is the meaningful forensic anchor.
  if (e.event_type === "geocode-match") {
    const d = parseDate(e.ts);
    if (d !== null) {
      return buildDated(d, buildLabel(e, "resolved"), e, "ts");
    }
  }

  return null;
}

/**
 * Assemble a `DatedEvent`. Conditionally spreads `url` so we never
 * set the optional key to explicit `undefined` (rejected by
 * `exactOptionalPropertyTypes: true`).
 */
function buildDated(
  date: Date,
  label: string,
  e: InvestigationEvent,
  anchor_field: string,
): DatedEvent {
  const url = extractUrl(e);
  return {
    date,
    label,
    source_event_type: e.event_type,
    anchor_field,
    ...(url !== undefined ? { url } : {}),
  };
}

function buildLabel(e: InvestigationEvent, suffix: string): string {
  const head =
    payloadString(e.payload, "title") ||
    payloadString(e.payload, "name") ||
    payloadString(e.payload, "host_name") ||
    payloadString(e.payload, "computer_name") ||
    payloadString(e.payload, "domain") ||
    payloadString(e.payload, "platform") ||
    payloadString(e.payload, "display_name") ||
    payloadString(e.payload, "source") ||
    e.event_type;
  return `${head} — ${suffix}`;
}

function extractUrl(e: InvestigationEvent): string | undefined {
  const candidates = [
    "listing_url",
    "profile_url",
    "search_url",
    "result_url",
    "url",
  ];
  for (const key of candidates) {
    const v = payloadString(e.payload, key);
    if (v) return v;
  }
  return undefined;
}

/** Format a Date as YYYY-MM-DD (UTC). Used in headlines and labels. */
function dateOnly(d: Date): string {
  return d.toISOString().slice(0, 10);
}

/**
 * Find runs of ≥CLUSTER_MIN_EVENTS dated events within a
 * CLUSTER_WINDOW_DAYS sliding window. Greedy: when a valid run is
 * found, extend it maximally (any subsequent event still within the
 * window of the run's START is included), then advance past the run
 * before scanning for the next cluster.
 *
 * Input MUST be sorted by date ascending. Caller's responsibility.
 */
export function findTemporalClusters(
  dated: ReadonlyArray<DatedEvent>,
): ReadonlyArray<ReadonlyArray<DatedEvent>> {
  const clusters: DatedEvent[][] = [];
  let i = 0;
  while (i < dated.length) {
    const start = dated[i]!;
    let j = i;
    while (
      j + 1 < dated.length &&
      dated[j + 1]!.date.getTime() - start.date.getTime() <= CLUSTER_WINDOW_MS
    ) {
      j += 1;
    }
    const runLength = j - i + 1;
    if (runLength >= CLUSTER_MIN_EVENTS) {
      clusters.push(dated.slice(i, j + 1));
      i = j + 1;
    } else {
      i += 1;
    }
  }
  return clusters;
}

/** Public projector consumed by `dossier-shape.ts#buildReportShape`. */
export function projectTimeline(
  events: ReadonlyArray<InvestigationEvent>,
): Finding[] {
  const dated: DatedEvent[] = [];
  for (const e of events) {
    const anchor = extractTemporalAnchor(e);
    if (anchor !== null) dated.push(anchor);
  }
  if (dated.length === 0) return [];

  dated.sort((a, b) => a.date.getTime() - b.date.getTime());

  const out: Finding[] = [];

  // Overview Finding — emit only when ≥2 dated events (a single
  // event has no span). The overview is the navigation primitive;
  // the investigator's first scan-target on opening this section.
  if (dated.length >= 2) {
    const first = dated[0]!;
    const last = dated[dated.length - 1]!;
    const spanMs = last.date.getTime() - first.date.getTime();
    const spanDays = Math.max(0, Math.round(spanMs / (24 * 60 * 60 * 1000)));
    out.push({
      headline: `Forensic timeline — ${dated.length} dated events over ${spanDays} day${spanDays === 1 ? "" : "s"}`,
      detail: `Earliest: ${dateOnly(first.date)} (${first.label}). Latest: ${dateOnly(last.date)} (${last.label}).`,
      severity: "info",
      source: "timeline-overview",
    });
  }

  // Cluster Findings.
  const clusters = findTemporalClusters(dated);
  for (const cluster of clusters) {
    const start = cluster[0]!;
    const end = cluster[cluster.length - 1]!;
    const spanMs = end.date.getTime() - start.date.getTime();
    const spanDays = Math.max(1, Math.round(spanMs / (24 * 60 * 60 * 1000)));
    const startDate = dateOnly(start.date);
    const endDate = dateOnly(end.date);
    const samples: Sample[] = cluster.slice(0, 6).map((ev) => {
      const sample: Sample = ev.url
        ? { label: `${ev.label} — ${dateOnly(ev.date)}`, url: ev.url }
        : { label: `${ev.label} — ${dateOnly(ev.date)}` };
      return sample;
    });
    const severity: Severity = cluster.length >= CLUSTER_WARN_THRESHOLD ? "warn" : "info";
    out.push({
      headline: `Temporal cluster: ${cluster.length} events between ${startDate} and ${endDate}`,
      detail: `${cluster.length} dated events fall within a ${spanDays}-day window. Patterns like rapid review accrual or coordinated account creation often surface here.`,
      samples,
      severity,
      source: "timeline-cluster",
      severity_basis: "matrix:UX_FORENSIC",
    });
  }

  return out;
}

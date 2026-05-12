// Dossier shape — pure projection from the raw event stream into a
// reader-friendly report structure. Phase 2 of the dashboard redesign
// (tasks/todos.md 2026-05-11).
//
// Two consumers (single source of truth):
//   - InvestigationReport.tsx     live React panel (Phase 4)
//   - serializeDossierHtml        existing static .html export
// Both render the same Section / Finding shapes.
//
// The projection is opinionated: events are bucketed into FIVE semantic
// sections that match the property-vetting investigator's mental model:
//
//   Identity   — who they CLAIM to be (Gravatar verified_accounts,
//                GitHub login attribution)
//   Behavior   — what they ACTUALLY did (GitHub commits, user_scanner
//                service registrations)
//   Compromise — was their machine pwned or their email leaked
//                (Hudson Rock infostealer logs, HIBP breaches, IntelBase)
//   Property   — what we know about the listing's address/host
//                (Nominatim, Overpass, Inside Airbnb, true_people_search,
//                phone validations)
//   Visual     — image evidence (ELA glow-maps, flipped variants, AI
//                detection verdicts, reverse-image-search hits)

import type { InvestigationEvent } from "@/types/api";

export type SectionId =
  | "identity"
  | "behavior"
  | "compromise"
  | "property"
  | "visual";

export type Severity = "info" | "good" | "warn" | "bad";

export interface Sample {
  readonly label: string;
  readonly url?: string | undefined;
}

export interface Finding {
  /** One-line scan-readable headline (the bit that fits in a single row). */
  readonly headline: string;
  /** Optional secondary line for context. */
  readonly detail?: string | undefined;
  /** Optional clickable canonical source URL. */
  readonly source_url?: string | undefined;
  /** Optional sub-bullets (each may carry its own link). */
  readonly samples?: ReadonlyArray<Sample> | undefined;
  /** Image artifacts (data/<rel>) the investigator can preview inline. */
  readonly image_rels?: ReadonlyArray<string> | undefined;
  /** Color hint; consumers decide whether to use it. */
  readonly severity?: Severity | undefined;
  /** Original event-source string for grouping/debug. */
  readonly source?: string | undefined;
}

export interface Section {
  readonly id: SectionId;
  readonly title: string;
  readonly findings: ReadonlyArray<Finding>;
}

export interface ReportError {
  readonly adapter_id?: string | undefined;
  readonly reason: string;
}

export interface ReportShape {
  readonly sections: ReadonlyArray<Section>;
  readonly errors: ReadonlyArray<ReportError>;
  readonly event_count: number;
  readonly has_any_findings: boolean;
}

const SECTION_ORDER: ReadonlyArray<{ id: SectionId; title: string }> = [
  { id: "identity", title: "Identity" },
  { id: "behavior", title: "Behavior" },
  { id: "compromise", title: "Compromise" },
  { id: "property", title: "Property" },
  { id: "visual", title: "Visual evidence" },
];

function payloadString(payload: unknown, key: string): string {
  if (typeof payload !== "object" || payload === null) return "";
  const v = (payload as Record<string, unknown>)[key];
  return typeof v === "string" ? v : "";
}

function payloadNumber(payload: unknown, key: string): number | null {
  if (typeof payload !== "object" || payload === null) return null;
  const v = (payload as Record<string, unknown>)[key];
  return typeof v === "number" ? v : null;
}

function payloadGet(payload: unknown, key: string): unknown {
  if (typeof payload !== "object" || payload === null) return undefined;
  return (payload as Record<string, unknown>)[key];
}

function isPersonMatchOfSource(
  e: InvestigationEvent,
  source: string,
): boolean {
  return (
    e.event_type === "person-match" && payloadString(e.payload, "source") === source
  );
}

function isBreachHitOfSource(e: InvestigationEvent, source: string): boolean {
  return (
    e.event_type === "breach-hit" && payloadString(e.payload, "source") === source
  );
}

function imageRelsIn(payload: unknown): string[] {
  if (typeof payload !== "object" || payload === null) return [];
  const out: string[] = [];
  for (const [k, v] of Object.entries(payload)) {
    if (k.endsWith("_rel") && typeof v === "string" && v) {
      out.push(v);
    }
  }
  return out;
}

// ---------------------------------------------------------------------------
// Identity section
// ---------------------------------------------------------------------------

function projectIdentity(events: ReadonlyArray<InvestigationEvent>): Finding[] {
  const out: Finding[] = [];
  // Gravatar — owner-attested identity. Aggregate verified-account
  // person-matches into a single Finding with samples.
  const gravatarMatches = events.filter((e) => isPersonMatchOfSource(e, "gravatar"));
  const gravatarSummary = events.find(
    (e) =>
      e.event_type === "tool-run-result" &&
      payloadString(e.payload, "source") === "gravatar",
  );
  if (gravatarMatches.length > 0 || (gravatarSummary && payloadGet(gravatarSummary.payload, "profile_found") === true)) {
    const displayName = gravatarSummary
      ? payloadString(gravatarSummary.payload, "display_name")
      : "";
    const profileUrl = gravatarSummary
      ? payloadString(gravatarSummary.payload, "profile_url")
      : "";
    const samples: Sample[] = gravatarMatches.map((e) => ({
      label:
        payloadString(e.payload, "platform_label") ||
        payloadString(e.payload, "platform"),
      url: payloadString(e.payload, "profile_url"),
    }));
    out.push({
      headline: displayName
        ? `Gravatar — claimed identity: ${displayName}`
        : `Gravatar — profile found (${gravatarMatches.length} verified account${gravatarMatches.length === 1 ? "" : "s"})`,
      detail:
        gravatarMatches.length > 0
          ? "Owner has explicitly tied this email to the following platforms."
          : "Gravatar profile exists but no verified_accounts disclosed.",
      source_url: profileUrl || undefined,
      samples: samples.length > 0 ? samples : undefined,
      severity: "good",
      source: "gravatar",
    });
  } else if (gravatarSummary) {
    out.push({
      headline: "Gravatar — no public profile",
      detail: "No claimed identity for this email.",
      severity: "info",
      source: "gravatar",
    });
  }

  // GitHub commits attribution: github events emit person-match per
  // unique repo with login + author_name. The login is identity signal.
  const githubMatches = events.filter((e) =>
    isPersonMatchOfSource(e, "github_commits"),
  );
  if (githubMatches.length > 0) {
    const firstLogin = payloadString(githubMatches[0]!.payload, "login");
    const profileUrl = payloadString(githubMatches[0]!.payload, "profile_url");
    const authorName = payloadString(githubMatches[0]!.payload, "author_name");
    const repoCount = new Set(
      githubMatches.map((e) => payloadString(e.payload, "repo")),
    ).size;
    out.push({
      headline: firstLogin
        ? `GitHub identity: @${firstLogin}${authorName ? ` (${authorName})` : ""}`
        : "GitHub commit-author identity found",
      detail: `${repoCount} unique repo${repoCount === 1 ? "" : "s"} attributed to this email.`,
      source_url: profileUrl || undefined,
      severity: "good",
      source: "github_commits",
    });
  }
  return out;
}

// ---------------------------------------------------------------------------
// Behavior section
// ---------------------------------------------------------------------------

function projectBehavior(events: ReadonlyArray<InvestigationEvent>): Finding[] {
  const out: Finding[] = [];

  // GitHub commits behavior: same source, but the lens is "did they
  // actually write code." Repo + commit_count list.
  const githubMatches = events.filter((e) =>
    isPersonMatchOfSource(e, "github_commits"),
  );
  const githubSummary = events.find(
    (e) =>
      e.event_type === "tool-run-result" &&
      payloadString(e.payload, "source") === "github_commits",
  );
  if (githubMatches.length > 0) {
    const total = githubSummary
      ? payloadNumber(githubSummary.payload, "total_commits")
      : null;
    const uniqueRepos = githubSummary
      ? payloadNumber(githubSummary.payload, "unique_repos")
      : null;
    const samples: Sample[] = githubMatches.slice(0, 6).map((e) => {
      const repo = payloadString(e.payload, "repo");
      const sample = payloadString(e.payload, "sample_commit");
      const count = payloadNumber(e.payload, "commit_count") ?? 0;
      return {
        label: `${repo} (${count} commit${count === 1 ? "" : "s"})`,
        url: sample || `https://github.com/${repo}`,
      };
    });
    out.push({
      headline: `Public code authorship — ${uniqueRepos ?? githubMatches.length} repos${total ? `, ${total.toLocaleString()} commits index-wide` : ""}`,
      detail:
        total && total > 1_000_000
          ? "Total reflects GitHub search-index fuzziness across mirrors/forks; unique repos count is the truthful figure."
          : undefined,
      samples,
      severity: "good",
      source: "github_commits",
    });
  }

  // user_scanner: 95+ services probed; emit one finding per platform
  // hit as samples under a single behavior Finding.
  const userScannerMatches = events.filter((e) =>
    isPersonMatchOfSource(e, "user_scanner"),
  );
  if (userScannerMatches.length > 0) {
    const samples: Sample[] = userScannerMatches.slice(0, 20).map((e) => ({
      label:
        payloadString(e.payload, "platform") ||
        payloadString(e.payload, "category"),
      url: payloadString(e.payload, "profile_url"),
    }));
    out.push({
      headline: `Service registrations — ${userScannerMatches.length} platform${userScannerMatches.length === 1 ? "" : "s"}`,
      detail:
        "user-scanner probed 95+ services; these confirmed the email is registered.",
      samples,
      severity: "info",
      source: "user_scanner",
    });
  }

  return out;
}

// ---------------------------------------------------------------------------
// Compromise section
// ---------------------------------------------------------------------------

function projectCompromise(events: ReadonlyArray<InvestigationEvent>): Finding[] {
  const out: Finding[] = [];

  // Hudson Rock infostealer logs.
  const hudson = events.filter((e) => isBreachHitOfSource(e, "hudson_rock"));
  if (hudson.length > 0) {
    const samples: Sample[] = hudson.slice(0, 5).map((e) => ({
      label: `${payloadString(e.payload, "computer_name") || "(unknown machine)"} — ${payloadString(e.payload, "date_compromised") || "(no date)"}${(() => {
        const os = payloadString(e.payload, "operating_system");
        return os ? ` · ${os}` : "";
      })()}`,
    }));
    out.push({
      headline: `Infostealer compromise — ${hudson.length} machine${hudson.length === 1 ? "" : "s"} in stealer dumps`,
      detail:
        "Hudson Rock free Cavalier API. Credentials redacted; only machine + date + OS shown.",
      samples,
      severity: "bad",
      source: "hudson_rock",
    });
  }

  // HIBP — breach-hits without a source field (the legacy HIBP shape).
  const hibp = events.filter(
    (e) =>
      e.event_type === "breach-hit" &&
      !payloadString(e.payload, "source") &&
      payloadString(e.payload, "domain"),
  );
  if (hibp.length > 0) {
    const samples: Sample[] = hibp.slice(0, 5).map((e) => ({
      label: `${payloadString(e.payload, "title") || payloadString(e.payload, "name")} — ${payloadString(e.payload, "breach_date") || "(no date)"}`,
    }));
    out.push({
      headline: `Domain breach history — ${hibp.length} breach record${hibp.length === 1 ? "" : "s"}`,
      detail: "HIBP domain-level breach signal (per-account confirmation requires paid HIBP).",
      samples,
      severity: "warn",
      source: "hibp",
    });
  }

  // IntelBase (if paid key in use).
  const intelbase = events.filter((e) => isBreachHitOfSource(e, "intelbase"));
  if (intelbase.length > 0) {
    const samples: Sample[] = intelbase.slice(0, 5).map((e) => ({
      label: `${payloadString(e.payload, "name")} — ${payloadString(e.payload, "breach_date") || "(no date)"}`,
    }));
    out.push({
      headline: `IntelBase per-email breach hits — ${intelbase.length}`,
      detail: "Credentials redacted at adapter boundary.",
      samples,
      severity: "bad",
      source: "intelbase",
    });
  }

  return out;
}

// ---------------------------------------------------------------------------
// Property section
// ---------------------------------------------------------------------------

function projectProperty(events: ReadonlyArray<InvestigationEvent>): Finding[] {
  const out: Finding[] = [];

  // Geocoded address.
  const geo = events.find((e) => e.event_type === "geocode-match");
  if (geo) {
    const lat = payloadNumber(geo.payload, "lat");
    const lon = payloadNumber(geo.payload, "lon");
    const display = payloadString(geo.payload, "display_name");
    out.push({
      headline: display || "Geocoded address",
      detail: lat !== null && lon !== null ? `Lat/lon: ${lat}, ${lon}` : undefined,
      source_url:
        lat !== null && lon !== null
          ? `https://www.openstreetmap.org/?mlat=${lat}&mlon=${lon}#map=18/${lat}/${lon}`
          : undefined,
      severity: "info",
      source: "nominatim",
    });
  }

  // Overpass neighborhood profile.
  const overpassMatches = events.filter(
    (e) =>
      e.event_type === "listing-match" &&
      payloadString(e.payload, "source") === "overpass",
  );
  const overpassSummary = events.find(
    (e) =>
      e.event_type === "tool-run-result" &&
      payloadString(e.payload, "source") === "overpass",
  );
  if (overpassMatches.length > 0 || overpassSummary) {
    const dominant = overpassSummary
      ? payloadString(overpassSummary.payload, "dominant_category")
      : "";
    const samples: Sample[] = overpassMatches.slice(0, 8).map((e) => ({
      label: `${payloadString(e.payload, "category")}: ${payloadNumber(e.payload, "count") ?? 0}`,
    }));
    out.push({
      headline: dominant
        ? `Neighborhood profile — dominant: ${dominant}`
        : "Neighborhood profile",
      detail: "OSM Overpass nearby-features classification.",
      samples,
      severity: "info",
      source: "overpass",
    });
  }

  // Inside Airbnb listings.
  const airbnb = events.filter(
    (e) =>
      e.event_type === "listing-match" &&
      payloadString(e.payload, "source") === "inside_airbnb",
  );
  if (airbnb.length > 0) {
    const samples: Sample[] = airbnb.slice(0, 5).map((e) => ({
      label: `${payloadString(e.payload, "host_name") || "(unknown host)"} — ${payloadString(e.payload, "listing_url") || ""}`,
      url: payloadString(e.payload, "listing_url"),
    }));
    out.push({
      headline: `Inside Airbnb host fingerprint — ${airbnb.length} listing${airbnb.length === 1 ? "" : "s"}`,
      samples,
      severity: airbnb.length >= 3 ? "warn" : "info",
      source: "inside_airbnb",
    });
  }

  // TruePeopleSearch person matches (the host_name leg).
  const tps = events.filter(
    (e) =>
      e.event_type === "person-match" &&
      (payloadString(e.payload, "source") === "true_people_search" ||
        // legacy shape: no source field but has age_range
        (!payloadString(e.payload, "source") &&
          payloadString(e.payload, "age_range"))),
  );
  if (tps.length > 0) {
    const samples: Sample[] = tps.slice(0, 5).map((e) => ({
      label: `${payloadString(e.payload, "name")} (${payloadString(e.payload, "age_range") || "?"}) — ${payloadString(e.payload, "city")}, ${payloadString(e.payload, "state")}`,
      url: payloadString(e.payload, "result_url"),
    }));
    out.push({
      headline: `TruePeopleSearch host matches — ${tps.length}`,
      samples,
      severity: "info",
      source: "true_people_search",
    });
  }

  return out;
}

// ---------------------------------------------------------------------------
// Visual section
// ---------------------------------------------------------------------------

function projectVisual(events: ReadonlyArray<InvestigationEvent>): Finding[] {
  const out: Finding[] = [];

  // AI image detection verdicts.
  const aiDetect = events.find(
    (e) =>
      e.event_type === "image-match" &&
      payloadString(e.payload, "source") === "ai_local_detect",
  );
  if (aiDetect) {
    const likelihood = payloadString(aiDetect.payload, "ai_likelihood");
    const score = payloadNumber(aiDetect.payload, "score") ?? 0;
    const sevMap: Record<string, Severity> = {
      none: "good",
      low: "good",
      medium: "warn",
      high: "bad",
    };
    out.push({
      headline: `AI-image heuristic — likelihood: ${likelihood} (score ${score})`,
      detail: "Local heuristic ensemble — not proof. Catches lazy AI listings.",
      severity: sevMap[likelihood] ?? "info",
      source: "ai_local_detect",
    });
  }

  // Provenance composite.
  const provenance = events.find(
    (e) =>
      e.event_type === "image-match" &&
      payloadString(e.payload, "source") === "provenance-composite",
  );
  if (provenance) {
    const ela = payloadString(provenance.payload, "ela_verdict");
    out.push({
      headline: `Image provenance — ELA: ${ela || "unknown"}`,
      detail: payloadString(provenance.payload, "software")
        ? `Software tag: ${payloadString(provenance.payload, "software")}`
        : undefined,
      severity: ela === "clean" ? "good" : "warn",
      source: "provenance-composite",
      image_rels: imageRelsIn(provenance.payload),
    });
  }

  // Flipped variants + ELA glow-maps from image_flip / image_ela.
  const flipped = events.filter(
    (e) =>
      e.event_type === "image-match" &&
      payloadGet(e.payload, "flipped_rel") !== undefined,
  );
  const elaImgs = events.filter(
    (e) =>
      e.event_type === "image-match" &&
      payloadGet(e.payload, "ela_rel") !== undefined,
  );
  const allRels: string[] = [];
  for (const e of flipped) allRels.push(...imageRelsIn(e.payload));
  for (const e of elaImgs) allRels.push(...imageRelsIn(e.payload));
  const uniqueRels = Array.from(new Set(allRels));
  if (uniqueRels.length > 0) {
    out.push({
      headline: `Visual artifacts — ${uniqueRels.length} preview${uniqueRels.length === 1 ? "" : "s"} available`,
      image_rels: uniqueRels,
      severity: "info",
      source: "image_artifacts",
    });
  }

  // Reverse image search aggregator hits.
  const reverseHits = events.filter(
    (e) =>
      e.event_type === "image-match" &&
      payloadString(e.payload, "source") === "reverse_image_aggregator",
  );
  if (reverseHits.length > 0) {
    const samples: Sample[] = reverseHits.slice(0, 5).map((e) => ({
      label: payloadString(e.payload, "engine") || "(unnamed engine)",
      url: payloadString(e.payload, "search_url"),
    }));
    out.push({
      headline: `Reverse image search — ${reverseHits.length} engine result${reverseHits.length === 1 ? "" : "s"}`,
      samples,
      severity: "info",
      source: "reverse_image_aggregator",
    });
  }

  return out;
}

// ---------------------------------------------------------------------------
// Errors
// ---------------------------------------------------------------------------

function collectErrors(events: ReadonlyArray<InvestigationEvent>): ReportError[] {
  return events
    .filter((e) => e.event_type === "tool-run-error")
    .map((e) => ({
      adapter_id: payloadString(e.payload, "adapter_id") || undefined,
      reason: payloadString(e.payload, "reason") || "(no reason given)",
    }));
}

// ---------------------------------------------------------------------------
// Top-level
// ---------------------------------------------------------------------------

export function buildReportShape(
  events: ReadonlyArray<InvestigationEvent>,
): ReportShape {
  const sections: Section[] = [];
  const projectors: Record<SectionId, (es: ReadonlyArray<InvestigationEvent>) => Finding[]> = {
    identity: projectIdentity,
    behavior: projectBehavior,
    compromise: projectCompromise,
    property: projectProperty,
    visual: projectVisual,
  };
  let anyFinding = false;
  for (const meta of SECTION_ORDER) {
    const findings = projectors[meta.id](events);
    if (findings.length > 0) anyFinding = true;
    sections.push({ id: meta.id, title: meta.title, findings });
  }
  return {
    sections,
    errors: collectErrors(events),
    event_count: events.length,
    has_any_findings: anyFinding,
  };
}

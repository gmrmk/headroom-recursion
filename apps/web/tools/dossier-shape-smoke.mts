// Smoke test for buildReportShape -- pure projection from events into
// the structured ReportShape that both InvestigationReport (Phase 4)
// and the existing HTML serializer (when migrated) consume.
//
// Run:
//   node --experimental-strip-types apps/web/tools/dossier-shape-smoke.mts

import { buildReportShape } from "../src/lib/dossier-shape.ts";
import type { InvestigationEvent } from "../src/types/api.ts";

let pass = 0;
let fail = 0;
const failures: string[] = [];

function evt(
  event_type: InvestigationEvent["event_type"],
  payload: Record<string, unknown> = {},
  sequence = 0,
): InvestigationEvent {
  return {
    event_type,
    investigation_id: "t",
    run_id: "t",
    sequence,
    ts: "2026-05-11T00:00:00Z",
    payload,
  };
}

function check(name: string, cond: boolean, detail = ""): void {
  if (cond) pass += 1;
  else {
    fail += 1;
    failures.push(`${name}${detail ? ": " + detail : ""}`);
  }
}

// ---------------------------------------------------------------------------
// Empty events -> 5 empty sections, no findings
// ---------------------------------------------------------------------------

{
  const shape = buildReportShape([]);
  check("5 sections always present", shape.sections.length === 5);
  check("has_any_findings false on empty", shape.has_any_findings === false);
  check("section ids ordered correctly",
    shape.sections.map((s) => s.id).join(",") ===
      "identity,behavior,compromise,property,visual",
  );
  check("event_count 0", shape.event_count === 0);
}

// ---------------------------------------------------------------------------
// Identity: Gravatar verified accounts aggregated to one Finding
// ---------------------------------------------------------------------------

{
  const events: InvestigationEvent[] = [
    evt("person-match", {
      source: "gravatar",
      platform: "github",
      platform_label: "GitHub",
      profile_url: "https://github.com/alex",
    }, 1),
    evt("person-match", {
      source: "gravatar",
      platform: "linkedin",
      platform_label: "LinkedIn",
      profile_url: "https://linkedin.com/in/alex",
    }, 2),
    evt("tool-run-result", {
      source: "gravatar",
      profile_found: true,
      display_name: "Alex Morgan",
      profile_url: "https://gravatar.com/alex",
      verified_count: 2,
    }, 3),
  ];
  const shape = buildReportShape(events);
  const identity = shape.sections.find((s) => s.id === "identity")!;
  check("gravatar identity finding emitted", identity.findings.length === 1);
  const f = identity.findings[0]!;
  check("identity headline names the display_name",
    f.headline.includes("Alex Morgan"));
  check("identity has source_url to gravatar profile",
    f.source_url === "https://gravatar.com/alex");
  check("identity samples list both verified accounts",
    (f.samples?.length ?? 0) === 2);
  check("identity sample github has correct url",
    f.samples?.[0]?.url === "https://github.com/alex");
}

// ---------------------------------------------------------------------------
// Identity: Gravatar 404 surfaces as no-public-profile Finding
// ---------------------------------------------------------------------------

{
  const events: InvestigationEvent[] = [
    evt("tool-run-result", {
      source: "gravatar",
      profile_found: false,
      verified_count: 0,
    }),
  ];
  const shape = buildReportShape(events);
  const identity = shape.sections.find((s) => s.id === "identity")!;
  check("gravatar 404 surfaces as a Finding", identity.findings.length === 1);
  check("gravatar 404 headline says 'no public profile'",
    identity.findings[0]!.headline.toLowerCase().includes("no public"));
  check("gravatar 404 severity is info",
    identity.findings[0]!.severity === "info");
}

// ---------------------------------------------------------------------------
// Behavior: GitHub commits + user_scanner
// ---------------------------------------------------------------------------

{
  const events: InvestigationEvent[] = [
    evt("person-match", {
      source: "github_commits",
      repo: "torvalds/linux",
      login: "torvalds",
      profile_url: "https://github.com/torvalds",
      author_name: "Linus Torvalds",
      commit_count: 4500,
      sample_commit: "https://github.com/torvalds/linux/commit/abc",
    }, 1),
    evt("person-match", {
      source: "github_commits",
      repo: "torvalds/subsurface",
      login: "torvalds",
      profile_url: "https://github.com/torvalds",
      commit_count: 100,
    }, 2),
    evt("tool-run-result", {
      source: "github_commits",
      total_commits: 178_156_233,
      unique_repos: 2,
      rate_limited: false,
    }, 3),
    evt("person-match", {
      source: "user_scanner",
      platform: "github",
      profile_url: "https://github.com",
    }, 4),
    evt("person-match", {
      source: "user_scanner",
      platform: "spotify",
      profile_url: "https://spotify.com",
    }, 5),
  ];
  const shape = buildReportShape(events);
  const behavior = shape.sections.find((s) => s.id === "behavior")!;
  check("behavior has github + user_scanner findings",
    behavior.findings.length === 2);
  check(
    "github finding mentions unique_repos count",
    behavior.findings.some((f) => f.headline.includes("2 repos")),
  );
  check(
    "user_scanner finding lists 2 platforms",
    behavior.findings.some(
      (f) =>
        f.source === "user_scanner" && (f.samples?.length ?? 0) === 2,
    ),
  );
}

// ---------------------------------------------------------------------------
// Compromise: Hudson Rock + HIBP
// ---------------------------------------------------------------------------

{
  const events: InvestigationEvent[] = [
    evt("breach-hit", {
      source: "hudson_rock",
      computer_name: "DESKTOP-X (alice)",
      operating_system: "Windows 11",
      date_compromised: "2024-06-01T00:00:00.000Z",
    }, 1),
    evt("breach-hit", {
      source: "hudson_rock",
      computer_name: "LAPTOP-Y (bob)",
      operating_system: "Windows 10",
      date_compromised: "2024-08-15T00:00:00.000Z",
    }, 2),
    evt("breach-hit", {
      // HIBP: no source field, has domain
      domain: "example.com",
      name: "BigBreach2024",
      title: "Big Breach 2024",
      breach_date: "2024-03-01",
    }, 3),
  ];
  const shape = buildReportShape(events);
  const compromise = shape.sections.find((s) => s.id === "compromise")!;
  check("compromise has 2 findings (hudson + hibp)",
    compromise.findings.length === 2);
  const hudson = compromise.findings.find((f) => f.source === "hudson_rock");
  check("hudson rock finding has 2 samples", (hudson?.samples?.length ?? 0) === 2);
  check("hudson rock severity is bad", hudson?.severity === "bad");
  const hibp = compromise.findings.find((f) => f.source === "hibp");
  check("hibp finding severity is warn", hibp?.severity === "warn");
}

// ---------------------------------------------------------------------------
// Property: Nominatim + Overpass + TruePeopleSearch
// ---------------------------------------------------------------------------

{
  const events: InvestigationEvent[] = [
    evt("geocode-match", {
      query: "123 Main St",
      lat: 39.78,
      lon: -89.65,
      display_name: "123 Main St, Springfield, IL",
    }, 1),
    evt("listing-match", {
      source: "overpass",
      category: "residential",
      count: 12,
    }, 2),
    evt("listing-match", {
      source: "overpass",
      category: "food_drink",
      count: 3,
    }, 3),
    evt("tool-run-result", {
      source: "overpass",
      dominant_category: "residential",
      category_counts: { residential: 12, food_drink: 3 },
    }, 4),
    evt("person-match", {
      // legacy TPS shape: no source field, has age_range
      name: "Alice Smith",
      age_range: "40-45",
      city: "Springfield",
      state: "IL",
      result_url: "https://www.truepeoplesearch.com/x",
    }, 5),
  ];
  const shape = buildReportShape(events);
  const property = shape.sections.find((s) => s.id === "property")!;
  check("property has nominatim + overpass + tps",
    property.findings.length === 3);
  const overpass = property.findings.find((f) => f.source === "overpass");
  check("overpass headline names dominant category",
    overpass?.headline.toLowerCase().includes("residential") ?? false);
  const geo = property.findings.find((f) => f.source === "nominatim");
  check("nominatim source_url is an OSM map link",
    geo?.source_url?.startsWith("https://www.openstreetmap.org") ?? false);
}

// ---------------------------------------------------------------------------
// Visual: AI detection + image rels
// ---------------------------------------------------------------------------

{
  const events: InvestigationEvent[] = [
    evt("image-match", {
      source: "ai_local_detect",
      ai_likelihood: "high",
      score: 7,
    }, 1),
    evt("image-match", {
      source: "image_flip_check",
      flipped_rel: "flipped/abc.jpg",
    }, 2),
    evt("image-match", {
      source: "image_ela_check",
      ela_rel: "ela/abc.jpg",
    }, 3),
  ];
  const shape = buildReportShape(events);
  const visual = shape.sections.find((s) => s.id === "visual")!;
  check("visual has AI + artifacts findings",
    visual.findings.length === 2);
  const ai = visual.findings.find((f) => f.source === "ai_local_detect");
  check("AI severity is bad for likelihood=high", ai?.severity === "bad");
  const artifacts = visual.findings.find((f) => f.source === "image_artifacts");
  check("artifacts include flipped_rel + ela_rel",
    (artifacts?.image_rels?.length ?? 0) === 2);
}

// ---------------------------------------------------------------------------
// Errors collected separately from findings
// ---------------------------------------------------------------------------

{
  const events: InvestigationEvent[] = [
    evt("tool-run-error", {
      adapter_id: "hudson_rock_email_check",
      reason: "HTTP 503",
    }, 1),
    evt("tool-run-error", { reason: "missing image_url" }, 2),
  ];
  const shape = buildReportShape(events);
  check("errors collected separately", shape.errors.length === 2);
  check("first error has adapter_id",
    shape.errors[0]!.adapter_id === "hudson_rock_email_check");
  check("second error has reason but no adapter_id",
    shape.errors[1]!.adapter_id === undefined &&
      shape.errors[1]!.reason === "missing image_url");
}

// ---------------------------------------------------------------------------
// has_any_findings reflects findings across ALL sections, not just one
// ---------------------------------------------------------------------------

{
  const shapeNone = buildReportShape([evt("heartbeat")]);
  check("heartbeat alone -> no findings", shapeNone.has_any_findings === false);
  const shapeOne = buildReportShape([
    evt("breach-hit", { source: "hudson_rock", computer_name: "X" }),
  ]);
  check("one breach -> has_any_findings true",
    shapeOne.has_any_findings === true);
}

console.log(`dossier-shape smoke: ${pass} passed, ${fail} failed`);
if (failures.length > 0) {
  console.error("FAILURES:");
  for (const f of failures) console.error("  " + f);
  process.exit(1);
}
process.exit(0);

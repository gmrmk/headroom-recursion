// timeline.test.ts — unit tests for the W4-TIMELINE projector.
// Co-located with `timeline.ts` per the wave-3 many-small-files
// discipline (mirrors `entity-canonicalization.test.ts`).
//
// Runner: `node:test` (built into Node 22+). Run with type-stripping:
//   node --test --experimental-strip-types src/lib/timeline.test.ts
//
// Six asserted behaviors per W4-TIMELINE spec:
//   1. 0 dated events            → empty findings
//   2. 3 events within 14 days   → 1 cluster finding (info severity)
//   3. 3 events spanning 60 days → 0 cluster findings (overview only)
//   4. 5 events in same window   → cluster severity "warn"
//   5. Mixed-source cluster      → cluster includes breach + listing + infra
//   6. severity_basis            → "matrix:UX_FORENSIC" on every cluster

import { strict as assert } from "node:assert";
import { describe, it } from "node:test";

import { projectTimeline } from "./timeline.ts";
import type { InvestigationEvent, InvestigationEventType } from "../types/api.ts";

// ---------------------------------------------------------------------------
// Test helpers
// ---------------------------------------------------------------------------

let seq = 0;

function mkEvent(
  event_type: InvestigationEventType,
  payload: Record<string, unknown>,
  ts: string = "2026-01-01T00:00:00Z",
): InvestigationEvent {
  seq += 1;
  return {
    event_type,
    investigation_id: "test-inv",
    run_id: null,
    sequence: seq,
    ts,
    payload,
  };
}

function mkBreachHit(breach_date: string, name: string = "Adobe"): InvestigationEvent {
  return mkEvent("breach-hit", { name, breach_date, source: "intelbase" });
}

function mkListingMatch(first_seen: string, host_name: string = "host-1"): InvestigationEvent {
  return mkEvent("listing-match", {
    source: "inside_airbnb",
    host_name,
    first_seen,
    listing_url: `https://www.airbnb.com/rooms/${host_name}`,
  });
}

function mkInfraFact(created: string, domain: string = "example.com"): InvestigationEvent {
  return mkEvent("infra-fact", { domain, created });
}

const clusterFindings = (events: ReadonlyArray<InvestigationEvent>) =>
  projectTimeline(events).filter((f) => f.source === "timeline-cluster");

const overviewFindings = (events: ReadonlyArray<InvestigationEvent>) =>
  projectTimeline(events).filter((f) => f.source === "timeline-overview");

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("projectTimeline", () => {
  it("returns empty findings when no events carry temporal anchors", () => {
    // person-match has no payload date; user_scanner registrations are
    // not temporally anchored in payload. Expect zero findings.
    const events: InvestigationEvent[] = [
      mkEvent("person-match", { source: "gravatar", platform: "github" }),
      mkEvent("person-match", { source: "user_scanner", platform: "reddit" }),
    ];
    const findings = projectTimeline(events);
    assert.equal(findings.length, 0, "no dated events should produce zero findings");
  });

  it("emits one cluster finding for 3 events within a 14-day window", () => {
    const events = [
      mkBreachHit("2026-01-01"),
      mkBreachHit("2026-01-05"),
      mkBreachHit("2026-01-10"),
    ];
    const clusters = clusterFindings(events);
    assert.equal(clusters.length, 1, "3 events in a 14-day window should produce 1 cluster");
    assert.equal(clusters[0]!.severity, "info", "3-event cluster severity should be info");
    assert.ok(
      clusters[0]!.headline.includes("3 events"),
      `headline missing event count: ${clusters[0]!.headline}`,
    );
  });

  it("emits zero cluster findings when 3 events span 60 days (no 14-day window)", () => {
    const events = [
      mkBreachHit("2026-01-01"),
      mkBreachHit("2026-02-01"),
      mkBreachHit("2026-03-02"),
    ];
    const clusters = clusterFindings(events);
    assert.equal(clusters.length, 0, "events spanning 60 days should not cluster");
    // Overview Finding still fires (3 dated events, valid span).
    assert.equal(overviewFindings(events).length, 1, "overview Finding should still emit");
  });

  it("escalates cluster severity to 'warn' at 5 events in the same window", () => {
    const events = [
      mkBreachHit("2026-01-01"),
      mkListingMatch("2026-01-03"),
      mkInfraFact("2026-01-05"),
      mkBreachHit("2026-01-07"),
      mkListingMatch("2026-01-10"),
    ];
    const clusters = clusterFindings(events);
    assert.equal(clusters.length, 1, "5 events in 14-day window should produce 1 cluster");
    assert.equal(clusters[0]!.severity, "warn", "5-event cluster severity should be warn");
  });

  it("clusters across mixed event types (breach-hit + listing-match + infra-fact)", () => {
    const events = [
      mkBreachHit("2026-01-02", "LinkedIn"),
      mkListingMatch("2026-01-04", "host-A"),
      mkInfraFact("2026-01-08", "example.com"),
    ];
    const clusters = clusterFindings(events);
    assert.equal(clusters.length, 1, "mixed-source events should still cluster");
    const samples = clusters[0]!.samples ?? [];
    assert.equal(samples.length, 3, "all 3 mixed events should appear as samples");
    const labels = samples.map((s) => s.label).join(" | ");
    assert.ok(labels.includes("LinkedIn"), `expected LinkedIn in samples: ${labels}`);
    assert.ok(labels.includes("host-A"), `expected host-A in samples: ${labels}`);
    assert.ok(labels.includes("example.com"), `expected example.com in samples: ${labels}`);
  });

  it("stamps cluster severity_basis with 'matrix:UX_FORENSIC'", () => {
    const events = [
      mkBreachHit("2026-01-01"),
      mkBreachHit("2026-01-04"),
      mkBreachHit("2026-01-08"),
    ];
    const clusters = clusterFindings(events);
    assert.equal(clusters.length, 1);
    assert.equal(
      clusters[0]!.severity_basis,
      "matrix:UX_FORENSIC",
      "cluster Finding must cite the UX_FORENSIC rubric anchor",
    );
  });
});

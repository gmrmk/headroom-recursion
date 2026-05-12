// Parity smoke for the TS verdict synthesizer.
//
// Mirrors the Python tests in `tests/unit/test_verdict_synthesizer.py`:
// each scenario builds an InvestigationEvent[] that matches the
// equivalent per-adapter signal counts, asserts the SAME verdict
// bucket. The Python operates on per-adapter counts; the TS operates
// on event lists. Different input shapes, same rubric, same buckets.
//
// Run:
//   node --experimental-strip-types apps/web/tools/verdict-parity.mts

import { synthesizeVerdict } from "../src/lib/verdict.ts";
import type { InvestigationEvent } from "../src/types/api.ts";

let pass = 0;
let fail = 0;
const failures: string[] = [];

function evt(
  event_type: InvestigationEvent["event_type"],
  payload: Record<string, unknown> = {},
): InvestigationEvent {
  return {
    event_type,
    investigation_id: "test",
    run_id: "test",
    sequence: 0,
    ts: "2026-05-11T00:00:00Z",
    payload,
  };
}

function check(
  name: string,
  events: InvestigationEvent[],
  expectedBucket: string | null,
): void {
  const v = synthesizeVerdict(events);
  if (expectedBucket === null) {
    if (v === null) {
      pass += 1;
    } else {
      fail += 1;
      failures.push(`${name}: expected null verdict; got bucket=${v.bucket}`);
    }
    return;
  }
  if (v === null) {
    fail += 1;
    failures.push(`${name}: expected ${expectedBucket}; got null`);
    return;
  }
  if (v.bucket === expectedBucket) {
    pass += 1;
  } else {
    fail += 1;
    failures.push(`${name}: expected ${expectedBucket}; got ${v.bucket}`);
  }
}

// Empty event list -> null (no premature low-footprint flash).
check("empty event list", [], null);

// Only heartbeats -> still null (nothing of interest yet).
check("only heartbeats", [evt("heartbeat"), evt("heartbeat")], null);

// torvalds-like signature: Gravatar person-match + GitHub commits ->
// real-careful (high confidence). Mirrors
// test_real_careful_pattern_anchors_torvalds_case.
check(
  "torvalds-like: gravatar + github, no compromise, no consumer",
  [
    evt("person-match", { source: "gravatar" }),
    evt("person-match", { source: "github_commits" }),
    evt("person-match", { source: "github_commits" }),
    evt("tool-run-result", { source: "gravatar" }),
  ],
  "real-careful",
);

// test@example.com-like signature: github person-match + hudson_rock
// breach-hits -> compromised-real. Mirrors
// test_compromised_real_pattern_anchors_test_example_case.
check(
  "test@example.com-like: github + hudson rock stealers",
  [
    evt("person-match", { source: "github_commits" }),
    evt("breach-hit", { source: "hudson_rock" }),
    evt("breach-hit", { source: "hudson_rock" }),
    evt("tool-run-result"),
  ],
  "compromised-real",
);

// Zero hits across every leg: only tool-run-result events with no
// person-match or breach-hit -> low-footprint. Mirrors
// test_low_footprint_pattern_when_every_signal_zero.
check(
  "low-footprint: tool-run-result-only",
  [
    evt("tool-run-result", { source: "gravatar", profile_found: false }),
    evt("tool-run-result", { source: "github_commits", total_commits: 0 }),
    evt("tool-run-result", { source: "hudson_rock", stealer_count: 0 }),
    evt("tool-run-result", { source: "user_scanner", found: 0 }),
  ],
  "low-footprint",
);

// Suspicious-churn: no identity, no behavior, but compromise present.
// Mirrors test_suspicious_churn_pattern_compromise_without_identity.
check(
  "suspicious-churn: compromise without identity",
  [
    evt("breach-hit", { source: "hudson_rock" }),
    evt("breach-hit", { source: "hudson_rock" }),
    evt("tool-run-result"),
  ],
  "suspicious-churn",
);

// Real-active: identity + behavior + consumer-tail, no compromise.
// Mirrors test_real_active_when_identity_behavior_and_consumer_tail.
check(
  "real-active: identity + behavior + consumer tail",
  [
    evt("person-match", { source: "gravatar" }),
    evt("person-match", { source: "github_commits" }),
    evt("person-match", { source: "user_scanner" }),
    evt("tool-run-result"),
  ],
  "real-active",
);

// Rule precedence: compromise + real-careful pattern -> compromised-real.
// Mirrors test_compromised_real_overrides_real_careful_when_both_present.
check(
  "rule precedence: compromised-real beats real-careful",
  [
    evt("person-match", { source: "gravatar" }),
    evt("person-match", { source: "github_commits" }),
    evt("breach-hit", { source: "hudson_rock" }),
    evt("tool-run-result"),
  ],
  "compromised-real",
);

// HIBP breach-hits omit `source` -- they should still fire the
// compromise signal.
check(
  "HIBP breach-hit without source still fires compromise",
  [
    evt("breach-hit", { domain: "example.com", name: "AcmeBreach2024" }),
    evt("tool-run-result"),
  ],
  "suspicious-churn",
);

console.log(`verdict TS parity: ${pass} passed, ${fail} failed`);
if (failures.length > 0) {
  console.error("FAILURES:");
  for (const f of failures) {
    console.error("  " + f);
  }
  process.exit(1);
}
process.exit(0);

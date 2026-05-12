// Smoke test for serializeDossierMarkdown -- verifies the dossier
// contains the right sections, the verdict block, signal chips, and
// per-event-type/per-source grouping. Hermetic; no network.
//
// Run:
//   node --experimental-strip-types apps/web/tools/dossier-export-smoke.mts

import { serializeDossierMarkdown, makeDossierFilename } from "../src/lib/dossier-export.ts";
import { synthesizeVerdict } from "../src/lib/verdict.ts";
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
    investigation_id: "test",
    run_id: "test",
    sequence,
    ts: "2026-05-11T00:00:00Z",
    payload,
  };
}

function check(name: string, cond: boolean, detail = ""): void {
  if (cond) {
    pass += 1;
  } else {
    fail += 1;
    failures.push(`${name}${detail ? ": " + detail : ""}`);
  }
}

// ---------------------------------------------------------------------------
// Header + metadata block
// ---------------------------------------------------------------------------

{
  const md = serializeDossierMarkdown(
    [],
    {
      investigationId: "inv-abc-123",
      subjectKind: "email",
      subjectValue: "user@example.com",
      investigatorHandle: "alice",
      createdAt: "2026-05-11T00:00:00Z",
    },
    null,
  );
  check("header contains subject", md.includes("# Investigation Dossier — email: user@example.com"));
  check("header contains investigation id", md.includes("`inv-abc-123`"));
  check("header contains investigator handle", md.includes("**Investigator:** alice"));
  check("header contains exported timestamp", md.includes("**Exported:**"));
  check("null verdict surfaces as 'no verdict yet'", md.includes("*No verdict yet"));
  check("logless footer present", md.toLowerCase().includes("logless"));
}

// ---------------------------------------------------------------------------
// Verdict block when verdict is non-null
// ---------------------------------------------------------------------------

{
  const events: InvestigationEvent[] = [
    evt("person-match", { source: "gravatar" }, 1),
    evt("person-match", { source: "github_commits" }, 2),
    evt("tool-run-result", { source: "gravatar" }, 3),
  ];
  const verdict = synthesizeVerdict(events);
  const md = serializeDossierMarkdown(
    events,
    { investigationId: "test" },
    verdict,
  );
  check("verdict bucket appears in body", md.includes("**real-careful**"));
  check("verdict why line appears", md.includes("Owner-attested identity"));
  check("verdict next line appears", md.includes("Real long-lived person"));
  check("identity signal chip shows ✓", md.includes("✓ **identity**"));
  check("compromise signal chip shows —", md.includes("— **compromise**"));
}

// ---------------------------------------------------------------------------
// Findings grouping: event_type -> source
// ---------------------------------------------------------------------------

{
  const events: InvestigationEvent[] = [
    evt("person-match", { source: "gravatar" }, 1),
    evt("person-match", { source: "github_commits" }, 2),
    evt("person-match", { source: "github_commits" }, 3),
    evt("breach-hit", { source: "hudson_rock" }, 4),
    evt("breach-hit", { domain: "example.com" }, 5),  // HIBP-style, no source
    evt("tool-run-result", { source: "hudson_rock" }, 6),
    evt("heartbeat", {}, 7),  // should be filtered out
    evt("tool-run-accepted", {}, 8),  // should be filtered out
  ];
  const md = serializeDossierMarkdown(events, { investigationId: "test" }, null);
  check("person-match section header with count", md.includes("### person-match (3)"));
  check("breach-hit section header with count", md.includes("### breach-hit (2)"));
  check("gravatar source group", md.includes("source: `gravatar`"));
  check("github_commits source group with 2 events", md.includes("source: `github_commits` (2)"));
  check("hudson_rock source group", md.includes("source: `hudson_rock`"));
  check("HIBP unsourced bucket present", md.includes("(unsourced)"));
  check("heartbeat NOT in findings", !md.includes("heartbeat"));
  check(
    "tool-run-accepted NOT in findings",
    !md.includes("tool-run-accepted"),
  );
}

// ---------------------------------------------------------------------------
// makeDossierFilename
// ---------------------------------------------------------------------------

{
  const fn1 = makeDossierFilename(
    { investigationId: "inv-abc-123", subjectValue: "user@example.com" },
    "md",
  );
  check("filename has dossier- prefix", fn1.startsWith("dossier-"));
  check("filename has .md extension", fn1.endsWith(".md"));
  check(
    "filename sanitizes @ symbol",
    !fn1.includes("@"),
    `got '${fn1}'`,
  );

  const fn2 = makeDossierFilename({ investigationId: "inv-xyz" }, "md");
  check("filename falls back to investigation id when no subject", fn2.includes("inv-xyz"));
}

// ---------------------------------------------------------------------------
// Empty-events case
// ---------------------------------------------------------------------------

{
  const md = serializeDossierMarkdown([], { investigationId: "empty" }, null);
  check("empty events shows '*No findings*'", md.includes("*No findings to report.*"));
  check("empty events still has header", md.includes("# Investigation Dossier"));
}

console.log(`dossier-export smoke: ${pass} passed, ${fail} failed`);
if (failures.length > 0) {
  console.error("FAILURES:");
  for (const f of failures) {
    console.error("  " + f);
  }
  process.exit(1);
}
process.exit(0);

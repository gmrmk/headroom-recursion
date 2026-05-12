// Smoke test for serializeDossierHtml + collectImageRels.
//
// Pure functions only -- the async fetchImageDataUrls is browser-bound
// and verified manually. Tests verify HTML structure, verdict block,
// findings grouping, image embedding when data URLs are provided, and
// HTML escaping of user-provided values.
//
// Run:
//   node --experimental-strip-types apps/web/tools/dossier-html-smoke.mts

import {
  collectImageRels,
  serializeDossierHtml,
} from "../src/lib/dossier-export.ts";
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
  if (cond) pass += 1;
  else {
    fail += 1;
    failures.push(`${name}${detail ? ": " + detail : ""}`);
  }
}

// ---------------------------------------------------------------------------
// HTML skeleton + header
// ---------------------------------------------------------------------------

{
  const html = serializeDossierHtml(
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
  check("html starts with doctype", html.startsWith("<!doctype html>"));
  check("html has <title>", html.includes("<title>Investigation Dossier"));
  check("html has inline <style>", html.includes("<style>"));
  check("html has subject in h1", html.includes("email: user@example.com"));
  check("html has investigation id code block", html.includes("inv-abc-123"));
  check("html has investigator handle", html.includes("alice"));
  check("html has exported timestamp section", html.includes("<strong>Exported:</strong>"));
  check("null verdict shows empty message", html.includes("No verdict yet"));
  check("logless footer present", html.toLowerCase().includes("logless"));
}

// ---------------------------------------------------------------------------
// Verdict block with bucket + colored signal chips
// ---------------------------------------------------------------------------

{
  const events: InvestigationEvent[] = [
    evt("person-match", { source: "gravatar" }, 1),
    evt("person-match", { source: "github_commits" }, 2),
    evt("tool-run-result", { source: "gravatar" }, 3),
  ];
  const verdict = synthesizeVerdict(events);
  const html = serializeDossierHtml(events, { investigationId: "test" }, verdict);
  check("verdict bucket appears", html.includes("real-careful"));
  check("verdict why line appears", html.includes("Owner-attested identity"));
  check("identity chip on", html.includes('class="chip on">✓ identity'));
  check("compromise chip off", html.includes('class="chip off">— compromise'));
  check(
    "verdict colored border",
    html.includes("border-left-color: #34d399"),
    "real-careful should use green accent",
  );
}

// ---------------------------------------------------------------------------
// Findings grouping with multiple sources
// ---------------------------------------------------------------------------

{
  const events: InvestigationEvent[] = [
    evt("person-match", { source: "gravatar" }, 1),
    evt("person-match", { source: "github_commits" }, 2),
    evt("breach-hit", { source: "hudson_rock" }, 3),
    evt("heartbeat", {}, 4), // filtered out
    evt("tool-run-accepted", {}, 5), // filtered out
  ];
  const html = serializeDossierHtml(events, { investigationId: "test" }, null);
  check("h3 person-match (2)", html.includes("<h3>person-match (2)</h3>"));
  check("h3 breach-hit (1)", html.includes("<h3>breach-hit (1)</h3>"));
  check("source gravatar header", html.includes(">gravatar</code>"));
  check("source hudson_rock header", html.includes(">hudson_rock</code>"));
  check("heartbeat filtered out", !html.includes(">heartbeat"));
  check("tool-run-accepted filtered out", !html.includes(">tool-run-accepted"));
}

// ---------------------------------------------------------------------------
// Inline image evidence via data: URLs
// ---------------------------------------------------------------------------

{
  const events: InvestigationEvent[] = [
    evt(
      "image-match",
      {
        source: "ai_local_detect",
        image_url: "https://example.com/x.png",
        flipped_rel: "flipped/abc.jpg",
        ela_rel: "ela/abc.jpg",
      },
      1,
    ),
  ];
  const dataUrls = new Map<string, string>([
    ["flipped/abc.jpg", "data:image/jpeg;base64,FAKE_FLIP_DATA"],
    ["ela/abc.jpg", "data:image/jpeg;base64,FAKE_ELA_DATA"],
  ]);
  const html = serializeDossierHtml(events, { investigationId: "test" }, null, dataUrls);
  check(
    "html embeds flipped image data URL",
    html.includes("FAKE_FLIP_DATA"),
    "expected the data: URL to appear inside an <img> src",
  );
  check("html embeds ela image data URL", html.includes("FAKE_ELA_DATA"));
  check("html has evidence-grid container", html.includes("evidence-grid"));
  check("html marks images lazy-loaded", html.includes('loading="lazy"'));
}

// ---------------------------------------------------------------------------
// collectImageRels gathers every *_rel value uniquely
// ---------------------------------------------------------------------------

{
  const events: InvestigationEvent[] = [
    evt("image-match", { flipped_rel: "flipped/a.jpg" }, 1),
    evt("image-match", { ela_rel: "ela/a.jpg" }, 2),
    evt("image-match", { flipped_rel: "flipped/a.jpg" }, 3), // duplicate
    evt("image-match", { flipped_rel: "flipped/b.jpg", ela_rel: "ela/b.jpg" }, 4),
    evt("image-match", { something_rel: "x/y.png" }, 5),
    evt("image-match", { not_a_rel_key: "ignored" }, 6),
    evt("image-match", { flipped_rel: "" }, 7), // empty -> skipped
  ];
  const rels = collectImageRels(events);
  const set = new Set(rels);
  check("collects flipped/a.jpg", set.has("flipped/a.jpg"));
  check("collects ela/a.jpg", set.has("ela/a.jpg"));
  check("collects flipped/b.jpg", set.has("flipped/b.jpg"));
  check("collects ela/b.jpg", set.has("ela/b.jpg"));
  check("collects something_rel x/y.png", set.has("x/y.png"));
  check("deduplicates flipped/a.jpg", rels.filter((r) => r === "flipped/a.jpg").length === 1);
  check("ignores non-_rel keys", !set.has("ignored"));
  check("ignores empty _rel values", !set.has(""));
}

// ---------------------------------------------------------------------------
// HTML-escape user-provided values
// ---------------------------------------------------------------------------

{
  const html = serializeDossierHtml(
    [],
    {
      investigationId: "<script>alert(1)</script>",
      subjectKind: "email",
      subjectValue: 'evil"<>&',
    },
    null,
  );
  check("escapes script tag in investigation id", !html.includes("<script>alert(1)"));
  check("escapes quotes in subject", !html.includes('evil"<>&'));
  check(
    "encoded entities present for subject value",
    html.includes("evil&quot;&lt;&gt;&amp;"),
    "expected &quot;&lt;&gt;&amp; encoding",
  );
}

console.log(`dossier-html smoke: ${pass} passed, ${fail} failed`);
if (failures.length > 0) {
  console.error("FAILURES:");
  for (const f of failures) console.error("  " + f);
  process.exit(1);
}
process.exit(0);

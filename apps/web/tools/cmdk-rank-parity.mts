// Parity smoke for the TS port of the cmd-K ranker (ADR-0017 §4 WI-0606).
//
// Loads the same fixture the Python tests load, runs every case through
// the TS ranker, asserts the SAME expectations. If this script exits 0,
// the TS port behaves identically to the Python ranker on every fixture
// case -- the parity contract is honored.
//
// Run:
//   node --experimental-strip-types apps/web/tools/cmdk-rank-parity.mts
// (Node >=22 has built-in TS strip; tested on 24.)

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

import { rank, type RankCandidate, type RankContext } from "../src/lib/cmdk-rank.ts";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const FIXTURE_PATH = resolve(
  __dirname,
  "../../../tests/golden_path_e2e/cmdk_ranking_fixture.json",
);

interface Case {
  readonly id: number | string;
  readonly query: string;
  readonly context_subject?: string | null;
  readonly warm?: boolean;
  readonly opsec_state?: string;
  readonly expected_top?: string | null;
  readonly expected_top_in?: string[];
  readonly expected_top_after?: string;
}

interface Fixture {
  readonly candidates: RankCandidate[];
  readonly cases: Case[];
  readonly negative_cases: { id: string; query: string }[];
}

const fixture: Fixture = JSON.parse(readFileSync(FIXTURE_PATH, "utf-8"));

let pass = 0;
let fail = 0;
const failures: string[] = [];

for (const c of fixture.cases) {
  const ctx: RankContext = {
    subject: c.context_subject ?? null,
    warm: c.warm ?? false,
    opsec_state: c.opsec_state ?? "green",
  };
  const ranked = rank(c.query, fixture.candidates, ctx);
  let ok = false;
  let msg = "";
  if (c.expected_top_in) {
    ok = ranked.length > 0 && c.expected_top_in.includes(ranked[0]!.id);
    msg = `expected top in ${JSON.stringify(c.expected_top_in)}; got ${ranked[0]?.id ?? "<empty>"}`;
  } else if (c.expected_top_after) {
    ok = ranked.some((r) => r.id === c.expected_top_after);
    msg = `expected '${c.expected_top_after}' in ranked; got ${ranked.map((r) => r.id).join(",")}`;
  } else if (c.expected_top === null || c.expected_top === undefined) {
    ok = ranked.length === 0 || ranked.every((r) => r.score < 0.1);
    msg = `expected empty/low-score; got ${ranked.slice(0, 3).map((r) => `${r.id}:${r.score.toFixed(2)}`).join(",")}`;
  } else {
    ok = ranked.length > 0 && ranked[0]!.id === c.expected_top;
    msg = `expected '${c.expected_top}'; got ${ranked[0]?.id ?? "<empty>"} (score=${ranked[0]?.score?.toFixed(2) ?? "-"})`;
  }
  if (ok) {
    pass += 1;
  } else {
    fail += 1;
    failures.push(`case ${c.id} q=${JSON.stringify(c.query)} ctx=${JSON.stringify(ctx)}: ${msg}`);
  }
}

for (const neg of fixture.negative_cases) {
  const ranked = rank(neg.query, fixture.candidates, null);
  if (ranked.length === 0) {
    pass += 1;
  } else {
    fail += 1;
    failures.push(`negative ${neg.id} q=${JSON.stringify(neg.query)}: expected []; got ${ranked.length} matches`);
  }
}

console.log(`cmdk-rank TS parity: ${pass} passed, ${fail} failed`);
if (failures.length > 0) {
  console.error("FAILURES:");
  for (const f of failures) {
    console.error("  " + f);
  }
  process.exit(1);
}
process.exit(0);

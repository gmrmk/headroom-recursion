// Smoke test for workflow-routing -- deterministic mapping from
// filled investigation fields to dispatched workflow ids. Pins the
// rules so silent regressions can't ship.
//
// Run:
//   node --experimental-strip-types apps/web/tools/workflow-routing-smoke.mts

import {
  hasInvestigatableFields,
  routeWorkflows,
} from "../src/lib/workflow-routing.ts";

let pass = 0;
let fail = 0;
const failures: string[] = [];

function check(name: string, cond: boolean, detail = ""): void {
  if (cond) pass += 1;
  else {
    fail += 1;
    failures.push(`${name}${detail ? ": " + detail : ""}`);
  }
}

function ids(selections: ReadonlyArray<{ id: string }>): string[] {
  return selections.map((s) => s.id);
}

// ---------------------------------------------------------------------------
// Empty fields -> no workflows
// ---------------------------------------------------------------------------

{
  const selections = routeWorkflows({});
  check("empty fields -> no workflows", selections.length === 0);
  check("hasInvestigatableFields false on empty",
    hasInvestigatableFields({}) === false);
}

{
  const selections = routeWorkflows({ notes: "just some notes" });
  check("notes alone -> no workflows", selections.length === 0);
}

// ---------------------------------------------------------------------------
// Single primitives -> single workflow
// ---------------------------------------------------------------------------

{
  check(
    "email alone -> w11.em",
    ids(routeWorkflows({ email: "user@example.com" })).join(",") === "w11.em",
  );
  check(
    "ip alone -> w10.ip",
    ids(routeWorkflows({ ip: "8.8.8.8" })).join(",") === "w10.ip",
  );
  check(
    "phone alone -> w3.ph",
    ids(routeWorkflows({ phone: "+12175550123" })).join(",") === "w3.ph",
  );
  check(
    "domain alone -> w5.do",
    ids(routeWorkflows({ domain: "example.com" })).join(",") === "w5.do",
  );
  check(
    "username alone -> w1.un",
    ids(routeWorkflows({ username: "alice" })).join(",") === "w1.un",
  );
  check(
    "photo_url alone (no address) -> w4.im",
    ids(routeWorkflows({ photo_url: "https://x/y.jpg" })).join(",") === "w4.im",
  );
}

// ---------------------------------------------------------------------------
// Address -> w9.pv (umbrella). Photo_url alongside doesn't double-dispatch w4.im.
// ---------------------------------------------------------------------------

{
  const sel1 = routeWorkflows({ address: "123 Main St" });
  check("address alone -> w9.pv only", ids(sel1).join(",") === "w9.pv");
  check(
    "w9.pv seed carries address",
    sel1[0]?.seed.address === "123 Main St",
  );

  const sel2 = routeWorkflows({
    address: "123 Main St",
    photo_url: "https://x/y.jpg",
  });
  check(
    "address + photo_url -> w9.pv only (no w4.im double-dispatch)",
    ids(sel2).join(",") === "w9.pv",
  );

  const sel3 = routeWorkflows({
    address: "123 Main St",
    host_name: "Alice",
    photo_url: "https://x/y.jpg",
    email: "alice@example.com",
  });
  check(
    "full property + email -> w9.pv + w11.em",
    ids(sel3).join(",") === "w9.pv,w11.em",
  );
  check(
    "w11.em seed only carries email",
    sel3[1]?.seed.email === "alice@example.com" && !sel3[1]?.seed.address,
  );
}

// ---------------------------------------------------------------------------
// Multiple primitives -> multiple workflows in parallel
// ---------------------------------------------------------------------------

{
  const sel = routeWorkflows({
    email: "u@example.com",
    ip: "1.2.3.4",
    domain: "example.com",
    username: "alice",
  });
  check(
    "email + ip + domain + username -> 4 workflows",
    sel.length === 4,
    `got ${ids(sel).join(",")}`,
  );
  check("email -> w11.em present", ids(sel).includes("w11.em"));
  check("ip -> w10.ip present", ids(sel).includes("w10.ip"));
  check("domain -> w5.do present", ids(sel).includes("w5.do"));
  check("username -> w1.un present", ids(sel).includes("w1.un"));
}

// ---------------------------------------------------------------------------
// Whitespace-only values treated as empty
// ---------------------------------------------------------------------------

{
  const sel = routeWorkflows({ email: "   ", ip: "\t" });
  check("whitespace-only fields -> 0 workflows", sel.length === 0);
}

// ---------------------------------------------------------------------------
// Each selection carries a `why` rationale and a seed dict
// ---------------------------------------------------------------------------

{
  const sel = routeWorkflows({ email: "u@example.com" });
  check("selection has non-empty why", sel[0]?.why.length > 0);
  check("selection has non-empty label", sel[0]?.label.length > 0);
  check("w11.em seed has email key", typeof sel[0]?.seed.email === "string");
}

console.log(`workflow-routing smoke: ${pass} passed, ${fail} failed`);
if (failures.length > 0) {
  console.error("FAILURES:");
  for (const f of failures) console.error("  " + f);
  process.exit(1);
}
process.exit(0);

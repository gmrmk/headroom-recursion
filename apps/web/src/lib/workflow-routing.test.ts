// workflow-routing.test.ts — unit tests for the deterministic field
// → workflow id routing layer. Co-located with `workflow-routing.ts`.
//
// Runner: `node:test` (Node 22+). Run with type-stripping:
//   node --test --experimental-strip-types src/lib/workflow-routing.test.ts
//
// Coverage focus: the new w13.dk dork-sweep rule + regression checks
// that the existing w-id rules didn't move.

import { strict as assert } from "node:assert";
import { describe, it } from "node:test";

import { routeWorkflows } from "./workflow-routing.ts";

function ids(fields: Parameters<typeof routeWorkflows>[0]): string[] {
  return routeWorkflows(fields).map((s) => s.id);
}

describe("routeWorkflows — empty / nothing-to-do", () => {
  it("returns no selections when no fields are filled", () => {
    assert.deepEqual(ids({}), []);
  });

  it("treats whitespace-only fields as empty", () => {
    assert.deepEqual(ids({ email: "   ", username: "\t" }), []);
  });
});

describe("routeWorkflows — single-identifier dispatch includes w13.dk", () => {
  it("email-only → w11.em + w13.dk", () => {
    const list = ids({ email: "alice@example.com" });
    assert.ok(list.includes("w11.em"), "expected w11.em");
    assert.ok(list.includes("w13.dk"), "expected w13.dk");
  });

  it("username-only → w1.un + w13.dk", () => {
    const list = ids({ username: "alice" });
    assert.ok(list.includes("w1.un"));
    assert.ok(list.includes("w13.dk"));
  });

  it("phone-only → w3.ph + w13.dk", () => {
    const list = ids({ phone: "+12175550123" });
    assert.ok(list.includes("w3.ph"));
    assert.ok(list.includes("w13.dk"));
  });

  it("domain-only → w5.do + w13.dk", () => {
    const list = ids({ domain: "example.com" });
    assert.ok(list.includes("w5.do"));
    assert.ok(list.includes("w13.dk"));
  });

  it("host_name-only → w13.dk only (no other identifier workflow fires)", () => {
    const list = ids({ host_name: "Alice Smith" });
    assert.ok(list.includes("w13.dk"));
    assert.equal(list.length, 1, `host_name alone shouldn't trigger ${list.join(",")}`);
  });

  it("ip-only does NOT include w13.dk (ip isn't a dork seed)", () => {
    const list = ids({ ip: "8.8.8.8" });
    assert.ok(list.includes("w10.ip"));
    assert.ok(!list.includes("w13.dk"), "w13.dk shouldn't dispatch on IP alone");
  });
});

describe("routeWorkflows — w13.dk seed mapping", () => {
  it("maps host_name → name on the seed (per workflow template)", () => {
    const dork = routeWorkflows({ host_name: "Alice Smith" }).find(
      (s) => s.id === "w13.dk",
    );
    assert.ok(dork, "w13.dk should be present");
    assert.equal(dork.seed.name, "Alice Smith");
  });

  it("passes through all identifier fields when present", () => {
    const dork = routeWorkflows({
      host_name: "Alice",
      email: "a@b.com",
      phone: "+1 555 0123",
      domain: "example.com",
      username: "alice",
      address: "1 Main St",
    }).find((s) => s.id === "w13.dk");
    assert.ok(dork);
    assert.equal(dork.seed.name, "Alice");
    assert.equal(dork.seed.email, "a@b.com");
    assert.equal(dork.seed.domain, "example.com");
    assert.equal(dork.seed.username, "alice");
    assert.equal(dork.seed.address, "1 Main St");
  });

  it("rationale reports the count of filled identifiers", () => {
    const dork = routeWorkflows({
      email: "a@b.com",
      username: "alice",
    }).find((s) => s.id === "w13.dk");
    assert.ok(dork);
    assert.match(dork.why, /2 identifiers/);
  });
});

describe("routeWorkflows — regression checks (existing rules unchanged)", () => {
  it("address triggers w9.pv (property-vetting umbrella)", () => {
    const list = ids({ address: "1 Main St" });
    assert.ok(list.includes("w9.pv"));
  });

  it("photo_url alone (no address) triggers w4.im", () => {
    const list = ids({ photo_url: "https://example.com/img.jpg" });
    assert.ok(list.includes("w4.im"));
    assert.ok(!list.includes("w9.pv"));
  });

  it("address + photo_url triggers w9.pv (umbrella), not w4.im", () => {
    const list = ids({
      address: "1 Main St",
      photo_url: "https://example.com/img.jpg",
    });
    assert.ok(list.includes("w9.pv"));
    assert.ok(!list.includes("w4.im"));
  });
});

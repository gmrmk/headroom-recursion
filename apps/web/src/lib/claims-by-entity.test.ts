// claims-by-entity.test.ts -- unit tests for W4-CLAIMS-BY-ENTITY
// (Sprint-5 keystone). Co-located with `claims-by-entity.ts` per the
// wave-3 many-small-files discipline; mirrors the test harness convention
// established by `entity-canonicalization.test.ts`.
//
// Runner: `node:test` (built into Node 22+). Run with type-stripping:
//   node --test --experimental-strip-types src/lib/claims-by-entity.test.ts

import { strict as assert } from "node:assert";
import { describe, it } from "node:test";

import { projectClaimsByEntity } from "./claims-by-entity.ts";
import type { Finding } from "./dossier-shape.ts";
import type { InvestigationEvent } from "../types/api.ts";

// ---------------------------------------------------------------------------
// Test factories
// ---------------------------------------------------------------------------

let seq = 0;
function ev(
  event_type: InvestigationEvent["event_type"],
  payload: Record<string, unknown>,
): InvestigationEvent {
  seq += 1;
  return {
    event_type,
    investigation_id: "test-inv",
    run_id: null,
    sequence: seq,
    ts: "2026-05-12T00:00:00Z",
    payload,
  };
}

// Convenience: geocode event with an address.
function geocode(address: string): InvestigationEvent {
  return ev("geocode-match", { source: "nominatim", display_name: address });
}

// Convenience: listing event with an address (Inside Airbnb shape).
function listing(address: string, host_name?: string): InvestigationEvent {
  const payload: Record<string, unknown> = {
    source: "inside_airbnb",
    address,
  };
  if (host_name !== undefined) payload.host_name = host_name;
  return ev("listing-match", payload);
}

// Convenience: person event referencing an address.
function personAtAddress(name: string, address: string): InvestigationEvent {
  return ev("person-match", {
    source: "true_people_search",
    name,
    address,
  });
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("projectClaimsByEntity", () => {
  it("emits 0 findings on empty events + empty findings (empty-day-one check)", () => {
    const findings = projectClaimsByEntity([], []);
    assert.deepEqual(findings, []);
  });

  it("emits 0 findings when no entity has >=2 distinct sources", () => {
    // Single source per entity -- nothing collapses.
    const events = [
      geocode("123 Main St, Springfield"),
      ev("person-match", { source: "gravatar", name: "Alice Smith" }),
    ];
    const findings = projectClaimsByEntity(events, []);
    assert.equal(findings.length, 0);
  });

  it("emits 1 finding when 2 distinct sources hit the same address", () => {
    const events = [
      geocode("123 Main Street, Springfield 12345"),
      listing("123 Main St, Springfield 12345"),
    ];
    const findings = projectClaimsByEntity(events, []);
    assert.equal(findings.length, 1);
    const f = findings[0]!;
    assert.match(f.headline, /Address:/);
    assert.match(f.headline, /2 sources agree/);
    assert.equal(f.source, "entity-fingerprint");
    assert.equal(f.severity_basis, "matrix:PV_ENTITY_FINGERPRINT_MATCH");
    assert.equal(f.severity, "info");
  });

  it("emits 0 findings when 2 events cite DIFFERENT addresses (each has 1 source)", () => {
    const events = [
      geocode("123 Main St, Springfield 12345"),
      listing("456 Oak Ave, Riverdale 67890"),
    ];
    const findings = projectClaimsByEntity(events, []);
    assert.equal(findings.length, 0);
  });

  it("collapses case-variation: '123 Main St' + '123 main street' -> 1 entity, 1 finding", () => {
    const events = [
      geocode("123 Main St"),
      listing("123 main street"),
    ];
    const findings = projectClaimsByEntity(events, []);
    assert.equal(findings.length, 1);
    assert.match(findings[0]!.headline, /2 sources agree/);
  });

  it("escalates severity to 'warn' when >=3 sources concur", () => {
    const events = [
      geocode("123 Main St, Springfield 12345"),
      listing("123 Main Street, Springfield 12345"),
      personAtAddress("Alice Smith", "123 Main St, Springfield 12345"),
    ];
    const findings = projectClaimsByEntity(events, []);
    // person-match contributes BOTH a person entity and an address entity.
    // The address cluster should have 3 distinct sources.
    const addrFinding = findings.find((f) => f.headline.startsWith("Address:"));
    assert.ok(addrFinding !== undefined, "expected address cluster finding");
    assert.match(addrFinding.headline, /3 sources agree/);
    assert.equal(addrFinding.severity, "warn");
  });

  it("escalates severity to 'warn' for person+LLC at same address (nominee/shell signal)", () => {
    // Two sources cite the address; one of them attaches a person, the
    // other attaches an LLC. The address cluster has only 2 sources but
    // co-locates a person AND an LLC -- escalate to warn.
    const events = [
      ev("person-match", {
        source: "true_people_search",
        name: "Bob Roe",
        address: "500 Pine Rd, Townsville 11111",
      }),
      ev("listing-match", {
        source: "secretary_of_state",
        address: "500 Pine Rd, Townsville 11111",
        llc_name: "Acme Holdings LLC",
      }),
    ];
    const findings = projectClaimsByEntity(events, []);
    const addrFinding = findings.find((f) => f.headline.startsWith("Address:"));
    assert.ok(addrFinding !== undefined, "expected address cluster finding");
    assert.equal(addrFinding.severity, "warn");
  });

  it("every emitted finding carries severity_basis 'matrix:PV_ENTITY_FINGERPRINT_MATCH'", () => {
    const events = [
      geocode("123 Main St, Springfield 12345"),
      listing("123 Main St, Springfield 12345"),
      ev("person-match", {
        source: "gravatar",
        name: "Alice Smith",
      }),
      ev("listing-match", {
        source: "secretary_of_state",
        // Different address -- but person 'Alice Smith' is matched.
      }),
      ev("person-match", {
        source: "true_people_search",
        name: "Alice Smith",
      }),
    ];
    const findings = projectClaimsByEntity(events, []);
    assert.ok(findings.length > 0, "expected at least one finding");
    for (const f of findings) {
      assert.equal(f.severity_basis, "matrix:PV_ENTITY_FINGERPRINT_MATCH");
    }
  });

  it("picks up source attribution from existing Findings (cross-section evidence)", () => {
    // Only one event mentions the address structurally; a Finding from
    // another section (e.g., Property) carries the address in its text.
    // The attribution sweep should register the Finding's source against
    // the address cluster -- promoting it from 1 source to 2.
    const events = [
      geocode("742 Evergreen Terrace, Springfield 12345"),
    ];
    const existing: Finding[] = [
      {
        headline: "742 Evergreen Terrace, Springfield 12345",
        detail: "Inside Airbnb listing for this property.",
        source: "inside_airbnb",
        severity: "info",
      },
    ];
    const findings = projectClaimsByEntity(events, existing);
    assert.equal(findings.length, 1);
    assert.match(findings[0]!.headline, /2 sources agree/);
    assert.equal(findings[0]!.source, "entity-fingerprint");
  });
});

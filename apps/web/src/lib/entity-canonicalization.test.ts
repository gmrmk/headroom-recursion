// entity-canonicalization.test.ts -- unit tests for the W4-ENTITY-CANON
// library. Co-located with `entity-canonicalization.ts` per the wave-3
// many-small-files discipline.
//
// Runner: `node:test` (built into Node 22+). Run with type-stripping:
//   node --test --experimental-strip-types src/lib/entity-canonicalization.test.ts
//
// Vitest is not configured in this workspace; node:test keeps the test
// surface dependency-free until W4-CLAIMS-BY-ENTITY lands and a richer
// test harness is justified.

import { strict as assert } from "node:assert";
import { describe, it } from "node:test";

import {
  canonicalize,
  canonicalizeAddress,
  canonicalizeDomain,
  canonicalizeEmail,
  canonicalizeLLC,
  canonicalizePerson,
  canonicalizePhone,
  entityFingerprint,
} from "./entity-canonicalization.ts";

// ---------------------------------------------------------------------------
// Address
// ---------------------------------------------------------------------------

describe("canonicalizeAddress", () => {
  it("treats 'St' and 'Street' as the same address", () => {
    const a = canonicalizeAddress("123 Main St");
    const b = canonicalizeAddress("123 Main Street");
    assert.equal(a.canonical_id, b.canonical_id);
  });

  it("is case-insensitive (UPPER vs lower)", () => {
    const a = canonicalizeAddress("123 MAIN STREET");
    const b = canonicalizeAddress("123 main street");
    assert.equal(a.canonical_id, b.canonical_id);
  });

  it("collapses repeated whitespace and tabs", () => {
    const a = canonicalizeAddress("123   Main\tStreet");
    const b = canonicalizeAddress("123 Main Street");
    assert.equal(a.canonical_id, b.canonical_id);
  });

  it("normalizes apartment unit suffix ('Apt 4B' vs '#4B')", () => {
    const a = canonicalizeAddress("123 Main Street Apt 4B");
    const b = canonicalizeAddress("123 Main Street #4B");
    assert.equal(a.canonical_id, b.canonical_id);
  });

  it("normalizes ZIP+4 to 5-digit and prefixes canonical_id", () => {
    const a = canonicalizeAddress("123 Main Street, Springfield 12345-6789");
    const b = canonicalizeAddress("123 Main Street, Springfield 12345");
    assert.equal(a.canonical_id, b.canonical_id);
    assert.ok(a.canonical_id.startsWith("12345|"), `got ${a.canonical_id}`);
  });

  it("falls back to street-only canonical_id when ZIP absent", () => {
    const fp = canonicalizeAddress("123 Main Street");
    assert.ok(!fp.canonical_id.includes("|"));
    assert.ok(fp.canonical_id.includes("main street"));
  });

  it("returns empty canonical_id on empty input", () => {
    const fp = canonicalizeAddress("");
    assert.equal(fp.canonical_id, "");
  });
});

// ---------------------------------------------------------------------------
// Person
// ---------------------------------------------------------------------------

describe("canonicalizePerson", () => {
  it("strips title prefix and suffix ('Dr. John Smith Jr.' -> 'john smith')", () => {
    const fp = canonicalizePerson("Dr. John Smith Jr.");
    assert.equal(fp.canonical_id, "john smith");
  });

  it("collapses case variation", () => {
    const a = canonicalizePerson("JOHN SMITH");
    const b = canonicalizePerson("john smith");
    assert.equal(a.canonical_id, b.canonical_id);
  });

  it("strips PhD / Esq variants", () => {
    const a = canonicalizePerson("Jane Doe, PhD");
    const b = canonicalizePerson("jane doe esq");
    const c = canonicalizePerson("Jane Doe");
    assert.equal(a.canonical_id, c.canonical_id);
    assert.equal(b.canonical_id, c.canonical_id);
  });

  it("handles trailing II/III roman numerals", () => {
    const a = canonicalizePerson("John Smith III");
    const b = canonicalizePerson("john smith");
    assert.equal(a.canonical_id, b.canonical_id);
  });

  it("returns empty canonical_id on empty input", () => {
    const fp = canonicalizePerson("");
    assert.equal(fp.canonical_id, "");
  });
});

// ---------------------------------------------------------------------------
// LLC
// ---------------------------------------------------------------------------

describe("canonicalizeLLC", () => {
  it("collapses 'Acme LLC' and 'Acme L.L.C.'", () => {
    const a = canonicalizeLLC("Acme LLC");
    const b = canonicalizeLLC("Acme L.L.C.");
    assert.equal(a.canonical_id, b.canonical_id);
  });

  it("strips Inc / Incorporated / Corp / Ltd", () => {
    const variants = [
      "Acme Inc",
      "Acme Incorporated",
      "Acme Corp.",
      "acme corporation",
      "Acme Ltd",
      "Acme Limited",
    ];
    const baseline = canonicalizeLLC("Acme");
    for (const v of variants) {
      const fp = canonicalizeLLC(v);
      assert.equal(fp.canonical_id, baseline.canonical_id, `failed on ${v}`);
    }
  });

  it("EIN hint takes precedence over name", () => {
    const a = canonicalizeLLC("Acme LLC", { ein: "12-3456789" });
    const b = canonicalizeLLC("Totally Different Name LLC", { ein: "12-3456789" });
    assert.equal(a.canonical_id, b.canonical_id);
    assert.equal(a.canonical_id, "ein:123456789");
  });

  it("state-id hint takes precedence over name when EIN absent", () => {
    const a = canonicalizeLLC("Acme LLC", { stateId: "DE-12345" });
    assert.equal(a.canonical_id, "state:de-12345");
  });
});

// ---------------------------------------------------------------------------
// Domain
// ---------------------------------------------------------------------------

describe("canonicalizeDomain", () => {
  it("treats 'www.example.com' and 'example.com' as the same", () => {
    const a = canonicalizeDomain("www.example.com");
    const b = canonicalizeDomain("example.com");
    assert.equal(a.canonical_id, b.canonical_id);
  });

  it("strips trailing FQDN root dot", () => {
    const a = canonicalizeDomain("example.com.");
    const b = canonicalizeDomain("example.com");
    assert.equal(a.canonical_id, b.canonical_id);
  });

  it("lowercases ('EXAMPLE.COM' -> 'example.com')", () => {
    const fp = canonicalizeDomain("EXAMPLE.COM");
    assert.equal(fp.canonical_id, "example.com");
  });
});

// ---------------------------------------------------------------------------
// Email
// ---------------------------------------------------------------------------

describe("canonicalizeEmail", () => {
  it("strips plus-tag and Gmail dots", () => {
    const a = canonicalizeEmail("a.lice+spam@gmail.com");
    const b = canonicalizeEmail("alice@gmail.com");
    assert.equal(a.canonical_id, b.canonical_id);
  });

  it("preserves dots in local-part for non-Gmail domains", () => {
    const a = canonicalizeEmail("a.lice@example.com");
    const b = canonicalizeEmail("alice@example.com");
    assert.notEqual(a.canonical_id, b.canonical_id);
  });

  it("lowercases the entire address", () => {
    const fp = canonicalizeEmail("ALICE@EXAMPLE.COM");
    assert.equal(fp.canonical_id, "alice@example.com");
  });

  it("handles malformed input gracefully (no @)", () => {
    const fp = canonicalizeEmail("not-an-email");
    assert.equal(fp.canonical_id, "not-an-email");
    assert.ok(fp.normalization_notes?.includes("malformed_no_at"));
  });

  it("treats googlemail.com as Gmail-equivalent for dot-stripping", () => {
    const a = canonicalizeEmail("a.l.i.c.e@googlemail.com");
    assert.equal(a.canonical_id, "alice@googlemail.com");
  });
});

// ---------------------------------------------------------------------------
// Phone
// ---------------------------------------------------------------------------

describe("canonicalizePhone", () => {
  it("collapses formatted and unformatted US numbers", () => {
    const a = canonicalizePhone("(415) 555-1212");
    const b = canonicalizePhone("4155551212");
    assert.equal(a.canonical_id, b.canonical_id);
  });

  it("normalizes US country code (1+10 digits -> +1...)", () => {
    const a = canonicalizePhone("+1 415 555 1212");
    const b = canonicalizePhone("1-415-555-1212");
    assert.equal(a.canonical_id, b.canonical_id);
    assert.equal(a.canonical_id, "+14155551212");
  });

  it("returns empty canonical_id when input has no digits", () => {
    const fp = canonicalizePhone("call me maybe");
    assert.equal(fp.canonical_id, "");
  });
});

// ---------------------------------------------------------------------------
// Dispatcher + fingerprint composition
// ---------------------------------------------------------------------------

describe("canonicalize dispatcher", () => {
  it("dispatches to the right kind", () => {
    const addr = canonicalize("address", "123 Main St");
    const dir = canonicalizeAddress("123 Main St");
    assert.equal(addr.canonical_id, dir.canonical_id);
  });

  it("entityFingerprint composes '<kind>:<canonical_id>'", () => {
    const fp = canonicalize("domain", "WWW.Example.com");
    assert.equal(entityFingerprint(fp), "domain:example.com");
  });
});

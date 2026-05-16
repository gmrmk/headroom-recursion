// entity-canonicalization.ts -- per-claim canonical-identifier extraction.
//
// Wave-4 W4-ENTITY-CANON. The Sprint-5 W4-CLAIMS-BY-ENTITY projection
// depends on this primitive: two findings about the same real-world
// object (address, person, LLC) collapse to one entity-fingerprint row
// in the dossier's claims_by_entity section.
//
// Iris IA sign-off (load-bearing gate per Margaret roadmap §4
// W4-ENTITY-CANON card, line 562): the canonical fingerprint format
// `<kind>:<canonical_value>` is ratified in
// `phase6/wave4/iris-deliberation-wave4.md` §C.5 lines 328-336, where
// the `extractEntityFingerprint` sketch returns
// `"person:Jane Doe" | "listing:abnb-42424" | "address:123 Main St, City" | null`.
//
// SCOPE NOTE (Iris open-question §6, line 448): canonicalization here is
// strictly intra-investigation. Two fingerprints with the same string
// inside one investigation collapse to one entity; the same string in a
// different investigation is NOT assumed to refer to the same real-world
// object. Cross-investigation entity resolution is the deferred
// `actor_dossier` architectural decision (wave-3 §B.3) and lives
// outside this module.
//
// USAGE
//   import { canonicalize, entityFingerprint } from "@/lib/entity-canonicalization";
//   const fp = canonicalize("address", "123 Main St, Apt 4B, Springfield 12345-6789");
//   const key = entityFingerprint(fp); // -> "address:12345|123 main street #4b"

// ---------------------------------------------------------------------------
// Public types
// ---------------------------------------------------------------------------

export type EntityKind =
  | "address"
  | "person"
  | "llc"
  | "domain"
  | "email"
  | "phone";

export interface EntityFingerprint {
  readonly kind: EntityKind;
  /** Stable, lowercase, normalized identifier. Empty string on empty input. */
  readonly canonical_id: string;
  /** Original input string, verbatim. */
  readonly raw: string;
  /** Optional debug breadcrumbs documenting what was stripped or rewritten. */
  readonly normalization_notes?: ReadonlyArray<string>;
}

/**
 * Optional structured hints. When an LLC has an EIN or a state filing id,
 * it is the strongest canonical key. When an address has a parsed ZIP, it
 * is preferred over street-name-only matching.
 */
export interface CanonicalizeHints {
  readonly ein?: string;
  readonly stateId?: string;
}

// ---------------------------------------------------------------------------
// Shared helpers
// ---------------------------------------------------------------------------

const WHITESPACE = /\s+/g;

function collapseWhitespace(s: string): string {
  return s.replace(WHITESPACE, " ").trim();
}

function fingerprint(
  kind: EntityKind,
  raw: string,
  canonical_id: string,
  notes: ReadonlyArray<string>,
): EntityFingerprint {
  // exactOptionalPropertyTypes: only attach normalization_notes when non-empty.
  return notes.length === 0
    ? { kind, canonical_id, raw }
    : { kind, canonical_id, raw, normalization_notes: notes };
}

// ---------------------------------------------------------------------------
// Address
// ---------------------------------------------------------------------------
//
// Rules (Margaret §4 W4-ENTITY-CANON):
//   - lowercase, trim, collapse whitespace
//   - expand common abbreviations (St->Street, Ave->Avenue, Blvd->Boulevard, Apt-># )
//   - normalize US ZIP to 5-digit (drop +4)
//   - ZIP-prefixed canonical_id when ZIP present, else street-keyed

const ADDRESS_ABBREVIATIONS: ReadonlyArray<readonly [RegExp, string]> = [
  [/\bst\.?\b/g, "street"],
  [/\bave\.?\b/g, "avenue"],
  [/\bblvd\.?\b/g, "boulevard"],
  [/\brd\.?\b/g, "road"],
  [/\bdr\.?\b/g, "drive"],
  [/\bln\.?\b/g, "lane"],
  [/\bct\.?\b/g, "court"],
  [/\bpl\.?\b/g, "place"],
  [/\bpkwy\.?\b/g, "parkway"],
  [/\bhwy\.?\b/g, "highway"],
  [/\bapt\.?\b/g, "#"],
  [/\bunit\b/g, "#"],
  [/\bsuite\b/g, "#"],
  [/\bste\.?\b/g, "#"],
];

const ZIP_RX = /\b(\d{5})(?:-\d{4})?\b/;

export function canonicalizeAddress(raw: string): EntityFingerprint {
  const notes: string[] = [];
  if (raw.length === 0) {
    return fingerprint("address", raw, "", notes);
  }

  let working = raw.toLowerCase();
  const before = working;
  working = collapseWhitespace(working);
  if (working !== before.trim()) {
    notes.push("whitespace_collapsed");
  }

  for (const [pattern, replacement] of ADDRESS_ABBREVIATIONS) {
    if (pattern.test(working)) {
      working = working.replace(pattern, replacement);
      notes.push(`abbrev_expanded:${replacement}`);
    }
  }

  // Pull ZIP and strip +4 if present.
  const zipMatch = working.match(ZIP_RX);
  let zip = "";
  if (zipMatch !== null && zipMatch[1] !== undefined) {
    zip = zipMatch[1];
    if (zipMatch[0] !== zip) {
      notes.push("zip_plus4_dropped");
    }
  }

  // After abbrev expansion, recollapse (apt -> # introduces stray spaces).
  working = collapseWhitespace(working);
  // Normalize "# 4b" -> "#4b" so "Apt 4B" matches "#4B".
  working = working.replace(/#\s+/g, "#");

  // Strip the literal ZIP from the street portion so we don't double-count it.
  const street = zip === "" ? working : collapseWhitespace(working.replace(ZIP_RX, ""));
  // Strip trailing punctuation that survives normalization (commas, periods).
  const streetClean = street.replace(/[,\.]+/g, " ").replace(WHITESPACE, " ").trim();

  const canonical_id = zip === "" ? streetClean : `${zip}|${streetClean}`;
  return fingerprint("address", raw, canonical_id, notes);
}

// ---------------------------------------------------------------------------
// Person
// ---------------------------------------------------------------------------
//
// Rules: lowercase; strip titles (Mr./Mrs./Dr./PhD/Esq./Jr./Sr./II/III);
// trim; collapse whitespace; first-last only.

const PERSON_TITLES_PREFIX = new Set([
  "mr",
  "mr.",
  "mrs",
  "mrs.",
  "ms",
  "ms.",
  "miss",
  "dr",
  "dr.",
  "prof",
  "prof.",
  "sir",
  "madam",
]);

const PERSON_TITLES_SUFFIX = new Set([
  "jr",
  "jr.",
  "sr",
  "sr.",
  "ii",
  "iii",
  "iv",
  "phd",
  "ph.d",
  "ph.d.",
  "md",
  "m.d.",
  "esq",
  "esq.",
  "esquire",
  "cpa",
  "dds",
]);

export function canonicalizePerson(raw: string): EntityFingerprint {
  const notes: string[] = [];
  if (raw.length === 0) {
    return fingerprint("person", raw, "", notes);
  }

  const lowered = collapseWhitespace(raw.toLowerCase());
  // Tokenize on whitespace, then drop leading titles and trailing suffixes.
  const tokens = lowered.split(" ").filter((t) => t.length > 0);

  // Strip trailing commas attached to suffix tokens ("Smith, Jr.").
  const cleaned = tokens.map((t) => t.replace(/[,]+$/g, ""));

  let start = 0;
  while (start < cleaned.length) {
    const tok = cleaned[start];
    if (tok !== undefined && PERSON_TITLES_PREFIX.has(tok)) {
      notes.push(`title_prefix_stripped:${tok}`);
      start += 1;
    } else {
      break;
    }
  }
  let end = cleaned.length;
  while (end > start) {
    const tok = cleaned[end - 1];
    if (tok !== undefined && PERSON_TITLES_SUFFIX.has(tok)) {
      notes.push(`title_suffix_stripped:${tok}`);
      end -= 1;
    } else {
      break;
    }
  }
  const core = cleaned.slice(start, end);
  const canonical_id = core.join(" ");
  return fingerprint("person", raw, canonical_id, notes);
}

// ---------------------------------------------------------------------------
// LLC
// ---------------------------------------------------------------------------
//
// Rules: lowercase; strip LLC/L.L.C./Inc/Incorporated/Corp/Ltd suffix;
// EIN-keyed if EIN hint given, else state-id-keyed if state-id hint given,
// else name-only.

const LLC_SUFFIX_RX =
  /[\s,]*\b(l\.?l\.?c\.?|inc\.?|incorporated|corp\.?|corporation|co\.?|ltd\.?|limited|llp\.?|lp\.?|plc\.?|gmbh)\.?$/gi;

export function canonicalizeLLC(
  raw: string,
  hints: CanonicalizeHints = {},
): EntityFingerprint {
  const notes: string[] = [];
  if (raw.length === 0 && hints.ein === undefined && hints.stateId === undefined) {
    return fingerprint("llc", raw, "", notes);
  }

  if (hints.ein !== undefined && hints.ein.length > 0) {
    const ein = hints.ein.replace(/\D+/g, "");
    notes.push("ein_keyed");
    return fingerprint("llc", raw, `ein:${ein}`, notes);
  }
  if (hints.stateId !== undefined && hints.stateId.length > 0) {
    const sid = hints.stateId.trim().toLowerCase();
    notes.push("state_id_keyed");
    return fingerprint("llc", raw, `state:${sid}`, notes);
  }

  let working = collapseWhitespace(raw.toLowerCase());
  const beforeSuffix = working;
  // Strip suffix (possibly multiple times if "Acme Inc Corp").
  while (LLC_SUFFIX_RX.test(working)) {
    working = working.replace(LLC_SUFFIX_RX, "");
  }
  working = collapseWhitespace(working).replace(/[,\.]+$/g, "").trim();
  if (working !== beforeSuffix) {
    notes.push("suffix_stripped");
  }

  return fingerprint("llc", raw, working, notes);
}

// ---------------------------------------------------------------------------
// Domain
// ---------------------------------------------------------------------------
//
// Rules: lowercase; strip leading `www.`; strip trailing `.` (FQDN root).

export function canonicalizeDomain(raw: string): EntityFingerprint {
  const notes: string[] = [];
  if (raw.length === 0) {
    return fingerprint("domain", raw, "", notes);
  }
  let working = raw.toLowerCase().trim();
  if (working.endsWith(".")) {
    working = working.slice(0, -1);
    notes.push("fqdn_root_dot_stripped");
  }
  if (working.startsWith("www.")) {
    working = working.slice(4);
    notes.push("www_prefix_stripped");
  }
  return fingerprint("domain", raw, working, notes);
}

// ---------------------------------------------------------------------------
// Email
// ---------------------------------------------------------------------------
//
// Rules: lowercase; strip plus-tag (`a+tag@b` -> `a@b`); Gmail special-case
// strips dots in local-part (Google treats them as equivalent).

const GMAIL_DOMAINS = new Set(["gmail.com", "googlemail.com"]);

export function canonicalizeEmail(raw: string): EntityFingerprint {
  const notes: string[] = [];
  if (raw.length === 0) {
    return fingerprint("email", raw, "", notes);
  }
  const lowered = raw.toLowerCase().trim();
  const at = lowered.lastIndexOf("@");
  if (at <= 0 || at === lowered.length - 1) {
    // Malformed; return lowered as-is so callers can still collapse identical
    // garbage. Do not throw -- adapters feed this from messy upstream data.
    return fingerprint("email", raw, lowered, ["malformed_no_at"]);
  }
  let local = lowered.slice(0, at);
  const domain = lowered.slice(at + 1);

  const plusIdx = local.indexOf("+");
  if (plusIdx !== -1) {
    local = local.slice(0, plusIdx);
    notes.push("plus_tag_stripped");
  }

  if (GMAIL_DOMAINS.has(domain) && local.includes(".")) {
    local = local.replace(/\./g, "");
    notes.push("gmail_dots_stripped");
  }

  return fingerprint("email", raw, `${local}@${domain}`, notes);
}

// ---------------------------------------------------------------------------
// Phone
// ---------------------------------------------------------------------------
//
// Rules: digits-only; E.164 if country code derivable, else 10-digit US default.

export function canonicalizePhone(raw: string): EntityFingerprint {
  const notes: string[] = [];
  if (raw.length === 0) {
    return fingerprint("phone", raw, "", notes);
  }
  const hadPlus = raw.trim().startsWith("+");
  const digits = raw.replace(/\D+/g, "");
  if (digits.length === 0) {
    return fingerprint("phone", raw, "", ["no_digits"]);
  }

  // E.164: explicit `+` prefix or 11+ digits with a recognizable country code.
  if (hadPlus) {
    notes.push("e164_explicit_plus");
    return fingerprint("phone", raw, `+${digits}`, notes);
  }
  if (digits.length === 11 && digits.startsWith("1")) {
    // US/Canada with country-code 1 (no plus).
    notes.push("us_country_code_normalized");
    return fingerprint("phone", raw, `+${digits}`, notes);
  }
  if (digits.length === 10) {
    notes.push("us_default_10_digit");
    return fingerprint("phone", raw, digits, notes);
  }
  // Fallback: return digits-only.
  notes.push("digits_only_fallback");
  return fingerprint("phone", raw, digits, notes);
}

// ---------------------------------------------------------------------------
// Dispatcher + composition helper
// ---------------------------------------------------------------------------

/** Dispatch on kind. Hints are honored for kinds that accept them (currently `llc`). */
export function canonicalize(
  kind: EntityKind,
  raw: string,
  hints: CanonicalizeHints = {},
): EntityFingerprint {
  switch (kind) {
    case "address":
      return canonicalizeAddress(raw);
    case "person":
      return canonicalizePerson(raw);
    case "llc":
      return canonicalizeLLC(raw, hints);
    case "domain":
      return canonicalizeDomain(raw);
    case "email":
      return canonicalizeEmail(raw);
    case "phone":
      return canonicalizePhone(raw);
  }
}

/**
 * Compose the `<kind>:<canonical_id>` string the W4-CLAIMS-BY-ENTITY
 * projection uses as Map key. Centralized so consumers don't re-derive
 * the format (which would risk drift from Iris's ratified scheme).
 */
export function entityFingerprint(fp: EntityFingerprint): string {
  return `${fp.kind}:${fp.canonical_id}`;
}

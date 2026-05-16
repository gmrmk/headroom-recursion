# ADR-0026: Dossier-section rubric-coverage lint

- **Status:** proposed
- **Date:** 2026-05-12
- **Deciders:** Sora (Tech Lead), persona round wave-4
- **Tags:** ci, lint, dossier, rubric, structural-drift

## Context

The dossier projection layer at `apps/web/src/lib/dossier-shape.ts` and the severity rubric at `apps/web/src/lib/severity-rubric.ts` are coupled by a string convention: each Finding emitted by the projection carries a `severity_basis: "matrix:<id>"` literal that names an entry in the `RUBRIC` object. This is how a finding's tier (`info` / `low` / `medium` / `high` / `critical`) is anchored to a documented justification string.

Sora's wave-4 graphify-informed review (`sora-deliberation-wave4.md:26`) surfaced the structural risk: `dossier-shape.ts` sits in graph community 23 and `severity-rubric.ts` sits in community 27, and **there is no direct import edge between them.** Grep confirms: of all files in `apps/web/src`, only `apps/web/src/lib/breach-synthesis.ts:21-22` imports from `severity-rubric.ts`. Every other usage in `dossier-shape.ts` is a string literal of the form `severity_basis: "matrix:DORK_HIT_SNIPPET"` (representative example at `dossier-shape.ts:595`). The `RUBRIC` object is typed `Record<string, RubricEntry>`, so a missing key returns `undefined` at runtime rather than failing at compile time.

Today the codebase has six dossier sections and 24 rubric entries, and manual diligence holds. Wave-4 adds two new sections (`timeline`, `claims_by_entity`) and three to four new rubric entries (`PV_AI_CONTENT_SUSPECTED`, `PV_AI_CONTENT_MULTI_DETECTOR`, `PV_OPSEC_UA_SELF_IDENTIFICATION`, optionally `PV_LISTING_PHOTO_PDQ_CROSS_PLATFORM`). Each new section will emit new `severity_basis` string literals; each new rubric entry must be referenced from at least one section to earn its keep. The compile-time invariant we want — *every `matrix:<id>` literal resolves to a `RUBRIC` entry, and every `RUBRIC` entry is referenced by at least one section* — is unenforced today and silently breakable on every section add.

## Decision

We will add a CI lint at `tools/ci/dossier_rubric_lint.ts` (or `.py` — language decided by where the broader web-tier CI tooling lives at write time) that:

1. **Walks `apps/web/src/lib/dossier-shape.ts` for every string literal matching the pattern `severity_basis: "matrix:<id>"`** (also accepts the variants present in current code: `severity_basis: \`matrix:${...}\`` template literals where the head is `matrix:` plus a constant).
2. **Asserts each `<id>` exists as a key in the `RUBRIC` object exported from `apps/web/src/lib/severity-rubric.ts`.**
3. **Optionally reports rubric entries with zero references in `dossier-shape.ts`** (warning, not error — orphan entries are legal in principle since `breach-synthesis.ts` is the one file that imports the rubric directly, but warning surfaces drift in the other direction).

The lint is wired as a pre-commit hook and a CI gate. Mechanism mirrors ADR-0022's `import-linter` discipline: a project-specific lint that codifies a string-convention contract the type system cannot enforce. ~50-80 LOC; runs in seconds; declarative output.

This ADR is a **prerequisite for the Sprint-5 dossier section additions** (`timeline`, `claims_by_entity`) per Margaret roadmap §7. The lint ships in Sprint-4 (5pt card) and is required green before Sprint-5 can land the new sections.

## Alternatives considered

- **Replace `severity_basis` strings with imported `RubricEntry` references.** *Rejected because:* the projection layer is intentionally pure-data — Findings serialize to JSON for HTML/PDF export and over the API. Importing rubric entries pulls runtime code into the serialization boundary and makes the projection asymmetric with the events stream it consumes. The string convention is correct; the lint is the discipline.
- **Convert `RUBRIC` to a typed enum + branded type for `severity_basis`.** *Rejected because:* TypeScript's string-literal types would catch missing keys at compile time, but the projection code constructs `severity_basis` from runtime values in some places (e.g., reading `matrix:${event.matrix_id}`) and the type narrowing breaks. A lint that handles both literal and template-literal forms is more practical than a type-system rewrite that handles 80% of cases.
- **Defer the lint until drift is empirically observed.** *Rejected because:* the wave-4 sections compound drift risk silently; the lint is cheap (5pt) and lands before the drift, not after. CLAUDE.md doctrine on verification-before-done supports this — manual diligence on a growing surface is a known-bad pattern.
- **Hand-roll the check inline inside `dossier-shape.ts` (assertion at module load).** *Rejected because:* the dossier-shape module is loaded by both the web bundle and the test suite; a runtime assertion either ships to the browser (bundle cost) or runs only in dev (incomplete coverage). CI lint is the right placement.

## Consequences

- **Positive:** Sprint-5 can add `timeline` and `claims_by_entity` projections without manual cross-checking that every `severity_basis` they emit has a rubric home; the lint is the gate.
- **Positive:** Future rubric refactors (renaming a `<id>`, removing a deprecated entry) fail loudly at CI rather than silently producing `undefined` lookups in the UI.
- **Positive:** Pattern is reusable. The same lint mechanism can extend to other string-convention boundaries (e.g., `asset_graph` node-kind strings) if drift is observed there.
- **Negative:** Adds a small CI artifact and a pre-commit step. Acceptable; the import-linter precedent established that the cost is justified for string-convention contracts.
- **Negative:** The lint walks AST, not types — there is a long-tail of dynamic `severity_basis` construction patterns the lint may not catch on day one. Mitigation: explicit allowlist of dynamic construction sites + warning when a new dynamic construction site appears.
- **Neutral:** The lint is opinionated about the `matrix:` prefix. If we ever ship a second rubric family (e.g., `forensic:` for forensic-defensibility tier), the lint needs an amendment.

## Gates that re-open this

- **Gate 1 (false-positive surface):** When the lint fires on a legitimate dynamic construction pattern that cannot be statically resolved, amend with an inline-comment opt-out marker (analogous to `# noqa`) and document the marker in the ADR.
- **Gate 2 (second rubric family):** When a second rubric prefix is introduced, extend the lint to cover the new prefix and reconsider whether the prefix list should be config-driven.
- **Gate 3 (orphan-entry policy):** When the warning on orphan rubric entries fires repeatedly with legitimate cases (rubric entries used only by `breach-synthesis.ts` or other non-`dossier-shape` consumers), formalize the consumer-allowlist rather than warning on the whole file.

## References

- `phase6/wave4/sora-deliberation-wave4.md:14, 26` — wave-4 graphify-informed finding on the projection-rubric drift surface
- `phase6/MARGARET-ROADMAP-2026-05-12-wave4.html` §6, §7 — ADR-0026 prerequisite for Sprint-5 sections
- `apps/web/src/lib/dossier-shape.ts` — projection layer (community 23)
- `apps/web/src/lib/severity-rubric.ts` — rubric definitions (community 27)
- `apps/web/src/lib/breach-synthesis.ts:21-22` — the one file that imports the rubric directly
- ADR-0022 — import-linter precedent for project-specific string-convention lints
- ADR-0023 (proposed) — analogous two-surface drift discipline for adapter registration

// Test fixture for tools/ci/rubric_coverage_lint.py (ADR-0026).
//
// All severity_basis citations in this file MUST resolve to entries in
// apps/web/src/lib/severity-rubric.ts. A failing lint run on this fixture
// is a regression in the lint.

export const cleanFinding = {
  headline: "Synthetic finding citing a real rubric entry",
  detail: "Lint should pass on this file.",
  source: "test-fixture",
  severity_basis: "matrix:DORK_HIT_SNIPPET",
};

export const anotherCleanFinding = {
  headline: "Another clean finding",
  detail: "Cites a different real rubric entry.",
  source: "test-fixture",
  severity_basis: "matrix:OPEN_FIREBASE_RTDB",
};

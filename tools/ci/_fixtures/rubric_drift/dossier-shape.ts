// Test fixture for tools/ci/rubric_coverage_lint.py (ADR-0026).
//
// This fixture deliberately cites a rubric ID (`MATRIX_ID_DOES_NOT_EXIST`)
// that is NOT present in apps/web/src/lib/severity-rubric.ts. The lint
// MUST reject this file when pointed at it; a passing run on this fixture
// is a regression in the lint.

export const fakeFinding = {
  headline: "Synthetic finding for lint testing",
  detail: "Cites a nonexistent rubric entry.",
  source: "test-fixture",
  severity_basis: "matrix:MATRIX_ID_DOES_NOT_EXIST",
};

// Also include a known-good citation to confirm the lint distinguishes
// missing from present, not "all-or-nothing".
export const realFinding = {
  headline: "Synthetic finding citing a real rubric entry",
  detail: "Should NOT trip the lint by itself.",
  source: "test-fixture",
  severity_basis: "matrix:DORK_HIT_SNIPPET",
};

"""Unit tests for tools/ci/rubric_coverage_lint.py (ADR-0026).

The lint walks `apps/web/src/lib/dossier-shape.ts` (and `breach-synthesis.ts`)
for every `severity_basis: "matrix:<id>"` literal and asserts each <id>
exists as a key in the RUBRIC object in `apps/web/src/lib/severity-rubric.ts`.

Tests:
  - the real codebase passes (Phase 1 added 4 new rubric entries; doctrine
    work covered them, so the lint should be clean on master today)
  - the rubric_drift fixture FAILS (citation to MATRIX_ID_DOES_NOT_EXIST)
  - the clean-dossier-shape fixture PASSES (all citations resolve)
  - a citation to a nonexistent ID via tmp_path FAILS with a clear message
  - a citation count summary appears on clean runs
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = REPO_ROOT / "tools" / "ci" / "_fixtures" / "rubric_drift"
REAL_RUBRIC = REPO_ROOT / "apps" / "web" / "src" / "lib" / "severity-rubric.ts"


def test_real_codebase_passes(run_lint):
    """Master should be clean today — Phase 1 doctrine work covered the new
    wave-4 rubric entries. A failure here means real drift has landed."""
    exit_code, stderr, stdout = run_lint("rubric_coverage_lint.py")
    assert (
        exit_code == 0
    ), f"real-codebase rubric-coverage lint should pass; stderr={stderr!r} stdout={stdout!r}"
    assert "OK" in stdout
    # Sanity check the summary line shape — must report both counts.
    assert "citation" in stdout and "rubric entries" in stdout


def test_drift_fixture_fails(run_lint):
    """The committed drift fixture cites MATRIX_ID_DOES_NOT_EXIST; the lint
    must reject it with a message naming the missing id and the file."""
    fixture = FIXTURE_DIR / "dossier-shape.ts"
    assert fixture.exists(), f"missing test fixture: {fixture}"
    exit_code, stderr, _ = run_lint(
        "rubric_coverage_lint.py",
        str(fixture),
        "--rubric",
        str(REAL_RUBRIC),
    )
    assert exit_code == 1, f"expected lint to fail on drift fixture; stderr={stderr!r}"
    assert "MATRIX_ID_DOES_NOT_EXIST" in stderr
    assert "dossier-shape.ts" in stderr


def test_clean_fixture_passes(run_lint):
    """The committed clean fixture cites only real rubric entries; the lint
    must accept it cleanly."""
    fixture = FIXTURE_DIR / "clean-dossier-shape.ts"
    assert fixture.exists(), f"missing test fixture: {fixture}"
    exit_code, stderr, stdout = run_lint(
        "rubric_coverage_lint.py",
        str(fixture),
        "--rubric",
        str(REAL_RUBRIC),
    )
    assert exit_code == 0, f"clean fixture should pass; stderr={stderr!r} stdout={stdout!r}"
    assert "OK" in stdout


def test_synthetic_missing_id_fails(tmp_path, run_lint):
    """Create a tmp_path TS file with a single bad citation and confirm
    the lint flags it. Defends against any fixture-file caching/regression."""
    synthetic = tmp_path / "synthetic-dossier.ts"
    synthetic.write_text(
        'export const x = { severity_basis: "matrix:NEVER_EXISTED_KEY" };\n',
        encoding="utf-8",
    )
    exit_code, stderr, _ = run_lint(
        "rubric_coverage_lint.py",
        str(synthetic),
        "--rubric",
        str(REAL_RUBRIC),
    )
    assert exit_code == 1
    assert "NEVER_EXISTED_KEY" in stderr
    assert "no such entry" in stderr.lower() or "rubric" in stderr.lower()


def test_clean_synthetic_passes(tmp_path, run_lint):
    """Synthetic TS file with only real-rubric citations passes the lint."""
    synthetic = tmp_path / "synthetic-dossier.ts"
    synthetic.write_text(
        'export const x = { severity_basis: "matrix:DORK_HIT_SNIPPET" };\n'
        'export const y = { severity_basis: "matrix:SUBDOMAIN_TAKEOVER" };\n',
        encoding="utf-8",
    )
    exit_code, stderr, stdout = run_lint(
        "rubric_coverage_lint.py",
        str(synthetic),
        "--rubric",
        str(REAL_RUBRIC),
    )
    assert exit_code == 0, f"clean synthetic should pass; stderr={stderr!r}"
    assert "OK" in stdout


def test_empty_file_passes(tmp_path, run_lint):
    """A TS file with zero citations is trivially clean."""
    empty = tmp_path / "empty.ts"
    empty.write_text("export const nothing = 1;\n", encoding="utf-8")
    exit_code, stderr, stdout = run_lint(
        "rubric_coverage_lint.py",
        str(empty),
        "--rubric",
        str(REAL_RUBRIC),
    )
    assert exit_code == 0, f"empty file should pass; stderr={stderr!r}"
    assert "OK" in stdout
    assert "0 citation" in stdout


def test_missing_rubric_file_fails(tmp_path, run_lint):
    """If the rubric file doesn't exist, the lint must fail loudly rather
    than silently producing a 'zero entries' false-pass."""
    citation = tmp_path / "dossier.ts"
    citation.write_text(
        'export const x = { severity_basis: "matrix:ANYTHING" };\n',
        encoding="utf-8",
    )
    missing_rubric = tmp_path / "does-not-exist.ts"
    exit_code, stderr, _ = run_lint(
        "rubric_coverage_lint.py",
        str(citation),
        "--rubric",
        str(missing_rubric),
    )
    assert exit_code == 1
    assert "not found" in stderr.lower() or "does-not-exist" in stderr

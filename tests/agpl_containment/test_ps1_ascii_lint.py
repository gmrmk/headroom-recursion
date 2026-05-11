"""Unit tests for tools/ci/ps1_ascii_lint.py (Priya WI-0125b)."""

from __future__ import annotations


def write(path, content: str, *, encoding: str = "utf-8") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding=encoding)


def test_ascii_only_passes(tmp_path, run_lint):
    write(tmp_path / "clean.ps1", 'Write-Host "hello world"\n')
    exit_code, _, _ = run_lint("ps1_ascii_lint.py", str(tmp_path / "clean.ps1"))
    assert exit_code == 0


def test_em_dash_rejected(tmp_path, run_lint):
    """U+2014 em-dash is the canonical break — PowerShell reads it as cp1252."""
    p = tmp_path / "bad.ps1"
    write(p, 'Write-Host "hello \u2014 world"\n')
    exit_code, stderr, _ = run_lint("ps1_ascii_lint.py", str(p))
    assert exit_code == 1
    assert "U+2014" in stderr or "non-ASCII" in stderr


def test_section_sign_rejected(tmp_path, run_lint):
    """U+00A7 section sign also breaks PowerShell parsing."""
    p = tmp_path / "bad.ps1"
    write(p, "# see \u00a75.1 for context\n")
    exit_code, stderr, _ = run_lint("ps1_ascii_lint.py", str(p))
    assert exit_code == 1
    assert "U+00A7" in stderr or "non-ASCII" in stderr


def test_smart_quote_rejected(tmp_path, run_lint):
    """U+2019 right single quote — sneaks in via Word/clipboard."""
    p = tmp_path / "bad.ps1"
    write(p, 'Write-Host "it\u2019s a problem"\n')
    exit_code, stderr, _ = run_lint("ps1_ascii_lint.py", str(p))
    assert exit_code == 1
    assert "U+2019" in stderr or "non-ASCII" in stderr


def test_multiple_violations_first_per_line(tmp_path, run_lint):
    """Lint reports one violation per line (avoid noise on long bad lines)."""
    p = tmp_path / "bad.ps1"
    write(p, 'Write-Host "\u2014 \u2014 \u2014"\n')
    exit_code, stderr, _ = run_lint("ps1_ascii_lint.py", str(p))
    assert exit_code == 1
    # Exactly one error reported despite three em-dashes on one line
    assert stderr.count("U+2014") == 1


def test_repo_real_ps1_clean(run_lint):
    """The real .ps1 files in this repo should be ASCII-only after our Day-0 fix."""
    exit_code, stderr, _ = run_lint("ps1_ascii_lint.py", "scripts/start-dev.ps1")
    assert exit_code == 0, f"start-dev.ps1 not ASCII-only; stderr={stderr!r}"

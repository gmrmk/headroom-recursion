"""Unit tests for tools/ci/agpl_import_lint.py (WI-0106).

The lint is AST-based + path-exempt (adapters/<id>/wrapper.py). Tests:
  - bare import of an AGPL module is rejected
  - from-import is rejected
  - aliased import is rejected
  - submodule from-import is rejected (top-package match)
  - the wrapper.py path exemption works
  - a sibling file named wrapper_real.py is NOT exempt
  - comment-only mentions of AGPL modules are ignored (AST has no comments)
  - clean code passes
"""

from __future__ import annotations

import pytest


def write(path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_bare_import_rejected(tmp_path, run_lint):
    write(tmp_path / "case.py", "import ghunt\n")
    exit_code, stderr, _ = run_lint("agpl_import_lint.py", str(tmp_path / "case.py"))
    assert exit_code == 1
    assert "ghunt" in stderr


def test_from_import_rejected(tmp_path, run_lint):
    write(tmp_path / "case.py", "from ghunt import x\n")
    exit_code, stderr, _ = run_lint("agpl_import_lint.py", str(tmp_path / "case.py"))
    assert exit_code == 1
    assert "ghunt" in stderr


def test_aliased_import_rejected(tmp_path, run_lint):
    write(tmp_path / "case.py", "import ghunt as g\n")
    exit_code, stderr, _ = run_lint("agpl_import_lint.py", str(tmp_path / "case.py"))
    assert exit_code == 1
    assert "ghunt" in stderr


def test_submodule_from_import_rejected(tmp_path, run_lint):
    write(tmp_path / "case.py", "from ghunt.module import x\n")
    exit_code, stderr, _ = run_lint("agpl_import_lint.py", str(tmp_path / "case.py"))
    assert exit_code == 1
    assert "ghunt" in stderr


def test_wrapper_path_exempt(tmp_path, run_lint):
    """adapters/<id>/wrapper.py is the only path allowed to import AGPL modules."""
    p = tmp_path / "adapters" / "ghunt" / "wrapper.py"
    write(p, "import ghunt\nfrom ghunt import scan\n")
    exit_code, stderr, _ = run_lint("agpl_import_lint.py", str(p))
    assert exit_code == 0, f"wrapper.py should be exempt; stderr={stderr!r}"


def test_wrapper_sibling_NOT_exempt(tmp_path, run_lint):
    """Sibling files like wrapper_real.py are NOT exempt — must be exactly wrapper.py."""
    p = tmp_path / "adapters" / "ghunt" / "wrapper_real.py"
    write(p, "import ghunt\n")
    exit_code, stderr, _ = run_lint("agpl_import_lint.py", str(p))
    assert exit_code == 1
    assert "ghunt" in stderr


def test_comment_only_ignored(tmp_path, run_lint):
    """AST has no comments — `# import ghunt` is text not code."""
    write(tmp_path / "case.py", "# import ghunt is fine in adapters/\nx = 1\n")
    exit_code, _, _ = run_lint("agpl_import_lint.py", str(tmp_path / "case.py"))
    assert exit_code == 0


def test_clean_code_passes(tmp_path, run_lint):
    write(tmp_path / "case.py", "import json\nfrom pathlib import Path\nx: int = 1\n")
    exit_code, _, _ = run_lint("agpl_import_lint.py", str(tmp_path / "case.py"))
    assert exit_code == 0


@pytest.mark.parametrize(
    "agpl_module",
    [
        "bbot",
        "ghunt",
        "social_analyzer",
        "snscrape",
        "trufflehog",
        "phoneinfoga",
        "onionsearch",
        "ivre",
        "aleph",
        "spiderfoot",
    ],
)
def test_every_forbidden_module_is_caught(tmp_path, run_lint, agpl_module: str):
    """Each listed AGPL module in the forbidden set must trigger the lint."""
    write(tmp_path / "case.py", f"import {agpl_module}\n")
    exit_code, stderr, _ = run_lint("agpl_import_lint.py", str(tmp_path / "case.py"))
    assert exit_code == 1
    assert agpl_module in stderr


# ----------------------------------------------------------------------------
# Camille P1 (phase6 2026-05-11): dynamic-import scanner extension.
# The static AST layer above is blind to runtime/dynamic patterns:
#   __import__("ghunt") / importlib.import_module("ghunt") / exec("import ghunt")
# The regex scanner adds a second layer that catches those patterns in the
# raw file text. False positives (e.g. AGPL name inside a non-docstring
# string literal) can be suppressed with `# agpl-lint: dynamic-ok` on the
# same line.
# ----------------------------------------------------------------------------


def test_dunder_import_rejected(tmp_path, run_lint):
    write(tmp_path / "case.py", '__import__("ghunt")\n')
    exit_code, stderr, _ = run_lint("agpl_import_lint.py", str(tmp_path / "case.py"))
    assert exit_code == 1
    assert "ghunt" in stderr
    assert "dynamic" in stderr.lower()


def test_importlib_import_module_rejected(tmp_path, run_lint):
    write(
        tmp_path / "case.py",
        'import importlib\nm = importlib.import_module("ghunt")\n',
    )
    exit_code, stderr, _ = run_lint("agpl_import_lint.py", str(tmp_path / "case.py"))
    assert exit_code == 1
    assert "ghunt" in stderr
    assert "dynamic" in stderr.lower()


def test_exec_import_rejected(tmp_path, run_lint):
    write(tmp_path / "case.py", 'exec("import ghunt")\n')
    exit_code, stderr, _ = run_lint("agpl_import_lint.py", str(tmp_path / "case.py"))
    assert exit_code == 1
    assert "ghunt" in stderr
    assert "dynamic" in stderr.lower()


def test_eval_dunder_import_rejected(tmp_path, run_lint):
    write(tmp_path / "case.py", "eval(\"__import__('ghunt')\")\n")
    exit_code, stderr, _ = run_lint("agpl_import_lint.py", str(tmp_path / "case.py"))
    assert exit_code == 1
    assert "ghunt" in stderr
    assert "dynamic" in stderr.lower()


def test_dynamic_import_in_wrapper_is_exempt(tmp_path, run_lint):
    """Wrapper.py is path-exempt for BOTH static and dynamic imports."""
    p = tmp_path / "adapters" / "ghunt" / "wrapper.py"
    write(p, 'import importlib\nm = importlib.import_module("ghunt")\n')
    exit_code, stderr, _ = run_lint("agpl_import_lint.py", str(p))
    assert exit_code == 0, f"wrapper.py dynamic import should be exempt; stderr={stderr!r}"


def test_dynamic_ok_inline_suppression(tmp_path, run_lint):
    """`# agpl-lint: dynamic-ok` on the same line suppresses dynamic-scan only."""
    write(
        tmp_path / "case.py",
        'msg = "see docs for how to import ghunt"  # agpl-lint: dynamic-ok\n',
    )
    exit_code, _, _ = run_lint("agpl_import_lint.py", str(tmp_path / "case.py"))
    assert exit_code == 0


def test_dynamic_ok_does_not_suppress_static_import(tmp_path, run_lint):
    """The inline suppression covers dynamic regex only; static `import ghunt`
    still fails even with the suppression marker."""
    write(tmp_path / "case.py", "import ghunt  # agpl-lint: dynamic-ok\n")
    exit_code, stderr, _ = run_lint("agpl_import_lint.py", str(tmp_path / "case.py"))
    assert exit_code == 1
    assert "ghunt" in stderr


def test_parse_error_now_warns_not_silently_skips(tmp_path, run_lint):
    """Camille P1: a file that fails to parse must emit a warning, not be
    silently skipped (hostile contributor could exploit the silent skip)."""
    # Bytes that produce a UnicodeDecodeError on utf-8 strict
    p = tmp_path / "bad.py"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"\xff\xfeimport ghunt\n")
    exit_code, stderr, _ = run_lint("agpl_import_lint.py", str(p))
    # Parse error still skips the file (we can't trust the contents) but a
    # warning MUST be emitted to stderr so it's visible in CI logs.
    assert "warning" in stderr.lower() or "skip" in stderr.lower()
    assert str(p.name) in stderr

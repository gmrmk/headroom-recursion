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

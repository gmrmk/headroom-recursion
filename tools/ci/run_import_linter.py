"""Pre-commit-friendly shim for import-linter.

The `lint-imports` console script is installed by import-linter into the
venv's Scripts/ directory. Pre-commit's `language: system` runs hooks in
a subprocess where (a) the venv Scripts/ dir isn't on PATH and (b) `python`
may resolve to the host's system Python rather than the repo's venv Python.

Native deps (grimp ships a Rust extension via _rustgrimp.pyd) cannot be
loaded by a different Python interpreter than the one they were built
against, so a sys.path injection trick doesn't work -- we must invoke the
venv Python directly.

This shim:
  1. Detects whether the current interpreter has importlinter available.
  2. If not, re-execs under the repo-local venv Python (Win11: .venv/
     Scripts/python.exe; POSIX: .venv/bin/python).
  3. Once running under the right interpreter, dispatches to the
     import-linter Click CLI.

CLI:
  python tools/ci/run_import_linter.py            # uses .importlinter at repo root
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _venv_python() -> Path:
    """Locate the repo's venv Python interpreter cross-platform."""
    if os.name == "nt":
        candidate = REPO_ROOT / ".venv" / "Scripts" / "python.exe"
    else:
        candidate = REPO_ROOT / ".venv" / "bin" / "python"
    return candidate


def _have_importlinter() -> bool:
    try:
        import importlinter  # noqa: F401
    except ImportError:
        return False
    return True


def _reexec_under_venv() -> int:
    """Re-invoke this same script under the venv Python and return its exit
    code. Used when the current interpreter doesn't have importlinter."""
    py = _venv_python()
    if not py.is_file():
        print(
            f"venv Python not found at {py}. " f"Run `uv sync --all-packages` to create the venv.",
            file=sys.stderr,
        )
        return 3
    result = subprocess.run([str(py), str(Path(__file__).resolve()), *sys.argv[1:]])
    return result.returncode


def _run_lint() -> int:
    """Actual lint dispatch (only called when importlinter is available)."""
    from importlinter.cli import lint_imports_command

    try:
        lint_imports_command(["--config", ".importlinter"], standalone_mode=False)
    except SystemExit as exc:
        return int(exc.code) if exc.code is not None else 0
    except Exception as exc:
        print(f"import-linter error: {exc}", file=sys.stderr)
        return 1
    return 0


def main() -> int:
    if _have_importlinter():
        return _run_lint()
    return _reexec_under_venv()


if __name__ == "__main__":
    sys.exit(main())

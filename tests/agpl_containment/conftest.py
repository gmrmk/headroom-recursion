"""Shared fixtures for tools/ci/ lint script tests."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def run_lint():
    """Run a lint script via subprocess; returns (exit_code, stderr, stdout)."""

    def _run(script: str, *args: str) -> tuple[int, str, str]:
        script_path = REPO_ROOT / "tools" / "ci" / script
        proc = subprocess.run(
            [sys.executable, str(script_path), *args],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        return proc.returncode, proc.stderr, proc.stdout

    return _run

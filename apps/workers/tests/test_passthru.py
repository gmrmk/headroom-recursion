"""Tests for Hard Passthru runtime guard (OPSEC Phase 2).

The guard blocks any write to a path outside the configured whitelist.
Reads are never blocked. Default state on import is OFF; auto-install is
gated on OSINT_PASSTHRU=1.
"""

from __future__ import annotations

import builtins
import contextlib
import os
import tempfile
from pathlib import Path

import pytest
from osint_goblin_workers import passthru
from osint_goblin_workers.passthru import (
    PassthruViolationError,
    disable_passthru,
    install_passthru,
    is_passthru_active,
    passthru_context,
    should_auto_install,
)


@pytest.fixture(autouse=True)
def _ensure_disabled_after_test():
    """Hard-disable guard after every test so failures don't bleed."""
    yield
    if is_passthru_active():
        disable_passthru()


def _write_path(p: Path) -> None:
    with builtins.open(p, "w", encoding="utf-8") as fh:
        fh.write("x")


def test_module_imports_clean_without_auto_install():
    # No OSINT_PASSTHRU in env at collection time -> guard must be off.
    assert is_passthru_active() is False


def test_is_passthru_active_flips_on_install_and_disable():
    assert is_passthru_active() is False
    install_passthru()
    try:
        assert is_passthru_active() is True
    finally:
        disable_passthru()
    assert is_passthru_active() is False


def test_install_blocks_write_to_nonwhitelisted_path(tmp_path_factory):
    # tmp_path_factory uses gettempdir() under the hood which IS whitelisted.
    # We need a directory outside any default-allowed root. Use the user's
    # Documents-ish area via a path constructed to live in the repo's docs/
    # tree (not whitelisted by default).
    repo_root = Path(__file__).resolve().parents[3]
    forbidden = repo_root / "docs" / "_passthru_test_should_block.txt"
    install_passthru()
    try:
        with pytest.raises(PassthruViolationError):
            _write_path(forbidden)
    finally:
        disable_passthru()
    # Cleanup just in case the guard somehow let it through.
    if forbidden.exists():
        forbidden.unlink()


def test_write_to_tempdir_succeeds_after_install():
    tmp_path = Path(tempfile.gettempdir()) / "some-osint-passthru-test.txt"
    if tmp_path.exists():
        tmp_path.unlink()
    install_passthru()
    try:
        _write_path(tmp_path)
        assert tmp_path.exists()
        assert tmp_path.read_text(encoding="utf-8") == "x"
    finally:
        disable_passthru()
        if tmp_path.exists():
            tmp_path.unlink()


def test_read_mode_open_never_blocked_outside_whitelist(tmp_path):
    # Create a file inside tmp_path (whitelisted via gettempdir parent? not
    # necessarily — pytest tmp_path is under gettempdir() so it IS allowed
    # for writes too). Move read target to repo so we know it's outside the
    # write-whitelist.
    repo_root = Path(__file__).resolve().parents[3]
    # README.md is guaranteed-present and read-only-safe.
    readable = repo_root / "README.md"
    assert readable.exists()
    install_passthru()
    try:
        # Should NOT raise even though path is outside any write-whitelist.
        with builtins.open(readable, encoding="utf-8") as fh:
            data = fh.read(16)
        assert isinstance(data, str)
    finally:
        disable_passthru()


def test_disable_restores_original_open():
    repo_root = Path(__file__).resolve().parents[3]
    forbidden = repo_root / "docs" / "_passthru_disable_restore.txt"
    install_passthru()
    disable_passthru()
    # After disable, the formerly-forbidden write should succeed.
    try:
        _write_path(forbidden)
        assert forbidden.exists()
    finally:
        if forbidden.exists():
            forbidden.unlink()


def test_install_is_idempotent():
    install_passthru()
    install_passthru()
    install_passthru()
    assert is_passthru_active() is True
    disable_passthru()
    disable_passthru()  # also idempotent
    assert is_passthru_active() is False


def test_passthru_context_toggles_state():
    assert is_passthru_active() is False
    with passthru_context():
        assert is_passthru_active() is True
    assert is_passthru_active() is False


def test_passthru_context_disables_on_exception():
    assert is_passthru_active() is False
    with pytest.raises(RuntimeError), passthru_context():
        assert is_passthru_active() is True
        raise RuntimeError("boom")
    assert is_passthru_active() is False


def test_extra_whitelist_widens_allowlist(tmp_path):
    # Carve out a subdir of pytest tmp_path that we'll register and prove
    # writable, then prove a sibling that's NOT registered remains writable
    # too (because pytest tmp_path is already under gettempdir(), which is
    # default-whitelisted). To actually test extra_whitelist, use a path
    # outside the default whitelist: nest under the repo's docs/ dir.
    repo_root = Path(__file__).resolve().parents[3]
    extra_dir = repo_root / "docs" / "_passthru_extra_whitelist"
    extra_dir.mkdir(parents=True, exist_ok=True)
    target = extra_dir / "ok.txt"
    try:
        with passthru_context(extra_whitelist=[extra_dir]):
            _write_path(target)
            assert target.exists()
    finally:
        if target.exists():
            target.unlink()
        with contextlib.suppress(OSError):
            extra_dir.rmdir()


def test_os_open_with_write_flags_is_blocked():
    repo_root = Path(__file__).resolve().parents[3]
    forbidden = repo_root / "docs" / "_passthru_osopen_block.txt"
    install_passthru()
    try:
        with pytest.raises(PassthruViolationError):
            fd = os.open(str(forbidden), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
            os.close(fd)
    finally:
        disable_passthru()
        if forbidden.exists():
            forbidden.unlink()


def test_os_open_read_mode_not_blocked():
    repo_root = Path(__file__).resolve().parents[3]
    readable = repo_root / "README.md"
    install_passthru()
    try:
        fd = os.open(str(readable), os.O_RDONLY)
        os.close(fd)
    finally:
        disable_passthru()


def test_should_auto_install_true_when_env_set_to_1(monkeypatch):
    monkeypatch.setenv("OSINT_PASSTHRU", "1")
    assert should_auto_install() is True


def test_should_auto_install_false_when_env_unset(monkeypatch):
    monkeypatch.delenv("OSINT_PASSTHRU", raising=False)
    assert should_auto_install() is False


def test_should_auto_install_false_for_other_values(monkeypatch):
    for value in ("0", "true", "yes", "on", "", "2"):
        monkeypatch.setenv("OSINT_PASSTHRU", value)
        assert should_auto_install() is False, f"value={value!r}"


def test_error_message_mentions_path_mode_and_disable_hint():
    repo_root = Path(__file__).resolve().parents[3]
    forbidden = repo_root / "docs" / "_passthru_msg_check.txt"
    install_passthru()
    try:
        with pytest.raises(PassthruViolationError) as exc:
            _write_path(forbidden)
        msg = str(exc.value)
        assert "_passthru_msg_check.txt" in msg
        assert "OSINT_PASSTHRU" in msg
        # Mode hint should be present (we used "w" via builtins.open).
        assert "w" in msg.lower()
    finally:
        disable_passthru()
        if forbidden.exists():
            forbidden.unlink()


def test_module_exposes_expected_public_api():
    # Spot-check that the surface promised by the spec is importable.
    assert hasattr(passthru, "PassthruViolationError")
    assert hasattr(passthru, "install_passthru")
    assert hasattr(passthru, "disable_passthru")
    assert hasattr(passthru, "is_passthru_active")
    assert hasattr(passthru, "passthru_context")
    assert hasattr(passthru, "should_auto_install")
    assert issubclass(PassthruViolationError, OSError)

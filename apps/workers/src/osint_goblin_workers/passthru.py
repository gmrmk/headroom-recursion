"""Hard Passthru runtime guard (OPSEC Phase 2).

Monkey-patches ``builtins.open`` and ``os.open`` so any write outside a
configured whitelist raises :class:`PassthruViolationError`. Reads are never
blocked.

This enforces the project's privacy doctrine: refdata-only persistence, with
investigation artifacts kept in tempfs / explicit user-chosen roots so nothing
leaks into the source tree or user home by accident.

Auto-install is gated on the ``OSINT_PASSTHRU=1`` environment variable so the
guard is OFF by default and existing tests / dev workflows are unaffected.
"""

from __future__ import annotations

import builtins
import contextlib
import os
import sys
import tempfile
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

__all__ = [
    "PassthruViolationError",
    "install_passthru",
    "disable_passthru",
    "is_passthru_active",
    "passthru_context",
    "should_auto_install",
]


class PassthruViolationError(OSError):
    """Raised when code attempts to write outside the passthru whitelist."""


_lock = threading.Lock()
_state: dict[str, Any] = {
    "installed": False,
    "original_builtins_open": None,
    "original_os_open": None,
    "whitelist": [],  # list[Path]
}

# Write-intent string-mode characters. Read-only mode strings contain none of
# these (e.g. "r", "rb", "rt").
_WRITE_MODE_CHARS = frozenset("wax+")

# Write-intent bit mask for int flags passed to os.open.
_WRITE_FLAGS_MASK = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT


def _repo_root() -> Path:
    # passthru.py lives at apps/workers/src/osint_goblin_workers/passthru.py.
    # parents: [0]=osint_goblin_workers, [1]=src, [2]=workers, [3]=apps,
    # [4]=<repo_root>.
    return Path(__file__).resolve().parents[4]


def _default_whitelist() -> list[Path]:
    roots: list[Path] = []

    # Python install + venv prefixes (site-packages, bytecode caches, etc.).
    for attr in ("prefix", "exec_prefix", "base_prefix", "base_exec_prefix"):
        candidate = getattr(sys, attr, None)
        if candidate:
            roots.append(Path(candidate))

    # User site-packages (pip --user installs land here).
    try:
        import site

        for attr in ("USER_BASE", "USER_SITE"):
            candidate = getattr(site, attr, None)
            if candidate:
                roots.append(Path(candidate))
    except ImportError:
        pass

    # System tempdir (Playwright/Chromium, pytest tmp_path, dramatiq state).
    roots.append(Path(tempfile.gettempdir()))

    # Project-specific allowances.
    repo = _repo_root()
    roots.extend(
        [
            repo / "data" / "reference",  # refdata (privacy-safe persistence)
            repo / ".venv",  # uv venv bytecode caches
            repo / ".pytest_cache",
            repo / ".ruff_cache",
            repo / "__pycache__",
        ]
    )

    # Platform null device.
    if sys.platform.startswith("win"):
        roots.append(Path("NUL"))
    else:
        roots.append(Path("/dev/null"))

    # Resolve so is_relative_to comparisons match real-path inputs.
    resolved: list[Path] = []
    for r in roots:
        try:
            resolved.append(r.resolve(strict=False))
        except OSError:
            # If we can't resolve (e.g. weird platform path), skip silently.
            continue
    return resolved


def _format_allowed_roots(roots: list[Path]) -> str:
    return ", ".join(str(r) for r in roots)


def _mode_is_write_string(mode: str) -> bool:
    return any(ch in _WRITE_MODE_CHARS for ch in mode)


def _flags_are_write(flags: int) -> bool:
    return bool(flags & _WRITE_FLAGS_MASK)


def _resolve_path_arg(path_arg: Any) -> Path:
    """Resolve an open() path argument to a real Path.

    Fails closed: anything we cannot interpret as a string-ish path becomes
    an error (which the caller will translate into PassthruViolationError).
    Already-open file descriptors (int) are rejected outright for write
    intent — by the time you have an fd, the path-level vet has been skipped.
    """
    if isinstance(path_arg, int):
        # Caller is passing an fd into open() — we can't path-check that.
        # Fail closed for writes.
        raise ValueError("passthru: refusing write on raw fd (no path to vet)")
    if hasattr(path_arg, "__fspath__"):
        return Path(os.fspath(path_arg)).resolve(strict=False)
    if isinstance(path_arg, str | bytes):
        if isinstance(path_arg, bytes):
            path_arg = path_arg.decode("utf-8", errors="surrogateescape")
        return Path(path_arg).resolve(strict=False)
    raise ValueError(f"passthru: cannot interpret path argument: {path_arg!r}")


def _is_path_allowed(target: Path) -> bool:
    whitelist: list[Path] = _state["whitelist"]
    for root in whitelist:
        try:
            if target == root or target.is_relative_to(root):
                return True
        except ValueError:
            # Different drives on Windows -> is_relative_to raises ValueError.
            continue
    return False


def _violation(target: Path | str, mode: str) -> PassthruViolationError:
    roots = _format_allowed_roots(_state["whitelist"])
    msg = (
        f"passthru: write blocked to {target!s} (mode={mode!r}). "
        "Set OSINT_PASSTHRU=0 to disable the guard for this process. "
        f"Allowed roots: {roots}"
    )
    return PassthruViolationError(msg)


def _patched_builtins_open(
    file: Any,
    mode: str = "r",
    buffering: int = -1,
    encoding: str | None = None,
    errors: str | None = None,
    newline: str | None = None,
    closefd: bool = True,
    opener: Any = None,
) -> Any:
    original = _state["original_builtins_open"]
    if not _mode_is_write_string(mode):
        return original(
            file,
            mode,
            buffering,
            encoding,
            errors,
            newline,
            closefd,
            opener,
        )
    try:
        target = _resolve_path_arg(file)
    except ValueError as exc:
        raise _violation(str(file), mode) from exc
    if not _is_path_allowed(target):
        raise _violation(target, mode)
    return original(
        file,
        mode,
        buffering,
        encoding,
        errors,
        newline,
        closefd,
        opener,
    )


def _patched_os_open(
    path: Any,
    flags: int,
    mode: int = 0o777,
    *,
    dir_fd: int | None = None,
) -> int:
    original = _state["original_os_open"]
    if not _flags_are_write(flags):
        if dir_fd is None:
            return original(path, flags, mode)
        return original(path, flags, mode, dir_fd=dir_fd)
    try:
        target = _resolve_path_arg(path)
    except ValueError as exc:
        raise _violation(str(path), f"flags={flags:#o}") from exc
    if not _is_path_allowed(target):
        raise _violation(target, f"flags={flags:#o}")
    if dir_fd is None:
        return original(path, flags, mode)
    return original(path, flags, mode, dir_fd=dir_fd)


def install_passthru(extra_whitelist: list[Path] | None = None) -> None:
    """Install the guard. Idempotent — repeated calls are no-ops."""
    with _lock:
        if _state["installed"]:
            return
        whitelist = _default_whitelist()
        if extra_whitelist:
            for extra in extra_whitelist:
                try:
                    whitelist.append(Path(extra).resolve(strict=False))
                except OSError:
                    continue
        _state["whitelist"] = whitelist
        _state["original_builtins_open"] = builtins.open
        _state["original_os_open"] = os.open
        builtins.open = _patched_builtins_open  # type: ignore[assignment]
        os.open = _patched_os_open  # type: ignore[assignment]
        _state["installed"] = True


def disable_passthru() -> None:
    """Restore originals. Idempotent — safe to call when not installed."""
    with _lock:
        if not _state["installed"]:
            return
        if _state["original_builtins_open"] is not None:
            builtins.open = _state["original_builtins_open"]  # type: ignore[assignment]
        if _state["original_os_open"] is not None:
            os.open = _state["original_os_open"]  # type: ignore[assignment]
        _state["original_builtins_open"] = None
        _state["original_os_open"] = None
        _state["whitelist"] = []
        _state["installed"] = False


def is_passthru_active() -> bool:
    return bool(_state["installed"])


@contextlib.contextmanager
def passthru_context(
    extra_whitelist: list[Path] | None = None,
) -> Iterator[None]:
    """Context manager: install on enter, disable on exit (always)."""
    install_passthru(extra_whitelist=extra_whitelist)
    try:
        yield
    finally:
        disable_passthru()


def should_auto_install() -> bool:
    """True iff OSINT_PASSTHRU env var is exactly "1"."""
    return os.environ.get("OSINT_PASSTHRU") == "1"


# Auto-install on import only if explicitly opted in.
if should_auto_install():
    install_passthru()

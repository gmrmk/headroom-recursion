"""LOGLESS LOGLESS LOGLESS -- the Naomi gate's structural enforcement.

Per user directive 2026-05-16: "I dont hold onto any data so I am not
scraping, Im picking it up and throwing it out when Im done." Plus the
follow-up correction 2026-05-16: "why are we having an investigation ID
if this is LOGLESS LOGLESS LOGLESS".

The combined ask is structural: nothing about an investigation should
remain on disk in any form -- not its outputs, not its identifiers, not
its directory listing. The OSINT Goblin is a pass-through, not a store.

DEFAULT BEHAVIOR (no env config needed)
  Ephemeral mode is ON unless explicitly opted out. In ephemeral mode:
    - No image bytes are ever written to data/. Flipped variants and
      ELA visualizations are returned inline (base64) in the event
      payload only.
    - The perceptual-hash audit trail (phash DB) lives in a process-
      memory dict, dropped on shutdown. No data/phash-db.jsonl file.
    - Camoufox + other browser tempdirs use random suffixes (not
      investigation_id) so the directory listing can't be correlated.

OPT-OUT (OSINT_EPHEMERAL_MODE=0)
  For forensic deep-dives where the operator WANTS to inspect bytes
  later, they can opt out. Even then, artifacts land in a per-process
  anonymous tmpdir under %TEMP%, registered via atexit for cleanup on
  process exit -- never in the repo's data/ directory.

WHY NO INVESTIGATION_ID IN FILE NAMES
  Even if every artifact byte is opaque, a directory listing of
  `data/flipped/` exposing filenames like `case_alice_vrbo__abc.jpg`
  reveals WHAT was investigated. The Naomi gate requires that even the
  EXISTENCE of an investigation can't be inferred from disk state.
  Investigation IDs are in-memory routing keys (BrowserContext key,
  SSE channel) only -- never persisted, never logged, never put into
  file paths or row contents.

PUBLIC SURFACE
  is_ephemeral_mode()           default True; only False if
                                OSINT_EPHEMERAL_MODE=0 explicitly set.
  ephemeral_artifact_dir()      returns a per-process tmpdir under %TEMP%
                                registered for shutdown cleanup; ONLY
                                used in opt-out mode.
  random_token()                short cryptographic-random token for
                                file naming + tempdir suffixes.
  shutdown_cleanup()            called via atexit; removes the
                                per-process tmpdir.
  purge_repo_artifacts()        emergency: nuke any leftover bytes in
                                the repo's data/ directory from earlier
                                non-ephemeral sessions.
"""

from __future__ import annotations

import atexit
import os
import secrets
import shutil
import tempfile
from pathlib import Path

# -- Configuration ---------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[4]
_DATA_DIR = _REPO_ROOT / "data"

# Repo-side artifact subdirs that historical (pre-ephemeral) code wrote
# into. purge_repo_artifacts() cleans these. Add new entries here if any
# adapter ever stops respecting ephemeral mode.
_REPO_ARTIFACT_DIRS: tuple[str, ...] = ("flipped", "ela")
_REPO_PHASH_DB = _DATA_DIR / "phash-db.jsonl"


# -- Public API ------------------------------------------------------------


def is_ephemeral_mode() -> bool:
    """Default True. Returns False ONLY when OSINT_EPHEMERAL_MODE is the
    explicit string "0". Anything else (unset, "1", "true", garbage) is
    treated as ephemeral-on. Failure mode favors privacy: if the env var
    is malformed, we stay ephemeral.
    """
    return os.environ.get("OSINT_EPHEMERAL_MODE", "1").strip() != "0"


def random_token(n_bytes: int = 6) -> str:
    """Short URL-safe token for tempdir suffixes + opt-out artifact names.

    n_bytes=6 -> 8 base64 chars; collision-resistant within a process
    lifetime and reveals nothing about who/what is being investigated.
    """
    return secrets.token_urlsafe(n_bytes)


_PROCESS_TMPDIR: Path | None = None


def ephemeral_artifact_dir() -> Path:
    """Lazy-init the per-process opt-out artifact tmpdir.

    NEVER call this in ephemeral mode (ephemeral mode never touches
    disk). Only the explicitly-opted-out forensic path uses it.

    Lives under %TEMP%/osint-goblin-<random>/ so:
      - Filename + directory name reveal nothing about investigations.
      - Lives outside the repo so a forgotten cleanup doesn't pollute git.
      - Registered for atexit shutdown_cleanup() so even an abrupt
        process exit drops the bytes.
    """
    global _PROCESS_TMPDIR
    if _PROCESS_TMPDIR is None:
        _PROCESS_TMPDIR = Path(tempfile.mkdtemp(prefix=f"osint-goblin-{random_token()}-"))
        atexit.register(shutdown_cleanup)
    return _PROCESS_TMPDIR


def shutdown_cleanup() -> None:
    """Nuke the per-process tmpdir on shutdown.

    Idempotent + best-effort. Registered automatically by the first
    ephemeral_artifact_dir() call. Can also be invoked manually from
    a sigterm handler or test teardown.
    """
    global _PROCESS_TMPDIR
    if _PROCESS_TMPDIR is not None and _PROCESS_TMPDIR.is_dir():
        shutil.rmtree(_PROCESS_TMPDIR, ignore_errors=True)
    _PROCESS_TMPDIR = None


def purge_repo_artifacts() -> dict[str, int]:
    """Emergency-clean any leftover bytes in the repo's data/ dir.

    Earlier (pre-ephemeral) sessions wrote into data/flipped, data/ela,
    and data/phash-db.jsonl. This wipes them. Safe to run anytime --
    idempotent, returns 0s if data/ is already clean.

    Returns per-target removal counts for audit.
    """
    counts: dict[str, int] = {}
    for sub in _REPO_ARTIFACT_DIRS:
        d = _DATA_DIR / sub
        count = 0
        if d.is_dir():
            for p in d.iterdir():
                if p.is_file():
                    try:
                        p.unlink()
                        count += 1
                    except OSError:
                        pass
        counts[sub] = count
    phash_count = 0
    if _REPO_PHASH_DB.is_file():
        try:
            with _REPO_PHASH_DB.open("r", encoding="utf-8") as fh:
                phash_count = sum(1 for line in fh if line.strip())
            _REPO_PHASH_DB.unlink()
        except OSError:
            pass
    counts["phash_db"] = phash_count
    return counts


# -- In-memory phash store (replaces data/phash-db.jsonl) -----------------


class _PhashMemoryStore:
    """Process-memory replacement for data/phash-db.jsonl.

    Same access shape as the old append-and-scan logic. Lives for the
    process lifetime; dropped on shutdown_cleanup() / interpreter exit.
    No disk surface, no investigation tagging, no row contents that
    could be exfiltrated by reading a file.
    """

    def __init__(self) -> None:
        # phash_hex -> list of image_urls (oldest first).
        self._by_hash: dict[str, list[str]] = {}

    def add(self, phash: str, image_url: str) -> None:
        if not phash:
            return
        self._by_hash.setdefault(phash, []).append(image_url)

    def lookup(self, phash: str) -> list[str]:
        return list(self._by_hash.get(phash, ()))

    def clear(self) -> None:
        self._by_hash.clear()

    def size(self) -> int:
        return sum(len(v) for v in self._by_hash.values())


_phash_store = _PhashMemoryStore()


def phash_store() -> _PhashMemoryStore:
    """Singleton accessor for the process-memory phash store.

    Adapters call this in place of opening data/phash-db.jsonl.
    Tests can call .clear() between cases.
    """
    return _phash_store

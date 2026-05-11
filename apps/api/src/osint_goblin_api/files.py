"""GET /files/{rel_path} -- static file serve from the workspace data root.

Mei-Lan M1 (2026-05-11) needs inline image thumbnails in EventRow for
image-match events that carry a `flipped_path` or `flipped_rel`. The web
side can't render those without an HTTP surface for the data/ tree.

Camille's path-traversal containment contract (see test_files.py):

  1. Resolve the requested path under the data root; reject anything whose
     resolved real-path does not start with data_root.resolve().
  2. Reject `..` segments pre-resolve. Defense in depth: even if the
     resolved path stays in-tree (e.g. flipped/../flipped/x.jpg), the
     literal `..` is a tell that the client is probing.
  3. Allowlist subdirs. data/ also holds minio-fs/ (warc archives, case
     material) and phash-db.jsonl -- neither should ever be reachable by
     URL guessing. Only subdirs explicitly intended for web preview are
     on the list.
  4. 404 on every failure mode. Never 5xx, never 403 -- both leak info.
  5. Content-Type from extension; unknown -> octet-stream. Never text/html
     (that's the XSS pivot for any user-uploaded content).

Future subdirs to whitelist as workflows grow: captures/, exif-strips/,
thumbnails/. Add them by name -- never expand to all-of-data.
"""

from __future__ import annotations

import mimetypes
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

# Subdirs of data/ that are safe to expose by URL.
#   flipped/ = mirror-flipped variants (image_flip_check)
#   ela/     = Error-Level-Analysis glow-maps (image_ela_check) -- the
#              daily-driver "is this listing photo doctored?" tell
_ALLOWED_SUBDIRS: frozenset[str] = frozenset({"flipped", "ela"})

# Repo root: apps/api/src/osint_goblin_api/files.py -> ../../../../
_DEFAULT_DATA_ROOT = Path(__file__).resolve().parents[4] / "data"


def _data_root() -> Path:
    """Resolved data root. Honors OSINT_DATA_ROOT env override so tests
    can point at a tmp dir; default to the workspace's data/ tree."""
    override = os.environ.get("OSINT_DATA_ROOT")
    root = Path(override) if override else _DEFAULT_DATA_ROOT
    return root.resolve()


router = APIRouter()


@router.get("/files/{rel_path:path}")
def serve_file(rel_path: str) -> FileResponse:
    # Pre-resolve guards. Every failure returns 404 -- never leak which
    # check tripped.
    if not rel_path:
        raise HTTPException(status_code=404)
    if ".." in rel_path.split("/"):
        raise HTTPException(status_code=404)
    if ".." in rel_path.split("\\"):
        raise HTTPException(status_code=404)
    if rel_path.startswith(("/", "\\")):
        raise HTTPException(status_code=404)
    # Windows drive prefix (C:, D:, etc.) is an absolute path tell.
    if len(rel_path) >= 2 and rel_path[1] == ":":
        raise HTTPException(status_code=404)

    # First segment must be on the allowlist.
    first = rel_path.split("/", 1)[0]
    if first not in _ALLOWED_SUBDIRS:
        raise HTTPException(status_code=404)

    root = _data_root()
    candidate = (root / rel_path).resolve()

    # Containment check: resolved path must live under the resolved root.
    # Use Path.is_relative_to (3.9+) for the canonical comparison.
    if not candidate.is_relative_to(root):
        raise HTTPException(status_code=404)
    if not candidate.is_file():
        raise HTTPException(status_code=404)

    # Content-Type from extension. mimetypes.guess_type returns None for
    # unknown extensions; fall back to octet-stream rather than text/html.
    ctype, _enc = mimetypes.guess_type(candidate.name)
    if not ctype:
        ctype = "application/octet-stream"

    return FileResponse(candidate, media_type=ctype)

"""Tests for GET /files/{rel_path} -- Mei-Lan M1 inline-thumbnail surface
with Camille path-traversal containment (2026-05-11).

Camille's contract:
  1. Serve only files whose resolved absolute path is under the data root.
  2. Reject any rel_path with `..` segments pre-resolve (defense in depth).
  3. Restrict to an allowlist of subdirs so an attacker can't fish the
     full data tree even with a valid traversal-free path.
  4. 404 (not 5xx, not 403) on anything that fails any check. Don't leak
     existence of files outside the safe surface.
  5. Set Content-Type from extension; never default to text/html (XSS).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the API's data-root at a tmp dir so tests don't touch the
    real repo data/ tree. The static-serve module reads the env var at
    request time (not import time) so a per-test override works."""
    root = tmp_path / "data"
    (root / "flipped").mkdir(parents=True)
    (root / "ela").mkdir(parents=True)
    monkeypatch.setenv("OSINT_DATA_ROOT", str(root))
    return root


def test_serves_file_under_allowlisted_subdir(client: TestClient, data_root: Path) -> None:
    target = data_root / "flipped" / "abc123.jpg"
    target.write_bytes(b"\xff\xd8\xff\xe0fake-jpeg-bytes")

    resp = client.get("/files/flipped/abc123.jpg")
    assert resp.status_code == 200
    assert resp.content == b"\xff\xd8\xff\xe0fake-jpeg-bytes"
    assert resp.headers["content-type"].startswith("image/jpeg")


def test_404_on_missing_file(client: TestClient, data_root: Path) -> None:
    resp = client.get("/files/flipped/does-not-exist.jpg")
    assert resp.status_code == 404


def test_rejects_dotdot_traversal(client: TestClient, data_root: Path) -> None:
    # Even if the resolved path lands in data_root, `..` in the URL is a
    # pre-resolve red flag. Reject before resolution.
    resp = client.get("/files/flipped/../flipped/abc.jpg")
    assert resp.status_code == 404


def test_rejects_absolute_path(client: TestClient, data_root: Path) -> None:
    # FastAPI's path converter passes through leading slashes; verify the
    # handler doesn't fall for an absolute system path.
    resp = client.get("/files//etc/passwd")
    assert resp.status_code == 404


def test_rejects_non_allowlisted_subdir(client: TestClient, data_root: Path) -> None:
    # File exists under data_root but in a subdir not on the allowlist.
    other = data_root / "minio-fs" / "warc.gz"
    other.parent.mkdir(parents=True)
    other.write_bytes(b"sensitive-case-material")

    resp = client.get("/files/minio-fs/warc.gz")
    assert resp.status_code == 404


def test_content_type_from_extension(client: TestClient, data_root: Path) -> None:
    png = data_root / "flipped" / "img.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    resp = client.get("/files/flipped/img.png")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("image/png")


def test_unknown_extension_falls_back_to_octet_stream(client: TestClient, data_root: Path) -> None:
    weird = data_root / "flipped" / "noext"
    weird.write_bytes(b"raw")
    resp = client.get("/files/flipped/noext")
    assert resp.status_code == 200
    # Never text/html (that's the XSS pivot) -- octet-stream is safe.
    assert resp.headers["content-type"] == "application/octet-stream"


def test_serves_ela_subdir(client: TestClient, data_root: Path) -> None:
    # ELA visualizations land under data/ela/ via image_ela_check. The
    # allowlist must include ela/ so EventRow can render them inline.
    target = data_root / "ela" / "x.jpg"
    target.write_bytes(b"\xff\xd8\xff\xe0ela-glow-map")
    resp = client.get("/files/ela/x.jpg")
    assert resp.status_code == 200
    assert resp.content == b"\xff\xd8\xff\xe0ela-glow-map"

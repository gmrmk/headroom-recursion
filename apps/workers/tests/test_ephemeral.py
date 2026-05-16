"""Privacy invariant tests: the Naomi gate must hold structurally.

Locks in:
  1. is_ephemeral_mode() defaults True (no env var set).
  2. OSINT_EPHEMERAL_MODE=0 is the ONLY value that disables ephemeral.
  3. random_token() doesn't repeat (anti-correlation).
  4. The phash store is in-memory only (no disk surface).
  5. image_flip_check + image_ela_check + image_pdq_hash leave zero
     files in repo data/ when ephemeral mode is on (default).
  6. purge_repo_artifacts is idempotent and clears any pre-existing
     leftovers safely.

Tests live entirely on synthetic 1x1 PNG bytes -- no network, no real
landlord photos retrieved during CI.
"""

from __future__ import annotations

import io

import pytest
from osint_goblin_workers import ephemeral
from osint_goblin_workers.ephemeral import (
    is_ephemeral_mode,
    phash_store,
    purge_repo_artifacts,
    random_token,
)

# ---------------------------------------------------------------------------
# Mode flag semantics
# ---------------------------------------------------------------------------


class TestEphemeralMode:
    def test_default_is_ephemeral(self, monkeypatch):
        monkeypatch.delenv("OSINT_EPHEMERAL_MODE", raising=False)
        assert is_ephemeral_mode() is True

    def test_explicit_one_is_ephemeral(self, monkeypatch):
        monkeypatch.setenv("OSINT_EPHEMERAL_MODE", "1")
        assert is_ephemeral_mode() is True

    def test_only_explicit_zero_disables(self, monkeypatch):
        monkeypatch.setenv("OSINT_EPHEMERAL_MODE", "0")
        assert is_ephemeral_mode() is False

    def test_garbage_value_stays_ephemeral(self, monkeypatch):
        # Failure mode favors privacy: bad value -> stay ephemeral.
        monkeypatch.setenv("OSINT_EPHEMERAL_MODE", "yes")
        assert is_ephemeral_mode() is True

    def test_empty_string_stays_ephemeral(self, monkeypatch):
        monkeypatch.setenv("OSINT_EPHEMERAL_MODE", "")
        assert is_ephemeral_mode() is True

    def test_whitespace_zero_disables(self, monkeypatch):
        # Strip is applied, so "  0  " should still disable.
        monkeypatch.setenv("OSINT_EPHEMERAL_MODE", "  0  ")
        assert is_ephemeral_mode() is False


# ---------------------------------------------------------------------------
# random_token + safe_investigation_id
# ---------------------------------------------------------------------------


class TestRandomToken:
    def test_short_default_length(self):
        t = random_token()
        # 6 bytes -> 8 base64 chars
        assert len(t) == 8

    def test_unique_across_calls(self):
        # 1000 calls; not allowed to collide.
        seen = {random_token() for _ in range(1000)}
        assert len(seen) == 1000


# ---------------------------------------------------------------------------
# phash in-memory store
# ---------------------------------------------------------------------------


class TestPhashStore:
    def test_singleton_returns_same_instance(self):
        assert phash_store() is phash_store()

    def test_add_and_lookup(self):
        s = phash_store()
        s.clear()
        s.add("abc123", "https://x.com/1.jpg")
        s.add("abc123", "https://x.com/2.jpg")
        s.add("def456", "https://y.com/1.jpg")
        assert s.lookup("abc123") == [
            "https://x.com/1.jpg",
            "https://x.com/2.jpg",
        ]
        assert s.lookup("def456") == ["https://y.com/1.jpg"]
        assert s.lookup("never-seen") == []

    def test_clear_drops_everything(self):
        s = phash_store()
        s.add("z", "https://z.com/1.jpg")
        s.clear()
        assert s.size() == 0
        assert s.lookup("z") == []

    def test_size_counts_urls_not_hashes(self):
        s = phash_store()
        s.clear()
        s.add("h1", "u1")
        s.add("h1", "u2")
        s.add("h2", "u3")
        assert s.size() == 3

    def test_no_disk_writes(self, tmp_path, monkeypatch):
        # Working CWD doesn't matter; the store has zero filesystem
        # surface. Run a bunch of ops and confirm nothing landed.
        monkeypatch.chdir(tmp_path)
        s = phash_store()
        s.clear()
        for i in range(50):
            s.add(f"hash-{i:04d}", f"https://example.com/{i}.jpg")
        assert sum(1 for _ in tmp_path.iterdir()) == 0


# ---------------------------------------------------------------------------
# purge_repo_artifacts -- emergency wipe
# ---------------------------------------------------------------------------


class TestPurgeRepoArtifacts:
    def test_idempotent_on_empty_repo(self, tmp_path, monkeypatch):
        # Re-point _DATA_DIR at a clean tmpdir so the test isolation
        # holds regardless of the host's repo state.
        monkeypatch.setattr(ephemeral, "_DATA_DIR", tmp_path)
        monkeypatch.setattr(ephemeral, "_REPO_PHASH_DB", tmp_path / "phash-db.jsonl")
        result = purge_repo_artifacts()
        assert result["flipped"] == 0
        assert result["ela"] == 0
        assert result["phash_db"] == 0

    def test_removes_leftover_jpgs(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ephemeral, "_DATA_DIR", tmp_path)
        monkeypatch.setattr(ephemeral, "_REPO_PHASH_DB", tmp_path / "phash-db.jsonl")
        # Stage some leftover artifacts as if from a prior non-ephemeral run.
        (tmp_path / "flipped").mkdir()
        (tmp_path / "ela").mkdir()
        (tmp_path / "flipped" / "abc.jpg").write_bytes(b"FAKE")
        (tmp_path / "flipped" / "def.jpg").write_bytes(b"FAKE")
        (tmp_path / "ela" / "ghi.jpg").write_bytes(b"FAKE")
        (tmp_path / "phash-db.jsonl").write_text(
            '{"phash": "x", "image_url": "y"}\n', encoding="utf-8"
        )

        result = purge_repo_artifacts()
        assert result["flipped"] == 2
        assert result["ela"] == 1
        assert result["phash_db"] == 1
        # Verify ground truth.
        assert list((tmp_path / "flipped").iterdir()) == []
        assert list((tmp_path / "ela").iterdir()) == []
        assert not (tmp_path / "phash-db.jsonl").exists()


# ---------------------------------------------------------------------------
# Adapter integration: zero-disk-footprint invariant under default ephemeral
# ---------------------------------------------------------------------------


def _tiny_png_bytes() -> bytes:
    """Produce a real 1x1 PNG; small enough to be in-memory only."""
    try:
        from PIL import Image
    except ImportError:
        pytest.skip("Pillow not installed; skipping image-adapter integration")
    buf = io.BytesIO()
    Image.new("RGB", (1, 1), color=(127, 127, 127)).save(buf, format="PNG")
    return buf.getvalue()


class TestImageAdaptersHonorEphemeral:
    """End-to-end: ephemeral mode on -> the three write-prone adapters
    leave zero artifacts in the repo's data/ dir. Uses a 1x1 PNG in-
    process so no network."""

    def _run_adapters_with_inline_bytes(self, monkeypatch, png_bytes: bytes):
        # Monkeypatch _fetch_image_bytes to skip network and return our
        # in-memory tiny PNG. The adapters call _fetch_image_bytes
        # internally; we stub it module-wide.
        from osint_goblin_workers import adapters_image

        monkeypatch.setattr(
            adapters_image,
            "_fetch_image_bytes",
            lambda url, timeout_s=15.0: (png_bytes, "image/png"),
        )
        from osint_goblin_workers.adapters_image import (
            image_ela_check,
            image_flip_check,
            image_pdq_hash,
        )

        url = "https://example.com/tiny.png"
        image_flip_check({"image_url": url, "output_format": "file"})
        image_ela_check({"image_url": url})
        image_pdq_hash({"image_url": url})

    def test_default_ephemeral_writes_nothing_to_data(self, monkeypatch, tmp_path):
        monkeypatch.delenv("OSINT_EPHEMERAL_MODE", raising=False)
        # Re-point _REPO_ROOT so the (irrelevant) `data/` path the
        # adapters might compute lands in a fresh tmpdir, not the
        # developer's repo data/.
        from osint_goblin_workers import adapters_image, ephemeral

        monkeypatch.setattr(adapters_image, "_REPO_ROOT", tmp_path)
        monkeypatch.setattr(ephemeral, "_DATA_DIR", tmp_path / "data")
        monkeypatch.setattr(ephemeral, "_REPO_PHASH_DB", tmp_path / "data" / "phash-db.jsonl")

        png = _tiny_png_bytes()
        self._run_adapters_with_inline_bytes(monkeypatch, png)

        # Walk the tmpdir; assert that NO files exist under data/.
        data = tmp_path / "data"
        files = list(data.rglob("*")) if data.is_dir() else []
        leaked = [p for p in files if p.is_file()]
        assert leaked == [], f"Naomi-gate VIOLATION: {leaked!r}"

    def test_phash_lookup_works_across_calls_without_disk(self, monkeypatch):
        """Cross-listing dedupe still works -- the in-memory store is the
        replacement for the JSONL audit trail, not just a stub.

        phash_dedupe is the lookup+store adapter; image_pdq_hash is
        compute-only. The test exercises the dedupe one across two
        synthetic listings with identical bytes.
        """
        png = _tiny_png_bytes()
        from osint_goblin_workers import adapters_image

        monkeypatch.setattr(
            adapters_image,
            "_fetch_image_bytes",
            lambda url, timeout_s=15.0: (png, "image/png"),
        )
        from osint_goblin_workers.adapters_image import phash_dedupe

        phash_store().clear()
        # First listing hashes the image -> stored.
        phash_dedupe({"image_url": "https://listing-1.com/photo.jpg"})
        # Second listing with the SAME image bytes -> should see a match.
        evs2 = phash_dedupe({"image_url": "https://listing-2.com/photo.jpg"})
        matches_seen = [
            ev
            for ev in evs2
            if ev.get("event_type") == "image-match" and "prior_image_url" in ev.get("payload", {})
        ]
        assert matches_seen, "phash store didn't surface the cross-listing duplicate"
        assert matches_seen[0]["payload"]["prior_image_url"] == "https://listing-1.com/photo.jpg"

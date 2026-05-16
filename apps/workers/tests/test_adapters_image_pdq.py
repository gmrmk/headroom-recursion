"""Tests for image_pdq_hash -- PDQ perceptual hash adapter (W4-PDQ-PIPE).

Wave-4 Margaret roadmap §4. Locks the wire shape + the fallback path when
the pdqhash C extension is unavailable. Performance gate (<15ms/image
average) runs only when pdqhash is actually installed.

severity_basis: matrix:PV_LISTING_PHOTO_PDQ_CROSS_PLATFORM
"""

from __future__ import annotations

import io
import time

import pytest
from osint_goblin_workers.adapters import get_registry
from osint_goblin_workers.adapters_image import (
    _PDQ_AVAILABLE,
    _image_pdq_hash_synthetic,
    _pdq_bits_to_hex,
    image_pdq_hash,
)


def _make_jpeg(width: int = 256, height: int = 256) -> bytes:
    """Tiny synthetic JPEG fixture -- gradient so PDQ has real signal to hash."""
    from PIL import Image

    img = Image.new("RGB", (width, height))
    pixels = img.load()
    assert pixels is not None  # type narrowing for pyright/mypy
    for y in range(height):
        for x in range(width):
            pixels[x, y] = (x % 256, y % 256, (x + y) % 256)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Registration sanity
# ---------------------------------------------------------------------------


def test_image_pdq_hash_registered() -> None:
    entry = get_registry().get("image_pdq_hash")
    assert entry is not None
    assert entry.synthetic_mode is not None
    assert entry.in_process is True


# ---------------------------------------------------------------------------
# Synthetic wire shape -- always runs, locks the contract
# ---------------------------------------------------------------------------


def test_synthetic_emits_image_match_and_summary() -> None:
    """Synthetic mode locks the wire: one image-match (with pdq_hash_hex +
    pdq_quality + severity_basis), one tool-run-result summary."""
    events = _image_pdq_hash_synthetic({"image_url": "https://example.com/x.jpg"})
    assert len(events) == 2

    match = events[0]
    assert match["event_type"] == "image-match"
    p = match["payload"]
    assert p["source"] == "image_pdq_hash"
    assert p["image_url"] == "https://example.com/x.jpg"
    assert isinstance(p["pdq_hash_hex"], str)
    assert len(p["pdq_hash_hex"]) == 64  # 256 bits = 64 hex chars
    assert isinstance(p["pdq_quality"], int)
    assert 0 <= p["pdq_quality"] <= 100
    assert p["severity_basis"] == "matrix:PV_LISTING_PHOTO_PDQ_CROSS_PLATFORM"
    assert p["synthetic"] is True

    summary = events[1]
    assert summary["event_type"] == "tool-run-result"
    assert summary["payload"]["pdq_hash_hex"] == p["pdq_hash_hex"]
    assert summary["payload"]["synthetic"] is True


def test_missing_image_url_returns_tool_run_error() -> None:
    events = image_pdq_hash({})
    assert len(events) == 1
    assert events[0]["event_type"] == "tool-run-error"
    assert "missing 'image_url'" in events[0]["payload"]["reason"]


# ---------------------------------------------------------------------------
# Fallback path -- exercised when the pdqhash C extension isn't installed
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    _PDQ_AVAILABLE,
    reason="pdqhash is installed; fallback-emit path doesn't fire",
)
def test_skipped_when_pdqhash_unavailable() -> None:
    """If the C extension didn't build, the adapter must surface a clean
    tool-run-error with install instructions -- never crash the worker."""
    events = image_pdq_hash({"image_url": "https://example.com/x.jpg"})
    assert len(events) == 1
    assert events[0]["event_type"] == "tool-run-error"
    reason = events[0]["payload"]["reason"]
    assert "pdqhash" in reason
    assert "install" in reason.lower()


# ---------------------------------------------------------------------------
# Hex packing -- the 256-bit -> 64-hex conversion
# ---------------------------------------------------------------------------


def test_pdq_bits_to_hex_packs_msb_first() -> None:
    """numpy.packbits is MSB-first by default, matching PDQ reference.
    All-ones bits -> 'ff' * 32; all-zeros -> '00' * 32; alternating ->
    'aa' * 32 (10101010)."""
    import numpy as np

    all_ones = np.ones(256, dtype=np.uint8)
    assert _pdq_bits_to_hex(all_ones) == "ff" * 32

    all_zeros = np.zeros(256, dtype=np.uint8)
    assert _pdq_bits_to_hex(all_zeros) == "00" * 32

    alternating = np.array([1, 0] * 128, dtype=np.uint8)
    assert _pdq_bits_to_hex(alternating) == "aa" * 32


def test_pdq_bits_to_hex_rejects_wrong_length() -> None:
    import numpy as np

    with pytest.raises(ValueError, match="256 bits"):
        _pdq_bits_to_hex(np.zeros(128, dtype=np.uint8))


# ---------------------------------------------------------------------------
# Performance gate -- only meaningful when pdqhash is actually installed
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _PDQ_AVAILABLE,
    reason="pdqhash extension not installed; can't measure native perf",
)
def test_perf_under_15ms_per_image_avg() -> None:
    """Steinebach PHASER reports <10ms per image. Allow headroom -> 15ms.

    Hits the inner compute path only (not the HTTP fetch) -- we monkey
    out _fetch_image_bytes so the timing measures the hash work, which
    is the only part Steinebach's number describes.
    """
    from osint_goblin_workers import adapters_image

    jpeg_bytes = _make_jpeg(256, 256)

    def fake_fetch(url: str, timeout_s: float = 20.0) -> tuple[bytes, str]:
        return jpeg_bytes, "image/jpeg"

    original = adapters_image._fetch_image_bytes
    adapters_image._fetch_image_bytes = fake_fetch  # type: ignore[assignment]
    try:
        # Warm-up so we measure steady-state, not first-call JIT.
        image_pdq_hash({"image_url": "https://example.com/warmup.jpg"})

        runs = 10
        start = time.perf_counter()
        for _ in range(runs):
            events = image_pdq_hash({"image_url": "https://example.com/x.jpg"})
            assert events[0]["event_type"] == "image-match"
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        avg_ms = elapsed_ms / runs
    finally:
        adapters_image._fetch_image_bytes = original  # type: ignore[assignment]

    assert avg_ms < 15.0, f"PDQ avg {avg_ms:.2f}ms exceeds 15ms gate"

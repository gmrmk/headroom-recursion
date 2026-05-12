"""Tests for image_ai_local_detect -- local-only AI-image heuristic detector.

Locks the wire shape + the score-to-likelihood rubric. Each heuristic
(EXIF / dimensions / filename / PNG chunks / C2PA) tested in isolation
so a future refactor can't silently break one signal.

Discipline: synthetic mode tests verify wire shape; rubric tests use
synthesized image bytes (controlled fixtures) instead of fetching real
images, so the tests are hermetic + fast.
"""

from __future__ import annotations

import io

import pytest
from osint_goblin_workers.adapters import get_registry
from osint_goblin_workers.adapters_image import (
    _AI_DEFAULT_SIZES,
    _c2pa_byte_scan,
    _dimension_ai_signals,
    _filename_ai_signals,
    _image_ai_local_detect_synthetic,
    _png_chunk_ai_signals,
    _score_to_likelihood,
    image_ai_local_detect,
)


def _make_png(width: int, height: int, text_chunks: list[tuple[str, str]] | None = None) -> bytes:
    """Build a minimal valid PNG with given dimensions and optional tEXt chunks."""
    from PIL import Image, PngImagePlugin

    img = Image.new("RGB", (width, height), color="white")
    meta = PngImagePlugin.PngInfo()
    for key, value in text_chunks or []:
        meta.add_text(key, value)
    buf = io.BytesIO()
    img.save(buf, format="PNG", pnginfo=meta)
    return buf.getvalue()


def _make_jpeg(width: int, height: int) -> bytes:
    """Plain JPEG with no EXIF -- the simplest "could be AI" fixture."""
    from PIL import Image

    img = Image.new("RGB", (width, height), color="white")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Registration sanity
# ---------------------------------------------------------------------------


def test_image_ai_local_detect_registered() -> None:
    entry = get_registry().get("image_ai_local_detect")
    assert entry is not None
    assert entry.synthetic_mode is not None


# ---------------------------------------------------------------------------
# Synthetic wire shape
# ---------------------------------------------------------------------------


def test_synthetic_emits_image_match_and_summary() -> None:
    """Synthetic mode locks the wire shape: one image-match with the
    ai_likelihood verdict + reasons + per-signal breakdown, then a
    tool-run-result summary."""
    events = _image_ai_local_detect_synthetic({"image_url": "https://example.com/x.png"})
    assert len(events) == 2
    assert events[0]["event_type"] == "image-match"
    assert events[0]["payload"]["source"] == "ai_local_detect"
    assert events[0]["payload"]["ai_likelihood"] in ("none", "low", "medium", "high")
    assert "score" in events[0]["payload"]
    assert isinstance(events[0]["payload"]["reasons"], list)
    assert "signals" in events[0]["payload"]
    assert events[1]["event_type"] == "tool-run-result"
    assert events[1]["payload"].get("synthetic") is True


# ---------------------------------------------------------------------------
# Score-to-likelihood rubric
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "score,expected",
    [
        (0, "none"),
        (1, "low"),
        (2, "low"),
        (3, "medium"),
        (4, "medium"),
        (5, "high"),
        (10, "high"),
    ],
)
def test_score_to_likelihood_thresholds(score: int, expected: str) -> None:
    """The rubric: 0=none, 1-2=low, 3-4=medium, 5+=high. Pinned so the
    dashboard's color-coding doesn't drift."""
    assert _score_to_likelihood(score) == expected


# ---------------------------------------------------------------------------
# Heuristic units
# ---------------------------------------------------------------------------


def test_dimension_signal_known_ai_default_size_scores_2() -> None:
    """1024x1024 exactly matches a known AI generator default size."""
    png = _make_png(1024, 1024)
    sig = _dimension_ai_signals(png)
    assert sig["width"] == 1024
    assert sig["height"] == 1024
    assert sig["score"] == 2
    assert any("known AI generator default" in r for r in sig["reasons"])


def test_dimension_signal_64_multiples_score_1() -> None:
    """A 64-multiple size not in the default set scores 1."""
    png = _make_png(640, 384)  # both /64, not in default set
    sig = _dimension_ai_signals(png)
    assert sig["score"] == 1


def test_dimension_signal_phone_size_scores_0() -> None:
    """A typical phone-camera size (not /64, not in defaults) scores 0."""
    png = _make_png(4032, 3024)  # iPhone 13 native
    sig = _dimension_ai_signals(png)
    assert sig["score"] == 0


def test_dimension_default_sizes_set_contains_common_ai_defaults() -> None:
    """Anchor the default-size set so future refactors don't silently
    drop a generator's signature size."""
    expected = {(512, 512), (1024, 1024), (1024, 1792), (1792, 1024), (768, 1024)}
    assert expected.issubset(_AI_DEFAULT_SIZES)


def test_filename_signal_detects_dalle() -> None:
    sig = _filename_ai_signals("https://example.com/dalle3_kitchen.png")
    assert sig["score"] == 1
    assert any("dalle" in r.lower() for r in sig["reasons"])


def test_filename_signal_detects_midjourney_with_space() -> None:
    """Tokens are matched case-insensitively + space-stripped."""
    sig = _filename_ai_signals("https://example.com/midjourney_output.jpg")
    assert sig["score"] == 1


def test_filename_signal_no_match() -> None:
    sig = _filename_ai_signals("https://example.com/IMG_3421.jpg")
    assert sig["score"] == 0


def test_png_chunk_detects_a1111_parameters() -> None:
    """A1111 / SD WebUI writes the generation parameters into a tEXt
    chunk with key 'parameters'. This is the single most reliable
    single signal for AI generation."""
    png = _make_png(
        512,
        512,
        text_chunks=[("parameters", "prompt: a cottage, Steps: 30, Sampler: DPM++")],
    )
    sig = _png_chunk_ai_signals(png)
    assert sig["score"] == 5
    assert any("parameters" in r.lower() for r in sig["reasons"])


def test_png_chunk_detects_comfyui_workflow() -> None:
    """ComfyUI writes a 'workflow' (or 'comfy_workflow') tEXt chunk."""
    png = _make_png(1024, 1024, text_chunks=[("workflow", '{"nodes": []}')])
    sig = _png_chunk_ai_signals(png)
    assert sig["score"] == 5


def test_png_chunk_no_match_on_plain_png() -> None:
    """A plain PNG with no text chunks scores 0 on this heuristic."""
    png = _make_png(800, 600)
    sig = _png_chunk_ai_signals(png)
    assert sig["score"] == 0


def test_png_chunk_no_match_on_jpeg() -> None:
    """JPEG bytes don't trigger PNG chunk parsing (signature check)."""
    jpg = _make_jpeg(800, 600)
    sig = _png_chunk_ai_signals(jpg)
    assert sig["score"] == 0


def test_c2pa_byte_scan_detects_jumb_box() -> None:
    """C2PA presence by raw byte search for the JUMBF box magic.
    Not itself an AI verdict -- score is 0, but `c2pa_present` flag set."""
    fake = b"\xff\xd8\xff\xe0" + b"x" * 100 + b"jumb" + b"y" * 100
    sig = _c2pa_byte_scan(fake)
    assert sig["c2pa_present"] is True
    assert sig["score"] == 0


def test_c2pa_byte_scan_no_match_on_plain_image() -> None:
    sig = _c2pa_byte_scan(_make_jpeg(800, 600))
    assert sig["c2pa_present"] is False


# ---------------------------------------------------------------------------
# Integration: full adapter against a controlled image fixture
# ---------------------------------------------------------------------------


def test_adapter_missing_image_url_returns_error() -> None:
    events = image_ai_local_detect({})
    assert events[0]["event_type"] == "tool-run-error"
    assert "image_url" in events[0]["payload"]["reason"]


def test_adapter_returns_high_likelihood_on_sd_png(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A PNG with A1111 parameters chunk + 1024x1024 dimensions + no
    EXIF should score 'high' (5 PNG + 2 dimensions + 2 EXIF = 9)."""
    png = _make_png(
        1024,
        1024,
        text_chunks=[("parameters", "Steps: 30, Sampler: DPM++ 2M Karras")],
    )

    def fake_fetch(url: str, timeout_s: float = 15.0) -> tuple[bytes, str]:
        return png, "image/png"

    from osint_goblin_workers import adapters_image

    monkeypatch.setattr(adapters_image, "_fetch_image_bytes", fake_fetch)
    events = image_ai_local_detect({"image_url": "https://example.com/output.png"})
    assert events[0]["event_type"] == "image-match"
    assert events[0]["payload"]["ai_likelihood"] == "high"
    assert events[0]["payload"]["score"] >= 5


def test_adapter_returns_none_on_typical_phone_jpeg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A plain JPEG at a phone-camera size, no EXIF generator marker.
    Bare-minimum EXIF still triggers some +1 absence signals; we accept
    'low' as the upper bound here -- this is the conservative side of
    the rubric where investigator manual review is expected."""
    jpg = _make_jpeg(4032, 3024)

    def fake_fetch(url: str, timeout_s: float = 15.0) -> tuple[bytes, str]:
        return jpg, "image/jpeg"

    from osint_goblin_workers import adapters_image

    monkeypatch.setattr(adapters_image, "_fetch_image_bytes", fake_fetch)
    events = image_ai_local_detect({"image_url": "https://example.com/IMG_3421.jpg"})
    assert events[0]["event_type"] == "image-match"
    # Phone JPEG with no EXIF: scores 2 (no camera, no datetime) at most.
    # The rubric returns "low" for score 1-2. Not "none" because we have
    # to assume zero-EXIF could be AI; the investigator combines this
    # with the reverse-image-search result.
    assert events[0]["payload"]["ai_likelihood"] in ("none", "low")
    assert events[0]["payload"]["score"] < 3

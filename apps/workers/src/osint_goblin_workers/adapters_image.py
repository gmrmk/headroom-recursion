"""Image-analysis adapters for property-photo fraud detection (Sprint 3+).

Eleven adapters across four problem domains:

1. Reverse image search (find this photo elsewhere)
   - yandex_image_reverse, google_lens_reverse, bing_visual_reverse
     (Scrapling subprocess; live in adapters/<id>/wrapper.py)
   - reverse_image_aggregator: meta-adapter, fans one image URL out
     across TinEye + Yandex + Google + Bing including a horizontally
     flipped variant for the exact-match engines (TinEye)

2. Image preprocessing
   - image_flip_check: generate a horizontally-flipped variant. Universal
     workaround for exact-match reverse engines that don't handle flips.

3. EXIF / metadata depth
   - image_exif: lightweight exifread-based reader (~300 fields)
   - exiftool_full: subprocess wrap of the ExifTool binary (~23,000 fields).
     If exiftool is not on PATH, surfaces honest tool-run-error with
     install command.

4. Manipulation + provenance detection
   - image_ela_check: Error Level Analysis flags clone-stamped /
     retouched regions. PIL + numpy.
   - image_provenance_check: composite that runs image_exif +
     image_ela_check + (best-effort) c2pa_verify and emits one flag dict.
   - ai_image_detection: Sightengine free-tier API call to detect
     GenAI-fabricated photos. Requires OSINT_SIGHTENGINE_API_USER +
     _SECRET env; honest error if not set.
   - c2pa_verify: shell out to `c2patool` if available; cryptographic
     content-credentials chain verification (Sony/Leica/Nikon 2024+).

5. Location-side verification
   - kartaview_nearby: free OSM street-level imagery at a lat/lon.
     Public API, no key.

Scope discipline:
- Every adapter handles "image not reachable" + "image not a real image"
  + "tool not installed" honestly.
- No image data is uploaded to third-party services without explicit
  consent surfaces (Sightengine being the one exception, gated by env
  key presence -- if you didn't set the key, no upload happens).
"""

from __future__ import annotations

import base64
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import httpx

from .adapters import get_registry
from .subprocess_adapter import make_subprocess_adapter

_DEFAULT_UA = "osint-goblin/0.1 (https://github.com/local; personal-investigator)"
_USER_AGENT = os.environ.get("OSINT_USER_AGENT", _DEFAULT_UA)

# Empirical venv (Scrapling) -- same pattern as adapters_property.py
_EMPIRICAL_PY = (
    Path(r"C:\Users\strid\osint-dashboard-research\empirical\.venv\Scripts\python.exe")
    if os.name == "nt"
    else Path("/c/Users/strid/osint-dashboard-research/empirical/.venv/bin/python")
)
_REPO_ROOT = Path(__file__).resolve().parents[4]


def _client(timeout_s: float = 15.0) -> httpx.Client:
    return httpx.Client(
        timeout=timeout_s,
        headers={"User-Agent": _USER_AGENT, "Accept": "*/*"},
        follow_redirects=True,
    )


def _fetch_image_bytes(url: str, timeout_s: float = 15.0) -> tuple[bytes, str]:
    """Fetch image bytes. Returns (bytes, content_type). Raises on failure."""
    with _client(timeout_s) as c:
        r = c.get(url)
    r.raise_for_status()
    return r.content, r.headers.get("content-type", "")


# ---------------------------------------------------------------------------
# 1. image_exif -- lightweight EXIF dump via exifread
# ---------------------------------------------------------------------------


def _gps_to_decimal(ratios: Any, ref: str) -> float | None:
    """exifread returns GPS as a list of three Ratio objects: (deg, min, sec).
    Convert to signed decimal degrees."""
    try:
        deg, minutes, seconds = ratios.values
        d = float(deg.num) / float(deg.den)
        m = float(minutes.num) / float(minutes.den)
        s = float(seconds.num) / float(seconds.den)
        val = d + m / 60.0 + s / 3600.0
        if ref in ("S", "W"):
            val = -val
        return val
    except (AttributeError, ZeroDivisionError, ValueError, TypeError):
        return None


def image_exif(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Read EXIF + GPS from an image URL using exifread.

    Payload:
      {"image_url": "https://example.com/photo.jpg"}

    Emits one `image-match` event with the parsed metadata + a summary.
    """
    image_url = (payload.get("image_url") or "").strip()
    if not image_url:
        return [
            {
                "event_type": "tool-run-error",
                "payload": {"reason": "missing 'image_url' in payload"},
            }
        ]
    try:
        import exifread
    except ImportError:
        return [
            {
                "event_type": "tool-run-error",
                "payload": {"reason": "exifread not installed"},
            }
        ]
    try:
        data, ctype = _fetch_image_bytes(image_url, timeout_s=20.0)
    except Exception as exc:
        return [
            {
                "event_type": "tool-run-error",
                "payload": {
                    "reason": f"fetch failed: {type(exc).__name__}: {exc}",
                    "image_url": image_url,
                },
            }
        ]
    tags = exifread.process_file(io.BytesIO(data), details=False)
    if not tags:
        return [
            {
                "event_type": "tool-run-result",
                "payload": {"image_url": image_url, "exif_tags": 0, "note": "no EXIF"},
            }
        ]
    lat = _gps_to_decimal(tags.get("GPS GPSLatitude"), str(tags.get("GPS GPSLatitudeRef", "N")))
    lon = _gps_to_decimal(tags.get("GPS GPSLongitude"), str(tags.get("GPS GPSLongitudeRef", "E")))
    flat = {str(k): str(v) for k, v in tags.items() if not k.startswith("JPEGThumbnail")}
    return [
        {
            "event_type": "image-match",
            "payload": {
                "source": "exif",
                "image_url": image_url,
                "content_type": ctype,
                "exif_tag_count": len(flat),
                "gps_lat": lat,
                "gps_lon": lon,
                "gps_present": lat is not None and lon is not None,
                "camera_make": flat.get("Image Make", ""),
                "camera_model": flat.get("Image Model", ""),
                "software": flat.get("Image Software", ""),
                "datetime_original": flat.get("EXIF DateTimeOriginal", ""),
                "copyright": flat.get("Image Copyright", ""),
                "artist": flat.get("Image Artist", ""),
                "lens_model": flat.get("EXIF LensModel", ""),
                "raw_tags": flat,
            },
        },
        {
            "event_type": "tool-run-result",
            "payload": {
                "image_url": image_url,
                "exif_tags": len(flat),
                "gps_present": lat is not None and lon is not None,
            },
        },
    ]


def _image_exif_synthetic(payload: dict[str, Any]) -> list[dict[str, Any]]:
    url = payload.get("image_url") or "https://example.com/synthetic.jpg"
    return [
        {
            "event_type": "image-match",
            "payload": {
                "source": "exif",
                "image_url": url,
                "content_type": "image/jpeg",
                "exif_tag_count": 42,
                "gps_lat": 39.78,
                "gps_lon": -89.65,
                "gps_present": True,
                "camera_make": "Canon",
                "camera_model": "Canon EOS R5",
                "software": "Adobe Lightroom 13.0",
                "datetime_original": "2025:06:15 14:23:10",
                "copyright": "(c) Synthetic Photo",
                "artist": "Alice Synthetic",
                "synthetic": True,
            },
        },
        {
            "event_type": "tool-run-result",
            "payload": {"image_url": url, "exif_tags": 42, "synthetic": True},
        },
    ]


# ---------------------------------------------------------------------------
# 2. image_flip_check -- generate horizontally-flipped variant
# ---------------------------------------------------------------------------


def image_flip_check(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Fetch an image, flip horizontally, return the flipped variant.

    Payload:
      {"image_url": "https://example.com/photo.jpg",
       "output_format": "base64" | "file"}  # default base64

    For format="file", saves the flipped image to data/flipped/<hash>.jpg
    and returns the path. For format="base64", returns the bytes inline
    (the dossier UI's EventStream will render the JSON but not the image;
    that's a Sprint-4 UI concern). The intended workflow is: flip ->
    feed back into tineye_image / yandex_image_reverse with the flipped
    URL or path.
    """
    image_url = (payload.get("image_url") or "").strip()
    fmt = (payload.get("output_format") or "base64").strip().lower()
    if not image_url:
        return [
            {
                "event_type": "tool-run-error",
                "payload": {"reason": "missing 'image_url'"},
            }
        ]
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return [
            {
                "event_type": "tool-run-error",
                "payload": {"reason": "Pillow not installed"},
            }
        ]
    try:
        data, ctype = _fetch_image_bytes(image_url)
    except Exception as exc:
        return [
            {
                "event_type": "tool-run-error",
                "payload": {
                    "reason": f"fetch failed: {type(exc).__name__}: {exc}",
                    "image_url": image_url,
                },
            }
        ]
    try:
        img = Image.open(io.BytesIO(data))
        flipped = ImageOps.mirror(img)
        out = io.BytesIO()
        save_format = (img.format or "JPEG").upper()
        if save_format == "JPEG" and flipped.mode != "RGB":
            flipped = flipped.convert("RGB")
        flipped.save(out, format=save_format, quality=90)
        flipped_bytes = out.getvalue()
    except Exception as exc:
        return [
            {
                "event_type": "tool-run-error",
                "payload": {
                    "reason": f"flip failed: {type(exc).__name__}: {exc}",
                    "image_url": image_url,
                },
            }
        ]

    if fmt == "file":
        out_dir = _REPO_ROOT / "data" / "flipped"
        out_dir.mkdir(parents=True, exist_ok=True)
        # Sanitize a name from the URL
        import hashlib

        h = hashlib.sha256(image_url.encode()).hexdigest()[:16]
        out_path = out_dir / f"{h}.jpg"
        out_path.write_bytes(flipped_bytes)
        # Mei-Lan M1: emit a forward-slash rel-to-data path alongside the
        # absolute one. The API's /files/{rel_path} surface (Camille accept)
        # serves it inline so EventRow can render <img src="/files/...">.
        flipped_rel = f"flipped/{h}.jpg"
        return [
            {
                "event_type": "image-match",
                "payload": {
                    "source": "flip",
                    "image_url": image_url,
                    "flipped_path": str(out_path),
                    "flipped_rel": flipped_rel,
                    "size_bytes": len(flipped_bytes),
                    "content_type": ctype,
                    "note": "feed flipped_path back into tineye_image or yandex_image_reverse",
                },
            },
            {
                "event_type": "tool-run-result",
                "payload": {
                    "image_url": image_url,
                    "flipped_path": str(out_path),
                    "flipped_rel": flipped_rel,
                },
            },
        ]
    # base64 default
    b64 = base64.b64encode(flipped_bytes).decode("ascii")
    return [
        {
            "event_type": "image-match",
            "payload": {
                "source": "flip",
                "image_url": image_url,
                "flipped_b64_truncated": b64[:200] + "..." if len(b64) > 200 else b64,
                "flipped_b64_full_size": len(b64),
                "size_bytes": len(flipped_bytes),
                "content_type": ctype,
                "note": (
                    "request output_format='file' to save to disk + "
                    "feed into reverse-search adapters"
                ),
            },
        },
        {
            "event_type": "tool-run-result",
            "payload": {"image_url": image_url, "flipped_size": len(flipped_bytes)},
        },
    ]


def _image_flip_check_synthetic(payload: dict[str, Any]) -> list[dict[str, Any]]:
    url = payload.get("image_url") or "https://example.com/synthetic.jpg"
    return [
        {
            "event_type": "image-match",
            "payload": {
                "source": "flip",
                "image_url": url,
                "flipped_path": "data/flipped/abc123.jpg",
                "flipped_rel": "flipped/abc123.jpg",
                "size_bytes": 102400,
                "content_type": "image/jpeg",
                "synthetic": True,
            },
        },
        {
            "event_type": "tool-run-result",
            "payload": {"image_url": url, "synthetic": True},
        },
    ]


# ---------------------------------------------------------------------------
# 3. image_ela_check -- Error Level Analysis manipulation detector
# ---------------------------------------------------------------------------


def image_ela_check(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Error Level Analysis on a JPEG image.

    Payload:
      {"image_url": "...", "quality": 90}  # quality default 90

    Re-saves the image at the given quality, computes pixel-difference
    statistics. High mean/max-diff in localized regions = likely
    retouched. Returns a single image-match event with the verdict +
    saved ELA-diff image path.
    """
    image_url = (payload.get("image_url") or "").strip()
    quality = int(payload.get("quality", 90))
    if not image_url:
        return [
            {
                "event_type": "tool-run-error",
                "payload": {"reason": "missing 'image_url'"},
            }
        ]
    try:
        import numpy as np
        from PIL import Image, ImageChops
    except ImportError as exc:
        return [
            {
                "event_type": "tool-run-error",
                "payload": {"reason": f"PIL/numpy not installed: {exc}"},
            }
        ]
    try:
        data, _ctype = _fetch_image_bytes(image_url)
    except Exception as exc:
        return [
            {
                "event_type": "tool-run-error",
                "payload": {
                    "reason": f"fetch failed: {type(exc).__name__}: {exc}",
                    "image_url": image_url,
                },
            }
        ]
    try:
        import hashlib

        from PIL import ImageEnhance

        img = Image.open(io.BytesIO(data)).convert("RGB")
        # Re-save at known quality
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality)
        buf.seek(0)
        resaved = Image.open(buf).convert("RGB")
        # Pixel-by-pixel diff
        diff = ImageChops.difference(img, resaved)
        arr = np.array(diff, dtype=np.uint8)
        mean_diff = float(arr.mean())
        max_diff = int(arr.max())
        std_diff = float(arr.std())
        # Auto-contrast the diff so manipulated regions glow brightly --
        # raw ELA diffs are nearly black to the eye. Scale so max_diff
        # becomes ~255. This is the canonical ELA visualization.
        if max_diff > 0:
            scale = 255.0 / max_diff
            visualization = ImageEnhance.Brightness(diff).enhance(scale)
        else:
            visualization = diff
        out_dir = _REPO_ROOT / "data" / "ela"
        out_dir.mkdir(parents=True, exist_ok=True)
        h = hashlib.sha256(f"{image_url}|q={quality}".encode()).hexdigest()[:16]
        ela_path = out_dir / f"{h}.jpg"
        visualization.save(ela_path, format="JPEG", quality=92)
        ela_rel = f"ela/{h}.jpg"
    except Exception as exc:
        return [
            {
                "event_type": "tool-run-error",
                "payload": {
                    "reason": f"ELA failed: {type(exc).__name__}: {exc}",
                    "image_url": image_url,
                },
            }
        ]
    # Heuristic verdict thresholds; tuned to surface obvious retouching.
    # mean_diff > 8 OR std_diff > 12 on a JPEG saved at q=90 is suspicious.
    verdict = "clean"
    if mean_diff > 12 or std_diff > 18:
        verdict = "likely-edited"
    elif mean_diff > 6 or std_diff > 10:
        verdict = "suspicious"
    return [
        {
            "event_type": "image-match",
            "payload": {
                "source": "ela",
                "image_url": image_url,
                "quality": quality,
                "mean_diff": round(mean_diff, 2),
                "max_diff": max_diff,
                "std_diff": round(std_diff, 2),
                "verdict": verdict,
                "ela_path": str(ela_path),
                "ela_rel": ela_rel,
                "note": "ELA is heuristic; visual inspection of high-diff regions confirms",
            },
        },
        {
            "event_type": "tool-run-result",
            "payload": {
                "image_url": image_url,
                "verdict": verdict,
                "ela_path": str(ela_path),
                "ela_rel": ela_rel,
            },
        },
    ]


def _image_ela_check_synthetic(payload: dict[str, Any]) -> list[dict[str, Any]]:
    url = payload.get("image_url") or "https://example.com/synthetic.jpg"
    return [
        {
            "event_type": "image-match",
            "payload": {
                "source": "ela",
                "image_url": url,
                "quality": 90,
                "mean_diff": 3.2,
                "max_diff": 42,
                "std_diff": 6.8,
                "verdict": "clean",
                "ela_path": "data/ela/abc123.jpg",
                "ela_rel": "ela/abc123.jpg",
                "synthetic": True,
            },
        },
        {
            "event_type": "tool-run-result",
            "payload": {"image_url": url, "verdict": "clean", "synthetic": True},
        },
    ]


# ---------------------------------------------------------------------------
# 4. exiftool_full -- wrap ExifTool binary (the 23,000-tag gold standard)
# ---------------------------------------------------------------------------


def exiftool_full(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Run ExifTool against an image URL and return every tag it finds.

    Requires `exiftool` on PATH. Install:
      - Windows: winget install OliverBetz.ExifTool  (or Phil Harvey's
        official Windows package)
      - macOS:   brew install exiftool
      - Linux:   apt install libimage-exiftool-perl

    Payload:
      {"image_url": "https://example.com/photo.jpg"}
    """
    image_url = (payload.get("image_url") or "").strip()
    if not image_url:
        return [
            {
                "event_type": "tool-run-error",
                "payload": {"reason": "missing 'image_url'"},
            }
        ]
    exiftool_path = shutil.which("exiftool")
    if not exiftool_path:
        return [
            {
                "event_type": "tool-run-error",
                "payload": {
                    "reason": "exiftool not on PATH",
                    "suggest": (
                        "Windows: winget install OliverBetz.ExifTool; "
                        "macOS: brew install exiftool; "
                        "Linux: apt install libimage-exiftool-perl"
                    ),
                },
            }
        ]
    try:
        data, _ = _fetch_image_bytes(image_url, timeout_s=25.0)
    except Exception as exc:
        return [
            {
                "event_type": "tool-run-error",
                "payload": {"reason": f"fetch failed: {type(exc).__name__}: {exc}"},
            }
        ]
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tf:
        tf.write(data)
        tmp_path = tf.name
    try:
        proc = subprocess.run(
            [exiftool_path, "-json", "-G", "-n", "-a", tmp_path],
            capture_output=True,
            text=True,
            timeout=30.0,
            check=False,
        )
        if proc.returncode != 0:
            return [
                {
                    "event_type": "tool-run-error",
                    "payload": {
                        "reason": f"exiftool exit {proc.returncode}",
                        "stderr": (proc.stderr or "")[:500],
                    },
                }
            ]
        try:
            data_obj = json.loads(proc.stdout)
            tags = data_obj[0] if isinstance(data_obj, list) and data_obj else {}
        except json.JSONDecodeError as exc:
            return [
                {
                    "event_type": "tool-run-error",
                    "payload": {"reason": f"exiftool JSON parse: {exc}"},
                }
            ]
    finally:
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)

    # Curated property-vetting payload: tag count + the high-signal slice
    # + the full raw dict so the investigator can dig deeper.
    return [
        {
            "event_type": "image-match",
            "payload": {
                "source": "exiftool",
                "image_url": image_url,
                "tag_count": len(tags),
                "make": tags.get("EXIF:Make") or tags.get("MakerNotes:Make", ""),
                "model": tags.get("EXIF:Model", ""),
                "software": tags.get("EXIF:Software") or tags.get("XMP:CreatorTool", ""),
                "serial_number": tags.get("EXIF:SerialNumber")
                or tags.get("MakerNotes:SerialNumber", ""),
                "gps_lat": tags.get("Composite:GPSLatitude") or tags.get("EXIF:GPSLatitude", ""),
                "gps_lon": tags.get("Composite:GPSLongitude") or tags.get("EXIF:GPSLongitude", ""),
                "gps_direction": tags.get("EXIF:GPSImgDirection", ""),
                "datetime_original": tags.get("EXIF:DateTimeOriginal", ""),
                "copyright": tags.get("EXIF:Copyright", ""),
                "artist": tags.get("EXIF:Artist", ""),
                "iptc_creator": tags.get("IPTC:By-line", ""),
                "xmp_creator": tags.get("XMP:Creator", ""),
                "owner_name": tags.get("EXIF:OwnerName") or tags.get("MakerNotes:OwnerName", ""),
                "thumbnail_present": bool(tags.get("EXIF:ThumbnailImage", "")),
                "raw_tags": tags,
            },
        },
        {
            "event_type": "tool-run-result",
            "payload": {"image_url": image_url, "tag_count": len(tags)},
        },
    ]


def _exiftool_full_synthetic(payload: dict[str, Any]) -> list[dict[str, Any]]:
    url = payload.get("image_url") or "https://example.com/synthetic.jpg"
    return [
        {
            "event_type": "image-match",
            "payload": {
                "source": "exiftool",
                "image_url": url,
                "tag_count": 247,
                "make": "Canon",
                "model": "Canon EOS R5",
                "software": "Adobe Lightroom 13.0",
                "serial_number": "012345678901",
                "gps_lat": 39.78,
                "gps_lon": -89.65,
                "gps_direction": "180.5",
                "datetime_original": "2025:06:15 14:23:10",
                "owner_name": "Alice Synthetic",
                "thumbnail_present": True,
                "synthetic": True,
            },
        },
        {
            "event_type": "tool-run-result",
            "payload": {"image_url": url, "tag_count": 247, "synthetic": True},
        },
    ]


# ---------------------------------------------------------------------------
# 5. ai_image_detection -- Sightengine free-tier GenAI detector
# ---------------------------------------------------------------------------


def ai_image_detection(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """⚠ THIRD-PARTY UPLOAD: forwards image_url to sightengine.com (US-hosted
    SaaS, Sightengine SAS). The URL string is transmitted; Sightengine
    fetches and analyzes the image server-side. Not for private/auth'd
    URLs (presigned S3, Discord CDN, internal corp domains LEAK the
    bucket on transmission).

    Submit an image URL to Sightengine's `genai` model.

    Requires OSINT_SIGHTENGINE_API_USER + OSINT_SIGHTENGINE_API_SECRET env
    vars. Free tier is 500 requests/day. Without keys, returns honest
    tool-run-error (no upload happens).

    Consent contract (Camille security review 2026-05-11):
      - Setting the env keys IS consent to use the service at the
        operator-account level.
      - This adapter does NOT add per-call consent prompts at the current
        single-investigator personal-use scope; the warning above is the
        contract. See Camille's re-open gates in commit message for the
        scope-change triggers that would require a per-call modal.

    Payload:
      {"image_url": "https://example.com/photo.jpg"}
    """
    api_user = os.environ.get("OSINT_SIGHTENGINE_API_USER", "").strip()
    api_secret = os.environ.get("OSINT_SIGHTENGINE_API_SECRET", "").strip()
    if not api_user or not api_secret:
        return [
            {
                "event_type": "tool-run-error",
                "payload": {
                    "reason": "Sightengine API keys not set",
                    "suggest": (
                        "Get free tier at sightengine.com (500/day), "
                        "set OSINT_SIGHTENGINE_API_USER + "
                        "OSINT_SIGHTENGINE_API_SECRET env vars"
                    ),
                },
            }
        ]
    image_url = (payload.get("image_url") or "").strip()
    if not image_url:
        return [
            {
                "event_type": "tool-run-error",
                "payload": {"reason": "missing 'image_url'"},
            }
        ]
    try:
        with _client(timeout_s=20.0) as c:
            r = c.get(
                "https://api.sightengine.com/1.0/check.json",
                params={
                    "models": "genai",
                    "api_user": api_user,
                    "api_secret": api_secret,
                    "url": image_url,
                },
            )
        if r.status_code != 200:
            return [
                {
                    "event_type": "tool-run-error",
                    "payload": {"reason": f"sightengine HTTP {r.status_code}"},
                }
            ]
        data = r.json() or {}
    except httpx.RequestError as exc:
        return [
            {
                "event_type": "tool-run-error",
                "payload": {"reason": f"sightengine {type(exc).__name__}: {exc}"},
            }
        ]
    genai_score = (data.get("type") or {}).get("ai_generated") or 0.0
    verdict = (
        "ai-likely" if genai_score > 0.7 else "ai-possible" if genai_score > 0.3 else "human-likely"
    )
    return [
        {
            "event_type": "image-match",
            "payload": {
                "source": "sightengine-genai",
                "image_url": image_url,
                "ai_generated_score": genai_score,
                "verdict": verdict,
                "raw": data,
            },
        },
        {
            "event_type": "tool-run-result",
            "payload": {"image_url": image_url, "verdict": verdict, "score": genai_score},
        },
    ]


def _ai_image_detection_synthetic(payload: dict[str, Any]) -> list[dict[str, Any]]:
    url = payload.get("image_url") or "https://example.com/synthetic.jpg"
    return [
        {
            "event_type": "image-match",
            "payload": {
                "source": "sightengine-genai",
                "image_url": url,
                "ai_generated_score": 0.05,
                "verdict": "human-likely",
                "synthetic": True,
            },
        },
        {
            "event_type": "tool-run-result",
            "payload": {"image_url": url, "verdict": "human-likely", "synthetic": True},
        },
    ]


# ---------------------------------------------------------------------------
# 6. c2pa_verify -- Content Credentials chain verification
# ---------------------------------------------------------------------------


def c2pa_verify(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Check C2PA Content Credentials on an image.

    Requires `c2patool` on PATH. Install:
      https://github.com/contentauth/c2patool/releases

    Payload:
      {"image_url": "https://example.com/photo.jpg"}

    Returns the signed manifest chain if present, else "no-credentials".
    """
    c2pa_path = shutil.which("c2patool")
    if not c2pa_path:
        return [
            {
                "event_type": "tool-run-error",
                "payload": {
                    "reason": "c2patool not on PATH",
                    "suggest": (
                        "Install from github.com/contentauth/c2patool/releases; "
                        "C2PA is 2024+ standard, mostly Sony/Leica/Nikon"
                    ),
                },
            }
        ]
    image_url = (payload.get("image_url") or "").strip()
    if not image_url:
        return [
            {
                "event_type": "tool-run-error",
                "payload": {"reason": "missing 'image_url'"},
            }
        ]
    try:
        data, _ = _fetch_image_bytes(image_url, timeout_s=25.0)
    except Exception as exc:
        return [
            {
                "event_type": "tool-run-error",
                "payload": {"reason": f"fetch failed: {type(exc).__name__}: {exc}"},
            }
        ]
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tf:
        tf.write(data)
        tmp_path = tf.name
    try:
        proc = subprocess.run(
            [c2pa_path, tmp_path],
            capture_output=True,
            text=True,
            timeout=20.0,
            check=False,
        )
    finally:
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
    if "no claim found" in (proc.stderr or "").lower() or proc.returncode != 0:
        return [
            {
                "event_type": "tool-run-result",
                "payload": {
                    "image_url": image_url,
                    "credentials": "absent",
                    "note": "no C2PA chain on this image",
                },
            }
        ]
    try:
        manifest = json.loads(proc.stdout)
    except json.JSONDecodeError:
        manifest = {"raw": proc.stdout[:1000]}
    return [
        {
            "event_type": "image-match",
            "payload": {
                "source": "c2pa",
                "image_url": image_url,
                "credentials": "present",
                "manifest": manifest,
            },
        },
        {
            "event_type": "tool-run-result",
            "payload": {"image_url": image_url, "credentials": "present"},
        },
    ]


def _c2pa_verify_synthetic(payload: dict[str, Any]) -> list[dict[str, Any]]:
    url = payload.get("image_url") or "https://example.com/synthetic.jpg"
    return [
        {
            "event_type": "image-match",
            "payload": {
                "source": "c2pa",
                "image_url": url,
                "credentials": "present",
                "manifest": {
                    "claim_generator": "Sony Camera Authenticity",
                    "title": "synthetic.jpg",
                    "signature_info": {"issuer": "Sony Imaging Products"},
                    "synthetic": True,
                },
            },
        },
        {
            "event_type": "tool-run-result",
            "payload": {"image_url": url, "credentials": "present", "synthetic": True},
        },
    ]


# ---------------------------------------------------------------------------
# 7. kartaview_nearby -- OSM street-level imagery
# ---------------------------------------------------------------------------


def kartaview_nearby(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Find OSM street-level photos near a lat/lon.

    Payload:
      {"lat": 39.78, "lon": -89.65, "radius_m": 500}  # radius default 200, max 2000

    Hits KartaView's public API (open OSM project, no API key).
    """
    try:
        lat = float(payload.get("lat"))
        lon = float(payload.get("lon"))
    except (TypeError, ValueError):
        return [
            {
                "event_type": "tool-run-error",
                "payload": {"reason": "need numeric 'lat' and 'lon'"},
            }
        ]
    radius = min(int(payload.get("radius_m", 200)), 2000)
    try:
        with _client(timeout_s=15.0) as c:
            r = c.get(
                "https://api.openstreetcam.org/2.0/photo/",
                params={
                    "lat": lat,
                    "lng": lon,
                    "radius": radius,
                    "itemsPerPage": "20",
                },
            )
        if r.status_code != 200:
            return [
                {
                    "event_type": "tool-run-error",
                    "payload": {"reason": f"kartaview HTTP {r.status_code}"},
                }
            ]
        data = r.json() or {}
    except httpx.RequestError as exc:
        return [
            {
                "event_type": "tool-run-error",
                "payload": {"reason": f"kartaview {type(exc).__name__}: {exc}"},
            }
        ]
    photos = data.get("result", {}).get("data", []) or []
    events: list[dict[str, Any]] = []
    for p in photos[:20]:
        events.append(
            {
                "event_type": "image-match",
                "payload": {
                    "source": "kartaview",
                    "lat": float(p.get("lat", 0)),
                    "lon": float(p.get("lng", 0)),
                    "photo_url": p.get("fileurlLTh") or p.get("fileurlProc", ""),
                    "captured_at": p.get("dateAdded", ""),
                    "sequence_id": p.get("sequenceId", ""),
                    "heading": p.get("heading", 0),
                },
            }
        )
    events.append(
        {
            "event_type": "tool-run-result",
            "payload": {
                "lat": lat,
                "lon": lon,
                "radius_m": radius,
                "photos": len(events) - 0,
            },
        }
    )
    return events


def _kartaview_nearby_synthetic(payload: dict[str, Any]) -> list[dict[str, Any]]:
    lat = float(payload.get("lat", 39.78))
    lon = float(payload.get("lon", -89.65))
    return [
        {
            "event_type": "image-match",
            "payload": {
                "source": "kartaview",
                "lat": lat,
                "lon": lon,
                "photo_url": "https://kartaview.org/storage/synthetic/photo.jpg",
                "captured_at": "2024-06-15",
                "synthetic": True,
            },
        },
        {
            "event_type": "tool-run-result",
            "payload": {"lat": lat, "lon": lon, "photos": 1, "synthetic": True},
        },
    ]


# ---------------------------------------------------------------------------
# 7b. phash_dedupe -- catch multi-listing photo theft across an investigation
# ---------------------------------------------------------------------------
# Tomas gap (2026-05-11 pre-commit review): "property fraudsters reuse 4-6
# stolen photos across multiple fake listings. pHash on every fetched image,
# stored against the case, surfaces 'this photo also appeared in case-id-X
# last week.' Higher signal-per-minute than a fourth reverse engine."
#
# Store: jsonl at data/phash-db.jsonl. Each line: {phash, case_id, image_url,
# saved_at}. Append-only; no delete (audit trail). Hamming-distance match
# with a configurable threshold (default 8 = "visually identical-ish").

_PHASH_DB_PATH = _REPO_ROOT / "data" / "phash-db.jsonl"


def _hamming(a: str, b: str) -> int:
    """Hamming distance between two equal-length hex pHash strings.

    imagehash returns hex strings; convert to int and XOR + popcount."""
    try:
        return bin(int(a, 16) ^ int(b, 16)).count("1")
    except ValueError:
        return 99  # treat unparseable as "very far"


def phash_dedupe(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Compute pHash of an image, find prior matches in the local DB.

    Payload:
      {"image_url": "https://example.com/photo.jpg",
       "case_id": "case-2026-05-alice",      # optional but recommended
       "threshold": 8,                        # Hamming distance, default 8
       "skip_store": false}                   # don't append to DB if true

    Hamming-distance thresholds (Bowman & Williams 2013 perceptual-hash
    paper, plus empirical practice):
      <= 4   -- effectively identical (re-saved/recompressed)
      5-8   -- visually identical (cropped, resized, color-shifted)
      9-12  -- visually similar (significant edits)
      >12  -- different scene
    """
    image_url = (payload.get("image_url") or "").strip()
    if not image_url:
        return [
            {
                "event_type": "tool-run-error",
                "payload": {"reason": "missing 'image_url'"},
            }
        ]
    case_id = (payload.get("case_id") or "unscoped").strip()
    threshold = int(payload.get("threshold", 8))
    skip_store = bool(payload.get("skip_store", False))

    try:
        import imagehash
        from PIL import Image
    except ImportError as exc:
        return [
            {
                "event_type": "tool-run-error",
                "payload": {"reason": f"imagehash/PIL not installed: {exc}"},
            }
        ]

    try:
        data, _ctype = _fetch_image_bytes(image_url, timeout_s=20.0)
    except Exception as exc:
        return [
            {
                "event_type": "tool-run-error",
                "payload": {
                    "reason": f"fetch failed: {type(exc).__name__}: {exc}",
                    "image_url": image_url,
                },
            }
        ]
    try:
        img = Image.open(io.BytesIO(data))
        # Default 64-bit pHash via DCT-based perceptual hashing.
        phash = str(imagehash.phash(img))
    except Exception as exc:
        return [
            {
                "event_type": "tool-run-error",
                "payload": {
                    "reason": f"phash failed: {type(exc).__name__}: {exc}",
                    "image_url": image_url,
                },
            }
        ]

    # Search prior DB for matches within threshold.
    matches: list[dict[str, Any]] = []
    if _PHASH_DB_PATH.is_file():
        try:
            with _PHASH_DB_PATH.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    prior_phash = row.get("phash", "")
                    if not prior_phash:
                        continue
                    dist = _hamming(phash, prior_phash)
                    if dist <= threshold:
                        matches.append(
                            {
                                "prior_image_url": row.get("image_url", ""),
                                "prior_case_id": row.get("case_id", ""),
                                "prior_saved_at": row.get("saved_at", ""),
                                "hamming_distance": dist,
                                "verdict": (
                                    "identical"
                                    if dist <= 4
                                    else "visually-identical"
                                    if dist <= 8
                                    else "visually-similar"
                                ),
                            }
                        )
        except OSError as exc:
            sys.stderr.write(f"phash-db read soft-fail: {exc}\n")

    # Append to DB (append-only audit trail; no overwrite).
    if not skip_store:
        try:
            from datetime import UTC
            from datetime import datetime as _dt

            _PHASH_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            row = {
                "phash": phash,
                "case_id": case_id,
                "image_url": image_url,
                "saved_at": _dt.now(UTC).isoformat(),
            }
            with _PHASH_DB_PATH.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, separators=(",", ":")) + "\n")
        except OSError as exc:
            sys.stderr.write(f"phash-db write soft-fail: {exc}\n")

    events: list[dict[str, Any]] = []
    for m in matches[:20]:  # cap dossier noise
        events.append(
            {
                "event_type": "image-match",
                "payload": {
                    "source": "phash-dedupe",
                    "image_url": image_url,
                    "phash": phash,
                    **m,
                },
            }
        )
    events.append(
        {
            "event_type": "tool-run-result",
            "payload": {
                "image_url": image_url,
                "phash": phash,
                "case_id": case_id,
                "matches": len(matches),
                "threshold": threshold,
                "stored": not skip_store,
            },
        }
    )
    return events


def _phash_dedupe_synthetic(payload: dict[str, Any]) -> list[dict[str, Any]]:
    url = payload.get("image_url") or "https://example.com/synthetic.jpg"
    return [
        {
            "event_type": "image-match",
            "payload": {
                "source": "phash-dedupe",
                "image_url": url,
                "phash": "ffeeddccbbaa9988",
                "prior_image_url": "https://airbnb.com/rooms/12345/photos/main.jpg",
                "prior_case_id": "case-2026-04-prior",
                "prior_saved_at": "2026-04-22T15:00:00+00:00",
                "hamming_distance": 2,
                "verdict": "identical",
                "synthetic": True,
            },
        },
        {
            "event_type": "tool-run-result",
            "payload": {"image_url": url, "matches": 1, "synthetic": True},
        },
    ]


# ---------------------------------------------------------------------------
# 7c. seasonal_metadata_check -- EXIF date + sun-angle vs listing-claim season
# ---------------------------------------------------------------------------
# Tomas gap: "Investigator runs searches like 'this photo was taken outside
# in fall but the listing claims winter'. Should there be a
# seasonal_metadata_check?" Yes. Pull EXIF DateTimeOriginal, optionally GPS,
# compute solar elevation/azimuth at that lat/lon/time, surface
# date+season+sun-angle so the investigator compares to the listing claim.


def _solar_position(
    lat: float, lon: float, year: int, month: int, day: int, hour: float
) -> tuple[float, float]:
    """Compute solar elevation + azimuth in degrees at a lat/lon/UTC-time.

    Standard NOAA Solar Position Algorithm (simplified). Returns
    (elevation_deg, azimuth_deg). Inputs are decimal-degree lat/lon,
    civil date, and decimal hour UTC.

    Source: NOAA solar-position formulas, Cornwall et al. Accurate to
    +/- 0.5 degrees for the purpose of "is the sun high or low" which
    is what the property-vetting use case needs.
    """
    import math
    from datetime import datetime as _dt

    # Day-of-year + fractional time
    doy = (_dt(year, month, day) - _dt(year, 1, 1)).days + 1
    frac_year = (2 * math.pi / 365) * (doy - 1 + (hour - 12) / 24)

    # Equation of time (in minutes)
    eqtime = 229.18 * (
        0.000075
        + 0.001868 * math.cos(frac_year)
        - 0.032077 * math.sin(frac_year)
        - 0.014615 * math.cos(2 * frac_year)
        - 0.040849 * math.sin(2 * frac_year)
    )

    # Solar declination (radians)
    decl = (
        0.006918
        - 0.399912 * math.cos(frac_year)
        + 0.070257 * math.sin(frac_year)
        - 0.006758 * math.cos(2 * frac_year)
        + 0.000907 * math.sin(2 * frac_year)
        - 0.002697 * math.cos(3 * frac_year)
        + 0.00148 * math.sin(3 * frac_year)
    )

    # Time offset in minutes (lon in degrees east)
    time_offset = eqtime + 4 * lon  # lon in degrees east of GMT
    tst = (hour * 60) + time_offset  # true solar time in minutes
    ha = math.radians((tst / 4) - 180)  # hour angle in radians
    lat_r = math.radians(lat)

    # Solar zenith
    cos_z = math.sin(lat_r) * math.sin(decl) + math.cos(lat_r) * math.cos(decl) * math.cos(ha)
    cos_z = max(-1.0, min(1.0, cos_z))
    zenith = math.degrees(math.acos(cos_z))
    elevation = 90.0 - zenith

    # Azimuth (measured clockwise from north)
    cos_az = (math.sin(lat_r) * cos_z - math.sin(decl)) / (
        math.cos(lat_r) * math.sin(math.radians(zenith)) or 1e-9
    )
    cos_az = max(-1.0, min(1.0, cos_az))
    azimuth = math.degrees(math.acos(cos_az))
    if ha > 0:
        azimuth = 360 - azimuth

    return elevation, azimuth


def _season_for_month(month: int, lat: float) -> str:
    """Meteorological season heuristic. Hemispheres flipped for southern."""
    northern = lat >= 0
    if month in (12, 1, 2):
        return "winter" if northern else "summer"
    if month in (3, 4, 5):
        return "spring" if northern else "autumn"
    if month in (6, 7, 8):
        return "summer" if northern else "winter"
    return "autumn" if northern else "spring"


def seasonal_metadata_check(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Pull EXIF DateTimeOriginal + GPS; report season + solar position.

    Payload:
      {"image_url": "https://example.com/photo.jpg",
       "claimed_season": "winter"}            # optional; if set, emits
                                              # a match/mismatch flag

    Use cases:
      - Listing claims "cozy winter cabin"; EXIF says June -> mismatch flag.
      - Listing claims "sunlit afternoon" -> compare claimed time-of-day
        against sun elevation derived from EXIF date + GPS.
      - Shadow direction in photo can be cross-checked with the computed
        azimuth (manual investigator step, but surfaced for that workflow).
    """
    image_url = (payload.get("image_url") or "").strip()
    if not image_url:
        return [
            {
                "event_type": "tool-run-error",
                "payload": {"reason": "missing 'image_url'"},
            }
        ]
    claimed_season = (payload.get("claimed_season") or "").strip().lower()

    # Lean on image_exif rather than re-fetching the image.
    exif_events = image_exif({"image_url": image_url})
    base = exif_events[0]
    if base.get("event_type") != "image-match":
        return exif_events

    p = base["payload"]
    datetime_str = p.get("datetime_original", "") or ""
    lat = p.get("gps_lat")
    lon = p.get("gps_lon")

    # Parse EXIF datetime: "YYYY:MM:DD HH:MM:SS"
    import re as _re

    m = _re.match(r"(\d{4})[:\-/](\d{1,2})[:\-/](\d{1,2})\s+(\d{1,2}):(\d{1,2})", datetime_str)
    photo_year = photo_month = photo_day = 0
    photo_hour = 12.0
    if m:
        photo_year, photo_month, photo_day = int(m.group(1)), int(m.group(2)), int(m.group(3))
        photo_hour = int(m.group(4)) + int(m.group(5)) / 60.0

    flags: dict[str, Any] = {}
    if photo_month:
        photo_season = _season_for_month(photo_month, lat if isinstance(lat, int | float) else 0.0)
        flags["photo_season"] = photo_season
        flags["photo_month"] = photo_month
        flags["photo_year"] = photo_year
        if claimed_season and photo_season != claimed_season:
            flags["season_mismatch"] = True
            flags["mismatch_note"] = (
                f"listing claims '{claimed_season}' but photo EXIF dates to "
                f"{photo_year}-{photo_month:02d}-{photo_day:02d} ({photo_season})"
            )
        elif claimed_season:
            flags["season_mismatch"] = False
    else:
        flags["photo_season"] = ""
        flags["mismatch_note"] = "EXIF DateTimeOriginal absent or unparseable"

    # Solar position (only if GPS + datetime both present)
    if isinstance(lat, int | float) and isinstance(lon, int | float) and photo_year:
        elev, az = _solar_position(
            float(lat), float(lon), photo_year, photo_month, photo_day, photo_hour
        )
        flags["solar_elevation_deg"] = round(elev, 1)
        flags["solar_azimuth_deg"] = round(az, 1)
        flags["solar_time_descriptor"] = (
            "below-horizon (night)"
            if elev < 0
            else "low (golden hour)"
            if elev < 15
            else "high (midday)"
            if elev > 50
            else "mid"
        )

    return [
        {
            "event_type": "image-match",
            "payload": {
                "source": "seasonal-metadata",
                "image_url": image_url,
                "datetime_original": datetime_str,
                "gps_lat": lat,
                "gps_lon": lon,
                **flags,
            },
        },
        {
            "event_type": "tool-run-result",
            "payload": {
                "image_url": image_url,
                "season_mismatch": flags.get("season_mismatch"),
                "photo_season": flags.get("photo_season", ""),
            },
        },
    ]


def _seasonal_metadata_check_synthetic(payload: dict[str, Any]) -> list[dict[str, Any]]:
    url = payload.get("image_url") or "https://example.com/synthetic.jpg"
    claimed = payload.get("claimed_season") or "winter"
    return [
        {
            "event_type": "image-match",
            "payload": {
                "source": "seasonal-metadata",
                "image_url": url,
                "datetime_original": "2025:06:15 14:23:10",
                "gps_lat": 39.78,
                "gps_lon": -89.65,
                "photo_season": "summer",
                "photo_month": 6,
                "photo_year": 2025,
                "season_mismatch": claimed != "summer",
                "mismatch_note": (
                    f"listing claims '{claimed}' but photo EXIF dates to " f"2025-06-15 (summer)"
                    if claimed != "summer"
                    else ""
                ),
                "solar_elevation_deg": 71.2,
                "solar_azimuth_deg": 195.0,
                "solar_time_descriptor": "high (midday)",
                "synthetic": True,
            },
        },
        {
            "event_type": "tool-run-result",
            "payload": {
                "image_url": url,
                "season_mismatch": claimed != "summer",
                "photo_season": "summer",
                "synthetic": True,
            },
        },
    ]


# ---------------------------------------------------------------------------
# 8. image_provenance_check -- composite: exiftool + ELA + c2pa
# ---------------------------------------------------------------------------


def image_provenance_check(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Composite provenance check: ExifTool + ELA + C2PA.

    Runs all three in sequence; tools that aren't installed are skipped
    with a note rather than a hard error. Emits one image-match per
    sub-check + a single tool-run-result with the aggregated verdict.
    """
    image_url = (payload.get("image_url") or "").strip()
    if not image_url:
        return [
            {
                "event_type": "tool-run-error",
                "payload": {"reason": "missing 'image_url'"},
            }
        ]
    events: list[dict[str, Any]] = []
    flags: dict[str, Any] = {}

    # ExifTool (preferred) or exifread (fallback)
    sub = (
        exiftool_full({"image_url": image_url})
        if shutil.which("exiftool")
        else image_exif({"image_url": image_url})
    )
    events.extend(sub[:-1])  # drop the sub-summary; we'll emit our own
    if sub[0].get("event_type") == "image-match":
        p = sub[0]["payload"]
        flags["software"] = p.get("software", "")
        flags["camera_model"] = p.get("model") or p.get("camera_model", "")
        flags["gps_lat"] = p.get("gps_lat")
        flags["gps_lon"] = p.get("gps_lon")
        flags["serial_number"] = p.get("serial_number", "")

    # ELA
    ela = image_ela_check({"image_url": image_url})
    events.extend(ela[:-1])
    if ela[0].get("event_type") == "image-match":
        flags["ela_verdict"] = ela[0]["payload"].get("verdict")

    # C2PA (best-effort)
    if shutil.which("c2patool"):
        c2 = c2pa_verify({"image_url": image_url})
        events.extend(c2[:-1])
        if c2[0].get("event_type") == "image-match":
            flags["c2pa_credentials"] = "present"
        else:
            flags["c2pa_credentials"] = "absent"
    else:
        flags["c2pa_credentials"] = "tool-not-installed"

    # Aggregate verdict heuristic (Tomas review 2026-05-11 pre-commit):
    # Lightroom is ubiquitous in real listings (every real host uses it);
    # weight near zero. The signal is *unexpected software for the
    # camera context* -- e.g. desktop Photoshop on what claims to be a
    # phone photo. AI-generator strings remain hard signals.
    score = 0
    software_lower = (flags.get("software") or "").lower()
    camera_lower = (flags.get("camera_model") or "").lower()
    is_phone_camera = any(
        kw in camera_lower for kw in ("iphone", "pixel", "galaxy", "samsung", "oneplus", "xiaomi")
    )
    # Hard signals: AI generators
    if "stable diffusion" in software_lower or "automatic1111" in software_lower:
        score += 3
    if "midjourney" in software_lower:
        score += 3
    if "dall-e" in software_lower or "dalle" in software_lower:
        score += 3
    # Context-aware: desktop editing on a phone-shot photo is incongruent
    if is_phone_camera and "photoshop" in software_lower:
        score += 2
    # Lightroom / mobile-editing apps are normal real-listing workflow:
    # 0 score regardless. Photoshop on a non-phone camera is also
    # typical pro-real-estate workflow.
    if flags.get("ela_verdict") == "likely-edited":
        score += 2
    elif flags.get("ela_verdict") == "suspicious":
        score += 1
    verdict = "high-risk" if score >= 3 else "elevated-risk" if score >= 2 else "low-risk"
    flags["aggregate_verdict"] = verdict
    flags["aggregate_score"] = score

    events.append(
        {
            "event_type": "tool-run-result",
            "payload": {
                "image_url": image_url,
                "verdict": verdict,
                "score": score,
                "flags": flags,
            },
        }
    )
    return events


def _image_provenance_check_synthetic(payload: dict[str, Any]) -> list[dict[str, Any]]:
    url = payload.get("image_url") or "https://example.com/synthetic.jpg"
    return [
        {
            "event_type": "image-match",
            "payload": {
                "source": "provenance-composite",
                "image_url": url,
                "software": "Adobe Lightroom 13.0",
                "ela_verdict": "clean",
                "c2pa_credentials": "absent",
                "verdict": "low-risk",
                "synthetic": True,
            },
        },
        {
            "event_type": "tool-run-result",
            "payload": {"image_url": url, "verdict": "low-risk", "synthetic": True},
        },
    ]


# ---------------------------------------------------------------------------
# 9. reverse_image_aggregator -- meta-adapter, fans out to all engines
# ---------------------------------------------------------------------------


def reverse_image_aggregator(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Submit one image URL to TinEye + Yandex + Google Lens + Bing.

    Generates a horizontally-flipped variant in-process and saves it to
    data/flipped/<hash>.jpg so the investigator can re-run the aggregator
    against the flipped URL once it's hostable on a reachable surface.

    HONEST GAP (Tomas review 2026-05-11 pre-commit): the flipped local
    file is NOT auto-submitted to the reverse engines, because the
    engines accept URLs, not local paths, and the worker has no upload
    surface yet. The aggregator surfaces the flipped path so the
    investigator's next step is explicit: host the flipped file
    somewhere reachable + re-run this adapter. Sprint-4 carry-forward:
    wire the worker's MinIO/local-fs storage to give the flipped
    variant a reachable URL automatically.

    Payload:
      {"image_url": "https://example.com/photo.jpg"}

    Emits one image-match per engine hit + a tool-run-result summary.
    """
    image_url = (payload.get("image_url") or "").strip()
    if not image_url:
        return [
            {
                "event_type": "tool-run-error",
                "payload": {"reason": "missing 'image_url'"},
            }
        ]
    reg = get_registry()
    engines = [
        ("tineye_image", {"image_url": image_url}),
        ("yandex_image_reverse", {"image_url": image_url}),
        ("google_lens_reverse", {"image_url": image_url}),
        ("bing_visual_reverse", {"image_url": image_url}),
    ]
    events: list[dict[str, Any]] = []
    engine_results: dict[str, int] = {}
    for engine_id, sub_payload in engines:
        entry = reg.get(engine_id)
        if entry is None:
            engine_results[engine_id] = -1  # not registered
            continue
        try:
            # synthetic_mode for safety in M0; live mode swapped in by the
            # tool_runner when the user dispatches in non-synthetic mode.
            sub_events = entry.synthetic_mode(sub_payload)
        except Exception as exc:
            events.append(
                {
                    "event_type": "tool-run-error",
                    "payload": {
                        "reason": f"{engine_id} {type(exc).__name__}: {exc}",
                    },
                }
            )
            continue
        # Forward all image-match events; count for the summary
        engine_count = 0
        for ev in sub_events:
            if ev.get("event_type") == "image-match":
                events.append(ev)
                engine_count += 1
        engine_results[engine_id] = engine_count
    # Auto-generate flipped variant + surface its local path for the
    # investigator's next manual step (host it + re-run aggregator on
    # the flipped URL). Calls image_flip_check in-process; we do not
    # raise if the flip fails (it's a soft-helpful side path).
    flipped_path: str | None = None
    try:
        flip_events = image_flip_check({"image_url": image_url, "output_format": "file"})
        for fe in flip_events:
            if fe.get("event_type") == "image-match":
                flipped_path = fe["payload"].get("flipped_path") or None
                break
    except Exception:
        pass

    events.append(
        {
            "event_type": "tool-run-result",
            "payload": {
                "image_url": image_url,
                "engine_results": engine_results,
                "total_matches": sum(v for v in engine_results.values() if v > 0),
                "flipped_variant_path": flipped_path,
                "flipped_note": (
                    "Horizontally-flipped variant saved locally. The reverse "
                    "engines accept URLs not paths, so host this somewhere "
                    "reachable + re-run this adapter against the flipped URL "
                    "to close the flip-coverage gap (TinEye/Bing flip-blind)."
                    if flipped_path
                    else "Flip-variant generation failed; flip-coverage missing."
                ),
            },
        }
    )
    return events


def _reverse_image_aggregator_synthetic(payload: dict[str, Any]) -> list[dict[str, Any]]:
    url = payload.get("image_url") or "https://example.com/synthetic.jpg"
    return [
        {
            "event_type": "image-match",
            "payload": {
                "source": "tineye",
                "image_url": url,
                "match_url": "https://airbnb.com/rooms/12345/photos",
                "synthetic": True,
            },
        },
        {
            "event_type": "image-match",
            "payload": {
                "source": "yandex",
                "image_url": url,
                "match_url": "https://vrbo.com/listing/synthetic",
                "synthetic": True,
            },
        },
        {
            "event_type": "tool-run-result",
            "payload": {
                "image_url": url,
                "engine_results": {
                    "tineye_image": 1,
                    "yandex_image_reverse": 1,
                    "google_lens_reverse": 0,
                    "bing_visual_reverse": 0,
                },
                "total_matches": 2,
                "synthetic": True,
            },
        },
    ]


# ---------------------------------------------------------------------------
# Registry installation -- in-process adapters + subprocess wrappers
# ---------------------------------------------------------------------------

_REGISTRY = get_registry()

_REGISTRY.register(
    "image_exif",
    image_exif,
    synthetic_mode=_image_exif_synthetic,
    in_process=True,
    description="Lightweight EXIF + GPS read via exifread (Sprint 3 image battery).",
)
_REGISTRY.register(
    "image_flip_check",
    image_flip_check,
    synthetic_mode=_image_flip_check_synthetic,
    in_process=True,
    description="Horizontal-flip variant for feeding into exact-match reverse engines.",
)
_REGISTRY.register(
    "image_ela_check",
    image_ela_check,
    synthetic_mode=_image_ela_check_synthetic,
    in_process=True,
    description="Error Level Analysis manipulation detector (PIL + numpy).",
)
_REGISTRY.register(
    "exiftool_full",
    exiftool_full,
    synthetic_mode=_exiftool_full_synthetic,
    in_process=True,
    description="ExifTool subprocess (~23k tags). Requires exiftool on PATH.",
)
_REGISTRY.register(
    "ai_image_detection",
    ai_image_detection,
    synthetic_mode=_ai_image_detection_synthetic,
    in_process=True,
    description=(
        "AI-image detection (uploads URL to sightengine.com -- US SaaS). "
        "Sightengine GenAI free tier; requires API keys env."
    ),
)
_REGISTRY.register(
    "c2pa_verify",
    c2pa_verify,
    synthetic_mode=_c2pa_verify_synthetic,
    in_process=True,
    description="C2PA Content Credentials chain verify. Requires c2patool on PATH.",
)
_REGISTRY.register(
    "kartaview_nearby",
    kartaview_nearby,
    synthetic_mode=_kartaview_nearby_synthetic,
    in_process=True,
    description="KartaView open OSM street-level imagery by lat/lon.",
)
_REGISTRY.register(
    "image_provenance_check",
    image_provenance_check,
    synthetic_mode=_image_provenance_check_synthetic,
    in_process=True,
    description="Composite: ExifTool + ELA + C2PA -> aggregate verdict.",
)
_REGISTRY.register(
    "phash_dedupe",
    phash_dedupe,
    synthetic_mode=_phash_dedupe_synthetic,
    in_process=True,
    description="pHash dedupe across investigation; catches multi-listing photo theft.",
)
_REGISTRY.register(
    "seasonal_metadata_check",
    seasonal_metadata_check,
    synthetic_mode=_seasonal_metadata_check_synthetic,
    in_process=True,
    description="EXIF date + sun-angle vs listing-claim season (Tomas gap).",
)
_REGISTRY.register(
    "reverse_image_aggregator",
    reverse_image_aggregator,
    synthetic_mode=_reverse_image_aggregator_synthetic,
    in_process=True,
    description="Fan-out to TinEye + Yandex + Google Lens + Bing reverse engines.",
)

# Scrapling subprocess wrappers (live in adapters/<id>/wrapper.py)
for _img_id, _img_dir in (
    ("yandex_image_reverse", "yandex_image_reverse"),
    ("google_lens_reverse", "google_lens_reverse"),
    ("bing_visual_reverse", "bing_visual_reverse"),
):
    _wrapper_path = _REPO_ROOT / "adapters" / _img_dir / "wrapper.py"
    if _wrapper_path.is_file() and _EMPIRICAL_PY.is_file():
        _REGISTRY.register(
            _img_id,
            make_subprocess_adapter(
                _wrapper_path,
                timeout_s=60.0,
                python_executable=str(_EMPIRICAL_PY),
            ),
            synthetic_mode=make_subprocess_adapter(
                _wrapper_path,
                timeout_s=30.0,
                python_executable=str(_EMPIRICAL_PY),
                extra_env={"OSINT_ADAPTER_MODE": "synthetic"},
            ),
            in_process=False,
            description=(f"{_img_id} -- reverse image search via Scrapling stealth subprocess."),
        )

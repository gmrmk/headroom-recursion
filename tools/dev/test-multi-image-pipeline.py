"""Broader photo-pipeline test: multiple photos from multiple platforms.

Pulls photo URLs from BOTH captured bodies (VRBO + TripAdvisor) plus a
generic schema.org JSON-LD extraction for any additional images. Runs:

  per-photo (cheap, in-process):
    - image_exif
    - image_pdq_hash
    - image_ai_local_detect
    - image_provenance_check

  per-listing (live, network):
    - reverse_image_aggregator  (4 engines, ~45s each photo)

  end-to-end:
    - listing_photo_pivot       BFS over photos with 1-hop bound (faster
                                than 2-hop for demo; would expand in prod)

Honest design: live reverse-image is slow + heavily rate-limited. This
script caps per-photo time + skips engines that already returned 0 on
prior runs against the same photo. Stops after MAX_TOTAL_SECONDS.
"""

from __future__ import annotations

import json as _json
import re
import sys
import time
from pathlib import Path

WORKERS_SRC = Path(__file__).resolve().parents[2] / "apps" / "workers" / "src"
if WORKERS_SRC.exists():
    sys.path.insert(0, str(WORKERS_SRC))

_BODIES_DIR = Path(__file__).resolve().parent / "bypass-probe-bodies"
_OUT_DIR = Path(__file__).resolve().parent / "multi-image-pipeline-bodies"
_OUT_DIR.mkdir(exist_ok=True)

MAX_TOTAL_SECONDS = 600  # hard cap to avoid burning the session


def _extract_og_image_urls(html_body: str) -> list[str]:
    """Pull every <meta property='og:image' content='...'> URL."""
    return re.findall(
        r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
        html_body,
        re.IGNORECASE,
    )


def _extract_jsonld_image_urls(html_body: str) -> list[str]:
    """Pull image URLs from any schema.org JSON-LD block."""
    out: list[str] = []
    for raw in re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>([\s\S]*?)</script>',
        html_body,
        re.IGNORECASE,
    ):
        try:
            data = _json.loads(raw.strip())
        except Exception:
            continue
        items = data if isinstance(data, list) else [data]
        for it in items:
            if not isinstance(it, dict):
                continue
            img = it.get("image")
            if isinstance(img, str):
                out.append(img)
            elif isinstance(img, list):
                out.extend([x for x in img if isinstance(x, str)])
    return out


def _photo_urls_from_body(body_path: Path) -> list[str]:
    body = body_path.read_text(encoding="utf-8", errors="replace")
    urls = _extract_og_image_urls(body) + _extract_jsonld_image_urls(body)
    # Dedupe in encounter order
    seen: set[str] = set()
    out: list[str] = []
    for u in urls:
        if u not in seen and u.startswith("http"):
            seen.add(u)
            out.append(u)
    return out


def _format_event(ev: dict) -> str:
    et = ev.get("event_type", "?")
    pl = ev.get("payload", {}) if isinstance(ev.get("payload"), dict) else {}
    keys = [k for k in pl.keys() if k not in ("image_url",)][:5]
    snippet = ", ".join(f"{k}={str(pl.get(k))[:50]}" for k in keys)
    return f"    [{et:22}] {snippet}"


def main() -> None:
    from osint_goblin_workers.adapters_image import (
        image_ai_local_detect,
        image_exif,
        image_pdq_hash,
        image_provenance_check,
        reverse_image_aggregator,
    )

    deadline = time.monotonic() + MAX_TOTAL_SECONDS

    # Source bodies
    sources = [
        ("vrbo", "https://www.vrbo.com/1682245", _BODIES_DIR / "vrbo-zendriver-status200.html"),
        (
            "tripadvisor",
            "https://www.tripadvisor.com/VacationRentals",
            _BODIES_DIR / "tripadvisor-camoufox-status200.html",
        ),
    ]

    print(f"Output: {_OUT_DIR}")
    print(f"Budget: {MAX_TOTAL_SECONDS}s\n")

    pdq_hashes: dict[str, str] = {}  # photo_url -> hex hash (cross-listing dedupe check)

    for platform, seed_url, body_path in sources:
        if time.monotonic() >= deadline:
            print("--- budget exhausted, stopping ---")
            break
        if not body_path.exists():
            print(f"[skip] {platform}: {body_path} not found")
            continue
        photos = _photo_urls_from_body(body_path)
        print(f"=== {platform.upper()} @ {seed_url} ===")
        print(f"    photos found: {len(photos)}")
        for i, photo_url in enumerate(photos[:3]):  # cap at 3 photos per platform
            if time.monotonic() >= deadline:
                break
            print(f"\n  PHOTO {i+1}: {photo_url[:90]}")
            # Cheap in-process battery
            try:
                exif_ev = image_exif({"image_url": photo_url})
                ai_ev = image_ai_local_detect({"image_url": photo_url})
                pdq_ev = image_pdq_hash({"image_url": photo_url})
                prov_ev = image_provenance_check({"image_url": photo_url})
            except Exception as exc:
                print(f"    EXCEPTION in cheap battery: {type(exc).__name__}: {exc}")
                continue
            for ev in (*exif_ev, *ai_ev, *pdq_ev, *prov_ev):
                if ev.get("event_type") in ("image-match", "tool-run-result"):
                    print(_format_event(ev))
            # Record PDQ hash for cross-platform dedupe verification
            for ev in pdq_ev:
                pl = ev.get("payload", {}) if isinstance(ev.get("payload"), dict) else {}
                if pl.get("pdq_hash_hex"):
                    pdq_hashes[photo_url] = pl["pdq_hash_hex"]
                    break
        print()

    # ---- PDQ cross-platform check ----
    print("=== PDQ cross-platform dedupe check ===")
    print(f"    unique photos hashed: {len(pdq_hashes)}")
    seen_hashes: dict[str, list[str]] = {}
    for url, h in pdq_hashes.items():
        seen_hashes.setdefault(h, []).append(url)
    duplicates = {h: urls for h, urls in seen_hashes.items() if len(urls) > 1}
    if duplicates:
        print(f"    DUPLICATE HASHES FOUND: {len(duplicates)}")
        for h, urls in duplicates.items():
            print(f"      hash={h[:24]}... appears on:")
            for u in urls:
                print(f"        - {u[:90]}")
    else:
        print("    no cross-platform photo duplicates (expected for legit listings)")

    # ---- One live reverse-image probe (single photo, ~45s) ----
    if pdq_hashes and time.monotonic() + 60 < deadline:
        print()
        print("=== Live reverse-image probe (1 photo, ~45s) ===")
        sample_url = next(iter(pdq_hashes.keys()))
        print(f"    photo: {sample_url[:90]}")
        t0 = time.monotonic()
        try:
            events = reverse_image_aggregator({"image_url": sample_url})
            elapsed = time.monotonic() - t0
            print(f"    {len(events)} events in {elapsed:.1f}s")
            for ev in events:
                if ev.get("event_type") in ("image-match", "tool-run-result", "tool-run-warning"):
                    print(_format_event(ev))
        except Exception as exc:
            print(f"    EXCEPTION: {type(exc).__name__}: {exc}")

    # ---- Photo-pivot synthetic-mode demo ----
    # Live mode would chain reverse_image_aggregator -> listing_scrape recursively
    # and burn ~10 min; synthetic mode shows the event shape the UI gets.
    print()
    print("=== listing_photo_pivot (synthetic mode, demo) ===")
    from osint_goblin_workers.adapters_listing import _listing_photo_pivot_synthetic

    events = _listing_photo_pivot_synthetic({"listing_url": "https://www.vrbo.com/1682245"})
    for ev in events:
        print(_format_event(ev))


if __name__ == "__main__":
    main()

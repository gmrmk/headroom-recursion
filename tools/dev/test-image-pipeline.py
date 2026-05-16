"""End-to-end test: VRBO listing -> photo URL -> image-analysis pipeline.

Pulls the photo URL out of a real VRBO listing (using the captured 887KB
body from the zendriver pressure-test), then runs each image adapter in
LIVE mode against it. Demonstrates the photo-fraud PV chain end-to-end:

  Listing extractor                       (adapters_listing.extract_vrbo)
  -> photo_urls[]
     -> image_exif                        EXIF metadata + GPS + timestamps
     -> image_pdq_hash                    perceptual hash for dedupe
     -> image_provenance_check            composite forensic score
     -> reverse_image_aggregator          fan out to Yandex/Lens/TinEye/Bing
     -> ai_image_detection                AI-generated check
     -> seasonal_metadata_check           solar/season vs claimed location

Each adapter result is summarized inline; full event payloads are dumped
to tools/dev/image-pipeline-bodies/ for forensic review.
"""

from __future__ import annotations

import json as _json
import sys
import time
from pathlib import Path

WORKERS_SRC = Path(__file__).resolve().parents[2] / "apps" / "workers" / "src"
if WORKERS_SRC.exists():
    sys.path.insert(0, str(WORKERS_SRC))

_OUT_DIR = Path(__file__).resolve().parent / "image-pipeline-bodies"
_OUT_DIR.mkdir(exist_ok=True)


def _save(name: str, events: list[dict]) -> None:
    """Dump raw events JSON for forensic review."""
    p = _OUT_DIR / f"{name}.json"
    p.write_text(_json.dumps(events, indent=2, default=str), encoding="utf-8")


def _format_event(ev: dict) -> str:
    """One-line summary of an event for readable console output."""
    et = ev.get("event_type", "?")
    pl = ev.get("payload", {}) if isinstance(ev.get("payload"), dict) else {}
    keys = list(pl.keys())[:6]
    snippet = ", ".join(f"{k}={str(pl.get(k))[:60]}" for k in keys)
    return f"  [{et:22}] {snippet}"


def main() -> None:
    from osint_goblin_workers.adapters_listing import extract_vrbo

    # Step 1: get a real VRBO listing's photo URL via the extractor.
    body_path = (
        Path(__file__).resolve().parent / "bypass-probe-bodies" / "vrbo-zendriver-status200.html"
    )
    if not body_path.exists():
        print(f"FATAL: {body_path} not found. Run probe-bypass-stacks.py first.")
        sys.exit(1)

    body = body_path.read_text(encoding="utf-8", errors="replace")
    listing = extract_vrbo(body, "https://www.vrbo.com/1682245")
    photo_urls = listing.get("photo_urls", [])
    if not photo_urls:
        print("FATAL: extract_vrbo returned no photo_urls.")
        sys.exit(1)

    image_url = photo_urls[0]
    print(f"Listing:    {listing['title']}")
    print(f"Address:    {listing['address_displayed']}")
    print(f"GPS:        {listing['gps_lat']}, {listing['gps_lon']}")
    print(f"Photo URL:  {image_url}")
    print()
    print(f"Bodies dumped to: {_OUT_DIR}")
    print()

    # Step 2: run each image adapter LIVE against the photo URL.
    # Some adapters require internet/binaries:
    #   - exiftool_full needs exiftool on PATH
    #   - reverse_image_aggregator hits Yandex/Lens/TinEye/Bing (synthetic-only
    #     in worker, since live engines block bots aggressively)
    #   - kartaview_nearby needs a GPS pin
    from osint_goblin_workers.adapters_image import (
        ai_image_detection,
        image_ai_local_detect,
        image_exif,
        image_pdq_hash,
        image_provenance_check,
        kartaview_nearby,
        reverse_image_aggregator,
        seasonal_metadata_check,
    )

    adapters: list[tuple[str, callable, dict]] = [
        ("image_exif", image_exif, {"image_url": image_url}),
        ("image_pdq_hash", image_pdq_hash, {"image_url": image_url}),
        ("image_ai_local_detect", image_ai_local_detect, {"image_url": image_url}),
        ("ai_image_detection", ai_image_detection, {"image_url": image_url}),
        (
            "seasonal_metadata_check",
            seasonal_metadata_check,
            {
                "image_url": image_url,
                "claimed_lat": listing["gps_lat"],
                "claimed_lon": listing["gps_lon"],
            },
        ),
        (
            "image_provenance_check",
            image_provenance_check,
            {"image_url": image_url},
        ),
        (
            "reverse_image_aggregator",
            reverse_image_aggregator,
            {"image_url": image_url},
        ),
        (
            "kartaview_nearby",
            kartaview_nearby,
            {
                "lat": listing["gps_lat"],
                "lon": listing["gps_lon"],
                "radius_m": 300,
            },
        ),
    ]

    for name, fn, payload in adapters:
        print(f"=== {name} ===")
        t0 = time.monotonic()
        try:
            events = fn(payload)
            elapsed = time.monotonic() - t0
            print(f"  -> {len(events)} event(s) in {elapsed:.1f}s")
            for ev in events[:6]:
                print(_format_event(ev))
            _save(name, events)
        except Exception as exc:
            print(f"  EXCEPTION: {type(exc).__name__}: {exc}")
            _save(name, [{"exception": f"{type(exc).__name__}: {exc}"}])
        print()


if __name__ == "__main__":
    main()

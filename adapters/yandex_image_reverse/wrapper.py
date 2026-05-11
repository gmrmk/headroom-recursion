"""Yandex reverse image search subprocess wrapper.

Yandex is the strongest engine for flipped/cropped/recolored variants
because its matcher uses neural features rather than exact-hash. Best
single engine for property-photo duplicate detection.

Contract:
  stdin:  {"image_url": "https://example.com/photo.jpg"}
  stdout: NDJSON
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.parse
from datetime import UTC, datetime


def _emit(event: dict) -> None:
    sys.stdout.write(json.dumps(event, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _run_synthetic(payload: dict) -> int:
    url = payload.get("image_url", "https://example.com/synthetic.jpg")
    _emit({"event_type": "tool-run-accepted", "payload": {"adapter": "yandex_image_reverse", "image_url": url, "synthetic": True}})
    for host in ("airbnb.com", "vrbo.com", "booking.com"):
        _emit(
            {
                "event_type": "image-match",
                "payload": {
                    "source": "yandex",
                    "image_url": url,
                    "match_url": f"https://www.{host}/listing/synthetic-12345",
                    "host_domain": host,
                    "match_size": "1024x768",
                    "synthetic": True,
                },
            }
        )
    _emit({"event_type": "tool-run-result", "payload": {"source": "yandex", "image_url": url, "matches": 3, "synthetic": True}})
    return 0


def _run_live(payload: dict) -> int:
    try:
        from scrapling.fetchers import StealthyFetcher
    except ImportError:
        return _run_synthetic(payload)
    image_url = (payload.get("image_url") or "").strip()
    if not image_url:
        _emit({"event_type": "tool-run-error", "payload": {"reason": "missing 'image_url'"}})
        return 2
    started = time.monotonic()
    _emit({"event_type": "tool-run-accepted", "payload": {"adapter": "yandex_image_reverse", "image_url": image_url, "started_at": _now_iso()}})
    url = "https://yandex.com/images/search?rpt=imageview&url=" + urllib.parse.quote(image_url, safe="")
    try:
        page = StealthyFetcher.fetch(url, headless=True, network_idle=True)
    except Exception as exc:
        _emit({"event_type": "tool-run-error", "payload": {"reason": f"scrapling {type(exc).__name__}: {exc}", "url": url}})
        return 3
    matches = []
    try:
        # Yandex SERP -- results are in CbirSites class. Anchors expose hostname + URL.
        sites = page.css(".CbirSites-Item, .Sites-Item, .Site-Item")[:10]
        for s in sites:
            link = s.css_first("a[href*='://']")
            if not link:
                continue
            href = link.attrib.get("href", "")
            m = re.search(r"https?://([^/]+)", href)
            host = m.group(1) if m else ""
            title_el = s.css_first(".CbirSites-ItemTitle, .Site-Title")
            title = (title_el.text or "").strip() if title_el else ""
            matches.append({"source": "yandex", "image_url": image_url, "match_url": href, "host_domain": host, "title": title})
    except Exception as exc:
        sys.stderr.write(f"parse soft-fail: {type(exc).__name__}: {exc}\n")
    for m in matches:
        _emit({"event_type": "image-match", "payload": m})
    _emit({"event_type": "tool-run-result", "payload": {"source": "yandex", "image_url": image_url, "matches": len(matches), "duration_s": round(time.monotonic() - started, 2)}})
    return 0


def main() -> int:
    raw = sys.stdin.read().strip()
    if not raw:
        return 2
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return 2
    if os.environ.get("OSINT_ADAPTER_MODE") == "synthetic":
        return _run_synthetic(payload)
    return _run_live(payload)


if __name__ == "__main__":
    raise SystemExit(main())

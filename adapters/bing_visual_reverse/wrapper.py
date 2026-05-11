"""Bing Visual Search subprocess wrapper.

Third reverse-image engine for triangulation. Less hostile anti-bot
than Google. Catches matches that Yandex + Google miss.
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
    _emit({"event_type": "tool-run-accepted", "payload": {"adapter": "bing_visual_reverse", "image_url": url, "synthetic": True}})
    _emit({
        "event_type": "image-match",
        "payload": {
            "source": "bing",
            "image_url": url,
            "match_url": "https://www.realtor.com/synthetic-listing",
            "host_domain": "realtor.com",
            "title": "Synthetic Bing match",
            "synthetic": True,
        },
    })
    _emit({"event_type": "tool-run-result", "payload": {"source": "bing", "image_url": url, "matches": 1, "synthetic": True}})
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
    _emit({"event_type": "tool-run-accepted", "payload": {"adapter": "bing_visual_reverse", "image_url": image_url, "started_at": _now_iso()}})
    # Bing Visual Search URL pattern
    url = "https://www.bing.com/images/search?view=detailv2&iss=sbi&form=SBIVSP&q=imgurl:" + urllib.parse.quote(image_url, safe="")
    try:
        page = StealthyFetcher.fetch(url, headless=True, network_idle=True)
    except Exception as exc:
        _emit({"event_type": "tool-run-error", "payload": {"reason": f"scrapling {type(exc).__name__}: {exc}", "url": url}})
        return 3
    matches = []
    try:
        items = page.css(".richImage, .img_cont, .imgpt, .insightItem")[:15]
        seen = set()
        for it in items:
            link = it.css_first("a[href*='://']")
            if not link:
                continue
            href = link.attrib.get("href", "")
            if href in seen or "bing.com" in href:
                continue
            seen.add(href)
            host_m = re.search(r"https?://([^/]+)", href)
            host = host_m.group(1) if host_m else ""
            title = (link.attrib.get("aria-label", "") or link.text or "").strip()
            matches.append({"source": "bing", "image_url": image_url, "match_url": href, "host_domain": host, "title": title[:200]})
            if len(matches) >= 10:
                break
    except Exception as exc:
        sys.stderr.write(f"parse soft-fail: {type(exc).__name__}: {exc}\n")
    for m in matches:
        _emit({"event_type": "image-match", "payload": m})
    _emit({"event_type": "tool-run-result", "payload": {"source": "bing", "image_url": image_url, "matches": len(matches), "duration_s": round(time.monotonic() - started, 2)}})
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

"""Google Lens reverse image search subprocess wrapper.

Hits images.google.com via the legacy reverse-image-search URL.
Heavy anti-bot; falls back to honest tool-run-error if Google
captcha-walls. For flipped images, prefer yandex_image_reverse.
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
    _emit({"event_type": "tool-run-accepted", "payload": {"adapter": "google_lens_reverse", "image_url": url, "synthetic": True}})
    for host in ("airbnb.com", "zillow.com"):
        _emit({
            "event_type": "image-match",
            "payload": {
                "source": "google-lens",
                "image_url": url,
                "match_url": f"https://{host}/synthetic-page",
                "host_domain": host,
                "title": f"Synthetic match on {host}",
                "synthetic": True,
            },
        })
    _emit({"event_type": "tool-run-result", "payload": {"source": "google-lens", "image_url": url, "matches": 2, "synthetic": True}})
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
    _emit({"event_type": "tool-run-accepted", "payload": {"adapter": "google_lens_reverse", "image_url": image_url, "started_at": _now_iso()}})
    url = "https://www.google.com/searchbyimage?image_url=" + urllib.parse.quote(image_url, safe="")
    try:
        page = StealthyFetcher.fetch(url, headless=True, network_idle=True)
    except Exception as exc:
        _emit({"event_type": "tool-run-error", "payload": {"reason": f"scrapling {type(exc).__name__}: {exc}", "url": url, "suggest": "Google captcha-walling; try yandex_image_reverse"}})
        return 3
    matches = []
    try:
        # Google's reverse image surface varies; look for organic result anchors
        links = page.css("a[href*='/url?q=']")[:15]
        seen = set()
        for link in links:
            href = link.attrib.get("href", "")
            m = re.search(r"/url\?q=([^&]+)", href)
            if not m:
                continue
            target = urllib.parse.unquote(m.group(1))
            if target in seen or "google.com" in target:
                continue
            seen.add(target)
            host_m = re.search(r"https?://([^/]+)", target)
            host = host_m.group(1) if host_m else ""
            title = (link.text or "").strip()
            matches.append({"source": "google-lens", "image_url": image_url, "match_url": target, "host_domain": host, "title": title[:200]})
            if len(matches) >= 10:
                break
    except Exception as exc:
        sys.stderr.write(f"parse soft-fail: {type(exc).__name__}: {exc}\n")
    for m in matches:
        _emit({"event_type": "image-match", "payload": m})
    _emit({"event_type": "tool-run-result", "payload": {"source": "google-lens", "image_url": image_url, "matches": len(matches), "duration_s": round(time.monotonic() - started, 2)}})
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

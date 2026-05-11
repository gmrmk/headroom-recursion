"""Google SERP phone-mention search (W3.ph, Sprint 4).

Searches Google for any public mention of a phone number -- catches
social media listings, business pages, Yelp/Craigslist posts. Same
Scrapling subprocess pattern as google_serp_linkedin.

Contract:
  stdin:  {"phone": "+1 555 867 5309"}
  stdout: NDJSON
  exit:   0 / non-zero
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
    phone = payload.get("phone", "+1 555 867 5309")
    _emit({"event_type": "tool-run-accepted", "payload": {"adapter": "google_serp_phone", "phone": phone, "synthetic": True}})
    fixtures = [
        {"url": "https://www.yelp.com/biz/synthetic-business", "domain": "yelp.com", "title": "Synthetic Pizza Co."},
        {"url": "https://www.linkedin.com/in/alice-smith", "domain": "linkedin.com", "title": "Alice Smith — Synthetic Co."},
    ]
    for m in fixtures:
        _emit({
            "event_type": "person-match",
            "payload": {
                "source": "google-serp-phone",
                "phone": phone,
                "match_url": m["url"],
                "host_domain": m["domain"],
                "snippet": m["title"],
                "synthetic": True,
            },
        })
    _emit({"event_type": "tool-run-result", "payload": {"phone": phone, "matches": len(fixtures), "synthetic": True}})
    return 0


def _run_live(payload: dict) -> int:
    try:
        from scrapling.fetchers import StealthyFetcher
    except ImportError:
        return _run_synthetic(payload)
    phone = (payload.get("phone") or "").strip()
    if not phone:
        _emit({"event_type": "tool-run-error", "payload": {"reason": "missing 'phone'"}})
        return 2
    started = time.monotonic()
    _emit({"event_type": "tool-run-accepted", "payload": {"adapter": "google_serp_phone", "phone": phone, "started_at": _now_iso()}})

    # Quote the phone exactly to bias Google toward literal matches.
    # Google handles +/- separators in phone queries reasonably well.
    query = f'"{phone}"'
    url = "https://www.google.com/search?" + urllib.parse.urlencode({"q": query, "num": "10"})

    try:
        page = StealthyFetcher.fetch(url, headless=True, network_idle=True)
    except Exception as exc:
        _emit({
            "event_type": "tool-run-error",
            "payload": {
                "reason": f"scrapling {type(exc).__name__}: {exc}",
                "url": url,
                "suggest": "Google captcha-walling; retry later or use synthetic",
            },
        })
        return 3

    matches = []
    try:
        links = page.css("a[href*='/url?q=']")[:20]
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
            matches.append({
                "source": "google-serp-phone",
                "phone": phone,
                "match_url": target,
                "host_domain": host,
                "snippet": title[:200],
            })
            if len(matches) >= 8:
                break
    except Exception as exc:
        sys.stderr.write(f"parse soft-fail: {type(exc).__name__}: {exc}\n")

    for m in matches:
        _emit({"event_type": "person-match", "payload": m})
    _emit({
        "event_type": "tool-run-result",
        "payload": {
            "phone": phone,
            "matches": len(matches),
            "duration_s": round(time.monotonic() - started, 2),
            "url": url,
        },
    })
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

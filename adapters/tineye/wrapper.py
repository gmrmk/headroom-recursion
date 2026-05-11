"""TinEye reverse-image-search subprocess wrapper (Sprint 3).

Runs in the empirical venv (Scrapling + Patchright). The osint-goblin
worker dispatches this via subprocess_adapter.make_subprocess_adapter
with python_executable pinned to the empirical venv path.

Contract (Sora ADR-0004 sec.5):
  stdin:  {"image_url": "https://example.com/photo.jpg"} on one line, EOF
  stdout: NDJSON event objects, one per line
  stderr: free-form log
  exit:   0 on clean run; non-zero on adapter failure

Live mode policy:
  TinEye offers a free public web UI at tineye.com/search. Their
  commercial API exists for high-volume use; for personal-use
  property-vetting (one investigator manually reviewing host photos),
  the free web UI is the appropriate surface. We use Scrapling's
  StealthyFetcher because TinEye has bot-detection on the search
  results page. We do NOT scrape thumbnails or copyrighted images;
  the wrapper only extracts result URLs + domains + first-seen dates
  -- metadata that is freely visible in the result HTML.

Synthetic mode (OSINT_ADAPTER_MODE=synthetic): bypasses the network
entirely and emits a deterministic fixture. The wire shape mirrors
the live shape so the dossier UX is identical.

Failure modes the wrapper surfaces honestly:
  - image_url missing or not a string  -> tool-run-error (reason)
  - Scrapling fetch fails              -> tool-run-error (reason + URL)
  - HTML structure changed             -> tool-run-result with matches=0
  - No matches found                   -> tool-run-result with matches=0
"""

from __future__ import annotations

import json
import os
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
    """Synthetic: two image-match events with realistic domain mix."""
    image_url = payload.get("image_url", "https://example.com/synthetic.jpg")
    _emit(
        {
            "event_type": "tool-run-accepted",
            "payload": {
                "adapter": "tineye_image",
                "image_url": image_url,
                "synthetic": True,
            },
        }
    )
    fixtures = [
        {
            "match_url": "https://www.airbnb.com/rooms/12345/photos",
            "domain": "airbnb.com",
            "first_seen": "2023-06-15",
            "page_title": "Cozy apartment downtown",
        },
        {
            "match_url": "https://www.facebook.com/profile/photo/789",
            "domain": "facebook.com",
            "first_seen": "2021-03-08",
            "page_title": "John Smith profile photo",
        },
    ]
    for m in fixtures:
        _emit({"event_type": "image-match", "payload": {**m, "synthetic": True}})
    _emit(
        {
            "event_type": "tool-run-result",
            "payload": {
                "image_url": image_url,
                "matches": len(fixtures),
                "synthetic": True,
            },
        }
    )
    return 0


def _run_live(payload: dict) -> int:
    """Fetch tineye.com/search?url=<image_url> via Scrapling, parse the
    results list. URL-based search is simpler than image-upload; for
    property-vetting the host's photo URL is what the investigator has."""
    try:
        from scrapling.fetchers import StealthyFetcher
    except ImportError:
        sys.stderr.write("scrapling not importable; falling back to synthetic\n")
        return _run_synthetic(payload)

    image_url = (payload.get("image_url") or "").strip()
    if not image_url:
        sys.stderr.write("error: missing 'image_url' in payload\n")
        _emit(
            {
                "event_type": "tool-run-error",
                "payload": {"reason": "missing 'image_url' in payload"},
            }
        )
        return 2

    started = time.monotonic()
    _emit(
        {
            "event_type": "tool-run-accepted",
            "payload": {
                "adapter": "tineye_image",
                "image_url": image_url,
                "started_at": _now_iso(),
            },
        }
    )

    search_url = "https://tineye.com/search?url=" + urllib.parse.quote(image_url, safe="")

    try:
        page = StealthyFetcher.fetch(search_url, headless=True, network_idle=True)
    except Exception as exc:
        sys.stderr.write(f"scrapling fetch failed: {type(exc).__name__}: {exc}\n")
        _emit(
            {
                "event_type": "tool-run-error",
                "payload": {
                    "reason": f"scrapling {type(exc).__name__}: {exc}",
                    "url": search_url,
                    "suggest": "TinEye may be rate-limiting; retry later or use synthetic",
                },
            }
        )
        return 3

    # TinEye result rows live in .match elements as of 2026-05; each row
    # has a result link, a domain (often shown as the link text), and a
    # first-seen / last-seen date. Selectors below mirror empirical
    # structure; if TinEye re-skins, the fallback is zero matches plus
    # a tool-run-result event documenting the empty page.
    matches = []
    try:
        rows = page.css("div.match")[:10]  # top 10
        for row in rows:
            # Try multiple selectors; TinEye occasionally A/B-tests layout
            link_el = row.css_first("a.match-thumb, a.match-link, h4 a")
            href = link_el.attrib.get("href", "") if link_el else ""
            title_el = row.css_first(".match-details h4, .match-title")
            title = (title_el.text or "").strip() if title_el else ""
            date_el = row.css_first(".match-details .date, .first-seen")
            first_seen = (date_el.text or "").strip() if date_el else ""
            domain = ""
            if href:
                try:
                    parsed = urllib.parse.urlparse(href)
                    domain = parsed.netloc
                except (ValueError, AttributeError):
                    domain = ""
            if href:
                matches.append(
                    {
                        "match_url": href,
                        "domain": domain,
                        "page_title": title,
                        "first_seen": first_seen,
                    }
                )
    except Exception as exc:
        sys.stderr.write(f"parse error: {type(exc).__name__}: {exc}\n")
        _emit(
            {
                "event_type": "tool-run-error",
                "payload": {
                    "reason": f"parse {type(exc).__name__}: {exc}",
                    "url": search_url,
                    "suggest": "TinEye may have re-skinned; check selectors",
                },
            }
        )
        return 4

    for m in matches:
        _emit({"event_type": "image-match", "payload": m})
    _emit(
        {
            "event_type": "tool-run-result",
            "payload": {
                "image_url": image_url,
                "matches": len(matches),
                "duration_s": round(time.monotonic() - started, 2),
                "url": search_url,
            },
        }
    )
    return 0


def main() -> int:
    raw = sys.stdin.read().strip()
    if not raw:
        sys.stderr.write("error: empty stdin payload\n")
        return 2
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        sys.stderr.write(f"error: invalid JSON payload: {exc}\n")
        return 2

    if os.environ.get("OSINT_ADAPTER_MODE") == "synthetic":
        sys.stderr.write("OSINT_ADAPTER_MODE=synthetic; bypassing live fetch\n")
        return _run_synthetic(payload)

    return _run_live(payload)


if __name__ == "__main__":
    raise SystemExit(main())

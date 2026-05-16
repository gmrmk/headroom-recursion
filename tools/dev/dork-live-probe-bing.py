"""Live Bing probe — confirms Scrapling-routed Bing fetch returns parseable
results. Run manually before trusting the Bing engine in real investigations.

    python tools/dev/dork-live-probe-bing.py
    python tools/dev/dork-live-probe-bing.py --query 'site:linkedin.com "Satya Nadella"'
    python tools/dev/dork-live-probe-bing.py --tier stealthy  # if fetcher tier blocked

Exit 0 if >= 3 hits parsed. Non-zero otherwise -- that's the signal the
parser needs an update or Bing is rate-limiting this IP.

Naomi gate: query string contains the operator's chosen probe target, so
the request DOES leak to Bing. Default query is a benign public-figure
search; pass a different one via --query when probing against a real seed.
"""

from __future__ import annotations

import argparse
import sys
import urllib.parse
from pathlib import Path

WORKERS_SRC = Path(__file__).resolve().parents[2] / "apps" / "workers" / "src"
if WORKERS_SRC.exists():
    sys.path.insert(0, str(WORKERS_SRC))

from osint_goblin_workers.adapters_dork import (  # noqa: E402
    _BING_URL,
    _bing_fetch,
    _bing_ua_for_query,
    _parse_bing_html,
)


def probe(query: str, tier: str) -> int:
    ua = _bing_ua_for_query(0)
    encoded = urllib.parse.quote_plus(query)
    url = f"{_BING_URL}?q={encoded}&cc=us&setlang=en-US"
    print(f"[probe] tier={tier}  q={query!r}  ua={ua[:50]}...")
    status, body = _bing_fetch(url, ua, timeout_s=45.0)
    print(f"[probe] status={status}  body_len={len(body)}")
    if status != 200:
        print("[probe] FAIL -- non-200; Bing may be bot-walling this client fingerprint")
        return 2

    hits = _parse_bing_html(body)
    print(f"[probe] parsed {len(hits)} hits")
    for i, h in enumerate(hits[:5], 1):
        print(f"  {i}. {h['title'][:80]}")
        print(f"     {h['url'][:100]}")
        snippet = h.get("snippet", "")
        if snippet:
            print(f"     > {snippet[:120]}")

    if len(hits) < 3:
        print("[probe] FAIL -- fewer than 3 hits; parser may be drifted")
        out = Path(__file__).resolve().parent / "dork-live-probe-bing.last.html"
        out.write_text(body, encoding="utf-8")
        print(f"[probe] raw body saved to {out}")
        return 1

    snippet_count = sum(1 for h in hits if h.get("snippet"))
    print(f"[probe] {snippet_count}/{len(hits)} hits had snippets")
    print("[probe] PASS")
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--query",
        default='site:linkedin.com "Satya Nadella"',
        help="Stable query that should always return hits if Bing is functional",
    )
    p.add_argument(
        "--tier",
        default="fetcher",
        choices=("fetcher", "dynamic", "stealthy"),
        help="Scrapling tier (fetcher=curl_cffi, dynamic=Chrome, stealthy=Camoufox/Patchright)",
    )
    args = p.parse_args()
    return probe(args.query, args.tier)


if __name__ == "__main__":
    raise SystemExit(main())

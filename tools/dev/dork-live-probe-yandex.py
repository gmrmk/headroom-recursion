"""Live Yandex probe -- confirms StealthyFetcher-routed Yandex returns
parseable results. Run manually before trusting the Yandex engine.

    python tools/dev/dork-live-probe-yandex.py
    python tools/dev/dork-live-probe-yandex.py --query 'Satya Nadella linkedin.com'

Exit 0 if >= 3 hits parsed. Non-zero otherwise -- signal that the parser
needs an update OR Yandex is anti-bot-walling this IP.

Naomi gate: query string contains the operator's chosen probe target,
so the request DOES leak to Yandex. Default query is a benign
public-figure search.
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
    _YANDEX_URL,
    _parse_yandex_html,
    _yandex_fetch,
)


def probe(query: str) -> int:
    encoded = urllib.parse.quote_plus(query)
    url = f"{_YANDEX_URL}?text={encoded}"
    print(f"[probe] q={query!r}")
    status, body = _yandex_fetch(url, timeout_s=60.0)
    print(f"[probe] status={status}  body_len={len(body)}")
    if status != 200:
        print("[probe] FAIL -- non-200; Yandex may be bot-walling this client")
        return 2

    hits = _parse_yandex_html(body)
    print(f"[probe] parsed {len(hits)} hits")
    for i, h in enumerate(hits[:5], 1):
        print(f"  {i}. {h['title'][:80]}")
        print(f"     {h['url'][:100]}")
        snippet = h.get("snippet", "")
        if snippet:
            print(f"     > {snippet[:120]}")

    if len(hits) < 3:
        print("[probe] FAIL -- fewer than 3 hits; parser may be drifted")
        out = Path(__file__).resolve().parent / "dork-live-probe-yandex.last.html"
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
        default='"Satya Nadella" linkedin.com',
        help="Stable query that should always return hits if Yandex is functional",
    )
    args = p.parse_args()
    return probe(args.query)


if __name__ == "__main__":
    raise SystemExit(main())

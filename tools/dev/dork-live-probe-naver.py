"""Live Naver probe -- confirms StealthyFetcher + BS4 card-walk parser
returns hits on real Naver responses.

    python tools/dev/dork-live-probe-naver.py
    python tools/dev/dork-live-probe-naver.py --query '"이재용" linkedin.com'

Exit 0 if >= 3 hits parsed. Naver's coverage is strongest for KR-language
queries; English-only queries may return zero results.
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
    _NAVER_URL,
    _naver_fetch,
    _parse_naver_html,
)


def probe(query: str) -> int:
    encoded = urllib.parse.quote_plus(query)
    url = f"{_NAVER_URL}?where=web&query={encoded}"
    print(f"[probe] q={query!r}")
    status, body = _naver_fetch(url, timeout_s=60.0)
    print(f"[probe] status={status}  body_len={len(body)}")
    if status != 200:
        print("[probe] FAIL -- non-200; Naver may be bot-walling")
        return 2

    hits = _parse_naver_html(body)
    print(f"[probe] parsed {len(hits)} hits")
    for i, h in enumerate(hits[:5], 1):
        try:
            print(f"  {i}. {h['title'][:80]}")
            print(f"     {h['url'][:100]}")
            snippet = h.get("snippet", "")
            if snippet:
                print(f"     > {snippet[:120]}")
        except UnicodeEncodeError:
            print(f"  {i}. <{len(h['title'])} char title> -> {h['url'][:100]}")

    if len(hits) < 3:
        print("[probe] FAIL -- fewer than 3 hits; try a Korean query for better coverage")
        out = Path(__file__).resolve().parent / "dork-live-probe-naver.last.html"
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
        default='"이재용" linkedin.com',  # Lee Jae-yong (Samsung) -- known KR query
        help="Stable query (Korean-language returns best Naver coverage)",
    )
    args = p.parse_args()
    return probe(args.query)


if __name__ == "__main__":
    raise SystemExit(main())

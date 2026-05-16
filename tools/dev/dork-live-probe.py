"""Live DDG probe — confirms the adapter's regex still parses today's
HTML endpoint response. Run manually before trusting the dork sweep.

    python tools/dev/dork-live-probe.py
    python tools/dev/dork-live-probe.py --query 'site:linkedin.com "Satya Nadella"'

Exit 0 if >= 3 hits parsed. Non-zero otherwise — that's the signal the
parser needs an update.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow running this script directly without installing the workers package.
WORKERS_SRC = Path(__file__).resolve().parents[2] / "apps" / "workers" / "src"
if WORKERS_SRC.exists():
    sys.path.insert(0, str(WORKERS_SRC))

import httpx  # noqa: E402
from osint_goblin_workers.adapters_dork import (  # noqa: E402
    _DDG_URL,
    _DEFAULT_UA,
    _parse_ddg_html,
)


def probe(query: str) -> int:
    print(f"[probe] POST {_DDG_URL}  q={query!r}")
    with httpx.Client(
        timeout=15.0,
        headers={"User-Agent": _DEFAULT_UA, "Accept": "text/html"},
        follow_redirects=True,
    ) as c:
        r = c.post(_DDG_URL, data={"q": query})
    print(f"[probe] status={r.status_code}  body_len={len(r.text)}")
    if r.status_code != 200:
        print("[probe] FAIL — non-200; DDG may be rate-limiting this IP")
        return 2

    hits = _parse_ddg_html(r.text)
    print(f"[probe] parsed {len(hits)} hits")
    for i, h in enumerate(hits[:5], 1):
        print(f"  {i}. {h['title'][:80]}")
        print(f"     {h['url'][:100]}")

    if len(hits) < 3:
        print("[probe] FAIL — fewer than 3 hits; parser may be drifted")
        # Save the raw body so an operator can eyeball the markup
        out = Path(__file__).resolve().parent / "dork-live-probe.last.html"
        out.write_text(r.text, encoding="utf-8")
        print(f"[probe] raw body saved to {out}")
        return 1

    print("[probe] PASS")
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--query",
        default='site:linkedin.com "Satya Nadella"',
        help="Stable query that should always return hits if DDG is functional",
    )
    args = p.parse_args()
    return probe(args.query)


if __name__ == "__main__":
    raise SystemExit(main())

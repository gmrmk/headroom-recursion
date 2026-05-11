"""Inside Airbnb city-CSV fetcher (Sprint 3 helper).

The inside_airbnb_listings adapter reads a pre-downloaded city CSV
(see apps/workers/.../adapters_property.py). This script is the
download step: given the URL of a listings.csv.gz file from
http://insideairbnb.com/get-the-data/, fetch + decompress + place
at the canonical local path.

The adapter does NOT auto-fetch because:
  - Each city CSV is 5-50 MB; per-call download is wasteful.
  - Inside Airbnb publishes quarterly; freshness is bounded.
  - Cache invalidation belongs to the operator, not the adapter.

Usage:
  python tools/dev/fetch-inside-airbnb.py \\
      --url http://data.insideairbnb.com/united-states/il/chicago/2026-04-01/data/listings.csv.gz

  python tools/dev/fetch-inside-airbnb.py \\
      --url ... --out data/inside-airbnb/my-city.csv

If --out is omitted, the output path is derived from the URL:
  http://data.insideairbnb.com/united-states/il/chicago/2026-04-01/data/listings.csv.gz
  -> data/inside-airbnb/chicago-2026-04-01.csv

The fetcher caches by URL+date so repeated runs against the same
city+date snapshot are idempotent.

Exit codes:
  0  CSV downloaded (or already cached) at the target path
  1  download failed
  2  invalid URL or arguments
"""

from __future__ import annotations

import argparse
import contextlib
import gzip
import re
import shutil
import sys
import urllib.parse
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT_DIR = REPO_ROOT / "data" / "inside-airbnb"

USER_AGENT = "osint-goblin/0.1 (personal-investigator)"


def _derive_out_path(url: str) -> Path:
    """Derive `<city>-<date>.csv` from the Inside Airbnb URL.

    Expected URL shape:
      http://data.insideairbnb.com/<country>/<region>/<city>/<date>/data/listings.csv.gz
    """
    parsed = urllib.parse.urlparse(url)
    parts = [p for p in parsed.path.split("/") if p]
    # Last 3 segments are: <date>, 'data', 'listings.csv.gz' (or similar)
    if len(parts) < 4:
        return DEFAULT_OUT_DIR / "listings.csv"
    date = parts[-3]
    city = parts[-4]
    # Sanitize -- strip anything weird
    safe = re.sub(r"[^a-z0-9\-]", "-", f"{city}-{date}".lower())
    return DEFAULT_OUT_DIR / f"{safe}.csv"


def _download(url: str, target_gz: Path) -> bool:
    """Stream-download `url` to `target_gz`. Returns True on success."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            if resp.status != 200:
                print(f"  ERR  HTTP {resp.status} from {url}", file=sys.stderr)
                return False
            target_gz.parent.mkdir(parents=True, exist_ok=True)
            with target_gz.open("wb") as out:
                shutil.copyfileobj(resp, out, length=64 * 1024)
        return True
    except Exception as exc:
        print(f"  ERR  download failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return False


def _decompress(gz_path: Path, csv_path: Path) -> bool:
    """gz-decompress `gz_path` into `csv_path`. Returns True on success."""
    try:
        with gzip.open(gz_path, "rb") as src, csv_path.open("wb") as dst:
            shutil.copyfileobj(src, dst, length=64 * 1024)
        return True
    except Exception as exc:
        print(f"  ERR  decompress failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return False


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Download an Inside Airbnb city-listings CSV for the adapter."
    )
    parser.add_argument("--url", required=True, help="listings.csv.gz URL from insideairbnb.com")
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output CSV path. Default: data/inside-airbnb/<city>-<date>.csv",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-download even if the CSV already exists at the target path.",
    )
    args = parser.parse_args()

    if not args.url.lower().endswith(".csv.gz"):
        print(f"  ERR  --url must end in .csv.gz; got {args.url}", file=sys.stderr)
        return 2

    csv_path: Path = args.out if args.out is not None else _derive_out_path(args.url)
    csv_path = csv_path.resolve()

    if csv_path.is_file() and not args.force:
        print(f"  CACHED  {csv_path}  (use --force to re-download)")
        return 0

    print(f"  Downloading: {args.url}")
    print(f"  Target:      {csv_path}")

    gz_path = csv_path.with_suffix(".csv.gz")
    if not _download(args.url, gz_path):
        return 1
    print(f"  Downloaded:  {gz_path}  ({gz_path.stat().st_size // 1024} KiB)")

    if not _decompress(gz_path, csv_path):
        return 1
    print(f"  Decompressed: {csv_path}  ({csv_path.stat().st_size // 1024} KiB)")
    # Clean up the .gz; we only need the CSV
    with contextlib.suppress(OSError):
        gz_path.unlink()

    print()
    print("  Done. The inside_airbnb_listings adapter can now read this CSV:")
    print(f'    {{"csv_path": "{csv_path}", "host_name": "..."}}')
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Reference-database bootstrap for IP intel.

Downloads (or refreshes) the reference databases that feed the
Identity Triangulation sprint's IP consensus verdict:

  - DB-IP City Lite (.mmdb)          (city/country/lat-lon)
  - DB-IP ASN Lite (.mmdb)           (AS number + org name)
  - IP2Proxy LITE PX11 BIN           (VPN/Tor/datacenter/residential)
  - Tor Project bulk-exit-list       (definitive Tor flag)
  - X4BNet/lists_vpn ipv4.txt        (community VPN/datacenter CIDR ranges)

DB-IP Lite was chosen over MaxMind GeoLite2 because MaxMind gates
GeoLite2 license keys on corporate email approval, which is impractical
for individual investigators. DB-IP Lite is CC-BY-4.0, ships the
identical MaxMind MMDB format (drop-in compatible with the `geoip2`
Python library), refreshes monthly, and requires no account.

These are reference databases, not target data. They describe IP
*ranges* (like a phone book), safe to persist on disk under
`data/reference/` regardless of ephemeral-mode setting.

Idempotent. Re-runs skip files that already exist; pass `--force` to
re-download anyway.

Usage:
    uv run python infra/scripts/fetch_ip_refdata.py [--force] [--source <name>]

Environment:
    IP2PROXY_DOWNLOAD_TOKEN  Free token from https://lite.ip2location.com.
                             Without it, IP2Proxy LITE downloads are
                             skipped and the IP intel adapter falls back
                             to ASN-heuristic + DB-IP-only verdicts.
"""

from __future__ import annotations

import argparse
import datetime
import gzip
import os
import shutil
import sys
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[2]
REFDATA_DIR = REPO_ROOT / "data" / "reference"


# ---------------------------------------------------------------------------
# Source registry
# ---------------------------------------------------------------------------

# Public sources -- no auth required.
TOR_EXIT_URL = "https://check.torproject.org/torbulkexitlist"
X4BNET_VPN_URL = "https://raw.githubusercontent.com/X4BNet/lists_vpn/main/output/vpn/ipv4.txt"
X4BNET_DATACENTER_URL = (
    "https://raw.githubusercontent.com/X4BNet/lists_vpn/main/output/datacenter/ipv4.txt"
)
IP2PROXY_LITE_URL_TEMPLATE = (
    "https://download.ip2location.com/lite/?file=PX11LITEBINIPV6&token={token}"
)

# DB-IP Lite: monthly .mmdb.gz files at a predictable URL pattern.
# Files at this URL pattern are CC-BY-4.0 and require no account.
# Format: dbip-{kind}-lite-YYYY-MM.mmdb.gz where kind in {"city", "asn"}.
DBIP_URL_TEMPLATE = "https://download.db-ip.com/free/dbip-{kind}-lite-{ym}.mmdb.gz"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ensure_refdata_dir() -> None:
    REFDATA_DIR.mkdir(parents=True, exist_ok=True)


def _download(url: str, dest: Path, *, label: str) -> None:
    """Stream a URL to dest. Atomic rename on success."""
    tmp = dest.with_suffix(dest.suffix + ".part")
    print(f"  [{label}] downloading {url[:80]}{'...' if len(url) > 80 else ''}")
    with httpx.stream("GET", url, timeout=120.0, follow_redirects=True) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", "0")) or None
        with tmp.open("wb") as f:
            written = 0
            for chunk in r.iter_bytes(chunk_size=64 * 1024):
                f.write(chunk)
                written += len(chunk)
        if total and written != total:
            tmp.unlink(missing_ok=True)
            raise RuntimeError(f"{label}: short read ({written}/{total} bytes)")
    tmp.replace(dest)
    size_kb = dest.stat().st_size / 1024.0
    print(f"  [{label}] ok ({size_kb:,.0f} KB) -> {dest.name}")


def _gunzip(src_gz: Path, dest: Path) -> None:
    """Gunzip src_gz to dest; remove the .gz on success."""
    with gzip.open(src_gz, "rb") as src, dest.open("wb") as dst:
        shutil.copyfileobj(src, dst)
    src_gz.unlink(missing_ok=True)


def _current_yyyy_mm() -> str:
    return datetime.date.today().strftime("%Y-%m")


def _previous_yyyy_mm(offset: int = 1) -> str:
    """Returns the YYYY-MM string `offset` months in the past."""
    today = datetime.date.today()
    year, month = today.year, today.month
    for _ in range(offset):
        month -= 1
        if month == 0:
            month = 12
            year -= 1
    return f"{year:04d}-{month:02d}"


# ---------------------------------------------------------------------------
# Per-source fetchers
# ---------------------------------------------------------------------------


def fetch_tor_exit_list(*, force: bool = False) -> None:
    dest = REFDATA_DIR / "tor-exit.txt"
    if dest.exists() and not force:
        # Always refresh Tor list -- it changes every ~30 min upstream.
        # The "skip if fresh" rule for Tor uses age, not existence.
        age_h = (Path.cwd().stat().st_mtime - dest.stat().st_mtime) / 3600.0
        if age_h < 4:
            print("  [tor] skip (fresh, <4h old)")
            return
    _download(TOR_EXIT_URL, dest, label="tor")


def fetch_x4bnet(*, force: bool = False) -> None:
    for url, name in (
        (X4BNET_VPN_URL, "x4bnet-vpn.txt"),
        (X4BNET_DATACENTER_URL, "x4bnet-datacenter.txt"),
    ):
        dest = REFDATA_DIR / name
        if dest.exists() and not force:
            print(f"  [{name.removesuffix('.txt')}] skip (exists; use --force to refresh)")
            continue
        _download(url, dest, label=name.removesuffix(".txt"))


def fetch_ip2proxy_lite(*, force: bool = False) -> None:
    dest = REFDATA_DIR / "IP2PROXY-LITE-PX11.BIN"
    token = os.environ.get("IP2PROXY_DOWNLOAD_TOKEN", "").strip()
    if not token:
        print(
            "  [ip2proxy] SKIP: IP2PROXY_DOWNLOAD_TOKEN not set.\n"
            "            Get a free token at https://lite.ip2location.com and\n"
            "            export IP2PROXY_DOWNLOAD_TOKEN=<token> before re-running."
        )
        return
    if dest.exists() and not force:
        print("  [ip2proxy] skip (exists; use --force to refresh)")
        return
    # IP2Proxy LITE ships as a ZIP containing the .BIN.
    zip_dest = REFDATA_DIR / "ip2proxy-px11.zip"
    _download(IP2PROXY_LITE_URL_TEMPLATE.format(token=token), zip_dest, label="ip2proxy")
    # Extract the BIN.
    import zipfile

    with zipfile.ZipFile(zip_dest) as zf:
        bins = [n for n in zf.namelist() if n.endswith(".BIN")]
        if not bins:
            zip_dest.unlink(missing_ok=True)
            raise RuntimeError("no .BIN member in IP2Proxy LITE zip")
        with zf.open(bins[0]) as src, dest.open("wb") as dst:
            shutil.copyfileobj(src, dst)
    zip_dest.unlink(missing_ok=True)
    print(f"  [ip2proxy] extracted -> {dest.name}")


def _try_dbip_url(kind: str) -> tuple[str, str]:
    """Returns (url, ym) for the current month's DB-IP Lite file.
    Tries the current YYYY-MM first; if a HEAD check 404s, falls back
    to the previous month (publish day is mid-month).
    """
    for offset in range(0, 3):
        ym = _current_yyyy_mm() if offset == 0 else _previous_yyyy_mm(offset)
        url = DBIP_URL_TEMPLATE.format(kind=kind, ym=ym)
        try:
            r = httpx.head(url, follow_redirects=True, timeout=20.0)
            if r.status_code == 200:
                return url, ym
        except httpx.RequestError:
            continue
    raise RuntimeError(f"no dbip-{kind}-lite available within the last 3 months")


def fetch_dbip(*, force: bool = False) -> None:
    """Download DB-IP City Lite + DB-IP ASN Lite .mmdb files.
    No key, no account; CC-BY-4.0 attribution required in dossier
    output. Drop-in compatible with the `geoip2` Python reader
    library (same MMDB format as MaxMind GeoLite2).
    """
    for kind, mmdb_name in (
        ("city", "dbip-city-lite.mmdb"),
        ("asn", "dbip-asn-lite.mmdb"),
    ):
        dest = REFDATA_DIR / mmdb_name
        if dest.exists() and not force:
            print(f"  [{mmdb_name}] skip (exists; use --force to refresh)")
            continue
        try:
            url, ym = _try_dbip_url(kind)
        except RuntimeError as exc:
            print(f"  [{mmdb_name}] ERROR: {exc}")
            continue
        gz_dest = REFDATA_DIR / f"{mmdb_name}.gz"
        _download(url, gz_dest, label=mmdb_name.removesuffix(".mmdb"))
        _gunzip(gz_dest, dest)
        print(f"  [{mmdb_name}] extracted ({ym}) -> {dest.name}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

_SOURCES = {
    "tor": fetch_tor_exit_list,
    "x4bnet": fetch_x4bnet,
    "ip2proxy": fetch_ip2proxy_lite,
    "dbip": fetch_dbip,
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-download even if the destination file already exists.",
    )
    parser.add_argument(
        "--source",
        choices=sorted(_SOURCES.keys()),
        help="Fetch only the named source. Default: all.",
    )
    args = parser.parse_args()

    _ensure_refdata_dir()
    print(f"refdata dir: {REFDATA_DIR}")

    targets = [args.source] if args.source else list(_SOURCES.keys())
    for name in targets:
        print(f"[{name}]")
        _SOURCES[name](force=args.force)

    print("\nDone. Reference databases under data/reference/.")
    print("(Naomi-safe: knowledge bases, not target data.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

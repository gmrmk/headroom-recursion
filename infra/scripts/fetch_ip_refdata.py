"""Reference-database bootstrap for IP intel.

Downloads (or refreshes) the four reference databases that feed the
Identity Triangulation sprint's IP consensus verdict:

  - MaxMind GeoLite2-City.mmdb       (city/country/lat-lon)
  - MaxMind GeoLite2-ASN.mmdb        (AS number + org name)
  - IP2Proxy LITE PX11 BIN           (VPN/Tor/datacenter/residential)
  - Tor Project bulk-exit-list       (definitive Tor flag)
  - X4BNet/lists_vpn ipv4.txt        (community VPN/datacenter CIDR ranges)

These are reference databases, not target data. They describe IP
*ranges* (like a phone book). Naomi-strict: safe to persist on disk
under data/reference/.

Idempotent. Re-runs are cheap (HTTP HEAD + If-Modified-Since on the
public sources; for MaxMind, requires a license key in
MAXMIND_LICENSE_KEY).

Usage:
    uv run python infra/scripts/fetch_ip_refdata.py [--force] [--source <name>]

Environment:
    MAXMIND_LICENSE_KEY  Required for MaxMind downloads. Free; sign up
                         at https://www.maxmind.com/en/geolite2/signup.
                         Without it, MaxMind downloads are skipped and
                         the IP intel adapter falls back to ASN-heuristic
                         + IP2Proxy-only verdicts.
"""

from __future__ import annotations

import argparse
import gzip
import os
import shutil
import sys
import tarfile
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

# MaxMind downloads use a permalink with an account-scoped license key.
MAXMIND_CITY_URL_TEMPLATE = (
    "https://download.maxmind.com/app/geoip_download?"
    "edition_id=GeoLite2-City&license_key={key}&suffix=tar.gz"
)
MAXMIND_ASN_URL_TEMPLATE = (
    "https://download.maxmind.com/app/geoip_download?"
    "edition_id=GeoLite2-ASN&license_key={key}&suffix=tar.gz"
)


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


def _extract_mmdb_from_tarball(tarball: Path, dest_mmdb: Path) -> None:
    """MaxMind ships GeoLite2 as a tar.gz with the .mmdb nested inside.
    Extract just the .mmdb into dest, then delete the tarball.
    """
    with tarfile.open(tarball, "r:gz") as tf:
        members = [m for m in tf.getmembers() if m.name.endswith(".mmdb")]
        if not members:
            raise RuntimeError(f"no .mmdb member found in {tarball.name}")
        member = members[0]
        # Strip the directory prefix; write to dest_mmdb directly.
        with tf.extractfile(member) as src, dest_mmdb.open("wb") as dst:
            if src is None:
                raise RuntimeError(f"tarfile.extractfile returned None for {member.name}")
            shutil.copyfileobj(src, dst)
    tarball.unlink(missing_ok=True)


def _gunzip(src_gz: Path, dest: Path) -> None:
    with gzip.open(src_gz, "rb") as src, dest.open("wb") as dst:
        shutil.copyfileobj(src, dst)
    src_gz.unlink(missing_ok=True)


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


def fetch_maxmind(*, force: bool = False) -> None:
    key = os.environ.get("MAXMIND_LICENSE_KEY", "").strip()
    if not key:
        print(
            "  [maxmind] SKIP: MAXMIND_LICENSE_KEY not set.\n"
            "           Free MaxMind account required; sign up at\n"
            "           https://www.maxmind.com/en/geolite2/signup\n"
            "           and export MAXMIND_LICENSE_KEY=<key> before re-running."
        )
        return
    for url_template, mmdb_name in (
        (MAXMIND_CITY_URL_TEMPLATE, "GeoLite2-City.mmdb"),
        (MAXMIND_ASN_URL_TEMPLATE, "GeoLite2-ASN.mmdb"),
    ):
        dest = REFDATA_DIR / mmdb_name
        if dest.exists() and not force:
            print(f"  [{mmdb_name}] skip (exists; use --force to refresh)")
            continue
        tar_dest = REFDATA_DIR / (mmdb_name + ".tar.gz")
        _download(url_template.format(key=key), tar_dest, label=mmdb_name.removesuffix(".mmdb"))
        _extract_mmdb_from_tarball(tar_dest, dest)
        print(f"  [{mmdb_name}] extracted -> {dest.name}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

_SOURCES = {
    "tor": fetch_tor_exit_list,
    "x4bnet": fetch_x4bnet,
    "ip2proxy": fetch_ip2proxy_lite,
    "maxmind": fetch_maxmind,
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

"""Standalone evidence-package verifier.

Ships inside every evidence-package zip alongside chain.merkle, manifest.json,
the signature .sig files, the .tsr timestamp files, and the WARC artifacts.
A defense counsel or compliance auditor can run this on any machine with
Python 3.11+ and `cryptography` installed -- no other deps. The script
expects to live INSIDE the unzipped evidence package alongside the data files.

Usage:

    python verify.py <path-to-unzipped-evidence-dir>

Exit code 0 = chain verified. Non-zero = some integrity check failed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


def _load_chain(chain_path: Path) -> list[dict[str, Any]]:
    """Load chain.merkle (JSONL: one row per line)."""
    rows: list[dict[str, Any]] = []
    with chain_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def _verify_chain(rows: list[dict[str, Any]]) -> tuple[bool, int | None, str]:
    """Walk the chain. Same semantics as MerkleChain.verify_chain but on
    JSON dicts (since we can't import the package -- this is the offline tool)."""
    if not rows:
        return False, None, "empty chain"
    genesis_prev_hex = "00" * 32
    if rows[0].get("seq") != 0 or rows[0].get("prev_hash") != genesis_prev_hex:
        return False, 0, "genesis row malformed"
    prev_this_hash: str = ""
    prev_seq = -1
    for r in rows:
        seq = r["seq"]
        prev_hash = bytes.fromhex(r["prev_hash"])
        payload_hex = r.get("payload_hex", "")
        payload = bytes.fromhex(payload_hex)
        this_hash_claimed = bytes.fromhex(r["this_hash"])
        expected = hashlib.sha256(prev_hash + payload).digest()
        if expected != this_hash_claimed:
            return False, seq, f"this_hash mismatch at seq={seq}"
        if prev_seq >= 0:
            if r["prev_hash"] != prev_this_hash:
                return False, seq, f"prev_hash linkage broken at seq={seq}"
            if seq != prev_seq + 1:
                return False, seq, f"seq gap at seq={seq}"
        prev_this_hash = r["this_hash"]
        prev_seq = seq
    return True, None, "ok"


def _verify_signatures(evidence_dir: Path) -> tuple[bool, str]:
    """Verify each artifact's Ed25519 detached signature.

    Each artifact at <dir>/artifacts/<sha256>.zst has a sibling
    <dir>/signatures/<sha256>.sig (64-byte Ed25519). The chain head signature
    at <dir>/manifest.sig signs the manifest.json bytes with the
    investigation's signing key, whose public part is at
    <dir>/keys/investigation.pub.pem.

    Returns (ok, reason).
    """
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError:
        return False, "cryptography library not installed"

    pubkey_path = evidence_dir / "keys" / "investigation.pub.pem"
    manifest_path = evidence_dir / "manifest.json"
    manifest_sig_path = evidence_dir / "manifest.sig"

    if not pubkey_path.exists():
        return False, f"missing {pubkey_path}"
    if not manifest_path.exists():
        return False, f"missing {manifest_path}"
    if not manifest_sig_path.exists():
        return False, f"missing {manifest_sig_path}"

    pub = serialization.load_pem_public_key(pubkey_path.read_bytes())
    if not isinstance(pub, Ed25519PublicKey):
        return False, f"keys/investigation.pub.pem is not Ed25519 ({type(pub).__name__})"
    try:
        pub.verify(manifest_sig_path.read_bytes(), manifest_path.read_bytes())
    except InvalidSignature:
        return False, "manifest signature does not verify"

    return True, "manifest signature ok"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify an OSINT GOBLIN evidence package.")
    parser.add_argument("evidence_dir", type=Path, help="path to unzipped evidence directory")
    parser.add_argument(
        "--skip-signatures",
        action="store_true",
        help="verify only the Merkle chain (faster; skips Ed25519 signature checks)",
    )
    args = parser.parse_args(argv)

    evidence_dir: Path = args.evidence_dir
    if not evidence_dir.is_dir():
        print(f"ERROR: {evidence_dir} is not a directory", file=sys.stderr)
        return 2

    chain_path = evidence_dir / "chain.merkle"
    if not chain_path.exists():
        print(f"ERROR: missing chain file at {chain_path}", file=sys.stderr)
        return 2

    rows = _load_chain(chain_path)
    ok, broken_at, reason = _verify_chain(rows)
    if not ok:
        print(f"FAIL: chain integrity -- {reason} (broken_at_seq={broken_at})", file=sys.stderr)
        return 1
    print(f"OK: chain integrity verified ({len(rows)} rows, head={rows[-1]['this_hash'][:16]}...)")

    if not args.skip_signatures:
        ok, reason = _verify_signatures(evidence_dir)
        if not ok:
            print(f"FAIL: signature -- {reason}", file=sys.stderr)
            return 1
        print(f"OK: signatures -- {reason}")

    print("PASS: evidence package verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())

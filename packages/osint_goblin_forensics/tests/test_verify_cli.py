"""End-to-end: build a real evidence package, run verify.py, assert PASS."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from osint_goblin_forensics.chain import MerkleChain
from osint_goblin_forensics.signing import (
    generate_keypair,
    serialize_public_key,
    sign,
)

VERIFY_PY = Path(__file__).resolve().parents[1] / "src" / "osint_goblin_forensics" / "verify.py"


def _build_evidence_dir(tmp_path: Path) -> Path:
    """Build a minimal evidence-package directory at tmp_path/evidence/."""
    ev = tmp_path / "evidence"
    ev.mkdir()
    (ev / "keys").mkdir()
    (ev / "artifacts").mkdir()
    (ev / "signatures").mkdir()

    chain = MerkleChain()
    payloads = [b"artifact-1 bytes", b"artifact-2 bytes", b"artifact-3 bytes"]
    chain_rows: list[dict[str, object]] = []
    for r in chain.rows():
        chain_rows.append(
            {
                "seq": r.seq,
                "prev_hash": r.prev_hash.hex(),
                "payload_hex": r.payload.hex(),
                "this_hash": r.this_hash.hex(),
            }
        )
    for p in payloads:
        r = chain.append(p)
        chain_rows.append(
            {
                "seq": r.seq,
                "prev_hash": r.prev_hash.hex(),
                "payload_hex": r.payload.hex(),
                "this_hash": r.this_hash.hex(),
            }
        )

    # Write chain.merkle (JSONL)
    with (ev / "chain.merkle").open("w", encoding="utf-8") as f:
        for row in chain_rows:
            f.write(json.dumps(row) + "\n")

    # Generate signing key and write public part
    sk, pk = generate_keypair()
    (ev / "keys" / "investigation.pub.pem").write_bytes(serialize_public_key(pk))

    # Manifest + signature
    manifest = {
        "investigation": "test-001",
        "chain_head": chain_rows[-1]["this_hash"],
        "n_rows": len(chain_rows),
    }
    manifest_bytes = json.dumps(manifest, sort_keys=True).encode("utf-8")
    (ev / "manifest.json").write_bytes(manifest_bytes)
    (ev / "manifest.sig").write_bytes(sign(manifest_bytes, sk))

    return ev


def test_verify_cli_passes_on_clean_package(tmp_path):
    ev = _build_evidence_dir(tmp_path)
    proc = subprocess.run(
        [sys.executable, str(VERIFY_PY), str(ev)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"verify.py failed: stderr={proc.stderr!r} stdout={proc.stdout!r}"
    assert "PASS: evidence package verified" in proc.stdout


def test_verify_cli_fails_on_tampered_chain(tmp_path):
    ev = _build_evidence_dir(tmp_path)
    # Mutate one row's payload_hex
    chain_path = ev / "chain.merkle"
    lines = chain_path.read_text(encoding="utf-8").splitlines()
    row_2 = json.loads(lines[2])
    row_2["payload_hex"] = "ff" + row_2["payload_hex"][2:]
    lines[2] = json.dumps(row_2)
    chain_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    proc = subprocess.run(
        [sys.executable, str(VERIFY_PY), str(ev)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1
    assert "FAIL: chain integrity" in proc.stderr


def test_verify_cli_fails_on_tampered_manifest_sig(tmp_path):
    ev = _build_evidence_dir(tmp_path)
    # Replace manifest with different bytes (sig now invalid)
    (ev / "manifest.json").write_bytes(b'{"tampered":true}')
    proc = subprocess.run(
        [sys.executable, str(VERIFY_PY), str(ev)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1
    assert "signature" in proc.stderr.lower()


def test_verify_cli_skip_signatures_only_checks_chain(tmp_path):
    ev = _build_evidence_dir(tmp_path)
    (ev / "manifest.sig").unlink()  # delete sig
    proc = subprocess.run(
        [sys.executable, str(VERIFY_PY), str(ev), "--skip-signatures"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0

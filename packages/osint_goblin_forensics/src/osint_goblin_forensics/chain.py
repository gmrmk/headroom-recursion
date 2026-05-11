"""Hash-chain primitive.

This is a linear hash chain: `this = H(prev_hash || payload)`. NOT a Merkle
tree (a Merkle tree is a binary tree of hashes giving O(log n) membership
proofs; we walk linearly, O(n), which is fine for chain-of-custody where
the audit reads the whole chain anyway). Renamed from `MerkleChain` per
Camille P1 phase6 2026-05-11: the prior name was a misnomer that would not
survive expert-witness scrutiny in a court evidence context. A back-compat
alias remains in `__init__.py` for verify.py shipped in pre-rename evidence
zips.

Formula (Yuki phase4/03-qa.md sec.6 I2):

    this_hash = sha256(prev_hash || payload)
    genesis: seq=0, prev_hash=b"\x00" * 32, payload=b""

Single-writer-queue is the architectural fix for concurrent writes (Diego
ADR-0006). This module is INTENTIONALLY not thread-safe -- the caller
serializes appends. Property-based tests in test_chain.py cover I1-I7.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

GENESIS_PREV_HASH: bytes = b"\x00" * 32


@dataclass(frozen=True, slots=True)
class ChainRow:
    """One row in the hash chain. Immutable."""

    seq: int
    prev_hash: bytes  # 32 bytes; row[0].prev_hash == GENESIS_PREV_HASH
    payload: bytes
    this_hash: bytes  # 32 bytes


def chain_hash(prev_hash: bytes, payload: bytes) -> bytes:
    """The canonical chain-link hash. SHA-256 over prev_hash || payload.

    Used both for append (chain construction) and verify (offline chain walk).
    """
    return hashlib.sha256(prev_hash + payload).digest()


class HashChain:
    """In-memory hash chain. Single-writer; serialize externally.

    The chain starts with a genesis row at seq=0 with empty payload, then
    each `append(p)` adds a row whose prev_hash equals the previous row's
    this_hash.
    """

    def __init__(self) -> None:
        genesis_payload = b""
        genesis_hash = chain_hash(GENESIS_PREV_HASH, genesis_payload)
        self._rows: list[ChainRow] = [
            ChainRow(
                seq=0, prev_hash=GENESIS_PREV_HASH, payload=genesis_payload, this_hash=genesis_hash
            )
        ]

    def append(self, payload: bytes) -> ChainRow:
        """Append a new row. Returns the row."""
        prev = self._rows[-1]
        row = ChainRow(
            seq=prev.seq + 1,
            prev_hash=prev.this_hash,
            payload=payload,
            this_hash=chain_hash(prev.this_hash, payload),
        )
        self._rows.append(row)
        return row

    def rows(self) -> list[ChainRow]:
        """All rows including genesis."""
        return list(self._rows)

    @property
    def head(self) -> bytes:
        """Current head hash (this_hash of the latest row)."""
        return self._rows[-1].this_hash

    def __len__(self) -> int:
        return len(self._rows)


def verify_chain(rows: list[ChainRow]) -> tuple[bool, int | None, str]:
    """Walk a chain top-to-bottom. Returns (ok, broken_at_seq, reason).

    `ok=True` means every row's this_hash matches sha256(prev_hash || payload)
    AND each row's prev_hash matches the previous row's this_hash AND seq is
    monotone-by-one starting at 0.

    On the first violation, returns ok=False, broken_at_seq=<that_seq>, and a
    one-line reason. Stops at the first violation; downstream rows are not
    checked.
    """
    if not rows:
        return False, None, "empty chain"
    if rows[0].seq != 0 or rows[0].prev_hash != GENESIS_PREV_HASH:
        return False, 0, "genesis row malformed"
    for i, r in enumerate(rows):
        expected = chain_hash(r.prev_hash, r.payload)
        if r.this_hash != expected:
            return False, r.seq, f"this_hash mismatch at seq={r.seq}"
        if i > 0:
            prev = rows[i - 1]
            if r.prev_hash != prev.this_hash:
                return False, r.seq, f"prev_hash linkage broken at seq={r.seq}"
            if r.seq != prev.seq + 1:
                return False, r.seq, f"seq gap at seq={r.seq} (expected {prev.seq + 1})"
    return True, None, "ok"

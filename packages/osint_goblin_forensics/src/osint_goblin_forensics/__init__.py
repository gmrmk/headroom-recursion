"""osint_goblin_forensics -- evidence-chain primitives (L0).

Per Sora ADR-0001: stdlib + cryptography ONLY. No followthemoney, no Pydantic,
no FastAPI. The verify.py CLI ships standalone in evidence-package zips and
runs offline on any machine with Python 3.11+.

Public API:

    from osint_goblin_forensics import HashChain, sign, verify_signature, timestamp

    chain = HashChain()
    row = chain.append(payload=b"some evidence bytes")

    sk, pk = generate_keypair()
    sig = sign(b"manifest bytes", sk)
    assert verify_signature(b"manifest bytes", sig, pk)

    tsr = timestamp(sha256_digest=hashlib.sha256(b"...").digest())
"""

from .chain import GENESIS_PREV_HASH, ChainRow, HashChain, chain_hash
from .signing import generate_keypair, load_private_key, sign, verify_signature
from .timestamping import FREETSA_URL, RFC3161Error, TimestampedResult, timestamp

# Camille P1 phase6 (2026-05-11): the class was renamed from MerkleChain to
# HashChain because this is a linear hash chain (this = H(prev || payload)),
# not a Merkle tree (no binary tree structure, no O(log n) membership proofs).
# Camille flagged the misnomer as bad opsec for legal scrutiny -- an expert
# witness will catch it. This alias preserves back-compat for verify.py
# bundled in evidence-package zips already shipped before the rename.
# Remove the alias when no shipped evidence zip refers to MerkleChain.
MerkleChain = HashChain

__all__ = [
    "FREETSA_URL",
    "GENESIS_PREV_HASH",
    "ChainRow",
    "HashChain",
    "MerkleChain",  # deprecated alias; see comment above
    "RFC3161Error",
    "TimestampedResult",
    "chain_hash",
    "generate_keypair",
    "load_private_key",
    "sign",
    "timestamp",
    "verify_signature",
]

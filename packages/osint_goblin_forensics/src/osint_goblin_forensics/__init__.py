"""osint_goblin_forensics -- evidence-chain primitives (L0).

Per Sora ADR-0001: stdlib + cryptography ONLY. No followthemoney, no Pydantic,
no FastAPI. The verify.py CLI ships standalone in evidence-package zips and
runs offline on any machine with Python 3.11+.

Public API:

    from osint_goblin_forensics import MerkleChain, sign, verify_signature, timestamp

    chain = MerkleChain()
    row = chain.append(payload=b"some evidence bytes")

    sk, pk = generate_keypair()
    sig = sign(b"manifest bytes", sk)
    assert verify_signature(b"manifest bytes", sig, pk)

    tsr = timestamp(sha256_digest=hashlib.sha256(b"...").digest())
"""

from .chain import GENESIS_PREV_HASH, ChainRow, MerkleChain, chain_hash
from .signing import generate_keypair, load_private_key, sign, verify_signature
from .timestamping import FREETSA_URL, RFC3161Error, TimestampedResult, timestamp

__all__ = [
    "FREETSA_URL",
    "GENESIS_PREV_HASH",
    "ChainRow",
    "MerkleChain",
    "RFC3161Error",
    "TimestampedResult",
    "chain_hash",
    "generate_keypair",
    "load_private_key",
    "sign",
    "timestamp",
    "verify_signature",
]

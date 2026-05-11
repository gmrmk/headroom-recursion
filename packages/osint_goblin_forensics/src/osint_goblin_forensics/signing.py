"""Ed25519 sign/verify primitives.

Per Camille phase3/05-security-compliance.md (locked in phase5/spikes/
ed25519_smoke.py): Ed25519 64-byte signatures, sub-5ms wall on Win11 Python
3.13+. Tamper detection via cryptography's InvalidSignature exception.

Keys are owned by the caller (per-investigation Ed25519 keys are Camille's
domain; release-attestation keys are Boris D5, structurally separate). This
module just provides the primitives.
"""

from __future__ import annotations

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


def generate_keypair() -> tuple[Ed25519PrivateKey, Ed25519PublicKey]:
    """Generate a fresh Ed25519 keypair."""
    sk = Ed25519PrivateKey.generate()
    return sk, sk.public_key()


def sign(message: bytes, private_key: Ed25519PrivateKey) -> bytes:
    """Sign `message` with `private_key`. Returns 64-byte signature."""
    return private_key.sign(message)


def verify_signature(message: bytes, signature: bytes, public_key: Ed25519PublicKey) -> bool:
    """Verify `signature` over `message` with `public_key`.

    Returns True on success, False on any failure (tamper, wrong key, malformed
    sig). Does NOT raise -- callers get a clean boolean for chain-walk loops.
    """
    try:
        public_key.verify(signature, message)
        return True
    except InvalidSignature:
        return False


def load_private_key(pem_bytes: bytes, password: bytes | None = None) -> Ed25519PrivateKey:
    """Load a PEM-encoded Ed25519 private key."""
    key = serialization.load_pem_private_key(pem_bytes, password=password)
    if not isinstance(key, Ed25519PrivateKey):
        raise TypeError(f"expected Ed25519PrivateKey, got {type(key).__name__}")
    return key


def serialize_private_key(private_key: Ed25519PrivateKey, password: bytes | None = None) -> bytes:
    """Serialize to PEM. If `password` given, encrypts with BestAvailableEncryption."""
    enc: serialization.KeySerializationEncryption
    if password:
        enc = serialization.BestAvailableEncryption(password)
    else:
        enc = serialization.NoEncryption()
    return private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=enc,
    )


def serialize_public_key(public_key: Ed25519PublicKey) -> bytes:
    """Serialize public key to PEM."""
    return public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )

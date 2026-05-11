"""Unit tests for osint_goblin_forensics.signing (Ed25519 primitives)."""

from __future__ import annotations

from osint_goblin_forensics.signing import (
    generate_keypair,
    load_private_key,
    serialize_private_key,
    serialize_public_key,
    sign,
    verify_signature,
)


def test_keypair_roundtrip():
    sk, pk = generate_keypair()
    sig = sign(b"hello", sk)
    assert len(sig) == 64
    assert verify_signature(b"hello", sig, pk) is True


def test_tamper_detected():
    sk, pk = generate_keypair()
    sig = sign(b"hello", sk)
    assert verify_signature(b"hello!", sig, pk) is False


def test_wrong_key_rejected():
    sk1, _ = generate_keypair()
    _, pk2 = generate_keypair()
    sig = sign(b"hello", sk1)
    assert verify_signature(b"hello", sig, pk2) is False


def test_malformed_signature_returns_false_not_raises():
    _, pk = generate_keypair()
    assert verify_signature(b"hello", b"\x00" * 64, pk) is False


def test_serialize_load_unencrypted():
    sk, _ = generate_keypair()
    pem = serialize_private_key(sk)
    assert b"BEGIN PRIVATE KEY" in pem
    sk2 = load_private_key(pem)
    sig = sign(b"x", sk2)
    assert verify_signature(b"x", sig, sk.public_key()) is True


def test_serialize_load_encrypted():
    sk, _ = generate_keypair()
    pem = serialize_private_key(sk, password=b"correct-horse")
    assert b"ENCRYPTED" in pem
    sk2 = load_private_key(pem, password=b"correct-horse")
    sig = sign(b"y", sk2)
    assert verify_signature(b"y", sig, sk.public_key()) is True


def test_serialize_public_key():
    _, pk = generate_keypair()
    pem = serialize_public_key(pk)
    assert b"BEGIN PUBLIC KEY" in pem

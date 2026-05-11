"""Property tests for osint_goblin_forensics.chain (Yuki's I1-I7 invariants)."""

from __future__ import annotations

import hashlib

from hypothesis import given
from hypothesis import strategies as st
from osint_goblin_forensics.chain import (
    GENESIS_PREV_HASH,
    ChainRow,
    MerkleChain,
    chain_hash,
    verify_chain,
)


def test_I1_genesis_seq_zero_prev_hash_zero():
    c = MerkleChain()
    g = c.rows()[0]
    assert g.seq == 0
    assert g.prev_hash == GENESIS_PREV_HASH
    assert g.prev_hash == b"\x00" * 32


@given(payloads=st.lists(st.binary(max_size=4096), max_size=64))
def test_I2_hash_formula(payloads):
    c = MerkleChain()
    for p in payloads:
        c.append(p)
    for r in c.rows():
        assert r.this_hash == hashlib.sha256(r.prev_hash + r.payload).digest()


@given(payloads=st.lists(st.binary(max_size=512), min_size=1, max_size=32))
def test_I3_prev_linkage(payloads):
    c = MerkleChain()
    for p in payloads:
        c.append(p)
    rows = c.rows()
    for i in range(1, len(rows)):
        assert rows[i].prev_hash == rows[i - 1].this_hash


@given(payloads=st.lists(st.binary(max_size=256), min_size=0, max_size=64))
def test_I4_seq_monotone(payloads):
    c = MerkleChain()
    for p in payloads:
        c.append(p)
    rows = c.rows()
    for i, r in enumerate(rows):
        assert r.seq == i


@given(payloads=st.lists(st.binary(min_size=1, max_size=128), min_size=3, max_size=20))
def test_I5_tamper_detected(payloads):
    """Mutate any byte in any row's payload and re-verify -- chain must reject."""
    c = MerkleChain()
    for p in payloads:
        c.append(p)
    rows = c.rows()
    ok, _, _ = verify_chain(rows)
    assert ok

    # Tamper row 1's payload (genesis is row 0; row 1 is the first appended).
    tampered = ChainRow(
        seq=rows[1].seq,
        prev_hash=rows[1].prev_hash,
        payload=rows[1].payload + b"\x00",  # one extra byte
        this_hash=rows[1].this_hash,  # but keep the stored hash unchanged
    )
    bad_rows = [rows[0], tampered, *list(rows[2:])]
    ok, broken_at, reason = verify_chain(bad_rows)
    assert not ok
    assert broken_at == 1
    assert "this_hash mismatch" in reason


def test_I6_genesis_cannot_be_silently_replaced():
    c = MerkleChain()
    c.append(b"a")
    rows = c.rows()
    fake_genesis = ChainRow(
        seq=0, prev_hash=b"\xff" * 32, payload=b"", this_hash=chain_hash(b"\xff" * 32, b"")
    )
    bad = [fake_genesis, *rows[1:]]
    ok, broken_at, _ = verify_chain(bad)
    assert not ok
    assert broken_at == 0


def test_I7_order_sensitive():
    """Same payloads in different order -> different head hash."""
    c1 = MerkleChain()
    c1.append(b"a")
    c1.append(b"b")

    c2 = MerkleChain()
    c2.append(b"b")
    c2.append(b"a")

    assert c1.head != c2.head


def test_chain_hash_deterministic():
    p = b"\x00" * 32
    assert chain_hash(p, b"hello") == chain_hash(p, b"hello")
    assert chain_hash(p, b"hello") != chain_hash(p, b"world")


def test_empty_chain_rejected_by_verify():
    ok, _, reason = verify_chain([])
    assert not ok
    assert "empty" in reason
